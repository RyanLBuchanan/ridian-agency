# Create a "Ridian Agency" shortcut on the user's Desktop.
#
# Targets Start-Ridian-Agency.bat at the repo root. Uses the bundled
# icon at desktop/assets/favicon.ico (sunrise-waves) if present. Tags the shortcut with the
# same Windows AppUserModelID (AUMID) that Electron's main process sets,
# so when the user pins the running app to the taskbar Windows correctly
# associates the pinned icon with the shortcut and relaunches via the
# .bat instead of falling back to "generic Electron".
#
# Safe to re-run -- overwrites any existing shortcut of the same name.
#
# Usage (PowerShell, from anywhere):
#     powershell -NoProfile -ExecutionPolicy Bypass -File `
#         <path-to-repo>\scripts\Create-Ridian-Agency-Shortcut.ps1
#
# Or right-click this file -> "Run with PowerShell".

$ErrorActionPreference = 'Stop'

# Must match desktop/main.js exactly.
$AppUserModelId = 'com.ridiantechnologies.ridianagency'

# Repo root = parent of the scripts/ folder this file lives in.
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Launcher = Join-Path $RepoRoot 'Start-Ridian-Agency.bat'
# SUNRISE-WAVES emblem (the Ridian identity) — never icon.ico, which was the
# retired blue "RA" badge (file deleted; the name is a tooling-default trap).
$IconPath = Join-Path $RepoRoot 'desktop\assets\favicon.ico'

if (-not (Test-Path $Launcher)) {
  Write-Host "X  Could not find $Launcher" -ForegroundColor Red
  Write-Host "   Make sure you're running this from inside the ridian-agency repo." -ForegroundColor DarkGray
  exit 1
}

$DesktopDir = [Environment]::GetFolderPath('Desktop')
$ShortcutPath = Join-Path $DesktopDir 'Ridian Agency.lnk'

Write-Host ''
Write-Host '  Creating Ridian Agency desktop shortcut' -ForegroundColor White
Write-Host "  -> $ShortcutPath" -ForegroundColor DarkGray
Write-Host ''

# We build the .lnk entirely via IShellLinkW so the AUMID can be set on
# the SAME live object before IPersistFile::Save persists everything. The
# WScript.Shell pattern is simpler but doesn't carry property-store
# changes through save -- the AUMID ends up unwritten, and pinning the
# running app produces a separate taskbar entry from the shortcut.

$csharp = @'
using System;
using System.Runtime.InteropServices;

public static class RidianShortcut {
    [StructLayout(LayoutKind.Sequential, Pack = 4)]
    public struct PROPERTYKEY {
        public Guid fmtid;
        public uint pid;
    }

    [StructLayout(LayoutKind.Explicit, Size = 16)]
    public struct PROPVARIANT {
        [FieldOffset(0)] public ushort vt;
        [FieldOffset(8)] public IntPtr pwszVal;
    }

    [ComImport, Guid("000214F9-0000-0000-C000-000000000046"),
     InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IShellLinkW {
        [PreserveSig] int GetPath([Out, MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder pszFile, int cch, IntPtr pfd, uint fFlags);
        [PreserveSig] int GetIDList(out IntPtr ppidl);
        [PreserveSig] int SetIDList(IntPtr pidl);
        [PreserveSig] int GetDescription([Out, MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder pszName, int cch);
        [PreserveSig] int SetDescription([MarshalAs(UnmanagedType.LPWStr)] string pszName);
        [PreserveSig] int GetWorkingDirectory([Out, MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder pszDir, int cch);
        [PreserveSig] int SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string pszDir);
        [PreserveSig] int GetArguments([Out, MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder pszArgs, int cch);
        [PreserveSig] int SetArguments([MarshalAs(UnmanagedType.LPWStr)] string pszArgs);
        [PreserveSig] int GetHotkey(out ushort pwHotkey);
        [PreserveSig] int SetHotkey(ushort wHotkey);
        [PreserveSig] int GetShowCmd(out int piShowCmd);
        [PreserveSig] int SetShowCmd(int iShowCmd);
        [PreserveSig] int GetIconLocation([Out, MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder pszIconPath, int cch, out int piIcon);
        [PreserveSig] int SetIconLocation([MarshalAs(UnmanagedType.LPWStr)] string pszIconPath, int iIcon);
        [PreserveSig] int SetRelativePath([MarshalAs(UnmanagedType.LPWStr)] string pszPathRel, uint dwReserved);
        [PreserveSig] int Resolve(IntPtr hwnd, uint fFlags);
        [PreserveSig] int SetPath([MarshalAs(UnmanagedType.LPWStr)] string pszFile);
    }

    [ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"),
     InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IPropertyStore {
        void GetCount(out uint cProps);
        void GetAt(uint iProp, out PROPERTYKEY pkey);
        void GetValue(ref PROPERTYKEY key, out PROPVARIANT pv);
        void SetValue(ref PROPERTYKEY key, ref PROPVARIANT pv);
        void Commit();
    }

    [ComImport, Guid("0000010B-0000-0000-C000-000000000046"),
     InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IPersistFile {
        [PreserveSig] int GetClassID(out Guid pClassID);
        [PreserveSig] int IsDirty();
        [PreserveSig] int Load([MarshalAs(UnmanagedType.LPWStr)] string pszFileName, int dwMode);
        [PreserveSig] int Save([MarshalAs(UnmanagedType.LPWStr)] string pszFileName, [MarshalAs(UnmanagedType.Bool)] bool fRemember);
        [PreserveSig] int SaveCompleted([MarshalAs(UnmanagedType.LPWStr)] string pszFileName);
        [PreserveSig] int GetCurFile([MarshalAs(UnmanagedType.LPWStr)] out string ppszFileName);
    }

    [ComImport, Guid("00021401-0000-0000-C000-000000000046")]
    public class ShellLink { }

    public static void CreateShortcut(string lnkPath, string targetPath, string workingDir, string description, string iconPath, string aumid) {
        var link = new ShellLink();
        try {
            var sl = (IShellLinkW)link;
            int hr;
            hr = sl.SetPath(targetPath);            if (hr < 0) throw Marshal.GetExceptionForHR(hr);
            hr = sl.SetWorkingDirectory(workingDir);if (hr < 0) throw Marshal.GetExceptionForHR(hr);
            hr = sl.SetDescription(description);    if (hr < 0) throw Marshal.GetExceptionForHR(hr);
            hr = sl.SetShowCmd(1);                  if (hr < 0) throw Marshal.GetExceptionForHR(hr);
            if (!string.IsNullOrEmpty(iconPath)) {
                hr = sl.SetIconLocation(iconPath, 0);
                if (hr < 0) throw Marshal.GetExceptionForHR(hr);
            }

            // PKEY_AppUserModel_ID = {9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3} pid 5
            var ps = (IPropertyStore)link;
            var pk = new PROPERTYKEY {
                fmtid = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"),
                pid = 5
            };
            IntPtr str = Marshal.StringToCoTaskMemUni(aumid);
            try {
                var pv = new PROPVARIANT { vt = 31 /* VT_LPWSTR */, pwszVal = str };
                ps.SetValue(ref pk, ref pv);
                ps.Commit();
            } finally {
                Marshal.FreeCoTaskMem(str);
            }

            var pf = (IPersistFile)link;
            hr = pf.Save(lnkPath, true);
            if (hr < 0) throw Marshal.GetExceptionForHR(hr);
        } finally {
            Marshal.ReleaseComObject(link);
        }
    }

    public static string ReadAumid(string lnkPath) {
        var link = new ShellLink();
        try {
            var pf = (IPersistFile)link;
            int hr = pf.Load(lnkPath, 0);
            if (hr < 0) throw Marshal.GetExceptionForHR(hr);
            var ps = (IPropertyStore)link;
            var pk = new PROPERTYKEY {
                fmtid = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"),
                pid = 5
            };
            PROPVARIANT pv;
            ps.GetValue(ref pk, out pv);
            if (pv.vt == 31 && pv.pwszVal != IntPtr.Zero) return Marshal.PtrToStringUni(pv.pwszVal);
            return "";
        } finally {
            Marshal.ReleaseComObject(link);
        }
    }
}
'@

if (-not ('RidianShortcut' -as [type])) {
  Add-Type -TypeDefinition $csharp -Language CSharp
}

$iconArg = if (Test-Path $IconPath) { $IconPath } else { '' }

try {
  [RidianShortcut]::CreateShortcut(
    $ShortcutPath,
    $Launcher,
    $RepoRoot,
    'Ridian Agency -- local desktop console',
    $iconArg,
    $AppUserModelId
  )
} catch {
  Write-Host "X  Failed to create shortcut: $($_.Exception.Message)" -ForegroundColor Red
  exit 1
}

if (Test-Path $IconPath) {
  Write-Host "OK Using icon at $IconPath" -ForegroundColor Green
} else {
  Write-Host "!  No favicon.ico (sunrise-waves emblem) found at $IconPath" -ForegroundColor Yellow
  Write-Host "   The shortcut will use the .bat file's default icon." -ForegroundColor DarkGray
  Write-Host "   Restore desktop\assets\favicon.ico from the repo (git checkout -- desktop/assets)." -ForegroundColor DarkGray
}

# Verify the AUMID actually persisted to the .lnk.
try {
  $stamped = [RidianShortcut]::ReadAumid($ShortcutPath)
} catch { $stamped = '' }

if ($stamped -eq $AppUserModelId) {
  Write-Host "OK AppUserModelID stamped: $stamped" -ForegroundColor Green
} else {
  Write-Host "!  AppUserModelID readback was '$stamped' (expected '$AppUserModelId')." -ForegroundColor Yellow
  Write-Host "   The shortcut still works, but pinning the running app may create a separate taskbar entry." -ForegroundColor DarkGray
  Write-Host "   Workaround: pin the Desktop shortcut directly by dragging it onto the taskbar." -ForegroundColor DarkGray
}

Write-Host ''
Write-Host 'OK Shortcut created.' -ForegroundColor Green
Write-Host ''
Write-Host '  Next steps -- pin in the right order:' -ForegroundColor White
Write-Host '    1. If a Ridian Agency icon is already pinned to your taskbar,' -ForegroundColor DarkGray
Write-Host '       right-click it -> "Unpin from taskbar" first.' -ForegroundColor DarkGray
Write-Host '    2. RECOMMENDED: drag the Desktop "Ridian Agency" shortcut directly' -ForegroundColor DarkGray
Write-Host '       onto your taskbar (Windows pins the .lnk -- clicking always runs the .bat).' -ForegroundColor DarkGray
Write-Host '    3. ALTERNATIVE: double-click the Desktop shortcut to launch, then' -ForegroundColor DarkGray
Write-Host '       right-click the running Ridian Agency icon in the taskbar -> "Pin to taskbar".' -ForegroundColor DarkGray
Write-Host ''
Write-Host '  Why this matters:' -ForegroundColor White
Write-Host '    Pinning a running app pins the live process, not the .lnk. The matching' -ForegroundColor DarkGray
Write-Host '    AppUserModelID is supposed to unify the two, but Windows .lnk property-store' -ForegroundColor DarkGray
Write-Host '    handling is inconsistent. Pinning the shortcut directly is always reliable.' -ForegroundColor DarkGray
Write-Host ''
