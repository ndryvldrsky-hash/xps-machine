# xps-machine

Версионируемая копия PowerShell-скриптов и конфигов, которые реально живут на диске
компьютера XPS (Dell XPS One 2710, Windows 11, доступ по WinRM — см. `device_credentials/
xps.txt` в основном репозитории `/config`). Раньше правки заливались на машину напрямую
через WinRM без какой-либо истории изменений — эта копия существует, чтобы это исправить.

**Не источник истины сама по себе.** Реальный диск машины (`W:\...`) может разойтись с этой
копией, если кто-то правит файлы прямо на месте (RDP и т.п.) в обход неё. Перед правкой —
сверять актуальное содержимое с диска (побайтово, см. ниже про кодировку), а не полагаться
слепо на git.

## Что здесь лежит и куда деплоится

| В репозитории                    | На диске XPS                    | Назначение |
|-----------------------------------|----------------------------------|------------|
| `ffmpeg/webcam_push.ps1`          | `W:\ffmpeg\webcam_push.ps1`      | Постоянный push веб-камеры в go2rtc/Frigate по RTMP (кодирование h264_mf, оверлей даты/времени/FPS/адресов/ProcAmp) |
| `ffmpeg/build_dll.ps1`            | `W:\ffmpeg\build_dll.ps1`        | Исходник (инлайн C#) для `XpsCamProcAmp.dll` — DirectShow-обвязка чтения IAMVideoProcAmp |
| `ffmpeg/XpsCamProcAmp.dll`        | `W:\ffmpeg\XpsCamProcAmp.dll`    | Собранный артефакт из `build_dll.ps1` (пересборка требует .NET на самой машине — держим готовую сборку, чтобы не компилировать при каждом старте, см. историю ниже) |
| `ThermalLog/poll_thermal.ps1`     | `W:\ThermalLog\poll_thermal.ps1` | Опрос ACPI-температуры/загрузки CPU раз в минуту (Scheduled Task `ThermalLog`) |
| `ThermalLog/http_server.ps1`      | `W:\ThermalLog\http_server.ps1`  | HTTP-мостик (порт 8090), отдаёт `latest.json` для rest-сенсоров HA |
| `ThermalLog/lhm_probe.ps1`        | `W:\ThermalLog\lhm_probe.ps1`    | Разовый диагностический скрипт LibreHardwareMonitor (RPM вентилятора — тупик, см. память проекта) |
| `mediamtx/mediamtx.yml`           | `W:\mediamtx\mediamtx.yml`       | Конфиг RTSP/RTMP-релея (принимает push от `webcam_push.ps1`, отдаёт RTSP наружу для Frigate) |

Полный технический контекст (грабли, история правок, почему именно так) — в
`device_credentials/xps.txt` основного репозитория `/config`, не дублируется здесь.

## Что сознательно НЕ версионируется

Сторонние скачиваемые бинарники и рантаймы (`ffmpeg.exe`/`ffplay.exe`/`ffprobe.exe`,
`mediamtx.exe`, вся папка `LibreHardwareMonitor` с рантаймами под все платформы) — не наш
код, восстанавливаются повторным скачиванием. Runtime-данные (логи, `thermal_log.csv`,
`latest.json`, сертификаты `mediamtx`, `resolution.txt`) — генерируются заново, не имеют
смысла в истории коммитов. Одноразовые диагностические черновики (`test_*.ps1`, `camctl*.ps1`
и т.п.), которые остались на диске XPS от прошлых сессий отладки — тоже не сюда, это мусор
для отдельной уборки, не код проекта.

## Деплой на машину

Через WinRM, **побайтово** (`[System.IO.File]::ReadAllBytes`/`WriteAllBytes`), не через
обычный текстовый `Get-Content`/`Set-Content` и не через `xps_winrm.py::upload_file` —
у обоих известны проблемы с кодировкой (консоль WinRM корёжит кириллицу в комментариях
при текстовом выводе; `upload_file` использует `WriteAllText`, который добавляет свой BOM
поверх уже имеющегося в файле, если исходник в UTF-8-BOM — двойной BOM). Сверять sha256
до/после заливки. Полный рецепт — `device_credentials/xps.txt`.

После заливки `webcam_push.ps1` — перезапуск через Scheduled Task `WebcamPush`
(`Stop-ScheduledTask` → убить осиротевший `ffmpeg.exe` по PID, НЕ широким
`Get-Process|Stop-Process` — оборвёт текущую WinRM-сессию → `Start-ScheduledTask`), правка
`.ps1` на диске не подхватывается уже запущенным процессом.
