#SingleInstance Force
#NoTrayIcon   ; 14.10.2026 — иконки наших скриптов AHK убраны из трея по просьбе хозяина
; Caps Lock — переключение на ПРЕДЫДУЩУЮ раскладку; Shift+Caps — настоящий Caps Lock.
; Живёт в W:\Users\rdpuser\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\LanguageToggle.ahk,
; запускается задачей LanguageToggle при входе (копия в репозитории xps-machine/tools).

global LastLang := 0
global CurrLang := 0

SetTimer(CheckLang, 100)

CheckLang()
{
    global LastLang, CurrLang
    hwnd := WinExist('A')
    if !hwnd
        return
    threadId := DllCall('GetWindowThreadProcessId', 'Ptr', hwnd, 'Ptr', 0)
    ly := DllCall('GetKeyboardLayout', 'UInt', threadId, 'UPtr')
    if (ly != CurrLang)
    {
        LastLang := CurrLang
        CurrLang := ly
    }
}

; В окне Moonlight Caps Lock не перехватываем — он уходит на удалённую машину (03.10.2026)
#HotIf !WinActive("ahk_exe Moonlight.exe") && !WinActive("ahk_exe parsecd.exe")
*CapsLock::
{
    global LastLang
    hwnd := WinExist('A')
    if (LastLang != 0)
        PostMessage(0x50, 0, LastLang,, hwnd)
    else
        ; 17.11.2026 (хозяин после перезагрузки XPS: «переключение клавиатуры не работает»): после старта скрипт ещё не
        ; видел «предыдущей» раскладки и Caps молчал до первой смены другим способом — теперь в этом случае следующая
        ; раскладка по списку (WM_INPUTLANGCHANGEREQUEST, INPUTLANGCHANGE_FORWARD = 2)
        PostMessage(0x50, 2, 0,, hwnd)
}

+CapsLock::
{
    SetCapsLockState(!GetKeyState('CapsLock', 'T'))
}
#HotIf
