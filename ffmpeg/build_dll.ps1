Add-Type -OutputAssembly "W:\ffmpeg\XpsCamProcAmp.dll" -OutputType Library -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;

namespace XpsCam {
    [ComImport, Guid("29840822-5B84-11D0-BD3B-00A0C911CE86"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface ICreateDevEnum {
        [PreserveSig] int CreateClassEnumerator(ref Guid pType, out IEnumMoniker ppEnumMoniker, int dwFlags);
    }
    [ComImport, Guid("C6E13360-30AC-11d0-A18C-00A0C9118956"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IAMVideoProcAmp {
        [PreserveSig] int GetRange(int Property, out int pMin, out int pMax, out int pSteppingDelta, out int pDefault, out int pCapsFlags);
        [PreserveSig] int Set(int Property, int lValue, int Flags);
        [PreserveSig] int Get(int Property, out int lValue, out int Flags);
    }
    public static class NativeBind {
        [DllImport("ole32.dll")] public static extern int CreateBindCtx(int reserved, out IBindCtx ppbc);
    }
    public static class ProcAmp {
        static readonly Guid CLSID_SystemDeviceEnum = new Guid("62BE5D10-60EB-11d0-BD3B-00A0C911CE86");
        static readonly Guid CLSID_VideoInputDeviceCategory = new Guid("860BB310-5D01-11d0-BD3B-00A0C911CE86");
        static readonly Guid IID_IAMVideoProcAmp = new Guid("C6E13360-30AC-11d0-A18C-00A0C9118956");
        public static int[] GetValues() {
            int[] props = {0,1,2,3,4,5,7,8};
            int[] result = new int[props.Length];
            try {
                var devEnum = (ICreateDevEnum)Activator.CreateInstance(Type.GetTypeFromCLSID(CLSID_SystemDeviceEnum));
                IEnumMoniker enumMoniker;
                Guid cat = CLSID_VideoInputDeviceCategory;
                devEnum.CreateClassEnumerator(ref cat, out enumMoniker, 0);
                IMoniker[] monikers = new IMoniker[1];
                enumMoniker.Next(1, monikers, IntPtr.Zero);
                IBindCtx bindCtx;
                NativeBind.CreateBindCtx(0, out bindCtx);
                object obj;
                Guid iid = IID_IAMVideoProcAmp;
                monikers[0].BindToObject(bindCtx, null, ref iid, out obj);
                var vp = (IAMVideoProcAmp)obj;
                for (int i = 0; i < props.Length; i++) {
                    int val, flags;
                    result[i] = (vp.Get(props[i], out val, out flags) == 0) ? val : -9999;
                }
                Marshal.ReleaseComObject(obj);
                Marshal.ReleaseComObject(monikers[0]);
                Marshal.ReleaseComObject(enumMoniker);
                Marshal.ReleaseComObject(devEnum);
            } catch {
                for (int i = 0; i < result.Length; i++) result[i] = -9999;
            }
            return result;
        }
    }
}
"@
"built"
