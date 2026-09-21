// «Аврора 3D» (2026-09-21): вся аврора камеры XPS на видеокарте (OpenGL, NVIDIA GT 640M) — слой для webcam_push.ps1.
// «Грамматика авроры»: стиль отображения выбирается по роду данных, каждая грамматика включается кнопкой (пресетом
// PTZ во Frigate — встроенный поддельный ONVIF, см. ниже):
//   Сигнал           — распределение по кадру → «водопад»: рельеф средней яркости по столбцам, 20 с в глубину;
//   Вектор           — двумерная величина (U/V) → «тоннель»: точки цвета уходят вглубь, кольца насыщенности на метках времени;
//   Свет и Движение  — одно число во времени → бегущие линии (YAVG 0–255, YDIF 0–24) на подвижной оси 80 с;
//   Линии            — температура на улице, потребление дома (HA);
//   Свечение         — число с порогом → свечение рамки: снаружи — CPU XPS до TjMax, внутри — детектор Frigate;
//   Наборы           — однотипные числа → водопад столбиков: 4 ядра XPS, 8 каналов электричества (HA);
//   Направления      — направление и сила → стрелки в тоннеле: ветер (HA), куда движется картинка в кадре;
//   События          — вспышки на оси времени: люди на камерах Frigate, SMS, обрыв связи с HA, падение VPN;
//   Состояния        — дорожки под осью времени: Андрей снаружи, бойлер, блокировка XPS, VPN;
//   Потоки           — частицы, скорость ∝ объёму: сеть и диск XPS, WAN Оптиплекса, VPN телефона.
// Входы: собственный ffmpeg из чистого xps_sub (160x90 yuv444p 25 к/с); MQTT alena/aurora/data от HA (автоматизация
//   aurora_data_publish; логин/адрес берутся из appsettings.json HASS.Agent — пароль не дублируется); термо-лог
//   W:\ThermalLog\latest.json; счётчики Windows; пинг HA.
// Выход: канал \\.\pipe\aurora3d — сырой BGRA (прямая альфа) 960x540, ~10 к/с (файлом нельзя — ~2 ТБ записи в сутки).
// ONVIF: http://<XPS>:8099/onvif/… — Frigate видит PTZ-камеру: пресеты = переключатели грамматик, стрелки/зум —
//   поворот и приближение 3D-сцен. Состояние переключателей — в легенде кадра и в toggles.txt.
// Сборка: build.ps1 (csc .NET Framework 4, C# 5 — без интерполяции строк и прочего нового синтаксиса).
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.IO.Pipes;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Web.Script.Serialization;

static class Aurora3D
{
    // ---------- параметры ----------
    // 21.09 ночь: слой сразу 1920x1080 (раньше 960x540 и растяжение в ffmpeg); SC — масштаб пиксельных отступов/шрифта
    const int W = 1920, H = 1080; const double SC = W / 960.0; const float SCF = (float)SC;
    const int IW = 160, IH = 90;
    const int SLICES = 200; const double SLICE_SEC = 0.1;   // срез на каждый кадр анализа (10 к/с), 20 с в глубину
    const int YBINS = 48, UVB = 48;
    const double TAXIS = 80;                                   // ширина подвижной оси времени, с (как у прежних графиков)
    static readonly string DIR = @"W:\tools\aurora3d\";
    static readonly string FFMPEG = @"W:\ffmpeg\ffmpeg-9.0.1-essentials_build\bin\ffmpeg.exe";
    static readonly string SRC = "rtsp://127.0.0.1:8554/xps_sub";
    static readonly string HA_HOST = "192.168.77.2";
    static readonly string HASS_AGENT_CFG = @"W:\Users\rdpuser\AppData\Local\HASS.Agent\Client\config\appsettings.json";
    const int ONVIF_PORT = 8099;
    static readonly string LOG = DIR + "aurora3d.log";

    static void Log(string s)
    {
        try
        {
            if (File.Exists(LOG) && new FileInfo(LOG).Length > 1 << 20) File.Delete(LOG);
            File.AppendAllText(LOG, DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + "  " + s + "\r\n", Encoding.UTF8);
        }
        catch { }
    }
    static readonly object lk = new object();

    // ---------- грамматики и переключатели ----------
    static readonly string[] GRAM = { "Сигнал", "Вектор", "Свет и Движение", "Линии", "Свечение", "Наборы", "Направления", "События", "Состояния", "Потоки" };
    static readonly Color[] GCOL = { Color.FromArgb(64, 208, 255), Color.FromArgb(255, 96, 208), Color.FromArgb(255, 140, 58), Color.FromArgb(150, 225, 255),
        Color.FromArgb(255, 120, 60), Color.FromArgb(255, 210, 90), Color.FromArgb(230, 240, 255), Color.FromArgb(255, 80, 80), Color.FromArgb(120, 255, 170), Color.FromArgb(170, 150, 255) };
    static readonly Dictionary<string, bool> On = new Dictionary<string, bool>();
    static bool G(string n) { bool v; lock (lk) return On.TryGetValue(n, out v) && v; }
    static void LoadToggles()
    {
        foreach (var n in GRAM) On[n] = true;
        try { foreach (var l in File.ReadAllLines(DIR + "toggles.txt", Encoding.UTF8)) { var p = l.Split('='); if (p.Length == 2 && On.ContainsKey(p[0])) On[p[0]] = p[1].Trim() == "1"; if (p.Length == 2 && p[0] == "Наизнанку") inside = p[1].Trim() == "1"; } } catch { }
    }
    static void SaveToggles()
    {
        try { lock (lk) File.WriteAllLines(DIR + "toggles.txt", GRAM.Select(n => n + "=" + (On[n] ? "1" : "0")).Concat(new[] { "Наизнанку=" + (inside ? "1" : "0") }).ToArray(), Encoding.UTF8); } catch { }
    }

    // ---------- вид 3D-сцен (стрелки/зум PTZ) ----------
    static double yaw = 0, pitch = 0, zoom = 1, vYaw = 0, vPitch = 0, vZoom = 0;

    // ---------- данные ----------
    struct Pt { public DateTime t; public double v; }
    static readonly Dictionary<string, List<Pt>> series = new Dictionary<string, List<Pt>>();
    static void Add(string k, double v, DateTime t)
    {
        List<Pt> l; if (!series.TryGetValue(k, out l)) series[k] = l = new List<Pt>();
        l.Add(new Pt { t = t, v = v });
        int cut = 0; while (cut < l.Count && (t - l[cut].t).TotalSeconds > TAXIS + 5) cut++;
        if (cut > 0) l.RemoveRange(0, cut);
    }
    static double Last(string k, double def) { List<Pt> l; lock (lk) return series.TryGetValue(k, out l) && l.Count > 0 ? l[l.Count - 1].v : def; }
    struct Ev { public DateTime t; public string src; public Color c; }
    static readonly List<Ev> events = new List<Ev>();
    static void Event(string src, Color c) { lock (lk) { events.Add(new Ev { t = DateTime.Now, src = src, c = c }); events.RemoveAll(e => (DateTime.Now - e.t).TotalSeconds > TAXIS + 5); } }
    class SetSlice { public DateTime t; public double[] v; }
    static readonly List<SetSlice> coresHist = new List<SetSlice>(), elecHist = new List<SetSlice>();
    static string[] elecNames = new string[0];
    static Dictionary<string, object> mq = new Dictionary<string, object>();   // последнее сообщение HA
    static DateTime mqT = DateTime.MinValue;
    static readonly Dictionary<string, double> flows = new Dictionary<string, double>();

    // ---------- кадр: история для водопада/тоннеля ----------
    class Slice
    {
        public DateTime t; public float[] mean = new float[IW]; public float[,] uv = new float[UVB, UVB];
        // вершины среза строятся один раз (z = 0); вглубь срез уводит glTranslatef, затухание — туман OpenGL
        public float[] wfV, wfC, tnV, tnC;
    }
    static readonly LinkedList<Slice> hist = new LinkedList<Slice>();
    static float[,] frontDist = new float[IW, YBINS];
    static volatile bool haveInput = false;
    static double motX = 0, motY = 0;                          // сглаженное направление движения в кадре

    static void InputLoop()
    {
        int fsz = IW * IH * 3; byte[] prevY = null; double pcx = -1, pcy = -1;
        while (true)
        {
            try
            {
                var psi = new ProcessStartInfo(FFMPEG, "-hide_banner -loglevel error -rtsp_transport tcp -i " + SRC +
                    " -vf fps=25,scale=" + IW + ":" + IH + ",format=yuv444p -f rawvideo -")
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
                        haveInput = true; var now = DateTime.Now;
                        var dist = new float[IW, YBINS]; double ysum = 0;
                        for (int x = 0; x < IW; x++)
                        {
                            float s = 0;
                            for (int y = 0; y < IH; y++) { int Y = buf[y * IW + x]; s += Y; dist[x, Y * YBINS / 256] += 1; }
                            acc.mean[x] += s / IH; ysum += s;
                        }
                        int pu = IW * IH, pv = 2 * IW * IH;
                        for (int i = 0; i < IW * IH; i++)
                        {
                            int u = buf[pu + i] - 128, v = buf[pv + i] - 128;
                            if (u < -32 || u >= 32 || v < -32 || v >= 32) continue;
                            acc.uv[(u + 32) * UVB / 64, (v + 32) * UVB / 64] += 1;
                        }
                        // Свет (YAVG) и Движение (YDIF — средний модуль разницы с прошлым кадром) + центр движения
                        double ydif = 0, mx = 0, my = 0, mn = 0;
                        if (prevY != null)
                            for (int i = 0; i < IW * IH; i++)
                            {
                                int d = Math.Abs(buf[i] - prevY[i]); ydif += d;
                                if (d > 14) { mx += i % IW; my += i / IW; mn++; }
                            }
                        if (prevY == null) prevY = new byte[IW * IH];
                        Buffer.BlockCopy(buf, 0, prevY, 0, IW * IH);
                        if (mn > 20)
                        {
                            double cx = mx / mn / IW, cy = my / mn / IH;
                            if (pcx >= 0) { motX = motX * 0.8 + (cx - pcx) * 0.2 * 25; motY = motY * 0.8 + (cy - pcy) * 0.2 * 25; }   // доли кадра/с при 25 к/с
                            pcx = cx; pcy = cy;
                        }
                        else { motX *= 0.9; motY *= 0.9; pcx = -1; }
                        accN++;
                        lock (lk)
                        {
                            frontDist = dist;
                            Add("light", ysum / (IW * IH), now); Add("motion", ydif / (IW * IH), now);
                            Add("mot_x", motX, now); Add("mot_y", motY, now);
                        }
                        if ((now - accT).TotalSeconds >= SLICE_SEC)
                        {
                            for (int x = 0; x < IW; x++) acc.mean[x] /= accN;
                            acc.t = now;
                            lock (lk) { hist.AddFirst(acc); while (hist.Count > SLICES) hist.RemoveLast(); }
                            acc = new Slice(); accN = 0; accT = now;
                        }
                    }
                }
            }
            catch (Exception e) { haveInput = false; Log("вход: " + e.Message + " — повтор через 3 с"); Thread.Sleep(3000); }
        }
    }

    // ---------- MQTT: минимальный клиент 3.1.1 (подписка, QoS 0) ----------
    static void WStr(List<byte> b, string s) { var d = Encoding.UTF8.GetBytes(s); b.Add((byte)(d.Length >> 8)); b.Add((byte)d.Length); b.AddRange(d); }
    static byte[] Pkt(byte hdr, List<byte> body)
    {
        var r = new List<byte> { hdr }; int n = body.Count;
        do { byte d = (byte)(n % 128); n /= 128; if (n > 0) d |= 128; r.Add(d); } while (n > 0);
        r.AddRange(body); return r.ToArray();
    }
    static void ReadFull(Stream s, byte[] b, int n) { int g = 0; while (g < n) { int k = s.Read(b, g, n - g); if (k <= 0) throw new IOException("MQTT: соединение закрыто"); g += k; } }
    static void MqttLoop()
    {
        var js = new JavaScriptSerializer();
        while (true)
        {
            try
            {
                var cfg = js.DeserializeObject(File.ReadAllText(HASS_AGENT_CFG, Encoding.UTF8)) as Dictionary<string, object>;
                string host = (string)cfg["MqttAddress"], user = (string)cfg["MqttUsername"], pass = (string)cfg["MqttPassword"];
                int port = Convert.ToInt32(cfg["MqttPort"]);
                using (var tc = new TcpClient(host, port))
                {
                    var s = tc.GetStream(); var wl = new object();
                    var c = new List<byte>(); WStr(c, "MQTT"); c.Add(4); c.Add(0xC2); c.Add(0); c.Add(60);
                    WStr(c, "aurora3d-xps"); WStr(c, user); WStr(c, pass);
                    var pk = Pkt(0x10, c); s.Write(pk, 0, pk.Length);
                    var ack = new byte[4]; ReadFull(s, ack, 4);
                    if (ack[0] != 0x20 || ack[3] != 0) throw new Exception("MQTT CONNACK код " + ack[3]);
                    var sb = new List<byte> { 0, 1 }; WStr(sb, "alena/aurora/data"); sb.Add(0);
                    pk = Pkt(0x82, sb); s.Write(pk, 0, pk.Length);
                    Log("MQTT: подключено к " + host);
                    var ping = new Timer(_ => { try { lock (wl) s.Write(new byte[] { 0xC0, 0 }, 0, 2); } catch { } }, null, 30000, 30000);
                    try
                    {
                        while (true)
                        {
                            var h1 = new byte[1]; ReadFull(s, h1, 1);
                            int len = 0, mul = 1; var db = new byte[1];
                            do { ReadFull(s, db, 1); len += (db[0] & 127) * mul; mul *= 128; } while ((db[0] & 128) != 0);
                            var body = new byte[len]; ReadFull(s, body, len);
                            if ((h1[0] & 0xF0) != 0x30) continue;
                            int tl = (body[0] << 8) | body[1], off = 2 + tl; if (((h1[0] >> 1) & 3) > 0) off += 2;
                            var msg = js.DeserializeObject(Encoding.UTF8.GetString(body, off, len - off)) as Dictionary<string, object>;
                            if (msg != null) OnHa(msg);
                        }
                    }
                    finally { ping.Dispose(); }
                }
            }
            catch (Exception e) { Log("MQTT: " + e.Message + " — повтор через 5 с"); Thread.Sleep(5000); }
        }
    }
    static double D(Dictionary<string, object> m, string k, double def)
    {
        object o; if (m == null || !m.TryGetValue(k, out o) || o == null) return def;
        try { return Convert.ToDouble(o, System.Globalization.CultureInfo.InvariantCulture); } catch { return def; }
    }
    static bool B(Dictionary<string, object> m, string k) { object o; return m != null && m.TryGetValue(k, out o) && o is bool && (bool)o; }
    static readonly Color[] CAMC = { Color.FromArgb(64, 208, 255), Color.FromArgb(255, 140, 58), Color.FromArgb(120, 255, 170), Color.FromArgb(255, 96, 208), Color.FromArgb(255, 230, 90), Color.FromArgb(180, 160, 255), Color.FromArgb(255, 90, 90) };
    static void OnHa(Dictionary<string, object> m)
    {
        Dictionary<string, object> old; lock (lk) { old = mq; mq = m; mqT = DateTime.Now; }
        // события: рост числа людей на камере, новая SMS, падение VPN
        var pn = m.ContainsKey("persons") ? m["persons"] as Dictionary<string, object> : null;
        var po = old.ContainsKey("persons") ? old["persons"] as Dictionary<string, object> : null;
        if (pn != null && po != null)
        {
            int i = 0;
            foreach (var kv in pn) { if (D(pn, kv.Key, 0) > D(po, kv.Key, 0)) Event("человек · " + kv.Key, CAMC[i % CAMC.Length]); i++; }
        }
        object sn, so;
        if (m.TryGetValue("sms_changed", out sn) && old.TryGetValue("sms_changed", out so) && !Equals(sn, so)) Event("SMS", Color.FromArgb(255, 230, 90));
        if (old.ContainsKey("vpn") && B(old, "vpn") && !B(m, "vpn")) Event("VPN упал", Color.FromArgb(255, 150, 60));
        var el = m.ContainsKey("elec") ? m["elec"] as Dictionary<string, object> : null;
        if (el != null) lock (lk) elecNames = el.Keys.ToArray();
    }

    // ---------- локальные датчики XPS, раз в секунду ----------
    static void SamplerLoop()
    {
        PerformanceCounter disk = null; var nets = new List<PerformanceCounter>();
        try
        {
            disk = new PerformanceCounter("PhysicalDisk", "Disk Write Bytes/sec", "_Total");
            foreach (var n in new PerformanceCounterCategory("Network Interface").GetInstanceNames()) nets.Add(new PerformanceCounter("Network Interface", "Bytes Total/sec", n));
        }
        catch (Exception e) { Log("счётчики: " + e.Message); }
        var js = new JavaScriptSerializer(); var pinger = new System.Net.NetworkInformation.Ping(); bool pingOk = true;
        while (true)
        {
            var now = DateTime.Now;
            try
            {
                double[] cores = null; double pkg = double.NaN;
                try
                {
                    var t = js.DeserializeObject(File.ReadAllText(@"W:\ThermalLog\latest.json")) as Dictionary<string, object>;
                    pkg = D(t, "cpu_package_c", double.NaN);
                    var ca = t["cpu_cores_c"] as object[]; if (ca != null) cores = ca.Select(x => Convert.ToDouble(x, System.Globalization.CultureInfo.InvariantCulture)).ToArray();
                }
                catch { }
                double net = 0; foreach (var c in nets) try { net += c.NextValue(); } catch { }
                double dw = 0; try { if (disk != null) dw = disk.NextValue(); } catch { }
                bool ok; try { ok = pinger.Send(HA_HOST, 800).Status == System.Net.NetworkInformation.IPStatus.Success; } catch { ok = false; }
                if (!ok && pingOk) Event("нет связи с HA", Color.FromArgb(255, 70, 70));
                pingOk = ok;
                bool locked = Process.GetProcessesByName("LogonUI").Length > 0;
                Dictionary<string, object> m; DateTime mt; lock (lk) { m = mq; mt = mqT; }
                bool fresh = (now - mt).TotalSeconds < 20;
                lock (lk)
                {
                    if (!double.IsNaN(pkg)) Add("cpu_pkg", pkg, now);
                    if (cores != null) { coresHist.Insert(0, new SetSlice { t = now, v = cores }); if (coresHist.Count > 21) coresHist.RemoveAt(21); }
                    if (fresh)
                    {
                        Add("t_out", D(m, "t_out", double.NaN), now); Add("power_kw", D(m, "power_kw", double.NaN), now);
                        Add("det_fps", D(m, "det_fps", 0), now);
                        Add("wind_b", D(m, "wind_bearing", 0), now); Add("wind_s", D(m, "wind_speed", 0), now);
                        Add("st_andrey", B(m, "andrey_out") ? 1 : 0, now); Add("st_boiler", B(m, "boiler") ? 1 : 0, now); Add("st_vpn", B(m, "vpn") ? 1 : 0, now);
                        var el = m.ContainsKey("elec") ? m["elec"] as Dictionary<string, object> : null;
                        if (el != null) { elecHist.Insert(0, new SetSlice { t = now, v = elecNames.Select(n => D(el, n, 0)).ToArray() }); if (elecHist.Count > 21) elecHist.RemoveAt(21); }
                        flows["WAN Оптиплекса"] = D(m, "opt_wan_rx", 0); flows["VPN телефона"] = D(m, "vpn_down_kbs", 0) * 1024;
                    }
                    Add("st_locked", locked ? 1 : 0, now);
                    flows["Сеть XPS"] = net; flows["Диск XPS"] = dw;
                }
            }
            catch (Exception e) { Log("датчики: " + e.Message); }
            Thread.Sleep(Math.Max(100, 1000 - (int)(DateTime.Now - now).TotalMilliseconds));
        }
    }

    // ---------- ONVIF (поддельный PTZ для кнопок во Frigate) ----------
    static readonly string[] PRESETS = GRAM.Concat(new[] { "Всё включить", "Всё выключить", "Сброс вида", "Наизнанку" }).ToArray();
    const string NS = "xmlns:s=\"http://www.w3.org/2003/05/soap-envelope\" xmlns:tds=\"http://www.onvif.org/ver10/device/wsdl\" " +
        "xmlns:trt=\"http://www.onvif.org/ver10/media/wsdl\" xmlns:tptz=\"http://www.onvif.org/ver20/ptz/wsdl\" xmlns:tt=\"http://www.onvif.org/ver10/schema\"";
    const string VSP = "http://www.onvif.org/ver10/tptz/PanTiltSpaces/VelocityGenericSpace", VSZ = "http://www.onvif.org/ver10/tptz/ZoomSpaces/VelocityGenericSpace";
    static string OnvifReply(string req, string baseUrl)
    {
        string body;
        if (req.Contains("GetCapabilities"))
            body = "<tds:GetCapabilitiesResponse><tds:Capabilities><tt:Device><tt:XAddr>" + baseUrl + "device_service</tt:XAddr></tt:Device>" +
                "<tt:Media><tt:XAddr>" + baseUrl + "media_service</tt:XAddr><tt:StreamingCapabilities><tt:RTPMulticast>false</tt:RTPMulticast><tt:RTP_TCP>true</tt:RTP_TCP><tt:RTP_RTSP_TCP>true</tt:RTP_RTSP_TCP></tt:StreamingCapabilities></tt:Media>" +
                "<tt:PTZ><tt:XAddr>" + baseUrl + "ptz_service</tt:XAddr></tt:PTZ></tds:Capabilities></tds:GetCapabilitiesResponse>";
        else if (req.Contains("GetSystemDateAndTime"))
        {
            var u = DateTime.UtcNow;
            body = "<tds:GetSystemDateAndTimeResponse><tds:SystemDateAndTime><tt:DateTimeType>NTP</tt:DateTimeType><tt:DaylightSavings>false</tt:DaylightSavings>" +
                "<tt:UTCDateTime><tt:Time><tt:Hour>" + u.Hour + "</tt:Hour><tt:Minute>" + u.Minute + "</tt:Minute><tt:Second>" + u.Second + "</tt:Second></tt:Time>" +
                "<tt:Date><tt:Year>" + u.Year + "</tt:Year><tt:Month>" + u.Month + "</tt:Month><tt:Day>" + u.Day + "</tt:Day></tt:Date></tt:UTCDateTime></tds:SystemDateAndTime></tds:GetSystemDateAndTimeResponse>";
        }
        else if (req.Contains("GetDeviceInformation"))
            body = "<tds:GetDeviceInformationResponse><tds:Manufacturer>Alena</tds:Manufacturer><tds:Model>Aurora3D</tds:Model><tds:FirmwareVersion>1</tds:FirmwareVersion><tds:SerialNumber>xps</tds:SerialNumber><tds:HardwareId>xps</tds:HardwareId></tds:GetDeviceInformationResponse>";
        else if (req.Contains("GetProfiles"))
            body = "<trt:GetProfilesResponse><trt:Profiles token=\"aurora\" fixed=\"true\"><tt:Name>aurora</tt:Name>" +
                "<tt:VideoSourceConfiguration token=\"vsc\"><tt:Name>vsc</tt:Name><tt:UseCount>1</tt:UseCount><tt:SourceToken>vs</tt:SourceToken><tt:Bounds x=\"0\" y=\"0\" width=\"1920\" height=\"1080\"/></tt:VideoSourceConfiguration>" +
                "<tt:VideoEncoderConfiguration token=\"vec\"><tt:Name>vec</tt:Name><tt:UseCount>1</tt:UseCount><tt:Encoding>H264</tt:Encoding><tt:Resolution><tt:Width>1920</tt:Width><tt:Height>1080</tt:Height></tt:Resolution><tt:Quality>5</tt:Quality>" +
                "<tt:RateControl><tt:FrameRateLimit>25</tt:FrameRateLimit><tt:EncodingInterval>1</tt:EncodingInterval><tt:BitrateLimit>6000</tt:BitrateLimit></tt:RateControl><tt:SessionTimeout>PT60S</tt:SessionTimeout></tt:VideoEncoderConfiguration>" +
                "<tt:PTZConfiguration token=\"ptzc\"><tt:Name>ptz</tt:Name><tt:UseCount>1</tt:UseCount><tt:NodeToken>node</tt:NodeToken>" +
                "<tt:DefaultContinuousPanTiltVelocitySpace>" + VSP + "</tt:DefaultContinuousPanTiltVelocitySpace><tt:DefaultContinuousZoomVelocitySpace>" + VSZ + "</tt:DefaultContinuousZoomVelocitySpace>" +
                "<tt:DefaultPTZSpeed><tt:PanTilt x=\"0.5\" y=\"0.5\"/><tt:Zoom x=\"0.5\"/></tt:DefaultPTZSpeed><tt:DefaultPTZTimeout>PT5S</tt:DefaultPTZTimeout></tt:PTZConfiguration>" +
                "</trt:Profiles></trt:GetProfilesResponse>";
        else if (req.Contains("GetVideoSources"))
            body = "<trt:GetVideoSourcesResponse><trt:VideoSources token=\"vs\"><tt:Framerate>25</tt:Framerate><tt:Resolution><tt:Width>1920</tt:Width><tt:Height>1080</tt:Height></tt:Resolution></trt:VideoSources></trt:GetVideoSourcesResponse>";
        else if (req.Contains("GetConfigurationOptions"))
            body = "<tptz:GetConfigurationOptionsResponse><tptz:PTZConfigurationOptions><tt:Spaces>" +
                "<tt:ContinuousPanTiltVelocitySpace><tt:URI>" + VSP + "</tt:URI><tt:XRange><tt:Min>-1</tt:Min><tt:Max>1</tt:Max></tt:XRange><tt:YRange><tt:Min>-1</tt:Min><tt:Max>1</tt:Max></tt:YRange></tt:ContinuousPanTiltVelocitySpace>" +
                "<tt:ContinuousZoomVelocitySpace><tt:URI>" + VSZ + "</tt:URI><tt:XRange><tt:Min>-1</tt:Min><tt:Max>1</tt:Max></tt:XRange></tt:ContinuousZoomVelocitySpace>" +
                "</tt:Spaces><tt:PTZTimeout><tt:Min>PT1S</tt:Min><tt:Max>PT60S</tt:Max></tt:PTZTimeout></tptz:PTZConfigurationOptions></tptz:GetConfigurationOptionsResponse>";
        else if (req.Contains("GetPresets"))
        {
            var sb = new StringBuilder("<tptz:GetPresetsResponse>");
            for (int i = 0; i < PRESETS.Length; i++) sb.Append("<tptz:Preset token=\"p" + i + "\"><tt:Name>" + PRESETS[i] + "</tt:Name></tptz:Preset>");
            body = sb.Append("</tptz:GetPresetsResponse>").ToString();
        }
        else if (req.Contains("GetServiceCapabilities"))
            body = "<tptz:GetServiceCapabilitiesResponse><tptz:Capabilities MoveStatus=\"true\" StatusPosition=\"true\"/></tptz:GetServiceCapabilitiesResponse>";
        else if (req.Contains("GetStatus"))
            body = "<tptz:GetStatusResponse><tptz:PTZStatus><tt:Position><tt:PanTilt x=\"0\" y=\"0\" space=\"http://www.onvif.org/ver10/tptz/PanTiltSpaces/PositionGenericSpace\"/>" +
                "<tt:Zoom x=\"0\" space=\"http://www.onvif.org/ver10/tptz/ZoomSpaces/PositionGenericSpace\"/></tt:Position><tt:MoveStatus><tt:PanTilt>IDLE</tt:PanTilt><tt:Zoom>IDLE</tt:Zoom></tt:MoveStatus>" +
                "<tt:UtcTime>" + DateTime.UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ") + "</tt:UtcTime></tptz:PTZStatus></tptz:GetStatusResponse>";
        else if (req.Contains("ContinuousMove"))
        {
            var mp = Regex.Match(req, "PanTilt[^>]*?x=\"([-0-9.eE]+)\"[^>]*?y=\"([-0-9.eE]+)\""); var mz = Regex.Match(req, "Zoom[^>]*?x=\"([-0-9.eE]+)\"");
            var ci = System.Globalization.CultureInfo.InvariantCulture;
            lock (lk)
            {
                vYaw = mp.Success ? double.Parse(mp.Groups[1].Value, ci) : 0; vPitch = mp.Success ? double.Parse(mp.Groups[2].Value, ci) : 0;
                vZoom = mz.Success ? double.Parse(mz.Groups[1].Value, ci) : 0;
            }
            body = "<tptz:ContinuousMoveResponse/>";
        }
        else if (req.Contains("Stop"))
        {
            lock (lk) { vYaw = vPitch = vZoom = 0; }
            body = "<tptz:StopResponse/>";
        }
        else if (req.Contains("GotoPreset"))
        {
            var mt = Regex.Match(req, "PresetToken>\\s*p(\\d+)\\s*<");
            if (mt.Success) Preset(int.Parse(mt.Groups[1].Value));
            body = "<tptz:GotoPresetResponse/>";
        }
        else return null;
        return "<?xml version=\"1.0\" encoding=\"UTF-8\"?><s:Envelope " + NS + "><s:Body>" + body + "</s:Body></s:Envelope>";
    }
    static void Preset(int i)
    {
        if (i < 0 || i >= PRESETS.Length) return;
        string n = PRESETS[i];
        lock (lk)
        {
            if (On.ContainsKey(n)) On[n] = !On[n];
            else if (n == "Всё включить") foreach (var g in GRAM) On[g] = true;
            else if (n == "Всё выключить") foreach (var g in GRAM) On[g] = false;
            else if (n == "Сброс вида") { yaw = pitch = 0; zoom = 1; }
            else if (n == "Наизнанку") inside = !inside;
        }
        SaveToggles(); Log("кнопка: " + n);
    }
    static void OnvifLoop()
    {
        while (true)
        {
            try
            {
                var hl = new HttpListener(); hl.Prefixes.Add("http://+:" + ONVIF_PORT + "/onvif/"); hl.Start();
                Log("ONVIF: слушаю порт " + ONVIF_PORT);
                while (true)
                {
                    var ctx = hl.GetContext();
                    ThreadPool.QueueUserWorkItem(_ =>
                    {
                        try
                        {
                            string req; using (var r = new StreamReader(ctx.Request.InputStream, Encoding.UTF8)) req = r.ReadToEnd();
                            string baseUrl = "http://" + ctx.Request.Url.Host + ":" + ONVIF_PORT + "/onvif/";
                            string rep = OnvifReply(req, baseUrl);
                            if (rep == null)
                            {
                                ctx.Response.StatusCode = 500;
                                rep = "<?xml version=\"1.0\" encoding=\"UTF-8\"?><s:Envelope " + NS + "><s:Body><s:Fault><s:Code><s:Value>s:Receiver</s:Value></s:Code><s:Reason><s:Text xml:lang=\"en\">not supported</s:Text></s:Reason></s:Fault></s:Body></s:Envelope>";
                                var m = Regex.Match(req, "<[a-zA-Z0-9]+:Body[^>]*>\\s*<([^ >]+)"); Log("ONVIF: не поддержано " + (m.Success ? m.Groups[1].Value : "?"));
                            }
                            var b = Encoding.UTF8.GetBytes(rep);
                            ctx.Response.ContentType = "application/soap+xml; charset=utf-8"; ctx.Response.ContentLength64 = b.Length;
                            ctx.Response.OutputStream.Write(b, 0, b.Length); ctx.Response.Close();
                        }
                        catch (Exception e) { Log("ONVIF запрос: " + e.Message); try { ctx.Response.Abort(); } catch { } }
                    });
                }
            }
            catch (Exception e) { Log("ONVIF: " + e.Message + " — повтор через 10 с"); Thread.Sleep(10000); }
        }
    }

    // ---------- матрицы ----------
    static float[] Persp(double fovDeg, double asp, double n, double f)
    {
        double t = 1 / Math.Tan(fovDeg * Math.PI / 360); var m = new float[16];
        m[0] = (float)(t / asp); m[5] = (float)t; m[10] = (float)((f + n) / (n - f)); m[11] = -1; m[14] = (float)(2 * f * n / (n - f));
        return m;
    }
    static float[] Ortho(double w, double h)
    {
        var m = new float[16]; m[0] = (float)(2 / w); m[5] = (float)(-2 / h); m[10] = -1; m[12] = -1; m[13] = 1; m[15] = 1; return m;
    }
    static float[] LookAt(double ex, double ey, double ez, double cx, double cy, double cz)
    {
        double fx = cx - ex, fy = cy - ey, fz = cz - ez, fl = Math.Sqrt(fx * fx + fy * fy + fz * fz); fx /= fl; fy /= fl; fz /= fl;
        double sx = -fz, sy = 0, sz = fx, sl = Math.Sqrt(sx * sx + sz * sz); sx /= sl; sz /= sl;
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
    static float[] Trans(double x, double y, double z) { var m = new float[16]; m[0] = m[5] = m[10] = m[15] = 1; m[12] = (float)x; m[13] = (float)y; m[14] = (float)z; return m; }
    static float[] RotY(double a) { var m = new float[16]; float c = (float)Math.Cos(a), s = (float)Math.Sin(a); m[0] = c; m[2] = -s; m[8] = s; m[10] = c; m[5] = m[15] = 1; return m; }
    static float[] RotX(double a) { var m = new float[16]; float c = (float)Math.Cos(a), s = (float)Math.Sin(a); m[5] = c; m[6] = s; m[9] = -s; m[10] = c; m[0] = m[15] = 1; return m; }
    // модель: поворот сцены вокруг её центра (стрелки PTZ) — Trans(c)·Ry·Rx·Trans(−c)
    static float[] Orbit(double cx, double cy, double cz) { return Mul(Trans(cx, cy, cz), Mul(RotY(yaw), Mul(RotX(pitch), Trans(-cx, -cy, -cz)))); }
    static bool Proj(float[] mvp, double x, double y, double z, int vx, int vy, int vw, int vh, out float px, out float py)
    {
        double cx = mvp[0] * x + mvp[4] * y + mvp[8] * z + mvp[12], cy = mvp[1] * x + mvp[5] * y + mvp[9] * z + mvp[13], cw = mvp[3] * x + mvp[7] * y + mvp[11] * z + mvp[15];
        px = 0; py = 0; if (cw <= 0.01) return false;
        px = (float)(vx + (cx / cw + 1) / 2 * vw); py = (float)(H - (vy + (cy / cw + 1) / 2 * vh)); return true;
    }
    static void Scene3D(float[] P, float[] Vw, float[] Mo, int vx, int vy, int vw, int vh)
    {
        GL.glViewport(vx, vy, vw, vh); GL.glMatrixMode(0x1701); GL.glLoadMatrixf(P); GL.glMatrixMode(0x1700); GL.glLoadMatrixf(Mul(Vw, Mo));
    }
    static void Scene2D() { GL.glViewport(0, 0, W, H); GL.glMatrixMode(0x1701); GL.glLoadMatrixf(Ortho(W, H)); GL.glMatrixMode(0x1700); GL.glLoadMatrixf(Trans(0, 0, 0)); }

    // ---------- примитивы ----------
    static readonly List<float> vb = new List<float>(), cb = new List<float>();
    static void V(double x, double y, double z, double r, double g, double b) { vb.Add((float)x); vb.Add((float)y); vb.Add((float)z); cb.Add((float)r); cb.Add((float)g); cb.Add((float)b); cb.Add(1); }
    static void Vc(double x, double y, Color c, double k) { V(x, y, 0, c.R / 255.0 * k, c.G / 255.0 * k, c.B / 255.0 * k); }
    static void Flush(uint mode)
    {
        if (vb.Count == 0) return;
        var va = vb.ToArray(); var ca = cb.ToArray();
        GL.glVertexPointer(3, 0x1406, 0, va); GL.glColorPointer(4, 0x1406, 0, ca); GL.glDrawArrays(mode, 0, va.Length / 3);
        vb.Clear(); cb.Clear();
    }
    struct Label { public string s; public float x, y; public Color c; public int align; public bool big; }
    static readonly List<Label> labels = new List<Label>();
    static void L(string s, float x, float y, Color c, int align) { labels.Add(new Label { s = s, x = x, y = y, c = c, align = align }); }
    static readonly Color cAx = Color.FromArgb(225, 235, 245);
    static readonly System.Globalization.CultureInfo RU = new System.Globalization.CultureInfo("ru-RU");
    static string F(double v, string fmt) { return v.ToString(fmt, RU); }
    static double TX(DateTime t, DateTime now) { return W - (now - t).TotalSeconds / TAXIS * W; }   // x подвижной оси времени

    // ---------- грамматики ----------
    const double WF_D = 4.0, TN_D = 6.0;
    static readonly Color cSig = Color.FromArgb(64, 208, 255), cVec = Color.FromArgb(255, 96, 208);

    // ===== 2026-09-21 (ночь): «тоннель грамматик» (просьба пользователя) =====
    // Веер убран. Все 10 грамматик — 3D и делят круг поровну: десятигранный тоннель, каждая грамматика — своя грань,
    // повёрнутая на свой угол (i·36°) вокруг оси взгляда. «Сейчас» рождается в глубине у центра и наезжает на зрителя,
    // у кромки (z = 0) — 20 с назад; кольца-метки ЧЧ:ММ:СС через 5 с летят на зрителя по всем граням. Выключенная грамматика — пустая грань.
    // Грань в своих координатах: x — поперёк грани [−HW, HW], y = −TR + h·HMAX — высота данных над гранью, z — время.
    // «Наизнанку» (пресет, по умолчанию вкл): труба выворачивается в столб — колонна уже (TR 0,55), данные граней растут
    // из её стенок НАРУЖУ. Делается отражением каждой грани относительно её плоскости в матрице (WallBegin), код грамматик общий.
    static bool inside = true;
    static double TR { get { return inside ? 0.55 : 1.0; } }
    static double HW { get { return TR * Math.Tan(Math.PI / 10); } }
    const double TD = 6.0;
    // «Наизнанку» лучи данных длиннее и уходят за внешний контур (просьба пользователя), а названия/подписи/контур
    // стоят на прежнем радиусе LBR — данные проходят сквозь них к краям кадра
    static double HMAX { get { return inside ? 1.6 : 0.62; } }
    static double LBR { get { return inside ? 0.55 + 0.62 : TR; } }
    static float[] allM; static double spin; static readonly DateTime T0 = DateTime.Now;
    static float[] RotZ(double a) { var m = new float[16]; float c = (float)Math.Cos(a), s = (float)Math.Sin(a); m[0] = c; m[1] = s; m[4] = -s; m[5] = c; m[10] = m[15] = 1; return m; }
    // 21.09 ночь, по просьбе: тоннель НАЕЗЖАЕТ — «сейчас» рождается в глубине (z = −TD) и летит на зрителя, к кромке (z = 0)
    static double ZA(double age) { return -TD + Math.Min(20, age) / 20 * TD; }
    static double WallAng(int i) { return i * 2 * Math.PI / GRAM.Length; }          // 0 — нижняя грань, дальше против часовой
    static void WallBegin(int i)
    {
        GL.glPushMatrix(); GL.glRotatef((float)(WallAng(i) * 180 / Math.PI), 0, 0, 1);
        if (inside) { GL.glTranslatef(0, (float)(-2 * TR), 0); GL.glScalef(1, -1, 1); }   // y → −2·TR − y: данные наружу
    }
    static void WallBeginPlain(int i) { GL.glPushMatrix(); GL.glRotatef((float)(WallAng(i) * 180 / Math.PI), 0, 0, 1); }
    static void WallEnd() { GL.glPopMatrix(); }
    static double Yh(double h) { return -TR + Math.Max(0, Math.Min(1, h)) * HMAX; }
    static double N01(double v, double lo, double hi) { return double.IsNaN(v) ? 0 : Math.Max(0, Math.Min(1, (v - lo) / (hi - lo))); }
    // подпись в координатах грани i
    // Подписи значений и устройств. «Наизнанку» — лучами (просьба пользователя): текстура в плоскости кромки снаружи,
    // в полосе своих данных (x), тянется от столба наружу продолжением луча; на левой половине экрана развёрнута, чтобы
    // читалась к центру. Сверху и снизу лучи могут уходить за кадр — так задумано. В обычной трубе — прежние 2D-подписи.
    struct RayLabel { public int i; public double x; public string s; public Color c; }
    static readonly List<RayLabel> rays = new List<RayLabel>();
    static Font labelFont;
    static void DrawRays()
    {
        if (rays.Count == 0) return;
        var byLane = new Dictionary<string, int>();
        GL.glBlendFunc(0x0302, 1);
        foreach (var r in rays)
        {
            string lane = r.i + "|" + Math.Round(r.x, 3); int k; byLane.TryGetValue(lane, out k); byLane[lane] = k + 1;   // в одной полосе — друг за другом
            var tb = TextBmp(r.s, r.c, labelFont, StringFormat.GenericTypographic); uint tx = TexFor(r.c.ToArgb() + "|" + r.s, tb);
            double h = 0.05, len = h * tb.Width / tb.Height, r0 = LBR + 0.13 + k * 0.02, gap = 0.04;
            double off = 0; foreach (var q in rays) { if (q.Equals(r)) break; if (q.i == r.i && Math.Abs(q.x - r.x) < 1e-3) off += 0.05 * TextBmp(q.s, q.c, labelFont, StringFormat.GenericTypographic).Width / (double)TextBmp(q.s, q.c, labelFont, StringFormat.GenericTypographic).Height + gap; }
            double a = r0 + off, b = a + len, x0 = r.x - h / 2, x1 = r.x + h / 2;
            double outDir = -Math.PI / 2 + WallAng(r.i) + spin;           // направление «наружу» этой грани на экране
            bool flip = Math.Cos(outDir) < -0.05;
            WallBeginPlain(r.i);
            // вершины: начало-низ, конец-низ, конец-верх, начало-верх строки; верх букв — к +x грани (иначе зеркально)
            if (!flip) GL.TexQuad(tx, 0.95f, x0, -a, 0, x0, -b, 0, x1, -b, 0, x1, -a, 0);   // читается наружу
            else GL.TexQuad(tx, 0.95f, x1, -b, 0, x1, -a, 0, x0, -a, 0, x0, -b, 0);         // читается к центру
            WallEnd();
        }
        GL.glBlendFunc(1, 1);
    }
    static void LW(int i, double x, double y, double z, string s, Color c, int align, float dy)
    {
        if (inside) { rays.Add(new RayLabel { i = i, x = x, s = s, c = c }); return; }
        double a = WallAng(i), ca = Math.Cos(a), sa = Math.Sin(a); float px, py;
        if (Proj(allM, x * ca - y * sa, x * sa + y * ca, z, 0, 0, W, H, out px, out py)) L(s, px, py + dy * SCF, c, align);
    }
    static void Vk(double x, double y, double z, Color c, double k) { V(x, y, z, c.R / 255.0 * k, c.G / 255.0 * k, c.B / 255.0 * k); }
    static void HueColor(double u, double v, out double r, out double g, out double b)
    {
        double Y = 150, U = u * 32 * 3, Vv = v * 32 * 3;
        r = Math.Max(0, Math.Min(255, Y + 1.402 * Vv)) / 255; g = Math.Max(0, Math.Min(255, Y - 0.344 * U - 0.714 * Vv)) / 255; b = Math.Max(0, Math.Min(255, Y + 1.772 * U)) / 255;
    }
    static Color TempColor(double t)
    {
        double[] ts = { 45, 65, 80, 95 }; Color[] cs = { Color.FromArgb(60, 110, 255), Color.FromArgb(60, 255, 120), Color.FromArgb(255, 220, 60), Color.FromArgb(255, 50, 50) };
        if (t <= ts[0]) return cs[0]; if (t >= ts[3]) return cs[3];
        for (int i = 0; i < 3; i++) if (t <= ts[i + 1]) { double f = (t - ts[i]) / (ts[i + 1] - ts[i]); return Color.FromArgb((int)(cs[i].R + (cs[i + 1].R - cs[i].R) * f), (int)(cs[i].G + (cs[i + 1].G - cs[i].G) * f), (int)(cs[i].B + (cs[i + 1].B - cs[i].B) * f)); }
        return cs[3];
    }
    static List<Pt> Copy(string k) { List<Pt> l; return series.TryGetValue(k, out l) ? new List<Pt>(l) : new List<Pt>(); }
    // линия во времени по грани: x — полоса, значение → высота; «забор» до грани каждые step точек
    static void WallLine(List<Pt> l, DateTime now, double x, double lo, double hi, Color c, int step)
    {
        for (int j = 0; j + 1 < l.Count; j++)
        {
            double a0 = (now - l[j].t).TotalSeconds, a1 = (now - l[j + 1].t).TotalSeconds; if (a0 > 20) continue;
            double k = 0.25 + 0.7 * (1 - a0 / 20), y0 = Yh(N01(l[j].v, lo, hi)), y1 = Yh(N01(l[j + 1].v, lo, hi));
            Vk(x, y0, ZA(a0), c, k); Vk(x, y1, ZA(a1), c, k);
            if (step > 0 && j % step == 0) { Vk(x, -TR, ZA(a0), c, k * 0.18); Vk(x, y0, ZA(a0), c, k * 0.18); }
        }
    }
    static readonly Dictionary<string, List<double>> particles = new Dictionary<string, List<double>>();
    static readonly Random rnd = new Random();

    // ---------- часы по периметру кадра (вместо бегущего квадратика ffmpeg внизу — просьба пользователя) ----------
    // круг — 60 с по часовой стрелке от середины верхней кромки, риски каждую секунду, длинные с подписью — через 5 с
    static void PerimPt(double f, out double x, out double y, out double nx, out double ny)
    {
        double m = 10 * SC, w = W - 2 * m, h = H - 2 * m, P = 2 * (w + h), d = ((f % 1) + 1) % 1 * P;
        if (d < w / 2) { x = W / 2.0 + d; y = m; nx = 0; ny = 1; return; } d -= w / 2;
        if (d < h) { x = W - m; y = m + d; nx = -1; ny = 0; return; } d -= h;
        if (d < w) { x = W - m - d; y = H - m; nx = 0; ny = -1; return; } d -= w;
        if (d < h) { x = m; y = H - m - d; nx = 1; ny = 0; return; } d -= h;
        x = m + d; y = m; nx = 0; ny = 1;
    }
    static void DrawPerimeterClock(DateTime now)
    {
        Scene2D(); var cY = Color.FromArgb(255, 214, 90);
        for (int s = 0; s < 60; s++)
        {
            double x, y, nx, ny; PerimPt(s / 60.0, out x, out y, out nx, out ny); double l = (s % 5 == 0 ? 16 : 7) * SC;
            Vk(x, y, 0, cAx, s % 5 == 0 ? 0.55 : 0.3); Vk(x + nx * l, y + ny * l, 0, cAx, s % 5 == 0 ? 0.55 : 0.3);
            if (s % 5 == 0) L(s.ToString("00"), (float)(x + nx * 30 * SC), (float)(y + ny * 30 * SC - 7 * SC), cAx, 2);
        }
        GL.glLineWidth(1.5f * SCF); Flush(0x0001);
        double f = (now.Second + now.Millisecond / 1000.0) / 60.0, q = 7 * SC;
        for (int k = 8; k >= 0; k--)                                                 // хвост 0,8 с, затухает
        {
            double x, y, nx, ny; PerimPt(f - k * 0.1 / 60.0, out x, out y, out nx, out ny); double kk = k == 0 ? 1 : 0.5 * (1 - k / 9.0);
            Vk(x - q, y - q, 0, cY, kk); Vk(x + q, y - q, 0, cY, kk); Vk(x + q, y + q, 0, cY, kk); Vk(x - q, y + q, 0, cY, kk);
        }
        Flush(0x0007);
    }
    static void DrawAll(List<Slice> hs, float[,] dist, DateTime now, double dt)
    {
        // многогранник медленно крутится вокруг оси взгляда (просьба пользователя): оборот за 90 с
        spin = (DateTime.Now - T0).TotalSeconds / 90.0 * 2 * Math.PI;
        // камера на 2,2 — чтобы подписи снаружи кромки влезали в кадр сверху и снизу
        var P = Persp(62, (double)W / H, 0.05, 40); var Vw = LookAt(0, 0, 2.2 / zoom, 0, 0, -TD); var Mo = Mul(Orbit(0, 0, -TD / 2), RotZ(spin));
        allM = Mul(P, Mul(Vw, Mo)); Scene3D(P, Vw, Mo, 0, 0, W, H);
        int n = GRAM.Length; double cr = TR / Math.Cos(Math.PI / n);
        Func<int, double> corner = j => -Math.PI / 2 + Math.PI / n + j * 2 * Math.PI / n;
        // каркас: рёбра граней вдаль и кольца времени (через 5 с) с метками
        for (int j = 0; j < n; j++) { double a = corner(j); Vk(cr * Math.Cos(a), cr * Math.Sin(a), 0, cAx, 0.22); Vk(cr * Math.Cos(a), cr * Math.Sin(a), -TD, cAx, 0.04); }
        var tick0 = new DateTime(now.Year, now.Month, now.Day, now.Hour, now.Minute, now.Second / 5 * 5);
        for (int ti = -1; ti <= 4; ti++)
        {
            double age = 0; DateTime tt = now;
            if (ti >= 0) { tt = tick0.AddSeconds(-5 * ti); age = (now - tt).TotalSeconds; if (age > 20) continue; }
            double z = ZA(age), k = ti < 0 ? 0.45 : 0.3 * (1 - age / 20) + 0.06;
            for (int j = 0; j < n; j++) { double a0 = corner(j), a1 = corner(j + 1); Vk(cr * Math.Cos(a0), cr * Math.Sin(a0), z, cAx, k); Vk(cr * Math.Cos(a1), cr * Math.Sin(a1), z, cAx, k); }
            if (ti >= 0) { float px, py; if (Proj(allM, 0, inside ? LBR + 0.05 : TR, z, 0, 0, W, H, out px, out py)) L(tt.ToString("HH:mm:ss"), px, py - 14 * SCF, cAx, 2); }
        }
        for (int j = 0; j < n; j++) { double a0 = corner(j), a1 = corner(j + 1); Vk(cr * Math.Cos(a0), cr * Math.Sin(a0), 0, cAx, 0.3); Vk(cr * Math.Cos(a1), cr * Math.Sin(a1), 0, cAx, 0.3); }
        if (inside) { double co = LBR / Math.Cos(Math.PI / n); for (int j = 0; j < n; j++) { double a0 = corner(j), a1 = corner(j + 1); Vk(co * Math.Cos(a0), co * Math.Sin(a0), 0, cAx, 0.1); Vk(co * Math.Cos(a1), co * Math.Sin(a1), 0, cAx, 0.1); } }
        Flush(0x0001);
        // названия грамматик — снаружи многогранника, у внешней кромки своей грани, вдоль её ребра (просьба пользователя):
        // текстура в плоскости кромки (z = 0), верх букв — к центру; крутится вместе с многогранником
        GL.glBlendFunc(0x0302, 1);                                   // GL_SRC_ALPHA, GL_ONE — аддитивно по альфе текстуры
        for (int i = 0; i < n; i++)
        {
            bool on = G(GRAM[i]); var col = on ? GCOL[i] : Color.FromArgb(120, 120, 120);
            string name = GRAM[i] + (on ? "" : " · выкл");
            var tb = TextBmp(name, col, bigFont, StringFormat.GenericTypographic); uint tx = TexFor("big|" + col.ToArgb() + "|" + name, tb);
            double asp = (double)tb.Width / tb.Height, hgt = Math.Min(0.075, 2 * (inside ? LBR * Math.Tan(Math.PI / n) : HW) * 0.95 / asp), len = hgt * asp,
                yt = -LBR - 0.02, yb = yt - hgt;
            // грань в верхней половине экрана — подпись повёрнута на 180°, чтобы читалась, а не вверх ногами
            bool up = Math.Sin(-Math.PI / 2 + WallAng(i) + spin) > 0.05;
            WallBeginPlain(i);
            if (!up) GL.TexQuad(tx, on ? 0.95f : 0.55f, -len / 2, yb, 0, len / 2, yb, 0, len / 2, yt, 0, -len / 2, yt, 0);
            else GL.TexQuad(tx, on ? 0.95f : 0.55f, len / 2, yt, 0, -len / 2, yt, 0, -len / 2, yb, 0, len / 2, yb, 0);
            WallEnd();
        }
        GL.glBlendFunc(1, 1);

        Dictionary<string, List<Pt>> S; lock (lk) S = series.ToDictionary(kv => kv.Key, kv => new List<Pt>(kv.Value));
        Func<string, List<Pt>> Sr = k => S.ContainsKey(k) ? S[k] : new List<Pt>();
        Func<string, double> Lst = k => { var l = Sr(k); return l.Count > 0 ? l[l.Count - 1].v : double.NaN; };

        // 0 Сигнал — рельеф средней яркости по столбцам кадра, спереди — распределение текущего кадра
        if (G(GRAM[0]) && haveInput)
        {
            WallBegin(0); GL.glBlendFunc(0x8001, 1);
            foreach (var s in hs)
            {
                double age = (now - s.t).TotalSeconds; if (age > 20) continue;
                if (s.wfV == null)
                {
                    for (int x = 0; x + 1 < IW; x++) { V(-HW + 2 * HW * x / (IW - 1), Yh(s.mean[x] / 255.0), 0, 0.24, 0.78, 0.95); V(-HW + 2 * HW * (x + 1) / (IW - 1), Yh(s.mean[x + 1] / 255.0), 0, 0.24, 0.78, 0.95); }
                    s.wfV = vb.ToArray(); s.wfC = cb.ToArray(); vb.Clear(); cb.Clear();
                }
                float k = (float)(0.85 * (1 - age / 20) + 0.08); GL.BlendColor(k, k, k, k);
                GL.glPushMatrix(); GL.glTranslatef(0, 0, (float)ZA(age));
                GL.glVertexPointer(3, 0x1406, 0, s.wfV); GL.glColorPointer(4, 0x1406, 0, s.wfC); GL.glDrawArrays(0x0001, 0, s.wfV.Length / 3);
                GL.glPopMatrix();
            }
            GL.glBlendFunc(1, 1);
            if (dist != null) { for (int x = 0; x < IW; x++) for (int b = 0; b < YBINS; b++) { float c = dist[x, b]; if (c > 0) Vk(-HW + 2 * HW * x / (IW - 1), Yh((b + 0.5) / YBINS), ZA(0) + 0.01, GCOL[0], Math.Min(0.8, 0.12 + c / 20.0)); } GL.glPointSize(2 * SCF); Flush(0x0000); }
            WallEnd();
            LW(0, HW, Yh(1), 0, "Y 255", GCOL[0], 0, -6);
        }
        // 1 Вектор — точки цвета кадра: поперёк грани U, высота V
        if (G(GRAM[1]) && haveInput)
        {
            WallBegin(1); GL.glBlendFunc(0x8001, 1); GL.glPointSize(2 * SCF);
            foreach (var s in hs)
            {
                double age = (now - s.t).TotalSeconds; if (age > 20) continue;
                if (s.tnV == null)
                {
                    for (int i = 0; i < UVB; i++) for (int j = 0; j < UVB; j++)
                        {
                            float c = s.uv[i, j]; if (c <= 0) continue;
                            double u = (i + 0.5) / UVB * 2 - 1, v = (j + 0.5) / UVB * 2 - 1, r, g, b; HueColor(u, v, out r, out g, out b);
                            double k = Math.Min(0.9, 0.1 + c / 40.0); V(u * HW, Yh((v + 1) / 2), 0, r * k, g * k, b * k);
                        }
                    s.tnV = vb.ToArray(); s.tnC = cb.ToArray(); vb.Clear(); cb.Clear();
                }
                if (s.tnV.Length == 0) continue;
                float kf = (float)(0.12 + 0.88 * (1 - age / 20)); GL.BlendColor(kf, kf, kf, kf);
                GL.glPushMatrix(); GL.glTranslatef(0, 0, (float)ZA(age));
                GL.glVertexPointer(3, 0x1406, 0, s.tnV); GL.glColorPointer(4, 0x1406, 0, s.tnC); GL.glDrawArrays(0x0000, 0, s.tnV.Length / 3);
                GL.glPopMatrix();
            }
            GL.glBlendFunc(1, 1);
            // рамка окна U/V ±32 на кромке
            double zn = ZA(0);
            Vk(-HW, Yh(0), zn, GCOL[1], 0.4); Vk(HW, Yh(0), zn, GCOL[1], 0.4); Vk(-HW, Yh(1), zn, GCOL[1], 0.4); Vk(HW, Yh(1), zn, GCOL[1], 0.4);
            Vk(0, Yh(0), zn, GCOL[1], 0.25); Vk(0, Yh(1), zn, GCOL[1], 0.25); Flush(0x0001);
            WallEnd();
            LW(1, HW, Yh(1), 0, "U →, V ↑", GCOL[1], 0, -6);
        }
        // 2 Свет и Движение
        if (G(GRAM[2]))
        {
            WallBegin(2);
            WallLine(Sr("light"), now, -HW * 0.45, 0, 255, Color.FromArgb(255, 140, 58), 6);
            WallLine(Sr("motion"), now, HW * 0.45, 0, 24, Color.FromArgb(96, 255, 128), 6);
            GL.glLineWidth(2 * SCF); Flush(0x0001); GL.glLineWidth(1 * SCF); WallEnd();
            LW(2, -HW * 0.45, Yh(N01(Lst("light"), 0, 255)), 0, "свет " + F(Lst("light"), "0"), Color.FromArgb(255, 170, 110), 2, -16);
            LW(2, HW * 0.45, Yh(N01(Lst("motion"), 0, 24)), 0, "движ. " + F(Lst("motion"), "0.#"), Color.FromArgb(120, 255, 150), 2, -16);
        }
        // 3 Линии — улица, потребление дома
        if (G(GRAM[3]))
        {
            WallBegin(3);
            WallLine(Sr("t_out"), now, -HW * 0.45, 10, 40, Color.FromArgb(150, 225, 255), 2);
            WallLine(Sr("power_kw"), now, HW * 0.45, 0, 8, Color.FromArgb(255, 205, 90), 2);
            GL.glLineWidth(2 * SCF); Flush(0x0001); GL.glLineWidth(1 * SCF); WallEnd();
            LW(3, -HW * 0.45, Yh(N01(Lst("t_out"), 10, 40)), 0, "улица " + F(Lst("t_out"), "0.#") + " °C", Color.FromArgb(150, 225, 255), 2, -16);
            LW(3, HW * 0.45, Yh(N01(Lst("power_kw"), 0, 8)), 0, "дом " + F(Lst("power_kw"), "0.##") + " кВт", Color.FromArgb(255, 205, 90), 2, -16);
        }
        // 4 Свечение — полоса цвета температуры CPU XPS (левая половина) и нагрузки детектора Frigate (правая)
        if (G(GRAM[4]))
        {
            WallBegin(4);
            var pk = Sr("cpu_pkg"); var df = Sr("det_fps");
            for (int j = 0; j + 1 < pk.Count; j++)
            {
                double a0 = (now - pk[j].t).TotalSeconds, a1 = (now - pk[j + 1].t).TotalSeconds; if (a0 > 20) continue;
                var c = TempColor(pk[j].v); double k = 0.2 + 0.6 * (1 - a0 / 20), y = -TR + 0.01 + N01(pk[j].v, 40, 103) * 0.25;
                Vk(-HW, y, ZA(a0), c, k); Vk(-0.02, y, ZA(a0), c, k); Vk(-0.02, y, ZA(a1), c, k); Vk(-HW, y, ZA(a1), c, k);
            }
            for (int j = 0; j + 1 < df.Count; j++)
            {
                double a0 = (now - df[j].t).TotalSeconds, a1 = (now - df[j + 1].t).TotalSeconds; if (a0 > 20) continue;
                var c = Color.FromArgb(255, 80, 230); double k = (0.15 + 0.6 * (1 - a0 / 20)) * Math.Max(0.15, N01(df[j].v, 0, 30)), y = -TR + 0.01;
                Vk(0.02, y, ZA(a0), c, k); Vk(HW, y, ZA(a0), c, k); Vk(HW, y, ZA(a1), c, k); Vk(0.02, y, ZA(a1), c, k);
            }
            Flush(0x0007); WallEnd();
            double t = Lst("cpu_pkg");
            LW(4, -HW * 0.5, -TR + 0.3, 0, "CPU " + F(t, "0.#") + " °C · до TjMax " + F(103 - t, "0.#"), TempColor(t), 2, -8);
            LW(4, HW * 0.5, -TR + 0.18, 0, "детектор " + F(Lst("det_fps"), "0.#") + " к/с", Color.FromArgb(255, 80, 230), 2, -8);
        }
        // 5 Наборы — столбики: 4 ядра XPS, 8 каналов электричества
        if (G(GRAM[5]))
        {
            List<SetSlice> ch, eh; string[] en; lock (lk) { ch = new List<SetSlice>(coresHist); eh = new List<SetSlice>(elecHist); en = elecNames; }
            WallBegin(5);
            Action<List<SetSlice>, double, double, Func<double, double>, Color> bars = (hl, x0, xw, norm, c) =>
            {
                foreach (var s in hl)
                {
                    double age = (now - s.t).TotalSeconds; if (age > 20 || s.v == null) continue; double k = 0.2 + 0.7 * (1 - age / 20);
                    for (int j = 0; j < s.v.Length; j++) { double x = x0 + xw * (j + 0.5) / s.v.Length; Vk(x, -TR, ZA(age), c, k * 0.35); Vk(x, Yh(norm(s.v[j])), ZA(age), c, k); }
                }
            };
            bars(ch, -HW, HW * 0.62, v => (v - 30) / 70, Color.FromArgb(255, 150, 70));
            bars(eh, -HW * 0.3, HW * 1.3, v => Math.Sqrt(Math.Max(0, v) / 6000), Color.FromArgb(255, 220, 90));
            GL.glLineWidth(3 * SCF); Flush(0x0001); GL.glLineWidth(1 * SCF); WallEnd();
            if (ch.Count > 0 && ch[0].v != null) LW(5, -HW * 0.69, -TR + 0.1, 0, "ядра " + string.Join(" ", ch[0].v.Select(v => F(v, "0"))), Color.FromArgb(255, 150, 70), 2, 10);
            if (eh.Count > 0 && eh[0].v != null)
                for (int j = 0; j < en.Length && j < eh[0].v.Length; j++)
                    LW(5, -HW * 0.3 + HW * 1.3 * (j + 0.5) / en.Length, Yh(Math.Sqrt(Math.Max(0, eh[0].v[j]) / 6000)), 0, (j + 1).ToString(), Color.FromArgb(255, 220, 90), 2, -14);
        }
        // 6 Направления — стрелки на грани: ветер (слева), движение картинки (справа); поперёк — восток/вправо, вверх — север/вверх
        if (G(GRAM[6]))
        {
            WallBegin(6);
            var wb = Sr("wind_b"); var ws = Sr("wind_s"); var mx = Sr("mot_x"); var my = Sr("mot_y");
            var cW = Color.FromArgb(230, 240, 255); var cM = Color.FromArgb(120, 255, 140);
            for (int j = 0; j < ws.Count && j < wb.Count; j++)
            {
                double age = (now - ws[j].t).TotalSeconds; if (age > 20) continue;
                double a = (wb[j].v + 180) * Math.PI / 180, len = Math.Min(1, ws[j].v / 20) * 0.28, k = 0.2 + 0.7 * (1 - age / 20), x0 = -HW * 0.5, y0 = -TR + 0.3;
                Vk(x0, y0, ZA(age), cW, k * 0.3); Vk(x0 + Math.Sin(a) * len, y0 + Math.Cos(a) * len, ZA(age), cW, k);
            }
            for (int j = 0; j < mx.Count && j < my.Count; j += 3)
            {
                double age = (now - mx[j].t).TotalSeconds; if (age > 20) continue;
                double vx = mx[j].v, vy = -my[j].v, l = Math.Sqrt(vx * vx + vy * vy); if (l < 0.02) continue;
                double len = Math.Min(0.28, l), k = 0.15 + 0.75 * (1 - age / 20), x0 = HW * 0.5, y0 = -TR + 0.3;
                Vk(x0, y0, ZA(age), cM, k * 0.2); Vk(x0 + vx / l * len, y0 + vy / l * len, ZA(age), cM, k);
            }
            GL.glLineWidth(2 * SCF); Flush(0x0001); GL.glLineWidth(1 * SCF); WallEnd();
            object u; string unit; lock (lk) unit = mq.TryGetValue("wind_unit", out u) ? (string)u : "";
            string[] dirs = { "С", "СВ", "В", "ЮВ", "Ю", "ЮЗ", "З", "СЗ" };
            double wbl = Lst("wind_b");
            if (!double.IsNaN(wbl)) LW(6, -HW * 0.5, -TR + 0.62, 0, "ветер " + F(Lst("wind_s"), "0.#") + " " + unit + " из " + dirs[(int)Math.Round(wbl / 45) % 8], cW, 2, -8);
            LW(6, HW * 0.5, -TR + 0.62, 0, "движение в кадре", cM, 2, -8);
        }
        // 7 События — всплески на грани в момент события
        if (G(GRAM[7]))
        {
            List<Ev> ev; lock (lk) ev = new List<Ev>(events);
            WallBegin(7);
            foreach (var e in ev)
            {
                double age = (now - e.t).TotalSeconds; if (age > 20) continue;
                double x = -HW * 0.85 + HW * 1.7 * ((e.src.GetHashCode() & 0xffff) / 65535.0), k = age < 2 ? 1 : 0.35 + 0.5 * (1 - age / 20);
                Vk(x, -TR, ZA(age), e.c, k * 0.3); Vk(x, Yh(0.9), ZA(age), e.c, k);
            }
            GL.glLineWidth(3 * SCF); Flush(0x0001); GL.glLineWidth(1 * SCF); WallEnd();
            foreach (var e in ev) { double age = (now - e.t).TotalSeconds; if (age < 10) LW(7, -HW * 0.85 + HW * 1.7 * ((e.src.GetHashCode() & 0xffff) / 65535.0), Yh(0.9), ZA(age), e.src, e.c, 2, -14); }
        }
        // 8 Состояния — 4 дорожки вдоль грани, светятся, пока состояние «да»
        if (G(GRAM[8]))
        {
            var rows = new[] { new object[] { "st_andrey", "Андрей снаружи", Color.FromArgb(120, 255, 170) }, new object[] { "st_boiler", "бойлер", Color.FromArgb(255, 120, 80) },
                new object[] { "st_locked", "XPS заблокирован", Color.FromArgb(170, 150, 255) }, new object[] { "st_vpn", "VPN", Color.FromArgb(90, 200, 255) } };
            WallBegin(8);
            for (int r = 0; r < rows.Length; r++)
            {
                var l = Sr((string)rows[r][0]); var c = (Color)rows[r][2]; double x0 = -HW + 2 * HW * r / 4 + 0.012, x1 = -HW + 2 * HW * (r + 1) / 4 - 0.012, y = -TR + 0.01;
                for (int j = 0; j + 1 < l.Count; j++)
                {
                    double a0 = (now - l[j].t).TotalSeconds, a1 = (now - l[j + 1].t).TotalSeconds; if (a0 > 20 || l[j].v < 0.5) continue; double k = 0.25 + 0.55 * (1 - a0 / 20);
                    Vk(x0, y, ZA(a0), c, k); Vk(x1, y, ZA(a0), c, k); Vk(x1, y, ZA(a1), c, k); Vk(x0, y, ZA(a1), c, k);
                }
            }
            Flush(0x0007); WallEnd();
            for (int r = 0; r < rows.Length; r++)
            {
                var l = Sr((string)rows[r][0]); bool on = l.Count > 0 && l[l.Count - 1].v > 0.5;
                LW(8, -HW + 2 * HW * (r + 0.5) / 4, -TR + 0.06 + (r % 2) * 0.07, 0, (string)rows[r][1] + (on ? " · да" : " · нет"), on ? (Color)rows[r][2] : Color.FromArgb(140, 140, 140), 2, -8);
            }
        }
        // 9 Потоки — частицы летят вдаль, скорость ∝ log объёма
        if (G(GRAM[9]))
        {
            Dictionary<string, double> fl; lock (lk) fl = new Dictionary<string, double>(flows);
            WallBegin(9); int r = 0; var c = GCOL[9];
            foreach (var kv in fl)
            {
                double x = -HW + 2 * HW * (r + 0.5) / Math.Max(1, fl.Count), rate = kv.Value, sp = Math.Log10(1 + rate / 1024) * 1.2;   // с «пути» за с
                List<double> ps; if (!particles.TryGetValue(kv.Key, out ps)) particles[kv.Key] = ps = new List<double>();
                for (int j = 0; j < ps.Count; j++) ps[j] += sp * dt;
                ps.RemoveAll(p => p > 20);
                if (rate > 1 && rnd.NextDouble() < Math.Min(0.9, Math.Log10(1 + rate / 1024) / 4)) ps.Add(0);
                foreach (var p in ps) { double k = 0.9 * (1 - p / 20) + 0.1; Vk(x, -TR + 0.04, ZA(p), c, k); Vk(x, -TR + 0.04, ZA(Math.Max(0, p - 0.25 - sp * 0.1)), c, k * 0.1); }
                Vk(x, -TR + 0.005, 0, c, 0.15); Vk(x, -TR + 0.005, -TD, c, 0.03);
                r++;
            }
            GL.glLineWidth(2 * SCF); Flush(0x0001); GL.glLineWidth(1 * SCF); WallEnd();
            r = 0;
            foreach (var kv in fl)
            {
                string v = kv.Value > 1048576 ? F(kv.Value / 1048576, "0.##") + " МБ/с" : F(kv.Value / 1024, "0.#") + " КБ/с";
                LW(9, -HW + 2 * HW * (r + 0.5) / Math.Max(1, fl.Count), -TR + 0.07 + (r % 2) * 0.08, 0, kv.Key + " " + v, c, 2, -8); r++;
            }
        }
    }

    // легенда грамматик: ■ включено, □ выключено (переключатели — пресеты PTZ во Frigate)
    static void Legend()
    {
        var parts = new List<Label>();
        for (int i = 0; i < GRAM.Length; i++) parts.Add(new Label { s = (G(GRAM[i]) ? "■ " : "□ ") + GRAM[i], c = G(GRAM[i]) ? GCOL[i] : Color.FromArgb(140, 140, 140) });
        legendParts = parts;
    }
    static List<Label> legendParts = new List<Label>();

    // 25 к/с (21.09, поздний вечер): текст GDI+ дорогой — каждая подпись рисуется один раз в маленькую картинку
    // (с чёрным контуром) и дальше только копируется; кэш сбрасывается, когда разрастается (метки времени меняются).
    static readonly Dictionary<string, Bitmap> textCache = new Dictionary<string, Bitmap>();
    static Font bigFont = new Font("Consolas", 40f, FontStyle.Bold, GraphicsUnit.Pixel);   // для надписей вдоль граней
    static readonly Dictionary<string, uint> texOf = new Dictionary<string, uint>();
    static uint TexFor(string key, Bitmap b)
    {
        uint t; if (texOf.TryGetValue(key, out t)) return t;
        var d = b.LockBits(new Rectangle(0, 0, b.Width, b.Height), ImageLockMode.ReadOnly, PixelFormat.Format32bppArgb);
        try { t = GL.UploadTex(b.Width, b.Height, d.Scan0); } finally { b.UnlockBits(d); }
        texOf[key] = t; return t;
    }
    static Bitmap TextBmp(string s, Color c, Font font, StringFormat fmt)
    {
        string key = c.ToArgb() + "|" + s; Bitmap b;
        if (textCache.TryGetValue(key, out b)) return b;
        if (textCache.Count > 600) { foreach (var v in textCache.Values) v.Dispose(); textCache.Clear(); foreach (var t in texOf.Values) GL.DeleteTex(t); texOf.Clear(); }
        SizeF sz; using (var tmp = new Bitmap(1, 1)) using (var tg = Graphics.FromImage(tmp)) sz = tg.MeasureString(s, font, new PointF(0, 0), fmt);
        b = new Bitmap(Math.Max(1, (int)Math.Ceiling(sz.Width) + 3), Math.Max(1, (int)Math.Ceiling(sz.Height) + 3), PixelFormat.Format32bppArgb);
        using (var g = Graphics.FromImage(b))
        using (var sh = new SolidBrush(Color.FromArgb(200, 0, 0, 0)))
        using (var br = new SolidBrush(c))
        {
            g.TextRenderingHint = System.Drawing.Text.TextRenderingHint.AntiAlias;
            foreach (var d in new[] { new[] { 0, 1 }, new[] { 2, 1 }, new[] { 1, 0 }, new[] { 1, 2 } }) g.DrawString(s, font, sh, d[0], d[1], fmt);
            g.DrawString(s, font, br, 1, 1, fmt);
        }
        textCache[key] = b; return b;
    }
    static readonly byte[] unpm = BuildUnpm();                 // unpm[a*256+c] = c*255/a — без деления в цикле
    static byte[] BuildUnpm() { var t = new byte[65536]; for (int a = 1; a < 256; a++) for (int c = 0; c <= a; c++) t[a * 256 + c] = (byte)(c * 255 / a); return t; }

    // ===== Сборка всего кадра на видеокарте (21.09, ночь; пробный поток xps_gpu) =====
    // ffmpeg камеры отдаёт чистый кадр 1080p yuv420p в канал \\.\pipe\aurora_cam; здесь на GPU: камера + аура-таблички
    // (overlay.png рендерера ауры) + аврора + часы и шарик → yuv420p → свой ffmpeg только кодирует. Выключатель —
    // W:\tools\aurora3d\composite_off.txt.
    static readonly int CAMB = W * H * 3 / 2;
    static byte[] camFrame = new byte[CAMB]; static long camSeq = 0; static volatile bool camOn = false;
    static void CamLoop()
    {
        var buf = new byte[CAMB];
        while (true)
        {
            NamedPipeServerStream ps = null;
            try
            {
                ps = new NamedPipeServerStream("aurora_cam", PipeDirection.In, 1, PipeTransmissionMode.Byte, PipeOptions.None, CAMB, 0);
                ps.WaitForConnection(); Log("камера: ffmpeg подключился к aurora_cam");
                while (true)
                {
                    int got = 0; while (got < CAMB) { int n = ps.Read(buf, got, CAMB - got); if (n <= 0) throw new IOException("камера: канал закрыт"); got += n; }
                    lock (camLock) { var t = camFrame; camFrame = buf; buf = t; camSeq++; }
                    camOn = true;
                }
            }
            catch (Exception e) { camOn = false; Log(e.Message); }
            finally { if (ps != null) try { ps.Dispose(); } catch { } }
            Thread.Sleep(500);
        }
    }
    static readonly object camLock = new object();
    // аура-таблички: overlay.png пишет overlay_render.ps1 (2 раза в секунду) — перечитываем по времени изменения
    static byte[] auraBgra; static volatile bool auraNew = false; static DateTime auraT = DateTime.MinValue; static volatile bool auraLoading = false;
    static void AuraCheck()
    {
        const string f = @"W:\ffmpeg\overlay\overlay.png";
        DateTime t; try { t = File.GetLastWriteTimeUtc(f); } catch { return; }
        if (t == auraT || auraLoading) return;
        auraLoading = true; auraT = t;
        ThreadPool.QueueUserWorkItem(_ =>
        {
            try
            {
                byte[] raw; using (var fs = new FileStream(f, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete)) { raw = new byte[fs.Length]; int g = 0; while (g < raw.Length) { int k = fs.Read(raw, g, raw.Length - g); if (k <= 0) break; g += k; } }
                using (var ms = new MemoryStream(raw)) using (var bm = new Bitmap(ms))
                {
                    if (bm.Width != W || bm.Height != H) return;
                    var d = bm.LockBits(new Rectangle(0, 0, W, H), ImageLockMode.ReadOnly, PixelFormat.Format32bppArgb);
                    var b = new byte[W * H * 4]; Marshal.Copy(d.Scan0, b, 0, b.Length); bm.UnlockBits(d);
                    auraBgra = b; auraNew = true;
                }
            }
            catch { auraT = DateTime.MinValue; }                          // PNG мог быть недописан — повторим на следующем кадре
            finally { auraLoading = false; }
        });
    }
    // часы (как drawtext ffmpeg): позиция и кегль — из clock_pos.txt рендерера ауры
    static int clkX = 18, clkY = 18, clkFs = 16; static DateTime clkPosT = DateTime.MinValue;
    static void ClockPosCheck()
    {
        const string f = @"W:\ffmpeg\overlay\clock_pos.txt";
        try { var t = File.GetLastWriteTimeUtc(f); if (t == clkPosT) return; clkPosT = t; var p = File.ReadAllText(f).Trim().Split(' '); if (p.Length >= 3) { clkX = int.Parse(p[0]); clkY = int.Parse(p[1]); clkFs = int.Parse(p[2]); } } catch { }
    }
    static Bitmap clockBmp; static Graphics clockG; static Font clockFont; static int clockFontFs = 0;
    static void DrawAuraExtras(long n)
    {
        // часы ГГГГ-ММ-ДД  ЧЧ:ММ:СС.сссс (кадр) — жёлтые с чёрным контуром, текстура обновляется на месте
        if (clockFontFs != clkFs) { if (clockFont != null) clockFont.Dispose(); clockFont = new Font("Consolas", clkFs, FontStyle.Bold, GraphicsUnit.Pixel); clockFontFs = clkFs; }
        if (clockBmp == null) { clockBmp = new Bitmap(1024, 96, PixelFormat.Format32bppArgb); clockG = Graphics.FromImage(clockBmp); clockG.TextRenderingHint = System.Drawing.Text.TextRenderingHint.AntiAlias; }
        clockG.Clear(Color.Transparent);
        string txt = DateTime.Now.ToString("yyyy-MM-dd  HH:mm:ss.ffff") + " (" + n + ")";
        using (var sh = new SolidBrush(Color.FromArgb(230, 0, 0, 0))) using (var br = new SolidBrush(Color.FromArgb(255, 214, 90)))
        {
            foreach (var d in new[] { new[] { 0, 1 }, new[] { 2, 1 }, new[] { 1, 0 }, new[] { 1, 2 }, new[] { 2, 2 } }) clockG.DrawString(txt, clockFont, sh, d[0], d[1], StringFormat.GenericTypographic);
            clockG.DrawString(txt, clockFont, br, 1, 1, StringFormat.GenericTypographic);
        }
        GL.ExtrasBegin();
        var dd = clockBmp.LockBits(new Rectangle(0, 0, clockBmp.Width, clockBmp.Height), ImageLockMode.ReadOnly, PixelFormat.Format32bppArgb);
        try { GL.DrawDynTex(dd.Scan0, clockBmp.Width, clockBmp.Height, clkX - 1, clkY - 1); } finally { clockBmp.UnlockBits(dd); }
        // шарик по кругу (оборот за секунду при 25 к/с) с хвостом из 6 затухающих точек — как fx в webcam_push.ps1
        double R = H / 2.0 - 22, F1 = 44;
        for (int j = 6; j >= 0; j--)
        {
            double th = 2 * Math.PI * (n - j / 6.0) / 25.0, fs = F1 * (1 - 0.7 * j / 6), al = j == 0 ? 1 : 0.75 * (1 - j / 7.0);
            GL.Dot(W / 2.0 + R * Math.Cos(th), H / 2.0 + R * Math.Sin(th), fs * 0.62, 1f, 214 / 255f, 90 / 255f, (float)al);
        }
        GL.ExtrasEnd();
    }
    // второй ffmpeg: только кодирует готовый кадр (stdin) → пробный xps_gpu
    static Process encProc; static Stream encIn; static DateTime encStartT = DateTime.MinValue;
    static void EncEnsure()
    {
        if (encProc != null && !encProc.HasExited) return;
        if ((DateTime.Now - encStartT).TotalSeconds < 5) return;
        encStartT = DateTime.Now;
        try
        {
            var psi = new ProcessStartInfo(FFMPEG, "-hide_banner -loglevel warning -f rawvideo -pix_fmt yuv420p -s " + W + "x" + H + " -use_wallclock_as_timestamps 1 -i - " +
                // проба: libx264 720p (оба NVENC заняты ffmpeg камеры, а MF в этом процессе Intel не отдаёт — «Could not open encoder»)
                "-vf scale=1280:720:flags=fast_bilinear -c:v libx264 -preset ultrafast -tune zerolatency -g 50 -fps_mode cfr -r 25 -b:v 3M -f flv rtmp://127.0.0.1:1935/xps_gpu")
            { UseShellExecute = false, RedirectStandardInput = true, RedirectStandardError = true, CreateNoWindow = true };
            encProc = Process.Start(psi);
            encProc.ErrorDataReceived += (o, e) => { if (e.Data != null) Log("кодер: " + e.Data); };
            encProc.BeginErrorReadLine();
            encIn = encProc.StandardInput.BaseStream;
            Log("кодер xps_gpu запущен, pid " + encProc.Id);
        }
        catch (Exception e) { Log("кодер: " + e.Message); encProc = null; }
    }

    // ---------- главный цикл ----------
    static int Main()
    {
        Log("старт");
        // 19:44 21.09 процесс молча умер (перезапустил повтор задачи) — теперь причина останется в журнале
        AppDomain.CurrentDomain.UnhandledException += (o, e) => Log("НЕОБРАБОТАННОЕ: " + e.ExceptionObject);
        LoadToggles();
        new Thread(InputLoop) { IsBackground = true }.Start();
        new Thread(MqttLoop) { IsBackground = true }.Start();
        new Thread(SamplerLoop) { IsBackground = true }.Start();
        new Thread(OnvifLoop) { IsBackground = true }.Start();
        new Thread(CamLoop) { IsBackground = true }.Start();
        GL.Init(W, H);
        Log("OpenGL: " + GL.Renderer);
        var outb = new byte[W * H * 5 / 2];   // yuva420p: Y, U, V, A
        var compb = new byte[CAMB]; long compN = 0;                // собранный кадр yuv420p для xps_gpu
        var font = new Font("Consolas", 12f * SCF, FontStyle.Bold, GraphicsUnit.Pixel); labelFont = font;
        var shadow = new SolidBrush(Color.FromArgb(200, 0, 0, 0));
        var fmt = StringFormat.GenericTypographic;
        DateTime prev = DateTime.Now;
        while (true)
        {
            NamedPipeServerStream pipe = null;
            try
            {
                pipe = new NamedPipeServerStream("aurora3d", PipeDirection.Out, 1, PipeTransmissionMode.Byte, PipeOptions.None, 0, W * H * 5 / 2);
                pipe.WaitForConnection();
                Log("читатель подключился");
                var sw = Stopwatch.StartNew(); long frame = 0; int statN = 0; double statMs = 0, statW = 0, stDraw = 0, stLab = 0, stPost = 0, stComp = 0; DateTime statT = DateTime.Now;
                while (pipe.IsConnected)
                {
                    var now = DateTime.Now; double dt = Math.Min(0.5, (now - prev).TotalSeconds); prev = now;
                    lock (lk) { yaw += vYaw * dt * 1.2; pitch = Math.Max(-1.2, Math.Min(1.2, pitch + vPitch * dt * 0.8)); zoom = Math.Max(0.4, Math.Min(3, zoom * Math.Exp(vZoom * dt))); }
                    List<Slice> hs; float[,] dist;
                    lock (lk) { hs = new List<Slice>(hist); dist = frontDist; }
                    labels.Clear();
                    GL.glClearColor(0, 0, 0, 0); GL.glClear(0x4000 | 0x100);
                    double tA = sw.Elapsed.TotalMilliseconds;
                    DrawAll(hs, dist, now, dt);
                    DrawRays(); rays.Clear();
                    DrawPerimeterClock(now);
                    Legend(); GL.glFinish(); double tB = sw.Elapsed.TotalMilliseconds; stDraw += tB - tA;
                    // подписи: GDI+ растрирует строку ОДИН раз (кэш), дальше она — текстура на видеокарте
                    var ws = legendParts.Select(p => (float)TextBmp(p.s, p.c, font, fmt).Width + 14 * SCF).ToArray();
                    for (int row = 0; row < 2; row++)
                    {
                        int a0 = row * 5, a1 = Math.Min(legendParts.Count, a0 + 5); float lw = 0;
                        for (int i = a0; i < a1; i++) lw += ws[i];
                        float lx = (W - lw) / 2;
                        for (int i = a0; i < a1; i++) { var p = legendParts[i]; p.x = lx; p.y = (4 + row * 14) * SCF; p.align = 0; labels.Add(p); lx += ws[i]; }
                    }
                    GL.LabelsBegin();
                    foreach (var l in labels)
                    {
                        var tb = TextBmp(l.s, l.c, font, fmt); float x = l.x - 1;
                        if (l.align == 1) x -= tb.Width - 3; else if (l.align == 2) x -= (tb.Width - 3) / 2f;
                        GL.DrawTex(TexFor(l.c.ToArgb() + "|" + l.s, tb), (int)x, (int)l.y - 1, tb.Width, tb.Height);
                    }
                    GL.LabelsEnd(); GL.glFinish(); double tC = sw.Elapsed.TotalMilliseconds; stLab += tC - tB;
                    // итог на GPU: свечение (аддитивное) + подписи → прямая альфа → yuva420p (Y, U и V на ½, A) в 4 прохода шейдера;
                    // ffmpeg берёт кадр как есть, без растяжения и перевода цвета
                    GL.PostPassYuva(outb); stPost += sw.Elapsed.TotalMilliseconds - tC;
                    // сборка всего кадра (пробный xps_gpu): только когда из ffmpeg камеры идут кадры
                    if (camOn && !File.Exists(DIR + "composite_off.txt"))
                    {
                        double tD = sw.Elapsed.TotalMilliseconds;
                        AuraCheck(); ClockPosCheck();
                        if (auraNew) { GL.UploadAura(auraBgra, W, H); auraNew = false; }
                        lock (camLock) GL.UploadCam(camFrame, W, H);
                        DrawAuraExtras(compN++);
                        GL.Composite(compb);
                        EncEnsure();
                        try { if (encIn != null) encIn.Write(compb, 0, compb.Length); } catch (Exception e) { Log("кодер: запись — " + e.Message); try { encProc.Kill(); } catch { } encProc = null; }
                        stComp += sw.Elapsed.TotalMilliseconds - tD;
                    }
                    var tw0 = sw.Elapsed.TotalMilliseconds; pipe.Write(outb, 0, outb.Length); statW += sw.Elapsed.TotalMilliseconds - tw0;
                    frame++;
                    // 25 к/с (40 мс на кадр); раз в минуту — фактическая частота и время кадра в журнал
                    long due = (long)(frame * 40) - sw.ElapsedMilliseconds;
                    if (due > 0) Thread.Sleep((int)due); else if (due < -1000) { frame = sw.ElapsedMilliseconds / 40; }
                    statN++; statMs += (DateTime.Now - now).TotalMilliseconds;
                    if ((DateTime.Now - statT).TotalSeconds >= 20) { Log("кадров/с " + F(statN / (DateTime.Now - statT).TotalSeconds, "0.0") + ", кадр " + F(statMs / Math.Max(1, statN), "0.0") + " мс: сцена " + F(stDraw / Math.Max(1, statN), "0.0") + ", подписи " + F(stLab / Math.Max(1, statN), "0.0") + ", итог+считывание " + F(stPost / Math.Max(1, statN), "0.0") + ", сборка кадра " + F(stComp / Math.Max(1, statN), "0.0") + ", запись в канал " + F(statW / Math.Max(1, statN), "0.0") + " мс; текстур подписей " + texOf.Count); statN = 0; statMs = 0; statW = 0; stDraw = stLab = stPost = stComp = 0; statT = DateTime.Now; }
                }
            }
            catch (Exception e) { Log("выход: " + e.Message); }
            finally { if (pipe != null) try { pipe.Dispose(); } catch { } }
            Thread.Sleep(500);
        }
    }
}

// ---------- OpenGL 1.x + FBO (EXT) + шейдер второго прохода через P/Invoke ----------
static class GL
{
    [DllImport("opengl32.dll")] static extern void glGenTextures(int n, out uint t);
    [DllImport("opengl32.dll")] static extern void glBindTexture(uint target, uint t);
    [DllImport("opengl32.dll")] static extern void glTexImage2D(uint target, int level, int ifmt, int w, int h, int border, uint fmt, uint type, IntPtr data);
    [DllImport("opengl32.dll")] static extern void glTexParameteri(uint target, uint p, int v);
    [DllImport("opengl32.dll")] static extern void glBegin(uint mode);
    [DllImport("opengl32.dll")] static extern void glEnd();
    [DllImport("opengl32.dll")] static extern void glTexCoord2f(float s, float t);
    [DllImport("opengl32.dll")] static extern void glVertex2f(float x, float y);
    delegate uint CreateShaderFn(uint type);
    delegate void ShaderSourceFn(uint sh, int n, [MarshalAs(UnmanagedType.LPArray, ArraySubType = UnmanagedType.LPStr)] string[] src, int[] len);
    delegate void UIntFn(uint x);
    delegate void GetShaderivFn(uint sh, uint p, out int v);
    delegate void InfoLogFn(uint sh, int max, out int len, System.Text.StringBuilder log);
    delegate uint CreateProgramFn();
    delegate void AttachFn(uint p, uint sh);
    delegate int UniformLocFn(uint p, string name);
    delegate void Uniform1iFn(int loc, int v);
    delegate void FbTex2DFn(uint target, uint attach, uint textarget, uint tex, int level);
    static uint fbo1, fbo2, tex1, tex2, prog; static int W_, H_;
    static BindFn bindFb;
    static uint Shader(uint type, string src)
    {
        uint sh = Fn<CreateShaderFn>("glCreateShader")(type);
        Fn<ShaderSourceFn>("glShaderSource")(sh, 1, new[] { src }, null);
        Fn<UIntFn>("glCompileShader")(sh);
        int ok; Fn<GetShaderivFn>("glGetShaderiv")(sh, 0x8B81, out ok);
        if (ok == 0) { var sb = new System.Text.StringBuilder(2048); int l; Fn<InfoLogFn>("glGetShaderInfoLog")(sh, 2048, out l, sb); throw new Exception("шейдер: " + sb); }
        return sh;
    }
    static uint Tex(int w, int h)
    {
        uint t; glGenTextures(1, out t); glBindTexture(0x0DE1, t);
        glTexImage2D(0x0DE1, 0, 0x8058, w, h, 0, 0x1908, 0x1401, IntPtr.Zero);
        glTexParameteri(0x0DE1, 0x2801, 0x2600); glTexParameteri(0x0DE1, 0x2800, 0x2600);   // GL_NEAREST: 1:1, без размытия
        return t;
    }
    [DllImport("opengl32.dll")] static extern void glReadPixels(int x, int y, int w, int h, uint fmt, uint type, IntPtr data);
    [DllImport("opengl32.dll")] static extern void glPixelStorei(uint p, int v);
    [DllImport("opengl32.dll")] static extern void glDeleteTextures(int n, ref uint t);
    [DllImport("opengl32.dll")] static extern void glColor4f(float r, float g, float b, float a);
    delegate void BlendSepFn(uint sr, uint dr, uint sa, uint da);
    delegate void ActiveTexFn(uint unit);
    delegate void Uniform2fFn(int loc, float x, float y);
    static uint fbo3, tex3, fboY, fboU, fboV, fboA, texY, texU, texV, texA, progY;
    static int locMode, locPx;
    public static void DeleteTex(uint t) { glDeleteTextures(1, ref t); }
    public static uint UploadTex(int w, int h, IntPtr bgra)
    {
        uint t; glGenTextures(1, out t); glBindTexture(0x0DE1, t);
        glTexParameteri(0x0DE1, 0x2801, 0x2600); glTexParameteri(0x0DE1, 0x2800, 0x2600);
        glTexImage2D(0x0DE1, 0, 0x8058, w, h, 0, 0x80E1, 0x1401, bgra);
        glBindTexture(0x0DE1, 0); return t;
    }
    // слой подписей: отдельная текстура с настоящей альфой (у аддитивного свечения альфа — из яркости, тени там не бывает)
    public static void LabelsBegin()
    {
        bindFb(0x8D40, fbo3); glViewport(0, 0, W_, H_); glClearColor(0, 0, 0, 0); glClear(0x4000);
        glMatrixMode(0x1701); var m = new float[16]; m[0] = 2f / W_; m[5] = -2f / H_; m[10] = -1; m[12] = -1; m[13] = 1; m[15] = 1; glLoadMatrixf(m);
        glMatrixMode(0x1700); glLoadMatrixf(Id());
        glEnable(0x0DE1); glColor4f(1, 1, 1, 1);
        Fn<BlendSepFn>("glBlendFuncSeparate")(0x0302, 0x0303, 1, 0x0303);   // цвет premultiplied, альфа «поверх»
    }
    public static void DrawTex(uint t, int x, int y, int w, int h)
    {
        glBindTexture(0x0DE1, t); glBegin(0x0007);
        glTexCoord2f(0, 0); glVertex2f(x, y); glTexCoord2f(1, 0); glVertex2f(x + w, y); glTexCoord2f(1, 1); glVertex2f(x + w, y + h); glTexCoord2f(0, 1); glVertex2f(x, y + h);
        glEnd();
    }
    // четырёхугольник с текстурой в 3D (координаты — четыре вершины: начало-низ, конец-низ, конец-верх, начало-верх строки)
    public static void TexQuad(uint t, float k, double x0, double y0, double z0, double x1, double y1, double z1, double x2, double y2, double z2, double x3, double y3, double z3)
    {
        glEnable(0x0DE1); glBindTexture(0x0DE1, t); glColor4f(k, k, k, 1);
        glBegin(0x0007);
        glTexCoord2f(0, 1); glVertex3f((float)x0, (float)y0, (float)z0); glTexCoord2f(1, 1); glVertex3f((float)x1, (float)y1, (float)z1);
        glTexCoord2f(1, 0); glVertex3f((float)x2, (float)y2, (float)z2); glTexCoord2f(0, 0); glVertex3f((float)x3, (float)y3, (float)z3);
        glEnd(); glBindTexture(0x0DE1, 0); glDisable(0x0DE1);
    }
    [DllImport("opengl32.dll")] static extern void glVertex3f(float x, float y, float z);
    public static void LabelsEnd() { glDisable(0x0DE1); glBindTexture(0x0DE1, 0); glBlendFunc(1, 1); bindFb(0x8D40, fbo1); }
    // итог в yuva420p: 4 прохода шейдера (Y, U, V, A) по текстурам свечения [0] и подписей [1] → R8-текстуры → glReadPixels
    public static void PostPassYuva(byte[] outb)
    {
        glDisable(0x0BE2); glMatrixMode(0x1701); glLoadMatrixf(Id()); glMatrixMode(0x1700); glLoadMatrixf(Id());
        var at = Fn<ActiveTexFn>("glActiveTexture");
        Bloom();
        at(0x84C2); glBindTexture(0x0DE1, texB1);
        at(0x84C1); glBindTexture(0x0DE1, tex3); at(0x84C0); glBindTexture(0x0DE1, tex1);
        Fn<UIntFn>("glUseProgram")(progY); glPixelStorei(0x0D05, 1);
        var h = GCHandle.Alloc(outb, GCHandleType.Pinned);
        try
        {
            IntPtr p0 = h.AddrOfPinnedObject(); int ys = W_ * H_, cs = (W_ / 2) * (H_ / 2);
            uint[] fb = { fboY, fboU, fboV, fboA }; int[] off = { 0, ys, ys + cs, ys + 2 * cs };
            for (int mode = 0; mode < 4; mode++)
            {
                int w = (mode == 1 || mode == 2) ? W_ / 2 : W_, hh = (mode == 1 || mode == 2) ? H_ / 2 : H_;
                bindFb(0x8D40, fb[mode]); glViewport(0, 0, w, hh);
                Fn<Uniform1iFn>("glUniform1i")(locMode, mode);
                glBegin(0x0007);
                glTexCoord2f(0, 1); glVertex2f(-1, -1); glTexCoord2f(1, 1); glVertex2f(1, -1);
                glTexCoord2f(1, 0); glVertex2f(1, 1); glTexCoord2f(0, 0); glVertex2f(-1, 1);
                glEnd();
                glReadPixels(0, 0, w, hh, 0x1903, 0x1401, IntPtr.Add(p0, off[mode]));
            }
        }
        finally { h.Free(); }
        Fn<UIntFn>("glUseProgram")(0);
        at(0x84C2); glBindTexture(0x0DE1, 0); at(0x84C1); glBindTexture(0x0DE1, 0); at(0x84C0); glBindTexture(0x0DE1, 0);
        bindFb(0x8D40, fbo1); glViewport(0, 0, W_, H_); glEnable(0x0BE2);
    }
    static uint fboB1, fboB2, texB1, texB2, progDown, progBlur; static int bw, bh, locD;
    // ---- сборка всего кадра (камера + аура + аврора + часы) ----
    [DllImport("opengl32.dll")] static extern void glTexSubImage2D(uint target, int level, int x, int y, int w, int h, uint fmt, uint type, IntPtr data);
    [DllImport("opengl32.dll")] static extern void glTexSubImage2D(uint target, int level, int x, int y, int w, int h, uint fmt, uint type, byte[] data);
    static uint texCY, texCU, texCV, texAura, tex4, fbo4, texClock, progC; static int locCMode;
    static uint TexFmt(int w, int h, int ifmt, uint fmt)
    {
        uint t; glGenTextures(1, out t); glBindTexture(0x0DE1, t);
        glTexImage2D(0x0DE1, 0, ifmt, w, h, 0, fmt, 0x1401, IntPtr.Zero);
        glTexParameteri(0x0DE1, 0x2801, 0x2601); glTexParameteri(0x0DE1, 0x2800, 0x2601);
        glTexParameteri(0x0DE1, 0x2802, 0x812F); glTexParameteri(0x0DE1, 0x2803, 0x812F);
        glBindTexture(0x0DE1, 0); return t;
    }
    public static void UploadCam(byte[] yuv, int w, int h)
    {
        var hd = GCHandle.Alloc(yuv, GCHandleType.Pinned);
        try
        {
            IntPtr p = hd.AddrOfPinnedObject(); glPixelStorei(0x0CF5, 1);
            glBindTexture(0x0DE1, texCY); glTexSubImage2D(0x0DE1, 0, 0, 0, w, h, 0x1903, 0x1401, p);
            glBindTexture(0x0DE1, texCU); glTexSubImage2D(0x0DE1, 0, 0, 0, w / 2, h / 2, 0x1903, 0x1401, IntPtr.Add(p, w * h));
            glBindTexture(0x0DE1, texCV); glTexSubImage2D(0x0DE1, 0, 0, 0, w / 2, h / 2, 0x1903, 0x1401, IntPtr.Add(p, w * h + w * h / 4));
            glBindTexture(0x0DE1, 0);
        }
        finally { hd.Free(); }
    }
    public static void UploadAura(byte[] bgra, int w, int h) { glBindTexture(0x0DE1, texAura); glTexSubImage2D(0x0DE1, 0, 0, 0, w, h, 0x80E1, 0x1401, bgra); glBindTexture(0x0DE1, 0); }
    // слой часов и шарика (premultiplied, как подписи)
    public static void ExtrasBegin()
    {
        bindFb(0x8D40, fbo4); glViewport(0, 0, W_, H_); glClearColor(0, 0, 0, 0); glClear(0x4000);
        glMatrixMode(0x1701); var m = new float[16]; m[0] = 2f / W_; m[5] = -2f / H_; m[10] = -1; m[12] = -1; m[13] = 1; m[15] = 1; glLoadMatrixf(m);
        glMatrixMode(0x1700); glLoadMatrixf(Id());
        glEnable(0x0BE2); Fn<BlendSepFn>("glBlendFuncSeparate")(0x0302, 0x0303, 1, 0x0303);
    }
    public static void DrawDynTex(IntPtr bgra, int w, int h, int x, int y)
    {
        glEnable(0x0DE1); glColor4f(1, 1, 1, 1);
        glBindTexture(0x0DE1, texClock); glTexSubImage2D(0x0DE1, 0, 0, 0, w, h, 0x80E1, 0x1401, bgra);
        glBegin(0x0007); glTexCoord2f(0, 0); glVertex2f(x, y); glTexCoord2f(1, 0); glVertex2f(x + w, y); glTexCoord2f(1, 1); glVertex2f(x + w, y + h); glTexCoord2f(0, 1); glVertex2f(x, y + h); glEnd();
        glBindTexture(0x0DE1, 0); glDisable(0x0DE1);
    }
    public static void Dot(double x, double y, double d, float r, float g, float b, float a)
    {
        glPointSize((float)d); glColor4f(r, g, b, a); glBegin(0); glVertex2f((float)x, (float)y); glEnd();
    }
    public static void ExtrasEnd() { glBlendFunc(1, 1); bindFb(0x8D40, fbo1); }
    // сборка: 3 прохода (Y, U, V) в R8-цели, считывание в yuv420p
    public static void Composite(byte[] outb)
    {
        glDisable(0x0BE2); glMatrixMode(0x1701); glLoadMatrixf(Id()); glMatrixMode(0x1700); glLoadMatrixf(Id());
        var at = Fn<ActiveTexFn>("glActiveTexture");
        uint[] tx = { texCY, texCU, texCV, texAura, tex1, tex3, texB1, tex4 };
        for (int i = 7; i >= 0; i--) { at((uint)(0x84C0 + i)); glBindTexture(0x0DE1, tx[i]); }
        Fn<UIntFn>("glUseProgram")(progC); glPixelStorei(0x0D05, 1);
        var h = GCHandle.Alloc(outb, GCHandleType.Pinned);
        try
        {
            IntPtr p0 = h.AddrOfPinnedObject(); int ys = W_ * H_, cs = (W_ / 2) * (H_ / 2);
            uint[] fb = { fboY, fboU, fboV }; int[] off = { 0, ys, ys + cs };
            for (int mode = 0; mode < 3; mode++)
            {
                int w = mode == 0 ? W_ : W_ / 2, hh = mode == 0 ? H_ : H_ / 2;
                bindFb(0x8D40, fb[mode]); glViewport(0, 0, w, hh);
                Fn<Uniform1iFn>("glUniform1i")(locCMode, mode);
                glBegin(0x0007);
                glTexCoord2f(0, 1); glVertex2f(-1, -1); glTexCoord2f(1, 1); glVertex2f(1, -1);
                glTexCoord2f(1, 0); glVertex2f(1, 1); glTexCoord2f(0, 0); glVertex2f(-1, 1);
                glEnd();
                glReadPixels(0, 0, w, hh, 0x1903, 0x1401, IntPtr.Add(p0, off[mode]));
            }
        }
        finally { h.Free(); }
        Fn<UIntFn>("glUseProgram")(0);
        for (int i = 7; i >= 0; i--) { at((uint)(0x84C0 + i)); glBindTexture(0x0DE1, 0); }
        bindFb(0x8D40, fbo1); glViewport(0, 0, W_, H_); glEnable(0x0BE2);
    }
    delegate void Uniform1fFn(int loc, float v);
    static uint TexLin(int w, int h)
    {
        uint t; glGenTextures(1, out t); glBindTexture(0x0DE1, t);
        glTexImage2D(0x0DE1, 0, 0x8058, w, h, 0, 0x1908, 0x1401, IntPtr.Zero);
        glTexParameteri(0x0DE1, 0x2801, 0x2601); glTexParameteri(0x0DE1, 0x2800, 0x2601);   // GL_LINEAR — для гаусса через линейную выборку
        glTexParameteri(0x0DE1, 0x2802, 0x812F); glTexParameteri(0x0DE1, 0x2803, 0x812F);   // CLAMP_TO_EDGE
        return t;
    }
    static uint Prog(uint vs, uint fs) { uint p = Fn<CreateProgramFn>("glCreateProgram")(); Fn<AttachFn>("glAttachShader")(p, vs); Fn<AttachFn>("glAttachShader")(p, fs); Fn<UIntFn>("glLinkProgram")(p); return p; }
    static void FullQuad()
    {
        glBegin(0x0007); glTexCoord2f(0, 0); glVertex2f(-1, -1); glTexCoord2f(1, 0); glVertex2f(1, -1); glTexCoord2f(1, 1); glVertex2f(1, 1); glTexCoord2f(0, 1); glVertex2f(-1, 1); glEnd();
    }
    // ореол свечения: tex1 → ¼ (texB1) → гаусс Г → texB2 → гаусс В → texB1, и ещё раз для ширины
    static void Bloom()
    {
        var use = Fn<UIntFn>("glUseProgram"); var u2 = Fn<Uniform2fFn>("glUniform2f");
        bindFb(0x8D40, fboB1); glViewport(0, 0, bw, bh); use(progDown); glBindTexture(0x0DE1, tex1); FullQuad();
        use(progBlur);
        for (int it = 0; it < 2; it++)
        {
            bindFb(0x8D40, fboB2); u2(locD, 1f / bw, 0); glBindTexture(0x0DE1, texB1); FullQuad();
            bindFb(0x8D40, fboB1); u2(locD, 0, 1f / bh); glBindTexture(0x0DE1, texB2); FullQuad();
        }
        use(0); glBindTexture(0x0DE1, 0);
    }
    static uint R8(int w, int h, FbTex2DFn fbTex, out uint fbo)
    {
        uint t; glGenTextures(1, out t); glBindTexture(0x0DE1, t);
        glTexImage2D(0x0DE1, 0, 0x8229, w, h, 0, 0x1903, 0x1401, IntPtr.Zero);          // GL_R8 / GL_RED
        glTexParameteri(0x0DE1, 0x2801, 0x2600); glTexParameteri(0x0DE1, 0x2800, 0x2600);
        Fn<GenFn>("glGenFramebuffersEXT")(1, out fbo); bindFb(0x8D40, fbo); fbTex(0x8D40, 0x8CE0, 0x0DE1, t, 0);
        return t;
    }
    // второй проход: сцена (текстура 1) → шейдер → текстура 2 → glReadPixels в готовый буфер (сверху вниз, прямая альфа)
    public static void PostPass(byte[] outb)
    {
        glDisable(0x0BE2); bindFb(0x8D40, fbo2); glViewport(0, 0, W_, H_);
        glMatrixMode(0x1701); glLoadMatrixf(Id()); glMatrixMode(0x1700); glLoadMatrixf(Id());
        Fn<UIntFn>("glUseProgram")(prog); glBindTexture(0x0DE1, tex1);
        glBegin(0x0007);
        glTexCoord2f(0, 1); glVertex2f(-1, -1); glTexCoord2f(1, 1); glVertex2f(1, -1);
        glTexCoord2f(1, 0); glVertex2f(1, 1); glTexCoord2f(0, 0); glVertex2f(-1, 1);
        glEnd();
        Fn<UIntFn>("glUseProgram")(0);
        glReadPixels(0, 0, W_, H_, 0x80E1, 0x1401, outb);
        bindFb(0x8D40, fbo1); glEnable(0x0BE2);
    }
    static float[] Id() { var m = new float[16]; m[0] = m[5] = m[10] = m[15] = 1; return m; }
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
    [DllImport("opengl32.dll")] public static extern void glLineWidth(float w);
    [DllImport("opengl32.dll")] public static extern void glEnableClientState(uint arr);
    [DllImport("opengl32.dll")] public static extern void glVertexPointer(int size, uint type, int stride, float[] ptr);
    [DllImport("opengl32.dll")] public static extern void glColorPointer(int size, uint type, int stride, float[] ptr);
    [DllImport("opengl32.dll")] public static extern void glDrawArrays(uint mode, int first, int count);
    [DllImport("opengl32.dll")] public static extern void glReadPixels(int x, int y, int w, int h, uint fmt, uint type, byte[] data);
    [DllImport("opengl32.dll")] public static extern void glFinish();
    [DllImport("opengl32.dll")] public static extern void glPushMatrix();
    [DllImport("opengl32.dll")] public static extern void glPopMatrix();
    [DllImport("opengl32.dll")] public static extern void glTranslatef(float x, float y, float z);
    [DllImport("opengl32.dll")] public static extern void glRotatef(float a, float x, float y, float z);
    [DllImport("opengl32.dll")] public static extern void glScalef(float x, float y, float z);
    delegate void BlendColorFn(float r, float g, float b, float a);
    static BlendColorFn blendColor;
    public static void BlendColor(float r, float g, float b, float a) { if (blendColor == null) blendColor = Fn<BlendColorFn>("glBlendColor"); blendColor(r, g, b, a); }
    [DllImport("opengl32.dll")] public static extern void glFogi(uint p, int v);
    [DllImport("opengl32.dll")] public static extern void glFogf(uint p, float v);
    [DllImport("opengl32.dll")] public static extern void glFogfv(uint p, float[] v);
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
        const uint FB = 0x8D40, RB = 0x8D41; uint rd; W_ = w; H_ = h;
        bindFb = Fn<BindFn>("glBindFramebufferEXT"); var fbTex = Fn<FbTex2DFn>("glFramebufferTexture2DEXT");
        // FBO 2 — результат второго прохода
        tex2 = Tex(w, h); Fn<GenFn>("glGenFramebuffersEXT")(1, out fbo2); bindFb(FB, fbo2); fbTex(FB, 0x8CE0, 0x0DE1, tex2, 0);
        // FBO 1 — сцена: цвет в текстуру (её читает шейдер), глубина — renderbuffer
        tex1 = Tex(w, h); Fn<GenFn>("glGenFramebuffersEXT")(1, out fbo1); bindFb(FB, fbo1); fbTex(FB, 0x8CE0, 0x0DE1, tex1, 0);
        Fn<GenFn>("glGenRenderbuffersEXT")(1, out rd); Fn<BindFn>("glBindRenderbufferEXT")(RB, rd);
        Fn<StorageFn>("glRenderbufferStorageEXT")(RB, 0x81A6, w, h); Fn<AttachRbFn>("glFramebufferRenderbufferEXT")(FB, 0x8D00, RB, rd);
        glBindTexture(0x0DE1, 0);
        tex3 = Tex(w, h); Fn<GenFn>("glGenFramebuffersEXT")(1, out fbo3); bindFb(FB, fbo3); fbTex(FB, 0x8CE0, 0x0DE1, tex3, 0);
        texY = R8(w, h, fbTex, out fboY); texA = R8(w, h, fbTex, out fboA); texU = R8(w / 2, h / 2, fbTex, out fboU); texV = R8(w / 2, h / 2, fbTex, out fboV);
        bindFb(FB, fbo1); glBindTexture(0x0DE1, 0);
        uint vs = Shader(0x8B31, "varying vec2 uv; void main(){ uv = gl_MultiTexCoord0.xy; gl_Position = gl_Vertex; }");
        // yuva: итог = подписи поверх свечения (прямая альфа), BT.601 ограниченный диапазон; U/V — среднее 2x2 пикселей
        uint fy = Shader(0x8B30,
            "uniform sampler2D g; uniform sampler2D l; uniform sampler2D bl; uniform float bk; uniform int mode; uniform vec2 px; varying vec2 uv;" +
            "vec4 comp(vec2 t){ vec3 c = min(texture2D(g, t).rgb + texture2D(bl, t).rgb * bk, vec3(1.0)); float ag = max(c.r, max(c.g, c.b)) * 0.784; vec3 gp = c * 0.784;" +
            " vec4 L = texture2D(l, t); float a = L.a + ag * (1.0 - L.a); vec3 pm = L.rgb + gp * (1.0 - L.a);" +
            " return a > 0.0 ? vec4(pm / a, a) : vec4(0.0); }" +
            "void main(){ vec4 c;" +
            " if (mode == 1 || mode == 2) { c = (comp(uv + vec2(-px.x, -px.y) * 0.5) + comp(uv + vec2(px.x, -px.y) * 0.5) + comp(uv + vec2(-px.x, px.y) * 0.5) + comp(uv + vec2(px.x, px.y) * 0.5)) * 0.25; }" +
            " else c = comp(uv);" +
            " float v;" +
            " if (mode == 0) v = (16.0 + 65.481 * c.r + 128.553 * c.g + 24.966 * c.b) / 255.0;" +
            " else if (mode == 1) v = (128.0 - 37.797 * c.r - 74.203 * c.g + 112.0 * c.b) / 255.0;" +
            " else if (mode == 2) v = (128.0 + 112.0 * c.r - 93.786 * c.g - 18.214 * c.b) / 255.0;" +
            " else v = c.a;" +
            " gl_FragColor = vec4(v, 0.0, 0.0, 1.0); }");
        // bloom: уменьшение в 4 раза (среднее 4 выборок) и гаусс 9 выборок через линейную фильтрацию, два прохода по две оси
        bw = w / 4; bh = h / 4;
        texB1 = TexLin(bw, bh); Fn<GenFn>("glGenFramebuffersEXT")(1, out fboB1); bindFb(FB, fboB1); fbTex(FB, 0x8CE0, 0x0DE1, texB1, 0);
        texB2 = TexLin(bw, bh); Fn<GenFn>("glGenFramebuffersEXT")(1, out fboB2); bindFb(FB, fboB2); fbTex(FB, 0x8CE0, 0x0DE1, texB2, 0);
        bindFb(FB, fbo1); glBindTexture(0x0DE1, 0);
        progDown = Prog(vs, Shader(0x8B30, "uniform sampler2D t; uniform vec2 px; varying vec2 uv; void main(){ vec3 c = texture2D(t, uv + vec2(-px.x, -px.y)).rgb +" +
            " texture2D(t, uv + vec2(px.x, -px.y)).rgb + texture2D(t, uv + vec2(-px.x, px.y)).rgb + texture2D(t, uv + vec2(px.x, px.y)).rgb; gl_FragColor = vec4(c * 0.25, 1.0); }"));
        Fn<UIntFn>("glUseProgram")(progDown); Fn<Uniform2fFn>("glUniform2f")(Fn<UniformLocFn>("glGetUniformLocation")(progDown, "px"), 1f / w, 1f / h);
        progBlur = Prog(vs, Shader(0x8B30, "uniform sampler2D t; uniform vec2 d; varying vec2 uv; void main(){ vec3 c = texture2D(t, uv).rgb * 0.2270270270;" +
            " c += (texture2D(t, uv + d * 1.3846153846).rgb + texture2D(t, uv - d * 1.3846153846).rgb) * 0.3162162162;" +
            " c += (texture2D(t, uv + d * 3.2307692308).rgb + texture2D(t, uv - d * 3.2307692308).rgb) * 0.0702702703; gl_FragColor = vec4(c, 1.0); }"));
        locD = Fn<UniformLocFn>("glGetUniformLocation")(progBlur, "d");
        Fn<UIntFn>("glUseProgram")(0);
        // сборка кадра: текстуры камеры (R8: Y, U, V), ауры (RGBA), слоя часов (FBO 4), динамическая текстура часов
        texCY = TexFmt(w, h, 0x8229, 0x1903); texCU = TexFmt(w / 2, h / 2, 0x8229, 0x1903); texCV = TexFmt(w / 2, h / 2, 0x8229, 0x1903);
        texAura = TexFmt(w, h, 0x8058, 0x80E1); texClock = TexFmt(1024, 96, 0x8058, 0x80E1);
        tex4 = Tex(w, h); Fn<GenFn>("glGenFramebuffersEXT")(1, out fbo4); bindFb(FB, fbo4); fbTex(FB, 0x8CE0, 0x0DE1, tex4, 0); bindFb(FB, fbo1);
        uint fc = Shader(0x8B30,
            "uniform sampler2D cy, cu, cv, au, g, l, bl, ex; uniform int mode; uniform vec2 px; uniform float bk; varying vec2 uv;" +
            "vec3 frame(vec2 t){ vec2 f = vec2(t.x, 1.0 - t.y);" +                                   // загруженные текстуры — сверху вниз
            " float Y = (texture2D(cy, f).r * 255.0 - 16.0) / 219.0, U = (texture2D(cu, f).r * 255.0 - 128.0) / 224.0, V = (texture2D(cv, f).r * 255.0 - 128.0) / 224.0;" +
            " vec3 c = clamp(vec3(Y + 1.402 * V, Y - 0.344136 * U - 0.714136 * V, Y + 1.772 * U), 0.0, 1.0);" +
            " vec4 A = texture2D(au, f); c = mix(c, A.rgb, A.a);" +                                  // аура-таблички (прямая альфа)
            " vec3 gc = min(texture2D(g, t).rgb + texture2D(bl, t).rgb * bk, vec3(1.0)); float ag = max(gc.r, max(gc.g, gc.b)) * 0.784;" +
            " vec4 L = texture2D(l, t); float a = L.a + ag * (1.0 - L.a); vec3 pm = L.rgb + gc * 0.784 * (1.0 - L.a); c = pm + c * (1.0 - a);" +   // аврора
            " vec4 E = texture2D(ex, t); c = E.rgb + c * (1.0 - E.a);" +                              // часы и шарик
            " return c; }" +
            "void main(){ vec3 c;" +
            " if (mode == 0) c = frame(uv); else c = (frame(uv + vec2(-px.x, -px.y) * 0.5) + frame(uv + vec2(px.x, -px.y) * 0.5) + frame(uv + vec2(-px.x, px.y) * 0.5) + frame(uv + vec2(px.x, px.y) * 0.5)) * 0.25;" +
            " float v; if (mode == 0) v = (16.0 + 65.481 * c.r + 128.553 * c.g + 24.966 * c.b) / 255.0;" +
            " else if (mode == 1) v = (128.0 - 37.797 * c.r - 74.203 * c.g + 112.0 * c.b) / 255.0;" +
            " else v = (128.0 + 112.0 * c.r - 93.786 * c.g - 18.214 * c.b) / 255.0;" +
            " gl_FragColor = vec4(v, 0.0, 0.0, 1.0); }");
        progC = Prog(vs, fc);
        Fn<UIntFn>("glUseProgram")(progC);
        string[] un = { "cy", "cu", "cv", "au", "g", "l", "bl", "ex" };
        for (int i = 0; i < un.Length; i++) Fn<Uniform1iFn>("glUniform1i")(Fn<UniformLocFn>("glGetUniformLocation")(progC, un[i]), i);
        Fn<Uniform2fFn>("glUniform2f")(Fn<UniformLocFn>("glGetUniformLocation")(progC, "px"), 1f / w, 1f / h);
        Fn<Uniform1fFn>("glUniform1f")(Fn<UniformLocFn>("glGetUniformLocation")(progC, "bk"), 1.1f);
        locCMode = Fn<UniformLocFn>("glGetUniformLocation")(progC, "mode");
        Fn<UIntFn>("glUseProgram")(0);
        progY = Fn<CreateProgramFn>("glCreateProgram")(); Fn<AttachFn>("glAttachShader")(progY, vs); Fn<AttachFn>("glAttachShader")(progY, fy); Fn<UIntFn>("glLinkProgram")(progY);
        Fn<UIntFn>("glUseProgram")(progY);
        Fn<Uniform1iFn>("glUniform1i")(Fn<UniformLocFn>("glGetUniformLocation")(progY, "g"), 0);
        Fn<Uniform1iFn>("glUniform1i")(Fn<UniformLocFn>("glGetUniformLocation")(progY, "l"), 1);
        Fn<Uniform1iFn>("glUniform1i")(Fn<UniformLocFn>("glGetUniformLocation")(progY, "bl"), 2);
        Fn<Uniform1fFn>("glUniform1f")(Fn<UniformLocFn>("glGetUniformLocation")(progY, "bk"), 1.1f);   // сила ореола
        locMode = Fn<UniformLocFn>("glGetUniformLocation")(progY, "mode"); locPx = Fn<UniformLocFn>("glGetUniformLocation")(progY, "px");
        Fn<Uniform2fFn>("glUniform2f")(locPx, 1f / w, 1f / h);
        Fn<UIntFn>("glUseProgram")(0);
        uint fs = Shader(0x8B30, "uniform sampler2D t; varying vec2 uv; void main(){ vec4 c = texture2D(t, uv); float a = max(c.r, max(c.g, c.b));" +
            " gl_FragColor = a > 0.0 ? vec4(c.rgb / a, a * 0.784) : vec4(0.0); }");
        prog = Fn<CreateProgramFn>("glCreateProgram")(); Fn<AttachFn>("glAttachShader")(prog, vs); Fn<AttachFn>("glAttachShader")(prog, fs);
        Fn<UIntFn>("glLinkProgram")(prog);
        Fn<UIntFn>("glUseProgram")(prog); Fn<Uniform1iFn>("glUniform1i")(Fn<UniformLocFn>("glGetUniformLocation")(prog, "t"), 0); Fn<UIntFn>("glUseProgram")(0);
        glEnable(0x0BE2); glBlendFunc(1, 1);
        glDisable(0x0B71);
        glEnable(0x0B10);
        glEnableClientState(0x8074); glEnableClientState(0x8076);
    }
}
