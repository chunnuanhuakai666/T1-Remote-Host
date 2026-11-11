$ErrorActionPreference = 'Continue'

$cs = @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;

public static class RDesc
{
    [StructLayout(LayoutKind.Sequential)]
    struct SP_DEVICE_INTERFACE_DATA { public int cbSize; public Guid InterfaceClassGuid; public int Flags; public IntPtr Reserved; }
    [StructLayout(LayoutKind.Sequential)]
    struct MEMORY_BASIC_INFORMATION {
        public IntPtr BaseAddress; public IntPtr AllocationBase; public uint AllocationProtect;
        public IntPtr RegionSize; public uint State; public uint Protect; public uint Type;
    }

    const int DIGCF_PRESENT = 2, DIGCF_DEVICEINTERFACE = 0x10;
    const uint GENERIC_READ = 0x80000000, GENERIC_WRITE = 0x40000000;
    const uint FILE_SHARE_RW = 3, OPEN_EXISTING = 3;
    const uint MEM_COMMIT = 0x1000;

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
    [DllImport("kernel32.dll")] static extern bool HidD_GetPreparsedData_placeholder();
    [DllImport("kernel32.dll", EntryPoint = "VirtualQuery")] static extern IntPtr VQuery(IntPtr addr, out MEMORY_BASIC_INFORMATION mbi, IntPtr len);
    [DllImport("hid.dll")] static extern bool HidD_GetPreparsedData(IntPtr h, out IntPtr p);
    [DllImport("hid.dll")] static extern bool HidD_FreePreparsedData(IntPtr p);

    public static List<string> FindPaths(string filter)
    {
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

    static bool Readable(IntPtr addr, int n)
    {
        MEMORY_BASIC_INFORMATION mbi;
        if (VQuery(addr, out mbi, (IntPtr)Marshal.SizeOf(typeof(MEMORY_BASIC_INFORMATION))) == IntPtr.Zero) return false;
        if (mbi.State != MEM_COMMIT) return false;
        uint p = mbi.Protect & 0xFF;
        if (p == 0x01 || p == 0x00) return false;
        return true;
    }

    public static void Dump(string filter)
    {
        foreach (var path in FindPaths(filter)) {
            string tag = path.Contains("col05") ? "col05(vendor FF00)" : path.Contains("col02") ? "col02(consumer)" : path.Contains("col03") ? "col03(system)" : "other";
            IntPtr h = CreateFile(path, GENERIC_READ | GENERIC_WRITE, FILE_SHARE_RW, IntPtr.Zero, OPEN_EXISTING, 0, IntPtr.Zero);
            if (h.ToInt64() == -1) { Console.WriteLine(tag + " OPEN FAIL " + Marshal.GetLastWin32Error()); continue; }
            IntPtr prep;
            if (!HidD_GetPreparsedData(h, out prep)) { Console.WriteLine(tag + " NO PREPARSED"); CloseHandle(h); continue; }
            Console.WriteLine("");
            Console.WriteLine("### " + tag);
            byte[] head = new byte[0x50];
            Marshal.Copy(prep, head, 0, head.Length);
            var hex = new StringBuilder();
            for (int i = 0; i < head.Length; i += 16) {
                hex.Append(string.Format("{0:X4}: ", i));
                for (int j = 0; j < 16 && i + j < head.Length; j++) hex.Append(head[i + j].ToString("X2") + " ");
                hex.AppendLine();
            }
            Console.WriteLine("PREPARSED HEAD:");
            Console.Write(hex.ToString());

            var cands = new List<string>();
            for (int off = 8; off <= 0x30; off += 8) {
                IntPtr ptr = (IntPtr)BitConverter.ToInt64(head, off);
                if (ptr == IntPtr.Zero) continue;
                if (!Readable(ptr, 8)) continue;
                byte[] probe = new byte[16];
                try { Marshal.Copy(ptr, probe, 0, 16); } catch { continue; }
                cands.Add(string.Format("  off=0x{0:X2} ptr=0x{1:X} bytes={2}", off, ptr.ToInt64(), BitConverter.ToString(probe).Replace("-", " ")));
            }
            Console.WriteLine("POINTER CANDIDATES:");
            foreach (var c in cands) Console.WriteLine(c);

            // candidate length ULONGs
            var lens = new List<string>();
            for (int off = 8; off <= 0x48; off += 4) {
                uint v = BitConverter.ToUInt32(head, off);
                if (v > 30 && v < 8192) lens.Add(string.Format("  off=0x{0:X2} val={1}", off, v));
            }
            Console.WriteLine("LENGTH CANDIDATES:");
            foreach (var c in lens) Console.WriteLine(c);

            HidD_FreePreparsedData(prep);
            CloseHandle(h);
        }
    }
}
'@

Add-Type -TypeDefinition $cs -Language CSharp | Out-Null
[RDesc]::Dump('aabbccddeeff')
