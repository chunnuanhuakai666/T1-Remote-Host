param([string]$Mode = 'dump', [int]$Seconds = 60)

$ErrorActionPreference = 'Continue'

$cs = @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;

public static class HidProbe
{
    [StructLayout(LayoutKind.Sequential)]
    struct SP_DEVICE_INTERFACE_DATA {
        public int cbSize; public Guid InterfaceClassGuid; public int Flags; public IntPtr Reserved;
    }
    [StructLayout(LayoutKind.Sequential)]
    public struct HIDD_ATTRIBUTES {
        public int Size; public ushort VendorID; public ushort ProductID; public ushort VersionNumber;
    }
    [StructLayout(LayoutKind.Sequential)]
    public struct HIDP_CAPS {
        public ushort Usage; public ushort UsagePage;
        public ushort InputReportByteLength; public ushort OutputReportByteLength; public ushort FeatureReportByteLength;
        [MarshalAs(UnmanagedType.ByValArray, SizeConst = 17)] public ushort[] Reserved;
        public ushort NumberLinkCollectionNodes;
        public ushort NumberInputButtonCaps; public ushort NumberInputValueCaps; public ushort NumberInputDataIndices;
        public ushort NumberOutputButtonCaps; public ushort NumberOutputValueCaps; public ushort NumberOutputDataIndices;
        public ushort NumberFeatureButtonCaps; public ushort NumberFeatureValueCaps; public ushort NumberFeatureDataIndices;
    }

    const int DIGCF_PRESENT = 2, DIGCF_DEVICEINTERFACE = 0x10;
    const uint GENERIC_READ = 0x80000000, GENERIC_WRITE = 0x40000000;
    const uint FILE_SHARE_RW = 3, OPEN_EXISTING = 3;
    const int HidP_Input = 0, HidP_Output = 1, HidP_Feature = 2;
    const int HIDP_STATUS_SUCCESS = 0x00110000;

    [DllImport("setupapi.dll", CharSet = CharSet.Auto, SetLastError = true)]
    static extern IntPtr SetupDiGetClassDevs(ref Guid g, IntPtr e, IntPtr h, int f);
    [DllImport("setupapi.dll", CharSet = CharSet.Auto, SetLastError = true)]
    static extern bool SetupDiEnumDeviceInterfaces(IntPtr s, IntPtr d, ref Guid g, int i, ref SP_DEVICE_INTERFACE_DATA data);
    [DllImport("setupapi.dll", CharSet = CharSet.Auto, SetLastError = true)]
    static extern bool SetupDiGetDeviceInterfaceDetail(IntPtr s, ref SP_DEVICE_INTERFACE_DATA data, IntPtr detail, int size, ref int req, IntPtr devInfo);
    [DllImport("setupapi.dll")] static extern bool SetupDiDestroyDeviceInfoList(IntPtr s);

    [DllImport("kernel32.dll", CharSet = CharSet.Auto, SetLastError = true)]
    static extern IntPtr CreateFile(string name, uint access, uint share, IntPtr sa, uint disp, uint flags, IntPtr tpl);
    [DllImport("kernel32.dll", SetLastError = true)] static extern bool CloseHandle(IntPtr h);
    [DllImport("kernel32.dll", SetLastError = true)] static extern bool ReadFile(IntPtr h, byte[] buf, int n, out int read, IntPtr ov);

    [DllImport("hid.dll")] static extern bool HidD_GetAttributes(IntPtr h, ref HIDD_ATTRIBUTES a);
    [DllImport("hid.dll")] static extern bool HidD_GetPreparsedData(IntPtr h, out IntPtr p);
    [DllImport("hid.dll")] static extern bool HidD_FreePreparsedData(IntPtr p);
    [DllImport("hid.dll", CharSet = CharSet.Unicode)] static extern bool HidD_GetProductString(IntPtr h, StringBuilder b, int len);
    [DllImport("hid.dll", CharSet = CharSet.Unicode)] static extern bool HidD_GetManufacturerString(IntPtr h, StringBuilder b, int len);
    [DllImport("hid.dll", CharSet = CharSet.Unicode)] static extern bool HidD_GetSerialNumberString(IntPtr h, StringBuilder b, int len);
    [DllImport("hid.dll")] static extern int HidP_GetCaps(IntPtr prep, ref HIDP_CAPS caps);
    [DllImport("hid.dll")] static extern int HidP_GetButtonCaps(int type, byte[] caps, ref ushort len, IntPtr prep);
    [DllImport("hid.dll")] static extern int HidP_GetValueCaps(int type, byte[] caps, ref ushort len, IntPtr prep);
    [DllImport("hid.dll")] static extern int HidP_GetUsages(int type, ushort page, ushort linkColl, ushort[] list, ref uint len, IntPtr prep, byte[] report, uint reportLen);
    [DllImport("hid.dll")] static extern int HidP_GetUsageValue(int type, ushort page, ushort linkColl, ushort usage, out uint val, IntPtr prep, byte[] report, uint reportLen);

    static readonly string[] CONSUMER = {
        "0x30 Power","0x31 Reset","0x32 Sleep","0x34 Menu","0x40 MenuPick","0x41 MenuUp","0x42 MenuDown",
        "0x60 DataOnScreen","0xB0 Play","0xB1 Pause","0xB2 Record","0xB3 FastForward","0xB4 Rewind",
        "0xB5 ScanNextTrack","0xB6 ScanPreviousTrack","0xB7 Stop","0xCD PlayPause",
        "0xE2 Mute","0xE9 VolumeIncrement","0xEA VolumeDecrement","0xE8 Volume",
        "0xC3 CameraZoomIn","0x22 MediaSelectComputer","0x23 MediaSelectTV","0x24 MediaSelectWWW",
        "0x183 MediaSelectProgramGuide","0x1F2 ACBack","0x1F3 ACHome","0x1F4 ACForward",
        "0x221 ACSearch","0x223 ACGoTo","0x224 ACSetup","0x225 ACStop","0x226 ACRefresh",
        "0x227 ACBookmarks","0x22A ACBack","0x22B ACForward","0x18A AL_ConsumerControl"
    };
    static readonly string[] KEYBOARD = {
        "0x00 None","0x04 A","0x05 B","0x06 C","0x07 D","0x08 E","0x09 F","0x0A G","0x0B H","0x0C I",
        "0x0D J","0x0E K","0x0F L","0x10 M","0x11 N","0x12 O","0x13 P","0x14 Q","0x15 R","0x16 S",
        "0x17 T","0x18 U","0x19 V","0x1A W","0x1B X","0x1C Y","0x1D Z","0x27 0","0x28 Enter",
        "0x29 Escape","0x2A Backspace","0x2B Tab","0x2C Space","0x3A F1","0x3B F2","0x4F Right",
        "0x50 Left","0x51 Down","0x52 Up","0x65 Application","0x66 Power","0x68 F13","0x6F BrightnessUp",
        "0x70 BrightnessDown","0x74 Execute","0x75 Help","0x76 Menu","0x7F Mute","0x80 VolumeUp",
        "0x81 VolumeDown","0x9F Cancel","0xA9 Mute2","0xE0 LeftCtrl","0xE1 LeftShift","0xE2 LeftAlt",
        "0xE3 LeftGUI","0xE8 MediaPlayer"
    };

    static string NameOf(ushort page, ushort usage)
    {
        if (page == 0x0C) {
            foreach (var s in CONSUMER) if (s.StartsWith(string.Format("0x{0:X}", usage))) return s.Substring(s.IndexOf(' ') + 1);
        }
        if (page == 0x07) {
            foreach (var s in KEYBOARD) if (s.StartsWith(string.Format("0x{0:X2}", usage))) return s.Substring(s.IndexOf(' ') + 1);
        }
        if (page == 0x09) return "Button" + usage;
        return null;
    }

    public static List<string> FindPaths(string filter)
    {
        var res = new List<string>();
        Guid guid = new Guid("4d1e55b2-f16f-11cf-88cb-001111000030");
        IntPtr set = SetupDiGetClassDevs(ref guid, IntPtr.Zero, IntPtr.Zero, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE);
        if (set == IntPtr.Zero || set.ToInt64() == -1) return res;
        var did = new SP_DEVICE_INTERFACE_DATA();
        did.cbSize = Marshal.SizeOf(did);
        for (int i = 0; ; i++) {
            if (!SetupDiEnumDeviceInterfaces(set, IntPtr.Zero, ref guid, i, ref did)) break;
            int req = 0;
            SetupDiGetDeviceInterfaceDetail(set, ref did, IntPtr.Zero, 0, ref req, IntPtr.Zero);
            IntPtr buf = Marshal.AllocHGlobal(req);
            try {
                Marshal.WriteInt32(buf, IntPtr.Size == 8 ? 8 : 6);
                if (SetupDiGetDeviceInterfaceDetail(set, ref did, buf, req, ref req, IntPtr.Zero)) {
                    string p = Marshal.PtrToStringAuto(IntPtr.Add(buf, 4));
                    if (string.IsNullOrEmpty(filter) || p.ToLower().Contains(filter.ToLower())) res.Add(p);
                }
            } finally { Marshal.FreeHGlobal(buf); }
        }
        SetupDiDestroyDeviceInfoList(set);
        return res;
    }

    public static void Dump(string filter)
    {
        var paths = FindPaths(filter);
        Console.WriteLine("MATCHING HID INTERFACES: " + paths.Count);
        foreach (var path in paths) {
            IntPtr h = CreateFile(path, GENERIC_READ | GENERIC_WRITE, FILE_SHARE_RW, IntPtr.Zero, OPEN_EXISTING, 0, IntPtr.Zero);
            if (h.ToInt64() == -1) { Console.WriteLine("OPEN FAIL " + Marshal.GetLastWin32Error() + " " + path); continue; }
            try {
                var at = new HIDD_ATTRIBUTES(); at.Size = Marshal.SizeOf(at);
                HidD_GetAttributes(h, ref at);
                var sbP = new StringBuilder(260); HidD_GetProductString(h, sbP, 260);
                var sbM = new StringBuilder(260); HidD_GetManufacturerString(h, sbM, 260);
                var sbS = new StringBuilder(260); HidD_GetSerialNumberString(h, sbS, 260);
                IntPtr prep;
                if (!HidD_GetPreparsedData(h, out prep)) { Console.WriteLine("PREPARSED FAIL"); continue; }
                try {
                    var caps = new HIDP_CAPS();
                    int st = HidP_GetCaps(prep, ref caps);
                    Console.WriteLine("");
                    Console.WriteLine("=== HID COLLECTION ===");
                    Console.WriteLine("PATH    : " + path);
                    Console.WriteLine(string.Format("VID/PID : {0:X4}/{1:X4}  ver {2:X4}", at.VendorID, at.ProductID, at.VersionNumber));
                    Console.WriteLine("PRODUCT : " + sbP);
                    Console.WriteLine("MFR     : " + sbM);
                    Console.WriteLine("SERIAL  : " + sbS);
                    Console.WriteLine(string.Format("CAPS    : usagePage=0x{0:X4} usage=0x{1:X4}  IN={2}B OUT={3}B FEAT={4}B",
                        caps.UsagePage, caps.Usage, caps.InputReportByteLength, caps.OutputReportByteLength, caps.FeatureReportByteLength));

                    ushort n = caps.NumberInputButtonCaps;
                    if (n > 0) {
                        byte[] buf = new byte[n * 72];
                        if (HidP_GetButtonCaps(HidP_Input, buf, ref n, prep) == HIDP_STATUS_SUCCESS) {
                            Console.WriteLine("INPUT BUTTON CAPS (" + n + "):");
                            for (int i = 0; i < n; i++) {
                                int o = i * 72;
                                ushort up = BitConverter.ToUInt16(buf, o);
                                byte rid = buf[o + 2];
                                ushort link = BitConverter.ToUInt16(buf, o + 6);
                                ushort linkUsage = BitConverter.ToUInt16(buf, o + 8);
                                bool isRange = buf[o + 12] != 0;
                                ushort lo, hi, dmin, dmax;
                                if (isRange) { lo = BitConverter.ToUInt16(buf, o + 56); hi = BitConverter.ToUInt16(buf, o + 58); dmin = BitConverter.ToUInt16(buf, o + 68); dmax = BitConverter.ToUInt16(buf, o + 70); }
                                else { lo = BitConverter.ToUInt16(buf, o + 56); hi = lo; dmin = BitConverter.ToUInt16(buf, o + 72); dmax = dmin; }
                                Console.WriteLine(string.Format("  rptID=0x{0:X2} page=0x{1:X2} usage=0x{2:X}..0x{3:X} dataIdx={4}..{5} linkColl={6}(usage 0x{7:X}) range={8}",
                                    rid, up, lo, hi, dmin, dmax, link, linkUsage, isRange ? 1 : 0));
                            }
                        } else Console.WriteLine("BUTTON CAPS FAIL");
                    }
                    ushort nv = caps.NumberInputValueCaps;
                    if (nv > 0) {
                        byte[] buf = new byte[nv * 72];
                        if (HidP_GetValueCaps(HidP_Input, buf, ref nv, prep) == HIDP_STATUS_SUCCESS) {
                            Console.WriteLine("INPUT VALUE CAPS (" + nv + "):");
                            for (int i = 0; i < nv; i++) {
                                int o = i * 72;
                                ushort up = BitConverter.ToUInt16(buf, o);
                                byte rid = buf[o + 2];
                                ushort bitsz = BitConverter.ToUInt16(buf, o + 18);
                                ushort rptcnt = BitConverter.ToUInt16(buf, o + 20);
                                int lmin = BitConverter.ToInt32(buf, o + 40), lmax = BitConverter.ToInt32(buf, o + 44);
                                ushort lo = BitConverter.ToUInt16(buf, o + 56), hi = BitConverter.ToUInt16(buf, o + 58);
                                Console.WriteLine(string.Format("  rptID=0x{0:X2} page=0x{1:X2} usage=0x{2:X}..0x{3:X} bits={4} count={5} logical={6}..{7}",
                                    rid, up, lo, hi, bitsz, rptcnt, lmin, lmax));
                            }
                        } else Console.WriteLine("VALUE CAPS FAIL");
                    }
                } finally { HidD_FreePreparsedData(prep); }
            } finally { CloseHandle(h); }
        }
    }

    public static void Listen(string filter, int seconds)
    {
        var paths = FindPaths(filter);
        var handles = new List<IntPtr>();
        var preps = new List<IntPtr>();
        var capsL = new List<HIDP_CAPS>();
        var bufL = new List<byte[]>();
        foreach (var path in paths) {
            IntPtr h = CreateFile(path, GENERIC_READ | GENERIC_WRITE, FILE_SHARE_RW, IntPtr.Zero, OPEN_EXISTING, 0, IntPtr.Zero);
            if (h.ToInt64() == -1) { Console.WriteLine("OPEN FAIL " + Marshal.GetLastWin32Error() + " " + path); continue; }
            IntPtr prep;
            if (!HidD_GetPreparsedData(h, out prep)) { CloseHandle(h); continue; }
            var caps = new HIDP_CAPS();
            HidP_GetCaps(prep, ref caps);
            if (caps.InputReportByteLength == 0) { HidD_FreePreparsedData(prep); CloseHandle(h); continue; }
            handles.Add(h); preps.Add(prep); capsL.Add(caps);
            bufL.Add(new byte[caps.InputReportByteLength]);
            Console.WriteLine(string.Format("LISTENING on rptID-capable collection IN={0}B  {1}", caps.InputReportByteLength, path));
        }
        Console.WriteLine("READY - press buttons now (" + seconds + "s)");
        Console.Out.Flush();
        var sw = System.Diagnostics.Stopwatch.StartNew();
        var th = new List<System.Threading.Thread>();
        for (int k = 0; k < handles.Count; k++) {
            int kk = k;
            var t = new System.Threading.Thread(() => {
                while (sw.Elapsed.TotalSeconds < seconds) {
                    int read = 0;
                    byte[] b = new byte[capsL[kk].InputReportByteLength];
                    if (!ReadFile(handles[kk], b, b.Length, out read, IntPtr.Zero)) {
                        int err = Marshal.GetLastWin32Error();
                        if (err == 995 || err == 6) break;
                        System.Threading.Thread.Sleep(20); continue;
                    }
                    if (read <= 0) continue;
                    var hex = new StringBuilder();
                    for (int i = 0; i < read; i++) hex.Append(b[i].ToString("X2") + " ");
                    var dec = new StringBuilder();
                    for (int i = 0; i < read; i++) {
                        if (b[i] == 0) continue;
                    }
                    try {
                        for (int pg = 0; pg < 2; pg++) {
                            ushort page = (ushort)(pg == 0 ? 0x0C : 0x07);
                            ushort[] list = new ushort[64];
                            uint len = 64;
                            int st = HidP_GetUsages(HidP_Input, page, 0, list, ref len, preps[kk], b, (uint)read);
                            if (st == HIDP_STATUS_SUCCESS && len > 0) {
                                for (uint i = 0; i < len; i++) {
                                    string nm = NameOf(page, list[i]);
                                    dec.Append(string.Format(" P{0:X2}:0x{1:X2}{2};", page, list[i], nm == null ? "" : "(" + nm + ")"));
                                }
                            }
                        }
                        ushort[] btn = new ushort[64]; uint blen = 64;
                        if (HidP_GetUsages(HidP_Input, 0x09, 0, btn, ref blen, preps[kk], b, (uint)read) == HIDP_STATUS_SUCCESS && blen > 0) {
                            for (uint i = 0; i < blen; i++) dec.Append(string.Format(" Btn{0};", btn[i]));
                        }
                    } catch {}
                    Console.WriteLine(string.Format("[{0,8:F3}s] LEN={1}  DATA= {2} |{3}", sw.Elapsed.TotalSeconds, read, hex.ToString().Trim(), dec.ToString()));
                    Console.Out.Flush();
                }
            });
            t.IsBackground = true;
            th.Add(t); t.Start();
        }
        sw.Start();
        foreach (var t in th) t.Join((seconds + 5) * 1000);
        foreach (var h in handles) CloseHandle(h);
        foreach (var p in preps) HidD_FreePreparsedData(p);
        Console.WriteLine("DONE");
    }
}
'@

Add-Type -TypeDefinition $cs -Language CSharp | Out-Null

if ($Mode -eq 'dump') {
    [HidProbe]::Dump('aabbccddeeff')
} else {
    [HidProbe]::Listen('aabbccddeeff', $Seconds)
}
