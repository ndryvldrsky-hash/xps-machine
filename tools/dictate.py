# -*- coding: utf-8 -*-
"""Голос на XPS: диктовка по клавише и вызов «Алёна» без рук.

1. Диктовка: зажал ПРАВЫЙ Ctrl — говоришь, отпустил — текст печатается в активное окно. Нажатие другой
   клавиши во время удержания (обычное Ctrl+…) отменяет запись; удержание короче 0,3 с — тоже отмена.
2. «Алёна, …» или «Эй, Алёна, …»: детектор речи по громкости режет микрофон на фразы; первые 1,5 с каждой фразы уходят в GigaAM
   на Нуксе. Если там «Алёна» — сигнал, фраза пишется до паузы 1 с (макс. 25 с) и распознаётся целиком,
   обращение срезается, текст печатается в активное окно; если это VS Code (чат Claude Code со мной) — с Enter,
   и тогда мой ответ целиком проигрывается голосом (правый Ctrl — перебить) (voice_reply.py на Нуксе, Piper, голос Ирины).
   (Vosk small-ru не подошёл: в его словаре нет имени «Алёна».)

Распознавание локальное: Wyoming STT на Нуксе (аддон «Whisper», модель GigaAM v3 e2e через onnx-asr,
192.168.77.2:10300). Звук в интернет не уходит.
Сигналы: 1200 Гц — слушаю, 700 — отправлено, 300 — ошибка, 500+400 — «Алёна» не подтвердилась.
Выключить режим «Алёна», не трогая диктовку: создать файл no_wake рядом со скриптом (проверяется каждую секунду).
Запуск: задача планировщика «AlenaDictate» при входе rdpuser (pythonw). Лог — dictate.log рядом.
"""
import collections, ctypes, io, wave, datetime, json, os, queue, re, socket, threading, time, urllib.request, winsound

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
WAKE_RE = re.compile(r"^\s*(?:(?:эй|хей|ну|о['’]?кей|ок)[\s,.!?:—-]+)?[ао]л[её]н(?:а|у|ушка|очка|ка)?\b[\s,.!?:—-]*", re.I)   # «Алёна», «Эй/Окей, Алёна»
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


# 26.09 (просьба пользователя «более мягкий звук, чем писк»): вместо winsound.Beep (прямоугольный писк полной громкости) —
# синусоида с плавным нарастанием/затуханием, тише и ниже; частоты сдвинуты вниз с сохранением смысла сигналов
# (высокий — слушаю, средний — отправлено, низкий — ошибка). Играет через winsound.PlaySound (WAV в памяти) — тем же
# путём, что голосовые ответы: sounddevice (PortAudio) выбирал не то устройство, на мониторе SHARP HDMI сигнал
# не звучал. Сигнал может оборвать играющий ответ — но он звучит только на нажатие/речь, а они и так прерывают ответ.
# 26.09 «ещё мягче… аккордом… ситар»: каждый сигнал — аккорд перебором на «ситаре» (см. _soft_tone). Смысл прежний: слушаю — до мажор, отправлено — фа мажор, ошибка — ля минор,
# «Алёна» не подтвердилась — ре минор.
# 26.09 «гаммы классиков… разделить на убывающее и возрастающее»: «слушаю» — восходящий мотив, «отправлено» —
# нисходящий; каждый набор идёт по кругу независимо, ритм из оригинала (длительность в долях NOTE_UNIT), играет «ситар».
# Ошибка и «не Алёна» — постоянные. Ноты — MIDI (60 = C4).
MOTIFS_UP = [("Штраус, «Заратустра»", ((60, 2), (67, 2), (72, 3))),
             ("Бетховен, «Ода к радости»", ((64, 1), (64, 1), (65, 1), (67, 2))),
             ("Моцарт, «Маленькая ночная серенада»", ((67, 2), (62, 1), (67, 1), (71, 1), (74, 2))),
             ("Григ, «Утро» (подъём)", ((74, 1), (76, 1), (79, 1), (81, 2)))]
MOTIFS_DOWN = [("Григ, «Утро»", ((79, 1), (76, 1), (74, 1), (72, 2))),
               ("Дворжак, Ларго", ((64, 2), (67, 1), (67, 2), (64, 2), (62, 1), (60, 2))),
               ("Бетховен, «К Элизе»", ((76, 1), (75, 1), (76, 1), (71, 1), (74, 1), (72, 1), (69, 2))),
               ("Бах, «Шутка»", ((71, 1), (74, 1), (71, 1), (66, 1), (71, 2))),
               # 26.09 «добавь нисходящих» (длинные, для «отправлено»)
               ("Пахельбель, «Канон»", ((78, 2), (76, 2), (74, 2), (73, 2), (71, 2), (69, 3))),
               ("Бах, Токката и фуга ре минор", ((69, 1), (67, 1), (69, 3), (67, 1), (65, 1), (64, 1), (62, 1), (61, 2), (62, 3))),
               ("Бетховен, Пятая симфония", ((67, 1), (67, 1), (67, 1), (63, 4), (65, 1), (65, 1), (65, 1), (62, 4)))]
NOTE_UNIT = 0.09
# 26.09 «инструмент, который звуком ниже»: сурбахар (бас-ситар) — всё на октаву вниз, щипок мягче (толстая струна).
# TRANSPOSE — сдвиг в полутонах (0 — ситар, −12 — сурбахар), PLUCK — яркость щипка (доля «сырого» шума, ситар 0,7)
TRANSPOSE = -12
PLUCK = 0.45
# 26.09 «что-нибудь низкое, похожее на виолончель»: INSTRUMENT = "cello" — смычковый синтез (см. _cello);
# "sitar" — щипковый (Карплус–Стронг + джавари + тараб), с TRANSPOSE −12 — сурбахар
INSTRUMENT = "sf2_sitar"    # 26.09 ситар из SF2 (до этого "vsco_harp" — «давай арфу», ещё раньше "vsco_cello")
# Живые сэмплы VSCO-2 CE (CC0, Versilian Studios; скачаны на XPS в samples\<папка>, не в git). У инструмента: папка,
# маска файлов, сдвиг октавы в имени (виолончель: «C1» = 65 Гц → +2; арфа — научная нумерация → +1), легато
# (смычок: нота до следующей) или звон (щипок: ноты звенят поверх друг друга), темп «слушаю»/«отправлено», транспозиция.
# Пока сэмплы грузятся (первые доли секунды после старта) — синтез "cello".
VSCO_INST = {
    "vsco_cello": {"dir": "cello_susvib", "mask": "susvib_*_v1_1.wav", "oct": 2, "legato": True, "tempo": (1.15, 1.6), "tr": -12},
    "vsco_harp": {"dir": "harp", "mask": "KSHarp_*.wav", "oct": 1, "legato": False, "tempo": (1.0, 1.3), "tr": 0},
    # ситар: 105-Sitar.sf2 (musical-artifacts #3847, общественное достояние, Dr. Narayan Bhagawan Raikar), разобран
    # в аддоне HA на 26 нот MIDI 36–79 → samples\\sitar\\sitar_<midi>.wav (имя — сразу номер MIDI, признак "midi")
    "sf2_sitar": {"dir": "sitar", "mask": "sitar_*.wav", "midi": True, "legato": False, "tempo": (1.0, 1.3), "tr": 0},
}
VSCO_DIR = os.path.join(HERE, "samples")
_vsco = {}                  # midi → моно float32 при BEEP_SR (текущего инструмента)


def _vsco_load():
    import glob as _glob
    cfg = VSCO_INST[INSTRUMENT]
    names = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
    for p in sorted(_glob.glob(os.path.join(VSCO_DIR, cfg["dir"], cfg["mask"]))):
        nm = os.path.splitext(os.path.basename(p))[0].split("_")[1]   # «A2», «C1»… или сразу номер MIDI
        midi = int(nm) if cfg.get("midi") else 12 * (int(nm[1:]) + cfg["oct"]) + names[nm[0]]
        w = wave.open(p); ch, sw, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(min(n, int(sr * 3.0))); w.close()  # хватает первых 3 с
        if sw == 3:
            a = np.frombuffer(raw, np.uint8).reshape(-1, 3)
            x = a[:, 0].astype(np.int32) | (a[:, 1].astype(np.int32) << 8) | (a[:, 2].astype(np.int32) << 16)
            x = np.where(x & 0x800000, x - 0x1000000, x) / 8388608.0
        else:
            x = np.frombuffer(raw, "<i2") / 32768.0
        x = x.reshape(-1, ch).mean(axis=1)
        k = int(np.argmax(np.abs(x) > 0.02 * np.abs(x).max()))  # без тишины в начале
        x = x[max(0, k - int(0.005 * sr)):]
        t = np.arange(0, len(x) * BEEP_SR / sr) * sr / BEEP_SR   # 44,1 → 48 кГц
        _vsco[midi] = np.interp(t, np.arange(len(x)), x).astype(np.float32)
    log(f"сэмплы VSCO ({INSTRUMENT}): загружено {len(_vsco)} нот")


def _vsco_render(notes, onsets, n, last=0.7):
    out = np.zeros(n)
    for k, f in enumerate(notes):
        target = 69 + 12 * np.log2(f / 440)
        src = min(_vsco, key=lambda m: abs(m - target))
        x = _vsco[src]
        rate = 2 ** ((target - src) / 12)
        d = int(onsets[k] * BEEP_SR)
        if VSCO_INST[INSTRUMENT]["legato"]:
            dur = (onsets[k + 1] - onsets[k] + 0.06) if k + 1 < len(notes) else last
        else:
            dur = n / BEEP_SR - onsets[k]                   # щипок: нота звенит до конца сигнала
        m = min(n - d, int((dur + 0.18) * BEEP_SR), int(len(x) / rate) - 1)
        y = np.interp(np.arange(m) * rate, np.arange(len(x)), x)
        env = np.ones(m)
        a = min(m, int(0.012 * BEEP_SR)); env[:a] = np.linspace(0, 1, a)       # без щелчка
        rel = int(dur * BEEP_SR)
        if rel < m:
            env[rel:] *= np.linspace(1, 0, m - rel) ** 1.5                     # снятие смычка / переход к следующей
        out[d:d + m] += y * env
    return out


def _cello(notes, onsets, n, rng):
    """Смычковая «виолончель»: у ноты 24 гармоники ~1/k (как пилообразная волна) с окраской корпуса (подъём около 250
    и 600 Гц, спад выше 2,5 кГц), атака смычка 70 мс, вибрато 5,5 Гц ±0,5 % через 0,15 с, лёгкий шум смычка; ноты легато —
    каждая тянется до вступления следующей (+40 мс перекрытия), последняя — 0,45 с, затухание 0,15 с."""
    out = np.zeros(n)
    for k, f in enumerate(notes):
        d = int(onsets[k] * BEEP_SR)
        dur = (onsets[k + 1] - onsets[k] + 0.04) if k + 1 < len(notes) else 0.45
        m = min(n - d, int((dur + 0.15) * BEEP_SR))
        t = np.arange(m) / BEEP_SR
        vib = 1 + 0.005 * np.sin(2 * np.pi * 5.5 * t) * np.clip((t - 0.15) / 0.12, 0, 1)
        ph = 2 * np.pi * f * np.cumsum(vib) / BEEP_SR
        w = np.zeros(m)
        for h in range(1, 25):
            hf = h * f
            if hf > BEEP_SR / 2.2:
                break
            g = (1 / h) / (1 + (hf / 2500) ** 2) * (1 + 0.8 * np.exp(-((hf - 250) / 120) ** 2) + 0.5 * np.exp(-((hf - 600) / 250) ** 2))
            w += g * np.sin(h * ph + 0.3 * h)
        w += 0.02 * np.convolve(rng.standard_normal(m), np.ones(8) / 8, mode="same")   # шум смычка
        env = np.clip(t / 0.07, 0, 1) ** 1.3                                          # атака смычка
        rel = int(dur * BEEP_SR)
        if rel < m:
            env[rel:] *= np.linspace(1, 0, m - rel)                                   # снятие смычка
        out[d:d + m] += w * env
    return out
# 26.09 «гаммы-ситара тоже оставь»: раги (Билавал, Бхупали, Дурга, Кафи) в тех же кругах, чередуясь с мотивами:
# вверх от C5 — к «слушаю», вниз к C4 — к «отправлено»; темп гамм — 50 мс на ноту (NOTE_STEP / NOTE_UNIT долей)
_RAGA_FIG = [("Билавал", (0, 2, 4, 7)), ("Бхупали", (0, 2, 4, 7, 9)), ("Дурга", (0, 2, 5, 7, 9)), ("Кафи", (0, 2, 3, 7))]
_g = 0.05 / NOTE_UNIT
from itertools import zip_longest as _zl   # чередование без потерь (zip обрезал по короткому списку — мотивы сверх 4 пропадали)
MOTIFS_UP = [m for pair in _zl(MOTIFS_UP, [(f"рага {n}", tuple((72 + x, _g) for x in fig)) for n, fig in _RAGA_FIG]) for m in pair if m]
MOTIFS_DOWN = [m for pair in _zl(MOTIFS_DOWN, [(f"рага {n}", tuple((60 + x, _g) for x in reversed(fig))) for n, fig in _RAGA_FIG]) for m in pair if m]
# 26.09 «короткие в начало, длинные в конец»: наборы пересобраны по длине, а не по направлению — «слушаю» звучит,
# когда микрофон уже пишет, поэтому туда только короткие (ноты укладываются в ≤0,5 с), длинные — на «отправлено»
_span = lambda m: sum(ln for _n, ln in m[1][:-1]) * NOTE_UNIT
# … и «те, которые не совпадают по направлению, убрать»: «слушаю» — короткие ВОСХОДЯЩИЕ, «отправлено» — длинные
# НИСХОДЯЩИЕ; короткие нисходящие и длинные восходящие не звучат
MOTIFS_UP = [m for m in MOTIFS_UP if _span(m) <= 0.5]      # «слушаю» — короткие восходящие
MOTIFS_DOWN = [m for m in MOTIFS_DOWN if _span(m) > 0.5]   # «отправлено» — длинные нисходящие
_motif = {1200: [len(MOTIFS_UP) - 1], 700: [len(MOTIFS_DOWN) - 1]}


def _hz(midi):
    return 440.0 * 2 ** ((midi - 69) / 12)


def _motif_notes(fr, idx):
    """(частоты, моменты вступления в секундах) мотива idx для сигнала fr."""
    seq = (MOTIFS_UP if fr == 1200 else MOTIFS_DOWN)[idx][1]
    hz, on, t = [], [], 0.0
    for m, ln in seq:
        hz.append(_hz(m)); on.append(t); t += ln * NOTE_UNIT
    return tuple(hz), tuple(on)


CHORDS = {300: (261.63, 246.94, 220.0),             # C4 B3 A3 — ошибка
          500: (293.66, 349.23, 293.66),            # D4 F4 D4 — не «Алёна»
          400: (293.66, 349.23, 293.66)}
NOTE_STEP = 0.05   # 26.09 «чуть побыстрее» (было 0,07)
BEEP_VOL = 0.3    # 26.09: 0.12 при системной 19 % не слышно, 0.45 — «ещё мягче»
BEEP_SR = 48000


def _ks(f, n, decay, rng, bright=0.5):
    """Щипковая струна Карплуса–Стронга (векторно по периодам): линия задержки ~sr/f из шума, каждый проход —
    усреднение соседних отсчётов с потерей decay; bright — доля «сырого» шума в первом периоде (яркость щипка)."""
    N = max(2, int(round(BEEP_SR / f)))
    buf = rng.uniform(-1, 1, N)
    buf = bright * buf + (1 - bright) * np.convolve(buf, np.ones(3) / 3, mode="same")
    out = np.empty(n)
    for i in range(0, n, N):
        m = min(N, n - i)
        out[i:i + m] = buf[:m]
        buf = decay * 0.5 * (buf + np.roll(buf, -1))
    return out


_tone_cache = {}


def _soft_tone(fr, ms, raga=None):
    """26.09 «сделай ситар»: аккорд перебором (ноты сверху вниз с шагом 40 мс), каждая — щипковая струна
    Карплуса–Стронга с жужжанием джавари (мягкое насыщение tanh) и тихие резонансные струны тараб на тех же нотах
    октавой выше, вступающие через 60 мс с долгим звоном. Длина 0,9 с; звуки просчитываются один раз (кэш)."""
    idx = (_motif[fr][0] if raga is None else raga) if fr in (1200, 700) else -1
    key = (fr, idx, INSTRUMENT in VSCO_INST and bool(_vsco))   # живые сэмплы / синтез до их загрузки
    if key in _tone_cache:
        return _tone_cache[key]
    if idx >= 0:
        notes, onsets = _motif_notes(fr, idx)
    else:
        notes = CHORDS.get(fr, (fr,)); onsets = tuple(NOTE_STEP * k for k in range(len(notes)))
    live = INSTRUMENT in VSCO_INST and bool(_vsco)
    if live:   # «слушаю» — микрофон уже пишет: темп быстрее и короткая последняя нота (≈0,8 с); «отправлено» — полностью
        tp = VSCO_INST[INSTRUMENT]["tempo"]
        onsets = tuple(o * (tp[1] if fr != 1200 else tp[0]) for o in onsets)
    tr = VSCO_INST[INSTRUMENT]["tr"] if INSTRUMENT in VSCO_INST else TRANSPOSE
    notes = tuple(f * 2 ** (tr / 12) for f in notes)            # сдвиг регистра инструмента (сурбахар/виолончель −12)
    n = int(BEEP_SR * (onsets[-1] + 0.75))
    rng = np.random.default_rng(11)
    out = np.zeros(n)
    if live:
        last = (0.3 if fr == 1200 else 0.7) if VSCO_INST[INSTRUMENT]["legato"] else (0.45 if fr == 1200 else 1.1)
        n = int(BEEP_SR * (onsets[-1] + last + 0.25))
        out = _vsco_render(notes, onsets, n, last)
        fade = int(0.05 * BEEP_SR)
        out[-fade:] *= np.linspace(1, 0, fade)
        out = BEEP_VOL * out / max(1e-6, np.abs(out).max()) * 0.8
        _tone_cache[key] = out.astype(np.float32)
        return _tone_cache[key]
    if INSTRUMENT == "cello" or INSTRUMENT in VSCO_INST:
        out = _cello(notes, onsets, n, rng)
        fade = int(0.05 * BEEP_SR)
        out[-fade:] *= np.linspace(1, 0, fade)
        out = BEEP_VOL * out / max(1e-6, np.abs(out).max()) * 0.8
        _tone_cache[key] = out.astype(np.float32)
        return _tone_cache[key]
    for k, f in enumerate(notes):                                 # ноты по очереди, прежние ещё звенят
        d = int(onsets[k] * BEEP_SR)
        s1 = _ks(f, n - d, 0.995, rng, PLUCK) * (0.8 if k < len(notes) - 1 else 1.0)
        s1 = 0.55 * s1 + 0.45 * np.tanh(3.0 * s1) / np.tanh(3.0)   # джавари: «жужжание» подставки
        out[d:] += s1
    dl = int(onsets[-1] * BEEP_SR) + int(0.06 * BEEP_SR)          # тараб на последней ноте — долгий звон
    out[dl:] += 0.18 * _ks(2 * notes[-1], n - dl, 0.9985, rng, 0.2)
    fade = int(0.08 * BEEP_SR)
    out[-fade:] *= np.linspace(1, 0, fade)
    out = BEEP_VOL * out / max(1e-6, np.abs(out).max()) * 0.8
    _tone_cache[key] = out.astype(np.float32)
    return _tone_cache[key]


# прогрев: все сигналы просчитываются в фоне при старте, чтобы первый же не опаздывал на ~0,2 с
def _warm():
    if INSTRUMENT in VSCO_INST:
        try:
            _vsco_load()
        except Exception as e:
            log(f"сэмплы VSCO не загрузились, играет синтез: {e!r}")
    for f in CHORDS:
        _soft_tone(f, 0)
    for i in range(len(MOTIFS_UP)):      # все мотивы (индекс передаётся явно — без гонки с beep)
        _soft_tone(1200, 0, i)
    for i in range(len(MOTIFS_DOWN)):
        _soft_tone(700, 0, i)


threading.Thread(target=_warm, daemon=True).start()


class Balloon:
    """26.09 («мотив, который прозвучал, показывать в виде баллона над курсором»): маленькая подсказка над указателем
    мыши на ~2,5 с. Своё окно Tk в отдельном потоке; окно НЕ активируется (WS_EX_NOACTIVATE, показ SW_SHOWNOACTIVATE),
    прозрачно для кликов (WS_EX_TRANSPARENT) и без кнопки на панели задач — фокус остаётся в окне, куда печатается текст."""

    def __init__(self):
        self.q = queue.Queue()
        threading.Thread(target=self._run, daemon=True).start()

    def show(self, text):
        self.q.put(text)

    def _run(self):
        try:
            import tkinter as tk
        except Exception as e:
            log(f"баллон: нет tkinter: {e!r}")
            return
        root = tk.Tk()
        root.withdraw()
        u32 = ctypes.windll.user32
        cur = [None, None]   # (окно, id таймера)

        def pop():
            try:
                text = self.q.get_nowait()
            except queue.Empty:
                root.after(50, pop)
                return
            if cur[0] is not None:
                cur[0].destroy()
            w = tk.Toplevel(root)
            w.withdraw()
            w.overrideredirect(True)
            w.attributes("-topmost", True)
            w.attributes("-alpha", 0.92)
            tk.Label(w, text=text, font=("Segoe UI", 11), bg="#FFF6D8", fg="#3A2E10", padx=10, pady=5,
                     relief="solid", bd=1).pack()
            w.update_idletasks()

            class PT(ctypes.Structure):
                _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
            pt = PT()
            u32.GetCursorPos(ctypes.byref(pt))
            # границы монитора, где указатель (у мониторов левее/выше основного координаты отрицательные — прежний
            # max(0, …) загонял баллон в левый верхний угол основного экрана)
            class MI(ctypes.Structure):
                _fields_ = [("cb", ctypes.c_ulong), ("rc", ctypes.c_long * 4), ("wk", ctypes.c_long * 4), ("fl", ctypes.c_ulong)]
            mi = MI(); mi.cb = ctypes.sizeof(MI)
            u32.MonitorFromPoint.restype = ctypes.c_void_p
            hm = u32.MonitorFromPoint(pt, 2)                     # MONITOR_DEFAULTTONEAREST
            u32.GetMonitorInfoW(ctypes.c_void_p(hm), ctypes.byref(mi))
            L, T, R, B = mi.wk[0], mi.wk[1], mi.wk[2], mi.wk[3]
            bw, bh = w.winfo_reqwidth(), w.winfo_reqheight()
            x = min(max(L, pt.x - bw // 2), R - bw)
            y = pt.y - bh - 18                                   # над указателем
            if y < T:
                y = pt.y + 24                                    # сверху нет места — под указателем
            y = min(y, B - bh)
            # положение задаёт сам Tk (показ мимо Tk — ShowWindow/SetWindowPos — он игнорировал: окно оставалось в 0,0);
            # стиль «не активироваться» ставится ДО показа, а если фокус всё же ушёл — сразу возвращается
            w.geometry(f"+{x}+{y}")
            hwnd = int(w.wm_frame(), 16)                         # внешняя рамка окна Tk
            ex = u32.GetWindowLongW(hwnd, -20)
            u32.SetWindowLongW(hwnd, -20, ex | 0x08000000 | 0x80 | 0x20 | 0x8)   # NOACTIVATE|TOOLWINDOW|TRANSPARENT|TOPMOST
            fg = u32.GetForegroundWindow()
            w.deiconify()
            w.update_idletasks()
            stole = u32.GetForegroundWindow() != fg
            if stole and fg:
                u32.SetForegroundWindow(fg)
            r = (ctypes.c_long * 4)()
            u32.GetWindowRect(hwnd, ctypes.byref(r))
            log(f"баллон: мышь {pt.x},{pt.y} → {x},{y}; окно {list(r)}" + ("; фокус уходил — возвращён" if stole else ""))

            def fade(a=0.92):
                if cur[0] is not w:
                    return
                if a <= 0.05:
                    w.destroy(); cur[0] = None
                    return
                w.attributes("-alpha", a)
                w.after(40, fade, a - 0.08)
            cur[0] = w
            w.after(2500, fade)
            root.after(50, pop)

        root.after(50, pop)
        root.mainloop()


_balloon = [None]


def balloon(text):
    try:
        if _balloon[0] is None:
            _balloon[0] = Balloon()
        _balloon[0].show(text)
    except Exception as e:
        log(f"баллон: {e!r}")


def beep(*tones):
    if tones and tones[0][0] in _motif:   # «слушаю»/«отправлено» — следующий мотив своего набора
        fr = tones[0][0]
        pool = MOTIFS_UP if fr == 1200 else MOTIFS_DOWN
        _motif[fr][0] = (_motif[fr][0] + 1) % len(pool)
        log(f"мотив: {pool[_motif[fr][0]][0]}")
        balloon(("♪ " if fr == 1200 else "♫ ") + pool[_motif[fr][0]][0])

    def run():
        try:
            gap = np.zeros(int(0.03 * BEEP_SR), np.float32)
            parts = []
            for fr, ms in tones:
                parts += [_soft_tone(fr, ms), gap]
            pcm = (np.clip(np.concatenate(parts), -1, 1) * 32767).astype("<i2").tobytes()
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wf:
                wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(BEEP_SR); wf.writeframes(pcm)
            winsound.PlaySound(buf.getvalue(), winsound.SND_MEMORY | winsound.SND_NODEFAULT)
        except Exception as e:   # нет устройства вывода — прежний писк лучше тишины
            log(f"мягкий сигнал не сыграл: {e!r}")
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
REPLY_WAIT_SEC = 1800     # ответ озвучивается целиком по концу моего хода — ход бывает долгим
REPLY_WAV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reply.wav")
_reply_gen = [0]           # номер последней отправленной голосом фразы: новая фраза отменяет ожидание старого ответа
_playing = [False]


def stop_reply():
    """Оборвать чтение ответа (правый Ctrl — перебить и сразу диктовать)."""
    if _playing[0]:
        _playing[0] = False
        winsound.PlaySound(None, 0)
        log("ответ голосом прерван")


def norm(t):
    return re.sub(r"\W+", " ", t).strip().lower()


def speak_reply(sent, t_sent):
    """Ждать озвученный ответ Алёны на отправленную голосом фразу и проиграть его.

    Ответ готовит voice_reply.py в аддоне на Нуксе: /local/voice_reply/voice_reply.json + WAV (голос Piper).
    Сопоставление — по началу отправленного текста в поле `for`."""
    key = norm(sent)[:40]
    _reply_gen[0] += 1
    gen = _reply_gen[0]
    deadline = time.time() + REPLY_WAIT_SEC
    while time.time() < deadline:
        if gen != _reply_gen[0]:
            return
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
            if gen != _reply_gen[0]:
                return
            with open(REPLY_WAV, "wb") as f:          # SND_ASYNC из памяти нельзя — через файл
                f.write(data)
            log(f"ответ голосом ({len(meta.get('text', ''))} симв.): {meta.get('text', '')[:120]}")
            with wave.open(io.BytesIO(data)) as w:
                dur = w.getnframes() / w.getframerate()
            _playing[0] = True
            winsound.PlaySound(REPLY_WAV, winsound.SND_FILENAME | winsound.SND_ASYNC)
            threading.Timer(dur + 0.5, lambda: _playing.__setitem__(0, False)).start()
        except Exception as e:
            log(f"не удалось проиграть ответ: {e!r}")
        return
    log("ответ голосом не дождалась (30 мин)")


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
            # 24.09: не слушать «Алёну», пока играет голосовой ответ (+0,5 с): клиент слышал ответ из динамиков и слал каждую
            # его фразу пробой в GigaAM — аддон Whisper на Нуксе ел 600 % CPU (петля эха). Прервать ответ — правый Ctrl.
            if voiced and self.wake_enabled and not self.rec and not self.busy and not _playing[0] and now - self.last_key > KEY_QUIET_SEC:
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
            # чат Claude Code: десктопный VS Code или Studio Code Server в браузере
            send = wake and any(k in title for k in ("Visual Studio Code", "Studio Code Server"))
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
                stop_reply()
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
