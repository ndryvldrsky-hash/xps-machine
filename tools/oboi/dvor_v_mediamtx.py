#!/usr/bin/env python3
# Живые обои XPS (23.11.2026, хозяин: «Двор с Ксониксом», «На обоих»): путь `dvor` в местном mediamtx XPS тянет поток
# go2rtc Frigate (Кузница 192.168.77.62:8554/x2_wq_bl_xonix_nested) по требованию; Lively смотрит rtsp://127.0.0.1:8554/dvor —
# пароль go2rtc остаётся в mediamtx.yml (W:\mediamtx), а не в карточке Lively в профиле. Пароль — из Укромного места,
# в вывод не попадает. Повторный запуск заменяет блок между метками.
import base64, importlib.util, sys
sys.path.insert(0, "/config/.local/bin")
from укромное import значение

s = importlib.util.spec_from_file_location("xw", "/config/.local/bin/xps_winrm.py"); xw = importlib.util.module_from_spec(s); s.loader.exec_module(xw)
pw = значение("/config/device_credentials/frigate_go2rtc_rtsp.txt", "Пароль")
блок = (
    "  # >>> alena dvor (живые обои, 23.11.2026; пишет xps-machine/tools/oboi/dvor_v_mediamtx.py)\n"
    "  dvor:\n"
    f"    source: rtsp://alena:{pw}@192.168.77.62:8554/x2_wq_bl_xonix_nested\n"
    "    sourceOnDemand: yes\n"
    "  # 23.11: «На основной экран надо показать Ксоникс игру» — игра Ксоникс (go2rtc xonix_game) для обоев основной панели\n"
    "  xonix:\n"
    f"    source: rtsp://alena:{pw}@192.168.77.62:8554/xonix_game\n"
    "    sourceOnDemand: yes\n"
    "  # 23.11: «добавь остальные потоки у камеры двора» → «прорядить»: оставлены аура, аура 60, слой и мульти Ксоникса,\n"
    "  # основной x2_wq_bl — dvor_osnovnoi ($G1 — группа из имени пути)\n"
    "  dvor_osnovnoi:\n"
    f"    source: rtsp://alena:{pw}@192.168.77.62:8554/x2_wq_bl\n"
    "    sourceOnDemand: yes\n"
    "  ~^dvor_(aura|b60|xonix|xonix_multi)$:\n"
    f"    source: rtsp://alena:{pw}@192.168.77.62:8554/x2_wq_bl_$G1\n"
    "    sourceOnDemand: yes\n"
    "  # 23.11: «Добавь ещё потоки ворот тамбур» — та же тройка: основной, Аура, Аура 60 к/с (ворота в go2rtc — xm530)\n"
    "  vorota_osnovnoi:\n"
    f"    source: rtsp://alena:{pw}@192.168.77.62:8554/xm530\n"
    "    sourceOnDemand: yes\n"
    "  ~^vorota_(aura|b60)$:\n"
    f"    source: rtsp://alena:{pw}@192.168.77.62:8554/xm530_$G1\n"
    "    sourceOnDemand: yes\n"
    "  tambur_osnovnoi:\n"
    f"    source: rtsp://alena:{pw}@192.168.77.62:8554/tambur\n"
    "    sourceOnDemand: yes\n"
    "  ~^tambur_(aura|b60)$:\n"
    f"    source: rtsp://alena:{pw}@192.168.77.62:8554/tambur_$G1\n"
    "    sourceOnDemand: yes\n"
    "  # <<< alena dvor\n\n"
)
# 23.11 хозяин: «У нас вроде длинные скрипты передаются с помощью самбы» — так и делаем (как xps_put): весь сценарий одним файлом
# в шару обмена Кузницы (/share/obmen/xps = \\192.168.77.62\obmen\xps), XPS выполняет его оттуда; нарезка по WinRM запрещена
# (решение хозяина 11.11: «вырежи её, чтобы её просто больше не было»). Имя файла постоянное; после запуска он затирается
# пустым (в нём пароль go2rtc), не удаляется.
b64 = base64.b64encode(блок.encode("utf-8")).decode()
ps = r'''
$f = 'W:\mediamtx\mediamtx.yml'
$t = [IO.File]::ReadAllText($f, [Text.Encoding]::UTF8)
$b = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('B64'))
$t = [regex]::Replace($t, '(?s)  # >>> alena dvor.*?  # <<< alena dvor\r?\n\r?\n?', '')
$m = '  # Settings under path "all_others"'
if ($t.IndexOf($m) -lt 0) { 'НЕТ метки all_others'; exit 1 }
$z = 'W:\ProgramData\alena\zapasnik\' + (Get-Date -Format yyyy-MM-dd) + '\W\mediamtx'; New-Item -ItemType Directory -Force $z | Out-Null
Copy-Item $f "$z\mediamtx.yml.до_dvor_$(Get-Date -Format HHmmss)" -Force   # копия — в Запасник XPS, не рядом с рабочим
$t = $t.Replace($m, $b + $m)
[IO.File]::WriteAllText($f, $t, (New-Object Text.UTF8Encoding $false))
'путь dvor записан'
'''.replace("B64", b64)
ИМЯ = "alena_dvor_mediamtx.ps1"
локально = "/share/obmen/xps/" + ИМЯ
open(локально, "w", encoding="utf-8-sig").write(ps)
try:
    unc = r"\\192.168.77.62\obmen"
    u, pwsmb = значение("/config/device_credentials/samba_kuznitsa.txt", "username"), значение("/config/device_credentials/samba_kuznitsa.txt", "password")
    pwsmb = pwsmb.replace("'", "''")
    cmd = (f"if (-not (Test-Path '{unc}\\xps')) {{ net use '{unc}' '{pwsmb}' /user:'{u}' /persistent:no 2>&1 | Out-Null }}; "
           f"powershell -NoProfile -ExecutionPolicy Bypass -File '{unc}\\xps\\{ИМЯ}'")
    p = xw.get_protocol(); sh = p.open_shell()
    c = p.run_command(sh, "powershell", ["-NoProfile", "-EncodedCommand", base64.b64encode(cmd.encode("utf-16-le")).decode()])
    out, err, rc = p.get_command_output(sh, c); p.cleanup_command(sh, c); p.close_shell(sh)
finally:
    open(локально, "w").close()   # затереть (пароль), файл остаётся пустым
print(out.decode("utf-8", "replace").strip(), ("ОШИБКА rc=%d" % rc) if rc else "")
