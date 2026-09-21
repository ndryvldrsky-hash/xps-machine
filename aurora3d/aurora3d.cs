// «Аврора 3D» (2026-09-21): 3D-скопы камеры XPS на видеокарте (OpenGL, NVIDIA GT 640M) — слой для webcam_push.ps1.
//   Сигнал — «водопад»: рельеф средней яркости по столбцам кадра, срез каждые 0,2 с, 20 с в глубину; спереди —
//            полное распределение яркости текущего кадра (как прежний waveform). Ось Y — яркость 0–255.
//   Вектор — «тоннель»: цвета кадра (U/V, окно ±32 из ±128, как прежний crop) точками своего цвета, срезы уходят
//            вглубь; кольца насыщенности (5–25 % от чистого цвета) стоят на метках времени и едут вместе с ним.
//   Время — подвижные метки ЧЧ:ММ:СС через 5 с по глубине обеих сцен.
// Вход: собственный ffmpeg читает чистый xps_sub (rtsp://127.0.0.1:8554/xps_sub) → 160x90 yuv444p 10 к/с.
// Выход: именованный канал \\.\pipe\aurora3d — сырой BGRA (прямая альфа) W×H, ~10 к/с; webcam_push.ps1 берёт его
//   входом ffmpeg (-f rawvideo -use_wallclock_as_timestamps 1). Файлом НЕ отдаём: 2 МБ × 10 к/с = ~2 ТБ записи в сутки.
//   Нет читателя — ждём подключения; читатель ушёл (перезапуск ffmpeg камеры) — снова ждём, ничего не падает.
// Рисование аддитивное (свечение), альфа потом = максимум канала, цвет «распремножается» — ffmpeg ждёт прямую альфу.
// Подписи — GDI+ поверх считанного кадра, позиции — той же матрицей, что у OpenGL.
// Сборка: build.ps1 (csc из .NET Framework 4, C# 5 — без интерполяции строк и прочего нового синтаксиса).
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.IO.Pipes;
using System.Runtime.InteropServices;
using System.Threading;

static class Aurora3D
{
    // ---------- параметры ----------
    const int W = 960, H = 540;                 // размер слоя; ffmpeg растягивает его на кадр
    const int IW = 160, IH = 90;                // размер анализируемого кадра
    const int SLICES = 100;                     // срезов истории: 100 × 0,2 с = 20 с
    const double SLICE_SEC = 0.2;
    const int YBINS = 48;                       // уровней яркости в распределении переднего среза
    const int UVB = 48;                         // сетка U/V тоннеля (окно ±32)
    static readonly string FFMPEG = @"W:\ffmpeg\ffmpeg-9.0.1-essentials_build\bin\ffmpeg.exe";
    static readonly string SRC = "rtsp://127.0.0.1:8554/xps_sub";
    static readonly string LOG = @"W:\tools\aurora3d\aurora3d.log";

    // ---------- история ----------
    class Slice { public DateTime t; public float[] mean = new float[IW]; public float[,] uv = new float[UVB, UVB]; }
    static readonly LinkedList<Slice> hist = new LinkedList<Slice>();
    static float[,] frontDist = new float[IW, YBINS];     // распределение яркости последнего кадра
    static readonly object lk = new object();
    static volatile bool haveInput = false;

    static void Log(string s)
    {
        try
        {
            if (File.Exists(LOG) && new FileInfo(LOG).Length > 1 << 20) File.Delete(LOG);
            File.AppendAllText(LOG, DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + "  " + s + "\r\n", System.Text.Encoding.UTF8);
        }
        catch { }
    }

    // ---------- вход: ffmpeg → yuv444p ----------
    static void InputLoop()
    {
        int fsz = IW * IH * 3;
        while (true)
        {
            try
            {
                var psi = new ProcessStartInfo(FFMPEG, "-hide_banner -loglevel error -rtsp_transport tcp -i " + SRC +
                    " -vf fps=10,scale=" + IW + ":" + IH + ",format=yuv444p -f rawvideo -")
                { UseShellExecute = false, RedirectStandardOutput = true, RedirectStandardError = true, CreateNoWindow = true };
                using (var p = Process.Start(psi))
                {
                    p.ErrorDataReceived += (o, e) => { if (e.Data != null) Log("вход ffmpeg: " + e.Data); };
                    p.BeginErrorReadLine();
                    var st = p.StandardOutput.BaseStream; var buf = new byte[fsz];
                    var acc = new Slice(); int accN = 0; DateTime accT = DateTime.Now;
                    while (true)
                    {
                        int got = 0;
                        while (got < fsz) { int n = st.Read(buf, got, fsz - got); if (n <= 0) throw new IOException("вход закончился"); got += n; }
                        haveInput = true;
                        // распределение яркости по столбцам + средняя яркость столбца
                        var dist = new float[IW, YBINS];
                        for (int x = 0; x < IW; x++)
                        {
                            float s = 0;
                            for (int y = 0; y < IH; y++) { int Y = buf[y * IW + x]; s += Y; dist[x, Y * YBINS / 256] += 1; }
                            acc.mean[x] += s / IH;
                        }
                        // U/V в окне ±32 (как crop 64:64:96:96 у прежнего vectorscope)
                        int pu = IW * IH, pv = 2 * IW * IH;
                        for (int i = 0; i < IW * IH; i++)
                        {
                            int u = buf[pu + i] - 128, v = buf[pv + i] - 128;
                            if (u < -32 || u >= 32 || v < -32 || v >= 32) continue;
                            acc.uv[(u + 32) * UVB / 64, (v + 32) * UVB / 64] += 1;
                        }
                        accN++;
                        lock (lk) frontDist = dist;
                        if ((DateTime.Now - accT).TotalSeconds >= SLICE_SEC)
                        {
                            for (int x = 0; x < IW; x++) acc.mean[x] /= accN;
                            acc.t = DateTime.Now;
                            lock (lk) { hist.AddFirst(acc); while (hist.Count > SLICES) hist.RemoveLast(); }
                            acc = new Slice(); accN = 0; accT = DateTime.Now;
                        }
                    }
                }
            }
            catch (Exception e) { haveInput = false; Log("вход: " + e.Message + " — повтор через 3 с"); Thread.Sleep(3000); }
        }
    }

    // ---------- матрицы (столбцовый порядок, как ждёт OpenGL) ----------
    static float[] Persp(double fovDeg, double asp, double n, double f)
    {
        double t = 1 / Math.Tan(fovDeg * Math.PI / 360); var m = new float[16];
        m[0] = (float)(t / asp); m[5] = (float)t; m[10] = (float)((f + n) / (n - f)); m[11] = -1; m[14] = (float)(2 * f * n / (n - f));
        return m;
    }
    static float[] LookAt(double ex, double ey, double ez, double cx, double cy, double cz)
    {
        double fx = cx - ex, fy = cy - ey, fz = cz - ez, fl = Math.Sqrt(fx * fx + fy * fy + fz * fz); fx /= fl; fy /= fl; fz /= fl;
        double sx = fy * 0 - fz * 1, sy = fz * 0 - fx * 0, sz = fx * 1 - fy * 0, sl = Math.Sqrt(sx * sx + sy * sy + sz * sz); sx /= sl; sy /= sl; sz /= sl;
        double ux = sy * fz - sz * fy, uy = sz * fx - sx * fz, uz = sx * fy - sy * fx;
        var m = new float[16];
        m[0] = (float)sx; m[4] = (float)sy; m[8] = (float)sz;
        m[1] = (float)ux; m[5] = (float)uy; m[9] = (float)uz;
        m[2] = (float)-fx; m[6] = (float)-fy; m[10] = (float)-fz;
        m[12] = (float)-(sx * ex + sy * ey + sz * ez); m[13] = (float)-(ux * ex + uy * ey + uz * ez); m[14] = (float)(fx * ex + fy * ey + fz * ez); m[15] = 1;
        return m;
    }
    static float[] Mul(float[] a, float[] b)
    {
        var r = new float[16];
        for (int c = 0; c < 4; c++) for (int rr = 0; rr < 4; rr++) { float s = 0; for (int k = 0; k < 4; k++) s += a[k * 4 + rr] * b[c * 4 + k]; r[c * 4 + rr] = s; }
        return r;
    }
    // проекция точки в пиксели слоя (вьюпорт vx,vy,vw,vh; y — сверху вниз); false — за камерой
    static bool Proj(float[] mvp, double x, double y, double z, int vx, int vy, int vw, int vh, out float px, out float py)
    {
        double cx = mvp[0] * x + mvp[4] * y + mvp[8] * z + mvp[12], cy = mvp[1] * x + mvp[5] * y + mvp[9] * z + mvp[13], cw = mvp[3] * x + mvp[7] * y + mvp[11] * z + mvp[15];
        px = 0; py = 0; if (cw <= 0.01) return false;
        px = (float)(vx + (cx / cw + 1) / 2 * vw); py = (float)(H - (vy + (cy / cw + 1) / 2 * vh)); return true;
    }

    // ---------- рисование ----------
    static readonly List<float> vb = new List<float>(), cb = new List<float>();
    static void V(double x, double y, double z, double r, double g, double b) { vb.Add((float)x); vb.Add((float)y); vb.Add((float)z); cb.Add((float)r); cb.Add((float)g); cb.Add((float)b); cb.Add(1); }
    static void Flush(uint mode)
    {
        if (vb.Count == 0) return;
        var va = vb.ToArray(); var ca = cb.ToArray();
        GL.glVertexPointer(3, 0x1406, 0, va); GL.glColorPointer(4, 0x1406, 0, ca); GL.glDrawArrays(mode, 0, va.Length / 3);
        vb.Clear(); cb.Clear();
    }

    struct Label { public string s; public float x, y; public Color c; public bool center; }
    static readonly List<Label> labels = new List<Label>();
    static void L(string s, float x, float y, Color c, bool center) { labels.Add(new Label { s = s, x = x, y = y, c = c, center = center }); }

    const double WF_D = 4.0, TN_D = 6.0;        // глубина сцен в мировых единицах на 20 с
    static readonly Color cSig = Color.FromArgb(64, 208, 255), cVec = Color.FromArgb(255, 96, 208), cAx = Color.FromArgb(225, 235, 245);

    static void DrawWaterfall(List<Slice> hs, float[,] dist, DateTime now)
    {
        // Сигнал на весь слой: x — столбец кадра, y — яркость (0–1), z — возраст среза
        var P = Persp(48, (double)W / H, 0.1, 40); var Vw = LookAt(0, 1.45, 2.9, 0, 0.3, -1.6); var M = Mul(P, Vw);
        GL.glViewport(0, 0, W, H); GL.glMatrixMode(0x1701); GL.glLoadMatrixf(P); GL.glMatrixMode(0x1700); GL.glLoadMatrixf(Vw);
        const double X0 = -1.55, XW = 3.1;
        // рельеф: линия средней яркости каждого среза, яркость следа гаснет с возрастом
        foreach (var s in hs)
        {
            double age = (now - s.t).TotalSeconds; if (age > 20) continue;
            double z = -age / 20 * WF_D, k = 0.9 * (1 - age / 20) + 0.08;
            for (int x = 0; x + 1 < IW; x++)
            {
                V(X0 + XW * x / (IW - 1), s.mean[x] / 255.0, z, 0.25 * k, 0.82 * k, 1 * k);
                V(X0 + XW * (x + 1) / (IW - 1), s.mean[x + 1] / 255.0, z, 0.25 * k, 0.82 * k, 1 * k);
            }
        }
        Flush(0x0001);
        // передний срез — полное распределение яркости (как прежний waveform)
        if (dist != null)
        {
            for (int x = 0; x < IW; x++) for (int b = 0; b < YBINS; b++)
                {
                    float n = dist[x, b]; if (n <= 0) continue; double k = Math.Min(0.8, 0.12 + n / 20.0);
                    V(X0 + XW * x / (IW - 1), (b + 0.5) / YBINS, 0.02, 0.25 * k, 0.82 * k, 1 * k);
                }
            GL.glPointSize(2); Flush(0x0000);
        }
        // оси: рамка переднего среза, шкала Y, линии времени по глубине
        double a = 0.35;
        V(X0, 0, 0, a, a, a); V(X0 + XW, 0, 0, a, a, a); V(X0, 0, 0, a, a, a); V(X0, 1, 0, a, a, a);
        V(X0, 0, 0, a, a, a); V(X0, 0, -WF_D, a, a, a);
        for (int i = 0; i <= 4; i++) { double y = i / 4.0; V(X0, y, 0, a * 0.6, a * 0.6, a * 0.6); V(X0 + XW, y, 0, a * 0.6, a * 0.6, a * 0.6); }
        var tick0 = new DateTime(now.Year, now.Month, now.Day, now.Hour, now.Minute, now.Second / 5 * 5);
        for (int i = 0; i <= 4; i++)
        {
            var tt = tick0.AddSeconds(-5 * i); double age = (now - tt).TotalSeconds; if (age > 20) continue; double z = -age / 20 * WF_D;
            V(X0, 0, z, a, a, a); V(X0 + XW, 0, z, a, a, a);
            float px, py; if (Proj(M, X0 - 0.05, 0, z, 0, 0, W, H, out px, out py)) L(tt.ToString("HH:mm:ss"), px - 62, py - 8, cAx, false);
        }
        Flush(0x0001);
        for (int i = 0; i <= 4; i++)
        {
            float px, py; if (Proj(M, X0 - 0.03, i / 4.0, 0, 0, 0, W, H, out px, out py)) L(((int)Math.Round(255 * i / 4.0)).ToString(), px - 26, py - 7, cSig, false);
        }
        float lx, ly; if (Proj(M, X0, 1.08, 0, 0, 0, W, H, out lx, out ly)) L("Сигнал · Y 0–255, вглубь — время", lx, ly - 8, cSig, false);
    }

    static void HueColor(double u, double v, out double r, out double g, out double b)
    {
        // цвет точки тоннеля: оттенок из U/V (усилен ×3 — окно ±32 из ±128), яркость средняя
        double Y = 150, U = u * 32 * 3, Vv = v * 32 * 3;
        r = Math.Max(0, Math.Min(255, Y + 1.402 * Vv)) / 255; g = Math.Max(0, Math.Min(255, Y - 0.344 * U - 0.714 * Vv)) / 255; b = Math.Max(0, Math.Min(255, Y + 1.772 * U)) / 255;
    }

    static void DrawTunnel(List<Slice> hs, DateTime now)
    {
        // Вектор в центральном квадрате H×H: x = U, y = V, z — возраст среза (окно ±32 → [-1, 1])
        int vx = (W - H) / 2;
        var P = Persp(58, 1, 0.1, 40); var Vw = LookAt(0.25, 0.35, 1.9, 0, 0, -2.5); var M = Mul(P, Vw);
        GL.glViewport(vx, 0, H, H); GL.glMatrixMode(0x1701); GL.glLoadMatrixf(P); GL.glMatrixMode(0x1700); GL.glLoadMatrixf(Vw);
        foreach (var s in hs)
        {
            double age = (now - s.t).TotalSeconds; if (age > 20) continue;
            double z = -age / 20 * TN_D, fade = 1 - age / 20;
            for (int i = 0; i < UVB; i++) for (int j = 0; j < UVB; j++)
                {
                    float n = s.uv[i, j]; if (n <= 0) continue;
                    double u = (i + 0.5) / UVB * 2 - 1, v = (j + 0.5) / UVB * 2 - 1, r, g, b; HueColor(u, v, out r, out g, out b);
                    double k = Math.Min(0.9, 0.1 + n / 80.0) * (0.12 + 0.88 * fade);   // без засвета центра: аддитивные точки быстро белеют
                    V(u, v, z, r * k, g * k, b * k);
                }
        }
        GL.glPointSize(2); Flush(0x0000);
        // кольца насыщенности: спереди яркие, по глубине — на метках времени (едут вместе с временем)
        const double pure = 118.0;
        var tick0 = new DateTime(now.Year, now.Month, now.Day, now.Hour, now.Minute, now.Second / 5 * 5);
        for (int ti = -1; ti <= 4; ti++)
        {
            double z = 0, k = 0.55; DateTime tt = now;
            if (ti >= 0) { tt = tick0.AddSeconds(-5 * ti); double age = (now - tt).TotalSeconds; if (age > 20) continue; z = -age / 20 * TN_D; k = 0.28 * (1 - age / 20) + 0.08; }
            foreach (int p in new[] { 5, 10, 15, 20, 25 })
            {
                if (ti >= 0 && p != 25) continue;          // по глубине — только внешнее кольцо
                double rad = pure * p / 100 / 32;
                for (int a = 0; a < 72; a++)
                {
                    double a0 = a * Math.PI / 36, a1 = (a + 1) * Math.PI / 36;
                    V(rad * Math.Cos(a0), rad * Math.Sin(a0), z, k, 0.38 * k, 0.82 * k); V(rad * Math.Cos(a1), rad * Math.Sin(a1), z, k, 0.38 * k, 0.82 * k);
                }
                float px, py;
                if (ti < 0 && Proj(M, -rad * 0.7071, -rad * 0.7071, 0, vx, 0, H, H, out px, out py)) L(p + " %", px - 34, py, cVec, false);
            }
            // метки времени — справа на кольце (сверху они сходились в перспективе и налезали друг на друга)
            if (ti >= 0) { float px, py; if (Proj(M, pure * 0.25 / 32, 0, z, vx, 0, H, H, out px, out py)) L(tt.ToString("HH:mm:ss"), px + 4, py - 7, cAx, false); }
        }
        // направления основных цветов (замер scope_probe.ps1: смещения в пикселях скопа, y вниз → здесь y вверх)
        var hues = new object[][] {
            new object[] { "Кр", -38, 112, Color.FromArgb(255, 80, 80) }, new object[] { "Пр", 74, 94, Color.FromArgb(255, 90, 230) },
            new object[] { "Сн", 112, -18, Color.FromArgb(110, 150, 255) }, new object[] { "Гл", 38, -112, Color.FromArgb(90, 230, 255) },
            new object[] { "Зл", -74, -94, Color.FromArgb(100, 255, 110) }, new object[] { "Жл", -112, 18, Color.FromArgb(255, 230, 90) } };
        double rr = pure * 0.27 / 32;
        foreach (var h in hues)
        {
            double du = (int)h[1], dv = (int)h[2], l = Math.Sqrt(du * du + dv * dv), x = du / l * rr, y = dv / l * rr;
            V(0, 0, 0, 0.2, 0.08, 0.16); V(x, y, 0, 0.2, 0.08, 0.16);
            float px, py; if (Proj(M, x * 1.08, y * 1.08, 0, vx, 0, H, H, out px, out py)) L((string)h[0], px, py - 8, (Color)h[3], true);
        }
        Flush(0x0001);
        float lx, ly; if (Proj(M, 0, -pure * 0.25 / 32 - 0.12, 0, vx, 0, H, H, out lx, out ly)) L("Вектор · насыщенность, % от чистого цвета; вглубь — время", lx, ly, cVec, true);
    }

    // ---------- главный цикл ----------
    static int Main()
    {
        Log("старт");
        new Thread(InputLoop) { IsBackground = true }.Start();
        GL.Init(W, H);
        Log("OpenGL: " + GL.Renderer);
        var px = new byte[W * H * 4]; var outb = new byte[W * H * 4];
        var font = new Font("Consolas", 12f, FontStyle.Bold, GraphicsUnit.Pixel);
        var shadow = new SolidBrush(Color.FromArgb(200, 0, 0, 0));
        var fmt = StringFormat.GenericTypographic;
        while (true)
        {
            NamedPipeServerStream pipe = null;
            try
            {
                pipe = new NamedPipeServerStream("aurora3d", PipeDirection.Out, 1, PipeTransmissionMode.Byte, PipeOptions.None, 0, W * H * 4);
                pipe.WaitForConnection();
                Log("читатель подключился");
                var sw = Stopwatch.StartNew(); long frame = 0;
                while (pipe.IsConnected)
                {
                    List<Slice> hs; float[,] dist;
                    lock (lk) { hs = new List<Slice>(hist); dist = frontDist; }
                    var now = DateTime.Now; labels.Clear();
                    GL.glClearColor(0, 0, 0, 0); GL.glClear(0x4000 | 0x100);
                    if (haveInput) { DrawWaterfall(hs, dist, now); DrawTunnel(hs, now); }
                    GL.glFinish();
                    GL.glReadPixels(0, 0, W, H, 0x80E1, 0x1401, px);
                    // переворот по вертикали + альфа = максимум канала, цвет «распремножить» (прямая альфа для ffmpeg)
                    for (int y = 0; y < H; y++)
                    {
                        int si = (H - 1 - y) * W * 4, di = y * W * 4;
                        for (int x = 0; x < W; x++, si += 4, di += 4)
                        {
                            int b = px[si], g = px[si + 1], r = px[si + 2], a = Math.Max(r, Math.Max(g, b));
                            if (a == 0) { outb[di] = outb[di + 1] = outb[di + 2] = outb[di + 3] = 0; continue; }
                            outb[di] = (byte)(b * 255 / a); outb[di + 1] = (byte)(g * 255 / a); outb[di + 2] = (byte)(r * 255 / a); outb[di + 3] = (byte)(a * 200 / 255);
                        }
                    }
                    if (labels.Count > 0)
                    {
                        var h = GCHandle.Alloc(outb, GCHandleType.Pinned);
                        try
                        {
                            using (var bmp = new Bitmap(W, H, W * 4, PixelFormat.Format32bppArgb, h.AddrOfPinnedObject()))
                            using (var g = Graphics.FromImage(bmp))
                            {
                                g.TextRenderingHint = System.Drawing.Text.TextRenderingHint.AntiAlias;
                                foreach (var l in labels)
                                {
                                    float x = l.x; if (l.center) x -= g.MeasureString(l.s, font, new PointF(0, 0), fmt).Width / 2;
                                    foreach (var d in new[] { new[] { -1, 0 }, new[] { 1, 0 }, new[] { 0, -1 }, new[] { 0, 1 } }) g.DrawString(l.s, font, shadow, x + d[0], l.y + d[1], fmt);
                                    using (var br = new SolidBrush(l.c)) g.DrawString(l.s, font, br, x, l.y, fmt);
                                }
                            }
                        }
                        finally { h.Free(); }
                    }
                    pipe.Write(outb, 0, outb.Length);
                    frame++;
                    // 10 к/с по часам: догоняем, если отстали, и не спешим вперёд
                    long due = (long)(frame * 100) - sw.ElapsedMilliseconds;
                    if (due > 0) Thread.Sleep((int)due); else if (due < -1000) { frame = sw.ElapsedMilliseconds / 100; }
                }
            }
            catch (Exception e) { Log("выход: " + e.Message); }
            finally { if (pipe != null) try { pipe.Dispose(); } catch { } }
            Thread.Sleep(500);
        }
    }
}

// ---------- OpenGL 1.x + FBO (EXT) через P/Invoke ----------
static class GL
{
    [StructLayout(LayoutKind.Sequential)]
    struct PFD
    {
        public ushort nSize, nVersion; public uint dwFlags; public byte iPixelType, cColorBits, cRedBits, cRedShift,
        cGreenBits, cGreenShift, cBlueBits, cBlueShift, cAlphaBits, cAlphaShift, cAccumBits, cAccumRedBits,
        cAccumGreenBits, cAccumBlueBits, cAccumAlphaBits, cDepthBits, cStencilBits, cAuxBuffers, iLayerType, bReserved;
        public uint dwLayerMask, dwVisibleMask, dwDamageMask;
    }
    [DllImport("user32.dll")] static extern IntPtr CreateWindowEx(uint ex, string cls, string name, uint style, int x, int y, int w, int h, IntPtr parent, IntPtr menu, IntPtr inst, IntPtr param);
    [DllImport("user32.dll")] static extern IntPtr GetDC(IntPtr hwnd);
    [DllImport("gdi32.dll")] static extern int ChoosePixelFormat(IntPtr hdc, ref PFD pfd);
    [DllImport("gdi32.dll")] static extern bool SetPixelFormat(IntPtr hdc, int fmt, ref PFD pfd);
    [DllImport("opengl32.dll")] static extern IntPtr wglCreateContext(IntPtr hdc);
    [DllImport("opengl32.dll")] static extern bool wglMakeCurrent(IntPtr hdc, IntPtr ctx);
    [DllImport("opengl32.dll")] static extern IntPtr wglGetProcAddress(string name);
    [DllImport("opengl32.dll")] static extern IntPtr glGetString(uint name);
    [DllImport("opengl32.dll")] public static extern void glViewport(int x, int y, int w, int h);
    [DllImport("opengl32.dll")] public static extern void glClearColor(float r, float g, float b, float a);
    [DllImport("opengl32.dll")] public static extern void glClear(uint mask);
    [DllImport("opengl32.dll")] public static extern void glMatrixMode(uint mode);
    [DllImport("opengl32.dll")] public static extern void glLoadMatrixf(float[] m);
    [DllImport("opengl32.dll")] public static extern void glEnable(uint cap);
    [DllImport("opengl32.dll")] public static extern void glDisable(uint cap);
    [DllImport("opengl32.dll")] public static extern void glBlendFunc(uint s, uint d);
    [DllImport("opengl32.dll")] public static extern void glPointSize(float s);
    [DllImport("opengl32.dll")] public static extern void glEnableClientState(uint arr);
    [DllImport("opengl32.dll")] public static extern void glVertexPointer(int size, uint type, int stride, float[] ptr);
    [DllImport("opengl32.dll")] public static extern void glColorPointer(int size, uint type, int stride, float[] ptr);
    [DllImport("opengl32.dll")] public static extern void glDrawArrays(uint mode, int first, int count);
    [DllImport("opengl32.dll")] public static extern void glReadPixels(int x, int y, int w, int h, uint fmt, uint type, byte[] data);
    [DllImport("opengl32.dll")] public static extern void glFinish();
    delegate void GenFn(int n, out uint id);
    delegate void BindFn(uint target, uint id);
    delegate void StorageFn(uint target, uint fmt, int w, int h);
    delegate void AttachRbFn(uint target, uint attach, uint rbTarget, uint rb);
    static T Fn<T>(string n) where T : class
    {
        IntPtr p = wglGetProcAddress(n); if (p == IntPtr.Zero) throw new Exception("нет функции " + n);
        return Marshal.GetDelegateForFunctionPointer(p, typeof(T)) as T;
    }
    public static string Renderer;
    public static void Init(int w, int h)
    {
        IntPtr hwnd = CreateWindowEx(0, "STATIC", "aurora3d", 0, 0, 0, 16, 16, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero);
        IntPtr hdc = GetDC(hwnd);
        var pfd = new PFD { nSize = (ushort)Marshal.SizeOf(typeof(PFD)), nVersion = 1, dwFlags = 0x4 | 0x20 | 0x1, cColorBits = 32, cAlphaBits = 8, cDepthBits = 24 };
        SetPixelFormat(hdc, ChoosePixelFormat(hdc, ref pfd), ref pfd);
        IntPtr ctx = wglCreateContext(hdc);
        if (ctx == IntPtr.Zero || !wglMakeCurrent(hdc, ctx)) throw new Exception("контекст OpenGL не создан");
        Renderer = Marshal.PtrToStringAnsi(glGetString(0x1F01)) + " / " + Marshal.PtrToStringAnsi(glGetString(0x1F02));
        const uint FB = 0x8D40, RB = 0x8D41; uint fbo, rc, rd;
        Fn<GenFn>("glGenFramebuffersEXT")(1, out fbo); Fn<BindFn>("glBindFramebufferEXT")(FB, fbo);
        Fn<GenFn>("glGenRenderbuffersEXT")(1, out rc); Fn<BindFn>("glBindRenderbufferEXT")(RB, rc);
        Fn<StorageFn>("glRenderbufferStorageEXT")(RB, 0x8058, w, h); Fn<AttachRbFn>("glFramebufferRenderbufferEXT")(FB, 0x8CE0, RB, rc);
        Fn<GenFn>("glGenRenderbuffersEXT")(1, out rd); Fn<BindFn>("glBindRenderbufferEXT")(RB, rd);
        Fn<StorageFn>("glRenderbufferStorageEXT")(RB, 0x81A6, w, h); Fn<AttachRbFn>("glFramebufferRenderbufferEXT")(FB, 0x8D00, RB, rd);
        glEnable(0x0BE2); glBlendFunc(1, 1);                 // аддитивное свечение
        glDisable(0x0B71);                                   // без теста глубины: следы просвечивают друг друга
        glEnable(0x0B10);                                    // сглаживание точек
        glEnableClientState(0x8074); glEnableClientState(0x8076);
    }
}
