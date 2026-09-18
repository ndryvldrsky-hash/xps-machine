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
; Ctrl M        развернуть окно на весь экран (Ctrl Shift M — вернуть обычный размер)
; CapsLock      переключить язык на телефоне (Shift+CapsLock — запасной способ)
; Ctrl Alt V    картинку из буфера обмена Windows — в галерею телефона (буферы у них разные)

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

; Ctrl+M — развернуть окно на весь экран (scrcpy вписывает кадр сам, поля по бокам)
^m::WinMaximize("ahk_exe scrcpy.exe")

; Ctrl+Shift+M — вернуть обычный размер
^+m::WinRestore("ahk_exe scrcpy.exe")

#HotIf

; --- Глобальные: работают в любом окне Windows ---
^!a::Beg("A56.bat")                     ; Ctrl+Alt+A — открыть окно телефона
^!s::Beg("A56_shot.bat")                ; Ctrl+Alt+S — снимок экрана в буфер
^!j::Beg("A56_shrift_menshe.bat")       ; Ctrl+Alt+J — шрифт меньше
^!k::Beg("A56_shrift_bolshe.bat")       ; Ctrl+Alt+K — шрифт больше
^!n::Beg("A56_elementy_melche.bat")     ; Ctrl+Alt+N — элементы мельче
^!m::Beg("A56_elementy_krupnee.bat")    ; Ctrl+Alt+M — элементы крупнее
^!0::Beg("A56_sbros_vse.bat")           ; Ctrl+Alt+0 — сброс размеров
^!v::Beg("A56_otpravit_kartinku.bat")   ; Ctrl+Alt+V — картинку из буфера Windows в галерею телефона
