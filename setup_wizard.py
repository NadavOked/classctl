"""
classctl.setup_wizard  —  אשף התקנה חד-פעמי.
מגדיר סיסמת כניסה, בוחר מיקום לתיקיית הסקריפטים, מגן עליה בהרשאות
מערכת (ACL) כך שרק אדמין ניגש אליה, שואל אילו סקריפטים בסיסיים להוסיף,
ומייצר שני קבצי קונפיג:

  controller.json  — לשלט (כולל hash של הסיסמה + מפתח הרשת)
  agent.json       — לסוכן, זה שמכניסים לגלופה (image)

הרצה:  python setup_wizard.py
"""

import io
import os
import platform
import shutil
import socket
import subprocess
import tempfile
import sys
import threading
import time

import common
import i18n
from i18n import t as _

IS_WINDOWS = platform.system() == "Windows"
DEFAULT_BASE = r"C:\ProgramData\ClassCtl" if IS_WINDOWS else "/opt/classctl"


def detect_platform() -> dict:
    """
    Work out what this machine is, once, at install time.
    The whole classroom is cloned from one image, so what we find here holds
    for every station: no need to re-check at run time.
    """
    info = {"os": "windows" if IS_WINDOWS else platform.system().lower(),
            "name": "", "version": platform.release(),
            "is_server": False, "hypervisors": [], "service": ""}

    if IS_WINDOWS:
        info["service"] = "schtasks"
        try:
            import winreg
            k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                               r"SOFTWARE\Microsoft\Windows NT\CurrentVersion")
            info["name"] = winreg.QueryValueEx(k, "ProductName")[0]
            try:
                info["version"] = winreg.QueryValueEx(k, "DisplayVersion")[0]
            except OSError:
                pass
            try:
                info["is_server"] = (winreg.QueryValueEx(k, "InstallationType")[0]
                                     .lower() == "server")
            except OSError:
                info["is_server"] = "server" in info["name"].lower()
            winreg.CloseKey(k)
        except Exception:
            info["name"] = platform.platform()

        sysroot = os.environ.get("SystemRoot", r"C:\Windows")
        if os.path.exists(os.path.join(sysroot, "System32", "vmms.exe")):
            info["hypervisors"].append("hyperv")
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                     os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
            if base and os.path.exists(os.path.join(
                    base, "VMware", "VMware Workstation", "vmrun.exe")):
                info["hypervisors"].append("vmware")
            if base and os.path.exists(os.path.join(
                    base, "Oracle", "VirtualBox", "VBoxManage.exe")):
                info["hypervisors"].append("virtualbox")
    else:
        info["service"] = "systemd" if os.path.isdir("/run/systemd/system") else "none"
        try:
            with open("/etc/os-release", encoding="utf-8") as f:
                data = dict(l.strip().split("=", 1) for l in f if "=" in l)
            info["name"] = data.get("PRETTY_NAME", "").strip('"') or platform.system()
        except Exception:
            info["name"] = platform.system()
        if os.path.exists("/usr/bin/VBoxManage"):
            info["hypervisors"].append("virtualbox")
        if os.path.exists("/usr/bin/virsh"):
            info["hypervisors"].append("kvm")

    info["hypervisors"] = sorted(set(info["hypervisors"]))
    return info


def platform_summary(p: dict) -> str:
    bits = [p["name"] or p["os"]]
    if p.get("is_server"):
        bits.append("server edition")
    bits.append("hypervisor: " + (", ".join(p["hypervisors"]) if p["hypervisors"]
                                  else "none detected"))
    return " \u00b7 ".join(bits)


# ---------- starter action scripts ----------
# The OS is fixed by the image, so it is detected once at install time.
# Hypervisors are NOT: a student can install one after imaging, so every
# script checks for them when it runs.
#
# Shutdown, restart and close-windows all follow the same chain:
#   hypervisor installed?  ->  any VM running?  ->  shut those down properly
#   ->  then do the action.

# --- Windows: machine-level hypervisor (Hyper-V). The agent is SYSTEM, so it
#     can do this itself.
_WIN_HYPERV = r"""
$RunningAsSystem = ([Security.Principal.WindowsIdentity]::GetCurrent().Name -eq 'NT AUTHORITY\SYSTEM')

if (Get-Command Get-VM -ErrorAction SilentlyContinue) {
    try {
        $running = @(Get-VM | Where-Object { $_.State -eq 'Running' })
        if ($running.Count -gt 0) {
            foreach ($vm in $running) { Stop-VM -Name $vm.Name -ErrorAction SilentlyContinue }
            $deadline = (Get-Date).AddSeconds(90)
            while (@(Get-VM | Where-Object { $_.State -eq 'Running' }).Count -gt 0 -and (Get-Date) -lt $deadline) {
                Start-Sleep -Seconds 2
            }
        }
    } catch {}
}
"""

# --- Windows: hypervisors that belong to the logged-on user. Must run inside
#     that user's session, so the parent hands this text to a temporary task.
_WIN_USER_VMS = r"""
function Find-Tool([string]$exe, [string[]]$dirs) {
    foreach ($d in $dirs) {
        if ($d) { $p = Join-Path $d $exe; if (Test-Path $p) { return $p } }
    }
    $w = Get-Command $exe -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($w) { return $w.Source }
    return $null
}

$pf   = $env:ProgramFiles
$pf86 = ${env:ProgramFiles(x86)}

# VMware Workstation / Player
$vmrun = Find-Tool "vmrun.exe" @(
    (Join-Path $pf   "VMware\VMware Workstation"),
    (Join-Path $pf86 "VMware\VMware Workstation"),
    (Join-Path $pf   "VMware\VMware Player"),
    (Join-Path $pf86 "VMware\VMware Player"))
if ($vmrun) {
    try {
        $vms = @(& $vmrun list | Select-Object -Skip 1 | Where-Object { $_ -and (Test-Path $_) })
        foreach ($vm in $vms) { & $vmrun stop "$vm" soft }
        if ($vms.Count -gt 0) {
            $deadline = (Get-Date).AddSeconds(90)
            while ((@(& $vmrun list | Select-Object -Skip 1).Count -gt 0) -and (Get-Date) -lt $deadline) {
                Start-Sleep -Seconds 2
            }
        }
    } catch {}
}

# VirtualBox - VBOX_MSI_INSTALL_PATH covers installs in unusual folders
$vbox = Find-Tool "VBoxManage.exe" @(
    $env:VBOX_MSI_INSTALL_PATH,
    (Join-Path $pf   "Oracle\VirtualBox"),
    (Join-Path $pf86 "Oracle\VirtualBox"))
if ($vbox) {
    try {
        $ids = @(& $vbox list runningvms | ForEach-Object { if ($_ -match '\{(.+?)\}') { $matches[1] } })
        foreach ($id in $ids) { & $vbox controlvm $id acpipowerbutton }
        if ($ids.Count -gt 0) {
            $deadline = (Get-Date).AddSeconds(90)
            while ((@(& $vbox list runningvms).Count -gt 0) -and (Get-Date) -lt $deadline) {
                Start-Sleep -Seconds 2
            }
        }
    } catch {}
}

# WSL
if (Get-Command wsl.exe -ErrorAction SilentlyContinue) { try { wsl.exe --shutdown } catch {} }
"""

_WIN_CLOSE_WINDOWS = r"""
# Close whatever the last user left open. VM processes stay untouched: if some
# other hypervisor is in use, its machine keeps running rather than being killed.
# Never close: the shell, our own host process, or anything running a VM.
# 'powershell' matters - without it this script kills itself half way through.
$keep = @('explorer','python','pythonw','classctl-agent','ApplicationFrameHost',
          'SystemSettings','TextInputHost','ShellExperienceHost','SearchHost',
          'StartMenuExperienceHost','dwm','sihost','ctfmon','LogonUI','fontdrvhost',
          'powershell','powershell_ise','pwsh','cmd','conhost','WindowsTerminal',
          'OpenConsole','taskhostw','RuntimeBroker',
          'vmware','vmware-vmx','vmplayer','VirtualBox','VirtualBoxVM',
          'VBoxHeadless','VBoxSVC','vmwp','vmmem','vmconnect','qemu-system-x86_64')

$mine = @($PID)
try { $mine += (Get-CimInstance Win32_Process -Filter "ProcessId=$PID").ParentProcessId } catch {}

Get-Process | Where-Object {
    $_.MainWindowTitle -and
    ($keep -notcontains $_.ProcessName) -and
    ($mine -notcontains $_.Id)
} | ForEach-Object { try { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue } catch {} }
"""

# Runs __BODY__ inside the logged-on user's session and waits for it to finish,
# so the machine is not shut down while a VM is still closing.
_WIN_RUN_IN_USER_SESSION = r"""
$UserBody = @'
__BODY__
'@

if ($RunningAsSystem) {
    $u = (Get-CimInstance Win32_ComputerSystem).UserName
    if (-not $u) {
        $p = Get-CimInstance Win32_Process -Filter "Name='explorer.exe'" | Select-Object -First 1
        if ($p) {
            $o = Invoke-CimMethod -InputObject $p -MethodName GetOwner
            if ($o.ReturnValue -eq 0) { $u = "$($o.Domain)\$($o.User)" }
        }
    }
    if ($u) {
        $tmp = "$env:windir\Temp\__TMP__"
        Set-Content -LiteralPath $tmp -Value $UserBody -Encoding UTF8
        schtasks /create /tn __TASK__ /tr "powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File $tmp" /sc once /st 23:59 /ru "$u" /it /f | Out-Null
        schtasks /run /tn __TASK__ | Out-Null
        $deadline = (Get-Date).AddSeconds(120)
        Start-Sleep -Seconds 2
        while ((Get-Date) -lt $deadline) {
            # Get-ScheduledTask returns an enum, so this still works on a
            # localised Windows. Parsing schtasks output for 'Running' did not:
            # on Hebrew Windows it reads פועל, the match always failed, and the
            # wait broke on its first pass - meaning shutdown fired while the
            # VMs it was supposed to wait for were still shutting down.
            $st = (Get-ScheduledTask -TaskName '__TASK__' -ErrorAction SilentlyContinue).State
            if ($st -ne 'Running') { break }
            Start-Sleep -Seconds 2
        }
        schtasks /delete /tn __TASK__ /f | Out-Null
        Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
    }
} else {
    # already running as the user - just do it here
    Invoke-Expression $UserBody
}
"""


_WIN_WAKE = r"""
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public class Mon {
  [DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr h,int m,IntPtr w,IntPtr l);
  [DllImport("user32.dll")] public static extern void mouse_event(int f,int dx,int dy,int d,int e);
}
"@
[Mon]::SendMessage([IntPtr]0xffff,0x0112,[IntPtr]0xF170,[IntPtr](-1)) | Out-Null
[Mon]::mouse_event(0x0001,0,1,0,0)
[Mon]::mouse_event(0x0001,0,-1,0,0)
"""

_WIN_RESET_NET = r"""
# Refresh the lease first: that fixes most classroom IP problems without
# dropping the link. Only bounce the adapter if there is still no gateway.
Start-Sleep -Milliseconds 500
ipconfig /release  | Out-Null
ipconfig /renew    | Out-Null
ipconfig /flushdns | Out-Null
Start-Sleep -Seconds 2
$gw = (Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway }).Count
if ($gw -eq 0) {
    Get-NetAdapter -Physical | Where-Object { $_.Status -eq 'Up' } |
        Restart-NetAdapter -Confirm:$false
}
"""


# Opening an Office app on a *blank* page is not the same as starting it.
# Start-Process winword.exe lands on the template gallery and the recent-files
# list, so the class sits looking at a menu instead of an empty page. The
# command-line switches only cover part of it (winword /w, powerpnt /B) and
# Excel has no equivalent at all, so COM is the one road that works for all
# three. If Office is missing or COM is blocked, fall back to just starting it.
_WIN_OFFICE_APP = r"""
try {
    $app = New-Object -ComObject __PROGID__
    $app.Visible = $true
    $app.__COLLECTION__.Add() | Out-Null
} catch {
    try { Start-Process __EXE__ } catch {}
}
"""


def _win_office_script(prog_id: str, collection: str, exe: str,
                       task: str, tmp: str) -> str:
    body = (_WIN_OFFICE_APP
            .replace("__PROGID__", prog_id)
            .replace("__COLLECTION__", collection)
            .replace("__EXE__", exe))
    return _win_user_only(body, task, tmp)


def _win_user_only(body: str, task: str, tmp: str) -> str:
    """No VM logic - just run this in the logged-on user's session."""
    head = "$RunningAsSystem = ([Security.Principal.WindowsIdentity]::GetCurrent().Name -eq 'NT AUTHORITY\\SYSTEM')\n"
    session = (_WIN_RUN_IN_USER_SESSION
               .replace("__BODY__", body.strip("\n"))
               .replace("__TASK__", task)
               .replace("__TMP__", tmp))
    return (head + session).lstrip("\n")


def _win_script(user_body: str, action: str, task: str, tmp: str) -> str:
    session = (_WIN_RUN_IN_USER_SESSION
               .replace("__BODY__", user_body.strip("\n"))
               .replace("__TASK__", task)
               .replace("__TMP__", tmp))
    return (_WIN_HYPERV + session + action).lstrip("\n")


_LINUX_STOP_VMS = r"""
# --- hypervisor installed? any VM running? shut them down properly ---
CONSOLE_USER=$(loginctl list-sessions --no-legend 2>/dev/null | awk '$3 != "" {print $3; exit}')
[ -z "$CONSOLE_USER" ] && CONSOLE_USER=$(who 2>/dev/null | awk 'NR==1{print $1}')

as_user() {
    if [ -n "$CONSOLE_USER" ] && [ "$(id -u)" = "0" ]; then
        su - "$CONSOLE_USER" -c "$1"
    else
        sh -c "$1"
    fi
}

# VirtualBox belongs to the desktop user
if command -v VBoxManage >/dev/null 2>&1; then
    RUNNING=$(as_user "VBoxManage list runningvms" 2>/dev/null | wc -l)
    if [ "$RUNNING" -gt 0 ]; then
        as_user "VBoxManage list runningvms" 2>/dev/null | sed 's/.*{\(.*\)}/\1/' | while read -r id; do
            [ -n "$id" ] && as_user "VBoxManage controlvm $id acpipowerbutton" 2>/dev/null
        done
        for _ in $(seq 1 45); do
            [ "$(as_user 'VBoxManage list runningvms' 2>/dev/null | wc -l)" -eq 0 ] && break
            sleep 2
        done
    fi
fi

# KVM / libvirt is system level
if command -v virsh >/dev/null 2>&1; then
    VMS=$(virsh list --name --state-running 2>/dev/null)
    if [ -n "$VMS" ]; then
        echo "$VMS" | while read -r vm; do [ -n "$vm" ] && virsh shutdown "$vm" >/dev/null 2>&1; done
        for _ in $(seq 1 45); do
            [ -z "$(virsh list --name --state-running 2>/dev/null)" ] && break
            sleep 2
        done
    fi
fi

# VMware Workstation on Linux
if command -v vmrun >/dev/null 2>&1; then
    as_user "vmrun list | tail -n +2 | while read -r vm; do vmrun stop \"\$vm\" soft; done" 2>/dev/null
    sleep 3
fi
"""


def basic_script_defs(plat: dict | None = None) -> dict[str, tuple[str, str]]:
    """key -> (filename, content). The OS decides the flavour, not the hypervisor."""
    plat = plat or detect_platform()

    if plat["os"] == "windows":
        return {
            "shutdown": ("shutdown.ps1",
                         _win_script(_WIN_USER_VMS,
                                     "\nshutdown /s /f /t 0\n",
                                     "ClassCtlVmsOff", "cc_vms_off.ps1")),
            "restart": ("restart.ps1",
                        _win_script(_WIN_USER_VMS,
                                    "\nshutdown /r /f /t 0\n",
                                    "ClassCtlVmsRst", "cc_vms_rst.ps1")),
            "reset_network": ("reset-network.ps1", _WIN_RESET_NET.lstrip("\n")),
            "close_windows": ("close-windows.ps1",
                              _win_script(_WIN_USER_VMS + _WIN_CLOSE_WINDOWS,
                                          "", "ClassCtlClose", "cc_close.ps1")),
            "wake_screens": ("wake-screens.ps1",
                             _win_user_only(_WIN_WAKE, "ClassCtlWake", "cc_wake.ps1")),
            "open_word": ("open-word.ps1",
                          _win_office_script("Word.Application", "Documents",
                                             "winword.exe", "ClassCtlWord",
                                             "cc_word.ps1")),
            "open_excel": ("open-excel.ps1",
                           _win_office_script("Excel.Application", "Workbooks",
                                              "excel.exe", "ClassCtlExcel",
                                              "cc_excel.ps1")),
            "open_powerpoint": ("open-powerpoint.ps1",
                                _win_office_script("PowerPoint.Application",
                                                   "Presentations", "powerpnt.exe",
                                                   "ClassCtlPpt", "cc_ppt.ps1")),
        }

    return {
        "shutdown": ("shutdown.sh",
                     "#!/bin/bash\n" + _LINUX_STOP_VMS + "\nshutdown -h now\n"),
        "restart": ("restart.sh",
                    "#!/bin/bash\n" + _LINUX_STOP_VMS + "\nshutdown -r now\n"),
        "reset_network": ("reset-network.sh",
                          "#!/bin/bash\nsleep 1\n"
                          "if command -v nmcli >/dev/null 2>&1; then\n"
                          "  nmcli networking off && sleep 2 && nmcli networking on\n"
                          "else\n"
                          "  IF=$(ip route | awk '/default/{print $5; exit}')\n"
                          "  ip link set \"$IF\" down && sleep 2 && ip link set \"$IF\" up\n"
                          "fi\n"),
        "close_windows": ("close-windows.sh",
                          "#!/bin/bash\n" + _LINUX_STOP_VMS + r"""
# close remaining windows for the desktop user
if command -v wmctrl >/dev/null 2>&1; then
    as_user "DISPLAY=${DISPLAY:-:0} wmctrl -l | awk '{print \$1}' | while read -r w; do DISPLAY=${DISPLAY:-:0} wmctrl -i -c \$w; done" 2>/dev/null
fi
"""),
        "wake_screens": ("wake-screens.sh",
                         "#!/bin/bash\nexport DISPLAY=${DISPLAY:-:0}\n"
                         "if command -v xset >/dev/null 2>&1; then\n"
                         "  xset dpms force on\n  xset s reset\nfi\n"),
        # LibreOffice opens straight onto an empty document with these, so the
        # Linux side needs no COM equivalent.
        "open_word": ("open-writer.sh",
                      common.open_app_script("soffice --writer", is_windows=False)),
        "open_excel": ("open-calc.sh",
                       common.open_app_script("soffice --calc", is_windows=False)),
        "open_powerpoint": ("open-impress.sh",
                            common.open_app_script("soffice --impress",
                                                   is_windows=False)),
    }


def protect_folder_windows(path: str) -> tuple[bool, str]:
    """מסיר ירושת הרשאות, נותן גישה רק ל-Administrators ו-SYSTEM, ומסתיר את התיקייה."""
    try:
        subprocess.run(["icacls", path, "/inheritance:r",
                        "/grant:r", "Administrators:(OI)(CI)F",
                        "/grant:r", "SYSTEM:(OI)(CI)F"],
                       check=True, capture_output=True, text=True)
        try:
            subprocess.run(["attrib", "+h", path], check=False, capture_output=True)
        except Exception:
            pass
        return True, "הוגדרו הרשאות: רק Administrators ו-SYSTEM, והתיקייה הוסתרה"
    except subprocess.CalledProcessError as e:
        return False, (e.stderr or str(e)).strip()
    except FileNotFoundError:
        return False, "icacls לא נמצא"


def protect_folder_linux(path: str) -> tuple[bool, str]:
    try:
        os.chmod(path, 0o700)
        return True, "chmod 700 (רק root)"
    except OSError as e:
        return False, str(e)


def do_setup(password: str, base_dir: str, protect: bool,
             basic_scripts: list[str], plat: dict | None = None) -> dict:
    plat = plat or detect_platform()
    scripts_dir = os.path.join(base_dir, "scripts")
    os.makedirs(scripts_dir, exist_ok=True)

    # כתיבת רק הסקריפטים הבסיסיים שנבחרו (אם נבחרו). אין ברירת מחדל כפויה.
    defs = basic_script_defs(plat)
    created = []
    for key in basic_scripts:
        if key not in defs:
            continue
        fname, content = defs[key]
        path = os.path.join(scripts_dir, fname)
        if not os.path.exists(path):
            # .ps1 needs a BOM or Windows PowerShell mis-reads it; Windows
            # scripts need CRLF. Linux scripts stay LF.
            is_win_script = fname.lower().endswith((".ps1", ".bat", ".cmd"))
            enc = "utf-8-sig" if fname.lower().endswith(".ps1") else "utf-8"
            nl = "\r\n" if is_win_script else "\n"
            with open(path, "w", encoding=enc, newline=nl) as f:
                f.write(content)
            if not IS_WINDOWS:
                os.chmod(path, 0o755)
        created.append(fname)

    net_key = common.gen_net_key()
    pw = common.hash_password(password)

    controller_cfg = {
        "role": "controller", "version": common.VERSION, "platform": plat,
        "password": pw, "net_key": net_key,
        "scripts_dir": scripts_dir,
        "tcp_port": common.DEFAULT_TCP_PORT, "udp_port": common.DEFAULT_UDP_PORT,
    }
    agent_cfg = {
        "role": "agent", "version": common.VERSION, "platform": plat,
        "net_key": net_key, "scripts_dir": scripts_dir,
        "tcp_port": common.DEFAULT_TCP_PORT, "udp_port": common.DEFAULT_UDP_PORT,
    }
    controller_path = os.path.join(base_dir, "controller.json")
    agent_path = os.path.join(base_dir, "agent.json")
    common.save_config(controller_path, controller_cfg)
    common.save_config(agent_path, agent_cfg)

    protect_msg = "דילגתי על הגנת התיקייה"
    if protect:
        ok, protect_msg = (protect_folder_windows(base_dir) if IS_WINDOWS
                           else protect_folder_linux(base_dir))
        protect_msg = ("\u2713 " if ok else "\u2717 ") + protect_msg

    return {
        "base_dir": base_dir, "scripts_dir": scripts_dir,
        "controller_path": controller_path, "agent_path": agent_path,
        "protect_msg": protect_msg, "created": created,
    }


# ---------------- GUI ----------------
def is_admin() -> bool:
    if not IS_WINDOWS:
        return os.geteuid() == 0
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _hidden_ps(script_path: str, args: list[str]) -> tuple[bool, str]:
    """Run a PowerShell script with no console window."""
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
           "-WindowStyle", "Hidden", "-File", script_path] + args
    flags = 0x08000000 if IS_WINDOWS else 0        # CREATE_NO_WINDOW
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           creationflags=flags, timeout=180)
        return r.returncode == 0, (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return False, str(e)


def perform_install(password, base_dir, scripts, progress, plat=None):
    """
    The whole installation, driven from the wizard so no console ever appears.
    progress(fraction, message)
    """
    here = os.path.dirname(os.path.abspath(__file__))

    plat = plat or detect_platform()
    progress(0.08, _("Creating the protected folder\u2026"))
    info = do_setup(password, base_dir, True, scripts, plat)

    progress(0.30, _("Copying program files\u2026"))
    if os.path.abspath(here) != os.path.abspath(base_dir):
        for f in os.listdir(here):
            if f.endswith((".py", ".ps1", ".ico")):
                try:
                    shutil.copy2(os.path.join(here, f), os.path.join(base_dir, f))
                except Exception:
                    pass

    steps_log = []
    if IS_WINDOWS:
        progress(0.50, _("Registering the agent and firewall rules\u2026"))
        ok, out = _hidden_ps(os.path.join(base_dir, "install_agent.ps1"),
                             ["-InstallDir", base_dir, "-PythonExe", sys.executable])
        steps_log.append(("agent", ok, out))

        progress(0.75, _("Creating shortcuts\u2026"))
        ok2, out2 = _hidden_ps(os.path.join(base_dir, "post_install.ps1"),
                               ["-InstallDir", base_dir, "-PythonExe", sys.executable])
        steps_log.append(("shortcuts", ok2, out2))

    if plat["os"] != "windows" and plat.get("service") == "systemd":
        progress(0.55, _("Installing the systemd service\u2026"))
        unit = (
            "[Unit]\nDescription=ClassCtl Agent\n"
            "After=network-online.target\nWants=network-online.target\n\n"
            "[Service]\nType=simple\n"
            f"ExecStart={sys.executable} {os.path.join(base_dir,'agent.py')} "
            f"--config {os.path.join(base_dir,'agent.json')}\n"
            "Restart=always\nRestartSec=5\nUser=root\n\n"
            "[Install]\nWantedBy=multi-user.target\n")
        try:
            with open("/etc/systemd/system/classctl-agent.service", "w",
                      encoding="utf-8") as f:
                f.write(unit)
            subprocess.run(["systemctl", "daemon-reload"], capture_output=True)
            subprocess.run(["systemctl", "enable", "--now", "classctl-agent"],
                           capture_output=True)
        except Exception:
            pass

    progress(0.92, _("Checking that the agent answers\u2026"))
    listening = False
    port = common.DEFAULT_TCP_PORT
    for _attempt in range(16):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                listening = True
                break
        except Exception:
            time.sleep(0.5)

    progress(1.0, _("Done"))
    info["listening"] = listening
    info["log"] = steps_log
    return info



def installed_dir() -> str | None:
    """Where ClassCtl is installed, if it is."""
    for d in (DEFAULT_BASE,):
        if os.path.exists(os.path.join(d, "agent.json")):
            return d
    return None


def missing_starter_scripts(base_dir: str, plat: dict | None = None) -> list[str]:
    """Starter actions that were not created during setup."""
    plat = plat or detect_platform()
    scripts_dir = os.path.join(base_dir, "scripts")
    have = set(os.listdir(scripts_dir)) if os.path.isdir(scripts_dir) else set()
    return [k for k, (fname, _body) in basic_script_defs(plat).items() if fname not in have]


def add_starter_scripts(base_dir: str, keys: list[str],
                        plat: dict | None = None) -> list[str]:
    plat = plat or detect_platform()
    scripts_dir = os.path.join(base_dir, "scripts")
    os.makedirs(scripts_dir, exist_ok=True)
    defs = basic_script_defs(plat)
    created = []
    for k in keys:
        if k not in defs:
            continue
        fname, content = defs[k]
        path = os.path.join(scripts_dir, fname)
        if os.path.exists(path):
            continue
        is_win = fname.lower().endswith((".ps1", ".bat", ".cmd"))
        enc = "utf-8-sig" if fname.lower().endswith(".ps1") else "utf-8"
        with open(path, "w", encoding=enc, newline=("\r\n" if is_win else "\n")) as f:
            f.write(content)
        if not IS_WINDOWS:
            os.chmod(path, 0o755)
        created.append(fname)
    return created


def run_uninstall(base_dir: str) -> tuple[bool, str]:
    """Hand off to uninstall.ps1, which undoes every change made to Windows."""
    here = os.path.dirname(os.path.abspath(__file__))
    script = os.path.join(base_dir, "uninstall.ps1")
    if not os.path.exists(script):
        script = os.path.join(here, "uninstall.ps1")
    if IS_WINDOWS:
        if not os.path.exists(script):
            return False, "uninstall.ps1 not found"
        return _hidden_ps(script, ["-InstallDir", base_dir])
    # linux
    try:
        subprocess.run(["systemctl", "disable", "--now", "classctl-agent"],
                       capture_output=True)
        subprocess.run(["rm", "-f", "/etc/systemd/system/classctl-agent.service"],
                       capture_output=True)
        subprocess.run(["systemctl", "daemon-reload"], capture_output=True)
        shutil.rmtree(base_dir, ignore_errors=True)
        return (not os.path.exists(base_dir)), ""
    except Exception as e:
        return False, str(e)



# ---------- updating from the public repository ----------
UPDATE_REPO = "NadavOked/classctl"
UPDATE_BRANCH = "main"

# Only program files are replaced. Configuration, the network key, the chosen
# language and your own scripts are never touched.
UPDATE_EXTENSIONS = (".py", ".ps1", ".bat", ".ico")
UPDATE_KEEP = {"agent.json", "controller.json", "language.txt", "stations.json"}


def _remote_version() -> str | None:
    """Read VERSION straight out of common.py in the repository."""
    import urllib.request
    url = (f"https://raw.githubusercontent.com/{UPDATE_REPO}/"
           f"{UPDATE_BRANCH}/common.py")
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            text = r.read().decode("utf-8", "replace")
    except Exception:
        return None
    import re
    m = re.search(r'^VERSION\s*=\s*"([^"]+)"', text, re.M)
    return m.group(1) if m else None


def _as_tuple(v: str) -> tuple:
    try:
        return tuple(int(x) for x in v.split("."))
    except Exception:
        return (0,)


def check_for_update() -> tuple[str, str | None, bool]:
    """Returns (current, remote-or-None, remote-is-newer)."""
    rem = _remote_version()
    newer = bool(rem) and _as_tuple(rem) > _as_tuple(common.VERSION)
    return common.VERSION, rem, newer


def download_update(progress=None) -> str:
    """Fetch the repository as a zip and unpack it. Returns the folder."""
    import urllib.request, zipfile, io as _io
    url = (f"https://codeload.github.com/{UPDATE_REPO}/zip/refs/heads/"
           f"{UPDATE_BRANCH}")
    if progress:
        progress(0.15, "Downloading\u2026")
    with urllib.request.urlopen(url, timeout=60) as r:
        data = r.read()
    if progress:
        progress(0.45, "Unpacking\u2026")
    tmp = tempfile.mkdtemp(prefix="classctl-update-")
    with zipfile.ZipFile(_io.BytesIO(data)) as z:
        z.extractall(tmp)
    inner = [os.path.join(tmp, d) for d in os.listdir(tmp)
             if os.path.isdir(os.path.join(tmp, d))]
    if not inner:
        raise RuntimeError("the downloaded archive looks empty")
    return inner[0]


def new_actions_in(src_dir: str, base_dir: str) -> list[str]:
    """
    Starter action files the new version ships that this machine does not have.
    Read from the downloaded source as text - nothing from the download is
    executed here.
    """
    import re
    path = os.path.join(src_dir, "setup_wizard.py")
    try:
        text = io.open(path, encoding="utf-8").read()
    except Exception:
        return []
    names = set(re.findall(r'\("([a-z0-9\-]+\.(?:ps1|bat|sh))"', text))
    # keep only the flavour this machine uses
    if IS_WINDOWS:
        names = {n for n in names if n.endswith((".ps1", ".bat"))}
    else:
        names = {n for n in names if n.endswith(".sh")}
    scripts_dir = os.path.join(base_dir, "scripts")
    have = set(os.listdir(scripts_dir)) if os.path.isdir(scripts_dir) else set()
    return sorted(n for n in names if n not in have)


def apply_update(src_dir: str, base_dir: str, progress=None) -> list[str]:
    """Copy the new program files over the installation. Returns what changed."""
    changed = []
    files = [f for f in os.listdir(src_dir)
             if f.endswith(UPDATE_EXTENSIONS) and f not in UPDATE_KEEP]
    for i, f in enumerate(files):
        src = os.path.join(src_dir, f)
        dst = os.path.join(base_dir, f)
        try:
            old = io.open(src, encoding="utf-8", errors="replace").read()
            cur = (io.open(dst, encoding="utf-8", errors="replace").read()
                   if os.path.exists(dst) else None)
            if old != cur:
                shutil.copy2(src, dst)
                changed.append(f)
        except Exception:
            try:
                shutil.copy2(src, dst)
                changed.append(f)
            except Exception:
                pass
        if progress:
            progress(0.55 + 0.3 * (i + 1) / max(1, len(files)), "Replacing files\u2026")
    return changed


def restart_agent(base_dir: str) -> bool:
    """
    The agent holds its code and its key in memory, so it has to be stopped
    properly - ending the scheduled task alone does not do it.
    """
    if not IS_WINDOWS:
        try:
            subprocess.run(["systemctl", "restart", "classctl-agent"],
                           capture_output=True, timeout=30)
            return True
        except Exception:
            return False
    try:
        subprocess.run(["schtasks", "/end", "/tn", "ClassCtl Agent"],
                       capture_output=True, timeout=20,
                       creationflags=0x08000000)
        subprocess.run(["powershell", "-NoProfile", "-WindowStyle", "Hidden",
                        "-Command",
                        "Get-CimInstance Win32_Process -Filter \"Name='python.exe' "
                        "OR Name='pythonw.exe'\" | Where-Object { $_.CommandLine "
                        "-match 'agent.py' } | ForEach-Object { Stop-Process -Id "
                        "$_.ProcessId -Force -ErrorAction SilentlyContinue }"],
                       capture_output=True, timeout=30, creationflags=0x08000000)
        time.sleep(1)
        subprocess.run(["schtasks", "/run", "/tn", "ClassCtl Agent"],
                       capture_output=True, timeout=20,
                       creationflags=0x08000000)
        return True
    except Exception:
        return False



CRASH_LOG = os.path.join(tempfile.gettempdir(), "classctl-setup-error.log")


def report_fatal(detail: str) -> None:
    """
    Make a startup failure visible.

    install.bat launches this with pythonw so no console flashes up - which
    also means there is no stdout to print to and no stdin to wait on. Anything
    that goes wrong before the window exists used to produce complete silence:
    double-click, brief flash, nothing, forever. Write it down, then say so in
    a message box that needs neither a console nor tkinter.
    """
    try:
        with open(CRASH_LOG, "w", encoding="utf-8") as f:
            f.write(detail)
    except Exception:
        pass
    try:
        print(detail)          # only lands somewhere when run from a console
    except Exception:
        pass
    if IS_WINDOWS:
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                0,
                "ClassCtl setup could not start.\n\n%s\n\nFull details:\n%s"
                % (detail.strip().splitlines()[-1] if detail.strip() else "",
                   CRASH_LOG),
                "ClassCtl setup", 0x10)
        except Exception:
            pass


def run_gui():
    import tkinter as tk
    import ui

    base_now = installed_dir()
    i18n.set_lang(common.read_lang(base_now) if base_now else "en")
    from ui import CANVAS, SURFACE, INK, MUTED, LINE, SIGNAL, FIELD, UI, MONO

    root = tk.Tk()

    def _on_error(exc, val, tb):
        import traceback
        try:
            ui.error(root, _("Something went wrong"),
                     "".join(traceback.format_exception_only(exc, val)).strip())
        except Exception:
            pass
    root.report_callback_exception = _on_error

    root.title("ClassCtl Setup")
    root.overrideredirect(True)
    W, H = 560, 600
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"{W}x{H}+{(sw-W)//2}+{(sh-H)//3}")
    root.configure(bg=CANVAS, highlightbackground=LINE, highlightthickness=1)
    root.after(50, lambda: ui.round_window_corners(root))

    # ---- our own title bar ----
    bar = tk.Frame(root, bg=INK, height=48); bar.pack(fill="x"); bar.pack_propagate(False)
    tk.Label(bar, text=_("CLASSCTL SETUP"), bg=INK, fg="#FFFFFF",
             font=(UI, 11, "bold")).pack(side=i18n.side("left"), padx=18)
    closer = tk.Label(bar, text="\u2715", bg=INK, fg="#FFFFFF", font=(UI, 11),
                      cursor="hand2")
    closer.pack(side=i18n.side("right"), padx=16)
    closer.bind("<Button-1>", lambda e: root.destroy())
    drag = {"x": 0, "y": 0}
    def sd(e): drag.update(x=e.x_root - root.winfo_x(), y=e.y_root - root.winfo_y())
    def dd(e): root.geometry(f"+{e.x_root - drag['x']}+{e.y_root - drag['y']}")
    for w in (bar, bar.winfo_children()[0]):
        w.bind("<Button-1>", sd); w.bind("<B1-Motion>", dd)

    dots = ui.StepDots(root, 4, bg=CANVAS); dots.pack(pady=(14, 0))
    foot = tk.Frame(root, bg=CANVAS); foot.pack(side="bottom", fill="x", pady=(0, 18))
    scroller = ui.ScrollFrame(root, bg=CANVAS)
    scroller.pack(fill="both", expand=True)
    stage = scroller.inner

    data = {"password": "", "dir": DEFAULT_BASE, "platform": detect_platform(),
            "lang": i18n.get_lang(),
            "scripts": ["shutdown", "restart", "reset_network",
                        "close_windows", "wake_screens"]}

    def clear():
        for w in stage.winfo_children(): w.destroy()
        for w in foot.winfo_children(): w.destroy()

    def settle():
        """Give the window the height this screen actually needs."""
        def do():
            root.update_idletasks()
            need = (bar.winfo_height()
                    + dots.c.winfo_height() + 14
                    + scroller.inner.winfo_reqheight()
                    + foot.winfo_reqheight() + 40)
            ui.size_window(root, need, width=W)
        root.after_idle(do)

    def title(parent, text, sub=""):
        tk.Label(parent, text=text, bg=CANVAS, fg=INK,
                 font=(UI, 16, "bold")).pack(anchor=i18n.anchor("w"), padx=30, pady=(18, 2))
        if sub:
            tk.Label(parent, text=sub, bg=CANVAS, fg=MUTED, font=(UI, 10),
                     wraplength=480, justify=i18n.justify("left")).pack(anchor=i18n.anchor("w"), padx=30)

    def card(parent):
        return ui.Card(parent, bg=CANVAS, pad=18).pack(anchor=i18n.anchor("w"), padx=30,
                                                       pady=16).inner

    # ---------------- step 1: password ----------------
    def step_password():
        clear(); dots.set(0)
        title(stage, _("Set the console password"),
              _("You will type this every time you open ClassCtl. It is stored as a "
              "hash, so nobody can read it back out of the files."))
        c = card(stage)

        def field(lbl):
            r = tk.Frame(c, bg=SURFACE); r.pack(fill="x", pady=5)
            tk.Label(r, text=lbl, bg=SURFACE, fg=INK, font=(UI, 10),
                     width=17, anchor=i18n.anchor("w")).pack(side=i18n.side("left"))
            e = tk.Entry(r, show="\u2022", width=26, font=(UI, 11), relief="flat",
                         bg=FIELD, fg=INK, insertbackground=INK)
            e.pack(side=i18n.side("left"), ipady=6)
            return e

        p1 = field("Password"); p2 = field("Confirm password")
        p1.insert(0, data["password"]); p2.insert(0, data["password"])
        p1.focus_set()

        sv = tk.BooleanVar(value=False)
        def tog():
            ch = "" if sv.get() else "\u2022"
            p1.config(show=ch); p2.config(show=ch)
        ui.Checkbox(c, _("Show password"), variable=sv, command=tog, bg=SURFACE,
                    font_size=9, fg=MUTED).pack(anchor=i18n.anchor("w"), pady=(8, 0))

        def nxt():
            if not p1.get():
                ui.error(root, _("Password required"), _("Enter a password to continue."))
                root.after(60, lambda: (p1.focus_force(), p1.icursor("end")))
                return
            if p1.get() != p2.get():
                ui.error(root, _("Passwords differ"),
                         _("The two passwords do not match. Type the second one again."))
                # put the cursor back where the correction is made
                def retry_focus():
                    p2.delete(0, "end")
                    p2.focus_force()
                root.after(60, retry_focus)
                return
            data["password"] = p1.get()
            step_options()

        ui.RButton(foot, _("Next"), nxt, width=150, height=46, bg=CANVAS).pack(side=i18n.side("right"), padx=30)
        root.bind("<Return>", lambda e: nxt())
        settle()

    # ---------------- step 2: location + actions ----------------
    def step_options():
        clear(); dots.set(1)
        root.unbind("<Return>")
        title(stage, _("Location and starter actions"),
              _("The folder is locked to administrators and hidden, because the "
              "network key lives in it."))
        c = card(stage)
        pv = tk.StringVar(value=data["dir"])
        r = tk.Frame(c, bg=SURFACE); r.pack(fill="x")
        tk.Entry(r, textvariable=pv, width=36, font=(MONO, 9), relief="flat",
                 bg=FIELD, fg=INK).pack(side=i18n.side("left"), ipady=6)
        def browse():
            # the ordinary Windows browser: familiar, and better at navigating
            from tkinter import filedialog
            d = filedialog.askdirectory(parent=root, title="Choose install location",
                                        initialdir=os.path.dirname(DEFAULT_BASE) or "/")
            if d:
                pv.set(os.path.join(os.path.normpath(d), "ClassCtl"))
            root.after(60, root.focus_force)
        ui.RButton(r, _("Browse"), browse, kind="quiet", width=92, height=32,
                   radius=8, font_size=9, bg=SURFACE).pack(side=i18n.side("left"), padx=10)

        c2 = card(stage)
        tk.Label(c2, text=_("Starter actions"), bg=SURFACE, fg=INK,
                 font=(UI, 10, "bold")).pack(anchor=i18n.anchor("w"), pady=(0, 6))
        labels = {"shutdown": _("Shut down"), "restart": _("Restart"),
                  "reset_network": _("Reset network card"),
                  "close_windows": _("Close open windows"),
                  "wake_screens": _("Wake screens"),
                  "open_word": _("Open Word"),
                  "open_excel": _("Open Excel"),
                  "open_powerpoint": _("Open PowerPoint")}
        vs = {k: tk.BooleanVar(value=(k in data["scripts"])) for k in labels}
        for k, t in labels.items():
            ui.Checkbox(c2, t, variable=vs[k], bg=SURFACE).pack(anchor=i18n.anchor("w"), pady=2)
        tk.Label(c2, text=_("Each one becomes a button. You can add or remove any time."),
                 bg=SURFACE, fg=MUTED, font=(UI, 9)).pack(anchor=i18n.anchor("w"), pady=(8, 0))

        def nxt():
            data["dir"] = pv.get().strip() or DEFAULT_BASE
            data["scripts"] = [k for k, v in vs.items() if v.get()]
            step_review()

        ui.RButton(foot, _("Next"), nxt, width=150, height=46, bg=CANVAS).pack(side=i18n.side("right"), padx=30)
        ui.RButton(foot, _("Back"), step_password, kind="quiet", width=110, height=46,
                   bg=CANVAS).pack(side=i18n.side("right"))
        settle()

    # ---------------- step 3: review ----------------
    def step_review():
        clear(); dots.set(2)
        title(stage, _("Ready to install"),
              _("Check this, then install. It takes a few seconds."))
        c = card(stage)
        rows = [(_("This machine"), platform_summary(data["platform"])),
                (_("Install to"), data["dir"]),
                (_("Folder access"), _("Administrators only, hidden")),
                (_("Agent"), _("Runs as SYSTEM at every boot")
                          if data["platform"]["os"] == "windows"
                          else _("Runs as a systemd service")),
                (_("Firewall"), f"TCP {common.DEFAULT_TCP_PORT} / "
                                f"UDP {common.DEFAULT_UDP_PORT}"),
                (_("Actions"), ", ".join(data["scripts"]) or _("none"))]
        for k, v in rows:
            r = tk.Frame(c, bg=SURFACE); r.pack(fill="x", pady=3)
            tk.Label(r, text=k, bg=SURFACE, fg=MUTED, font=(UI, 9),
                     width=14, anchor=i18n.anchor("w")).pack(side=i18n.side("left"))
            tk.Label(r, text=v, bg=SURFACE, fg=INK, font=(UI, 9), anchor=i18n.anchor("w"),
                     wraplength=330, justify=i18n.justify("left")).pack(side=i18n.side("left"))

        if not is_admin():
            tk.Label(stage, text=_("Not running as administrator - the protected folder and the agent service will fail."),
                     bg=CANVAS, fg=ui.ALERT, font=(UI, 9), wraplength=480,
                     justify=i18n.justify("left")).pack(anchor=i18n.anchor("w"), padx=30)

        ui.RButton(foot, _("Install"), step_install, width=150, height=46,
                   bg=CANVAS).pack(side=i18n.side("right"), padx=30)
        ui.RButton(foot, _("Back"), step_options, kind="quiet", width=110, height=46,
                   bg=CANVAS).pack(side=i18n.side("right"))
        settle()

    # ---------------- step 4: installing ----------------
    def step_install():
        clear(); dots.set(3)
        title(stage, _("Installing"), "")
        c = card(stage)
        msg = tk.Label(c, text=_("Starting\u2026"), bg=SURFACE, fg=INK, font=(UI, 10),
                       anchor=i18n.anchor("w"))
        msg.pack(fill="x", pady=(2, 12))
        bar_ = ui.ProgressBar(c, width=460, height=10, bg=SURFACE); bar_.pack(anchor=i18n.anchor("w"))
        pct = tk.Label(c, text=_("0%"), bg=SURFACE, fg=MUTED, font=(MONO, 9))
        pct.pack(anchor=i18n.anchor("e"), pady=(6, 0))

        def progress(frac, text):
            def upd():
                bar_.set(frac); msg.config(text=text)
                pct.config(text=f"{int(frac*100)}%")
            root.after(0, upd)

        def work():
            try:
                info = perform_install(data["password"], data["dir"],
                                       data["scripts"], progress,
                                       data["platform"])
                try:
                    common.write_lang(data["dir"], data.get("lang", "en"))
                except Exception:
                    pass
            except PermissionError:
                root.after(0, lambda: (ui.error(root, _("Access denied"),
                           _("Run the installer as administrator and try again.")),
                           step_review()))
                return
            except Exception as e:
                root.after(0, lambda: (ui.error(root, _("Install failed"), str(e)),
                                       step_review()))
                return
            root.after(600, lambda: step_done(info))

        threading.Thread(target=work, daemon=True).start()

    # ---------------- step 5: done ----------------
    def step_done(info):
        clear(); dots.set(3)
        good = info.get("listening")
        title(stage, "Installed" if good else "Installed with a warning",
              "Open ClassCtl from the Start menu or the desktop."
              if good else
              "Setup finished, but the agent did not answer on its port yet. "
              "Reboot, then open ClassCtl and press Rescan.")
        c = card(stage)
        rows = [(_("Scripts folder"), info["scripts_dir"]),
                (_("For your image"), info["agent_path"]),
                (_("Actions created"), ", ".join(info["created"]) or _("none")),
                (_("Agent"), _("listening") if good else _("not answering yet"))]
        for k, v in rows:
            r = tk.Frame(c, bg=SURFACE); r.pack(fill="x", pady=3)
            tk.Label(r, text=k, bg=SURFACE, fg=MUTED, font=(UI, 9),
                     width=15, anchor=i18n.anchor("w")).pack(side=i18n.side("left"))
            tk.Label(r, text=v, bg=SURFACE, fg=(ui.OK if good else INK),
                     font=(MONO, 8), anchor=i18n.anchor("w"), wraplength=320,
                     justify=i18n.justify("left")).pack(side=i18n.side("left"))
        tk.Label(stage, text=_("Copy agent.json and controller.json to every other PC so they all share one network key."),
                 bg=CANVAS, fg=MUTED, font=(UI, 9), wraplength=480,
                 justify=i18n.justify("left")).pack(anchor=i18n.anchor("w"), padx=30)
        ui.RButton(foot, _("Close"), root.destroy, width=150, height=46,
                   bg=CANVAS).pack(side=i18n.side("right"), padx=30)

    # ---------------- launcher ----------------
    def step_launcher():
        clear()
        dots.c.pack_forget()
        installed = installed_dir()

        tk.Label(stage, text=_("ClassCtl"), bg=CANVAS, fg=INK,
                 font=(UI, 20, "bold")).pack(anchor=i18n.anchor("w"), padx=30, pady=(22, 2))
        tk.Label(stage,
                 text=("Already installed on this computer."
                       if installed else "Not installed on this computer yet."),
                 bg=CANVAS, fg=MUTED, font=(UI, 10)).pack(anchor=i18n.anchor("w"), padx=30)

        # language switch, so this can be set before anything is installed
        langrow = tk.Frame(stage, bg=CANVAS)
        langrow.pack(fill="x", padx=30, pady=(14, 0))
        tk.Label(langrow, text=_("Language"), bg=CANVAS, fg=MUTED,
                 font=(UI, 9, "bold")).pack(side=i18n.side("left"))

        def choose_lang(code):
            i18n.set_lang(code)
            b = installed_dir()
            if b:
                try:
                    common.write_lang(b, code)
                except Exception:
                    pass
            data["lang"] = code
            step_launcher()

        for code, label in (("en", "English"), ("he", "\u05e2\u05d1\u05e8\u05d9\u05ea")):
            kind = "primary" if i18n.get_lang() == code else "quiet"
            ui.RButton(langrow, label, (lambda c=code: choose_lang(c)), kind=kind,
                       width=92, height=30, radius=8, font_size=9, bg=CANVAS
                       ).pack(side=i18n.side("left"), padx=6)

        opts = tk.Frame(stage, bg=CANVAS)
        opts.pack(fill="x", padx=30, pady=(18, 0))

        def option(title, desc, btn, cmd, kind="primary", enabled=True):
            c = ui.Card(opts, bg=CANVAS, pad=14)
            c.pack(fill="x", pady=7)
            box = c.inner
            tk.Label(box, text=title, bg=SURFACE, fg=(INK if enabled else MUTED),
                     font=(UI, 12, "bold")).pack(anchor=i18n.anchor("w"))
            tk.Label(box, text=desc, bg=SURFACE, fg=MUTED, font=(UI, 9),
                     justify=i18n.justify("left"), wraplength=380).pack(anchor=i18n.anchor("w"), pady=(2, 8))
            ui.RButton(box, btn, (cmd if enabled else (lambda: None)),
                       kind=(kind if enabled else "quiet"),
                       width=150, height=38, bg=SURFACE).pack(anchor=i18n.anchor("w"))

        option(_("Install"),
               _("Set a password, choose the folder and the starter actions."),
               _("Install"), step_password, "primary", not installed)
        option(_("Add actions"),
               _("Add starter actions you did not pick during setup."),
               _("Add"), step_add_scripts, "quiet", bool(installed))
        option(_("Update"),
               _("Check the public repository and install the newest version. "
                 "Your password, key and scripts are kept."),
               _("Update"), step_update, "quiet", bool(installed))
        option(_("Uninstall"),
               _("Stop the agent, close the two ports, remove the shortcuts and "
                 "delete the protected folder."),
               _("Uninstall"), step_uninstall, "danger", bool(installed))
        settle()

    # ---------------- add starter actions ----------------
    def step_add_scripts():
        clear()
        base = installed_dir() or DEFAULT_BASE
        missing = missing_starter_scripts(base, data["platform"])
        title(stage, _("Add actions"),
              _("Only the ones you do not have yet are listed. Each becomes a button."))
        if not missing:
            c = card(stage)
            tk.Label(c, text=_("Every starter action is already installed."),
                     bg=SURFACE, fg=INK, font=(UI, 10)).pack(anchor=i18n.anchor("w"))
            ui.RButton(foot, _("Back"), step_launcher, kind="quiet", width=130,
                       height=46, bg=CANVAS).pack(side=i18n.side("right"), padx=30)
            settle()
            return

        c = card(stage)
        labels = {"shutdown": _("Shut down"), "restart": _("Restart"),
                  "reset_network": _("Reset network card"),
                  "close_windows": _("Close open windows"),
                  "wake_screens": _("Wake screens"),
                  "open_word": _("Open Word"),
                  "open_excel": _("Open Excel"),
                  "open_powerpoint": _("Open PowerPoint")}
        vs = {k: tk.BooleanVar(value=True) for k in missing}
        for k in missing:
            ui.Checkbox(c, labels.get(k, k), variable=vs[k],
                        bg=SURFACE).pack(anchor=i18n.anchor("w"), pady=2)

        def do_add():
            chosen = [k for k, v in vs.items() if v.get()]
            if not chosen:
                ui.info(root, _("Nothing selected"), _("Tick at least one action.")); return
            try:
                created = add_starter_scripts(base, chosen, data["platform"])
            except PermissionError:
                ui.error(root, _("Access denied"),
                         _("Run this as administrator and try again.")); return
            except Exception as e:
                ui.error(root, _("Could not add"), str(e)); return
            ui.success(root, _("Actions added"),
                       _("Added: {list}", list=(", ".join(created) or _("none"))))
            step_launcher()

        ui.RButton(foot, _("Add"), do_add, width=150, height=46,
                   bg=CANVAS).pack(side=i18n.side("right"), padx=30)
        ui.RButton(foot, _("Back"), step_launcher, kind="quiet", width=110,
                   height=46, bg=CANVAS).pack(side=i18n.side("right"))
        settle()

    # ---------------- update ----------------
    def step_update():
        clear()
        base = installed_dir() or DEFAULT_BASE
        title(stage, _("Update ClassCtl"),
              _("Fetches the newest version from the public repository."))
        c = card(stage)
        line1 = tk.Label(c, text=_("Installed version: {v}", v=common.VERSION),
                         bg=SURFACE, fg=INK, font=(UI, 10), anchor=i18n.anchor("w"))
        line1.pack(fill="x", pady=2)
        line2 = tk.Label(c, text=_("Checking the repository\u2026"), bg=SURFACE,
                         fg=MUTED, font=(UI, 10), anchor=i18n.anchor("w"),
                         justify=i18n.justify("left"), wraplength=420)
        line2.pack(fill="x", pady=2)

        holder = {"remote": None, "src": None, "new": []}

        def check():
            cur, rem, newer = check_for_update()

            def show():
                holder["remote"] = rem
                if rem is None:
                    line2.config(text=_("Could not reach the repository. Check the "
                                        "internet connection."), fg=ui.ALERT)
                elif newer:
                    line2.config(text=_("Version {v} is available.", v=rem), fg=SIGNAL)
                else:
                    line2.config(text=_("This is the newest version."), fg=ui.OK)
            root.after(0, show)
        threading.Thread(target=check, daemon=True).start()

        def do_update():
            clear()
            title(stage, _("Updating"), "")
            c2 = card(stage)
            msg = tk.Label(c2, text=_("Starting\u2026"), bg=SURFACE, fg=INK,
                           font=(UI, 10), anchor=i18n.anchor("w"))
            msg.pack(fill="x", pady=(2, 12))
            bar_ = ui.ProgressBar(c2, width=440, height=10, bg=SURFACE)
            bar_.pack(anchor=i18n.anchor("w"))

            def progress(frac, text):
                root.after(0, lambda: (bar_.set(frac), msg.config(text=_(text))))

            def work():
                try:
                    src = download_update(progress)
                    new_actions = new_actions_in(src, base)
                    changed = apply_update(src, base, progress)
                    progress(0.9, "Restarting the agent\u2026")
                    restart_agent(base)
                    progress(1.0, "Done")
                except Exception as e:
                    root.after(0, lambda: (ui.error(root, _("Update failed"), str(e)),
                                           step_launcher()))
                    return

                def finish():
                    if not changed:
                        ui.info(root, _("Nothing to update"),
                                _("The files are already up to date."))
                        step_launcher()
                        return
                    if new_actions:
                        ui.success(root, _("Updated"),
                                   _("{n} files updated.\n\nThis version also adds "
                                     "these actions:\n{list}\n\nUse Add actions to "
                                     "put them on the console.",
                                     n=len(changed), list=", ".join(new_actions)))
                    else:
                        ui.success(root, _("Updated"),
                                   _("{n} files updated. No new actions in this "
                                     "version.", n=len(changed)))
                    # rerun so the new code is the one running
                    root.destroy()
                    os.execv(sys.executable, [sys.executable] + sys.argv)
                root.after(0, finish)
            threading.Thread(target=work, daemon=True).start()

        ui.RButton(foot, _("Update"), do_update, width=150, height=46,
                   bg=CANVAS).pack(side=i18n.side("right"), padx=30)
        ui.RButton(foot, _("Back"), step_launcher, kind="quiet", width=110,
                   height=46, bg=CANVAS).pack(side=i18n.side("right"))
        settle()

    # ---------------- uninstall ----------------
    def step_uninstall():
        clear()
        base = installed_dir() or DEFAULT_BASE
        title(stage, _("Uninstall ClassCtl"),
              _("This undoes everything the installer changed on this computer."))
        c = card(stage)
        for line_txt in ("Stops the agent and removes its scheduled task",
                         "Closes TCP 48720 and UDP 48719 in the firewall",
                         "Removes the desktop and Start menu shortcuts",
                         "Removes the Add/Remove Programs entry",
                         "Deletes " + base + ", including your password and key"):
            tk.Label(c, text="\u2022  " + line_txt, bg=SURFACE, fg=INK,
                     font=(UI, 10), justify=i18n.justify("left"), wraplength=420).pack(anchor=i18n.anchor("w"),
                                                                        pady=1)

        def do_uninstall():
            if not ui.confirm(root, _("Uninstall ClassCtl"),
                              _("Remove ClassCtl from this computer?\n"
                              "The password and the network key are deleted with it."),
                              ok_text=_("Uninstall"), danger=True):
                return
            clear()
            title(stage, _("Uninstalling"), "")
            c2 = card(stage)
            msg = tk.Label(c2, text=_("Working\u2026"), bg=SURFACE, fg=INK,
                           font=(UI, 10), anchor=i18n.anchor("w"))
            msg.pack(fill="x", pady=(2, 12))
            bar_ = ui.ProgressBar(c2, width=440, height=10, bg=SURFACE)
            bar_.pack(anchor=i18n.anchor("w"))
            prog = {"v": 0.0}

            def creep():
                if prog["v"] < 0.9:
                    prog["v"] += 0.04
                    bar_.set(prog["v"])
                    root.after(220, creep)
            creep()

            def work():
                ok, out = run_uninstall(base)

                def finish():
                    prog["v"] = 1.0
                    bar_.set(1.0)
                    if ok:
                        ui.success(root, _("Uninstalled"),
                                   _("ClassCtl has been removed from this computer."))
                    else:
                        ui.error(root, _("Uninstall problem"),
                                 (out or "").strip()[-400:] or
                                 "Something did not complete. Reboot and try again.")
                    root.destroy()
                root.after(0, finish)
            threading.Thread(target=work, daemon=True).start()

        ui.RButton(foot, _("Uninstall"), do_uninstall, kind="danger", width=150,
                   height=46, bg=CANVAS).pack(side=i18n.side("right"), padx=30)
        ui.RButton(foot, _("Back"), step_launcher, kind="quiet", width=110,
                   height=46, bg=CANVAS).pack(side=i18n.side("right"))
        settle()

    # dots belong to the install flow only
    _orig_step_password = step_password

    def step_password_with_dots():
        # before= must name a widget that is actually packed in root. `stage` is
        # the ScrollFrame's inner frame, which lives inside a canvas via
        # create_window and is never packed, so passing it raised TclError and
        # the Install button did nothing but show "Something went wrong".
        dots.c.pack(pady=(14, 0), before=scroller.holder)
        _orig_step_password()
    step_password = step_password_with_dots

    step_launcher()
    root.mainloop()


def run_cli():
    import getpass
    print("== ClassCtl setup (CLI) ==")
    p1 = getpass.getpass("Password: ")
    p2 = getpass.getpass("Confirm : ")
    if p1 != p2 or not p1:
        print("passwords empty or mismatch"); return 1
    base = input(f"Install dir [{DEFAULT_BASE}]: ").strip() or DEFAULT_BASE
    protect = True   # always: net_key lives here
    chosen = []
    for k, txt in (("shutdown", "shutdown"), ("restart", "restart"),
                   ("reset_network", "reset network"), ("close_windows", "close windows"),
                   ("wake_screens", "wake screens"), ("open_word", "open Word"),
                   ("open_excel", "open Excel"),
                   ("open_powerpoint", "open PowerPoint")):
        if input(f"Add basic script '{txt}'? [Y/n]: ").strip().lower() != "n":
            chosen.append(k)
    info = do_setup(p1, base, protect, chosen)
    print("scripts dir :", info["scripts_dir"])
    print("created     :", ", ".join(info["created"]) or "(none)")
    print("agent.json  :", info["agent_path"], "  <-- put this in the image")
    print("controller  :", info["controller_path"])
    print(info["protect_msg"])
    return 0


if __name__ == "__main__":
    # אם אין tkinter -> CLI. אם ה-GUI נכשל -> להציג את השגיאה, לא לבלוע אותה.
    try:
        import tkinter  # noqa: F401
        has_tk = True
    except Exception as e:
        report_fatal("Python on this computer has no tkinter, so the setup "
                     "window cannot be shown.\n\n%s" % e)
        has_tk = False

    if has_tk:
        try:
            run_gui()
        except Exception:
            import traceback
            report_fatal(traceback.format_exc())
            sys.exit(1)
    else:
        sys.exit(run_cli())
