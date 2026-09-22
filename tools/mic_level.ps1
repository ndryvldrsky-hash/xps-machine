# Уровень и mute микрофонов XPS через CoreAudio (IAudioEndpointVolume); аргументы: имя-подстрока и уровень 0..1 (0 — только показать)
param([string]$Target = "нет", [double]$Level = 0)
$src = @'
using System; using System.Runtime.InteropServices;
[ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")] class MMDeviceEnumeratorCom2 {}
[Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)] interface IMMDeviceEnumerator2 { int EnumAudioEndpoints(int f, int s, out IMMDeviceCollection2 c); int GetDefaultAudioEndpoint(int f, int r, out IMMDevice2 d); }
[Guid("0BD7A1BE-7A1A-44DB-8397-CC5392387B5E"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)] interface IMMDeviceCollection2 { int GetCount(out int n); int Item(int i, out IMMDevice2 d); }
[Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)] interface IMMDevice2 { int Activate(ref Guid iid, int ctx, IntPtr p, [MarshalAs(UnmanagedType.IUnknown)] out object o); int OpenPropertyStore(int a, out IPropertyStore2 s); int GetId([MarshalAs(UnmanagedType.LPWStr)] out string id); }
[Guid("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)] interface IPropertyStore2 { int GetCount(out int n); int GetAt(int i, out PROPERTYKEY2 k); int GetValue(ref PROPERTYKEY2 k, out PROPVARIANT2 v); }
[StructLayout(LayoutKind.Sequential)] struct PROPERTYKEY2 { public Guid fmtid; public int pid; }
[StructLayout(LayoutKind.Explicit)] struct PROPVARIANT2 { [FieldOffset(0)] public short vt; [FieldOffset(8)] public IntPtr p; }
[Guid("5CDF2C82-841E-4546-9722-0CF74078229A"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)] interface IAudioEndpointVolume2 { int RegisterControlChangeNotify(IntPtr n); int UnregisterControlChangeNotify(IntPtr n); int GetChannelCount(out int c); int SetMasterVolumeLevel(float l, ref Guid g); int SetMasterVolumeLevelScalar(float l, ref Guid g); int GetMasterVolumeLevel(out float l); int GetMasterVolumeLevelScalar(out float l); int SetChannelVolumeLevel(int c, float l, ref Guid g); int SetChannelVolumeLevelScalar(int c, float l, ref Guid g); int GetChannelVolumeLevel(int c, out float l); int GetChannelVolumeLevelScalar(int c, out float l); int SetMute(bool m, ref Guid g); int GetMute(out bool m); }
public static class Aud2 {
  public static string Run(string target, float set) {
    var e = (IMMDeviceEnumerator2)new MMDeviceEnumeratorCom2(); string s = "";
    string[] roles = {"console","multimedia","communications"};
    for (int r = 0; r < 3; r++) { IMMDevice2 dd; e.GetDefaultAudioEndpoint(1, r, out dd); s += "default " + roles[r] + ": " + Name(dd) + "\n"; }
    IMMDeviceCollection2 c; e.EnumAudioEndpoints(1, 1, out c); int n; c.GetCount(out n);
    for (int i = 0; i < n; i++) { IMMDevice2 d; c.Item(i, out d); string name = Name(d); object o; Guid g = typeof(IAudioEndpointVolume2).GUID; d.Activate(ref g, 23, IntPtr.Zero, out o); var v = (IAudioEndpointVolume2)o; Guid z = Guid.Empty;
      if (name.Contains(target) && set > 0) { v.SetMasterVolumeLevelScalar(set, ref z); v.SetMute(false, ref z); }
      float lv; bool mu; v.GetMasterVolumeLevelScalar(out lv); v.GetMute(out mu); s += "  " + name + " level=" + (int)Math.Round(lv*100) + "% mute=" + mu + "\n"; }
    return s; }
  static string Name(IMMDevice2 d) { IPropertyStore2 ps; d.OpenPropertyStore(0, out ps); var k = new PROPERTYKEY2 { fmtid = new Guid("a45c254e-df1c-4efd-8020-67d146a850e0"), pid = 14 }; PROPVARIANT2 pv; ps.GetValue(ref k, out pv); return Marshal.PtrToStringUni(pv.p); } }
'@
Add-Type -TypeDefinition $src -ErrorAction Stop
[Aud2]::Run($Target, [float]$Level)
