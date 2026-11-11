param([int]$Seconds = 180, [string]$LogPath = "$PSScriptRoot\t1_rawinput.log")

$ErrorActionPreference = 'Continue'

$cs = @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
using System.IO;

public static class RawCap
{
    delegate IntPtr WndProcDelegate(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam);

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    struct WNDCLASSEX {
        public uint cbSize; public uint style; public IntPtr lpfnWndProc;
        public int cbClsExtra; public int cbWndExtra; public IntPtr hInstance;
        public IntPtr hIcon; public IntPtr hCursor; public IntPtr hbrBackground;
        [MarshalAs(UnmanagedType.LPWStr)] public string lpszMenuName;
        [MarshalAs(UnmanagedType.LPWStr)] public string lpszClassName;
        public IntPtr hIconSm;
    }
    [StructLayout(LayoutKind.Sequential)]
    struct RAWINPUTDEVICE { public ushort usUsagePage; public ushort usUsage; public uint dwFlags; public IntPtr hwndTarget; }
    [StructLayout(LayoutKind.Sequential)]
    struct RAWINPUTHEADER { public uint dwType; public uint dwSize; public IntPtr hDevice; public IntPtr wParam; }
    [StructLayout(LayoutKind.Sequential)]
    struct MSG { public IntPtr hwnd; public uint message; public IntPtr wParam; public IntPtr lParam; public uint time; public int ptX; public int ptY; }

    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern ushort RegisterClassEx(ref WNDCLASSEX wc);
    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern IntPtr CreateWindowExW(uint exStyle, string cls, string name, uint style, int x, int y, int w, int h,
        IntPtr parent, IntPtr menu, IntPtr inst, IntPtr param);
    [DllImport("user32.dll")] static extern IntPtr DefWindowProc(IntPtr h, uint m, IntPtr w, IntPtr l);
    [DllImport("user32.dll", SetLastError = true)] static extern bool RegisterRawInputDevices(RAWINPUTDEVICE[] devs, uint num, uint size);
    [DllImport("user32.dll")] static extern uint GetRawInputData(IntPtr hRawInput, uint cmd, IntPtr pData, ref uint pcbSize, uint cbHeader);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern uint GetRawInputDeviceInfoW(IntPtr hDevice, uint cmd, IntPtr pData, ref uint pcbSize);
    [DllImport("user32.dll")] static extern int GetMessage(out MSG m, IntPtr h, uint min, uint max);
    [DllImport("user32.dll")] static extern bool TranslateMessage(ref MSG m);
    [DllImport("user32.dll")] static extern IntPtr DispatchMessage(ref MSG m);
    [DllImport("user32.dll")] static extern bool PostThreadMessage(uint tid, uint msg, IntPtr w, IntPtr l);
    [DllImport("kernel32.dll")] static extern uint GetCurrentThreadId();
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)] static extern IntPtr GetModuleHandleW(string n);

    const uint WM_INPUT = 0x00FF, WM_QUIT = 0x0012;
    const uint RID_INPUT = 0x10000003, RIDI_DEVICENAME = 0x20000007;
    const uint RIDEV_INPUTSINK = 0x00000100;

    static StreamWriter W;
    static readonly object Lock = new object();
    static IntPtr OurHwnd = IntPtr.Zero;
    static uint PumpTid = 0;
    static DateTime T0;
    static Dictionary<IntPtr, string> NameCache = new Dictionary<IntPtr, string>();
    static WndProcDelegate ProcRef;
    static int OtherCount = 0;

    static void Emit(string s) {
        string line = string.Format("+{0,7:F3}s {1}", (DateTime.Now - T0).TotalSeconds, s);
        lock (Lock) { Console.WriteLine(line); W.WriteLine(line); W.Flush(); }
    }

    static string DevName(IntPtr h) {
        if (NameCache.ContainsKey(h)) return NameCache[h];
        uint size = 0;
        GetRawInputDeviceInfoW(h, RIDI_DEVICENAME, IntPtr.Zero, ref size);
        string name = "?";
        if (size > 0) {
            IntPtr buf = Marshal.AllocHGlobal((int)size * 2 + 2);
            try {
                if (GetRawInputDeviceInfoW(h, RIDI_DEVICENAME, buf, ref size) > 0)
                    name = Marshal.PtrToStringUni(buf);
            } finally { Marshal.FreeHGlobal(buf); }
        }
        NameCache[h] = name;
        return name;
    }

    static readonly Dictionary<int, string> KEY = new Dictionary<int, string>();
    static void InitKeys() {
        object[,] t = new object[,] {
            {0x04,"A"},{0x28,"Enter"},{0x29,"Esc"},{0x2A,"Backspace"},{0x2B,"Tab"},{0x2C,"Space"},{0x39,"CapsLock"},
            {0x4F,"Right"},{0x50,"Left"},{0x51,"Down"},{0x52,"Up"},{0x65,"App"},{0x66,"Power"},
            {0x6F,"BrightnessUp"},{0x70,"BrightnessDown"},{0x74,"Execute"},{0x75,"Help"},{0x76,"Menu"},
            {0x7F,"Mute"},{0x80,"VolumeUp"},{0x81,"VolumeDown"},{0x9F,"Cancel"},
            {0xB5,"NextTrack"},{0xB6,"PrevTrack"},{0xB7,"Stop"},{0xCD,"PlayPause"},
            {0xE2,"Mute"},{0xE8,"MediaPlayer"},{0xE9,"VolumeInc"},{0xEA,"VolumeDec"},
            {0x183,"ProgramGuide"},{0x221,"AC_Search"},{0x223,"AC_GoTo"},{0x224,"AC_Setup"},
            {0x225,"AC_Stop"},{0x226,"AC_Refresh"},{0x227,"AC_Bookmark"},{0x22A,"AC_Back"},{0x22B,"AC_Forward"}
        };
        for (int i = 0; i < t.GetLength(0); i++) KEY[(int)t[i, 0]] = (string)t[i, 1];
    }
    static string KN(int code) { return KEY.ContainsKey(code) ? KEY[code] : null; }

    static string DecodeKeyboard(byte[] d, int off, int len) {
        var sb = new StringBuilder();
        if (len >= 3) {
            int mod = d[off + 1];
            if (mod != 0) sb.Append(" mod=0x" + mod.ToString("X2"));
            for (int i = 2; i < len; i++) {
                int k = d[off + i];
                if (k == 0) continue;
                string n = KN(k);
                sb.Append(string.Format(" 0x{0:X2}{1}", k, n == null ? "" : "(" + n + ")"));
            }
        }
        return sb.ToString();
    }

    static IntPtr Proc(IntPtr hwnd, uint msg, IntPtr wParam, IntPtr lParam) {
        if (msg == WM_INPUT) {
            uint size = 0;
            GetRawInputData(lParam, RID_INPUT, IntPtr.Zero, ref size, (uint)Marshal.SizeOf(typeof(RAWINPUTHEADER)));
            if (size > 0) {
                IntPtr buf = Marshal.AllocHGlobal((int)size);
                try {
                    if (GetRawInputData(lParam, RID_INPUT, buf, ref size, (uint)Marshal.SizeOf(typeof(RAWINPUTHEADER))) == size) {
                        var hdr = (RAWINPUTHEADER)Marshal.PtrToStructure(buf, typeof(RAWINPUTHEADER));
                        if (hdr.dwType == 2) {
                            int hs = Marshal.SizeOf(typeof(RAWINPUTHEADER));
                            uint sizeHid = (uint)Marshal.ReadInt32(buf, hs);
                            uint count = (uint)Marshal.ReadInt32(buf, hs + 4);
                            int total = (int)(sizeHid * count);
                            string nm = DevName(hdr.hDevice);
                            if (nm.ToLower().Contains("aabbccddeeff")) {
                                byte[] data = new byte[total];
                                Marshal.Copy(IntPtr.Add(buf, hs + 8), data, 0, total);
                                var hex = new StringBuilder();
                                for (int i = 0; i < total; i++) hex.Append(data[i].ToString("X2") + " ");
                                int rid = total > 0 ? data[0] : -1;
                                Emit(string.Format("RID=0x{0:X2} len={1,-3} [{2}] {3}", rid, total, hex.ToString().Trim(), DecodeKeyboard(data, 0, total)));
                            } else {
                                OtherCount++;
                                if (OtherCount == 1) {
                                    int rid0 = total > 0 ? Marshal.ReadByte(buf, hs + 8) : 0;
                                    Emit("PUMP ALIVE - keyboard class path delivers. first foreign event len=" + total + " rid=0x" + rid0.ToString("X2") + " name=" + nm);
                                }
                            }
                        }
                    }
                } finally { Marshal.FreeHGlobal(buf); }
            }
        }
        return DefWindowProc(hwnd, msg, wParam, lParam);
    }

    public static void Run(int seconds, string logPath) {
        InitKeys();
        W = new StreamWriter(logPath, false, Encoding.UTF8);
        T0 = DateTime.Now;
        var pump = new System.Threading.Thread(() => {
            PumpTid = GetCurrentThreadId();
            ProcRef = new WndProcDelegate(Proc);
            var wc = new WNDCLASSEX();
            wc.cbSize = (uint)Marshal.SizeOf(typeof(WNDCLASSEX));
            wc.lpfnWndProc = Marshal.GetFunctionPointerForDelegate(ProcRef);
            wc.hInstance = GetModuleHandleW(null);
            wc.lpszClassName = "T1CapRawWnd";
            if (RegisterClassEx(ref wc) == 0) { Emit("REGISTERCLASS FAIL " + Marshal.GetLastWin32Error()); return; }
            OurHwnd = CreateWindowExW(0x00000080 | 0x08000000, "T1CapRawWnd", "t1cap", 0x80000000, -2000, -2000, 1, 1, IntPtr.Zero, IntPtr.Zero, wc.hInstance, IntPtr.Zero);
            if (OurHwnd == IntPtr.Zero) { Emit("CREATEWINDOW FAIL " + Marshal.GetLastWin32Error()); return; }
            var devs = new RAWINPUTDEVICE[2];
            devs[0].usUsagePage = 0x01; devs[0].usUsage = 0x06; devs[0].dwFlags = RIDEV_INPUTSINK; devs[0].hwndTarget = OurHwnd;
            devs[1].usUsagePage = 0x0C; devs[1].usUsage = 0x01; devs[1].dwFlags = RIDEV_INPUTSINK; devs[1].hwndTarget = OurHwnd;
            if (!RegisterRawInputDevices(devs, 2, (uint)Marshal.SizeOf(typeof(RAWINPUTDEVICE))))
                Emit("REGISTERRAWINPUT FAIL " + Marshal.GetLastWin32Error());
            else Emit("RAW INPUT ARMED (kbd 0x01/0x06, consumer 0x0C/0x01, vendor 0xFF00)");
            MSG m;
            while (GetMessage(out m, IntPtr.Zero, 0, 0) > 0) { TranslateMessage(ref m); DispatchMessage(ref m); }
        });
        pump.IsBackground = true;
        pump.Start();
        System.Threading.Thread.Sleep(seconds * 1000);
        Emit("=== RAW CAPTURE DONE ===");
        W.Close();
        Environment.Exit(0);
    }
}
'@

Add-Type -TypeDefinition $cs -Language CSharp | Out-Null
[RawCap]::Run($Seconds, $LogPath)
