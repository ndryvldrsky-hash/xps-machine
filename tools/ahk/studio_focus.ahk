; Фокус — в поле сообщения чата Студии (12.10.2026, просьба хозяина).
; Ctrl+\ и Ctrl+Esc из любого окна: поднять окно браузера со Студией и передать ему Ctrl+\ —
; в Студии на это сочетание назначена команда «Claude Code: Focus input» (/config/vscode_keybindings.json).
; Ctrl+Esc в Windows открывает «Пуск», поэтому штатное сочетание расширения не работало; здесь оно перехвачено.
; Клавиши заданы скан-кодами, чтобы работать при любой раскладке: SC02B — «\» над Enter, SC056 — «\» у левого Shift.

#Requires AutoHotkey v2.0
#SingleInstance Force
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
        Send "^{SC02B}"
}

$^SC02B::FocusStudio()
$^SC056::FocusStudio()
$^Esc::FocusStudio()
