"""Проба записи звонков WhatsApp: проиграть WAV в устройство вывода по части имени (26.10.2026).

  pythonw play.py <файл.wav> <часть имени устройства, напр. Realtek>
Запускать в сеансе rdpuser. Служебный Realtek хозяину не слышен — для проб «собеседника» без живого человека.
"""
import sys, wave
import numpy as np
import sounddevice as sd

f, часть = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "Realtek")
wasapi = next(i for i, h in enumerate(sd.query_hostapis()) if "WASAPI" in h["name"])
dev = next(i for i, d in enumerate(sd.query_devices())
           if d["hostapi"] == wasapi and d["max_output_channels"] > 0 and часть.lower() in d["name"].lower())
with wave.open(f) as w:
    ch, rate, n = w.getnchannels(), w.getframerate(), w.getnframes()
    x = np.frombuffer(w.readframes(n), "<i2").reshape(-1, ch).astype(np.float32) / 32768
# WASAPI в общем режиме хочет частоту устройства — простая передискретизация линейной интерполяцией
r_dev = int(sd.query_devices(dev)["default_samplerate"])
if rate != r_dev:
    t = np.arange(0, len(x), rate / r_dev)
    x = np.stack([np.interp(t, np.arange(len(x)), x[:, c]) for c in range(ch)], axis=1).astype(np.float32)
sd.play(x, r_dev, device=dev, blocking=True)
