; Фокус — в поле сообщения чата Студии (12.10.2026, просьба хозяина; 09.11.2026 сочетание сменено на Alt+/).
; Правый Alt+/ (и Ctrl+Esc) из любого окна: поднять окно браузера со Студией и передать ему Alt+/ —
; в Студии на это сочетание назначена команда «Claude Code: Focus input» (/config/vscode_keybindings.json).
; Ctrl+Esc в Windows открывает «Пуск», поэтому штатное сочетание расширения не работало; здесь оно перехвачено.
; Клавиша задана скан-кодом, чтобы работать при любой раскладке: SC035 — «/» слева от правого Shift.
; Прежние Ctrl+\ (SC02B, SC056) сняты 09.11.2026 — возвращены штатному «разделить редактор».

#Requires AutoHotkey v2.0
#SingleInstance Force
#NoTrayIcon
SetTitleMatchMode 2

FocusStudio() {
    hwnd := WinExist("Studio Code Server")
    if !hwnd {
        hwnd := WinExist("Visual Studio Code")
    }
    if !hwnd {
        SoundBeep 400, 120          ; окна Студии нет
        return
    }
    ; свёрнутое окно развернуть; развёрнутое не трогать — WinRestore снял бы с него полный экран
    if WinGetMinMax(hwnd) = -1
        WinRestore hwnd
    WinActivate hwnd
    if WinWaitActive(hwnd, , 1)
        Send "!{SC035}"
}

; Правый Alt (хозяин 09.11: «использует правый альт»). Во время озвучки диктовки правый Alt+/ занят аккордом «reset»
; (dictate.py, ALT_CHORDS 53) и до сюда не дойдёт — вне озвучки работает.
$>!SC035::FocusStudio()
$^Esc::FocusStudio()
