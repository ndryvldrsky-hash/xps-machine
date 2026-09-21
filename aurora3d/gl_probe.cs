// Пробник OpenGL для «авроры 3D» (2026-09-21): можно ли рисовать 3D-скопы на видеокарте XPS вне экрана.
// Создаёт скрытое окно + контекст WGL, кадровый буфер (FBO, расширение EXT), рисует сетку из N линий
// с перспективой, считывает кадр glReadPixels и пишет в gl_probe.txt: какая видеокарта, версия GL,
// среднее время кадра (рисование + считывание). Сборка — csc из .NET Framework (см. build.ps1).
using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;

static class GlProbe
{
    [StructLayout(LayoutKind.Sequential)]
    struct PIXELFORMATDESCRIPTOR
    {
        public ushort nSize, nVersion; public uint dwFlags; public byte iPixelType, cColorBits, cRedBits, cRedShift,
        cGreenBits, cGreenShift, cBlueBits, cBlueShift, cAlphaBits, cAlphaShift, cAccumBits, cAccumRedBits,
        cAccumGreenBits, cAccumBlueBits, cAccumAlphaBits, cDepthBits, cStencilBits, cAuxBuffers, iLayerType, bReserved;
        public uint dwLayerMask, dwVisibleMask, dwDamageMask;
    }
    [DllImport("user32.dll")] static extern IntPtr CreateWindowEx(uint ex, string cls, string name, uint style, int x, int y, int w, int h, IntPtr parent, IntPtr menu, IntPtr inst, IntPtr param);
    [DllImport("user32.dll")] static extern IntPtr GetDC(IntPtr hwnd);
    [DllImport("gdi32.dll")] static extern int ChoosePixelFormat(IntPtr hdc, ref PIXELFORMATDESCRIPTOR pfd);
    [DllImport("gdi32.dll")] static extern bool SetPixelFormat(IntPtr hdc, int fmt, ref PIXELFORMATDESCRIPTOR pfd);
    [DllImport("opengl32.dll")] static extern IntPtr wglCreateContext(IntPtr hdc);
    [DllImport("opengl32.dll")] static extern bool wglMakeCurrent(IntPtr hdc, IntPtr ctx);
    [DllImport("opengl32.dll")] static extern IntPtr wglGetProcAddress(string name);
    [DllImport("opengl32.dll")] static extern IntPtr glGetString(uint name);
    [DllImport("opengl32.dll")] static extern void glViewport(int x, int y, int w, int h);
    [DllImport("opengl32.dll")] static extern void glClearColor(float r, float g, float b, float a);
    [DllImport("opengl32.dll")] static extern void glClear(uint mask);
    [DllImport("opengl32.dll")] static extern void glMatrixMode(uint mode);
    [DllImport("opengl32.dll")] static extern void glLoadIdentity();
    [DllImport("opengl32.dll")] static extern void glFrustum(double l, double r, double b, double t, double n, double f);
    [DllImport("opengl32.dll")] static extern void glTranslatef(float x, float y, float z);
    [DllImport("opengl32.dll")] static extern void glRotatef(float a, float x, float y, float z);
    [DllImport("opengl32.dll")] static extern void glEnable(uint cap);
    [DllImport("opengl32.dll")] static extern void glBlendFunc(uint s, uint d);
    [DllImport("opengl32.dll")] static extern void glEnableClientState(uint arr);
    [DllImport("opengl32.dll")] static extern void glVertexPointer(int size, uint type, int stride, float[] ptr);
    [DllImport("opengl32.dll")] static extern void glColorPointer(int size, uint type, int stride, float[] ptr);
    [DllImport("opengl32.dll")] static extern void glDrawArrays(uint mode, int first, int count);
    [DllImport("opengl32.dll")] static extern void glReadPixels(int x, int y, int w, int h, uint fmt, uint type, byte[] data);
    [DllImport("opengl32.dll")] static extern void glFinish();

    delegate void GenFn(int n, out uint id);
    delegate void BindFn(uint target, uint id);
    delegate void StorageFn(uint target, uint fmt, int w, int h);
    delegate void AttachRbFn(uint target, uint attach, uint rbTarget, uint rb);
    delegate uint StatusFn(uint target);
    static T Gl<T>(string n) where T : class
    {
        IntPtr p = wglGetProcAddress(n);
        if (p == IntPtr.Zero) throw new Exception("нет функции " + n);
        return Marshal.GetDelegateForFunctionPointer(p, typeof(T)) as T;
    }

    static int Main(string[] args)
    {
        string outFile = args.Length > 0 ? args[0] : "gl_probe.txt";
        int W = 960, H = 540, lines = 20000, frames = 50;
        var log = new StringWriter();
        try
        {
            IntPtr hwnd = CreateWindowEx(0, "STATIC", "aurora3d", 0, 0, 0, 16, 16, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero);
            IntPtr hdc = GetDC(hwnd);
            var pfd = new PIXELFORMATDESCRIPTOR { nSize = (ushort)Marshal.SizeOf(typeof(PIXELFORMATDESCRIPTOR)), nVersion = 1,
                dwFlags = 0x4 | 0x20 | 0x1, iPixelType = 0, cColorBits = 32, cAlphaBits = 8, cDepthBits = 24 };
            int fmt = ChoosePixelFormat(hdc, ref pfd); SetPixelFormat(hdc, fmt, ref pfd);
            IntPtr ctx = wglCreateContext(hdc);
            if (ctx == IntPtr.Zero || !wglMakeCurrent(hdc, ctx)) throw new Exception("контекст OpenGL не создан");
            log.WriteLine("vendor:   " + Marshal.PtrToStringAnsi(glGetString(0x1F00)));
            log.WriteLine("renderer: " + Marshal.PtrToStringAnsi(glGetString(0x1F01)));
            log.WriteLine("version:  " + Marshal.PtrToStringAnsi(glGetString(0x1F02)));

            // FBO: цвет RGBA8 + глубина, всё через EXT (есть и на HD 4000, и на Kepler)
            const uint FB = 0x8D40, RB = 0x8D41;
            uint fbo, rbC, rbD;
            Gl<GenFn>("glGenFramebuffersEXT")(1, out fbo); Gl<BindFn>("glBindFramebufferEXT")(FB, fbo);
            Gl<GenFn>("glGenRenderbuffersEXT")(1, out rbC); Gl<BindFn>("glBindRenderbufferEXT")(RB, rbC);
            Gl<StorageFn>("glRenderbufferStorageEXT")(RB, 0x8058 /*RGBA8*/, W, H);
            Gl<AttachRbFn>("glFramebufferRenderbufferEXT")(FB, 0x8CE0 /*COLOR0*/, RB, rbC);
            Gl<GenFn>("glGenRenderbuffersEXT")(1, out rbD); Gl<BindFn>("glBindRenderbufferEXT")(RB, rbD);
            Gl<StorageFn>("glRenderbufferStorageEXT")(RB, 0x81A6 /*DEPTH24*/, W, H);
            Gl<AttachRbFn>("glFramebufferRenderbufferEXT")(FB, 0x8D00 /*DEPTH*/, RB, rbD);
            uint st = Gl<StatusFn>("glCheckFramebufferStatusEXT")(FB);
            log.WriteLine("fbo:      " + (st == 0x8CD5 ? "ok" : "ошибка 0x" + st.ToString("x")));

            // «водопад»: lines отрезков, лежащих в 200 рядах глубины
            var v = new float[lines * 2 * 3]; var c = new float[lines * 2 * 4]; var rnd = new Random(1);
            for (int i = 0; i < lines; i++)
            {
                float z = -(i % 200) * 0.05f, x = (i / 200) / 50f - 1f, y = (float)rnd.NextDouble();
                for (int k = 0; k < 2; k++)
                {
                    v[i * 6 + k * 3] = x + k * 0.02f; v[i * 6 + k * 3 + 1] = y; v[i * 6 + k * 3 + 2] = z;
                    c[i * 8 + k * 4] = 0.25f; c[i * 8 + k * 4 + 1] = 0.82f; c[i * 8 + k * 4 + 2] = 1f; c[i * 8 + k * 4 + 3] = 0.6f;
                }
            }
            var px = new byte[W * H * 4];
            glViewport(0, 0, W, H); glEnable(0x0BE2 /*BLEND*/); glBlendFunc(0x0302, 0x0303);
            glEnableClientState(0x8074 /*VERTEX_ARRAY*/); glEnableClientState(0x8076 /*COLOR_ARRAY*/);
            var sw = Stopwatch.StartNew(); double draw = 0, read = 0;
            for (int f = 0; f < frames; f++)
            {
                var t0 = sw.Elapsed.TotalMilliseconds;
                glClearColor(0, 0, 0, 0); glClear(0x4000 | 0x100);
                glMatrixMode(0x1701); glLoadIdentity(); glFrustum(-0.8, 0.8, -0.45, 0.45, 1, 30);
                glMatrixMode(0x1700); glLoadIdentity(); glTranslatef(0, -0.5f, -2.5f); glRotatef(25 + f, 1, 0, 0);
                glVertexPointer(3, 0x1406 /*FLOAT*/, 0, v); glColorPointer(4, 0x1406, 0, c);
                glDrawArrays(0x0001 /*LINES*/, 0, lines * 2);
                glFinish();
                var t1 = sw.Elapsed.TotalMilliseconds;
                glReadPixels(0, 0, W, H, 0x80E1 /*BGRA*/, 0x1401 /*UBYTE*/, px);
                var t2 = sw.Elapsed.TotalMilliseconds;
                draw += t1 - t0; read += t2 - t1;
            }
            var cpu = Process.GetCurrentProcess().TotalProcessorTime.TotalMilliseconds;
            int lit = 0; for (int i = 3; i < px.Length; i += 4) if (px[i] > 0) lit++;
            log.WriteLine("кадр {0}x{1}, {2} линий: рисование {3:0.0} мс, считывание {4:0.0} мс (среднее за {5}); CPU процесса {6:0} мс; закрашено пикселей {7}",
                W, H, lines, draw / frames, read / frames, frames, cpu, lit);
        }
        catch (Exception e) { log.WriteLine("ОШИБКА: " + e.Message); }
        File.WriteAllText(outFile, log.ToString(), System.Text.Encoding.UTF8);
        return 0;
    }
}
