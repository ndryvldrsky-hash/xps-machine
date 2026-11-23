# Перенос стола XPS с локальной rdpuser на доменную ALENA\<В> (23.11.2026; хозяин: «Давай пересаживать Стол»; Грамота Премудрой).
# Копия, не перенос: профиль rdpuser остаётся целым — архив и откат (DPAPI его паролей/кук живёт только там).
# Пароли и куки браузеров в копии НЕ расшифруются (DPAPI другой учётки) — вход в Google/Яндекс заново, пароли вернёт синхронизация.
# Запускать, когда исходная учётка ВЫШЛА (иначе файлы браузеров заняты), а новая уже входила (профиль создан) и браузеры в ней не открывались.
#   powershell -File перенос_профиля.ps1 [-Из rdpuser] [-В ahuser]          — копия папок + правка задач
param([string]$Из = 'rdpuser', [string]$В = 'ahuser')
$ErrorActionPreference = 'Stop'
$src = "W:\Users\$Из"; $dst = "W:\Users\$В"
if (-not (Test-Path $dst)) { throw "нет профиля $dst — сначала первый вход ALENA\$В" }
if ((quser 2>&1 | Out-String) -match "(?m)^\s*>?$Из\s") { throw "$Из ещё в системе — выйти из неё (не «сменить пользователя»)" }
$лог = "W:\ProgramData\alena\перенос_профиля_$(Get-Date -Format yyyyMMdd_HHmm).log"
$папки = 'Desktop', 'Documents', 'Downloads', 'Pictures', 'Videos', 'Music', '.android', '.ssh', '.cursor', '.vscode', '.vscode-shared', '.espressif', '.copilot',
    'AppData\Roaming\Telegram Desktop', 'AppData\Local\Google\Chrome\User Data', 'AppData\Local\Yandex\YandexBrowser\User Data',
    'AppData\Local\Microsoft\Edge\User Data', 'AppData\Local\HASS.Agent', 'AppData\Local\Programs', 'AppData\Roaming\obs-studio',
    'AppData\Roaming\Code', 'AppData\Roaming\Cursor', 'AppData\Local\alena_cdp_edge', 'AppData\Local\Lively Wallpaper', 'AppData\Roaming\Lively Wallpaper',
    'AppData\Roaming\TigerVNC', 'AppData\Roaming\TightVNC', 'AppData\Local\Moonlight Game Streaming Project', 'AppData\Roaming\Microsoft\Windows\Start Menu\Programs'
$кеши = 'Cache', 'Code Cache', 'GPUCache', 'GrShaderCache', 'ShaderCache', 'DawnCache', 'DawnGraphiteCache', 'DawnWebGPUCache', 'CacheStorage', 'Crashpad', 'component_crx_cache'
foreach ($p in $папки) {
    $s = Join-Path $src $p; if (-not (Test-Path $s)) { continue }
    $d = Join-Path $dst $p
    # /COPY:DAT — без ACL: файлы наследуют права профиля новой учётки; /XJ — без точек соединения (Application Data и т. п.)
    robocopy $s $d /E /COPY:DAT /DCOPY:DAT /XJ /R:1 /W:1 /MT:8 /NFL /NDL /NP /XD $кеши /XF 'LOCK' 'SingletonLock' 'lockfile' /LOG+:$лог | Out-Null
    "{0,-55} код {1}" -f $p, $LASTEXITCODE    # 0–7 — успех, 8+ — ошибки (смотреть лог)
}
# задачи с путями в профиль старой учётки → новая
foreach ($t in Get-ScheduledTask | ? { $_.Principal.GroupId -eq 'AlenaStol' }) {
    [xml]$x = Export-ScheduledTask -TaskName $t.TaskName -TaskPath $t.TaskPath
    $было = $x.OuterXml
    $стало = $было -replace [regex]::Escape("W:\Users\$Из\"), "W:\Users\$В\"
    if ($стало -ne $было) {
        Register-ScheduledTask -Xml $стало -TaskName $t.TaskName -TaskPath $t.TaskPath -Force | Out-Null
        "задача $($t.TaskName): пути $Из → $В"
    }
}
"лог: $лог"
