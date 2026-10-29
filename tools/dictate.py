# -*- coding: utf-8 -*-
"""Голос на XPS: диктовка по клавише и вызов «Алёна» без рук.

1. Диктовка: зажал ПРАВЫЙ Ctrl — говоришь, отпустил — текст печатается в активное окно. Нажатие другой
   клавиши во время удержания (обычное Ctrl+…) отменяет запись; удержание короче 0,3 с — тоже отмена.
2. «Алёна, …» или «Эй, Алёна, …»: детектор речи по громкости режет микрофон на фразы; первые 1,5 с каждой фразы уходят в GigaAM
   (на Кузнице, см. ниже). Если там «Алёна» — сигнал, фраза пишется до паузы 1 с (макс. 25 с) и распознаётся целиком,
   обращение срезается, текст печатается в активное окно; если это VS Code (чат Claude Code со мной) — с Enter,
   и тогда мой ответ проигрывается голосом — с 28.09.2026 по частям, включая промежуточные сообщения хода
   (правый Ctrl — оборвать весь ответ, короткий правый Alt — пропустить часть и перейти к следующей,
   двойной правый Alt — вернуться к предыдущей, в тишине — повторить последнюю; история — 30 частей
   через все ответы, новая часть не перебивает ту, что слушают, а ждёт своей очереди).
   16.10.2026: часть с метками предложений (marks в voice_reply.json) играет sounddevice с позиции; пока звучит озвучка,
   перехвачены две клавиши НАМПАДА (с 16.10 поменяны местами): Enter — к началу предыдущего предложения (если с начала текущего прошло > 1,5 с — к началу
   текущего), «.» нампада — к следующему предложению. В тишине клавиши свободны. Части без marks — как раньше (winsound).
   16.10.2026 (поздно): озвучка ПОТОКОМ — каждое предложение отдельная часть ленты (поля msg — номер сообщения хода, sent — номер
   предложения), звук начинается с первого предложения, остальные подгружаются по мере появления. Нампад: «.» — следующая часть
   (предложение), Enter — предыдущая, двойной Enter за 0,35 с — текущая заново; правый Alt — к первой части СЛЕДУЮЩЕГО сообщения,
   двойной правый Alt — к началу текущего сообщения (если с его начала > 1,5 с) или предыдущего; история — 300 частей.
   Части без поля msg (старый формат, в т.ч. с marks) — по-старому.
   Диктовка правым Ctrl в чат Code тоже получает ответ голосом — после ручного Enter (без Shift), до 10 мин.
   (voice_reply.py в аддоне Студии, в Штабе; голос Gemini, запасные Silero и Piper).
   16.10.2026 (хозяин): нампад — Backspace пауза/продолжить; «+»/«−» — темп ТЕКУЩЕГО предложения (шаги 0,6…1,3; предложение озвучивается
   заново Gemini TTS с подсказкой темпа, пока оно готовится ~3–5 с — тишина), «*» — темп к норме. Коррекция помнится за предложением.
   (Vosk small-ru не подошёл: в его словаре нет имени «Алёна».)

Распознавание локальное, модель GigaAM v3 e2e через onnx-asr (Wyoming, порт 10300): сначала Кузница
(192.168.77.62, видеокарта RTX 2000 Ada — служба stt-gigaam, с 02.10.2026), а если она выключена или не
отвечает — Штаб (192.168.77.2, аддон «Whisper» Home Assistant, процессор; до 04.10.2026 по этому адресу был Нукс).
Звук в интернет не уходит.
Сигналы: 1200 Гц — слушаю, 700 — отправлено, 300 — ошибка, 500+400 — «Алёна» не подтвердилась.
Выключить режим «Алёна», не трогая диктовку: создать файл no_wake рядом со скриптом (проверяется каждую секунду).
Запуск: задача планировщика «AlenaDictate» при входе rdpuser (pythonw). Лог — dictate.log рядом.
"""
import collections, ctypes, hmac, io, wave, datetime, json, os, queue, re, socket, subprocess, sys, threading, time, urllib.error, urllib.request, winsound
from ctypes import wintypes

import keyboard
import numpy as np
import sounddevice as sd

HERE = os.path.dirname(os.path.abspath(__file__))
HOST, PORT = "192.168.77.2", 10300
STT_HOSTS = ("192.168.77.62", HOST)   # Кузница (видеокарта), запасной — аддон Whisper в Штабе
STT_CONNECT_SEC = 0.7                 # выключенная Кузница не должна задерживать диктовку
_stt_skip = {}                        # адрес → время, до которого его не пробовать (после неудачи)
RATE = 16000
BLOCK = 480                      # 30 мс
HOTKEY = "right ctrl"
SKIP_KEYS = ("right alt", "alt gr")   # короткое нажатие одного правого Alt — следующая часть голосового ответа (28.09)
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
def _virtual_mic(name):
    """Виртуальный звуковой кабель (VB-Audio CABLE и подобные) и «устройство по умолчанию» Windows под своим именем
    (Sound Mapper / «Переназначение звуковых устр.», Primary Sound Capture) — не микрофон: первый отдаёт тишину,
    второе — лишь ссылка на то же устройство по умолчанию."""
    n = (name or "").upper()
    return any(x in n for x in ("CABLE", "VB-AUDIO", "VOICEMEETER", "SOUND MAPPER", "PRIMARY SOUND", "ПЕРЕНАЗНАЧЕНИЕ", "ПЕРВИЧНЫЙ"))


MIC_PREFER = "K66"               # микрофон хозяина: если он есть в списке — берём его, что бы ни стояло по умолчанию
MIC_DEAD_RMS = 3.0               # громкость не выше этой 30 с подряд — цифровая тишина, у живого микрофона так не бывает


MIC_STALL_SEC = 5.0              # колбэк микрофона молчит дольше — поток мёртв, переоткрыть
MIC_FAILS_RESTART = 3            # столько неудачных переоткрытий подряд на консоли — перезапуск всего процесса
MIN_UPTIME_RESTART = 120.0       # не перезапускаться чаще раза в 2 мин (микрофона нет вовсе — не крутиться)
GUARD_STALL_SEC = 120.0          # пульс status.json из _wake_step молчит дольше (на консоли) — поток завис, перезапуск
START_T = time.time()
STATUS = os.path.join(HERE, "status.json")   # пульс для витрины «Диктовка» (раз в 30 с)


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


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class GUITHREADINFO(ctypes.Structure):
    """GetGUIThreadInfo у потока активного окна: даёт каретку (rcCaret, координаты клиентские) и окно
    с фокусом — по ним баллон встаёт над полем ввода. У Chromium/Electron/UWP классической каретки нет,
    hwndCaret там 0 — тогда якорем служит окно с фокусом."""
    _fields_ = [("cbSize", ctypes.c_ulong), ("flags", ctypes.c_ulong),
                ("hwndActive", ctypes.c_void_p), ("hwndFocus", ctypes.c_void_p),
                ("hwndCapture", ctypes.c_void_p), ("hwndMenuOwner", ctypes.c_void_p),
                ("hwndMoveSize", ctypes.c_void_p), ("hwndCaret", ctypes.c_void_p),
                ("rcCaret", RECT)]


class Balloon:
    """26.09 («мотив, который прозвучал, показывать в виде баллона над курсором»): маленькая подсказка на ~2,5 с.
    02.10 по просьбе пользователя переехала с указателя мыши на ПОЛЕ ВВОДА — встаёт над кареткой/полем, куда ляжет
    текст. Своё окно Tk в отдельном потоке; окно НЕ активируется (WS_EX_NOACTIVATE, показ SW_SHOWNOACTIVATE),
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

            # 02.10 (просьба пользователя): баллон больше не ходит за указателем мыши — он встаёт НАД ПОЛЕМ,
            # куда ляжет текст. Якорь ищем по убыванию точности: каретка активного потока → окно с фокусом
            # (поле ввода) → само активное окно → верх-центр основного экрана.
            class MI(ctypes.Structure):
                _fields_ = [("cb", ctypes.c_ulong), ("rc", ctypes.c_long * 4), ("wk", ctypes.c_long * 4), ("fl", ctypes.c_ulong)]
            mi = MI(); mi.cb = ctypes.sizeof(MI)
            # типы объявлять обязательно: без restype ctypes режет 64-битные дескрипторы до 32 бит
            u32.GetForegroundWindow.restype = ctypes.c_void_p
            u32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
            u32.MonitorFromWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
            u32.MonitorFromWindow.restype = ctypes.c_void_p
            u32.MonitorFromPoint.restype = ctypes.c_void_p
            u32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            u32.ClientToScreen.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            u32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

            fgw = u32.GetForegroundWindow()
            anchor, how = None, "экран"
            if fgw:
                tid = u32.GetWindowThreadProcessId(fgw, None)
                gti = GUITHREADINFO(); gti.cbSize = ctypes.sizeof(GUITHREADINFO)
                if u32.GetGUIThreadInfo(tid, ctypes.byref(gti)):
                    # каретка: координаты клиентские, у hwndCaret (а если его нет — у окна с фокусом)
                    cw = gti.hwndCaret or gti.hwndFocus
                    c = gti.rcCaret
                    if cw and (c.right - c.left) >= 0 and (c.bottom - c.top) > 0:
                        p1, p2 = POINT(c.left, c.top), POINT(c.right, c.bottom)
                        if u32.ClientToScreen(cw, ctypes.byref(p1)) and u32.ClientToScreen(cw, ctypes.byref(p2)):
                            anchor, how = (p1.x, p1.y, p2.x, p2.y), "каретка"
                    if anchor is None and gti.hwndFocus:
                        r0 = (ctypes.c_long * 4)()
                        if u32.GetWindowRect(gti.hwndFocus, ctypes.byref(r0)):
                            anchor, how = tuple(r0), "поле"
                if anchor is None:
                    r0 = (ctypes.c_long * 4)()
                    if u32.GetWindowRect(fgw, ctypes.byref(r0)):
                        anchor, how = tuple(r0), "окно"

            hm = u32.MonitorFromWindow(fgw, 2) if fgw else None   # MONITOR_DEFAULTTONEAREST
            if not hm:
                hm = u32.MonitorFromPoint(POINT(0, 0), 1)         # MONITOR_DEFAULTTOPRIMARY
            u32.GetMonitorInfoW(ctypes.c_void_p(hm), ctypes.byref(mi))
            L, T, R, B = mi.wk[0], mi.wk[1], mi.wk[2], mi.wk[3]
            bw, bh = w.winfo_reqwidth(), w.winfo_reqheight()
            if anchor is None:
                x, y = L + (R - L - bw) // 2, T + 24
            else:
                ax1, ay1, ax2, ay2 = anchor
                x = (ax1 + ax2 - bw) // 2
                y = ay1 - bh - 8                                  # над полем
                if y < T:
                    y = ay2 + 8                                   # сверху нет места — под полем
            x = min(max(L, x), max(L, R - bw))
            y = min(max(T, y), max(T, B - bh))
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
            log(f"баллон: якорь {how} {anchor} → {x},{y}; окно {list(r)}" + ("; фокус уходил — возвращён" if stole else ""))

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


def _transcribe_on(host: str, pcm: bytes, connect_sec: float) -> str:
    fmt = {"rate": RATE, "width": 2, "channels": 1}
    with socket.create_connection((host, PORT), timeout=connect_sec) as s:
        s.settimeout(30)
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


def transcribe(pcm: bytes) -> str:
    """16 кГц моно int16 → Wyoming STT (Кузница, запасной — Штаб) → текст."""
    last = STT_HOSTS[-1]
    for host in STT_HOSTS:
        if host != last and time.time() < _stt_skip.get(host, 0):
            continue
        try:
            return _transcribe_on(host, pcm, 30 if host == last else STT_CONNECT_SEC)
        except Exception as e:
            if host == last:
                raise
            _stt_skip[host] = time.time() + 120     # две минуты не пробовать, потом снова
            try:
                log(f"распознавание: {host} не отвечает ({e!r}) — запасной {last}")
            except Exception:
                pass


CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

# ВАЖНО: типы объявлять обязательно. Без argtypes/restype ctypes считает, что функция возвращает int (32 бита),
# и 64-битный дескриптор буфера обмена молча обрезается — обращение по битому адресу убивает процесс целиком,
# без исключения и без строки в логе. Так 02.10 диктовка и легла после первой же вставки.
_u32 = ctypes.WinDLL("user32", use_last_error=True)
_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_u32.OpenClipboard.argtypes, _u32.OpenClipboard.restype = [wintypes.HWND], wintypes.BOOL
_u32.CloseClipboard.argtypes, _u32.CloseClipboard.restype = [], wintypes.BOOL
_u32.EmptyClipboard.argtypes, _u32.EmptyClipboard.restype = [], wintypes.BOOL
_u32.GetClipboardData.argtypes, _u32.GetClipboardData.restype = [wintypes.UINT], wintypes.HANDLE
_u32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
_u32.SetClipboardData.restype = wintypes.HANDLE
_k32.GlobalAlloc.argtypes, _k32.GlobalAlloc.restype = [wintypes.UINT, ctypes.c_size_t], wintypes.HGLOBAL
_k32.GlobalLock.argtypes, _k32.GlobalLock.restype = [wintypes.HGLOBAL], wintypes.LPVOID
_k32.GlobalUnlock.argtypes, _k32.GlobalUnlock.restype = [wintypes.HGLOBAL], wintypes.BOOL


def _буфер_открыть(попыток=10):
    """Буфер обмена — общий на всю систему, его может держать другое окно; ждём своей очереди."""
    for _ in range(попыток):
        if _u32.OpenClipboard(None):
            return True
        time.sleep(0.02)
    return False


def буфер_прочитать():
    """Текст из буфера обмена (или None, если там не текст либо буфер занят)."""
    if not _буфер_открыть():
        return None
    try:
        h = _u32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return None
        p = _k32.GlobalLock(h)
        if not p:
            return None
        try:
            return ctypes.wstring_at(p)
        finally:
            _k32.GlobalUnlock(h)
    finally:
        _u32.CloseClipboard()


def буфер_записать(text):
    if not _буфер_открыть():
        return False
    try:
        _u32.EmptyClipboard()
        данные = ctypes.create_unicode_buffer(text)
        размер = ctypes.sizeof(данные)
        h = _k32.GlobalAlloc(GMEM_MOVEABLE, размер)
        if not h:
            return False
        p = _k32.GlobalLock(h)
        if not p:
            return False
        ctypes.memmove(p, ctypes.byref(данные), размер)
        _k32.GlobalUnlock(h)
        # SetClipboardData забирает память себе — освобождать её нельзя
        return bool(_u32.SetClipboardData(CF_UNICODETEXT, h))
    finally:
        _u32.CloseClipboard()


def вставить(text):
    """Положить текст в окно ОДНИМ движением — через буфер обмена и Ctrl+V (02.10, просьба пользователя).

    Посимвольный ввод (keyboard.write) иногда терял ПРОБЕЛЫ — «Ачторежимночи…»: распознавалось верно,
    но на Windows каждый символ уходит отдельным SendInput KEYEVENTF_UNICODE, и Chromium (окно VS Code)
    на быстрой очереди часть событий не разбирал. Вставка кладёт всю фразу разом, терять нечего.
    Прежнее содержимое буфера возвращаем: пользователь мог что-то скопировать для себя.
    """
    прежнее = буфер_прочитать()
    if not буфер_записать(text):
        log("буфер обмена занят — ввожу посимвольно")
        keyboard.write(text, delay=0.005)
        return
    keyboard.send("ctrl+v")
    time.sleep(0.25)                       # дать окну забрать текст до возврата буфера
    if прежнее is not None and прежнее != text:
        буфер_записать(прежнее)


# ---- 29.10.2026: диктовка в окно Moonlight ----
# Moonlight не передаёт на хост ни Юникод-события, ни буфер обмена: Ctrl+V из вставить() уходил на Кузницу и вставлял
# ЕЁ буфер, а не фразу. Если впереди окно потока «<хост> - Moonlight», фраза уходит приёмнику на самом хосте
# (kuznitsa/diktovka/diktovka_priem.py, служба diktovka-priem у alena), он вставляет её в активное окно там.
MOONLIGHT_HOSTS = {"kuznitsa": "192.168.77.62"}   # имя хоста в заголовке окна Moonlight → адрес приёмника
MOONLIGHT_PORT = 10320
MOONLIGHT_TOKEN_FILE = os.path.join(HERE, "kuznitsa_token")


def moonlight_host(title):
    """Адрес приёмника, если впереди поток Moonlight с известным хостом, иначе None."""
    m = re.match(r"^\s*(.+?)\s+-\s+Moonlight\s*$", title or "")
    return MOONLIGHT_HOSTS.get(m.group(1).strip().lower()) if m else None


def отправить_на_хост(addr, text):
    """Передать фразу приёмнику на хосте Moonlight. (True — вставлено, заголовок окна на хосте)."""
    try:
        with open(MOONLIGHT_TOKEN_FILE, encoding="utf-8") as f:
            tok = f.read().strip()
        req = urllib.request.Request(f"http://{addr}:{MOONLIGHT_PORT}/type", method="POST",
                                     data=json.dumps({"text": text}, ensure_ascii=False).encode("utf-8"),
                                     headers={"Content-Type": "application/json", "X-Token": tok})
        with urllib.request.urlopen(req, timeout=10) as r:
            ans = json.loads(r.read().decode("utf-8"))
        log(f"Moonlight → {addr}: {ans.get('how')} в окно «{str(ans.get('window', ''))[:60]}»")
        return bool(ans.get("ok")), str(ans.get("window", ""))
    except Exception as e:
        log(f"Moonlight → {addr}: не передано: {e!r}")
        return False, ""


# ---- 29.10.2026 (вечер): клавиши диктовки с хоста Moonlight ----
# Moonlight (capturesyskeys=2) забирает клавиатуру своим хуком — правый Ctrl и прочие до keyboard.hook не доходят.
# Решение хозяина: Moonlight шлёт клавиши на Кузницу как есть, там их ловит слушатель (kuznitsa/diktovka/diktovka_priem.py)
# и сразу присылает сюда: POST http://<XPS>:10321/klavisha {"k": "rctrl", "t": "down"|"up", ...}, заголовок X-Token
# (тот же kuznitsa_token). Принимаются только из 192.168.77.0/24, tailnet и localhost, и только когда впереди окно
# Moonlight известного хоста — иначе игнор (двойных срабатываний нет). Дальше — тот же on_event, что и у своих клавиш.
KLAV_PORT = 10321
KLAV_SETI = ("192.168.77.0/24", "100.64.0.0/10", "127.0.0.0/8")
KLAV_IMENA = {"rctrl": "right ctrl", "ralt": "right alt", "rshift": "right shift", "enter": "enter", "kp_enter": "enter",
              "other": "внешняя прочая"}
KLAV_NAMPAD = {"kp_dot": "next", "kp_enter": "prev", "kp_minus": "slower", "kp_plus": "faster", "kp_star": "reset",
               "kp_0": "pause"}
_vnesh = {"obrabotchik": None, "okno": "", "ignor_t": 0.0, "rec": False}
_dedup = {}                 # (источник, имя, тип) → время: одна и та же клавиша и от своего хука, и с хоста — один раз
_dedup_lock = threading.Lock()


def klav_dubl(src, name, typ):
    """True — такое же событие только что пришло из другого источника (свой хук / хост Moonlight)."""
    if name not in ("right ctrl", "right alt", "alt gr", "right shift", "enter"):
        return False
    name = "right alt" if name == "alt gr" else name
    now = time.time()
    with _dedup_lock:
        drugoi = _dedup.get(("host" if src == "local" else "local", name, typ), 0.0)
        _dedup[(src, name, typ)] = now
        if now - drugoi < 0.5:
            _dedup[(src, name, typ)] = 0.0     # пара сработала — следующую не глушить
            return True
    return False


def klav_vneshnyaya(sob):
    """Событие клавиши с хоста Moonlight. Возвращает строку-итог для ответа и журнала."""
    k, typ = str(sob.get("k", "")), str(sob.get("t", ""))
    title = foreground_title()
    if not moonlight_host(title):
        # отпускание правого Ctrl, нажатого при окне Moonlight, всё же принимаем — иначе запись не закончится
        if not (k == "rctrl" and typ == "up" and _vnesh["rec"]):
            if k != "other" and time.time() - _vnesh["ignor_t"] > 30:
                _vnesh["ignor_t"] = time.time()
                log(f"клавиша с хоста ({k} {typ}) — впереди не Moonlight («{title[:40]}»), игнорирую")
            return "игнор: впереди не Moonlight"
    if k in KLAV_NAMPAD and typ == "down" and _np_state["remove"] is not None:
        # нампад во время озвучки — как в _np_filter (на хосте клавиша при этом всё равно дойдёт до окна)
        _nav[0] = KLAV_NAMPAD[k]
        log(f"клавиша с хоста: {k} — {KLAV_NAMPAD[k]} (озвучка)")
        return "нампад"
    name = KLAV_IMENA.get(k)
    if name is None:
        return "не моя клавиша"
    if klav_dubl("host", name, typ):
        return "дубль своего хука"
    if k in ("enter", "kp_enter"):
        _vnesh["okno"] = str(sob.get("okno", ""))
    import types
    e = types.SimpleNamespace(name=name, event_type=typ, scan_code=0, is_keypad=False,
                              shift=bool(sob.get("shift")), vnesh=True)
    f = _vnesh["obrabotchik"]
    if f is None:
        return "ещё не готово"
    f(e)
    if k == "rctrl":
        _vnesh["rec"] = typ == "down"
        log(f"клавиша с хоста: правый Ctrl {'нажат — запись' if typ == 'down' else 'отпущен'}")
    return "принято"


def klav_server():
    import ipaddress
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    seti = [ipaddress.ip_network(n) for n in KLAV_SETI]

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"          # keep-alive: слушатель держит одно соединение, без лишних рукопожатий

        def setup(self):
            super().setup()
            # без Nagle: ответ уходит двумя кусками (заголовки, тело), и второй ждал подтверждения ~40 мс — следующая
            # клавиша в очереди Кузницы опаздывала на столько же (замер 29.10: 50 мс вместо 4)
            try:
                self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass

        def _otvet(self, code, obj):
            b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def do_POST(self):
            n = int(self.headers.get("Content-Length", "0") or 0)
            body = self.rfile.read(min(n, 10000)) if n else b""
            try:
                ip_ok = any(ipaddress.ip_address(self.client_address[0]) in s for s in seti)
            except ValueError:
                ip_ok = False
            if self.path != "/klavisha" or not ip_ok:
                return self._otvet(404, {"ok": False})
            try:
                with open(MOONLIGHT_TOKEN_FILE, encoding="utf-8") as f:
                    tok = f.read().strip()
            except Exception:
                tok = ""
            if not tok or not hmac.compare_digest(self.headers.get("X-Token", ""), tok):
                log(f"клавиша с хоста: чужой токен с {self.client_address[0]}")
                return self._otvet(403, {"ok": False})
            t0 = time.time()
            try:
                itog = klav_vneshnyaya(json.loads(body.decode("utf-8")))
            except Exception as e:
                log(f"клавиша с хоста: ошибка {e!r}")
                return self._otvet(500, {"ok": False})
            self._otvet(200, {"ok": True, "itog": itog, "ms": round((time.time() - t0) * 1000, 1)})

        def log_message(self, *a):
            pass

    while True:
        try:
            srv = ThreadingHTTPServer(("0.0.0.0", KLAV_PORT), H)
            srv.daemon_threads = True
            log(f"клавиши с хоста Moonlight: слушаю :{KLAV_PORT}")
            srv.serve_forever()
        except Exception as e:
            log(f"клавиши с хоста: сервер упал {e!r} — снова через 10 с")
        time.sleep(10)


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
_skip = [False]            # правый Alt: оборвать текущую часть ответа и перейти к следующей
_back = [False]            # двойной правый Alt: вернуться к предыдущей части (03.10)
_hist = []                 # последние сыгранные части через ВСЕ ответы подряд: (подпись, WAV-байты) — по ним ходит «назад»
HIST_MAX = 300             # сколько частей помнить (WAV лежат в памяти: ~1,5 МБ на полминуты речи)
_skipmsg = [False]         # правый Alt (а не «.» нампада): пропуск идёт к следующему СООБЩЕНИЮ, а не к следующей части (16.10)
_cur_msg = [None, 0.0]     # (ключ сообщения (ход, msg), когда оно начало звучать) — для двойного Alt «к началу или к предыдущему»
_skip_key = [None]         # сообщение, к чьему концу прыгнули Alt-ом, пока его части ещё публикуются: их не играем
MSG_BACK_SEC = 1.5         # двойной правый Alt: если с начала текущего сообщения прошло больше — к его началу, иначе к началу предыдущего
_live_gen = [0]            # номер хода, чей цикл проигрывания сейчас жив (0 — никто не играет и не ждёт частей)


# ---- 16.10.2026: проигрывание с позиции и переход по предложениям (нампад) ----
SENT_BACK_SEC = 1.5         # «назад»: если с начала текущего предложения прошло больше — к началу текущего, иначе к предыдущему
OUT_DEVICE_FILE = os.path.join(HERE, "out_device")   # необязательно: номер или кусок имени устройства вывода (если звук идёт не туда)
_paused = [False]           # 16.10.2026: пауза озвучки (Backspace), общая для всех частей
_nav = [None]               # «prev» / «next» от нампада — забирает цикл проигрывания части
_np_state = {"remove": None, "held": set()}
_np_lock = threading.Lock()
_out_dev_logged = [False]


def nav_target(marks, t, key):
    """Куда прыгнуть по клавише нампада. marks — начала предложений (с), t — текущая позиция (с).
    Возвращает ('seek', секунды) | ('prevpart', None) — назад за начало части | ('nextpart', None) — вперёд за конец части."""
    i = 0
    for k, m in enumerate(marks):
        if m <= t + 0.001:
            i = k
    if key == "again":                 # 16.10 хозяин: двойной Enter нампада — текущее предложение заново
        return ("seek", marks[i])
    if key == "prev":                  # одиночный Enter — сразу к предыдущему предложению
        if t - marks[i] > SENT_BACK_SEC and False:   # прежнее «к началу текущего» отдано двойному нажатию
            return ("seek", marks[i])
        if i > 0:
            return ("seek", marks[i - 1])
        return ("prevpart", None)
    if i + 1 < len(marks):
        return ("seek", marks[i + 1])
    return ("nextpart", None)


def _np_filter(e):
    """Блокирующий хук keyboard (стоит ТОЛЬКО пока звучит озвучка): глотаются и превращаются в команды
      «.» нампада (скан-код 83 + is_keypad; и при выключенном NumLock) — к следующему предложению, Enter нампада (28 + keypad) — к предыдущему,
      Backspace (скан-код 14, 16.10.2026 хозяин) — пауза/продолжить, «+» нампада (78 + keypad) — темп текущего предложения быстрее,
      «−» нампада (74 + keypad) — медленнее, «*» нампада (55 + keypad) — темп к норме.
    ВАЖНО: Backspace нампада и обычный Backspace — один и тот же скан-код 14 (keyboard их не различает), поэтому на время озвучки
    перехватывается ЛЮБОЙ Backspace, пока звучит озвучка или стоит пауза (хук на паузе остаётся) — стирать текст клавишей Backspace в это
    время нельзя: сначала правый Ctrl/Alt (стоп) или дослушать. Какие коды шлёт именно нампадный Backspace — видно по строкам «клавиша: …» в журнале.
    Всё остальное — True (клавиша идёт дальше как обычно). Должен работать мгновенно: вызывается на каждое нажатие."""
    try:
        sc, kp = e.scan_code, bool(getattr(e, "is_keypad", False))
        # 16.10.2026 хозяин: пауза — «0» нампада (скан-код 82 + keypad), не Backspace: тот шлёт один код с обычным и мешал бы стирать
        if kp:
            k = {83: "next", 28: "prev", 74: "slower", 78: "faster", 55: "reset", 82: "pause"}.get(sc)
        else:
            k = None
        held = _np_state["held"]
        # 16.10.2026: журнал нажатий командных клавиш во время озвучки — проверить вживую, шлёт ли нампадный Backspace что-то иное
        # (только эти скан-коды: остальной набор текста в журнал не попадает)
        if e.event_type == "down" and sc in (14, 28, 55, 74, 78, 83) and (k is None or k not in held):
            log(f"клавиша: {getattr(e, 'name', None)}/{sc}/{kp}" + (f" — перехвачена ({k})" if k else " — пропущена"))
        if k is None:
            return True
        if e.event_type == "down":
            if k not in held:                 # автоповтор удержания — одна команда
                held.add(k)
                _nav[0] = k
            return False
        if k in held:                         # отпускание той клавиши, нажатие которой мы проглотили
            held.discard(k)
            return False
        return True                           # нажали до включения перехвата — отпускание пропускаем
    except Exception:
        return True


_np_timer = [None]


def _np_off_soon(delay=0.5):
    """Снять перехват нампада не сразу, а через delay с, если следующая часть не успела его снова взять (между предложениями
    озвучки — доли секунды, хук не переустанавливаем; в настоящей тишине клавиши отпускаются)."""
    def fire(me):
        with _np_lock:
            if _np_timer[0] is not me:
                return
            _np_timer[0] = None
        _np_off()
    with _np_lock:
        t = threading.Timer(delay, lambda: fire(t))
        t.daemon = True
        _np_timer[0] = t
        t.start()


def _np_on():
    with _np_lock:
        _np_timer[0] = None
        if _np_state["remove"] is None:
            try:
                _np_state["held"].clear()
                _np_state["remove"] = keyboard.hook(_np_filter, suppress=True)
            except Exception as e:
                log(f"перехват нампада не включился: {e!r}")


def _np_off():
    with _np_lock:
        rm, _np_state["remove"] = _np_state["remove"], None
        _nav[0] = None
        if rm is not None:
            try:
                rm()
            except Exception as e:
                log(f"перехват нампада не снялся: {e!r}")


def _out_device():
    """Устройство вывода для sounddevice. winsound играет в «Переназначение звуковых устройств» MME (то, что выбрано в Windows);
    чтобы звук шёл ровно туда же (26.09 sounddevice по умолчанию попадал не в то устройство — на SHARP HDMI было не слышно),
    берём MME «Microsoft Sound Mapper - Output». Переопределить — файл out_device рядом со скриптом."""
    try:
        want = open(OUT_DEVICE_FILE, encoding="utf-8").read().strip()
    except Exception:
        want = ""
    devs = sd.query_devices()
    if want:
        if want.isdigit():
            return int(want)
        for i, d in enumerate(devs):
            if d["max_output_channels"] > 0 and want.lower() in d["name"].lower():
                return i
    try:
        for h in sd.query_hostapis():
            if h["name"] == "MME":
                for i in h["devices"]:
                    if devs[i]["max_output_channels"] > 0 and devs[i]["name"].startswith("Microsoft Sound Mapper"):
                        return i
    except Exception:
        pass
    return None


class _SdPlayer:
    """WAV в памяти → OutputStream с колбэком, позицию можно менять на лету (seek)."""

    def __init__(self, data):
        with wave.open(io.BytesIO(data)) as w:
            self.rate, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
            raw = w.readframes(w.getnframes())
        if width != 2:
            raise ValueError(f"WAV не 16 бит ({width * 8})")
        self.a = np.frombuffer(raw, dtype="<i2").reshape(-1, ch)
        self.ch, self.n, self.pos = ch, len(self.a), 0
        self.lock = threading.Lock()
        self.stream = None
        self.end_t = None
        self.stall = (0, time.time())
        self.lat = 0.2
        self.paused = _paused[0]       # 16.10: пауза (Backspace) держится и на следующих частях

    def _cb(self, outdata, frames, tinfo, status):
        if self.paused:                # пауза: тишина, позиция стоит
            outdata[:] = 0
            return
        with self.lock:
            p = self.pos
            e = min(p + frames, self.n)
            k = e - p
            if k > 0:
                outdata[:k] = self.a[p:e]
            if k < frames:
                outdata[max(k, 0):] = 0
            self.pos = max(e, p)

    def start(self):
        dev = _out_device()
        self.stream = sd.OutputStream(samplerate=self.rate, channels=self.ch, dtype="int16", device=dev, callback=self._cb)
        if not _out_dev_logged[0]:
            _out_dev_logged[0] = True
            try:
                d = sd.query_devices(self.stream.device)
                log(f"озвучка с позиции: устройство {self.stream.device} «{d['name']}», {self.rate} Гц, задержка {self.stream.latency:.2f} с")
            except Exception:
                pass
        self.lat = float(self.stream.latency or 0.2)
        self.stream.start()

    def seek(self, sec):
        with self.lock:
            self.pos = max(0, min(self.n, int(sec * self.rate)))
            self.end_t = None

    def now(self):
        """Позиция в секундах (по очереди колбэка; она на задержку устройства впереди слышимого — для прыжков не важно)."""
        return self.pos / self.rate

    def finished(self):
        try:
            if not self.stream.active:         # поток убит (sd._terminate при переоткрытии микрофона)
                return True
        except Exception:
            return True
        if self.paused:                        # 16.10: на паузе позиция стоит — это не «колбэк замолчал» (раньше через 3 с часть
            self.stall = (self.pos, time.time())   # считалась законченной и пауза «перелистывала» предложения)
            return False
        if self.pos >= self.n:
            if self.end_t is None:
                self.end_t = time.time()
            return time.time() - self.end_t > self.lat + 0.1
        if self.pos != self.stall[0]:
            self.stall = (self.pos, time.time())
        elif time.time() - self.stall[1] > 3:  # колбэк замолчал
            return True
        return False

    def close(self):
        try:
            if self.stream is not None:
                self.stream.abort()
                self.stream.close()
        except Exception:
            pass


def _msg_start(pos):
    """Индекс первой части того же сообщения, что и _hist[pos] (ключ None — сама часть)."""
    k = _hist[pos][3] if len(_hist[pos]) > 3 else None
    if k is None:
        return pos
    while pos > 0 and len(_hist[pos - 1]) > 3 and _hist[pos - 1][3] == k:
        pos -= 1
    return pos


def _enter_msg(j):
    """Перешли к началу сообщения j: «с начала сообщения» отсчитывается заново."""
    if 0 <= j < len(_hist) and len(_hist[j]) > 3 and _hist[j][3] is not None:
        _cur_msg[:] = [_hist[j][3], time.time()]
    return j


def _apply_skip(pos):
    """После прыжка к следующему сообщению, пока его предшественник ещё доезжает в ленту, оставшиеся его части пропускаем."""
    k = _skip_key[0]
    while k is not None and pos < len(_hist) and len(_hist[pos]) > 3 and _hist[pos][3] == k:
        pos += 1
    if pos < len(_hist) and (len(_hist[pos]) <= 3 or _hist[pos][3] != k):
        _skip_key[0] = None
    return pos


def _step(res, pos):
    """Куда идти по результату _play_part: (новая позиция в _hist, с какого места играть: 0 или 'last').
    16.10: для частей нового формата (есть ключ сообщения) — 'nextmsg' (правый Alt) и 'back' (двойной правый Alt) ходят по
    сообщениям; 'skip'/'prev' (нампад) — по частям (предложениям); 'last' для них не нужен (часть — одно предложение)."""
    key = _hist[pos][3] if 0 <= pos < len(_hist) and len(_hist[pos]) > 3 else None
    if key is not None and res == "nextmsg":
        j = pos + 1
        while j < len(_hist) and len(_hist[j]) > 3 and _hist[j][3] == key:
            j += 1
        _skip_key[0] = key if j >= len(_hist) else None
        return _enter_msg(j), 0
    if key is not None and res == "back":
        first = _msg_start(pos)
        elapsed = time.time() - _cur_msg[1] if _cur_msg[0] == key else 999
        if elapsed > MSG_BACK_SEC or first == 0:
            return _enter_msg(first), 0                 # к началу текущего сообщения
        return _enter_msg(_msg_start(first - 1)), 0     # к началу предыдущего
    if res == "nextmsg":
        res = "skip"
    if res == "back":
        return max(0, pos - 1), 0
    if res == "prev":                          # «.» в самом начале части — к последнему предложению предыдущей
        return (pos - 1, "last") if pos > 0 else (0, 0)
    return pos + 1, 0


def _tail_pos(новых=0):
    """С какой части играть, когда ничего не звучит (двойной Alt в тишине / повтор): новое непрозвучавшее; иначе — для частей
    нового формата начало последнего сообщения, для старых — последняя часть."""
    pos = len(_hist) - max(1, новых)
    if not новых and len(_hist[pos]) > 3 and _hist[pos][3] is not None:
        return _enter_msg(_msg_start(pos))
    return pos


def stop_reply():
    """Оборвать чтение ответа (правый Ctrl — перебить и сразу диктовать): и текущую часть, и остальные части хода."""
    if _playing[0]:
        _playing[0] = False
        _reply_gen[0] += 1             # ожидание следующих частей этого хода тоже прекращается
        _paused[0] = False
        winsound.PlaySound(None, 0)
        log("ответ голосом прерван")


def skip_part():
    """Правый Alt: оборвать текущую часть ответа, следующие части хода играют дальше."""
    if _playing[0]:
        _skipmsg[0] = True             # части нового формата: к первой части следующего сообщения; старые — к следующей части
        _skip[0] = True
        winsound.PlaySound(None, 0)
        log("правый Alt — к следующему сообщению (старый формат — к следующей части)")


def prev_part():
    """Двойной правый Alt: оборвать текущую часть и сыграть предыдущую; в тишине — повторить последнюю сыгранную."""
    if _live_gen[0]:                   # цикл ответа жив (играет или ждёт следующую часть) — он сам шагнёт назад
        _back[0] = True
        if _playing[0]:
            winsound.PlaySound(None, 0)
        log("назад — к предыдущей части ответа")
    else:                              # ответ уже дочитан или оборван — сыграть непрозвучавшее или повторить последнюю часть
        threading.Thread(target=replay_tail, daemon=True).start()


def _play_part(item, gen, start=0):
    """Сыграть одну часть ответа. Часть с marks — sounddevice с позиции (старт с начала или с последнего предложения),
    под перехватом «.»/Enter нампада; без marks (или если устройство вывода не открылось) — как раньше, winsound.
    Возвращает 'done', 'skip', 'back', 'prev' (нампад: назад за начало части) или 'stop'."""
    if len(item) > 3 and item[3] is not None:      # 16.10: часть нового формата — одно предложение, без marks
        return _play_part_msg(item, gen)
    marks = item[2] if len(item) > 2 else None
    if marks and all(isinstance(m, (int, float)) for m in marks):
        try:
            player = _SdPlayer(item[1])
            player.start()
        except Exception as e:
            log(f"sounddevice не сыграл ({e!r}) — играю как раньше, без перехода по предложениям")
            player = None
        if player is not None:
            return _play_part_sd(player, sorted(marks), gen, start)
    return _play_part_ws(item, gen)


# ---- 16.10.2026: темп отдельного предложения (нампад «+»/«−»/«*») ----
SPEED_STEPS = (0.6, 0.75, 0.9, 1.0, 1.15, 1.3)
SPEED_HOOK = "alena_golos_skorost"   # вебхук HA → input_text.golos_skorost «id|темп|nonce» → поток speed_loop в voice_reply.py (Студия) делает вариант
VAR_WAIT_SEC = 45           # сколько ждать готовый вариант (обычно 3–5 с)
VAR_CACHE_MAX = 40
_speed = {}                 # id части → запомненный темп (нет записи — норма 1,0); живёт, пока работает программа
_variants = {}              # (id части, темп) → WAV-байты варианта (кэш; на Студии варианты лежат файлами <id>_x<темп>.wav)
_var_busy, _var_fail = set(), set()
_var_lock = threading.Lock()


def speed_step(cur, key):
    """Следующий темп из SPEED_STEPS: 'faster' — на шаг быстрее, 'slower' — медленнее, 'reset' — 1,0. На краю остаётся тот же."""
    if key == "reset":
        return 1.0
    i = min(range(len(SPEED_STEPS)), key=lambda j: abs(SPEED_STEPS[j] - cur))
    i = min(len(SPEED_STEPS) - 1, i + 1) if key == "faster" else max(0, i - 1)
    return SPEED_STEPS[i]


def _fetch_variant(pid, k):
    """Поток: взять вариант части pid с темпом k — готовый файл на Студии, а если его нет, попросить вебхуком и ждать появления.
    Результат — в _variants[(pid, k)], при неудаче — (pid, k) в _var_fail."""
    key = (pid, k)
    url = REPLY_URL + f"{pid}_x{k:.2f}.wav"
    nonce = str(int(time.time() * 1000) % 10 ** 9)      # отличает этот запрос от прежних (input_text не меняется при том же значении)

    def get_err():
        """Студия не смогла сделать вариант: рядом лежит .err «nonce|причина» (только если nonce наш)."""
        try:
            with urllib.request.urlopen(url[:-4] + ".err?_=%d" % time.time(), timeout=5) as r:
                t = r.read().decode("utf-8", "replace")
            return t if t.split("|", 1)[0] == nonce else None
        except Exception:
            return None

    def get():
        try:
            with urllib.request.urlopen(url + "?_=%d" % time.time(), timeout=8) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise
    try:
        data = get()
        if data is None:
            req = urllib.request.Request(f"http://{HOST}:8123/api/webhook/{SPEED_HOOK}", method="POST",
                                         data=json.dumps({"id": pid, "k": k, "n": nonce}).encode("utf-8"),
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=5).read()
            t0 = time.time()
            while data is None and time.time() - t0 < VAR_WAIT_SEC:
                time.sleep(0.4)
                data = get()
                if data is None:
                    err = get_err()
                    if err:
                        raise RuntimeError("Студия: " + err.split("|", 1)[-1][:120])
        if data is None:
            raise TimeoutError(f"вариант не появился за {VAR_WAIT_SEC} с")
        with wave.open(io.BytesIO(data)) as w:        # проверка, что это настоящий WAV 16 бит
            if w.getsampwidth() != 2:
                raise ValueError("вариант не 16 бит")
        _variants[key] = data
        while len(_variants) > VAR_CACHE_MAX:
            _variants.pop(next(iter(_variants)))
        log(f"вариант темпа {k} для {pid} получен ({len(data) // 1024} КБ)")
    except Exception as e:
        _var_fail.add(key)
        log(f"вариант темпа {k} для {pid} не получен: {e!r}")
    finally:
        with _var_lock:
            _var_busy.discard(key)


def _fetch_start(pid, k):
    key = (pid, k)
    with _var_lock:
        if key in _var_busy:
            return
        _var_busy.add(key)
        _var_fail.discard(key)
    threading.Thread(target=_fetch_variant, args=(pid, k), daemon=True).start()


def _play_part_msg(item, gen):
    """Часть-предложение нового формата: sounddevice, нампад под перехватом. «.» — к следующей части, Enter — к предыдущей,
    двойной Enter за 0,35 с — эта часть заново; Backspace — пауза; «+»/«−»/«*» — темп ЭТОГО предложения (вариант Gemini с другим
    темпом, пока он готовится — тишина, потом играет с начала; темп помнится за предложением). После конца части пауза не
    добавляется (тишина уже в самом WAV), _playing остаётся поднятым до простоя цикла — правый Ctrl/Alt работают и в щели между
    предложениями. Возвращает 'done', 'skip', 'prev', 'nextmsg' (правый Alt), 'back' (двойной правый Alt) или 'stop'."""
    if item[3] != _cur_msg[0]:
        _cur_msg[:] = [item[3], time.time()]
    pid = item[5] if len(item) > 5 else None
    want = _speed.get(pid, 1.0) if pid else 1.0         # запомненный темп этого предложения
    playing = 1.0                                       # какой темп сейчас в плеере
    if want != 1.0 and (pid, want) in _variants:
        data, playing = _variants[(pid, want)], want
    else:
        data = item[1]
    try:
        player = _SdPlayer(data)
        player.start()
    except Exception as e:
        log(f"sounddevice не сыграл ({e!r}) — играю как раньше, без нампада")
        return _play_part_ws(item, gen)
    waiting = False                                     # ждём вариант: оригинал молчит на паузе
    if want != playing:
        waiting = True
        player.paused = True
        _fetch_start(pid, want)
        log(f"предложение {pid}: темп {want} — готовлю вариант")
    _playing[0] = True
    _skip[0] = _back[0] = _skipmsg[0] = False
    _nav[0] = None
    _np_on()
    res = "done"

    def swap(new_data, new_k):
        """Переключить плеер на другой WAV (вариант или оригинал) — с начала."""
        nonlocal player, playing, waiting
        p2 = _SdPlayer(new_data)               # p2.paused берётся из общей паузы
        p2.start()
        old, player = player, p2
        old.close()
        playing, waiting = new_k, False

    try:
        while not player.finished():
            if gen != _reply_gen[0]:
                res = "stop"
                break
            if _back[0]:
                _back[0] = False
                res = "back"
                break
            if _skip[0]:
                res = "nextmsg" if _skipmsg[0] else "skip"
                _skip[0] = _skipmsg[0] = False
                break
            if waiting:                          # вариант готов / не получился / запрос отменён
                want = _speed.get(pid, 1.0)
                try:
                    if want == playing:
                        waiting = False
                        player.paused = _paused[0]
                    elif want == 1.0:
                        swap(item[1], 1.0)
                    elif (pid, want) in _variants:
                        swap(_variants[(pid, want)], want)
                        log(f"предложение {pid}: играю вариант темпа {want}")
                    elif (pid, want) in _var_fail:
                        log(f"предложение {pid}: вариант темпа {want} не вышел — остаюсь на {playing}")
                        _speed.pop(pid, None) if playing == 1.0 else _speed.__setitem__(pid, playing)
                        waiting = False
                        player.paused = _paused[0]
                except Exception as e:
                    log(f"смена темпа: плеер не открылся ({e!r})")
                    waiting = False
                    player.paused = _paused[0]
            key, _nav[0] = _nav[0], None
            if key == "pause":
                _paused[0] = not _paused[0]
                player.paused = _paused[0] or waiting
                log("нампад: «0» — " + ("пауза" if _paused[0] else "продолжаю"))
                continue
            if key in ("faster", "slower", "reset"):
                if not pid:
                    log("нампад: темп недоступен — у части нет id")
                    continue
                cur = _speed.get(pid, 1.0)
                new = speed_step(cur, key)
                if new == cur:
                    log(f"нампад: темп {cur} — {'уже норма' if key == 'reset' else 'край шкалы'}")
                    continue
                if new == 1.0:
                    _speed.pop(pid, None)
                else:
                    _speed[pid] = new
                log(f"нампад: {dict(faster='«+»', slower='«−»', reset='«*»')[key]} — темп предложения {pid}: {cur} → {new}")
                try:
                    if new == playing:
                        waiting = False
                        player.paused = _paused[0]
                    elif new == 1.0:                  # сброс — исходный WAV, запроса не нужно
                        swap(item[1], 1.0)
                    elif (pid, new) in _variants:     # уже делали — кэш
                        swap(_variants[(pid, new)], new)
                    else:                             # новая генерация; пока — тишина
                        waiting = True
                        player.paused = True
                        _fetch_start(pid, new)
                except Exception as e:
                    log(f"смена темпа: плеер не открылся ({e!r})")
                continue
            if key in ("prev", "next") and _paused[0]:   # переход снимает паузу
                _paused[0] = False
                player.paused = waiting
            if key == "prev":                  # ждём второе нажатие Enter до 0,35 с — двойное = эта часть заново
                t_end = time.time() + 0.35
                while time.time() < t_end and _nav[0] != "prev":
                    time.sleep(0.02)
                if _nav[0] == "prev":
                    _nav[0] = None
                    player.seek(0)
                    log("нампад: двойной Enter — часть заново")
                else:
                    log("нампад: Enter — к предыдущей части")
                    res = "prev"
                    break
            elif key == "next":
                log("нампад: «.» — к следующей части")
                res = "skip"
                break
            time.sleep(0.03)
    finally:
        player.close()
        if res == "stop":
            _np_off()
        else:
            _np_off_soon()
    return res


def _play_part_sd(player, marks, gen, start):
    _playing[0] = True
    _skip[0] = _back[0] = _skipmsg[0] = False
    _nav[0] = None
    if start == "last":
        player.seek(marks[-1])
    _np_on()
    res = "done"
    try:
        while not player.finished():
            if gen != _reply_gen[0]:
                return "stop"
            if _back[0]:
                _back[0] = False
                res = "back"
                break
            if _skip[0]:
                _skip[0] = False
                res = "skip"
                break
            key, _nav[0] = _nav[0], None
            if key == "prev":                  # 16.10: ждём второе нажатие Enter до 0,35 с — двойное = «заново»
                t_end = time.time() + 0.35
                while time.time() < t_end and _nav[0] != "prev":
                    time.sleep(0.02)
                if _nav[0] == "prev":
                    _nav[0] = None
                    key = "again"
            if key:
                kind, sec = nav_target(marks, player.now(), key)
                if kind == "seek":
                    player.seek(sec)
                    log(f"нампад: {dict(prev='назад', next='вперёд', again='заново').get(key, key)} — к {sec:.1f} с")
                elif kind == "prevpart":
                    log("нампад: назад — к предыдущей части")
                    res = "prev"
                    break
                else:
                    log("нампад: вперёд — к следующей части")
                    res = "skip"
                    break
            time.sleep(0.03)
    finally:
        player.close()
        _np_off()
    if res == "done":
        time.sleep(0.5)
    _playing[0] = False
    return res


def _play_part_ws(item, gen):
    """Сыграть одну часть ответа. Возвращает 'done' (дочитана), 'skip' (правый Alt), 'back' (двойной правый Alt)
    или 'stop' (ответ оборван правым Ctrl либо начат новый)."""
    label, data = item[0], item[1]
    with open(REPLY_WAV, "wb") as f:          # SND_ASYNC из памяти нельзя — через файл
        f.write(data)
    with wave.open(io.BytesIO(data)) as w:
        dur = w.getnframes() / w.getframerate()
    _playing[0] = True
    _skip[0] = _back[0] = _skipmsg[0] = False
    winsound.PlaySound(REPLY_WAV, winsound.SND_FILENAME | winsound.SND_ASYNC)
    end = time.time() + dur
    res = "done"
    while time.time() < end:                    # ждём конца части; правый Ctrl обрывает (gen меняется)
        if gen != _reply_gen[0]:
            return "stop"
        if _back[0]:
            _back[0] = False
            res = "back"
            break
        if _skip[0]:
            _skip[0] = False
            res = "skip"
            break
        time.sleep(0.1)
    if res == "done":
        time.sleep(0.5)
    _playing[0] = False
    return res


def replay_tail():
    """Повтор с последней сыгранной части, когда ничего не играет; Alt и двойной Alt ходят по истории так же."""
    _reply_gen[0] += 1
    gen = _reply_gen[0]
    _live_gen[0] = gen
    try:
        новых = добрать_свежее()
        if not _hist:
            log("назад — повторять нечего")
            return
        log(f"назад — непрозвучавший ответ, частей: {новых}" if новых else "назад — повтор последней части ответа")
        _skip_key[0] = None
        pos = _tail_pos(новых)
        start = 0
        while 0 <= pos < len(_hist):
            pos = _apply_skip(pos)
            if pos >= len(_hist):
                break
            res = _play_part(_hist[pos], gen, start)
            if res == "stop":
                return
            pos, start = _step(res, pos)
    except Exception as e:
        _playing[0] = False
        log(f"не удалось повторить часть ответа: {e!r}")
    finally:
        if _live_gen[0] == gen:
            _live_gen[0] = 0
            _playing[0] = False         # части нового формата держат _playing поднятым между предложениями — в простое опускаем


CODE_TITLES = ("Visual Studio Code", "Studio Code Server")
ARM_SEC = 600              # сколько ждать ручного Enter после диктовки по Ctrl в чат Code
_armed = {"text": "", "t": 0.0, "все": []}   # фразы, продиктованные по Ctrl в ещё не отправленное сообщение; t — время последней
_enter_рано = [0.0]         # когда нажали Enter, пока диктовка ещё вставляла текст (12.10.2026)


def is_code_window(title):
    return any(k in title for k in CODE_TITLES)


def on_enter():
    """Ручной Enter в чате Code после диктовки по Ctrl — ждать и озвучить ответ, как после «Алёна, …»."""
    text, t, все = _armed["text"], _armed["t"], list(_armed["все"])
    _armed.update(text="", t=0.0, все=[])
    if not text:
        return
    # 12.10.2026: раньше Enter отбрасывался молча — по журналу было не понять, почему ответ не прозвучал
    if time.time() - t > ARM_SEC:
        log(f"Enter после диктовки — не жду ответ: с последней фразы прошло {time.time() - t:.0f} с (предел {ARM_SEC})")
        return
    title = foreground_title()
    if moonlight_host(title) and _vnesh["okno"]:
        title = _vnesh["okno"]          # 29.10.2026: Enter пришёл с хоста Moonlight — решает окно там
    if not is_code_window(title):
        log(f"Enter после диктовки — не жду ответ: впереди окно «{title[:60]}», не чат")
        return
    log(f"Enter после диктовки — жду ответ голосом на «{text[:60]}» (фраз в сообщении: {len(все)})")
    threading.Thread(target=speak_reply, args=(text, time.time(), все), daemon=True).start()


def norm(t):
    return re.sub(r"\W+", " ", t).strip().lower()


def mark_voice(sent):
    """Метка «эту фразу отправили голосом» для voice_reply.py (29.09.2026): вебхук HA alena_golos_zapros пишет её в
    input_text.golos_zapros, и ответ на неё озвучивается Gemini с интонацией; без метки — Piper."""
    try:
        req = urllib.request.Request(f"http://{HOST}:8123/api/webhook/alena_golos_zapros", method="POST",
                                     data=json.dumps({"text": sent[:250]}).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5).read()
    except Exception as e:
        log(f"метка голоса не отправлена: {e!r}")


_ожидают = []            # (ключ, время отправки) сообщений, на которые ответ ещё не дочитан до финала
_взяты = set()           # id частей ответа, уже положенных в историю, — чтобы новый ожидающий не сыграл их повторно
_взята_ts = [0.0]        # время самой свежей взятой части


def _item(p, data, meta):
    """Запись истории: (seq, WAV, marks, ключ сообщения (ход, msg) или None для старого формата, номер предложения, id части)."""
    key = (meta.get("turn"), p["msg"]) if p.get("msg") is not None else None
    return (p.get("seq", 1), data, p.get("marks"), key, p.get("sent"), p.get("id"))


def добрать_свежее():
    """Положить в историю части ленты, которые ещё не звучали. Возвращает, сколько добавлено.

    12.10.2026: ответ, написанный без сообщения хозяина (вернулась помощница, сработало расписание), никто не ждёт —
    он озвучен домом, но не проигран и в историю не попадал; двойной Alt повторял предыдущий ответ вместо последнего."""
    try:
        with urllib.request.urlopen(REPLY_URL + "voice_reply.json?_=%d" % time.time(), timeout=5) as r:
            meta = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        log(f"лента ответа не прочитана: {e!r}")
        return 0
    n = 0
    for p in meta.get("parts") or [dict(meta, final=True)]:
        if p.get("id") in _взяты or p.get("ts", 0) <= _взята_ts[0]:
            continue
        try:
            with urllib.request.urlopen(REPLY_URL + p["wav"], timeout=10) as r:
                data = r.read()
        except Exception as e:
            log(f"не удалось получить часть ответа: {e!r}")
            continue
        _взяты.add(p.get("id")); _взята_ts[0] = p.get("ts", 0)
        _hist.append(_item(p, data, meta))
        del _hist[:-HIST_MAX]
        n += 1
    return n


def speak_reply(sent, t_sent, фразы=None):
    """Ждать озвученный ответ Алёны на отправленную голосом фразу и проигрывать его по частям.

    Ответ готовит voice_reply.py в аддоне Студии (Штаб): /local/voice_reply/voice_reply.json — лента хода
    {turn, for, parts: [{id, seq, text, wav, ts, final}]} + WAV каждой части (голос Gemini, запасной Piper). С 28.09.2026 озвучиваются и
    промежуточные сообщения хода: части играются по порядку, одна за другой, до части с final. Сопоставление —
    по началу отправленного текста в поле `for`."""
    mark_voice(sent)
    # 12.10.2026: сообщение диктуется несколькими фразами, которые хозяин потом переставляет и правит («Ой.» стёр, начало
    # заменил) — ключ по одной первой фразе с итоговым текстом не совпадал, и ответ молчал. Теперь годится любая из фраз.
    keys = [k for k in {norm(x)[:40] for x in ([sent] + list(фразы or []))} if len(k) >= 8] or [norm(sent)[:40]]
    # 12.10.2026: сообщение, отправленное, пока я ещё работаю над предыдущим, попадает в тот же ход — дом озвучивает ответ
    # как ответ на ПЕРВОЕ сообщение хода. Новое ожидание отменяло прежнее и ждало только свой ключ: ответ на «Проверь линии…»
    # не прозвучал, потому что следом были отправлены «Напомню…» и «Напомни.». Теперь ключи копятся, пока ответ не дочитан
    # до финала: годится ответ на любое из неотвеченных сообщений.
    _ожидают[:] = [(k, t) for k, t in _ожидают if time.time() - t < REPLY_WAIT_SEC] + [(k, t_sent) for k in keys]
    keys = [k for k, _ in _ожидают]
    t_sent = min(t for _, t in _ожидают)
    _reply_gen[0] += 1
    gen = _reply_gen[0]
    _live_gen[0] = gen
    seen = set()
    # какая часть из _hist играет следующей; pos == len(_hist) — ждём новую. История общая для всех ответов (03.10):
    # «назад» уходит и в прошлые ответы, а новые части текущего не перебивают — ждут в очереди, пока до них дойдёт очередь.
    pos = len(_hist)
    start = 0
    _skip_key[0] = None
    poll_now = False            # только что доиграли часть — следующую (она обычно уже в ленте) берём без ожидания
    final = False
    deadline = time.time() + REPLY_WAIT_SEC
    try:
        while time.time() < deadline:
            if gen != _reply_gen[0]:
                return
            if pos < len(_hist):
                pos2 = _apply_skip(pos)
                if pos2 != pos:
                    pos = pos2
                    continue
                try:
                    res = _play_part(_hist[pos], gen, start)
                except Exception as e:
                    _playing[0] = False
                    log(f"не удалось проиграть ответ: {e!r}")
                    res = "done"
                if res == "stop":
                    return
                pos, start = _step(res, pos)
                poll_now = True
                continue
            if final:
                return
            _playing[0] = False                             # ждём следующую часть — это уже простой
            # ждём следующую часть; двойной Alt в тишине — повтор последней. 16.10: опрос ленты чаще (0,4 с вместо 1,5 с) и сразу
            # после конца части — предложения идут потоком, щель между ними должна быть минимальной
            for _ in range(0 if poll_now else 4):
                if _back[0] or gen != _reply_gen[0]:
                    break
                time.sleep(0.1)
            poll_now = False
            if _back[0]:
                _back[0] = False
                if _hist:
                    pos, start = (_tail_pos() if len(_hist[-1]) > 3 and _hist[-1][3] is not None else len(_hist) - 1), 0
                continue
            try:
                with urllib.request.urlopen(REPLY_URL + "voice_reply.json?_=%d" % time.time(), timeout=5) as r:
                    meta = json.loads(r.read().decode("utf-8"))
            except Exception:
                continue
            if not any(k in norm(meta.get("for", "")) for k in keys):
                continue
            parts = meta.get("parts") or [dict(meta, final=True)]     # старый формат — одна часть
            for p in parts:
                if p.get("id") in seen or p.get("id") in _взяты or p.get("ts", 0) < t_sent - 2:
                    continue
                seen.add(p.get("id")); _взяты.add(p.get("id")); _взята_ts[0] = max(_взята_ts[0], p.get("ts", 0))
                try:
                    with urllib.request.urlopen(REPLY_URL + p["wav"], timeout=10) as r:
                        data = r.read()
                    if gen != _reply_gen[0]:
                        return
                    _hist.append(_item(p, data, meta))
                    while len(_hist) > HIST_MAX:            # самую старую часть забываем, указатель сдвигаем вместе с ней
                        _hist.pop(0)
                        pos = max(0, pos - 1)
                    log(f"ответ голосом, часть {p.get('seq', 1)}{' (финал)' if p.get('final') else ''} "
                        f"({len(p.get('text', ''))} симв.): {p.get('text', '')[:120]}")
                except Exception as e:
                    log(f"не удалось получить часть ответа: {e!r}")
                if p.get("final"):
                    final = True
                    _ожидают.clear()        # ход закрыт — прежние ключи больше не ждут
                    break
    finally:
        if _live_gen[0] == gen:
            _live_gen[0] = 0
            _playing[0] = False
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
        self.status_t = 0.0
        self.last_key = 0.0
        self.voiced_n = 0
        self.probes = 0
        self.skipped = 0
        self.win_max = 0.0
        self.last_cb = time.time()
        self.reopen_t = 0.0
        self.dead_max = 0.0; self.dead_n = 0      # наибольшая громкость за последние 30–60 с и число прошедших полуминут
        self.reopens = 0
        self.mic_fails = 0
        self.off_console = False
        self.stream = None
        self._open_stream()
        threading.Thread(target=self._wake_loop, daemon=True).start()
        threading.Thread(target=self._flag_loop, daemon=True).start()
        threading.Thread(target=self._guard_loop, daemon=True).start()

    # 26.09: в 02:42 (Win32k 267 — экран/сон) микрофон K66 перестал отдавать звук, а поток PortAudio не упал и не
    # сообщил ошибку: колбэк просто больше не вызывался. «Алёна» висела на wake_q.get(), диктовка по Ctrl получала
    # ноль кусков и молча выходила (сигнал «слушаю» звучал, текста не было) — 8,5 ч без диктовки. Теперь сторож
    # в _wake_step: колбэк молчит дольше MIC_STALL_SEC — PortAudio переинициализируется (заново читает список
    # устройств: после переподключения USB у микрофона новый индекс) и поток открывается на устройстве по умолчанию.
    def _open_stream(self):
        if self.stream is not None:
            try:
                self.stream.abort()
                self.stream.close()
            except Exception as e:
                log(f"микрофон: старый поток не закрылся: {e!r}")
            self.stream = None
            try:
                sd._terminate()
                sd._initialize()
            except Exception as e:
                log(f"микрофон: PortAudio не переинициализировался: {e!r}")
        idx = None
        try:
            info = sd.query_devices(kind="input")
            dev = info["name"]
            # 13.10.2026: в 10:55 после короткого сбоя K66 устройством по умолчанию на миг оказался виртуальный кабель
            # «CABLE Output» — поток открылся на нём и остался: кабель исправно зовёт колбэк, но отдаёт чистую тишину,
            # сторож «микрофон молчит» этого не видит, диктовка по клавише приносила пустые записи. Виртуальный кабель
            # микрофоном не считаем: берём настоящий вход того же звукового интерфейса, K66 — первым.
            # 13.10.2026 11:06: первая правка взяла «Переназначение звуковых устр. - Input» — это то же устройство по
            # умолчанию, снова кабель. Теперь: есть K66 — берём его всегда; нет, а по умолчанию виртуальное — первый настоящий.
            real = [(i, d["name"]) for i, d in enumerate(sd.query_devices())
                    if d["max_input_channels"] > 0 and d["hostapi"] == info["hostapi"] and not _virtual_mic(d["name"])]
            свой = [x for x in real if MIC_PREFER in x[1]]
            if свой and MIC_PREFER not in dev:
                log(f"микрофон по умолчанию — {dev}; беру {свой[0][1]}")
                idx, dev = свой[0]
            elif _virtual_mic(dev) and real:
                log(f"микрофон по умолчанию — виртуальный ({dev}); беру {real[0][1]}")
                idx, dev = real[0]
        except Exception:
            dev = "?"
        self.stream = sd.InputStream(device=idx, samplerate=RATE, channels=1, dtype="int16", blocksize=BLOCK, callback=self._cb)
        self.stream.start()
        self.last_cb = time.time()
        self.mic = dev
        log(f"микрофон открыт: {dev}")

    # 29.09: в 10:22 сеанс rdpuser ушёл с монитора в RDP (вход с A54), в 10:28 RDP закрыт, в 11:16 сеанс вернулся на
    # консоль — а процесс до перезапуска в 11:41 так и получал «Error querying device -1»: переинициализация PortAudio
    # внутри процесса после смены сеанса устройств не видит. Пока сеанс не на консоли (RDP или отключён), местных
    # микрофонов у него нет — ждём; вернулся на консоль и переоткрыть не удаётся — перезапускаем процесс целиком.
    @staticmethod
    def _on_console():
        sid = ctypes.c_ulong()
        ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(sid))
        return ctypes.windll.kernel32.WTSGetActiveConsoleSessionId() == sid.value

    def _maybe_restart(self, now):
        if self.mic_fails < MIC_FAILS_RESTART or now - START_T < MIN_UPTIME_RESTART:
            return
        try:
            if not self._on_console():
                return
        except Exception as e:
            log(f"сеанс: не проверить консоль: {e!r}")
        self._restart_process(f"микрофон не открывается {self.mic_fails} раз подряд, сеанс на консоли")

    def _restart_process(self, why):
        log(f"{why} — перезапускаю процесс")
        try:
            subprocess.Popen([sys.executable, os.path.abspath(__file__)], cwd=HERE, close_fds=True,
                             creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
        except Exception as e:
            log(f"перезапуск не удался: {e!r}")
            return
        os._exit(0)

    # 07.10: RDP с 15:42 до 16:21 — 168 переоткрытий «Error querying device -1», на 169-м PortAudio завис внутри
    # _open_stream: пульс status.json и журнал встали в 16:10, сеанс вернулся на консоль в 16:21, а сторож в том же
    # потоке — висел, диктовка молчала до ручного перезапуска. Поэтому: (1) вне консоли PortAudio не трогаем вовсе,
    # только ждём; (2) вернулись на консоль после отлучки — перезапуск процесса сразу, без проб; (3) отдельный
    # поток-сторож: пульс _wake_step молчит дольше GUARD_STALL_SEC на консоли — перезапуск из другого потока.
    def _guard_loop(self):
        while True:
            time.sleep(15)
            now = time.time()
            if now - START_T < MIN_UPTIME_RESTART or now - self.status_t < GUARD_STALL_SEC:
                continue
            try:
                if not self._on_console():
                    continue
            except Exception:
                continue
            self._restart_process(f"поток «Алёна» не подаёт пульс {now - self.status_t:.0f} с")

    def _cb(self, indata, frames, t, status):
        self.last_cb = time.time()
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
        if self.cancelled or dur < MIN_SEC:
            return
        if not self.chunks:
            log(f"клавиша: {dur:.1f} с, но с микрофона не пришло ни одного куска звука")
            beep((300, 300))
            return
        pcm = np.concatenate(self.chunks).tobytes()
        threading.Thread(target=self._deliver, args=(pcm, dur, False), daemon=True).start()

    # ---------- «Алёна» ----------
    # Vosk-модель small-ru не знает слова «Алёна» (слышит «она»/«подобно»), поэтому слово-триггер проверяет сам
    # GigaAM: детектор речи по громкости режет поток на фразы, первые PROBE_SEC каждой фразы (с 0,3 с до начала)
    # уходят на распознавание; если распознанное начинается с «Алёна» — сигнал, и фраза пишется до паузы целиком.
    def _wake_loop(self):
        import traceback
        while True:
            try:
                self._wake_step()
            except Exception:
                log("ошибка в потоке «Алёна»: " + traceback.format_exc().replace("\n", " | ")[-600:])
                self.seg = None
                time.sleep(1)

    def _write_status(self, now):
        st = {"t": datetime.datetime.now().isoformat(timespec="seconds"), "mic": getattr(self, "mic", "?"),
              "mic_silent_s": round(now - self.last_cb, 4), "reopens": self.reopens, "wake": self.wake_enabled,
              "noise": round(self.noise, 4), "busy": self.busy}
        try:
            with open(STATUS + ".tmp", "w", encoding="utf-8") as f:
                json.dump(st, f, ensure_ascii=False)
            os.replace(STATUS + ".tmp", STATUS)
        except Exception:
            pass

    def _wake_step(self):
        try:
            block = self.wake_q.get(timeout=1)
        except queue.Empty:
            block = None
        now = time.time()
        if now - self.status_t > 30:
            self.status_t = now
            self._write_status(now)
            # Раз в 30 с: поток сидит не на том устройстве или отдаёт цифровую тишину — переоткрыть (PortAudio заново читает
            # список, вернувшийся K66 находится). Не на K66 — проверяем раз в 60 с, пока идёт запись по клавише — не трогаем.
            мёртв = self.dead_n >= 1 and self.dead_max <= MIC_DEAD_RMS
            чужой = MIC_PREFER not in (self.mic or "") and now - self.reopen_t > 60
            self.dead_n += 1
            if self.dead_n > 1: self.dead_max = 0.0; self.dead_n = 1
            if (мёртв or _virtual_mic(self.mic) or чужой) and not self.rec and now - self.reopen_t > 30:
                self.reopen_t = now
                try:
                    if self._on_console():
                        log(f"микрофон: {'цифровая тишина' if мёртв else 'не тот вход'} ({self.mic}) — переоткрываю")
                        self._open_stream(); self.dead_max = 0.0; self.dead_n = 0
                except Exception as e:
                    log(f"микрофон не переоткрылся: {e!r}")
        if block is None:
            if now - self.last_cb > MIC_STALL_SEC and now - self.reopen_t > 10:
                self.reopen_t = now
                try:
                    on_console = self._on_console()
                except Exception as e:
                    log(f"сеанс: не проверить консоль: {e!r}")
                    on_console = True
                if not on_console:
                    if not self.off_console:
                        self.off_console = True
                        log("сеанс не на консоли (RDP или отключён) — микрофона нет, жду возврата")
                    return
                if self.off_console and now - START_T >= MIN_UPTIME_RESTART:
                    self._restart_process("сеанс вернулся на консоль после отлучки")
                self.off_console = False
                self.reopens += 1
                log(f"микрофон молчит {now - self.last_cb:.0f} с — переоткрываю поток (раз {self.reopens})")
                try:
                    self._open_stream()
                    self.mic_fails = 0
                except Exception as e:
                    self.mic_fails += 1
                    log(f"микрофон не открылся: {e!r}")
                    self._maybe_restart(now)
            return
        rms = float(np.sqrt(np.mean(block.astype(np.float32) ** 2)))
        now = time.time()
        self.win_max = max(self.win_max, rms)
        self.dead_max = max(self.dead_max, rms)
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
                self.seg = None                   # щелчок, стук, короткий шум — на распознавание не шлём
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
            code_win = is_code_window(title)
            send = wake and code_win
            log(f"{'Алёна' if wake else 'клавиша'}: {dur:.1f} с → {took:.1f} с, окно «{title[:60]}»{' +Enter' if send else ''}: {text}")
            if not text:
                return
            ml = moonlight_host(title)
            if ml:
                # 29.10.2026: поток Moonlight — фразу вставляет приёмник на хосте; при сбое фраза остаётся
                # в буфере XPS (вставить потом руками в другом окне) и звучит сигнал ошибки
                ok, okno_hosta = отправить_на_хост(ml, text + " ")
                if not ok:
                    буфер_записать(text)
                    beep((300, 300))
                elif is_code_window(okno_hosta):
                    # 29.10.2026 (вечер): на хосте впереди чат Code — ждём ручной Enter (он придёт с хоста) и озвучим ответ
                    _armed["все"].append(text)
                    _armed.update(text=_armed["text"] or text, t=time.time())
                return
            вставить(text if send else text + " ")
            if send:
                time.sleep(0.15)
                keyboard.send("enter")
                _armed.update(text="", t=0.0, все=[])   # сообщение ушло само — ручного Enter ждать нечего
                threading.Thread(target=speak_reply, args=(text, time.time()), daemon=True).start()
            elif code_win:
                # диктовка правым Ctrl в чат Code: ответ озвучим, когда пользователь сам нажмёт Enter (28.09).
                # Ключ сопоставления — ПЕРВАЯ фраза сообщения: следующие диктовки до Enter её не перезаписывают.
                # 12.10: запоминаем каждую фразу сообщения и время последней — срок ожидания Enter считается от неё
                _armed["все"].append(text)
                _armed.update(text=_armed["text"] or text, t=time.time())
        finally:
            self.busy = False
        # 12.10.2026: хозяин сказал «Готово.» и нажал Enter раньше, чем закончилась вставка — Enter был отброшен как «занято»,
        # ответ не озвучился, а фраза осталась взведённой и подменила ключ следующего сообщения. Такой ранний Enter запоминаем
        # и отрабатываем здесь, сразу после вставки.
        if _armed["text"] and time.time() - _enter_рано[0] < 3:
            _enter_рано[0] = 0.0
            on_enter()


def voice_hotkey(action):
    """Горячая клавиша голоса Алёны (30.09.2026, по образцу правых Ctrl/Alt): короткое нажатие одного правого Shift —
    следующий голос Gemini по списку, двойное (два нажатия за 0,4 с) — режим «Случайный» (новый голос на каждый ответ). Вебхук HA alena_golos_hotkey → input_select.golos_alyony →
    голос.json, который voice_reply.py читает на каждую часть; перезапусков не нужно."""
    def run():
        try:
            req = urllib.request.Request(f"http://{HOST}:8123/api/webhook/alena_golos_hotkey", method="POST",
                                         data=json.dumps({"action": action}).encode("utf-8"),
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=5).read()
            beep((900, 50), (1100, 50)) if action == "next" else beep((1100, 40), (800, 40), (1100, 40))
            balloon("🎙 голос: " + ("следующий" if action == "next" else "случайный"))
            log(f"горячая клавиша голоса: {action}")
        except Exception as e:
            log(f"горячая клавиша голоса не сработала: {e!r}")
            beep((300, 200))
    threading.Thread(target=run, daemon=True).start()


def main():
    v = Voice()

    # правый Alt считается, только если нажат один, без других клавиш; двойное нажатие за 0,4 с — «назад» (03.10)
    alt = {"down": False, "other": False, "last": 0.0, "timer": None}
    rsh = {"down": False, "other": False, "last": 0.0, "timer": None}   # правый Shift — так же, только в одиночку

    def rshift_fire():
        rsh["timer"] = None
        voice_hotkey("next")

    def alt_fire():
        alt["timer"] = None
        prev_part()        # 16.10.2026 хозяин поменял: одиночный правый Alt — назад (к началу текущего/предыдущего сообщения)

    def on_event(e):
        if e.event_type == "down":
            v.last_key = time.time()
        if e.name == "right shift":
            if e.event_type == "down":
                if not rsh["down"]:
                    rsh.update(down=True, other=False)
            else:
                if rsh["down"] and not rsh["other"]:
                    t = time.time()
                    if rsh["timer"] and t - rsh["last"] < 0.4:        # второе нажатие — случайный голос
                        rsh["timer"].cancel()
                        rsh["timer"] = None
                        voice_hotkey("random")
                    else:                                             # ждём, не будет ли второго нажатия
                        rsh["timer"] = threading.Timer(0.4, rshift_fire)
                        rsh["timer"].start()
                    rsh["last"] = t
                rsh["down"] = False
            return
        if e.event_type == "down" and rsh["down"]:
            rsh["other"] = True                                      # Shift+буква — обычный ввод, не команда
        if e.name in SKIP_KEYS:
            if e.event_type == "down":
                if not alt["down"]:
                    alt.update(down=True, other=False)
            else:
                if alt["down"] and not alt["other"]:
                    t = time.time()
                    if alt["timer"] and t - alt["last"] < 0.4:        # второе нажатие — предыдущая часть
                        alt["timer"].cancel()
                        alt["timer"] = None
                        skip_part()   # 16.10.2026: двойной правый Alt — вперёд, к следующему сообщению
                    else:                                             # ждём, не будет ли второго нажатия
                        alt["timer"] = threading.Timer(0.4, alt_fire)
                        alt["timer"].start()
                    alt["last"] = t
                alt["down"] = False
            return
        if e.event_type == "down" and alt["down"]:
            alt["other"] = True
        if e.event_type == "down" and e.name == "enter" and not getattr(e, "shift", False) and not keyboard.is_pressed("shift"):   # Shift+Enter в чате — перенос строки
            if v.busy:
                _enter_рано[0] = time.time()     # вставка ещё идёт — отработаем в конце _deliver2
            elif _armed["text"]:
                on_enter()
        if e.name == HOTKEY:
            if e.event_type == "down":
                stop_reply()
                v.key_start()
            else:
                v.key_stop()
        elif e.event_type == "down" and v.rec:
            v.cancelled = True

    zamok = threading.Lock()     # свой хук и клавиши с хоста Moonlight приходят из разных потоков

    def on_local(e):
        if klav_dubl("local", getattr(e, "name", None), e.event_type):
            return
        with zamok:
            on_event(e)

    def on_host(e):
        with zamok:
            on_event(e)

    _vnesh["obrabotchik"] = on_host
    threading.Thread(target=klav_server, daemon=True).start()
    keyboard.hook(on_local)
    log("запущено: правый Ctrl — диктовка (и оборвать ответ); правый Alt — следующая часть ответа, дважды — предыдущая; нампад «.» — следующая часть-предложение, Enter — предыдущая, двойной Enter — заново, Backspace — пауза, «+»/«−» — темп предложения, «*» — норма, пока звучит ответ; правый Alt — следующее сообщение; «Алёна, …» — фраза в активное окно (в VS Code с Enter); правый Shift — следующий голос, двойной — случайный")
    keyboard.wait()


if __name__ == "__main__":
    main()
