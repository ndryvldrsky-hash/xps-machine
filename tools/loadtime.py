# -*- coding: utf-8 -*-
"""Время загрузки витрин HA фоновым Edge через DevTools Protocol (2026-09-28, оптимизация дашборда Май).

Для каждого адреса: холодный заход (свежий профиль, без кэша) и N тёплых перезагрузок. Меряется:
  ready — HA подключился и нарисовал оболочку (как в pageshot.py);
  view  — на открытой витрине появились карточки и их число не меняется 0,6 с;
  heap  — JS-память вкладки, МБ; cards — сколько карточек нарисовано.
  python loadtime.py <url> [<url> …] [--warm 2]   → JSON-строка на адрес
"""
import argparse
import asyncio
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request
import uuid

import websockets

from pageshot import CDP, EDGE, LOCAL_STORAGE, PORT, READY_JS

# число карточек открытой витрины: обход shadow DOM, считаем hui-card / hui-*-card с ненулевой высотой
CARDS_JS = """(() => {
  let n = 0;
  const walk = (root) => {
    for (const el of root.querySelectorAll('*')) {
      const t = el.tagName.toLowerCase();
      if ((t === 'hui-card' || (t.startsWith('hui-') && t.endsWith('-card'))) && el.getBoundingClientRect().height > 0) n++;
      if (el.shadowRoot) walk(el.shadowRoot);
    }
  };
  walk(document);
  return n;
})()"""


async def measure(c, url, reload=False):
    t0 = time.time()
    if reload:
        await c.call("Page.reload", ignoreCache=False)
    else:
        await c.call("Page.navigate", url=url)
    ready = view = None
    last, stable_since = -1, None
    while time.time() - t0 < 60:
        if ready is None and await c.js(READY_JS):
            ready = time.time() - t0
        if ready is not None:
            n = await c.js(CARDS_JS) or 0
            if n > 0 and n == last:
                if stable_since and time.time() - stable_since >= 0.6:
                    view = stable_since - t0
                    break
            else:
                last, stable_since = n, time.time()
        await asyncio.sleep(0.1)
    heap = await c.js("performance.memory ? Math.round(performance.memory.usedJSHeapSize/1048576) : null")
    return {"ready_s": round(ready or -1, 4), "view_s": round(view or -1, 4), "cards": last, "heap_mb": heap}


async def run(a):
    for _ in range(50):
        try:
            tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json", timeout=2))
            page = next(t for t in tabs if t.get("type") == "page")
            break
        except Exception:
            await asyncio.sleep(0.3)
    else:
        raise RuntimeError("Edge не открыл отладочный порт")
    out = []
    async with websockets.connect(page["webSocketDebuggerUrl"], max_size=200 * 1024 * 1024) as ws:
        c = CDP(ws)
        await c.call("Page.enable")
        await c.call("Emulation.setDeviceMetricsOverride", width=1280, height=1200, deviceScaleFactor=1, mobile=False)
        for url in a.urls:
            r = {"url": url.split("8123")[-1], "cold": await measure(c, url)}
            r["warm"] = [await measure(c, url, reload=True) for _ in range(a.warm)]
            out.append(r)
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("urls", nargs="+")
    p.add_argument("--warm", type=int, default=2)
    a = p.parse_args()
    profile = os.path.join(tempfile.gettempdir(), "loadtime_" + uuid.uuid4().hex[:8])
    os.makedirs(os.path.join(profile, "Default"))
    shutil.copytree(LOCAL_STORAGE, os.path.join(profile, "Default", "Local Storage"), dirs_exist_ok=True)
    proc = subprocess.Popen([EDGE, "--headless=new", f"--remote-debugging-port={PORT}", f"--user-data-dir={profile}",
                             "--no-first-run", "--hide-scrollbars", "--disable-gpu", "--window-size=1280,1200", "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for r in asyncio.run(run(a)):
            print(json.dumps(r))  # ASCII: консоль WinRM на XPS в cp1252
    except Exception as e:
        print(json.dumps({"ok": False, "error": repr(e)[:300]}))
    finally:
        proc.kill()
        time.sleep(0.8)
        shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    main()
