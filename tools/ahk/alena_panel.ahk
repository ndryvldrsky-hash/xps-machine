; Мини-приложение Алёны в левом углу панели задач XPS (на месте «Мини-приложений»/погоды) — 23.11.2026.
; Хозяин: «Давай сделаем своё мини-приложение для левой стороны от пуска» (Windows 11 своих значков левее «Пуска» не пускает,
; поэтому это своё окно поверх левого угла панели задач на каждом мониторе: без рамки, без кнопки на панели, поверх всех).
; Кнопки открывают ярлыки меню «Пуск» учётки стола (Chrome --app): «Май» — дашборд Май, «Студия» — Studio Code Server.
; Запуск — задача AlenaMiniPanel (группа AlenaStol, при входе). Файл на XPS: W:\tools\ahk\alena_panel.ahk.
#Requires AutoHotkey v2.0
#SingleInstance Force
Persistent
DetectHiddenWindows false

ПУСК := A_Programs   ; меню «Пуск» текущей учётки (Roaming\Microsoft\Windows\Start Menu\Programs)
; хозяин 23.11: «не будем там в панели писать слова, только иконки» — значок и всплывающая подсказка с названием
; 23.11 (дальше): «можно ещё значок показывать, который соответствует» — настоящие значки приложений, а не эмодзи:
; Май — значок Home Assistant (icons\ha.png, favicon Штаба), Студия — значок VS Code (icons\vscode_crop.png = code_150x150.png из его установки без полей — хозяин: «мелковат»;
; установки; значок прямо из Code.exe AutoHotkey 2.0.28 не берёт — «Can't create control»).
ЗНАЧКИ := A_ScriptDir "\icons"
; 23.11: «значок на Май тоже нужно сделать отдельным от домашнего ассистента» — свой: зелёный круг с «М» (icons\maj.png/.ico,
; рисует Алёна, xps-machine/tools/ahk/icons)
КНОПКИ := [[ЗНАЧКИ "\maj.png", ПУСК "\Май.lnk", "Май"], [ЗНАЧКИ "\vscode_crop.png", ПУСК "\Студия.lnk", "Студия"]]
ШИРИНА := 40, РАЗМЕР := 26
ФОН := "202020", ТЕКСТ := "E8E8E8", ПОДСВЕТКА := "2D2D2D"
панели := []
подсказки := Map()   ; hwnd значка → название (показывается при наведении)

Построить() {
    global панели, подсказки
    for g in панели
        try g.Destroy()
    панели := [], подсказки := Map()
    Loop MonitorGetCount() {
        MonitorGet(A_Index, &L, &T, &R, &B)
        MonitorGetWorkArea(A_Index, &WL, &WT, &WR, &WB)
        h := B - WB                                   ; панель задач снизу: от рабочей области до края монитора
        if (h < 24)                                   ; на этом мониторе панели снизу нет (скрыта/сбоку)
            continue
        g := Gui("+AlwaysOnTop -Caption +ToolWindow +E0x08000000")   ; WS_EX_NOACTIVATE — не забирает фокус у окна хозяина
        g.BackColor := ФОН
        g.MarginX := 6, g.MarginY := 0
        x := 6, мон := A_Index
        for к in КНОПКИ {
            try p := g.AddPicture("x" x + (ШИРИНА - РАЗМЕР) // 2 " y" (h - РАЗМЕР) // 2 " w" РАЗМЕР " h" РАЗМЕР " BackgroundTrans", к[1])
            catch                                      ; картинки нет — буква названия, но без окна ошибки на экране хозяина
                p := g.AddText("x" x " y0 h" h " w" ШИРИНА " +0x200 +Center c" ТЕКСТ " Background" ФОН, SubStr(к[3], 1, 1))
            p.OnEvent("Click", Открыть.Bind(к[2], мон))
            подсказки[p.Hwnd] := к[3]
            x += ШИРИНА + 4
        }
        g.Show("x" L + 4 " y" WB " w" x + 2 " h" h " NoActivate")
        панели.Push(g)
    }
}

; окно открывается на том мониторе, где нажат значок (хозяин: «открывать приложение на мониторе Sharp»), и разворачивается
Открыть(ярлык, мон, *) {
    было := Map()
    for w in WinGetList("ahk_exe chrome.exe")
        было[w] := 1
    try Run('"' ярлык '"')
    catch as e {
        TrayTip("Алёна: не открылось", ярлык "`n" e.Message)
        return
    }
    Loop 40 {                                          ; до 8 с ждём новое окно Chrome
        Sleep 200
        for w in WinGetList("ahk_exe chrome.exe") {
            if было.Has(w) || WinGetTitle(w) = ""
                continue
            MonitorGetWorkArea(мон, &WL, &WT, &WR, &WB)
            try {
                WinRestore(w), WinMove(WL, WT, WR - WL, WB - WT, w), WinMaximize(w), WinActivate(w)
            }
            return
        }
    }
}

; панель задач сама «topmost» и при щелчке по ней перекрывает окно — раз в 2 с поднимаем наши окна обратно
Поднять() {
    for g in панели
        try WinSetAlwaysOnTop(1, g.Hwnd), DllCall("SetWindowPos", "Ptr", g.Hwnd, "Ptr", -1, "Int", 0, "Int", 0, "Int", 0, "Int", 0, "UInt", 0x13)
}

; подсказка с названием при наведении мыши на значок
Наведение() {
    static было := 0
    MouseGetPos(,,, &ctl, 2)
    if (ctl && подсказки.Has(ctl)) {
        if (ctl != было)
            ToolTip(подсказки[ctl]), было := ctl
    } else if было
        ToolTip(), было := 0
}

; 23.11: «справа от кнопки Пуск … показывать значок правильный» — окно Студии (Chrome --app) берёт значок со страницы, а Студия
; открыта внутри HA → на кнопке окна значок Home Assistant. Ставим окну значок VS Code (WM_SETICON) и повторяем раз в 2 с:
; Chrome возвращает свой при смене favicon. Окна узнаём по заголовку без « - Google Chrome» (обычная вкладка не трогается).
; Значки окон: Студия — VS Code (из Code.exe), Май — свой (icons\maj.ico). Окно Май — «май – Home Assistant» без «Google Chrome».
ЗНАЧКИ_ОКОН := Map()
Значок(файл) {
    big := Buffer(A_PtrSize, 0), small := Buffer(A_PtrSize, 0)
    DllCall("Shell32\ExtractIconExW", "Str", файл, "Int", 0, "Ptr", big, "Ptr", small, "UInt", 1)
    return [NumGet(big, "Ptr"), NumGet(small, "Ptr")]
}
Значки_окон() {
    global ЗНАЧКИ_ОКОН
    if !ЗНАЧКИ_ОКОН.Count {
        ЗНАЧКИ_ОКОН["Studio Code Server"] := Значок(EnvGet("LOCALAPPDATA") "\Programs\Microsoft VS Code\Code.exe")
        ЗНАЧКИ_ОКОН["май – Home Assistant"] := Значок(ЗНАЧКИ "\maj.ico")
    }
    for заголовок, зн in ЗНАЧКИ_ОКОН {
        if !зн[1]
            continue
        for w in WinGetList(заголовок " ahk_exe chrome.exe") {
            if InStr(WinGetTitle(w), "Google Chrome")
                continue
            if (SendMessage(0x7F, 1, 0, , w) != зн[1]) {         ; WM_GETICON ICON_BIG — уже наш?
                SendMessage(0x80, 1, зн[1], , w)                  ; WM_SETICON ICON_BIG
                SendMessage(0x80, 0, зн[2], , w)                  ; WM_SETICON ICON_SMALL
            }
        }
    }
}

; Сторож живых обоев — отдельно: xps-machine/tools/oboi/oboi_storozh.ps1 (задача AlenaOboiStorozh); из AHK канал mpv не открылся.

Построить()
SetTimer(Поднять, 2000)
SetTimer(Значки_окон, 2000)
SetTimer(Наведение, 150)
OnMessage(0x007E, (*) => SetTimer(Построить, -1500))    ; WM_DISPLAYCHANGE — мониторы/разрешение поменялись: перестроить
