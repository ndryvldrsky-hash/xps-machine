"""Перевод звонка на экране XPS (30.09.2026): иврит собеседника → русские субтитры + подсказки ответа от Алёны.

Звук берётся ПРЯМО с телефона A54 (решение пользователя 30.09, без громкой связи): scrcpy 4.1
--audio-source=voice-call-downlink пишет голос собеседника в call.wav, программа читает файл по мере записи
(48 кГц стерео → 16 кГц моно). Запасной путь — микрофон XPS (аргумент mic). Речь режется по паузам, каждая фраза
уходит в Gemini (Vertex): распознать иврит и перевести на русский (решение пользователя: Gemini — только перевод).
Фразы пишутся в talk.jsonl рядом — его читает Алёна (Claude) и кладёт свой вариант ответа в suggest.json
({"answers": [{"he", "ru"}]}); окно показывает его справа, «Можно ответить» — иврит крупно, чтобы прочитать вслух.

Токен Google Cloud на ~1 ч кладёт аддон HA (перевод_звонка_запуск.py) в token.json — ключ на XPS не хранится.
Закрыть — Esc или крестик. Лог — perevod.log рядом.
"""
import base64
import io
import json
import os
import queue
import threading
import time
import tkinter as tk
import wave

import numpy as np
import requests
import sounddevice as sd

DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN = os.path.join(DIR, "token.json")
TALK = os.path.join(DIR, "talk.jsonl")
SUGGEST = os.path.join(DIR, "suggest.json")
LOG = os.path.join(DIR, "perevod.log")


def rtl(s):
    """30.09: Tk на Windows считает строку левосторонней и переставляет куски иврита вокруг латиницы («IP», «TMS») —
    обёртка RLE…PDF задаёт направление справа налево (проверено на XPS: единственный вариант с правильным порядком)."""
    return "\u202b" + s + "\u202c" if s else s
RATE = 16000
BLOCK = RATE // 20            # 50 мс
SILENCE_END = 0.7             # пауза, после которой фраза считается законченной, с
MAX_PHRASE = 15.0             # длинную речь режем кусками, чтобы перевод не отставал
MODEL = "gemini-2.5-flash"
import sys
SOURCE = "mic" if "mic" in sys.argv[1:] else "phone"
KEEP = 5

phrases = queue.Queue()
ui_q = queue.Queue()
stop = threading.Event()
last_suggest = []


def log(*a):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(time.strftime("%H:%M:%S ") + " ".join(str(x) for x in a) + "\n")


def creds():
    t = json.load(open(TOKEN, encoding="utf-8"))
    return t["token"], t["project"]


SCRCPY = r"W:\tools\scrcpy-win64-v4.1\scrcpy.exe"
PHONE = "100.116.182.97:5555"          # A54, номер для работодателей 058-739-6888
CALL_WAV = os.path.join(DIR, "call.wav")


in_call = threading.Event()


def call_watch():
    """Идёт ли звонок на A54 (mCallState=2). Без звонка scrcpy voice-call отдаёт звук микрофона телефона —
    30.09 так в перевод попал голос Алёны из колонок XPS; такой звук выбрасываем."""
    import subprocess
    adb = os.path.join(os.path.dirname(SCRCPY), "adb.exe")
    while not stop.is_set():
        try:
            out = subprocess.run([adb, "-s", PHONE, "shell", "dumpsys telephony.registry"], capture_output=True,
                                 text=True, timeout=8, creationflags=0x08000000).stdout
            if "mCallState=2" in out:
                if not in_call.is_set():
                    log("звонок начался")
                    ui_q.put(("status", "📞 Идёт звонок — перевожу", ""))
                in_call.set()
            else:
                if in_call.is_set():
                    log("звонок закончился")
                    ui_q.put(("status", "📞 жду звонка на A54…", ""))
                in_call.clear()
        except Exception as e:
            log("проверка звонка:", repr(e)[:150])
        time.sleep(1.5)


def phone_blocks(q):
    """Голос собеседника с A54: scrcpy пишет call.wav (raw PCM 48 кГц стерео), читаем хвост файла.
    Нет звонка — scrcpy завершается или молчит; перезапускаем, пока окно открыто."""
    import subprocess
    while not stop.is_set():
        try:
            os.remove(CALL_WAV)
        except OSError:
            pass
        proc = subprocess.Popen([SCRCPY, "-s", PHONE, "--no-video", "--no-playback", "--no-window",
                                 "--audio-source=voice-call-downlink", "--audio-codec=raw", f"--record={CALL_WAV}"],
                                cwd=os.path.dirname(SCRCPY), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=0x08000000)          # CREATE_NO_WINDOW
        log("scrcpy запущен, pid", proc.pid)
        pos, rest = None, b""
        while not stop.is_set() and proc.poll() is None:
            time.sleep(0.05)
            try:
                with open(CALL_WAV, "rb") as f:
                    if pos is None:
                        head = f.read(4096)
                        i = head.find(b"data")
                        if i < 0:
                            continue
                        pos = i + 8
                    f.seek(pos)
                    chunk = f.read()
            except OSError:
                continue
            if not chunk:
                continue
            pos += len(chunk)
            chunk = rest + chunk
            usable = len(chunk) // 12 * 12          # 3 кадра × 2 канала × 2 байта → один отсчёт 16 кГц
            rest = chunk[usable:]
            if not in_call.is_set():
                continue
            a = np.frombuffer(chunk[:usable], dtype=np.int16).astype(np.int32).reshape(-1, 3, 2)
            q.put(a.mean(axis=(1, 2)).astype(np.int16).reshape(-1, 1))
        if proc.poll() is None:
            proc.kill()
        log("scrcpy завершился, код", proc.returncode)
        ui_q.put(("status", "📞 жду звонка на A54…", ""))
        time.sleep(2)


def listen():
    """Звук (телефон или микрофон) → фразы по паузам. Порог тишины подстраивается под фон."""
    # телефонная линия тихая и чистая (речь 150–600 RMS, тишина 0–10 — замер 30.09), микрофон шумнее
    buf, voiced, silent, noise = [], 0.0, 0.0, (20.0 if SOURCE == "phone" else 300.0)
    floor, k = (60, 3.0) if SOURCE == "phone" else (250, 2.5)
    q = queue.Queue()
    if SOURCE == "mic":
        stream = sd.InputStream(samplerate=RATE, blocksize=BLOCK, channels=1, dtype="int16",
                                callback=lambda d, f, t, s: q.put(d.copy()))
        stream.start()
    else:
        threading.Thread(target=call_watch, daemon=True).start()
        threading.Thread(target=phone_blocks, args=(q,), daemon=True).start()
    if True:
        while not stop.is_set():
            try:
                d = q.get(timeout=0.5)
            except queue.Empty:
                continue
            level = float(np.sqrt(np.mean(d.astype(np.float32) ** 2)))
            blk = len(d) / RATE
            speech = level > max(noise * k, floor)
            if not speech and not buf:
                noise = noise * 0.95 + level * 0.05
            if speech or buf:
                buf.append(d)
            if speech:
                voiced += blk
                silent = 0.0
                ui_q.put(("hearing", True, ""))
            elif buf:
                silent += blk
            dur = sum(len(b) for b in buf) / RATE
            if buf and (silent >= SILENCE_END or dur >= MAX_PHRASE):
                if voiced >= 0.4:
                    phrases.put(np.concatenate(buf))
                buf, voiced, silent = [], 0.0, 0.0
                ui_q.put(("hearing", False, ""))


def wav_bytes(pcm):
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())
    return b.getvalue()


PROFILE = os.path.join(DIR, "profile.txt")
history = []            # (он, иврит, русский) — последние реплики для черновика ответа

# 30.09 (решение пользователя): Gemini в том же запросе даёт ЧЕРНОВИК ответа — он появляется сразу вместе с переводом,
# Алёна (Claude) заменяет его своим (suggest.json), где нужно точнее
PROMPT = ("Это фраза собеседника (работодатель/рекрутер) из телефонного разговора, скорее всего на иврите. Распознай её "
          "точно и переведи на русский. Если речи нет или неразборчиво — he пустая строка.\n"
          "ВСЕГДА, если в фразе есть вопрос или обращение к кандидату, предложи 1 короткий ответ кандидата (до 8 слов) на простом иврите: plain — полным "
          "письмом (כתב מלא) без огласовок, he — ТЕ ЖЕ буквы с никудом (никуд только добавлять, буквы не выбрасывать), ru — "
          "перевод. Факты — только из профиля; чего нет в профиле (зарплата, смены, машина) — не выдумывать, а вежливо "
          "уточнить или сказать, что ответит позже. Реплика служебная/обрывок — answers пустой.\n"
          "Верни JSON {\"he\": \"...\", \"ru\": \"...\", \"answers\": [{\"plain\": \"...\", \"he\": \"...\", \"ru\": \"...\"}]}.\n"
          "ПРОФИЛЬ КАНДИДАТА:\n{profile}\nНЕДАВНИЕ РЕПЛИКИ СОБЕСЕДНИКА:\n{history}")


def strip_nikud(t):
    return "".join(c for c in t if not ("\u0591" <= c <= "\u05c7") or c == "\u05be")


def fix_nikud(full, nik):
    """Буквы полного письма в огласованную фразу (как в аддоне): выпавшие вставить, лишние убрать, холам на ו."""
    import difflib
    if strip_nikud(nik) == full:
        return nik
    toks = []
    for c in nik:
        if ("\u0591" <= c <= "\u05c7") and c != "\u05be" and toks:
            toks[-1][1] += c
        else:
            toks.append([c, ""])
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, [b for b, _ in toks], list(full), autojunk=False).get_opcodes():
        if op == "equal":
            out += [b + m for b, m in toks[i1:i2]]
        elif op in ("insert", "replace"):
            for ch in full[j1:j2]:
                if ch == "ו" and out and "\u05b9" in out[-1]:
                    out[-1] = out[-1].replace("\u05b9", "")
                    ch = "ו\u05b9"
                out.append(ch)
    return "".join(out)


def translate_loop():
    while not stop.is_set():
        try:
            pcm = phrases.get(timeout=0.5)
        except queue.Empty:
            continue
        t0 = time.time()
        try:
            tok, proj = creds()
            body = {"contents": [{"role": "user", "parts": [
                        {"inlineData": {"mimeType": "audio/wav", "data": base64.b64encode(wav_bytes(pcm)).decode()}},
                        {"text": PROMPT.replace("{profile}", open(PROFILE, encoding="utf-8").read() if os.path.exists(PROFILE) else "")
                                        .replace("{history}", "\n".join(f"- {h} ({r})" for h, r in history[-6:]) or "—")}]}],
                    "generationConfig": {"responseMimeType": "application/json", "temperature": 0,
                                         "thinkingConfig": {"thinkingBudget": 0}}}
            r = requests.post(f"https://aiplatform.googleapis.com/v1/projects/{proj}/locations/global/publishers/google/"
                              f"models/{MODEL}:generateContent", headers={"Authorization": f"Bearer {tok}"},
                              json=body, timeout=30)
            r.raise_for_status()
            d = json.loads(r.json()["candidates"][0]["content"]["parts"][0]["text"])
            he, ru = (d.get("he") or "").strip(), (d.get("ru") or "").strip()
            if not he:
                continue
            # со звука телефона (downlink) идёт только собеседник — «своё» узнаём лишь в режиме микрофона
            mine = SOURCE == "mic" and is_mine(he)
            rec = {"ts": time.strftime("%H:%M:%S"), "who": "я" if mine else "он", "he": he, "ru": ru}
            with open(TALK, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            ui_q.put(("final", he, ("🗣 " if mine else "") + ru))
            history.append((he, ru))
            drafts = []
            for a in (d.get("answers") or [])[:2]:
                plain, nik = (a.get("plain") or "").strip(), (a.get("he") or "").strip()
                if plain and nik:
                    drafts.append({"he": fix_nikud(plain, nik), "ru": a.get("ru", "")})
            log("черновик:", json.dumps(d.get("answers"), ensure_ascii=False)[:300])
            if drafts and not mine:
                ui_q.put(("suggest", drafts, "draft"))
            push_to_alena(rec)
            log(f"{time.time() - t0:.1f} с", "он" if not mine else "я", he, "|", ru)
        except Exception as e:
            log("перевод не вышел:", repr(e)[:300])
            ui_q.put(("status", f"⚠ перевод: {str(e)[:100]}", ""))


def push_to_alena(rec):
    """Реплику — сразу Алёне: вебхук HA → событие perevod_zvonka (без опроса журнала, доли секунды)."""
    def go():
        try:
            hook = json.load(open(TOKEN, encoding="utf-8")).get("webhook")
            if hook:
                requests.post(hook, json=rec, timeout=5)
        except Exception as e:
            log("вебхук:", repr(e)[:200])
    threading.Thread(target=go, daemon=True).start()


def is_mine(text):
    """Похоже ли на прочитанную вслух подсказку (микрофон слышит и пользователя)."""
    clean = lambda s: set(s.replace("?", " ").replace(",", " ").replace(".", " ").replace("!", " ").split())
    w = clean(text)
    return any(sw and len(w & sw) / len(sw) >= 0.5 for sw in map(clean, last_suggest))


def watch_suggest():
    """Подсказки Алёны: suggest.json кладёт аддон, окно подхватывает по изменению файла."""
    seen = 0.0
    while not stop.is_set():
        try:
            m = os.path.getmtime(SUGGEST)
            if m != seen:
                seen = m
                ans = json.load(open(SUGGEST, encoding="utf-8")).get("answers", [])
                last_suggest[:] = [a.get("he", "") for a in ans]
                ui_q.put(("suggest", ans, "alena"))
        except (OSError, ValueError):
            pass
        time.sleep(0.4)


class Window:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Перевод звонка — Алёна")
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.94)
        self.root.configure(bg="#111418")
        w, h = self.root.winfo_screenwidth(), 380
        self.root.geometry(f"{int(w * 0.86)}x{h}+{int(w * 0.07)}+{self.root.winfo_screenheight() - h - 60}")
        self.root.bind("<Escape>", lambda e: self.close())
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.text = tk.Text(self.root, bg="#111418", fg="#f2f2f2", bd=0, wrap="word", padx=18, pady=10, width=40)
        self.text.pack(side="left", fill="both", expand=True)
        self.text.tag_configure("ru", font=("Segoe UI", 22, "bold"), foreground="#ffffff")
        self.text.tag_configure("he", font=("Segoe UI", 14), foreground="#8fb3d9", justify="right")
        self.text.tag_configure("status", font=("Segoe UI", 12), foreground="#e0a040")
        self.sug = tk.Text(self.root, bg="#0f2418", fg="#eafbe9", bd=0, wrap="word", padx=16, pady=10, width=62)
        self.sug.pack(side="right", fill="both")
        self.sug.tag_configure("title", font=("Segoe UI", 16, "bold"), foreground="#7fd18b")
        self.sug.tag_configure("draft", font=("Segoe UI", 16, "bold"), foreground="#e0c060")
        self.sug.tag_configure("he", font=("Segoe UI", 24, "bold"), foreground="#ffffff", justify="right")
        self.sug.tag_configure("ru", font=("Segoe UI", 14), foreground="#b8e6bd")
        self.lines, self.answers, self.src = [], [], "alena"
        self.hearing = False
        self.status = ("🎧 Слушаю микрофон XPS…" if SOURCE == "mic" else "📞 Слушаю звонок на A54 (058-739-6888)…")
        self.render()
        self.root.after(100, self.poll)

    def close(self):
        stop.set()
        self.root.destroy()

    def poll(self):
        changed = False
        while True:
            try:
                kind, a, b = ui_q.get_nowait()
            except queue.Empty:
                break
            changed = True
            if kind == "final":
                self.lines = (self.lines + [(a, b)])[-KEEP:]
                self.status = ""
            elif kind == "hearing":
                self.hearing = a
            elif kind == "status":
                self.status = a
            elif kind == "suggest":
                self.answers, self.src = a, b
        if changed:
            self.render()
        if not stop.is_set():
            self.root.after(100, self.poll)

    def render(self):
        t = self.text
        t.configure(state="normal")
        t.delete("1.0", "end")
        if self.status:
            t.insert("end", self.status + "\n", "status")
        for he, ru in self.lines:
            t.insert("end", ru + "\n", "ru")
            t.insert("end", rtl(he) + "\n", "he")
        if self.hearing:
            t.insert("end", "🎙 …\n", "status")
        t.configure(state="disabled")
        t.see("end")
        g = self.sug
        g.configure(state="normal")
        g.delete("1.0", "end")
        if self.src == "draft":
            g.insert("end", "⚡ ЧЕРНОВИК (Gemini) — Алёна поправит\n", "draft")
        else:
            g.insert("end", "✅ ОТВЕТ АЛЁНЫ\n", "title")
        for n, a in enumerate(self.answers, 1):
            g.insert("end", rtl(a.get("he", "")) + "\n", "he")
            g.insert("end", f"{n}) {a.get('ru', '')}\n\n", "ru")
        if not self.answers:
            g.insert("end", "подсказка появится после фразы собеседника\n", "ru")
        g.configure(state="disabled")


def main():
    log("старт")
    for p in (TALK, SUGGEST):          # новый звонок — чистый лист
        try:
            os.remove(p)
        except OSError:
            pass
    for f in (listen, translate_loop, watch_suggest):
        threading.Thread(target=f, daemon=True).start()
    Window().root.mainloop()
    stop.set()
    log("стоп")


if __name__ == "__main__":
    main()
