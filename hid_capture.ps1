param([int]$Seconds = 60, [string]$LogPath = "$PSScriptRoot\t1_capture.log")

$ErrorActionPreference = 'Continue'

$cs = @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
using System.IO;

public static class HidCap
{
    [StructLayout(LayoutKind.Sequential)]
    struct SP_DEVICE_INTERFACE_DATA { public int cbSize; public Guid InterfaceClassGuid; public int Flags; public IntPtr Reserved; }
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
    const int HidP_Input = 0;
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
    [DllImport("kernel32.dll", SetLastError = true)] static extern bool ReadFile(IntPtr h, byte[] buf, int n, out int read, IntPtr ov);
    [DllImport("hid.dll")] static extern bool HidD_GetPreparsedData(IntPtr h, out IntPtr p);
    [DllImport("hid.dll")] static extern int HidP_GetCaps(IntPtr prep, ref HIDP_CAPS caps);
    [DllImport("hid.dll")] static extern int HidP_GetUsages(int type, ushort page, ushort linkColl, ushort[] list, ref uint len, IntPtr prep, byte[] report, uint reportLen);

    static readonly Dictionary<ushort, string> CONS = new Dictionary<ushort, string>();
    static readonly Dictionary<ushort, string> KEYS = new Dictionary<ushort, string>();

    static void AddCons() {
        object[,] t = new object[,] {
            {(ushort)0x30,"Power"},{(ushort)0x31,"Reset"},{(ushort)0x32,"Sleep"},{(ushort)0x34,"Menu"},{(ushort)0x35,"MenuPick"},
            {(ushort)0x36,"MenuUp"},{(ushort)0x37,"MenuDown"},{(ushort)0x38,"MenuLeft"},{(ushort)0x39,"MenuRight"},{(ushort)0x3A,"MenuEscape"},
            {(ushort)0x40,"MenuValueIncrease"},{(ushort)0x41,"MenuValueDecrease"},{(ushort)0x60,"DataOnScreen"},{(ushort)0x61,"ClosedCaption"},
            {(ushort)0x6F,"BrightnessIncrement"},{(ushort)0x70,"BrightnessDecrement"},{(ushort)0x8A,"MediaSelectTV"},
            {(ushort)0xB0,"Play"},{(ushort)0xB1,"Pause"},{(ushort)0xB2,"Record"},{(ushort)0xB3,"FastForward"},{(ushort)0xB4,"Rewind"},
            {(ushort)0xB5,"ScanNextTrack"},{(ushort)0xB6,"ScanPreviousTrack"},{(ushort)0xB7,"Stop"},{(ushort)0xB8,"Eject"},
            {(ushort)0xB9,"RandomPlay"},{(ushort)0xBA,"Repeat"},{(ushort)0xBC,"Stop/Eject"},{(ushort)0xCD,"PlayPause"},
            {(ushort)0xE2,"Mute"},{(ushort)0xE8,"Volume"},{(ushort)0xE9,"VolumeInc"},{(ushort)0xEA,"VolumeDec"},{(ushort)0xE0,"Volume+?"},
            {(ushort)0xC3,"CameraZoomIn"},{(ushort)0xC4,"CameraZoomOut"},{(ushort)0x183,"MediaSelectProgramGuide"},
            {(ushort)0x192,"AL_Calculator"},{(ushort)0x194,"AL_LocalMachineBrowser"},{(ushort)0x196,"AL_InternetBrowser"},
            {(ushort)0x198,"AL_ConsumerControlConfig"},{(ushort)0x1A0,"AL_WordProcessor"},{(ushort)0x1A2,"AL_Spreadsheet"},
            {(ushort)0x1A4,"AL_PresentationApp"},{(ushort)0x1A6,"AL_GraphicsEditor"},{(ushort)0x1A9,"AL_EmailReader"},
            {(ushort)0x1B1,"AL_FileBrowser"},{(ushort)0x1B4,"AL_ProgramLauncher"},{(ushort)0x1B6,"AL_MediaPlayer"},
            {(ushort)0x1BC,"AL_IntegratedHelp"},{(ushort)0x1CB,"AL_Documents"},{(ushort)0x1CC,"AL_News"},
            {(ushort)0x1CD,"AL_Pictures"},{(ushort)0x1CE,"AL_Music"},{(ushort)0x1CF,"AL_Video"},
            {(ushort)0x1F1,"AC_Properties"},{(ushort)0x1F2,"AC_Back"},{(ushort)0x1F3,"AC_Home"},{(ushort)0x1F4,"AC_Forward"},
            {(ushort)0x1F5,"AC_Stop"},{(ushort)0x1F6,"AC_Refresh"},{(ushort)0x1F7,"AC_Bookmark"},{(ushort)0x201,"AC_New"},
            {(ushort)0x202,"AC_Close"},{(ushort)0x203,"AC_Print"},{(ushort)0x207,"AC_Save"},{(ushort)0x221,"AC_Search"},
            {(ushort)0x223,"AC_GoTo"},{(ushort)0x224,"AC_Setup"},{(ushort)0x225,"AC_Stop2"},{(ushort)0x226,"AC_Refresh2"},
            {(ushort)0x227,"AC_Bookmark2"},{(ushort)0x22A,"AC_Back2"},{(ushort)0x22B,"AC_Forward2"}
        };
        for (int i = 0; i < t.GetLength(0); i++) CONS[(ushort)t[i, 0]] = (string)t[i, 1];
    }
    static void AddKeys() {
        object[,] t = new object[,] {
            {(ushort)0x04,"A"},{(ushort)0x05,"B"},{(ushort)0x06,"C"},{(ushort)0x07,"D"},{(ushort)0x28,"Enter"},{(ushort)0x29,"Esc"},
            {(ushort)0x2A,"Backspace"},{(ushort)0x2B,"Tab"},{(ushort)0x2C,"Space"},{(ushort)0x39,"CapsLock"},
            {(ushort)0x4F,"Right"},{(ushort)0x50,"Left"},{(ushort)0x51,"Down"},{(ushort)0x52,"Up"},{(ushort)0x65,"App"},
            {(ushort)0x66,"Power"},{(ushort)0x68,"F13"},{(ushort)0x6F,"BrightnessUp"},{(ushort)0x70,"BrightnessDown"},
            {(ushort)0x74,"Execute"},{(ushort)0x75,"Help"},{(ushort)0x76,"Menu"},{(ushort)0x7F,"Mute"},
            {(ushort)0x80,"VolumeUp"},{(ushort)0x81,"VolumeDown"},{(ushort)0x9F,"Cancel"},{(ushort)0xB5,"NextTrack"},
            {(ushort)0xB6,"PrevTrack"},{(ushort)0xB7,"Stop"},{(ushort)0xCD,"PlayPause"},{(ushort)0xE2,"Mute"},
            {(ushort)0xE9,"VolUp"},{(ushort)0xEA,"VolDown"},{(ushort)0xE8,"MediaPlayer"},{(ushort)0xF0,"??"}
        };
        for (int i = 0; i < t.GetLength(0); i++) KEYS[(ushort)t[i, 0]] = (string)t[i, 1];
    }

    static string Lookup(ushort page, ushort u) {
        if (page == 0x0C && CONS.ContainsKey(u)) return CONS[u];
        if (page == 0x07 && KEYS.ContainsKey(u)) return KEYS[u];
        return null;
    }

    public static List<string> FindPaths(string filter) {
        var res = new List<string>();
        Guid guid = new Guid("4d1e55b2-f16f-11cf-88cb-001111000030");
        IntPtr set = SetupDiGetClassDevs(ref guid, IntPtr.Zero, IntPtr.Zero, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE);
        var did = new SP_DEVICE_INTERFACE_DATA(); did.cbSize = Marshal.SizeOf(did);
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

    static StreamWriter W;
    static object Lock = new object();
    static void Emit(string s) { lock (Lock) { Console.WriteLine(s); W.WriteLine(s); W.Flush(); } }

    public static void Run(string filter, int seconds, string logPath) {
        AddCons(); AddKeys();
        W = new StreamWriter(logPath, false, Encoding.UTF8);
        var handles = new List<IntPtr>(); var preps = new List<IntPtr>(); var capsL = new List<HIDP_CAPS>(); var tags = new List<string>();
        foreach (var path in FindPaths(filter)) {
            string tag = path.Contains("col05") ? "VENDOR-FF00" : path.Contains("col02") ? "CONSUMER" : path.Contains("col03") ? "SYSTEM" : path.Contains("col01") ? "KEYBOARD" : "OTHER";
            IntPtr h = CreateFile(path, GENERIC_READ | GENERIC_WRITE, FILE_SHARE_RW, IntPtr.Zero, OPEN_EXISTING, 0, IntPtr.Zero);
            if (h.ToInt64() == -1) { Emit("SKIP " + tag + " open_err=" + Marshal.GetLastWin32Error()); continue; }
            IntPtr prep;
            if (!HidD_GetPreparsedData(h, out prep)) { Emit("SKIP " + tag + " no_preparsed"); continue; }
            var caps = new HIDP_CAPS();
            HidP_GetCaps(prep, ref caps);
            if (caps.InputReportByteLength == 0) { Emit("SKIP " + tag + " no_input"); continue; }
            handles.Add(h); preps.Add(prep); capsL.Add(caps); tags.Add(tag);
            Emit(string.Format("OPEN {0}  inLen={1}  usagePage=0x{2:X2}", tag, caps.InputReportByteLength, caps.UsagePage));
        }
        Emit("READY  duration=" + seconds + "s  -- press buttons now");
        var start = DateTime.Now;
        var ths = new List<System.Threading.Thread>();
        for (int k = 0; k < handles.Count; k++) {
            int kk = k;
            var t = new System.Threading.Thread(() => {
                byte[] b = new byte[capsL[kk].InputReportByteLength];
                while (true) {
                    int read = 0;
                    if (!ReadFile(handles[kk], b, b.Length, out read, IntPtr.Zero)) {
                        int err = Marshal.GetLastWin32Error();
                        Emit(tags[kk] + " READFAIL err=" + err);
                        System.Threading.Thread.Sleep(200); continue;
                    }
                    if (read <= 0) continue;
                    var hex = new StringBuilder();
                    for (int i = 0; i < read; i++) hex.Append(b[i].ToString("X2") + " ");
                    var dec = new StringBuilder();
                    try {
                        ushort[] cand = { 0x0C, 0x07, 0x09, 0x01 };
                        foreach (ushort page in cand) {
                            ushort[] list = new ushort[128]; uint len = 128;
                            if (HidP_GetUsages(HidP_Input, page, 0, list, ref len, preps[kk], b, (uint)read) == HIDP_STATUS_SUCCESS && len > 0) {
                                for (uint i = 0; i < len; i++) {
                                    string nm = Lookup(page, list[i]);
                                    dec.Append(string.Format(" 0x{0:X2}:0x{1:X2}{2}", page, list[i], nm == null ? "" : "(" + nm + ")"));
                                }
                            }
                        }
                    } catch {}
                    Emit(string.Format("+{0,7:F3}s {1,-9} id=0x{2:X2} len={3,-3} [{4}]{5}",
                        (DateTime.Now - start).TotalSeconds, tags[kk], b[0], read, hex.ToString().Trim(), dec.ToString()));
                }
            });
            t.IsBackground = true; ths.Add(t); t.Start();
        }
        System.Threading.Thread.Sleep(seconds * 1000);
        Emit("=== CAPTURE DONE ===");
        W.Close();
        Environment.Exit(0);
    }
}
'@

Add-Type -TypeDefinition $cs -Language CSharp | Out-Null
[HidCap]::Run('aabbccddeeff', $Seconds, $LogPath)
