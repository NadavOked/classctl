"""
classctl.common
משותף בין הסוכן (agent), השלט (controller) ואשף ההתקנה.
כולל: זיהוי קידומת-כיתה, hashing לסיסמה, חתימת HMAC לפקודות רשת,
פרוטוקול העברה על TCP, ושירות גילוי (discovery) על UDP.

ספרייה סטנדרטית בלבד — אין תלות ב-pip, כדי שאריזה ל-exe תהיה פשוטה.
"""

import base64
import hashlib
import hmac
import json
import os
import platform
import socket
import struct
import time

# ---------- קבועים ----------
VERSION = "0.2.0"
PROTO_VERSION = 1
IS_WINDOWS = platform.system() == "Windows"
DEFAULT_TCP_PORT = 48720      # פקודות
DEFAULT_UDP_PORT = 48719      # גילוי מחשבים
PBKDF2_ITERATIONS = 600_000   # לסיסמת הכניסה לתוכנה
TS_WINDOW_SEC = 120           # חלון סבילות לשעון (מניעת replay)
DISCOVERY_MAGIC = "CLASSCTL_DISCOVER_V1"


# ---------- זיהוי כיתה לפי שם מחשב ----------
def short_hostname(name: str | None = None) -> str:
    """שם המחשב בלי סיומת דומיין, באותיות גדולות."""
    name = name or socket.gethostname()
    name = name.split(".")[0].strip()
    return name.upper()


def classroom_prefix(hostname: str | None = None) -> str:
    """
    Group id = the name without its station segment.
    The station segment is the last '-' part when it is digits or INS,
    matching the naming convention already used on site.

    101-12      -> 101
    202-08      -> 202
    303-ins     -> 303
    5500-01-INS -> 5500-01   (another room of the same course stays separate)
    5500-01-05  -> 5500-01
    LAB-EAST    -> LAB       (free-form suffix: fall back to the first part)
    """
    h = short_hostname(hostname)
    if "-" not in h:
        return h
    head, tail = h.rsplit("-", 1)
    if tail.isdigit() or "INS" in tail.upper():
        return head
    return h.split("-", 1)[0]


def same_classroom(host_a: str, host_b: str) -> bool:
    return classroom_prefix(host_a) == classroom_prefix(host_b)


def station_label(hostname: str | None = None) -> str:
    """
    Short label for display: the station segment only.
    101-12 -> 12 | 202-08 -> 8 | 303-ins -> INS
    5500-01-INS -> INS | 5500-01-05 -> 5 | LAB-EAST -> EAST
    """
    h = short_hostname(hostname)
    if "-" not in h:
        return h
    tail = h.rsplit("-", 1)[1]
    if "INS" in tail.upper():
        return "INS"
    if tail.isdigit():
        return str(int(tail))
    return tail


# ---------- סיסמת הכניסה לתוכנה (hash, לא הצפנה הפיכה) ----------
def hash_password(password: str, *, salt: bytes | None = None,
                  iterations: int = PBKDF2_ITERATIONS) -> dict:
    salt = salt or os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return {
        "algo": "pbkdf2_sha256",
        "iterations": iterations,
        "salt": salt.hex(),
        "hash": dk.hex(),
    }


def verify_password(password: str, record: dict) -> bool:
    try:
        salt = bytes.fromhex(record["salt"])
        it = int(record["iterations"])
        expected = bytes.fromhex(record["hash"])
    except (KeyError, ValueError):
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, it)
    return hmac.compare_digest(dk, expected)


# ---------- מפתח רשת משותף + חתימת פקודות ----------
def gen_net_key() -> str:
    """מפתח אקראי חזק שנוצר פעם אחת בהתקנה ומשותף לשלט ולכל הסוכנים."""
    return os.urandom(32).hex()


def _canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign(net_key_hex: str, payload: dict) -> str:
    key = bytes.fromhex(net_key_hex)
    return hmac.new(key, _canonical(payload), hashlib.sha256).hexdigest()


def verify_sig(net_key_hex: str, payload: dict, sig: str) -> bool:
    expected = sign(net_key_hex, payload)
    return hmac.compare_digest(expected, sig or "")


def fresh_timestamp_ok(ts: float) -> bool:
    return abs(time.time() - float(ts)) <= TS_WINDOW_SEC


# ---------- פרוטוקול TCP: אורך (4 bytes) + JSON ----------
def send_msg(sock: socket.socket, obj: dict) -> None:
    data = json.dumps(obj).encode("utf-8")
    sock.sendall(struct.pack(">I", len(data)) + data)


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("connection closed")
        buf += chunk
    return buf


def recv_msg(sock: socket.socket, max_len: int = 8 * 1024 * 1024) -> dict:
    (length,) = struct.unpack(">I", _recv_exact(sock, 4))
    if length > max_len:
        raise ValueError("message too large")
    return json.loads(_recv_exact(sock, length).decode("utf-8"))


# ---------- בניית פקודה חתומה ----------
def build_command(net_key_hex: str, cmd: str, args: dict | None = None) -> dict:
    payload = {
        "v": PROTO_VERSION,
        "cmd": cmd,
        "args": args or {},
        "ts": time.time(),
        "nonce": os.urandom(12).hex(),
    }
    payload["sig"] = sign(net_key_hex, {k: payload[k] for k in ("v", "cmd", "args", "ts", "nonce")})
    return payload


def check_command(net_key_hex: str, msg: dict, seen_nonces: set | None = None) -> tuple[bool, str]:
    """מאמת פקודה נכנסת בצד הסוכן. מחזיר (תקין, סיבת-כשל)."""
    try:
        core = {k: msg[k] for k in ("v", "cmd", "args", "ts", "nonce")}
    except KeyError:
        return False, "malformed"
    if not verify_sig(net_key_hex, core, msg.get("sig", "")):
        return False, "bad signature"
    if not fresh_timestamp_ok(core["ts"]):
        return False, "stale timestamp"
    if seen_nonces is not None:
        if core["nonce"] in seen_nonces:
            return False, "replay"
        seen_nonces.add(core["nonce"])
    return True, ""


# ---------- קונפיג ----------
def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(path: str, cfg: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ---------- עזר לסקריפטים ----------
def encode_file(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


def decode_to_file(b64: str, path: str) -> None:
    with open(path, "wb") as f:
        f.write(base64.b64decode(b64))


def interpreter_for(path: str) -> list[str]:
    """הפקודה להרצת סקריפט לפי סיומת ומערכת הפעלה. משותף לסוכן ולהרצה מקומית."""
    ext = os.path.splitext(path)[1].lower()
    if IS_WINDOWS:
        if ext == ".ps1":
            return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path]
        if ext in (".bat", ".cmd"):
            return ["cmd", "/c", path]
        if ext == ".py":
            return ["python", path]
        return [path]
    if ext == ".py":
        return ["python3", path]
    if ext == ".sh":
        return ["bash", path]
    try:
        os.chmod(path, 0o755)
    except OSError:
        pass
    return [path]

def natural_key(hostname: str):
    """
    Order stations the way a person reads them: 1, 2, 3, 10, 11 - not 1, 10, 11, 2.
    The instructor PC sorts first.
    """
    h = short_hostname(hostname)
    label = station_label(h)
    if label == "INS":
        return (0, 0, "")
    if label.isdigit():
        return (1, int(label), "")
    return (2, 0, label)


# ---------- running something in the logged-on user's session ----------
# The agent runs as SYSTEM in session 0. Anything the user must SEE - a window
# opening, windows closing, the screen waking - has to run inside their session.
WIN_USER_SESSION = r"""
$RunningAsSystem = ([Security.Principal.WindowsIdentity]::GetCurrent().Name -eq 'NT AUTHORITY\SYSTEM')

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
            $q = schtasks /query /tn __TASK__ /fo LIST 2>$null
            if (-not ($q -match 'Running')) { break }
            Start-Sleep -Seconds 2
        }
        schtasks /delete /tn __TASK__ /f | Out-Null
        Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
    }
} else {
    Invoke-Expression $UserBody
}
"""


def win_in_user_session(body: str, task: str, tmp: str) -> str:
    """Wrap a PowerShell body so it runs in the logged-on user's session."""
    return (WIN_USER_SESSION
            .replace("__BODY__", body.strip("\n"))
            .replace("__TASK__", task)
            .replace("__TMP__", tmp)).lstrip("\n")


def open_app_script(target: str, is_windows: bool = True) -> str:
    """A script that opens an application on a station, for the user to see."""
    if is_windows:
        safe = target.replace("'", "''")
        body = (f"$app = '{safe}'\n"
                "try {\n"
                "    Start-Process -FilePath $app\n"
                "} catch {\n"
                "    try { Start-Process explorer.exe -ArgumentList $app } catch {}\n"
                "}\n")
        return win_in_user_session(body, "ClassCtlOpenApp", "cc_openapp.ps1")
    safe = target.replace('"', '\\"')
    return ("#!/bin/bash\n"
            "U=$(loginctl list-sessions --no-legend 2>/dev/null | awk '$3 != \"\" {print $3; exit}')\n"
            "[ -z \"$U\" ] && U=$(who 2>/dev/null | awk 'NR==1{print $1}')\n"
            f'CMD="{safe}"\n'
            'if [ -n "$U" ] && [ "$(id -u)" = "0" ]; then\n'
            '  su - "$U" -c "DISPLAY=${DISPLAY:-:0} nohup $CMD >/dev/null 2>&1 &"\n'
            'else\n'
            '  DISPLAY=${DISPLAY:-:0} nohup $CMD >/dev/null 2>&1 &\n'
            'fi\n')


# ---------- interface language ----------
def lang_file(base_dir: str) -> str:
    return os.path.join(base_dir, "language.txt")


def read_lang(base_dir: str, default: str = "en") -> str:
    try:
        with open(lang_file(base_dir), encoding="utf-8") as f:
            code = f.read().strip().lower()
        return code if code in ("en", "he") else default
    except Exception:
        return default


def write_lang(base_dir: str, code: str) -> None:
    with open(lang_file(base_dir), "w", encoding="utf-8") as f:
        f.write(code)


# ---------- surviving a restart without asking for the password again ----------
# Switching language has to relaunch the app, because a live tkinter layout
# cannot be mirrored reliably. A single-use ticket in the protected folder lets
# the new process carry on where the old one left off. The folder is readable
# only by administrators, and the ticket dies after 30 seconds or one use.
def issue_resume_ticket(base_dir: str) -> str:
    token = os.urandom(16).hex()
    path = os.path.join(base_dir, ".resume")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"token": token, "ts": time.time()}, f)
    return token


def redeem_resume_ticket(base_dir: str, token: str, max_age: float = 30.0) -> bool:
    path = os.path.join(base_dir, ".resume")
    try:
        with open(path, encoding="utf-8") as f:
            rec = json.load(f)
    except Exception:
        return False
    finally:
        try:
            os.remove(path)          # one use only, valid or not
        except Exception:
            pass
    return (rec.get("token") == token
            and (time.time() - float(rec.get("ts", 0))) <= max_age)
