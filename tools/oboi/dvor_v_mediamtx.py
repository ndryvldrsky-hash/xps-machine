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
    "  # <<< alena dvor\n\n"
)
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
p = xw.get_protocol(); sh = p.open_shell()
cmd = p.run_command(sh, "powershell", ["-NoProfile", "-EncodedCommand", base64.b64encode(ps.encode("utf-16-le")).decode()])
out, err, rc = p.get_command_output(sh, cmd); p.cleanup_command(sh, cmd); p.close_shell(sh)
print(out.decode("utf-8", "replace").strip(), ("ОШИБКА rc=%d" % rc) if rc else "")
