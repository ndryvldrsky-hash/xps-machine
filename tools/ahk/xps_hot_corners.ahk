; Активные углы экрана для Windows — то, чего в ней нет штатно (в macOS это «горячие углы»).
; Курсор задерживается в углу на DELAY мс — срабатывает действие. Повтор не сработает, пока
; курсор не уйдёт из угла, иначе действие запускалось бы каждые полсекунды.
;
; По умолчанию:
;   левый верхний   — окно телефона A56 (открыть или поднять поверх)
;   правый верхний  — снимок экрана телефона в буфер обмена Windows
;   левый нижний    — показать все окна (Task View)
;   правый нижний   — свернуть всё, показать рабочий стол
;
; Поменять действия — в блоке Corner ниже. Отключить угол — закомментировать его строку.
;
; 14.10.2026 (хозяин: «очень плохо стали работать активные углы»): с двумя мониторами угол основного экрана на стыке
; со вторым (SHARP справа) — не угол: курсор не упирается в край, а проскакивает на соседний экран, и задержать его
; на 8 пикселях почти невозможно. Теперь угол засчитывается только там, где за краем нет другого монитора
; (HasMonitorAt), то есть по внешним углам всего рабочего стола: левые — на основном экране, правые — на SHARP.
; Зона увеличена до 12 px, задержка 350 мс. Копия в репозитории xps-machine/tools/ahk/.

#Requires AutoHotkey v2.0
#SingleInstance Force
#NoTrayIcon          ; 14.10.2026 (хозяин: «сократить иконки в трее») — у всех наших скриптов AHK иконка убрана
CoordMode("Mouse", "Screen")

TOOLS := "W:\tools\scrcpy-win64-v3.1\"
SIZE := 12       ; сторона квадрата в углу, пиксели
DELAY := 350     ; сколько держать курсор в углу, мс
armed := Map("lt", true, "rt", true, "lb", true, "rb", true)
timers := Map()

SetTimer(CheckCorners, 100)

CheckCorners() {
    global armed, timers
    MouseGetPos(&mx, &my)
    mon := MonitorFromPoint(mx, my)
    MonitorGet(mon, &l, &t, &r, &b)

    ; край «твёрдый», если сразу за ним нет другого монитора — только о такой край курсор упирается
    leftHard   := !HasMonitorAt(l - 2, my)
    rightHard  := !HasMonitorAt(r + 1, my)
    topHard    := !HasMonitorAt(mx, t - 2)
    bottomHard := !HasMonitorAt(mx, b + 1)

    corners := Map(
        "lt", (leftHard  && topHard    && mx <= l + SIZE     && my <= t + SIZE),
        "rt", (rightHard && topHard    && mx >= r - SIZE - 1 && my <= t + SIZE),
        "lb", (leftHard  && bottomHard && mx <= l + SIZE     && my >= b - SIZE - 1),
        "rb", (rightHard && bottomHard && mx >= r - SIZE - 1 && my >= b - SIZE - 1)
    )

    ; отладка (14.10): раз в секунду, когда курсор ближе 40 px к любому углу монитора — что видит скрипт
    static lastDbg := 0
    near := (mx <= l + 40 || mx >= r - 41) && (my <= t + 40 || my >= b - 41)
    if (near && A_TickCount - lastDbg > 1000) {
        lastDbg := A_TickCount
        try FileAppend(FormatTime(, "HH:mm:ss") " dbg mouse=" mx "," my " mon=" mon " b=" l "," t "-" r "," b " hard L" leftHard " R" rightHard " T" topHard " B" bottomHard " lt" corners["lt"] " rt" corners["rt"] " lb" corners["lb"] " rb" corners["rb"] "`n", "W:\tools\ahk\hot_corners.log", "UTF-8")
    }
    for key, inside in corners {
        if inside {
            if armed[key] && !timers.Has(key) {
                timers[key] := A_TickCount
            } else if timers.Has(key) && (A_TickCount - timers[key] >= DELAY) && armed[key] {
                armed[key] := false      ; не повторять, пока курсор не уйдёт
                timers.Delete(key)
                Corner(key)
            }
        } else {
            if timers.Has(key)
                timers.Delete(key)
            armed[key] := true
        }
    }
}

Corner(key) {
    try FileAppend(FormatTime(, "dd.MM HH:mm:ss") " угол " key "`n", "W:\tools\ahk\hot_corners.log", "UTF-8")   ; журнал срабатываний (14.10)
    switch key {
        case "lt":  ShowPhone()
        case "rt":  Run('"' . TOOLS . 'A56_shot.bat"', TOOLS, "Hide")
        case "lb":  Send("#{Tab}")        ; Task View — все окна
        case "rb":  Send("#d")            ; показать рабочий стол
    }
}

ShowPhone() {
    ; Окно уже открыто — поднимаем поверх; нет — запускаем
    if WinExist("ahk_exe scrcpy.exe") {
        WinActivate
        WinRestore("ahk_exe scrcpy.exe")
    } else {
        Run('"' . TOOLS . 'A56.bat"', TOOLS, "Hide")
    }
}

HasMonitorAt(x, y) {
    loop MonitorGetCount() {
        MonitorGet(A_Index, &ml, &mt, &mr, &mb)
        if (x >= ml && x < mr && y >= mt && y < mb)
            return true
    }
    return false
}

MonitorFromPoint(x, y) {
    loop MonitorGetCount() {
        MonitorGet(A_Index, &ml, &mt, &mr, &mb)
        if (x >= ml && x < mr && y >= mt && y < mb)
            return A_Index
    }
    return MonitorGetPrimary()
}
