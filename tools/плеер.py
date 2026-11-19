# -*- coding: utf-8 -*-
r"""Плеер озвучки Алёны на XPS — отдельный процесс (19.11.2026, вариант В по Грамоте Премудрой №2, план /config/.local/озвучка/план_В.md).

Шаг 4 — играет сам, если W:\Tools\pleer\плеер_режим.txt = «новый» (откат — «старый», без перезаливки): лента v2 → список фраз
по ord; ответ на голос — всегда, на печать и фон — при автоозвучке; звук — с /local/ Штаба, неготовое — событие «хочу» дому;
клавиши — POST /komanda {"вид":"клавиша","к":…} от dictate.py; часы для подсветки и «сыграно» — событиями дому.

Канал разворачивается наоборот: у аддона Студии нет открытых наружу портов, поэтому слушает плеер, а дом (voice_reply.py, поток
канал_плеера) сам держит к нему длинный запрос.
  GET  /zayavki?posle=N  — дом ждёт до 25 с события плеера с номером > N: {"события": [{n, вид, …}], "n": последний}
  POST /лента           — дом присылает ленту v2 целиком (lenta.json) при каждом изменении
  POST /zvuk/<id>       — дом присылает WAV предложения телом (шаг 4)
  POST /komanda         — только с 127.0.0.1, без токена: dictate.py (клавиши) и пробы кладут событие для дома
                          {"вид": "хочу", "ids": [...]} | {"вид": "тест", "t": <время отправки>} | …
Доступ: сети как у klav_server dictate.py, заголовок X-Token = W:\\Tools\\pleer\\token (у дома — Укромное место, плеер_xps_token.txt).
Пульс — плеер_status.json раз в 30 с. Второй экземпляр не стартует: порт занят.
"""
import hmac, io, ipaddress, json, os, socket, sys, threading, time, traceback, urllib.request, wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

ЗДЕСЬ = os.path.dirname(os.path.abspath(__file__))
ПОРТ = 10322
СЕТИ = [ipaddress.ip_network(n) for n in ("192.168.77.0/24", "100.64.0.0/10", "127.0.0.0/8")]
ТОКЕН = os.path.join(ЗДЕСЬ, "token")
ЛЕНТА = os.path.join(ЗДЕСЬ, "lenta.json")
ЗВУК = os.path.join(ЗДЕСЬ, "звук")
ЖУРНАЛ = os.path.join(ЗДЕСЬ, "плеер.log")
ПУЛЬС = os.path.join(ЗДЕСЬ, "плеер_status.json")
ДЛИННЫЙ_С = 25.0
# ---- шаг 4: воспроизведение ----
АВТО_ФАЙЛ = r"W:\Tools\dictate\автоозвучка.txt"        # общий с dictate.py: «вкл» — ответы на печать и фон играют сами
OUT_DEVICE_FILE = r"W:\Tools\dictate\out_device"
РЕЖИМ_ФАЙЛ = os.path.join(ЗДЕСЬ, "плеер_режим.txt")      # «новый» — играет этот плеер; иначе — старый в dictate.py
АКТИВНА = os.path.join(ЗДЕСЬ, "активна")                  # время, пока играю/пауза/жду звук — dictate.py: озвучка_активна()
ЗВУК_URL = "http://shtab.lan:8123/local/voice_reply/"
ЖДАТЬ_ЗВУК_С = 20.0
НАЗАД_ОКНО_С = 4.0
# ---- шаг 5 ----
ТЕМПЫ = (0.6, 0.75, 0.9, 1.0, 1.15, 1.3)                  # как SPEED_STEPS дома (+ 1.0)
ВЕБХУК = "http://192.168.77.220:8123/api/webhook/alena_golos_skorost"   # запасной канал: «id,id|req|nonce», «id|k|nonce»
ДОМ_МОЛЧИТ_С = 40.0                                      # длинного запроса дома нет столько — канал считаю оборванным
БАЛЛОН = "http://127.0.0.1:10321/balloon"                 # подсказка над указателем — рисует dictate.py
СОБЫТИЙ_ДЕРЖАТЬ = 500

_зам = threading.Condition()
_события = []          # [{n, вид, …, t}]
_n = [0]
_дом = {"последний_запрос": 0.0, "лента_ts": 0.0, "ходов": 0, "звуков": 0}


def log(s):
    try:
        with open(ЖУРНАЛ, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + str(s) + "\n")
    except Exception:
        pass


def канал_жив():
    return bool(_дом["последний_запрос"]) and time.time() - _дом["последний_запрос"] < ДОМ_МОЛЧИТ_С


def _вебхук(ids, k):
    try:
        req = urllib.request.Request(ВЕБХУК, method="POST", headers={"Content-Type": "application/json"},
                                     data=json.dumps({"id": ",".join(ids) if isinstance(ids, list) else ids, "k": k,
                                                      "n": str(int(time.time() * 1000) % 10**9)}).encode("utf-8"))
        urllib.request.urlopen(req, timeout=5).read()
    except Exception as e:
        log(f"запасной канал: вебхук не принят ({e!r})")


def баллон(текст):
    def f():
        try:
            urllib.request.urlopen(urllib.request.Request(БАЛЛОН, method="POST", headers={"Content-Type": "application/json"},
                                   data=json.dumps({"text": текст}, ensure_ascii=False).encode("utf-8")), timeout=1).read()
        except Exception:
            pass
    threading.Thread(target=f, daemon=True).start()


def событие(вид, **поля):
    if not канал_жив() and вид in ("хочу", "темп"):      # шаг 5: дом не на связи — заявка через вебхук HA
        if вид == "хочу":
            threading.Thread(target=_вебхук, args=(поля.get("ids", [])[:3], "req"), daemon=True).start()
        else:
            threading.Thread(target=_вебхук, args=(поля.get("id", ""), "%.2f" % поля.get("k", 1.0)), daemon=True).start()
    with _зам:
        _n[0] += 1
        _события.append({"n": _n[0], "вид": вид, "t": time.time(), **поля})
        del _события[:-СОБЫТИЙ_ДЕРЖАТЬ]
        _зам.notify_all()
        return _n[0]


def режим_новый():
    try:
        with open(РЕЖИМ_ФАЙЛ, encoding="utf-8") as f:
            return f.read().strip().lower().startswith("нов")
    except OSError:
        return False


def автоозвучка():
    try:
        with open(АВТО_ФАЙЛ, encoding="utf-8") as f:
            return f.read().strip().lower().startswith("вкл")
    except OSError:
        return False


# ---- лента v2 → плоский список предложений по ord ----
_лента = {"turns": []}
_список = []          # [{id, ord, turn, вид, msg, text, wav}]


def лента_обновить(д):
    global _список
    сп = []
    for t in д.get("turns") or []:
        for m in t.get("msgs") or []:
            for sn in m.get("sents") or []:
                сп.append({"id": sn["id"], "ord": sn["ord"], "turn": t.get("turn"), "вид": t.get("вид", ""), "msg": m.get("msg"),
                           "text": sn.get("text", ""), "wav": sn.get("wav")})
    сп.sort(key=lambda x: x["ord"])
    with _зам:
        _лента.clear(); _лента.update(д)
        _список = сп
        if П["слышано"] is None:              # первая лента после запуска — старое не играем
            П["слышано"] = max([x["ord"] for x in сп] or [0])
        _зам.notify_all()


def _найти(idx):
    for i, x in enumerate(_список):
        if x["id"] == idx:
            return i
    return -1


# ---- состояние плеера ----
П = {"темп": 1.0, "слышано": None, "указатель": None, "ручной": False, "играет": None, "пауза": False, "ждёт": None, "ждёт_с": 0.0,
     "сообщ_старт": 0.0, "команды": []}
_звук_кэш = {}


def _out_device(sd):
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


class Звук:
    """WAV в памяти → OutputStream с колбэком; пауза, позиция (как _SdPlayer в dictate.py)."""

    def __init__(self, data):
        import numpy as np, sounddevice as sd
        with wave.open(io.BytesIO(data)) as w:
            self.rate, ch = w.getframerate(), w.getnchannels()
            raw = w.readframes(w.getnframes())
        self.a = np.frombuffer(raw, dtype="<i2").reshape(-1, ch)
        self.ch, self.n, self.pos = ch, len(self.a), 0
        self.paused = False
        self.lock = threading.Lock()
        self.end_t = None
        self.stream = sd.OutputStream(samplerate=self.rate, channels=ch, dtype="int16", device=_out_device(sd), callback=self._cb)
        self.lat = float(self.stream.latency or 0.2)
        self.stream.start()

    def _cb(self, outdata, frames, tinfo, status):
        if self.paused:
            outdata[:] = 0
            return
        with self.lock:
            p = self.pos; e = min(p + frames, self.n); k = e - p
            if k > 0:
                outdata[:k] = self.a[p:e]
            if k < frames:
                outdata[max(k, 0):] = 0
            self.pos = max(e, p)

    def готово(self):
        try:
            if not self.stream.active:
                return True
        except Exception:
            return True
        if self.paused or self.pos < self.n:
            self.end_t = None
            return False
        if self.end_t is None:
            self.end_t = time.time()
        return time.time() - self.end_t > self.lat + 0.1

    def слышно_с(self):
        return max(0.0, self.pos / self.rate - self.lat)

    def закрыть(self):
        try:
            self.stream.abort(); self.stream.close()
        except Exception:
            pass


def _вариант(x, k):
    """WAV варианта темпа k (дом пишет <id>_x<k>.wav по событию «темп»); нет ещё — None."""
    ключ = (x["id"], k)
    if ключ in _звук_кэш:
        return _звук_кэш[ключ]
    try:
        with urllib.request.urlopen(ЗВУК_URL + f"{x['id']}_x{k:.2f}.wav", timeout=5) as r:
            d = r.read()
    except Exception:
        return None
    _звук_кэш[ключ] = d
    return d


def _звук_взять(x):
    """WAV предложения: из кэша или с /local/ Штаба; нет в ленте — None. При темпе ≠ 1 — вариант, если готов (иначе обычный)."""
    k = П["темп"]
    if k != 1.0 and x.get("wav"):
        d = _вариант(x, k)
        if d is not None:
            return d
        событие("темп", id=x["id"], k=k)
    if x["id"] in _звук_кэш:
        return _звук_кэш[x["id"]]
    if not x.get("wav"):
        return None
    with urllib.request.urlopen(ЗВУК_URL + x["wav"], timeout=10) as r:
        d = r.read()
    _звук_кэш[x["id"]] = d
    while len(_звук_кэш) > 400:
        _звук_кэш.pop(next(iter(_звук_кэш)))
    return d


def _подходит(x, ручной):
    if ручной:
        return True
    return x["вид"] == "голос" or (x["вид"] in ("печать", "фон") and автоозвучка())


def _часы(x, флаг, число=0):
    событие("часы", id=x["id"], флаг=флаг, число=int(число))


def _хочу_вокруг(i):
    ids = [_список[j]["id"] for j in range(i, min(i + 3, len(_список))) if not _список[j].get("wav")]
    if ids:
        событие("хочу", ids=ids)
    if П["темп"] != 1.0:
        for j in range(i, min(i + 3, len(_список))):
            if _список[j].get("wav") and (_список[j]["id"], П["темп"]) not in _звук_кэш:
                событие("темп", id=_список[j]["id"], k=П["темп"])
    for j in range(i + 1, min(i + 3, len(_список))):      # упреждение загрузки готового
        x = _список[j]
        if x.get("wav") and x["id"] not in _звук_кэш:
            threading.Thread(target=lambda x=x: (_звук_взять(x) if True else None), daemon=True).start()


def _начало_сообщения(i):
    while i > 0 and (_список[i - 1]["turn"], _список[i - 1]["msg"]) == (_список[i]["turn"], _список[i]["msg"]):
        i -= 1
    return i


def _стоп_звук():
    з = П["играет"]
    if з is not None:
        з[1].закрыть()
        _часы(з[0], "s")
    П["играет"] = None


def команда(к):
    """Клавиши от dictate.py: stop, pause, back, nextmsg, prevs, next, again, последнее; темп — шаг 5."""
    with _зам:
        сп = _список
        играет = П["играет"]
        i = _найти(играет[0]["id"]) if играет else (_найти(П["указатель"]) if П["указатель"] else -1)
        log(f"команда {к} (играет={'да' if играет else 'нет'})")
        if к == "stop":
            _стоп_звук(); П["пауза"] = False; П["ждёт"] = None; П["ручной"] = False; П["указатель"] = None
            П["слышано"] = max([x["ord"] for x in сп] or [0])
            баллон("Озвучка прервана")
        elif к == "pause":
            if играет:
                П["пауза"] = not П["пауза"]; играет[1].paused = П["пауза"]
                баллон("Пауза" if П["пауза"] else "Продолжаю")
                _часы(играет[0], "p" if П["пауза"] else "r",
                      играет[1].слышно_с() * 1000 if П["пауза"] else (time.time() - играет[1].слышно_с()) * 1000)
        elif к in ("back", "последнее"):
            if к == "back" and играет and i >= 0 and time.time() - П["сообщ_старт"] > НАЗАД_ОКНО_С:
                j = _начало_сообщения(i)                  # к началу текущего сообщения
            elif i >= 0 and играет:
                j = _начало_сообщения(_начало_сообщения(i) - 1) if _начало_сообщения(i) > 0 else 0
            else:                                         # в тишине — последнее сообщение с начала
                j = _начало_сообщения(len(сп) - 1) if сп else -1
            if j >= 0:
                _стоп_звук(); П["указатель"] = сп[j]["id"]; П["ручной"] = True; П["пауза"] = False
                баллон("Назад: " + " ".join(сп[j]["text"].split()[:5]))
        elif к == "nextmsg" and играет and i >= 0:
            j = i
            while j < len(сп) and (сп[j]["turn"], сп[j]["msg"]) == (сп[i]["turn"], сп[i]["msg"]):
                j += 1
            _стоп_звук()
            баллон("Следующее сообщение")
            П["слышано"] = max(П["слышано"] or 0, сп[j - 1]["ord"])
            П["указатель"] = сп[j]["id"] if j < len(сп) else None
        elif к in ("prevs", "next", "again") and играет and i >= 0:
            j = {"prevs": i - 1, "next": i + 1, "again": i}[к]
            if 0 <= j < len(сп):
                _стоп_звук(); П["указатель"] = сп[j]["id"]; П["ручной"] = True
        elif к in ("slower", "faster", "reset"):
            ст = П["темп"]
            n = ТЕМПЫ.index(ст) if ст in ТЕМПЫ else 3
            П["темп"] = 1.0 if к == "reset" else ТЕМПЫ[max(0, n - 1)] if к == "slower" else ТЕМПЫ[min(len(ТЕМПЫ) - 1, n + 1)]
            баллон(f"Темп {П['темп']:.2f}".replace(".", ",").replace(",00", ""))
            log(f"темп {ст} → {П['темп']}")
            if играет and П["темп"] != ст and i >= 0:   # текущую фразу — заново в новом темпе
                if П["темп"] != 1.0:
                    событие("темп", id=сп[i]["id"], k=П["темп"])
                _стоп_звук(); П["указатель"] = сп[i]["id"]
        _зам.notify_all()


def цикл_игры():
    """Автомат плеера: указатель → следующая фраза; нет указателя — первая неслышанная подходящая."""
    while True:
        try:
            if not режим_новый():
                time.sleep(1); continue
            with _зам:
                з = П["играет"]
                if з is not None:
                    if not з[1].готово():
                        pass
                    else:
                        x = з[0]; з[1].закрыть(); П["играет"] = None
                        П["слышано"] = max(П["слышано"] or 0, x["ord"])
                        событие("сыграно", ход=x["turn"], msg=x["msg"], text=x["text"][:200])
                        i = _найти(x["id"])
                        nxt = _список[i + 1] if 0 <= i < len(_список) - 1 else None
                        П["указатель"] = nxt["id"] if nxt and _подходит(nxt, П["ручной"]) else None
                        if П["указатель"] is None:
                            П["ручной"] = False
                            _часы(x, "s")
                    кандидат = None
                else:
                    кандидат = None
                    if П["указатель"]:
                        i = _найти(П["указатель"])
                        кандидат = _список[i] if i >= 0 else None
                    if кандидат is None and П["слышано"] is not None:
                        кандидат = next((x for x in _список if x["ord"] > П["слышано"] and _подходит(x, False)), None)
            if кандидат is not None and П["играет"] is None:
                i = _найти(кандидат["id"])
                _хочу_вокруг(i)
                try:
                    data = _звук_взять(кандидат)
                except Exception as e:
                    log(f"звук {кандидат['id']} не получен: {e!r}"); data = None
                if data is None:
                    if П["ждёт"] != кандидат["id"]:
                        П["ждёт"], П["ждёт_с"] = кандидат["id"], time.time()
                    elif time.time() - П["ждёт_с"] > ЖДАТЬ_ЗВУК_С:
                        log(f"звук {кандидат['id']} не дождалась за {ЖДАТЬ_ЗВУК_С:.0f} с — дальше")
                        with _зам:
                            П["слышано"] = max(П["слышано"] or 0, кандидат["ord"]); П["ждёт"] = None
                            П["указатель"] = _список[i + 1]["id"] if 0 <= i < len(_список) - 1 else None
                    with _зам:
                        _зам.wait(0.5)
                    continue
                П["ждёт"] = None
                with _зам:
                    if П["указатель"] not in (None, кандидат["id"]) and П["указатель"] != кандидат["id"]:
                        continue                     # пока грузили, пришла команда
                    if _начало_сообщения(i) == i or not П["сообщ_старт"]:
                        П["сообщ_старт"] = time.time()
                    з = Звук(data)
                    з.paused = П["пауза"]
                    П["играет"] = (кандидат, з); П["указатель"] = кандидат["id"]
                    _часы(кандидат, "r", (time.time() + з.lat) * 1000)
                    log(f"играю {кандидат['вид']} {кандидат['id']}: {кандидат['text'][:70]}")
                continue
            with _зам:
                _зам.wait(0.1)
        except Exception:
            log("цикл игры: " + traceback.format_exc()[-600:])
            time.sleep(1)


def запасной_опрос():
    """Шаг 5: дом не держит длинный запрос > ДОМ_МОЛЧИТ_С — лента v2 опросом с /local/ раз в 3 с."""
    было = None
    while True:
        time.sleep(3)
        if канал_жив():
            было = None
            continue
        try:
            with urllib.request.urlopen(ЗВУК_URL + "lenta.json?_=%d" % time.time(), timeout=5) as r:
                тело = r.read()
            д = json.loads(тело.decode("utf-8"))
            if д.get("ts") != было:
                было = д.get("ts")
                лента_обновить(д)
                if _дом["лента_ts"] != было:
                    log("запасной канал: ленту взяла опросом /local/")
                    _дом["лента_ts"] = было
        except Exception as e:
            log(f"запасной канал: лента не прочитана ({e!r})")


def флаг_активна():
    while True:
        try:
            if П["играет"] or П["ждёт"] or П["пауза"]:
                with open(АКТИВНА, "w") as f:
                    f.write(str(time.time()))
        except Exception:
            pass
        time.sleep(0.3)


def _токен():
    try:
        with open(ТОКЕН, encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def setup(self):
        super().setup()
        try:
            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass

    def _ответ(self, code, obj):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _свой(self):
        try:
            return any(ipaddress.ip_address(self.client_address[0]) in s for s in СЕТИ)
        except ValueError:
            return False

    def _с_токеном(self):
        т = _токен()
        return bool(т) and hmac.compare_digest(self.headers.get("X-Token", ""), т)

    def _тело(self, предел):
        n = int(self.headers.get("Content-Length", "0") or 0)
        return self.rfile.read(min(n, предел)) if n else b""

    def do_GET(self):
        u = urlparse(self.path)
        if not self._свой() or not self._с_токеном():
            return self._ответ(403, {"ok": False})
        if u.path == "/zayavki":
            после = int((parse_qs(u.query).get("posle") or ["0"])[0] or 0)
            _дом["последний_запрос"] = time.time()
            до = time.time() + ДЛИННЫЙ_С
            with _зам:
                if после > _n[0]:           # плеер перезапущен — номера начались заново: отдать всё
                    после = 0
                while not any(e["n"] > после for e in _события) and time.time() < до:
                    _зам.wait(max(0.1, до - time.time()))
                новые = [e for e in _события if e["n"] > после]
                return self._ответ(200, {"ok": True, "события": новые, "n": _n[0]})
        if u.path == "/sostoyanie":
            return self._ответ(200, {"ok": True, "n": _n[0], **_дом})
        self._ответ(404, {"ok": False})

    def do_POST(self):
        u = urlparse(self.path)
        if u.path == "/komanda":              # только с этой машины, без токена
            if self.client_address[0] != "127.0.0.1":
                return self._ответ(403, {"ok": False})
            try:
                д = json.loads(self._тело(100000).decode("utf-8"))
                if д.get("вид") == "клавиша":
                    threading.Thread(target=команда, args=(д.get("к", ""),), daemon=True).start()
                    return self._ответ(200, {"ok": True})
                n = событие(д.pop("вид", "?"), **д)
                return self._ответ(200, {"ok": True, "n": n})
            except Exception as e:
                return self._ответ(400, {"ok": False, "ошибка": repr(e)})
        if not self._свой() or not self._с_токеном():
            log(f"чужой запрос {u.path} с {self.client_address[0]}")
            return self._ответ(403, {"ok": False})
        if u.path == "/lenta":
            тело = self._тело(5_000_000)
            try:
                д = json.loads(тело.decode("utf-8"))
                tmp = ЛЕНТА + ".tmp"
                with open(tmp, "wb") as f:
                    f.write(тело)
                os.replace(tmp, ЛЕНТА)
                лента_обновить(д)
                _дом["лента_ts"] = д.get("ts", 0); _дом["ходов"] = len(д.get("turns") or [])
                return self._ответ(200, {"ok": True})
            except Exception as e:
                return self._ответ(400, {"ok": False, "ошибка": repr(e)})
        if u.path.startswith("/zvuk/"):
            idx = u.path[len("/zvuk/"):]
            if not idx.replace("_", "").isdigit():
                return self._ответ(400, {"ok": False})
            тело = self._тело(50_000_000)
            os.makedirs(ЗВУК, exist_ok=True)
            with open(os.path.join(ЗВУК, idx + ".wav"), "wb") as f:
                f.write(тело)
            _дом["звуков"] += 1
            return self._ответ(200, {"ok": True})
        self._ответ(404, {"ok": False})

    def log_message(self, *a):
        pass


def пульс():
    while True:
        try:
            tmp = ПУЛЬС + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "шаг": 5, "темп": П["темп"], "канал": "есть" if канал_жив() else "запасной", "режим": "новый" if режим_новый() else "старый", "играет": bool(П["играет"]), "событий": _n[0],
                           "дом_был_с_назад": round(time.time() - _дом["последний_запрос"], 1) if _дом["последний_запрос"] else None,
                           "ходов_в_ленте": _дом["ходов"], "звуков": _дом["звуков"]}, f, ensure_ascii=False)
            os.replace(tmp, ПУЛЬС)
        except Exception:
            pass
        time.sleep(30)


def main():
    try:
        srv = ThreadingHTTPServer(("0.0.0.0", ПОРТ), H)
    except OSError as e:
        log(f"порт {ПОРТ} занят ({e!r}) — второй экземпляр не запускаю")
        return 1
    srv.daemon_threads = True
    threading.Thread(target=пульс, daemon=True).start()
    try:                                     # лента с диска — до первой присылки дома
        лента_обновить(json.load(open(ЛЕНТА, encoding="utf-8")))
    except Exception:
        pass
    threading.Thread(target=цикл_игры, daemon=True).start()
    threading.Thread(target=флаг_активна, daemon=True).start()
    threading.Thread(target=запасной_опрос, daemon=True).start()
    log(f"плеер (шаг 5): слушаю :{ПОРТ}, режим {'НОВЫЙ — играю сам' if режим_новый() else 'старый — играет dictate.py'}")
    try:
        srv.serve_forever()
    except Exception:
        log("сервер упал: " + traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(main())
