# Консоль AirTies по UART на XPS (06.11.2026; раньше — Коин ~/airties_uart/авто.py). Ждёт переходник CP210x/CH340,
# пишет всё в W:\Tools\airties_uart\консоль.log; команды — строками в W:\Tools\airties_uart\ввод (^C = Ctrl+C,
# «!скорость N» — сменить скорость). Ядро и CFE AirTies — 115200 8N1 (getty днём 30.10 отвечал на 9600 — «!скорость 9600»).
import os, threading, time
import serial
from serial.tools import list_ports

К = r"W:\Tools\airties_uart"
os.makedirs(К, exist_ok=True)
ВВОД = os.path.join(К, "ввод")
class Два:
    """Журнал сразу в два места: локально и в папку обмена Кузницы (O:\\airties) — оттуда Штаб транслирует его в Студию
    (/config/.local/превью/airties/консоль_живая.txt, как 30.10 с Коина)."""
    def __init__(self, *пути):
        self.ф = []
        for п in пути:
            try:
                os.makedirs(os.path.dirname(п), exist_ok=True); self.ф.append(open(п, "ab", buffering=0))
            except OSError:
                pass
    def write(self, b):
        for ф in self.ф:
            try:
                ф.write(b)
            except OSError:
                pass


f = Два(os.path.join(К, "консоль.log"))   # хозяин 06.11: без обходов через Кузницу — смотрит окном на самом XPS
open(ВВОД, "w").close()
с = None
скорость = 115200


def метка(t):
    f.write(f"\n=== {time.strftime('%d.%m %H:%M:%S')} {t} ===\n".encode())


def найти_порт():
    for p in list_ports.comports():
        if any(x in (p.description or "") + (p.manufacturer or "") for x in ("CP210", "Silicon Labs", "CH340", "USB-SERIAL", "UART")):
            return p.device
    return None


def открыть():
    global с
    while True:
        порт = найти_порт()
        if порт:
            try:
                с = serial.Serial(порт, скорость, timeout=0.3)
                метка(f"модуль на связи: {порт}, {скорость}")
                return
            except serial.SerialException:
                pass
        time.sleep(1)


def писать():
    global скорость
    поз = 0
    while True:
        time.sleep(0.3)
        try:
            д = open(ВВОД, "rb").read()
        except OSError:
            continue
        if len(д) > поз and д.endswith(b"\n"):
            for стр in д[поз:].split(b"\n")[:-1]:
                стр = стр.rstrip(b"\r")
                try:
                    if стр.startswith("!скорость ".encode()):
                        скорость = int(стр.split()[1]); с.baudrate = скорость; метка(f"скорость {скорость} (вручную)"); continue
                    с.write(b"\x03" if стр == b"^C" else стр + b"\r"); f.write(b"\n>>> " + стр + b"\n"); time.sleep(0.2)
                except Exception as e:
                    метка(f"ввод не ушёл: {e!r}")
            поз = len(д)


метка("служба запущена, жду переходник")
открыть()
threading.Thread(target=писать, daemon=True).start()
while True:
    try:
        b = с.read(4096)
    except (OSError, serial.SerialException):
        try:
            с.close()
        except Exception:
            pass
        метка("модуль пропал — жду"); time.sleep(1); открыть(); continue
    if b:
        f.write(b)
