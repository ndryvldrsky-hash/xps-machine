# -*- coding: utf-8 -*-
# Раскладка: свой индикатор языка клавиатуры для XPS (11.11.2026).
#
# Зачем. Штатная плавающая языковая панель Windows светлая, её не найти глазами, и она
# прыгает при смене рабочих столов. Хозяин: «чтоб можно было увидеть, что там за ней»
# (про полупрозрачность плашки) и попросил сразу три вида индикации:
#   1. Плашка над кнопкой «Пуск» на КАЖДОМ мониторе (полупрозрачная, альфа ~0.7);
#   2. Маленькая цветная буква у текстовой каретки (нет каретки - у указателя мыши);
#   3. Вспышка крупными буквами по центру монитора активного окна при смене раскладки.
# Цвета: RU красный #D32F2F, EN синий #1565C0, HE зелёный #2E7D32, остальное - код серым.
#
# Как работает. Раскладка опрашивается раз в ~100 мс (GetKeyboardLayout потока активного окна).
# НИКАКИХ глобальных хуков клавиатуры - их держит диктовка dictate.py, ей мешать нельзя.
# Все окна: поверх всех (утверждается раз в секунду - видно и в F11), без значка в панели
# задач, не берут фокус, щелчки проходят сквозь (WS_EX_LAYERED|TRANSPARENT|NOACTIVATE|TOOLWINDOW).
# Положение «Пуск» - UIA (AutomationId StartButton в Shell_TrayWnd / Shell_SecondaryTrayWnd),
# перечитывается раз в ~10 с; нет UIA - запасной расчёт по прямоугольнику панели задач;
# нет и его - последнее известное положение (панель скрыта).
# Один экземпляр - мьютекс. Журнал ошибок - raskladka.log рядом со скриптом.
# Только стандартная библиотека + comtypes (он уже стоит, нужен для UIA).

import ctypes
import os
import sys
import threading
import time
import traceback
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "raskladka.log")


def log(msg):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except Exception:
        pass


user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
user32.GetKeyboardLayout.restype = ctypes.c_void_p
user32.GetForegroundWindow.restype = ctypes.c_void_p
user32.GetAncestor.restype = ctypes.c_void_p
user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_uint]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
user32.GetParent.restype = ctypes.c_void_p
user32.GetParent.argtypes = [ctypes.c_void_p]
user32.MonitorFromWindow.argtypes = [ctypes.c_void_p, wintypes.DWORD]
user32.MonitorFromWindow.restype = ctypes.c_void_p
user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass

GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x8
WS_EX_TRANSPARENT = 0x20
WS_EX_TOOLWINDOW = 0x80
WS_EX_LAYERED = 0x80000
WS_EX_NOACTIVATE = 0x08000000
EX_ADD = WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST
HWND_TOPMOST = -1
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x1, 0x2, 0x10, 0x40

COLORS = {0x0419: "#D32F2F", 0x0409: "#1565C0", 0x040D: "#2E7D32"}
NAMES = {0x0419: "RU", 0x0409: "EN", 0x040D: "HE"}
GRAY = "#6E6E6E"


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("hwndActive", ctypes.c_void_p), ("hwndFocus", ctypes.c_void_p),
                ("hwndCapture", ctypes.c_void_p), ("hwndMenuOwner", ctypes.c_void_p),
                ("hwndMoveSize", ctypes.c_void_p), ("hwndCaret", ctypes.c_void_p),
                ("rcCaret", RECT)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT), ("rcWork", RECT),
                ("dwFlags", wintypes.DWORD)]


def current_layout():
    """(lang_id, hwnd активного окна)"""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None, None
    tid = user32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), None)
    hkl = user32.GetKeyboardLayout(tid) or 0
    return hkl & 0xFFFF, hwnd


def monitors():
    """Список прямоугольников мониторов (l, t, r, b) в физических пикселях."""
    res = []
    CB = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
                            ctypes.POINTER(RECT), ctypes.c_void_p)

    def cb(hm, hdc, rc, lp):
        r = rc.contents
        res.append((r.left, r.top, r.right, r.bottom))
        return 1
    user32.EnumDisplayMonitors(None, None, CB(cb), None)
    return res


def monitor_of_point(mons, x, y):
    for m in mons:
        if m[0] <= x < m[2] and m[1] <= y < m[3]:
            return m
    return None


def tray_windows():
    """hwnd всех Shell_TrayWnd и Shell_SecondaryTrayWnd."""
    res = []
    CB = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
    buf = ctypes.create_unicode_buffer(64)

    def cb(h, lp):
        user32.GetClassNameW(ctypes.c_void_p(h), buf, 64)
        if buf.value in ("Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
            res.append(h)
        return 1
    user32.EnumWindows(CB(cb), None)
    return res


# ---------- положение «Пуск» (UIA в отдельном потоке) ----------
START = {}      # монитор -> (l, t, r, b) кнопки «Пуск» (последнее известное)
START_SRC = {}  # монитор -> "uia" | "tray" | "cache"


def start_worker():
    uia = None
    try:
        try:
            import comtypes
        except ImportError:
            # comtypes стоит в пользовательском site-packages учётки alena, не rdpuser
            sys.path.append(r"W:\Users\alena\AppData\Roaming\Python\Python312\site-packages")
            import comtypes
        import comtypes.client
        comtypes.CoInitialize()
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as UIA
        uia = comtypes.CoCreateInstance(UIA.CUIAutomation._reg_clsid_, interface=UIA.IUIAutomation,
                                        clsctx=comtypes.CLSCTX_INPROC_SERVER)
        cond = uia.CreatePropertyCondition(UIA.UIA_AutomationIdPropertyId, "StartButton")
    except Exception:
        log("UIA не поднялся, запасной расчёт: " + traceback.format_exc())
        uia = None
    while True:
        try:
            mons = monitors()
            found = {}
            for h in tray_windows():
                rc = RECT()
                user32.GetWindowRect(ctypes.c_void_p(h), ctypes.byref(rc))
                got = None
                if uia is not None:
                    try:
                        el = uia.ElementFromHandle(ctypes.c_void_p(h))
                        b = el.FindFirst(UIA.TreeScope_Descendants, cond)
                        if b is not None:
                            r = b.CurrentBoundingRectangle
                            if r.right - r.left > 4 and r.bottom - r.top > 4:
                                got = (r.left, r.top, r.right, r.bottom)
                    except Exception:
                        got = None
                m = monitor_of_point(mons, (rc.left + rc.right) // 2, (rc.top + rc.bottom) // 2)
                src = "uia"
                if got is not None:
                    mm = monitor_of_point(mons, (got[0] + got[2]) // 2, (got[1] + got[3]) // 2)
                    if mm is None:
                        got = None   # кнопка вне экранов: панель спрятана
                    else:
                        m = mm
                if got is None and m is not None:
                    # запасной расчёт: Пуск Win11 стоит по центру панели, квадрат по высоте панели
                    th = rc.bottom - rc.top
                    tw = rc.right - rc.left
                    if th > 8 and tw > th and monitor_of_point(mons, rc.left + 1, rc.top + 1):
                        s = max(th - 8, 20)
                        cx = (rc.left + rc.right) // 2 - s  # левее центра: Пуск рядом с центром
                        got = (cx - s // 2, rc.top + 4, cx + s // 2, rc.top + 4 + s)
                        src = "tray"
                if got is not None and m is not None:
                    found[m] = (got, src)
            for m, (g, src) in found.items():
                START[m] = g
                START_SRC[m] = src
        except Exception:
            log("start_worker: " + traceback.format_exc())
        time.sleep(10)


# ---------- окна Tk ----------
import tkinter as tk
import tkinter.font as tkfont


def make_overlay(root, text="", font_px=14, alpha=0.7):
    w = tk.Toplevel(root)
    w.overrideredirect(True)
    w.configure(bg=GRAY)
    lbl = tk.Label(w, text=text, fg="white", bg=GRAY, bd=0, padx=0, pady=0)
    lbl.pack(fill="both", expand=True)
    lbl.configure(font=("Segoe UI", -font_px, "bold"))
    w.attributes("-alpha", alpha)
    w.geometry("10x10+-5000+-5000")
    w.update_idletasks()
    o = {"w": w, "lbl": lbl, "font_px": font_px, "alpha": alpha, "hwnd": None, "pos": None}
    return o


def hwnd_of(o):
    if o["hwnd"] is None:
        wid = o["w"].winfo_id()
        o["hwnd"] = user32.GetParent(wid) or wid
    return o["hwnd"]


def apply_styles(o):
    h = hwnd_of(o)
    cur = user32.GetWindowLongPtrW(h, GWL_EXSTYLE)
    if cur & EX_ADD != EX_ADD:
        user32.SetWindowLongPtrW(h, GWL_EXSTYLE, cur | EX_ADD)


def place(o, x, y, w, h):
    x, y, w, h = int(x), int(y), max(int(w), 4), max(int(h), 4)
    apply_styles(o)
    user32.SetWindowPos(hwnd_of(o), ctypes.c_void_p(HWND_TOPMOST), x, y, w, h,
                        SWP_NOACTIVATE | SWP_SHOWWINDOW)
    o["pos"] = (x, y, w, h)


def move_only(o, x, y):
    user32.SetWindowPos(hwnd_of(o), ctypes.c_void_p(HWND_TOPMOST), int(x), int(y), 0, 0,
                        SWP_NOACTIVATE | SWP_NOSIZE | SWP_SHOWWINDOW)


def reassert(o):
    apply_styles(o)
    user32.SetWindowPos(hwnd_of(o), ctypes.c_void_p(HWND_TOPMOST), 0, 0, 0, 0,
                        SWP_NOACTIVATE | SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)


def paint(o, text, color):
    if o["lbl"].cget("text") != text:
        o["lbl"].configure(text=text)
    if o["lbl"].cget("bg") != color:
        o["lbl"].configure(bg=color)
        o["w"].configure(bg=color)


def set_font(o, px):
    px = max(int(px), 6)
    if o["font_px"] != px:
        o["font_px"] = px
        o["lbl"].configure(font=("Segoe UI", -px, "bold"))


def label_for(lid):
    if lid is None:
        return "?", GRAY
    if lid in NAMES:
        return NAMES[lid], COLORS[lid]
    return "%04X" % lid, GRAY


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.plates = {}       # монитор -> overlay
        self.mons = monitors()
        self.caret = make_overlay(self.root, "", 14, 0.75)
        self.flash = make_overlay(self.root, "", 90, 0.0)
        self.flash_t0 = None
        self.flash_mon = None
        self.lid = None
        self.cx = self.cy = None
        self.t_layout = self.t_top = self.t_mon = 0
        self.hwnd = None
        place(self.caret, -5000, -5000, 22, 22)
        place(self.flash, -5000, -5000, 260, 150)
        self.flash["w"].attributes("-alpha", 0.0)
        self.tick()

    # --- плашки над «Пуск» ---
    def update_plates(self):
        mons = self.mons
        for m in list(self.plates):
            if m not in mons:
                try:
                    self.plates[m]["w"].destroy()
                except Exception:
                    pass
                del self.plates[m]
        text, color = label_for(self.lid)
        for m in mons:
            o = self.plates.get(m)
            if o is None:
                o = self.plates[m] = make_overlay(self.root, text, 14, 0.7)
            rect = START.get(m)
            if rect is None:
                # Пока UIA не ответил: примерно по центру низа монитора (Win11)
                s = 40
                cx = (m[0] + m[2]) // 2 - s
                rect = (cx - s // 2, m[3] - 48, cx + s // 2, m[3] - 8)
            l, t, r, b = rect
            w = max(r - l, 30)
            h = max(int(w * 0.5), 20)
            # панель наверху экрана (середина кнопки в верхней половине) - плашка под кнопкой
            if (t + b) // 2 < (m[1] + m[3]) // 2:
                y = b + 2
            else:
                y = t - h - 2
            paint(o, text, color)
            set_font(o, h * 0.62)
            nxt = (l, y, w, h)
            if o["pos"] != nxt:
                place(o, *nxt)

    # --- метка у каретки / мыши ---
    def follow(self):
        tx = ty = None
        hwnd = user32.GetForegroundWindow()
        if hwnd:
            tid = user32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), None)
            gi = GUITHREADINFO()
            gi.cbSize = ctypes.sizeof(gi)
            if user32.GetGUIThreadInfo(tid, ctypes.byref(gi)) and gi.hwndCaret:
                rc = gi.rcCaret
                if rc.right > rc.left or rc.bottom > rc.top:
                    pt = POINT(rc.left, rc.bottom)
                    if user32.ClientToScreen(ctypes.c_void_p(gi.hwndCaret), ctypes.byref(pt)):
                        if pt.x or pt.y:
                            tx, ty = pt.x + 4, pt.y + 2
        if tx is None:
            pt = POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            tx, ty = pt.x + 18, pt.y + 18
        if self.cx is None or abs(tx - self.cx) > 600 or abs(ty - self.cy) > 600:
            self.cx, self.cy = tx, ty
        else:
            self.cx += (tx - self.cx) * 0.4
            self.cy += (ty - self.cy) * 0.4
        move_only(self.caret, self.cx, self.cy)

    # --- вспышка ---
    def start_flash(self, hwnd):
        text, color = label_for(self.lid)
        mon = None
        if hwnd:
            hm = user32.MonitorFromWindow(ctypes.c_void_p(hwnd), 2)
            mi = MONITORINFO()
            mi.cbSize = ctypes.sizeof(mi)
            if hm and user32.GetMonitorInfoW(ctypes.c_void_p(hm), ctypes.byref(mi)):
                r = mi.rcMonitor
                mon = (r.left, r.top, r.right, r.bottom)
        if mon is None:
            mon = self.mons[0] if self.mons else (0, 0, 1024, 768)
        w, h = 260, 150
        paint(self.flash, text, color)
        set_font(self.flash, 90)
        place(self.flash, (mon[0] + mon[2]) // 2 - w // 2, (mon[1] + mon[3]) // 2 - h // 2, w, h)
        self.flash["w"].attributes("-alpha", 0.9)
        self.flash_t0 = time.time()

    def step_flash(self):
        if self.flash_t0 is None:
            return
        dt = time.time() - self.flash_t0
        if dt < 1.0:
            a = 0.9
        elif dt < 1.6:
            a = 0.9 * (1.0 - (dt - 1.0) / 0.6)
        else:
            a = 0.0
            self.flash_t0 = None
            place(self.flash, -5000, -5000, 260, 150)
        self.flash["w"].attributes("-alpha", a)

    def tick(self):
        try:
            now = time.time()
            if now - self.t_layout >= 0.1:
                self.t_layout = now
                lid, hwnd = current_layout()
                if lid is not None and lid != self.lid:
                    first = self.lid is None
                    self.lid = lid
                    self.hwnd = hwnd
                    text, color = label_for(lid)
                    paint(self.caret, text, color)
                    if not first:
                        self.start_flash(hwnd)
                    self.update_plates()
            if now - self.t_mon >= 5:
                self.t_mon = now
                self.mons = monitors()
                self.update_plates()
            if now - self.t_top >= 1.0:
                self.t_top = now
                self.update_plates()
                reassert(self.caret)
                if self.flash_t0 is not None:
                    reassert(self.flash)
                for o in self.plates.values():
                    reassert(o)
            self.follow()
            self.step_flash()
        except Exception:
            log("tick: " + traceback.format_exc())
        self.root.after(25, self.tick)


def main():
    mutex = kernel32.CreateMutexW(None, False, "Local\\AlenaRaskladka")
    if kernel32.GetLastError() == 183:
        return
    threading.Thread(target=start_worker, daemon=True).start()
    app = App()
    app.root.mainloop()
    _ = mutex


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log("FATAL: " + traceback.format_exc())
        sys.exit(1)
