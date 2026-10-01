"""Тренажёр чтения ответов на собеседовании (30.09.2026) — перед звонками «Рона».

Окно (монитор SHARP): фраза на иврите крупно с никудом + перевод. Пробел — начать запись с микрофона XPS, пробел ещё
раз — стоп; фраза уходит в Gemini (Vertex): что расслышано, оценка 1–5, какие слова/звуки неточны, как исправить.
P или кнопка «▶ Послушать» — образец произношения: генерируется на ходу Gemini TTS (Vertex, медленно, голос Charon,
5–8 с) и кэшируется в samples/<md5 фразы>.wav — повторно играет сразу;
Enter/P — послушать; R — повторить фразу, → — следующая, ← — предыдущая, Esc — выход (итог попыток — в trainer_log.jsonl рядом).
Фразы — phrases.json рядом (кладёт аддон), токен Google — token.json (как у окна перевода).
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
PHRASES = os.path.join(DIR, "phrases.json")
LOGF = os.path.join(DIR, "trainer_log.jsonl")
RATE = 16000
MODEL = "gemini-2.5-flash"


def rtl(s):
    """30.09: Tk на Windows считает строку левосторонней и переставляет куски иврита вокруг латиницы («IP», «TMS») —
    обёртка RLE…PDF задаёт направление справа налево (проверено на XPS: единственный вариант с правильным порядком)."""
    return "\u202b" + s + "\u202c" if s else s


def rtl_runs(s):
    """В русской строке каждое ивритское словосочетание — в RLE…PDF, иначе Tk выводит его слова в обратном порядке."""
    import re
    return re.sub(r"[\u0590-\u05ff][\u0590-\u05ff\s\-־'\".,]*[\u0590-\u05ff]|[\u0590-\u05ff]",
                  lambda m: rtl(m.group(0)), s)


def analysis_text(a):
    """30.09: лингвистический разбор фразы (поле analysis в phrases.json, готовит тренажёр_разбор.py в аддоне)."""
    if not a:
        return ""
    out = [f"• {w['he']} [{w['tr']}] — {w['ru']}. {w['gram']}" for w in a.get("words", [])]
    out += [""] + [f"💡 {n}" for n in a.get("notes", [])] if a.get("notes") else []
    return "\n".join(rtl_runs(x) for x in out)

PROMPT = """Кандидат (русскоязычный, учит иврит) читает вслух фразу для собеседования. Целевая фраза:
{he}  ({ru})
Сначала внимательно прослушай запись и запиши, что реально прозвучало, потом сравни с целевой фразой по словам.
Верни JSON:
{{"heard": "что прозвучало — русскими буквами с ударением (например: тОда шеиткашАрта)",
 "score": 1-5 (5 — носитель поймёт без усилий),
 "problems": [{{"word": "слово на иврите", "said": "как прозвучало (русскими буквами, ударная гласная заглавной)",
               "should": "как правильно (так же)", "fix": "что сделать, коротко по-русски"}}],
 "tip": "главный совет одной фразой по-русски"}}
Правила:
- Если ошибка в звуке, который русскими буквами не отличить (ר, ח/כ, ע), пометь звук в скобках, чтобы said и should
  различались: said «ледабЭр (р русское, раскатистое)», should «ледабЭр (ר горловое)».
- В problems — ТОЛЬКО слова, где прозвучавшее реально отличается от правильного (пропущен или лишний слог, не тот звук,
  не то ударение). Если said и should совпадают — это не ошибка, не включай. Нет ошибок — пустой список.
- Не выдумывай разницу между ивритскими и русскими звуками там, где её нет: ת/ט = русское «т», ד = «д», ב = «б»,
  ב без точки = «в», ל = «л», ש = «ш», שׂ/ס = «с». Настоящие отличия — ר (горловое, как французское r), ח/כ без точки
  («х» глубже), ע/א/ה (не глотать, не придыхать лишнего), ударение (в иврите чаще на последний слог).
- Не придирайся к акценту, если слово понятно. Не больше 4 пунктов, самые важные первыми."""


def creds():
    t = json.load(open(TOKEN, encoding="utf-8"))
    return t["token"], t["project"]


class Trainer:
    def __init__(self):
        self.phrases = json.load(open(PHRASES, encoding="utf-8"))
        import sys
        self.i = int(sys.argv[1]) - 1 if len(sys.argv) > 1 else 0     # номер фразы, с которой начать (1…)
        self.rec, self.buf = False, []
        self.pending = set()
        self.stream = None
        self.q = queue.Queue()
        r = self.root = tk.Tk()
        r.title("Тренажёр чтения — Алёна")
        r.configure(bg="#111418")
        r.attributes("-topmost", True)
        self.he = tk.Label(r, font=("Segoe UI", 34, "bold"), fg="#ffffff", bg="#111418", wraplength=1150, justify="right")
        self.he.pack(pady=(30, 8), padx=30, anchor="e")
        self.ru = tk.Label(r, font=("Segoe UI", 18), fg="#9fc5e8", bg="#111418", wraplength=1150)
        self.ru.pack(pady=4)
        self.btn = tk.Button(r, text="▶  Послушать быстро (Enter)", font=("Segoe UI", 16, "bold"), fg="#111418", bg="#7fd18b",
                  activebackground="#9fe0a8", relief="flat", padx=18, pady=6, command=self.play)
        self.btn.pack(pady=6)
        self.fast = True                # 30.09: нажатия чередуют быстрый (естественный) и медленный образец
        self.state = tk.Label(r, font=("Segoe UI", 16, "bold"), fg="#e0c060", bg="#111418")
        self.state.pack(pady=10)
        self.fb = tk.Label(r, font=("Segoe UI", 15), fg="#eafbe9", bg="#111418", wraplength=1150, justify="left")
        self.fb.pack(pady=(0, 4), padx=30, anchor="w")
        # 30.09 (просьба пользователя): разбор фразы — простым текстом прямо под жёлтой надписью
        self.an = tk.Label(r, font=("Segoe UI", 13), fg="#c9d1d9", bg="#111418", wraplength=1200, justify="left")
        self.an.pack(pady=(4, 6), padx=30, anchor="w")
        tk.Label(r, text="Enter или P — послушать (быстро/медленно) · Пробел — запись/стоп · R — ещё раз · → следующая · ← предыдущая · Esc — выход",
                 font=("Segoe UI", 11), fg="#777777", bg="#111418").pack(side="bottom", pady=10)
        r.bind("<space>", lambda e: self.toggle())
        r.bind("<Right>", lambda e: self.move(1))
        r.bind("<Return>", lambda e: self.play())       # 30.09: Enter — послушать (быстро/медленно по очереди)
        r.bind("<Left>", lambda e: self.move(-1))
        r.bind("<r>", lambda e: self.show())
        r.bind("<p>", lambda e: self.play())
        r.bind("<Escape>", lambda e: r.destroy())
        self.show()
        r.after(100, self.poll)

    def show(self):
        p = self.phrases[self.i]
        self.he.config(text=rtl(p["he"]))
        self.ru.config(text=f"{self.i + 1}/{len(self.phrases)} · {p['ru']}")
        self.state.config(text="Пробел — начать запись", fg="#e0c060")
        self.fb.config(text="")
        self.an.config(text=analysis_text(p.get("analysis")))
        self.fast = True                # 30.09: на новой фразе первым — быстрый (естественный) вариант
        self.btn.config(text="▶  Послушать быстро (Enter)")
        # 30.09 (просьба пользователя): образцы готовы заранее — текущую и следующую фразу (оба темпа) озвучиваем в фоне
        for j in (self.i, (self.i + 1) % len(self.phrases)):
            for fast in (False, True):
                self.prefetch(self.phrases[j]["he"], fast)

    def play(self):
        """Образец произношения текущей фразы: из кэша сразу, иначе — Gemini TTS в фоне и потом проиграть."""
        he = self.phrases[self.i]["he"]
        fast = self.fast
        self.fast = not self.fast
        self.btn.config(text="▶  Послушать быстро (Enter)" if self.fast else "▶  Послушать медленно (Enter)")
        f = self.sample_path(he, fast)
        if os.path.exists(f):
            self._play_file(f)
            return
        self.state.config(text="⏳ Готовлю образец произношения…", fg="#e0c060")
        if f in self.pending:                       # уже заказан в фоне — дождаться и проиграть
            def wait():
                while f in self.pending:
                    time.sleep(0.3)
                if os.path.exists(f):
                    self.q.put({"play": f})
            threading.Thread(target=wait, daemon=True).start()
        else:
            self.pending.add(f)
            threading.Thread(target=self._tts, args=(he, f, True, fast), daemon=True).start()

    def sample_path(self, he, fast=False):
        import hashlib
        return os.path.join(DIR, "samples", hashlib.md5(he.encode()).hexdigest() + ("_fast" if fast else "") + ".wav")

    def prefetch(self, he, fast=False):
        f = self.sample_path(he, fast)
        if not os.path.exists(f) and f not in self.pending:
            self.pending.add(f)
            threading.Thread(target=self._tts, args=(he, f, False, fast), daemon=True).start()

    def _play_file(self, f):
        import winsound
        winsound.PlaySound(f, winsound.SND_FILENAME | winsound.SND_ASYNC)

    def _tts(self, he, f, play=True, fast=False):
        try:
            tok, proj = creds()
            body = {"contents": [{"role": "user", "parts": [{"text":
                        ("Произнеси на иврите в обычном разговорном темпе, естественно, как носитель языка в телефонном "
                         "разговоре:\n" if fast else
                         "Прочитай на иврите медленно, чётко и спокойно, как преподаватель для ученика, с правильным "
                         "ударением:\n") + he}]}],
                    "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": {
                        "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": "Charon"}}}}}
            r = requests.post(f"https://aiplatform.googleapis.com/v1/projects/{proj}/locations/global/publishers/google/"
                              f"models/gemini-2.5-flash-tts:generateContent", headers={"Authorization": f"Bearer {tok}"},
                              json=body, timeout=45)
            r.raise_for_status()
            pcm = base64.b64decode(r.json()["candidates"][0]["content"]["parts"][0]["inlineData"]["data"])
            os.makedirs(os.path.dirname(f), exist_ok=True)
            w = wave.open(f, "wb")
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(24000)
            w.writeframes(pcm)
            w.close()
            self.pending.discard(f)
            if play:
                self.q.put({"play": f})
        except Exception as e:
            self.pending.discard(f)
            if play:
                self.q.put({"error": "образец: " + str(e)[:150]})

    def move(self, d):
        if not self.rec:
            self.i = (self.i + d) % len(self.phrases)
            self.show()

    def toggle(self):
        if not self.rec:
            self.buf, self.rec = [], True
            self.stream = sd.InputStream(samplerate=RATE, channels=1, dtype="int16",
                                         callback=lambda d, f, t, s: self.buf.append(d.copy()))
            self.stream.start()
            self.state.config(text="🔴 Запись… читайте фразу, потом пробел", fg="#ff6060")
        else:
            self.stream.stop()
            self.stream.close()
            self.rec = False
            pcm = np.concatenate(self.buf) if self.buf else np.zeros((0, 1), dtype=np.int16)
            self.state.config(text="⏳ Gemini слушает…", fg="#e0c060")
            threading.Thread(target=self.judge, args=(pcm, self.phrases[self.i]), daemon=True).start()

    def judge(self, pcm, p):
        try:
            b = io.BytesIO()
            w = wave.open(b, "wb")
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(pcm.tobytes())
            w.close()
            body = {"contents": [{"role": "user", "parts": [
                        {"inlineData": {"mimeType": "audio/wav", "data": base64.b64encode(b.getvalue()).decode()}},
                        {"text": PROMPT.format(he=p["he"], ru=p["ru"])}]}],
                    "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2,
                                         # 30.09: без раздумья Flash выдавал «т вместо т» — немного подумать
                                         "thinkingConfig": {"thinkingBudget": 512}}}
            # 30.09: Vertex изредка зависает на минуту (обычно ответ за 1–2 с) — короткий срок и до 3 попыток
            for attempt in range(3):
                try:
                    tok, proj = creds()
                    r = requests.post(f"https://aiplatform.googleapis.com/v1/projects/{proj}/locations/global/publishers/"
                                      f"google/models/{MODEL}:generateContent", headers={"Authorization": f"Bearer {tok}"},
                                      json=body, timeout=25)
                    if r.status_code >= 500 or r.status_code == 429:
                        raise requests.HTTPError(f"{r.status_code}")
                    break
                except (requests.Timeout, requests.ConnectionError, requests.HTTPError):
                    if attempt == 2:
                        raise
                    self.q.put({"state": f"⏳ Gemini не ответил, пробую ещё раз ({attempt + 2}/3)…"})
            r.raise_for_status()
            d = json.loads(r.json()["candidates"][0]["content"]["parts"][0]["text"])
            with open(LOGF, "a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "phrase": p["he"], **d},
                                   ensure_ascii=False) + "\n")
            self.q.put(d)
        except Exception as e:
            self.q.put({"error": str(e)[:200]})

    def poll(self):
        try:
            d = self.q.get_nowait()
            if "play" in d:
                self.state.config(text="▶ Слушайте образец, потом пробел — ваша запись", fg="#7fd18b")
                self._play_file(d["play"])
            elif "state" in d:
                self.state.config(text=d["state"], fg="#e0c060")
            elif "error" in d:
                self.state.config(text="⚠ " + d["error"], fg="#ff6060")
            else:
                s = int(d.get("score") or 0)
                self.state.config(text=f"Оценка: {'★' * s}{'☆' * (5 - s)}  ({s}/5)",
                                  fg="#7fd18b" if s >= 4 else "#e0c060" if s == 3 else "#ff9060")
                txt = f"Расслышано: {d.get('heard', '')}\n"
                for pr in d.get("problems") or []:
                    if isinstance(pr, dict):
                        said, should = pr.get("said", "").strip(), pr.get("should", "").strip()
                        if said.lower().replace("ё", "е") == should.lower().replace("ё", "е"):
                            continue                # 30.09: «т вместо т» — не ошибка
                        txt += f"• {pr.get('word', '')}: прозвучало «{said}», надо «{should}» — {pr.get('fix', '')}\n"
                    else:
                        txt += f"• {pr}\n"
                if "•" not in txt:
                    txt += "• Ошибок не слышно 👍\n"
                txt += f"\n💡 {d.get('tip', '')}"
                self.fb.config(text=txt)
        except queue.Empty:
            pass
        self.root.after(150, self.poll)


if __name__ == "__main__":
    t = Trainer()
    t.root.geometry("1280x720")
    t.root.mainloop()
