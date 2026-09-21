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
// Входы: собственный ffmpeg из чистого xps_sub (160x90 yuv444p 10 к/с); MQTT alena/aurora/data от HA (автоматизация
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
    const int W = 960, H = 540;
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
        try { foreach (var l in File.ReadAllLines(DIR + "toggles.txt", Encoding.UTF8)) { var p = l.Split('='); if (p.Length == 2 && On.ContainsKey(p[0])) On[p[0]] = p[1].Trim() == "1"; } } catch { }
    }
    static void SaveToggles()
    {
        try { lock (lk) File.WriteAllLines(DIR + "toggles.txt", GRAM.Select(n => n + "=" + (On[n] ? "1" : "0")).ToArray(), Encoding.UTF8); } catch { }
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
                            if (pcx >= 0) { motX = motX * 0.8 + (cx - pcx) * 0.2 * 10; motY = motY * 0.8 + (cy - pcy) * 0.2 * 10; }
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
    static readonly string[] PRESETS = GRAM.Concat(new[] { "Всё включить", "Всё выключить", "Сброс вида" }).ToArray();
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

    static void DrawWaterfall(List<Slice> hs, float[,] dist, DateTime now)
    {
        var P = Persp(48, (double)W / H, 0.1, 40); var Vw = LookAt(0, 1.45, 2.9 / zoom, 0, 0.3, -1.6); var Mo = Orbit(0, 0.4, -2); var M = Mul(P, Mul(Vw, Mo));
        Scene3D(P, Vw, Mo, 0, 0, W, H);
        const double X0 = -1.55, XW = 3.1;
        double d0 = Math.Sqrt(1.15 * 1.15 + (2.9 / zoom) * (2.9 / zoom));
        Fog(d0 * 0.95, d0 + WF_D * 1.15);
        foreach (var s in hs)
        {
            double age = (now - s.t).TotalSeconds; if (age > 20) continue;
            if (s.wfV == null)
            {
                for (int x = 0; x + 1 < IW; x++)
                {
                    V(X0 + XW * x / (IW - 1), s.mean[x] / 255.0, 0, 0.25 * 0.95, 0.82 * 0.95, 0.95);
                    V(X0 + XW * (x + 1) / (IW - 1), s.mean[x + 1] / 255.0, 0, 0.25 * 0.95, 0.82 * 0.95, 0.95);
                }
                s.wfV = vb.ToArray(); s.wfC = cb.ToArray(); vb.Clear(); cb.Clear();
            }
            GL.glPushMatrix(); GL.glTranslatef(0, 0, (float)(-age / 20 * WF_D));
            GL.glVertexPointer(3, 0x1406, 0, s.wfV); GL.glColorPointer(4, 0x1406, 0, s.wfC); GL.glDrawArrays(0x0001, 0, s.wfV.Length / 3);
            GL.glPopMatrix();
        }
        GL.glDisable(0x0B60);
        if (dist != null)
        {
            for (int x = 0; x < IW; x++) for (int b = 0; b < YBINS; b++)
                {
                    float n = dist[x, b]; if (n <= 0) continue; double k = Math.Min(0.8, 0.12 + n / 20.0);
                    V(X0 + XW * x / (IW - 1), (b + 0.5) / YBINS, 0.02, 0.25 * k, 0.82 * k, 1 * k);
                }
            GL.glPointSize(2); Flush(0x0000);
        }
        double a = 0.35;
        V(X0, 0, 0, a, a, a); V(X0 + XW, 0, 0, a, a, a); V(X0, 0, 0, a, a, a); V(X0, 1, 0, a, a, a); V(X0, 0, 0, a, a, a); V(X0, 0, -WF_D, a, a, a);
        for (int i = 0; i <= 4; i++) { double y = i / 4.0; V(X0, y, 0, a * 0.6, a * 0.6, a * 0.6); V(X0 + XW, y, 0, a * 0.6, a * 0.6, a * 0.6); }
        var tick0 = new DateTime(now.Year, now.Month, now.Day, now.Hour, now.Minute, now.Second / 5 * 5);
        for (int i = 0; i <= 4; i++)
        {
            var tt = tick0.AddSeconds(-5 * i); double age = (now - tt).TotalSeconds; if (age > 20) continue; double z = -age / 20 * WF_D;
            V(X0, 0, z, a, a, a); V(X0 + XW, 0, z, a, a, a);
            float px, py; if (Proj(M, X0 - 0.05, 0, z, 0, 0, W, H, out px, out py)) L(tt.ToString("HH:mm:ss"), px, py - 8, cAx, 1);
        }
        Flush(0x0001);
        for (int i = 0; i <= 4; i++) { float px, py; if (Proj(M, X0 - 0.03, i / 4.0, 0, 0, 0, W, H, out px, out py)) L(((int)Math.Round(255 * i / 4.0)).ToString(), px, py - 7, cSig, 1); }
        float lx, ly; if (Proj(M, X0, 1.08, 0, 0, 0, W, H, out lx, out ly)) L("Сигнал · Y 0–255, вглубь — время", lx, ly - 8, cSig, 0);
    }

    // туман OpenGL (линейный, к чёрному): при аддитивном свечении чёрный = прозрачный — срезы гаснут с глубиной на GPU
    static void Fog(double start, double end)
    {
        GL.glEnable(0x0B60); GL.glFogi(0x0B65, 0x2601); GL.glFogf(0x0B63, (float)start); GL.glFogf(0x0B64, (float)end);
        GL.glFogfv(0x0B66, new float[] { 0, 0, 0, 0 });
    }
    static void HueColor(double u, double v, out double r, out double g, out double b)
    {
        double Y = 150, U = u * 32 * 3, Vv = v * 32 * 3;
        r = Math.Max(0, Math.Min(255, Y + 1.402 * Vv)) / 255; g = Math.Max(0, Math.Min(255, Y - 0.344 * U - 0.714 * Vv)) / 255; b = Math.Max(0, Math.Min(255, Y + 1.772 * U)) / 255;
    }
    static float[] tunM; static int tunVx;
    static void DrawTunnel(List<Slice> hs, DateTime now, bool points)
    {
        tunVx = (W - H) / 2;
        var P = Persp(58, 1, 0.1, 40); var Vw = LookAt(0.25, 0.35, 1.9 / zoom, 0, 0, -2.5); var Mo = Orbit(0, 0, -3); var M = Mul(P, Mul(Vw, Mo)); tunM = M;
        Scene3D(P, Vw, Mo, tunVx, 0, H, H);
        if (!points) return;
        GL.glPointSize(2);
        Fog(1.9 / zoom * 0.95, 1.9 / zoom + TN_D * 1.1);
        foreach (var s in hs)
        {
            double age = (now - s.t).TotalSeconds; if (age > 20) continue;
            if (s.tnV == null)
            {
                for (int i = 0; i < UVB; i++) for (int j = 0; j < UVB; j++)
                    {
                        float n = s.uv[i, j]; if (n <= 0) continue;
                        double u = (i + 0.5) / UVB * 2 - 1, v = (j + 0.5) / UVB * 2 - 1, r, g, b; HueColor(u, v, out r, out g, out b);
                        double k = Math.Min(0.9, 0.1 + n / 40.0);   // срез теперь из 1 кадра, а не из 2 — порог вдвое ниже
                        V(u, v, 0, r * k, g * k, b * k);
                    }
                s.tnV = vb.ToArray(); s.tnC = cb.ToArray(); vb.Clear(); cb.Clear();
            }
            if (s.tnV.Length == 0) continue;
            GL.glPushMatrix(); GL.glTranslatef(0, 0, (float)(-age / 20 * TN_D));
            GL.glVertexPointer(3, 0x1406, 0, s.tnV); GL.glColorPointer(4, 0x1406, 0, s.tnC); GL.glDrawArrays(0x0000, 0, s.tnV.Length / 3);
            GL.glPopMatrix();
        }
        GL.glDisable(0x0B60);
        const double pure = 118.0;
        var tick0 = new DateTime(now.Year, now.Month, now.Day, now.Hour, now.Minute, now.Second / 5 * 5);
        for (int ti = -1; ti <= 4; ti++)
        {
            double z = 0, k = 0.55; DateTime tt = now;
            if (ti >= 0) { tt = tick0.AddSeconds(-5 * ti); double age = (now - tt).TotalSeconds; if (age > 20) continue; z = -age / 20 * TN_D; k = 0.28 * (1 - age / 20) + 0.08; }
            foreach (int p in new[] { 5, 10, 15, 20, 25 })
            {
                if (ti >= 0 && p != 25) continue;
                double rad = pure * p / 100 / 32;
                for (int a = 0; a < 72; a++)
                {
                    double a0 = a * Math.PI / 36, a1 = (a + 1) * Math.PI / 36;
                    V(rad * Math.Cos(a0), rad * Math.Sin(a0), z, k, 0.38 * k, 0.82 * k); V(rad * Math.Cos(a1), rad * Math.Sin(a1), z, k, 0.38 * k, 0.82 * k);
                }
                float px, py;
                if (ti < 0 && Proj(M, -rad * 0.7071, -rad * 0.7071, 0, tunVx, 0, H, H, out px, out py)) L(p + " %", px - 4, py, cVec, 1);
            }
            if (ti >= 0) { float px, py; if (Proj(M, pure * 0.25 / 32, 0, z, tunVx, 0, H, H, out px, out py)) L(tt.ToString("HH:mm:ss"), px + 4, py - 7, cAx, 0); }
        }
        var hues = new object[][] {
            new object[] { "Кр", -38, 112, Color.FromArgb(255, 80, 80) }, new object[] { "Пр", 74, 94, Color.FromArgb(255, 90, 230) },
            new object[] { "Сн", 112, -18, Color.FromArgb(110, 150, 255) }, new object[] { "Гл", 38, -112, Color.FromArgb(90, 230, 255) },
            new object[] { "Зл", -74, -94, Color.FromArgb(100, 255, 110) }, new object[] { "Жл", -112, 18, Color.FromArgb(255, 230, 90) } };
        double rr = pure * 0.27 / 32;
        foreach (var h in hues)
        {
            double du = (int)h[1], dv = (int)h[2], l = Math.Sqrt(du * du + dv * dv), x = du / l * rr, y = dv / l * rr;
            V(0, 0, 0, 0.2, 0.08, 0.16); V(x, y, 0, 0.2, 0.08, 0.16);
            float px, py; if (Proj(M, x * 1.08, y * 1.08, 0, tunVx, 0, H, H, out px, out py)) L((string)h[0], px, py - 8, (Color)h[3], 2);
        }
        Flush(0x0001);
        float lx, ly; if (Proj(M, 0, -pure * 0.25 / 32 - 0.12, 0, tunVx, 0, H, H, out lx, out ly)) L("Вектор · насыщенность, % от чистого цвета; вглубь — время", lx, ly, cVec, 2);
    }

    // направления — стрелки в тоннеле (север/верх кадра — вверх), след по глубине
    static void DrawDirections(DateTime now)
    {
        if (tunM == null) return;
        List<Pt> wb, ws, mx, my; lock (lk) { wb = Copy("wind_b"); ws = Copy("wind_s"); mx = Copy("mot_x"); my = Copy("mot_y"); }
        var cW = Color.FromArgb(230, 240, 255); var cM = Color.FromArgb(120, 255, 140);
        // ветер: откуда дует (метео) → куда: +180°
        for (int i = 0; i < ws.Count && i < wb.Count; i++)
        {
            double age = (now - ws[i].t).TotalSeconds; if (age > 20) continue;
            double a = (wb[i].v + 180) * Math.PI / 180, len = Math.Min(1, ws[i].v / 20) * 0.9, z = -age / 20 * TN_D, k = 0.15 + 0.6 * (1 - age / 20);
            V(0, 0, z, cW.R / 255.0 * k * 0.3, cW.G / 255.0 * k * 0.3, cW.B / 255.0 * k * 0.3); V(Math.Sin(a) * len, Math.Cos(a) * len, z, cW.R / 255.0 * k, cW.G / 255.0 * k, cW.B / 255.0 * k);
        }
        for (int i = 0; i < mx.Count && i < my.Count; i += 2)
        {
            double age = (now - mx[i].t).TotalSeconds; if (age > 20) continue;
            double vx = mx[i].v, vy = -my[i].v, l = Math.Sqrt(vx * vx + vy * vy); if (l < 0.02) continue;
            double len = Math.Min(0.9, l * 3), z = -age / 20 * TN_D, k = 0.1 + 0.7 * (1 - age / 20);
            V(0, 0, z, 0, 0, 0); V(vx / l * len, vy / l * len, z, cM.R / 255.0 * k, cM.G / 255.0 * k, cM.B / 255.0 * k);
        }
        GL.glLineWidth(2); Flush(0x0001); GL.glLineWidth(1);
        if (ws.Count > 0 && wb.Count > 0)
        {
            double a = (wb[wb.Count - 1].v + 180) * Math.PI / 180, len = Math.Min(1, ws[ws.Count - 1].v / 20) * 0.9; float px, py;
            string[] dirs = { "С", "СВ", "В", "ЮВ", "Ю", "ЮЗ", "З", "СЗ" };
            string from = dirs[(int)Math.Round(wb[wb.Count - 1].v / 45) % 8];
            object u; string unit; lock (lk) unit = mq.TryGetValue("wind_unit", out u) ? (string)u : "";
            if (Proj(tunM, Math.Sin(a) * (len + 0.1), Math.Cos(a) * (len + 0.1), 0, tunVx, 0, H, H, out px, out py)) L("ветер " + F(ws[ws.Count - 1].v, "0.##") + " " + unit + " · из " + from, px, py - 8, cW, 2);
        }
    }
    static List<Pt> Copy(string k) { List<Pt> l; return series.TryGetValue(k, out l) ? new List<Pt>(l) : new List<Pt>(); }

    // наборы — водопад столбиков (ядра XPS, каналы электричества)
    static void DrawSets(DateTime now)
    {
        List<SetSlice> ch, eh; string[] en; lock (lk) { ch = new List<SetSlice>(coresHist); eh = new List<SetSlice>(elecHist); en = elecNames; }
        int vx = (int)(W * 0.33), vy = (int)(H * 0.52), vw = (int)(W * 0.34), vh = (int)(H * 0.36);
        var P = Persp(40, (double)vw / vh, 0.1, 40); var Vw = LookAt(0, 1.5, 3.2 / zoom, 0, 0.2, -1); var Mo = Orbit(0, 0, -1); var M = Mul(P, Mul(Vw, Mo));
        Scene3D(P, Vw, Mo, vx, vy, vw, vh);
        var cC = Color.FromArgb(255, 150, 70); var cE = Color.FromArgb(255, 220, 90);
        Action<List<SetSlice>, double, double, Func<double, double>, Color> bars = (hs, x0, xw, norm, c) =>
        {
            foreach (var s in hs)
            {
                double age = (now - s.t).TotalSeconds; if (age > 20 || s.v == null) continue;
                double z = -age / 20 * 2.5, k = 0.15 + 0.75 * (1 - age / 20); int n = s.v.Length;
                for (int i = 0; i < n; i++)
                {
                    double x = x0 + xw * (i + 0.5) / n, h = Math.Max(0.01, norm(s.v[i]));
                    V(x, 0, z, c.R / 255.0 * k * 0.4, c.G / 255.0 * k * 0.4, c.B / 255.0 * k * 0.4); V(x, h, z, c.R / 255.0 * k, c.G / 255.0 * k, c.B / 255.0 * k);
                }
            }
        };
        bars(ch, -1.6, 1.0, v => (v - 30) / 70, cC);
        bars(eh, -0.4, 2.0, v => Math.Sqrt(Math.Max(0, v) / 6000), cE);
        GL.glLineWidth(4); Flush(0x0001); GL.glLineWidth(1);
        float px, py;
        if (ch.Count > 0 && ch[0].v != null && Proj(M, -1.1, -0.12, 0, vx, vy, vw, vh, out px, out py)) L("ядра XPS " + string.Join(" ", ch[0].v.Select(v => F(v, "0.#"))) + " °C", px, py, cC, 2);
        // каналы электричества: над столбиком — только номер, расшифровка — две колонки под сценой
        // (над столбиками подписи налезали друг на друга, справа — на колонку ауры)
        if (eh.Count > 0 && eh[0].v != null)
            for (int i = 0; i < en.Length && i < eh[0].v.Length; i++)
            {
                if (Proj(M, -0.4 + 2.0 * (i + 0.5) / en.Length, Math.Sqrt(Math.Max(0, eh[0].v[i]) / 6000) + 0.06, 0, vx, vy, vw, vh, out px, out py)) L((i + 1).ToString(), px, py - 12, cE, 2);
                L((i + 1) + ". " + en[i] + " " + F(eh[0].v[i] / 1000, "0.##") + " кВт", vx + (i / 4) * vw / 2f, H - vy + 4 + (i % 4) * 13, cE, 0);
            }
        if (Proj(M, 0.6, -0.12, 0, vx, vy, vw, vh, out px, out py)) L("электричество 1…" + en.Length + " (слева направо)", px, py, cE, 2);
    }

    // 2D: свечение рамки
    static void Glow(double t, double inset, double thick, Color c, double k)
    {
        double a = inset, b = inset + thick;
        Action<double, double, double, double, double, double, double, double> q = (x0, y0, x1, y1, x2, y2, x3, y3) =>
        { Vc(x0, y0, c, k); Vc(x1, y1, c, k); Vc(x2, y2, c, 0); Vc(x3, y3, c, 0); };
        q(a, a, W - a, a, W - b, b, b, b); q(W - a, a, W - a, H - a, W - b, H - b, W - b, b);
        q(W - a, H - a, a, H - a, b, H - b, W - b, H - b); q(a, H - a, a, a, b, b, b, H - b);
        Flush(0x0007);
    }
    static Color TempColor(double t)
    {
        double[] ts = { 45, 65, 80, 95 }; Color[] cs = { Color.FromArgb(60, 110, 255), Color.FromArgb(60, 255, 120), Color.FromArgb(255, 220, 60), Color.FromArgb(255, 50, 50) };
        if (t <= ts[0]) return cs[0]; if (t >= ts[3]) return cs[3];
        for (int i = 0; i < 3; i++) if (t <= ts[i + 1]) { double f = (t - ts[i]) / (ts[i + 1] - ts[i]); return Color.FromArgb((int)(cs[i].R + (cs[i + 1].R - cs[i].R) * f), (int)(cs[i].G + (cs[i + 1].G - cs[i].G) * f), (int)(cs[i].B + (cs[i + 1].B - cs[i].B) * f)); }
        return cs[3];
    }

    // 2D: линия по подвижной оси времени, значения нормируются в полосу [y0, y1]
    static void TimeLine(List<Pt> l, DateTime now, double lo, double hi, double y0, double y1, Color c, double k)
    {
        for (int i = 0; i + 1 < l.Count; i++)
        {
            if (double.IsNaN(l[i].v) || double.IsNaN(l[i + 1].v)) continue;
            double xa = TX(l[i].t, now), xb = TX(l[i + 1].t, now); if (xb < 0) continue;
            double ya = y1 - (y1 - y0) * Math.Max(0, Math.Min(1, (l[i].v - lo) / (hi - lo))), yb = y1 - (y1 - y0) * Math.Max(0, Math.Min(1, (l[i + 1].v - lo) / (hi - lo)));
            Vc(xa, ya, c, k); Vc(xb, yb, c, k);
        }
    }
    static readonly Dictionary<string, List<double>> particles = new Dictionary<string, List<double>>();
    static readonly Random rnd = new Random();
    static void Draw2D(DateTime now, double dt)
    {
        Scene2D();
        bool anyTime = G("Свет и Движение") || G("Линии") || G("События") || G("Состояния");
        Dictionary<string, List<Pt>> s; lock (lk) s = series.ToDictionary(kv => kv.Key, kv => new List<Pt>(kv.Value));
        Func<string, List<Pt>> S = k => s.ContainsKey(k) ? s[k] : new List<Pt>();
        if (G("Свечение"))
        {
            var p = S("cpu_pkg"); double t = p.Count > 0 ? p[p.Count - 1].v : 0; var c = TempColor(t);
            Glow(t, 0, 22, c, 0.75);
            var d = S("det_fps"); double f = d.Count > 0 ? d[d.Count - 1].v : 0;
            Glow(f, 22, 10, Color.FromArgb(255, 80, 230), Math.Min(1, f / 30) * 0.8);
            L("свечение: CPU XPS " + F(t, "0.#") + " °C (до TjMax " + F(103 - t, "0.#") + ") · детектор Frigate " + F(f, "0.#") + " к/с", W / 2f, H - 64, c, 2);
        }
        if (anyTime)
        {
            var t0 = new DateTime(now.Year, now.Month, now.Day, now.Hour, now.Minute, now.Second / 10 * 10);
            for (int i = 0; i <= 8; i++) { var tt = t0.AddSeconds(-10 * i); double x = TX(tt, now); if (x < 0) continue; Vc(x, 0, cAx, 0.16); Vc(x, H, cAx, 0.16); L(tt.ToString("HH:mm:ss"), (float)x, H - 16, cAx, 2); }
            Flush(0x0001);
        }
        if (G("Свет и Движение"))
        {
            TimeLine(S("light"), now, 0, 255, 70, H - 70, Color.FromArgb(255, 140, 58), 0.95);
            TimeLine(S("motion"), now, 0, 24, 70, H - 70, Color.FromArgb(96, 255, 128), 0.95);
            GL.glLineWidth(2); Flush(0x0001); GL.glLineWidth(1);
            for (int i = 0; i <= 4; i++)
            {
                float y = (float)(H - 70 - (H - 140) * i / 4.0);
                L(((int)Math.Round(255 * i / 4.0)).ToString(), W / 2f - 4, y - 7, Color.FromArgb(255, 170, 110), 1); L((6 * i).ToString(), W / 2f + 4, y - 7, Color.FromArgb(120, 255, 150), 0);
            }
            L("Свет Y 0–255", W / 2f - 4, 52, Color.FromArgb(255, 170, 110), 1); L("Движение ΔY 0–24", W / 2f + 4, 52, Color.FromArgb(120, 255, 150), 0);
        }
        if (G("Линии"))
        {
            var to = S("t_out"); var pw = S("power_kw");
            TimeLine(to, now, 10, 40, 110, H - 110, Color.FromArgb(150, 225, 255), 0.9);
            TimeLine(pw, now, 0, 8, 110, H - 110, Color.FromArgb(255, 205, 90), 0.9);
            GL.glLineWidth(2); Flush(0x0001); GL.glLineWidth(1);
            if (to.Count > 0) L("улица " + F(to[to.Count - 1].v, "0.#") + " °C", W - 6, (float)(H - 110 - (H - 220) * Math.Max(0, Math.Min(1, (to[to.Count - 1].v - 10) / 30))) - 18, Color.FromArgb(150, 225, 255), 1);
            if (pw.Count > 0) L("дом " + F(pw[pw.Count - 1].v, "0.##") + " кВт", W - 6, (float)(H - 110 - (H - 220) * Math.Max(0, Math.Min(1, pw[pw.Count - 1].v / 8))) + 4, Color.FromArgb(255, 205, 90), 1);
        }
        if (G("События"))
        {
            List<Ev> ev; lock (lk) ev = new List<Ev>(events);
            foreach (var e in ev)
            {
                double x = TX(e.t, now), age = (now - e.t).TotalSeconds; if (x < 0) continue;
                double k = age < 2 ? 1 : 0.55; Vc(x, 64, e.c, k); Vc(x, H - 34, e.c, k * 0.2);
                L(e.src, (float)x + 3, 64 + (float)((e.t.Second % 4) * 13), e.c, 0);
            }
            GL.glLineWidth(2); Flush(0x0001); GL.glLineWidth(1);
        }
        if (G("Состояния"))
        {
            var rows = new[] { new object[] { "st_andrey", "Андрей снаружи", Color.FromArgb(120, 255, 170) }, new object[] { "st_boiler", "бойлер", Color.FromArgb(255, 120, 80) },
                new object[] { "st_locked", "XPS заблокирован", Color.FromArgb(170, 150, 255) }, new object[] { "st_vpn", "VPN", Color.FromArgb(90, 200, 255) } };
            for (int r = 0; r < rows.Length; r++)
            {
                var l = S((string)rows[r][0]); var c = (Color)rows[r][2]; double y = H - 36 - r * 9;
                for (int i = 0; i + 1 < l.Count; i++) if (l[i].v > 0.5) { double xa = Math.Max(0, TX(l[i].t, now)), xb = TX(l[i + 1].t, now); Vc(xa, y, c, 0.7); Vc(xb, y, c, 0.7); Vc(xb, y - 6, c, 0.7); Vc(xa, y - 6, c, 0.7); }
                bool onNow = l.Count > 0 && l[l.Count - 1].v > 0.5;
                L((string)rows[r][1] + (onNow ? " · да" : " · нет"), W - 4, (float)y - 10, onNow ? c : Color.FromArgb(150, 150, 150), 1);   // справа: слева колонка ауры
            }
            Flush(0x0007);
        }
        if (G("Потоки"))
        {
            Dictionary<string, double> fl; lock (lk) fl = new Dictionary<string, double>(flows);
            int r = 0;
            foreach (var kv in fl)
            {
                double y = 104 + r * 24, x0 = W * 0.40, x1 = W * 0.60, rate = kv.Value, sp = Math.Log10(1 + rate / 1024) * 60;  // px/с
                List<double> ps; if (!particles.TryGetValue(kv.Key, out ps)) particles[kv.Key] = ps = new List<double>();
                for (int i = 0; i < ps.Count; i++) ps[i] += sp * dt;
                ps.RemoveAll(p => p > x1 - x0);
                if (rate > 1 && rnd.NextDouble() < Math.Min(0.9, Math.Log10(1 + rate / 1024) / 4)) ps.Add(0);
                var c = GCOL[9];
                foreach (var p in ps) { Vc(x0 + p, y, c, 0.9); Vc(x0 + p - Math.Min(18, sp * 0.15), y, c, 0.1); }
                Vc(x0, y + 5, c, 0.12); Vc(x1, y + 5, c, 0.12);
                string v = rate > 1048576 ? F(rate / 1048576, "0.##") + " МБ/с" : F(rate / 1024, "0.#") + " КБ/с";
                L(kv.Key + " " + v, (float)(x0 + x1) / 2, (float)y - 15, c, 2);   // над дорожкой: слева колонка ауры
                r++;
            }
            GL.glLineWidth(2); Flush(0x0001); GL.glLineWidth(1);
        }
        // легенда грамматик: ■ включено, □ выключено (переключатели — пресеты PTZ во Frigate)
        float lxp = 0; var parts = new List<Label>();
        for (int i = 0; i < GRAM.Length; i++) parts.Add(new Label { s = (G(GRAM[i]) ? "■ " : "□ ") + GRAM[i], c = G(GRAM[i]) ? GCOL[i] : Color.FromArgb(140, 140, 140) });
        legendParts = parts; lxp++;
    }
    static List<Label> legendParts = new List<Label>();

    // 25 к/с (21.09, поздний вечер): текст GDI+ дорогой — каждая подпись рисуется один раз в маленькую картинку
    // (с чёрным контуром) и дальше только копируется; кэш сбрасывается, когда разрастается (метки времени меняются).
    static readonly Dictionary<string, Bitmap> textCache = new Dictionary<string, Bitmap>();
    static Bitmap TextBmp(string s, Color c, Font font, StringFormat fmt)
    {
        string key = c.ToArgb() + "|" + s; Bitmap b;
        if (textCache.TryGetValue(key, out b)) return b;
        if (textCache.Count > 600) { foreach (var v in textCache.Values) v.Dispose(); textCache.Clear(); }
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
        GL.Init(W, H);
        Log("OpenGL: " + GL.Renderer);
        var px = new byte[W * H * 4]; var outb = new byte[W * H * 4];
        var font = new Font("Consolas", 12f, FontStyle.Bold, GraphicsUnit.Pixel);
        var shadow = new SolidBrush(Color.FromArgb(200, 0, 0, 0));
        var fmt = StringFormat.GenericTypographic;
        DateTime prev = DateTime.Now;
        while (true)
        {
            NamedPipeServerStream pipe = null;
            try
            {
                pipe = new NamedPipeServerStream("aurora3d", PipeDirection.Out, 1, PipeTransmissionMode.Byte, PipeOptions.None, 0, W * H * 4);
                pipe.WaitForConnection();
                Log("читатель подключился");
                var sw = Stopwatch.StartNew(); long frame = 0; int statN = 0; double statMs = 0, statW = 0; DateTime statT = DateTime.Now;
                while (pipe.IsConnected)
                {
                    var now = DateTime.Now; double dt = Math.Min(0.5, (now - prev).TotalSeconds); prev = now;
                    lock (lk) { yaw += vYaw * dt * 1.2; pitch = Math.Max(-1.2, Math.Min(1.2, pitch + vPitch * dt * 0.8)); zoom = Math.Max(0.4, Math.Min(3, zoom * Math.Exp(vZoom * dt))); }
                    List<Slice> hs; float[,] dist;
                    lock (lk) { hs = new List<Slice>(hist); dist = frontDist; }
                    labels.Clear(); tunM = null;
                    GL.glClearColor(0, 0, 0, 0); GL.glClear(0x4000 | 0x100);
                    if (haveInput && G("Сигнал")) DrawWaterfall(hs, dist, now);
                    if (haveInput && (G("Вектор") || G("Направления"))) DrawTunnel(hs, now, G("Вектор"));
                    if (G("Направления")) DrawDirections(now);
                    if (G("Наборы")) DrawSets(now);
                    Draw2D(now, dt);
                    GL.glFinish();
                    GL.glReadPixels(0, 0, W, H, 0x80E1, 0x1401, px);
                    for (int y = 0; y < H; y++)
                    {
                        int si = (H - 1 - y) * W * 4, di = y * W * 4;
                        for (int x = 0; x < W; x++, si += 4, di += 4)
                        {
                            int b = px[si], g = px[si + 1], r = px[si + 2], a = Math.Max(r, Math.Max(g, b));
                            if (a == 0) { outb[di] = outb[di + 1] = outb[di + 2] = outb[di + 3] = 0; continue; }
                            int ia = a << 8; outb[di] = unpm[ia + b]; outb[di + 1] = unpm[ia + g]; outb[di + 2] = unpm[ia + r]; outb[di + 3] = (byte)(a * 200 / 255);
                        }
                    }
                    var h = GCHandle.Alloc(outb, GCHandleType.Pinned);
                    try
                    {
                        using (var bmp = new Bitmap(W, H, W * 4, PixelFormat.Format32bppArgb, h.AddrOfPinnedObject()))
                        using (var gr = Graphics.FromImage(bmp))
                        {
                            gr.TextRenderingHint = System.Drawing.Text.TextRenderingHint.AntiAlias;
                            // легенда сверху по центру
                            // две строки по 5: в одну легенда заезжала на часы слева
                            var ws = legendParts.Select(p => (float)TextBmp(p.s, p.c, font, fmt).Width + 14).ToArray();
                            for (int row = 0; row < 2; row++)
                            {
                                int a0 = row * 5, a1 = Math.Min(legendParts.Count, a0 + 5); float lw = 0;
                                for (int i = a0; i < a1; i++) lw += ws[i];
                                float lx = (W - lw) / 2;
                                for (int i = a0; i < a1; i++) { var p = legendParts[i]; p.x = lx; p.y = 4 + row * 14; p.align = 0; labels.Add(p); lx += ws[i]; }
                            }
                            gr.CompositingMode = System.Drawing.Drawing2D.CompositingMode.SourceOver;
                            foreach (var l in labels)
                            {
                                var tb = TextBmp(l.s, l.c, font, fmt); float x = l.x - 1;
                                if (l.align == 1) x -= tb.Width - 3; else if (l.align == 2) x -= (tb.Width - 3) / 2f;
                                gr.DrawImageUnscaled(tb, (int)x, (int)l.y - 1);
                            }
                        }
                    }
                    finally { h.Free(); }
                    var tw0 = sw.Elapsed.TotalMilliseconds; pipe.Write(outb, 0, outb.Length); statW += sw.Elapsed.TotalMilliseconds - tw0;
                    frame++;
                    // 25 к/с (40 мс на кадр); раз в минуту — фактическая частота и время кадра в журнал
                    long due = (long)(frame * 40) - sw.ElapsedMilliseconds;
                    if (due > 0) Thread.Sleep((int)due); else if (due < -1000) { frame = sw.ElapsedMilliseconds / 40; }
                    statN++; statMs += (DateTime.Now - now).TotalMilliseconds;
                    if ((DateTime.Now - statT).TotalSeconds >= 20) { Log("кадров/с " + F(statN / (DateTime.Now - statT).TotalSeconds, "0.0") + ", кадр " + F(statMs / Math.Max(1, statN), "0.0") + " мс, из них запись в канал " + F(statW / Math.Max(1, statN), "0.0") + " мс"); statN = 0; statMs = 0; statW = 0; statT = DateTime.Now; }
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
        const uint FB = 0x8D40, RB = 0x8D41; uint fbo, rc, rd;
        Fn<GenFn>("glGenFramebuffersEXT")(1, out fbo); Fn<BindFn>("glBindFramebufferEXT")(FB, fbo);
        Fn<GenFn>("glGenRenderbuffersEXT")(1, out rc); Fn<BindFn>("glBindRenderbufferEXT")(RB, rc);
        Fn<StorageFn>("glRenderbufferStorageEXT")(RB, 0x8058, w, h); Fn<AttachRbFn>("glFramebufferRenderbufferEXT")(FB, 0x8CE0, RB, rc);
        Fn<GenFn>("glGenRenderbuffersEXT")(1, out rd); Fn<BindFn>("glBindRenderbufferEXT")(RB, rd);
        Fn<StorageFn>("glRenderbufferStorageEXT")(RB, 0x81A6, w, h); Fn<AttachRbFn>("glFramebufferRenderbufferEXT")(FB, 0x8D00, RB, rd);
        glEnable(0x0BE2); glBlendFunc(1, 1);
        glDisable(0x0B71);
        glEnable(0x0B10);
        glEnableClientState(0x8074); glEnableClientState(0x8076);
    }
}
