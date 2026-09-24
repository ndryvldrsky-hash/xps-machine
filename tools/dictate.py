# -*- coding: utf-8 -*-
"""Голос на XPS: диктовка по клавише и вызов «Алёна» без рук.

1. Диктовка: зажал ПРАВЫЙ Ctrl — говоришь, отпустил — текст печатается в активное окно. Нажатие другой
   клавиши во время удержания (обычное Ctrl+…) отменяет запись; удержание короче 0,3 с — тоже отмена.
2. «Алёна, …» или «Эй, Алёна, …»: детектор речи по громкости режет микрофон на фразы; первые 1,5 с каждой фразы уходят в GigaAM
   на Нуксе. Если там «Алёна» — сигнал, фраза пишется до паузы 1 с (макс. 25 с) и распознаётся целиком,
   обращение срезается, текст печатается в активное окно; если это VS Code (чат Claude Code со мной) — с Enter,
   и тогда первые 1–2 фразы моего ответа проигрываются голосом (voice_reply.py на Нуксе, Piper, голос Ирины).
   (Vosk small-ru не подошёл: в его словаре нет имени «Алёна».)

Распознавание локальное: Wyoming STT на Нуксе (аддон «Whisper», модель GigaAM v3 e2e через onnx-asr,
192.168.77.2:10300). Звук в интернет не уходит.
Сигналы: 1200 Гц — слушаю, 700 — отправлено, 300 — ошибка, 500+400 — «Алёна» не подтвердилась.
Выключить режим «Алёна», не трогая диктовку: создать файл no_wake рядом со скриптом (проверяется каждую секунду).
Запуск: задача планировщика «AlenaDictate» при входе rdpuser (pythonw). Лог — dictate.log рядом.
"""
import collections, ctypes, datetime, json, os, queue, re, socket, threading, time, urllib.request, winsound

import keyboard
import numpy as np
import sounddevice as sd

HERE = os.path.dirname(os.path.abspath(__file__))
HOST, PORT = "192.168.77.2", 10300
RATE = 16000
BLOCK = 480                      # 30 мс
HOTKEY = "right ctrl"
MIN_SEC = 0.3
LOG = os.path.join(HERE, "dictate.log")
NO_WAKE = os.path.join(HERE, "no_wake")
DEBUG = os.path.exists(os.path.join(HERE, "debug_wake"))   # подробный лог Vosk: создать файл debug_wake
WAKE_RE = re.compile(r"^\s*(?:(?:эй|хей|ну)[\s,.!?:—-]+)?[ао]л[её]н(?:а|у|ушка|очка|ка)?\b[\s,.!?:—-]*", re.I)   # «Алёна», «Эй, Алёна»
PRE_ROLL_SEC = 0.3               # звук до начала речи, чтобы не съесть первый слог
PROBE_SEC = 1.5                  # сколько начала фразы отправить на проверку «Алёна»
MIN_VOICED_SEC = 0.4             # меньше «голосовых» 30-мс блоков в начале фразы — щелчок/шум, пробу не шлём
KEY_QUIET_SEC = 0.8              # звук в пределах 0,8 с от нажатия клавиши — это набор текста, не голос
SILENCE_END_SEC = 1.0            # пауза, завершающая фразу
MAX_UTT_SEC = 25.0
NO_SPEECH_SEC = 5.0              # если после «Алёна» тишина — отмена


def log(msg):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")


def beep(*tones):
    def run():
        for fr, ms in tones:
            winsound.Beep(fr, ms)
    threading.Thread(target=run, daemon=True).start()


def wy_send(s, typ, data=None, payload=b""):
    h = {"type": typ}
    if data is not None:
        h["data"] = data
    if payload:
        h["payload_length"] = len(payload)
    s.sendall((json.dumps(h) + "\n").encode() + payload)


def transcribe(pcm: bytes) -> str:
    """16 кГц моно int16 → Wyoming STT на Нуксе → текст."""
    fmt = {"rate": RATE, "width": 2, "channels": 1}
    with socket.create_connection((HOST, PORT), timeout=30) as s:
        f = s.makefile("rb")
        wy_send(s, "transcribe", {"language": "ru"})
        wy_send(s, "audio-start", fmt)
        for i in range(0, len(pcm), 16000):
            wy_send(s, "audio-chunk", fmt, pcm[i:i + 16000])
        wy_send(s, "audio-stop")
        while True:
            h = json.loads(f.readline())
            d = h.get("data") or {}
            if h.get("data_length"):
                d = json.loads(f.read(h["data_length"]))
            if h.get("payload_length"):
                f.read(h["payload_length"])
            if h["type"] == "transcript":
                return (d.get("text") or "").strip()


def foreground_title() -> str:
    u = ctypes.windll.user32
    hwnd = u.GetForegroundWindow()
    buf = ctypes.create_unicode_buffer(512)
    u.GetWindowTextW(hwnd, buf, 512)
    return buf.value


REPLY_URL = f"http://{HOST}:8123/local/voice_reply/"
REPLY_WAIT_SEC = 180


def norm(t):
    return re.sub(r"\W+", " ", t).strip().lower()


def speak_reply(sent, t_sent):
    """Ждать озвученный ответ Алёны на отправленную голосом фразу и проиграть его.

    Ответ готовит voice_reply.py в аддоне на Нуксе: /local/voice_reply/voice_reply.json + WAV (голос Piper).
    Сопоставление — по началу отправленного текста в поле `for`."""
    key = norm(sent)[:40]
    deadline = time.time() + REPLY_WAIT_SEC
    while time.time() < deadline:
        time.sleep(1.5)
        try:
            with urllib.request.urlopen(REPLY_URL + "voice_reply.json?_=%d" % time.time(), timeout=5) as r:
                meta = json.loads(r.read().decode("utf-8"))
        except Exception:
            continue
        if meta.get("ts", 0) < t_sent - 2 or key not in norm(meta.get("for", "")):
            continue
        try:
            with urllib.request.urlopen(REPLY_URL + meta["wav"], timeout=10) as r:
                data = r.read()
            log(f"ответ голосом: {meta.get('text', '')}")
            winsound.PlaySound(data, winsound.SND_MEMORY)
        except Exception as e:
            log(f"не удалось проиграть ответ: {e!r}")
        return
    log("ответ голосом не дождалась (3 мин)")


class Voice:
    def __init__(self):
        # --- диктовка по клавише
        self.rec = False
        self.cancelled = False
        self.t0 = 0.0
        self.chunks = []
        # --- «Алёна»
        self.ring = collections.deque(maxlen=int(PRE_ROLL_SEC * RATE / BLOCK))
        self.seg = None
        self.seg_t0 = 0.0
        self.state = ""
        self.wake_q = queue.Queue()
        self.last_voice = 0.0
        self.noise = 50.0
        self.wake_enabled = True
        self.busy = False
        self.dbg_t = 0.0
        self.last_key = 0.0
        self.voiced_n = 0
        self.probes = 0
        self.skipped = 0
        self.win_max = 0.0
        self.stream = sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=BLOCK, callback=self._cb)
        self.stream.start()
        threading.Thread(target=self._wake_loop, daemon=True).start()
        threading.Thread(target=self._flag_loop, daemon=True).start()

    def _cb(self, indata, frames, t, status):
        block = indata.copy()
        if self.rec:
            self.chunks.append(block)
        self.wake_q.put(block)

    def _flag_loop(self):
        while True:
            en = not os.path.exists(NO_WAKE)
            if en != self.wake_enabled:
                self.wake_enabled = en
                log("режим «Алёна» " + ("включён" if en else "выключен (файл no_wake)"))
            time.sleep(1)

    # ---------- диктовка по клавише ----------
    def key_start(self):
        if self.rec:
            return
        self.chunks = []
        self.cancelled = False
        self.t0 = time.time()
        self.rec = True
        beep((1200, 60))

    def key_stop(self):
        if not self.rec:
            return
        self.rec = False
        dur = time.time() - self.t0
        if self.cancelled or dur < MIN_SEC or not self.chunks:
            return
        pcm = np.concatenate(self.chunks).tobytes()
        threading.Thread(target=self._deliver, args=(pcm, dur, False), daemon=True).start()

    # ---------- «Алёна» ----------
    # Vosk-модель small-ru не знает слова «Алёна» (слышит «она»/«подобно»), поэтому слово-триггер проверяет сам
    # GigaAM: детектор речи по громкости режет поток на фразы, первые PROBE_SEC каждой фразы (с 0,3 с до начала)
    # уходят на Нукс; если распознанное начинается с «Алёна» — сигнал, и фраза пишется до паузы целиком.
    def _wake_loop(self):
        import traceback
        while True:
            try:
                self._wake_step()
            except Exception:
                log("ошибка в потоке «Алёна»: " + traceback.format_exc().replace("\n", " | ")[-600:])
                self.seg = None
                time.sleep(1)

    def _wake_step(self):
        block = self.wake_q.get()
        rms = float(np.sqrt(np.mean(block.astype(np.float32) ** 2)))
        now = time.time()
        self.win_max = max(self.win_max, rms)
        if DEBUG and now - self.dbg_t > 30:
            self.dbg_t = now
            log(f"слушаю: макс rms за 30 с={self.win_max:.0f} шум={self.noise:.0f} порог={self._thr():.0f} проб={self.probes} отсеяно={self.skipped}")
            self.win_max = 0.0
            self.probes = self.skipped = 0
        voiced = rms > self._thr()
        if not voiced and self.seg is None:
            self.noise = 0.97 * self.noise + 0.03 * max(rms, 1.0)     # фон учим только вне речи
        if self.seg is None:
            self.ring.append(block)
            if voiced and self.wake_enabled and not self.rec and not self.busy and now - self.last_key > KEY_QUIET_SEC:
                self.voiced_n = 0
                self.seg = list(self.ring)               # 0,3 с до начала речи
                self.seg_t0 = now
                self.last_voice = now
                self.state = "probe"
            return
        self.seg.append(block)
        if voiced:
            self.last_voice = now
            self.voiced_n += 1
        if self.state == "probe" and now - self.last_key < KEY_QUIET_SEC:
            self.seg = None                       # начали печатать — это был не голос
            self.ring.clear()
            self.skipped += 1
            return
        seg_sec = len(self.seg) * BLOCK / RATE
        ended = now - self.last_voice > SILENCE_END_SEC
        if self.state == "probe" and (seg_sec >= PROBE_SEC or ended):
            if self.voiced_n * BLOCK / RATE < MIN_VOICED_SEC:
                self.seg = None                   # щелчок, стук, короткий шум — на Нукс не шлём
                self.ring.clear()
                self.skipped += 1
                return
            self.probes += 1
            pcm = np.concatenate(self.seg).tobytes()
            try:
                text = transcribe(pcm)
            except Exception as e:
                log(f"проба не удалась: {e!r}")
                text = ""
            if DEBUG and text:
                log(f"проба {seg_sec:.1f} с: {text!r}")
            if WAKE_RE.match(text):
                log(f"«Алёна» услышана в пробе: {text!r} — пишу фразу до паузы")
                beep((1200, 60))
                self.state = "utt"
                # пока шёл запрос, звук копился в очереди — дозабираем его в фразу
                while not self.wake_q.empty():
                    self.seg.append(self.wake_q.get_nowait())
                self.last_voice = time.time()
            else:
                self.seg = None
                self.ring.clear()
            return
        if self.state == "utt" and (ended or now - self.seg_t0 > MAX_UTT_SEC):
            pcm = np.concatenate(self.seg).tobytes()
            self.seg = None
            self.ring.clear()
            self.state = ""
            log(f"фраза записана: {len(pcm) / 2 / RATE:.1f} с, отправляю")
            threading.Thread(target=self._deliver, args=(pcm, len(pcm) / 2 / RATE, True), daemon=True).start()

    def _thr(self):
        return max(120.0, self.noise * 4)

    # ---------- распознать и напечатать ----------
    def _deliver(self, pcm, dur, wake):
        import traceback
        try:
            self._deliver2(pcm, dur, wake)
        except Exception:
            log("ошибка при отправке: " + traceback.format_exc().replace("\n", " | ")[-700:])
            self.busy = False

    def _deliver2(self, pcm, dur, wake):
        self.busy = True
        try:
            beep((700, 60))
            t0 = time.time()
            try:
                text = transcribe(pcm)
            except Exception as e:
                log(f"ошибка распознавания: {e!r}")
                beep((300, 300))
                return
            took = time.time() - t0
            if wake:
                m = WAKE_RE.match(text)
                if not m:
                    log(f"«Алёна» не подтвердилась ({dur:.1f} с → {took:.1f} с): {text!r}")
                    beep((500, 80), (400, 80))
                    return
                text = text[m.end():].strip()
                if text:
                    text = text[0].upper() + text[1:]
            title = foreground_title()
            send = wake and "Visual Studio Code" in title
            log(f"{'Алёна' if wake else 'клавиша'}: {dur:.1f} с → {took:.1f} с, окно «{title[:60]}»{' +Enter' if send else ''}: {text}")
            if not text:
                return
            keyboard.write(text if send else text + " ")
            if send:
                time.sleep(0.15)
                keyboard.send("enter")
                threading.Thread(target=speak_reply, args=(text, time.time()), daemon=True).start()
        finally:
            self.busy = False


def main():
    v = Voice()

    def on_event(e):
        if e.event_type == "down":
            v.last_key = time.time()
        if e.name == HOTKEY:
            if e.event_type == "down":
                v.key_start()
            else:
                v.key_stop()
        elif e.event_type == "down" and v.rec:
            v.cancelled = True

    keyboard.hook(on_event)
    log("запущено: правый Ctrl — диктовка; «Алёна, …» — фраза в активное окно (в VS Code с Enter)")
    keyboard.wait()


if __name__ == "__main__":
    main()
