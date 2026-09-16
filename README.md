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
| `ffmpeg/variant_push.ps1`         | `W:\ffmpeg\variant_push.ps1`     | Варианты частоты xps_b<N> по требованию (runOnDemand в mediamtx): читает чистый xps_cam, кладёт оверлей/кольцо/часы/шарик, публикует xps_b<N> (2026-09-15) |
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

**У задач `MediaMTX` и `WebcamPush` снят лимит времени выполнения (`ExecutionTimeLimit = PT0S`,
2026-09-15).** По умолчанию `Register-ScheduledTask` ставит 72 часа («остановить задачу, если
выполняется дольше»), и планировщик считает только время бодрствования машины, сон не входит:
mediamtx был убит ровно через 3 суток чистого аптайма (`LastTaskResult 0x41306`), камера в
Frigate ушла в `unavailable`, а ffmpeg из `WebcamPush` вечно висел в `SYN_SENT` к `127.0.0.1:1935`.
При пересоздании любой «вечной» задачи на XPS обязательно добавлять
`New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero)` (плюс
`-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries`), иначе через три дня всё повторится.


## Оверлей телеметрии (2026-09-14/15)

`ffmpeg/overlay_render.ps1` — фоновый PowerShell-рендерер PNG-слоя, тот же дизайн, что у камеры iMac
(`/config/imac_camera/overlay_render.swift` в основном репозитории): три столбика-таблицы с подгруппами
(Поток, Камера (ProcAmp), Сеть, Система, Сессия, Home Assistant), правый зеркальный. Два файла в
`W:\ffmpeg\overlay\`: `overlay.png` (данные, раз в 2 с) и `clock.png` (дата и время с четырьмя
знаками секунд, `$hz` раз в секунду, по умолчанию 2 — каждый герц стоит ~9% ядра). `webcam_push.ps1`
запускает рендерер (проверка живости по `render.pid`), накладывает оба слоя фильтром `overlay`,
пишет для него `progress.txt` (`-progress`) и `yavg.txt` (signalstats) и больше не рисует drawtext.
Подгруппу «Home Assistant» рендерер забирает по HTTP с `http://192.168.77.2:8123/local/xps_overlay_extra.txt`
(HA кладёт её раз в минуту, автоматизация «Камеры: данные HA в оверлеи»).

Грабли Windows/ffmpeg, поймано вживую: путь с буквой диска в опции `file=` фильтра не проходит
(относительный путь + `Set-Location`); `-re` у image2-входов душит конвейер до 0.6x — вместо него
`-thread_queue_size 1`; `setpts=PTS-STARTPTS` на всех входах обязателен (dshow стартует с большой
метки, слои с нуля — overlay ждал часами); переименовать файл поверх открытого ffmpeg нельзя —
PNG пишется одним `WriteAllBytes`; имя функции `Measure` перехватывает алиас `Measure-Object`.
Деплой файлов — **в первую очередь через MCP-сервер xps** (`xps_put` с `bom: true`; с 2026-09-16 правило: скрипт ниже
конфликтует с постоянным WinRM-шеллом MCP — WSManFault InvalidSelectors). Запасной путь, только без MCP — `/config/.local/bin/xps_put.py <local> <remote> --bom` (кусками по 2000 символов
base64, ровно один BOM), обратно — `xps_get.py`.

### Живые скопы «Сигнал» (2026-09-15)

Внизу по центру кадра — блок из четырёх живых элементов, которые считает сам ffmpeg внутри
`webcam_push.ps1` (функция `Get-ScopeFilter`), без участия PowerShell-рендерера: waveform яркости
и vectorscope (10 к/с, «дышат» вместе с картинкой), два бегущих графика `drawgraph` по метаданным
`signalstats` — освещённость (YAVG) и движение (YDIF), 4 к/с, минута истории. Скопы прозрачные
(`colorkey` по чёрному + `colorchannelmixer aa=0.85`) на той же плашке 42 % чёрного, что и столбики.
Плашка с подписями — статичный `overlay\scopes_bg_<WxH>.png`, ffmpeg рисует его один раз на
разрешение (drawtext на каждом кадре обходился дороже). Цена — ffmpeg 10 % → 20 % ядра на
640x480@30. Выключатель — файл `W:\ffmpeg\scopes_off.txt` (применяется со следующего
5-минутного сегмента). Грабли: перед `waveform`/`vectorscope` нужен явный `format=`; `fontfile`
в drawtext — относительный путь (буква диска ломает парсер, как и `file=`); цвета `fg` у
`drawgraph` — `0xAABBGGRR`; у vectorscope берётся центр 96x96 из 256x256 (`crop`), иначе след
комнатной картинки сжимается в точку; комментарий после строки с `+` в конце обрывает
конкатенацию `$chain` — ffmpeg падает на «Error binding filtergraph». Тесты фильтров через
WinRM: не запускать несколько ffmpeg параллельно (машина захлёбывается, а сессия рвётся по
30-секундному таймауту чтения) и не `Start-Process` в фон — фоновый процесс умирает вместе
с командой сессии, как и HASS.Agent.

Той же ночью в центральный столбик рендерера добавлены «Камера (USB)» (чип Sunplus 1BCF:289A,
драйвер usbvideo и сборка, USB-порт — из Get-PnpDevice один раз при старте) и «Кодер» (кадры,
дубли, объём текущего 5-минутного сегмента из progress.txt). Блок «Камера (CameraControl)»
(экспозиция/фокус/зум) невозможен: IAMCameraControl у камеры пустой (все свойства
ERROR_NOT_FOUND, см. комментарий в `build_dll.ps1`). Два ограничения раскладки, пойманные
вживую: подгон масштаба в `Render` идёт по СУММЕ ШИРИН трёх столбиков — одно длинное значение
(«Sunplus 1BCF:289A», «usbvideo (Microsoft)») уменьшает весь оверлей на шаг 0.92, значения
держать не длиннее адресов из «Сеть»; центральный столбик обязан кончаться выше плашки скопов
(y≈333 на 480p), поэтому строки «Скорость» и «Битрейт факт.» убраны, а блок скопов сделан ниже
(волна 48 px, графики 18 px).

GPU на этой машине для фильтров не помощник (проверено 2026-09-15 на этой сборке ffmpeg 9.0.1
essentials): OpenCL и Vulkan не вкомпилированы (`-init_hw_device` → -12), CUDA не грузится с
драйвером GT 640M 2019 года (`cuMemAllocAsync`), QSV мёртв (см. шапку `webcam_push.ps1`), из
рабочего только `d3d11va` (декод h264/hevc — нам не нужен, вход yuyv/MJPEG) и `scale_d3d11`.
Кодер и так аппаратный: MF выбирает «NVIDIA H.264 Encoder MFT». GDI+-рендерер — софт по определению.

Проверено и full-сборкой (`W:\ffmpeg\ffmpeg-9.0.1-full_build`, скачана 2026-09-15 через задачу
планировщика: curl + встроенный bsdtar читает 7z; оставлена на диске, ~0,65 ГБ, в проде НЕ используется).
OpenCL там поднимается на обеих картах (`opencl=ocl:0.0` GT 640M, `1.0` HD 4000; без индекса —
«More than one matching device»), Vulkan на GT 640M падает. Замер боевого графа на testsrc2, 20 с видео,
CPU-время процесса: essentials/CPU 2250 мс, full/CPU 2531 мс, full + `overlay_opencl` для блока скопов
на HD 4000 3094 мс, на GT 640M 4219 мс — GPU ХУЖЕ: hwupload/hwdownload кадра 640x480 на каждом
кадре дороже самого наложения, а сами скопы (waveform/vectorscope/drawgraph) OpenCL-версий не имеют.
Грабли: у `hwdownload` формат вывода только sw-формат кадра (`hwdownload,format=yuv420p,format=nv12`);
yuva420p-кадр для OpenCL обязан быть чётных размеров, иначе «Failed to allocate frame to upload to»
(блок 278x139 → `pad=ceil(iw/2)*2:ceil(ih/2)*2`).

**CUDA (тем же вечером, эксперимент закрыт).** Драйвер GT 640M 425.31 — последний Game Ready для
Kepler-ноутбуков (апрель 2019), CUDA 10.1, NVENC API 9.0. ffmpeg ≥ 5 не грузит CUDA вовсе
(`cuMemAllocAsync`), ffmpeg 4.3/4.4 грузит (overlay_cuda работает), но их nvenc требует API 11.1/9.1,
а h264_mf в сборке gyan 4.4.1 (зеркало VideoHelp, gyan свои старые пакеты убрал) вообще не собран —
значит, кадр всё равно возвращать на CPU. Микробенч наложений (PNG-плашка + фон блока + drawtext,
testsrc2 640x480, 20 с, CPU-время): 9.0.1 CPU 1703 мс, 4.4.1 CPU 1672 мс, 4.4.1 hwupload_cuda →
2×overlay_cuda → hwdownload 2000–2375 мс. GPU снова дороже. Единственный путь с выигрышем — свой
кросс-билд ffmpeg 4.4 против ffnvcodec 9.0.18 (тогда nvenc принимает кадры из CUDA без выгрузки),
не делали. Грабли 4.4.1: drawtext с `%{localtime}` на Windows стоит ~140 мс на кадр (со статичным
текстом — нормально); overlay_cuda в 4.4 требует главный вход yuv420p, не nv12. Сборка 4.4.1 с
диска удалена.

**Часы (2026-09-15).** Слой clock.png (PowerShell, 2 Гц) заменён на drawtext в ffmpeg:
`%{localtime\:%F  %T.%4N}` каждый кадр, цена в пределах погрешности; рендерер запускается с
`-NoClock` и лишь пишет `clock_pos.txt` (x y размер шрифта). Формат только через `%F`/`%T` —
вложенные двоеточия парсер не переживает.

**Плашка «GPU» (2026-09-15).** Четвёртая плашка рендерера внизу слева (маркер `[bottomleft]`,
в подгоне масштаба по ширине не участвует): температура/P-state/память GT 640M из nvidia-smi
(utilization, clocks, power у этой карты «Not Supported») и занятость движков обеих карт из
счётчиков «GPU Engine» через `PerformanceCounterCategory.ReadCategory()` (Δraw/Δt между опросами
быстрого яруса, без секундной паузы `Get-Counter`; LUID дискретной карты — та, у которой
Dedicated Usage > 0). Блок скопов сужен до 180 px, чтобы левый и правый столбики могли расти
до низа кадра.
