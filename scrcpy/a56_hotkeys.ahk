; Горячие клавиши для окна телефона A56 (scrcpy).
; Работают ТОЛЬКО когда активно окно scrcpy — иначе Ctrl+= и Ctrl+- ломали бы масштаб
; в браузере и редакторе. Поле «Быстрый вызов» у ярлыков Windows такие сочетания не умеет
; (там только Ctrl+Alt+буква), поэтому здесь AutoHotkey v2.
;
; Ctrl +        элементы крупнее (плотность вверх, шрифт пересчитывается — текст не скачет)
; Ctrl -        элементы мельче
; Ctrl Shift +  шрифт больше (только текст)
; Ctrl Shift -  шрифт меньше
; Ctrl 0        полный сброс: заводская плотность, шрифт 1.0, автоповорот
; Ctrl S        снимок экрана телефона в буфер обмена и в «Изображения\A56»
; Ctrl R        повернуть телефон в альбом / вернуть автоповорот (переключатель)
; Ctrl W        пересоздать окно под текущую ориентацию телефона
; Ctrl M        растянуть окно до предела БЕЗ чёрных полей (Ctrl Shift M — обычный размер)
; CapsLock      переключить язык на телефоне (Shift+CapsLock — запасной способ)
; CapsLock вне окна телефона — предыдущий язык Windows (Shift+CapsLock — обычный Caps Lock)
; Ctrl Alt V    картинку из буфера обмена Windows — в галерею телефона (буферы у них разные)
; Ctrl Alt 1    пресет «монитор»: плотность 180, альбом, экран не гаснет на кабеле
; Ctrl Alt 2    пресет «в руки»: заводская плотность, автоповорот, обычное гашение
; Ctrl Alt 3    список пресетов и текущее состояние телефона
; Ctrl Alt 4    режим окна «зеркало»: собственный экран телефона
; Ctrl Alt 5    режим окна «монитор»: отдельный дисплей под размер экрана, без полос
; Ctrl Alt 6    два окна сразу: телефон слева, виртуальный дисплей справа
; Ctrl Alt W    растянуть окно телефона до предела экрана без чёрных полей

#Requires AutoHotkey v2.0
#SingleInstance Force

TOOLS := "W:\tools\scrcpy-win64-v3.1\"

Beg(bat) {
    ; Запуск молча, свёрнутым окном: скрипты отрабатывают за секунду
    Run('"' . TOOLS . bat . '"', TOOLS, "Hide")
}

FitWindow(*) {
    ; Окно scrcpy строится по размеру кадра только при старте и на поворот телефона не реагирует.
    ; Синтетический Alt+W (его штатный «подогнать окно») до SDL-окна не доходит — пересоздаём окно.
    Beg("A56_okno_zanovo.bat")
}

#HotIf WinActive("ahk_exe scrcpy.exe")

^=::Beg("A56_elementy_krupnee.bat")     ; Ctrl и «+» без Shift — это Ctrl+=
^+=::Beg("A56_shrift_bolshe.bat")       ; Ctrl+Shift+= — привычный «Ctrl плюс»
^-::Beg("A56_elementy_melche.bat")
^+-::Beg("A56_shrift_menshe.bat")
^0::Beg("A56_sbros_vse.bat")
^s::Beg("A56_shot.bat")

; CapsLock переключает язык НА ТЕЛЕФОНЕ, а не раскладку Windows: в окне телефона родной
; Caps не нужен, а привычка жать его для смены языка остаётся. Android для физической
; клавиатуры переключает раскладку по Ctrl+Space — его и шлём, окно scrcpy передаёт дальше.
CapsLock::Send("^{Space}")

; Если Ctrl+Space в каком-то поле занят, есть запасной путь через adb — Shift+CapsLock
+CapsLock::Beg("A56_yazyk.bat")


^r::
{
    static land := true
    Beg(land ? "A56_gorizont.bat" : "A56_avtopovorot.bat")
    land := !land
    ; Окно scrcpy не переразмеряется при повороте телефона само — подгоняем под кадр
    SetTimer(FitWindow, -4000)
}

^w::FitWindow()      ; Ctrl+W — пересоздать окно под текущую ориентацию

; Ctrl+M — растянуть окно до предела экрана, СОХРАНИВ пропорции кадра.
; Обычный «развернуть на весь экран» даёт чёрные поля: кадр телефона 2340x1080 (2.17:1),
; а монитор 2560x1440 (1.78:1) — разницу нечем заполнить. Считаем пропорции по клиентской
; области окна (scrcpy создаёт его точно по кадру) и вписываем в экран без полей.
^m::MaxKeepAspect()

; Ctrl+Shift+M — вернуть обычный размер
^+m::WinRestore("ahk_exe scrcpy.exe")

MaxKeepAspect() {
    hwnd := WinExist("ahk_exe scrcpy.exe")
    if !hwnd
        return
    WinRestore(hwnd)                    ; из развёрнутого состояния размеры не поменять
    Sleep 150
    WinGetClientPos(, , &cw, &ch, hwnd) ; клиентская область = сам кадр, без рамки и заголовка
    WinGetPos(, , &ww, &wh, hwnd)       ; полные размеры окна — чтобы учесть рамку и заголовок
    if (cw = 0 || ch = 0)
        return
    frameW := ww - cw, frameH := wh - ch

    ; Рабочая область монитора, на котором сейчас окно (без панели задач)
    MonitorGetWorkArea(MonitorFromWindow(hwnd), &l, &t, &r, &b)
    availW := (r - l) - frameW, availH := (b - t) - frameH

    scale := Min(availW / cw, availH / ch)
    newW := Floor(cw * scale), newH := Floor(ch * scale)
    x := l + Floor(((r - l) - (newW + frameW)) / 2)
    y := t + Floor(((b - t) - (newH + frameH)) / 2)
    WinMove(x, y, newW + frameW, newH + frameH, hwnd)
}

MonitorFromWindow(hwnd) {
    ; Номер монитора, на котором окно: чтобы растягивать по нужному экрану, а не всегда по первому
    WinGetPos(&wx, &wy, &ww, &wh, hwnd)
    cx := wx + ww // 2, cy := wy + wh // 2
    loop MonitorGetCount() {
        MonitorGet(A_Index, &ml, &mt, &mr, &mb)
        if (cx >= ml && cx < mr && cy >= mt && cy < mb)
            return A_Index
    }
    return MonitorGetPrimary()
}

#HotIf

; --- Глобальные: работают в любом окне Windows ---
^!a::Beg("A56.bat")                     ; Ctrl+Alt+A — открыть окно телефона
^!s::Beg("A56_shot.bat")                ; Ctrl+Alt+S — снимок экрана в буфер
^!j::Beg("A56_shrift_menshe.bat")       ; Ctrl+Alt+J — шрифт меньше
^!k::Beg("A56_shrift_bolshe.bat")       ; Ctrl+Alt+K — шрифт больше
^!n::Beg("A56_elementy_melche.bat")     ; Ctrl+Alt+N — элементы мельче
^!m::Beg("A56_elementy_krupnee.bat")    ; Ctrl+Alt+M — элементы крупнее
^!0::Beg("A56_sbros_vse.bat")           ; Ctrl+Alt+0 — сброс размеров
^!1::Beg("A56_preset_monitor.bat")       ; Ctrl+Alt+1 — пресет «монитор»: работа в окне на XPS
^!2::Beg("A56_preset_hand.bat")          ; Ctrl+Alt+2 — пресет «в руки»: заводской экран телефона
^!3::Beg("A56_preset_spisok.bat")        ; Ctrl+Alt+3 — показать список пресетов и текущее состояние

; --- Режимы окна ---
^!4::Beg("A56_okno_zerkalo.bat")         ; Ctrl+Alt+4 — зеркало: собственный экран телефона
^!5::Beg("A56_okno_monitor.bat")         ; Ctrl+Alt+5 — монитор: виртуальный дисплей под экран
^!6::Beg("A56_okno_dva.bat")             ; Ctrl+Alt+6 — два окна сразу: телефон слева, дисплей справа
^!w::MaxKeepAspect()                    ; Ctrl+Alt+W — растянуть окно без чёрных полей
^!v::Beg("A56_otpravit_kartinku.bat")   ; Ctrl+Alt+V — картинку из буфера Windows в галерею телефона

; --- Раскладка Windows ---
; CapsLock — вернуться на ПРЕДЫДУЩИЙ язык Windows (как на Mac): RU↔EN туда-обратно, иврит
; в круг не попадает, пока им не пользовались. Скрипт раз в 200 мс смотрит раскладку активного
; окна и помнит две последние, поэтому учитывает и переключения мышью или Alt+Shift.
; В окне scrcpy выше срабатывают свои варианты: там CapsLock переключает язык на телефоне.
; Сам Caps Lock (заглавные) — Shift+CapsLock.
global LangCur := 0, LangPrev := 0

ActiveLayout() {
    try hwnd := WinExist("A")
    catch
        return 0
    if !hwnd
        return 0
    tid := DllCall("GetWindowThreadProcessId", "Ptr", hwnd, "Ptr", 0, "UInt")
    return DllCall("GetKeyboardLayout", "UInt", tid, "Ptr") & 0xFFFFFFFF
}

TrackLayout() {
    global LangCur, LangPrev
    hkl := ActiveLayout()
    if (!hkl || hkl = LangCur)
        return
    if LangCur
        LangPrev := LangCur
    LangCur := hkl
}

SwitchToPrevLayout() {
    global LangCur, LangPrev
    TrackLayout()
    target := LangPrev
    if !target  ; после старта скрипта предыдущего ещё нет — берём пару RU/EN
        target := ((LangCur & 0xFFFF) = 0x0419) ? 0x04090409 : 0x04190419
    ; Просьба сменить язык — фокусному элементу активного окна (так же делает сама Windows)
    try {
        ctl := ControlGetFocus("A")
        dest := ctl ? ctl : WinExist("A")
        PostMessage(0x50, 0, target, , dest)  ; WM_INPUTLANGCHANGEREQUEST
    }
    Sleep(80)
    ; Окна, которые игнорируют просьбу (консоль, часть UWP) — штатным Alt+Shift по кругу
    loop 3 {
        if (ActiveLayout() = target)
            break
        Send("{Alt down}{Shift down}{Shift up}{Alt up}")
        Sleep(80)
    }
    TrackLayout()
}

SetTimer(TrackLayout, 200)
TrackLayout()

CapsLock::SwitchToPrevLayout()
+CapsLock::SetCapsLockState(!GetKeyState("CapsLock", "T"))
