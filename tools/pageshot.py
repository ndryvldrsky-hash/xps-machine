# -*- coding: utf-8 -*-
"""Снимок веб-страницы целиком фоновым Edge через DevTools Protocol — без окна и без DevTools (2026-09-26).

Зачем: витрины HA прокручиваются во внутреннем контейнере — «Веб-снимок всей страницы» Edge видит только экран,
открытые DevTools съедают ширину и меняют вёрстку, а `msedge --screenshot` снимает заставку «Загрузка…» (не ждёт
приложение). Здесь: фоновый Edge с отладочным портом, ждём, пока HA подключится и дорисует карточки, находим высоту
прокручиваемого контента (обход всех shadow DOM), растягиваем окно под неё и снимаем Page.captureScreenshot.

Авторизация HA: во временный профиль копируется только «Local Storage» из профиля Edge rdpuser (там токен HA);
временный профиль удаляется после снимка.

  python pageshot.py <url> [--width 1280] [--height 0 (0 — по содержимому)] [--out файл.png] [--wait 25]
Печатает JSON: {"ok", "out", "width", "height", "bytes"}. Вызывается MCP-инструментом xps_pageshot.
"""
import argparse
import asyncio
import base64
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request
import uuid

import websockets

EDGE = r"W:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
LOCAL_STORAGE = r"W:\Users\rdpuser\AppData\Local\Microsoft\Edge\User Data\Default\Local Storage"
PORT = 9333

# готовность HA: соединение есть и панель отрисована (для не-HA страниц — просто document.readyState)
READY_JS = """(() => {
  const ha = document.querySelector('home-assistant');
  if (!ha) return document.readyState === 'complete';
  if (!(ha.hass && ha.hass.connected)) return false;
  const main = ha.shadowRoot && ha.shadowRoot.querySelector('home-assistant-main');
  return !!main;
})()"""

# высота прокручиваемого контента: максимум scrollHeight по всем элементам, включая shadow DOM
HEIGHT_JS = """(() => {
  let max = document.documentElement.scrollHeight;
  const walk = (root) => {
    for (const el of root.querySelectorAll('*')) {
      if (el.scrollHeight > el.clientHeight + 4) max = Math.max(max, el.scrollHeight + (el.getBoundingClientRect().top || 0));
      if (el.shadowRoot) walk(el.shadowRoot);
    }
  };
  walk(document);
  return Math.ceil(max);
})()"""


class CDP:
    def __init__(self, ws):
        self.ws, self.n = ws, 0

    async def call(self, method, **params):
        self.n += 1
        mid = self.n
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(await self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    async def js(self, expr):
        r = await self.call("Runtime.evaluate", expression=expr, returnByValue=True)
        return r.get("result", {}).get("value")


async def shoot(a, profile):
    for _ in range(50):                                   # ждём отладочный порт
        try:
            tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json", timeout=2))
            page = next(t for t in tabs if t.get("type") == "page")
            break
        except Exception:
            await asyncio.sleep(0.3)
    else:
        raise RuntimeError("Edge не открыл отладочный порт")
    async with websockets.connect(page["webSocketDebuggerUrl"], max_size=200 * 1024 * 1024) as ws:
        c = CDP(ws)
        await c.call("Page.enable")
        await c.call("Emulation.setDeviceMetricsOverride", width=a.width, height=a.height or 1200,
                     deviceScaleFactor=1, mobile=False)
        await c.call("Page.navigate", url=a.url)
        t0 = time.time()
        while time.time() - t0 < a.wait:
            if await c.js(READY_JS):
                break
            await asyncio.sleep(0.5)
        await asyncio.sleep(3)                            # карточки, шаблоны markdown, картинки
        h = a.height or min(max(await c.js(HEIGHT_JS) or 1200, 600), 16000)
        await c.call("Emulation.setDeviceMetricsOverride", width=a.width, height=h, deviceScaleFactor=1, mobile=False)
        await asyncio.sleep(1.5)
        shot = await c.call("Page.captureScreenshot", format="png", captureBeyondViewport=False)
        data = base64.b64decode(shot["data"])
        with open(a.out, "wb") as f:
            f.write(data)
        return h, len(data)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("url")
    p.add_argument("--width", type=int, default=1280)
    p.add_argument("--height", type=int, default=0)
    p.add_argument("--out", default=r"W:\Tools\pageshot\shot.png")
    p.add_argument("--wait", type=int, default=25)
    a = p.parse_args()
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    profile = os.path.join(tempfile.gettempdir(), "pageshot_" + uuid.uuid4().hex[:8])
    os.makedirs(os.path.join(profile, "Default"))
    shutil.copytree(LOCAL_STORAGE, os.path.join(profile, "Default", "Local Storage"), dirs_exist_ok=True)
    proc = subprocess.Popen([EDGE, "--headless=new", f"--remote-debugging-port={PORT}", f"--user-data-dir={profile}",
                             "--no-first-run", "--hide-scrollbars", "--disable-gpu", "--window-size=1280,1200", "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        h, n = asyncio.run(shoot(a, profile))
        print(json.dumps({"ok": True, "out": a.out, "width": a.width, "height": h, "bytes": n}))
    except Exception as e:
        print(json.dumps({"ok": False, "error": repr(e)[:300]}))
    finally:
        proc.kill()
        time.sleep(0.8)
        shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    main()
