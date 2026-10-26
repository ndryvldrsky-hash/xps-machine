"""Запись звонков WhatsApp Desktop на XPS (26.10.2026, хозяин: «надо сделать звонки WhatsApp с XPS»).

Зачем: Android не даёт записывать звонки WhatsApp; важные разговоры хозяин ведёт с XPS, а мы их пишем и разбираем дома
(/config/.local/bin/звонки_whatsapp_xps.py — расшифровка, перевод с иврита, итог пушем и в журнал Секретаря).

Как работает (служба, pythonw, задача планировщика AlenaWaZvonki в сеансе rdpuser — из сеанса 0 звука и HKCU не видно):
- раз в секунду проверяет, идёт ли звонок: WhatsApp держит микрофон (HKCU ...\\ConsentStore\\microphone\\<пакет WhatsApp>,
  LastUsedTimeStop = 0) ИЛИ у процесса WhatsApp есть активная сессия захвата звука (pycaw);
- на время звонка пишет два канала: ЛЕВЫЙ — собеседник (WASAPI loopback устройства вывода, где играет WhatsApp; чтобы
  loopback не замирал в тишине, на то же устройство идёт поток нулей), ПРАВЫЙ — хозяин (микрофон по умолчанию, K66);
- после конца (3 с без признаков звонка): WAV 16 кГц стерео, разметка речи по каналам (VAD), mp3 для передачи домой,
  описание <имя>.json с "готово": true. Дом забирает по WinRM, после забора кладёт рядом <имя>.забран.
- ПРОБА без звонка: файл «проба.flag» в каталоге звонков = «звонок идёт», пока файл есть. В файле можно указать
  часть имени устройства вывода для loopback (например «Realtek» — служебный звук, хозяину не слышен).

Каталог: %ProgramData%\\alena\\звонки (на XPS это W:\\ProgramData\\alena\\звонки). Журнал — запись.log там же.
"""
import json, os, queue, subprocess, sys, threading, time, traceback, wave
# comtypes и pycaw у Python XPS стоят в пользовательском каталоге alena — rdpuser их не видит; своя копия рядом со скриптом
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
import warnings
warnings.filterwarnings("ignore")      # pycaw ругается на свойства отдельных устройств — не мешает
import numpy as np
import pyaudiowpatch as pa

D = os.path.join(os.environ.get("ProgramData", r"W:\ProgramData"), "alena", "звонки")
os.makedirs(D, exist_ok=True)
LOG = os.path.join(D, "запись.log")
ФЛАГ = os.path.join(D, "проба.flag")
FFMPEG = r"W:\ffmpeg\ffmpeg-9.0.1-essentials_build\bin\ffmpeg.exe"
ПАКЕТ = "5319275A.WhatsAppDesktop_cv1g1gvanyjgm"
КЛЮЧ_МИК = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone" + "\\" + ПАКЕТ
RATE_OUT = 16000
ХВОСТ_СЕК = 3          # столько секунд без признаков звонка — звонок окончен


def log(*a):
    s = time.strftime("%d.%m %H:%M:%S ") + " ".join(str(x) for x in a)
    try:
        with open(LOG, "a", encoding="utf-8") as f: f.write(s + "\n")
    except Exception: pass


# ---------- признаки звонка ----------
def мик_у_whatsapp():
    """True — WhatsApp сейчас держит микрофон (по журналу доступа Windows)."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, КЛЮЧ_МИК) as k:
            start = winreg.QueryValueEx(k, "LastUsedTimeStart")[0]
            stop = winreg.QueryValueEx(k, "LastUsedTimeStop")[0]
            return bool(start) and stop == 0
    except OSError:
        return False


def сессии_whatsapp():
    """Активные звуковые сессии процессов WhatsApp: [(вывод|захват, имя устройства)]."""
    out = []
    try:
        import psutil, comtypes
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioSessionManager2, IAudioSessionControl2
        comtypes.CoInitialize()
        pids = {p.pid for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower().startswith("whatsapp")}
        if not pids: return out
        en = AudioUtilities.GetDeviceEnumerator()
        for поток, вид in ((0, "вывод"), (1, "захват")):
            coll = en.EnumAudioEndpoints(поток, 1)
            for i in range(coll.GetCount()):
                dev = coll.Item(i)
                try: имя = AudioUtilities.CreateDevice(dev).FriendlyName
                except Exception: имя = "?"
                mgr = dev.Activate(IAudioSessionManager2._iid_, CLSCTX_ALL, None).QueryInterface(IAudioSessionManager2)
                se = mgr.GetSessionEnumerator()
                for j in range(se.GetCount()):
                    c = se.GetSession(j); c2 = c.QueryInterface(IAudioSessionControl2)
                    if c2.GetProcessId() in pids and c.GetState() == 1:
                        out.append((вид, имя))
    except Exception as e:
        log("сессии: ошибка", repr(e)[:200])
    return out


def идёт_звонок():
    """(идёт, источник, устройство вывода или '')"""
    if os.path.exists(ФЛАГ):
        try: устр = open(ФЛАГ, encoding="utf-8-sig").read().strip()
        except Exception: устр = ""
        return True, "проба", устр
    с = сессии_whatsapp()
    вывод = next((имя for вид, имя in с if вид == "вывод"), "")
    if мик_у_whatsapp() or any(вид == "захват" for вид, _ in с):
        return True, "whatsapp", вывод
    return False, "", вывод


# ---------- запись ----------
class Запись:
    def __init__(self, источник, устройство):
        self.имя = time.strftime("%Y%m%d_%H%M%S")
        self.источник = источник
        self.p = pa.PyAudio()
        w = self.p.get_host_api_info_by_type(pa.paWASAPI)
        out_def = self.p.get_device_info_by_index(w["defaultOutputDevice"])
        # устройство вывода: подсказка (флаг пробы или сессия WhatsApp) → иначе вывод по умолчанию
        выход = None
        if устройство:
            for i in range(self.p.get_device_count()):
                d = self.p.get_device_info_by_index(i)
                if d["hostApi"] == w["index"] and d["maxOutputChannels"] > 0 and not d.get("isLoopbackDevice") \
                        and устройство.lower() in d["name"].lower():
                    выход = d; break
        выход = выход or out_def
        петля = next((d for d in self.p.get_loopback_device_info_generator() if d["name"].startswith(выход["name"])), None)
        мик = self.p.get_device_info_by_index(w["defaultInputDevice"])
        self.устройства = {"вывод": выход["name"], "микрофон": мик["name"]}
        self.ql, self.qr = queue.Queue(), queue.Queue()
        self.фл = open(os.path.join(D, self.имя + "_L.raw"), "wb")
        self.фр = open(os.path.join(D, self.имя + "_R.raw"), "wb")
        self.хвост = {"L": np.zeros(0, np.float32), "R": np.zeros(0, np.float32)}
        self.начало = time.time(); self.whatsapp_звук = bool(устройство) and источник == "whatsapp"
        # поток нулей на то же устройство: WASAPI loopback в полной тишине не отдаёт кадров, и каналы разъезжаются
        ch_o = min(2, выход["maxOutputChannels"]); r_o = int(выход["defaultSampleRate"])
        тишина = bytes(2 * ch_o * 1024)
        self.s_тишина = self.p.open(format=pa.paInt16, channels=ch_o, rate=r_o, output=True, output_device_index=выход["index"],
                                    frames_per_buffer=1024, stream_callback=lambda *a: (тишина, pa.paContinue))
        self.lr, self.lch = int(петля["defaultSampleRate"]), петля["maxInputChannels"]
        self.mr, self.mch = int(мик["defaultSampleRate"]), max(1, min(2, мик["maxInputChannels"]))
        self.s_l = self.p.open(format=pa.paFloat32, channels=self.lch, rate=self.lr, input=True, input_device_index=петля["index"],
                               frames_per_buffer=1024, stream_callback=lambda d, *a: (self.ql.put(d), (None, pa.paContinue))[1])
        self.s_r = self.p.open(format=pa.paFloat32, channels=self.mch, rate=self.mr, input=True, input_device_index=мик["index"],
                               frames_per_buffer=1024, stream_callback=lambda d, *a: (self.qr.put(d), (None, pa.paContinue))[1])
        log("звонок начат:", self.имя, источник, self.устройства, f"loopback {self.lr} Гц ×{self.lch}, мик {self.mr} Гц ×{self.mch}")

    def _в16(self, кан, данные, rate, ch):
        """float32 interleaved → моно int16 16 кГц (усреднение по кратному шагу; 48 кГц → шаг 3)."""
        x = np.frombuffer(данные, np.float32).reshape(-1, ch).mean(axis=1)
        x = np.concatenate([self.хвост[кан], x])
        шаг = max(1, rate // RATE_OUT); n = len(x) // шаг * шаг
        self.хвост[кан] = x[n:]
        y = x[:n].reshape(-1, шаг).mean(axis=1)
        return (np.clip(y, -1, 1) * 32767).astype("<i2").tobytes()

    def слить(self):
        for q, f, кан, rate, ch in ((self.ql, self.фл, "L", self.lr, self.lch), (self.qr, self.фр, "R", self.mr, self.mch)):
            куски = []
            while True:
                try: куски.append(q.get_nowait())
                except queue.Empty: break
            if куски: f.write(self._в16(кан, b"".join(куски), rate, ch))

    def закончить(self):
        for s in (self.s_l, self.s_r, self.s_тишина):
            try: s.stop_stream(); s.close()
            except Exception: pass
        self.слить(); self.фл.close(); self.фр.close()
        try: self.p.terminate()
        except Exception: pass
        threading.Thread(target=обработать, args=(self.имя, self.начало, time.time(), self.источник, self.устройства,
                                                  self.whatsapp_звук), daemon=True).start()


def речь(x, rate=RATE_OUT):
    """Разметка речи в моно int16: [(начало_с, конец_с)]. Окно 30 мс, порог от уровня шума, склейка пауз до 0,7 с."""
    win = int(rate * 0.03)
    if len(x) < win: return []
    n = len(x) // win
    rms = np.sqrt((x[:n * win].astype(np.float32).reshape(n, win) ** 2).mean(axis=1))
    шум = np.percentile(rms, 20)
    порог = max(150.0, шум * 3)
    акт = rms > порог
    сег, нач = [], None
    for i, a in enumerate(акт):
        if a and нач is None: нач = i
        if not a and нач is not None: сег.append([нач, i]); нач = None
    if нач is not None: сег.append([нач, n])
    склеено = []
    for s in сег:
        if склеено and (s[0] - склеено[-1][1]) * 0.03 < 0.7: склеено[-1][1] = s[1]
        else: склеено.append(s)
    return [(round(a * 0.03, 2), round(b * 0.03, 2)) for a, b in склеено if (b - a) * 0.03 >= 0.35]


def обработать(имя, t0, t1, источник, устройства, whatsapp_звук):
    try:
        L = np.fromfile(os.path.join(D, имя + "_L.raw"), "<i2")
        R = np.fromfile(os.path.join(D, имя + "_R.raw"), "<i2")
        n = max(len(L), len(R))
        L = np.pad(L, (0, n - len(L))); R = np.pad(R, (0, n - len(R)))
        st = np.empty(2 * n, "<i2"); st[0::2] = L; st[1::2] = R
        wav = os.path.join(D, имя + ".wav")
        with wave.open(wav, "wb") as w:
            w.setnchannels(2); w.setsampwidth(2); w.setframerate(RATE_OUT); w.writeframes(st.tobytes())
        сл, ср = речь(L), речь(R)
        ok = {}
        for суфф, args in (("", ["-ac", "2", "-b:a", "48k"]), ("_собеседник", ["-af", "pan=mono|c0=c0", "-b:a", "32k"]),
                           ("_хозяин", ["-af", "pan=mono|c0=c1", "-b:a", "32k"])):
            mp3 = os.path.join(D, имя + суфф + ".mp3")
            r = subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", wav, *args, mp3], capture_output=True,
                               creationflags=0x08000000)
            ok[суфф or "стерео"] = r.returncode == 0 and os.path.exists(mp3)
        мета = {"имя": имя, "начало": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t0)),
                "конец": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t1)), "сек": round(n / RATE_OUT, 1),
                "источник": источник, "устройства": устройства, "звук_whatsapp": whatsapp_звук,
                "уровень_собеседник": int(np.sqrt((L.astype(np.float32) ** 2).mean())) if n else 0,
                "уровень_хозяин": int(np.sqrt((R.astype(np.float32) ** 2).mean())) if n else 0,
                "речь_собеседник": сл, "речь_хозяин": ср, "mp3": ok, "готово": True}
        tmp = os.path.join(D, имя + ".json.tmp")
        json.dump(мета, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        os.replace(tmp, os.path.join(D, имя + ".json"))
        # сырые половинки больше не нужны — переименовываем, не удаляем (уборка — по решению хозяина)
        for к in ("_L.raw", "_R.raw"):
            try: os.replace(os.path.join(D, имя + к), os.path.join(D, "сырое", имя + к))
            except Exception: pass
        log("звонок обработан:", имя, f"{мета['сек']} с, речь: собеседник {len(сл)}, хозяин {len(ср)}, mp3 {ok}")
    except Exception:
        log("обработка не удалась:", имя, traceback.format_exc()[-600:])


def main():
    os.makedirs(os.path.join(D, "сырое"), exist_ok=True)
    log("служба запущена, pid", os.getpid())
    зап, тихо_с, тик = None, None, 0
    while True:
        try:
            идёт, источник, устр = идёт_звонок()
            if идёт and зап is None:
                зап = Запись(источник, устр); тихо_с = None
            elif зап is not None:
                # у звонка WhatsApp есть и сессия вывода — так звонок отличается от голосового сообщения (там только микрофон)
                if устр and источник == "whatsapp": зап.whatsapp_звук = True
                if not идёт:
                    тихо_с = тихо_с or time.time()
                    if time.time() - тихо_с >= ХВОСТ_СЕК:
                        log("звонок окончен:", зап.имя, f"{time.time() - зап.начало:.0f} с")
                        зап.закончить(); зап = None
                else:
                    тихо_с = None
            if зап is not None:
                зап.слить()
            тик += 1
            if тик % 600 == 0: log("жива")
        except Exception:
            log("ошибка цикла:", traceback.format_exc()[-600:])
            if зап is not None:
                try: зап.закончить()
                except Exception: pass
                зап = None
            time.sleep(5)
        time.sleep(1)


if __name__ == "__main__":
    # один экземпляр: замок-файл с открытым дескриптором
    import msvcrt
    _замок = open(os.path.join(D, "запись.lock"), "w")
    try: msvcrt.locking(_замок.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError: sys.exit(0)
    main()
