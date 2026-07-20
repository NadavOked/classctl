"""
classctl.agent  —  רץ כשירות על כל מחשב בכיתה (הרשאות SYSTEM/root).
מאזין לפקודות חתומות מהשלט ומבצע אותן מקומית:
  shutdown / restart / reset_nic / run_script / list_scripts / ping
וגם משיב לשידורי גילוי (UDP) עם שם המחשב שלו.

הרצה:  python agent.py --config agent.json
"""

import argparse
import os
import platform
import socket
import socketserver
import tempfile
import subprocess
import sys
import threading
import time

import common

IS_WINDOWS = platform.system() == "Windows"
_seen_nonces: set[str] = set()
_nonce_lock = threading.Lock()


def log(*a):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), "[agent]", *a, flush=True)


# ---------- ביצוע פעולות מקומיות ----------
def do_run_script(scripts_dir: str, name: str, content_b64: str | None) -> str:
    # שם קובץ בלבד — הגנה מפני path traversal
    name = os.path.basename(name)
    os.makedirs(scripts_dir, exist_ok=True)
    path = os.path.join(scripts_dir, name)

    # אם השלט שלח תוכן — שומרים אותו (כך התיקייה נשארת מסונכרנת בכל המחשבים)
    if content_b64:
        common.decode_to_file(content_b64, path)
    if not os.path.exists(path):
        raise FileNotFoundError(f"script not found: {name}")

    # A script that powers the machine off must not be waited on: the reply has
    # to leave before Windows starts killing processes, or a station that shut
    # down correctly would be reported as failed.
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            text = f.read().lower()
        powers_off = any(k in text for k in
                         ("shutdown /s", "shutdown /r", "shutdown -h",
                          "shutdown -r", "stop-computer", "restart-computer"))
    except Exception:
        powers_off = False

    if powers_off:
        subprocess.Popen(common.interpreter_for(path), cwd=scripts_dir)
        return f"started: {name}"

    # Start it, then look back briefly. A script that dies immediately - wrong
    # interpreter, syntax error, missing dependency - used to be reported as a
    # success, which hid real failures. Long scripts still return "started", so
    # the console is never blocked and shutdown replies before powering off.
    out = tempfile.TemporaryFile()
    proc = subprocess.Popen(common.interpreter_for(path), cwd=scripts_dir,
                            stdout=out, stderr=subprocess.STDOUT)
    deadline = time.time() + 2.5
    while time.time() < deadline and proc.poll() is None:
        time.sleep(0.1)

    if proc.returncode not in (None, 0):
        detail = ""
        try:
            out.seek(0)
            lines = out.read().decode("utf-8", "replace").strip().splitlines()
            if lines:
                detail = ": " + lines[-1][:160]
        except Exception:
            pass
        raise RuntimeError(f"{name} failed (exit {proc.returncode}){detail}")
    return f"started: {name}"


def do_list_scripts(scripts_dir: str) -> list[str]:
    if not os.path.isdir(scripts_dir):
        return []
    return sorted(f for f in os.listdir(scripts_dir)
                  if os.path.isfile(os.path.join(scripts_dir, f)))


# ---------- שרת TCP לפקודות ----------
class CommandHandler(socketserver.BaseRequestHandler):
    def handle(self):
        cfg = self.server.cfg
        try:
            msg = common.recv_msg(self.request)
        except Exception as e:
            return

        with _nonce_lock:
            ok, reason = common.check_command(cfg["net_key"], msg, _seen_nonces)
            if len(_seen_nonces) > 5000:
                _seen_nonces.clear()
        if not ok:
            log("rejected:", reason, "from", self.client_address[0])
            self._reply(False, error=reason)
            return

        cmd = msg["cmd"]
        args = msg.get("args", {})
        host = common.short_hostname()
        try:
            if cmd == "ping":
                self._reply(True, host=host, result="pong")
            elif cmd == "run_script":
                res = do_run_script(cfg["scripts_dir"], args.get("name", ""),
                                    args.get("content_b64"))
                self._reply(True, host=host, result=res)
            elif cmd == "list_scripts":
                self._reply(True, host=host, result=do_list_scripts(cfg["scripts_dir"]))
            else:
                self._reply(False, host=host, error=f"unknown cmd: {cmd}")
        except Exception as e:
            log("error running", cmd, ":", e)
            self._reply(False, host=host, error=str(e))

    def _reply(self, ok, host="", result=None, error=""):
        try:
            common.send_msg(self.request, {"ok": ok, "host": host,
                                           "result": result, "error": error})
        except Exception:
            pass


class ThreadedTCP(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, addr, handler, cfg):
        super().__init__(addr, handler)
        self.cfg = cfg


# ---------- מאזין גילוי (UDP) ----------
def discovery_listener(cfg: dict, stop: threading.Event):
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    udp.bind(("", cfg.get("udp_port", common.DEFAULT_UDP_PORT)))
    udp.settimeout(1.0)
    host = common.short_hostname()
    prefix = common.classroom_prefix()
    log("discovery listening on udp", cfg.get("udp_port", common.DEFAULT_UDP_PORT))
    while not stop.is_set():
        try:
            data, addr = udp.recvfrom(2048)
        except socket.timeout:
            continue
        except OSError:
            break
        try:
            msg = data.decode("utf-8", "ignore")
            import json
            req = json.loads(msg)
        except Exception:
            continue
        if req.get("magic") != common.DISCOVERY_MAGIC:
            continue
        # מאמתים חתימה גם על הגילוי, כדי לא לחשוף מחשבים לכל שידור
        core = {k: req.get(k) for k in ("magic", "ts", "nonce")}
        if not common.verify_sig(cfg["net_key"], core, req.get("sig", "")):
            continue
        if not common.fresh_timestamp_ok(req.get("ts", 0)):
            continue
        reply = {"host": host, "prefix": prefix}
        udp.sendto(json.dumps(reply).encode("utf-8"), addr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="agent.json")
    args = ap.parse_args()
    cfg = common.load_config(args.config)
    cfg.setdefault("tcp_port", common.DEFAULT_TCP_PORT)
    cfg.setdefault("udp_port", common.DEFAULT_UDP_PORT)
    cfg.setdefault("scripts_dir", os.path.join(os.path.dirname(os.path.abspath(args.config)), "scripts"))
    os.makedirs(cfg["scripts_dir"], exist_ok=True)

    stop = threading.Event()
    t = threading.Thread(target=discovery_listener, args=(cfg, stop), daemon=True)
    t.start()

    srv = ThreadedTCP(("", cfg["tcp_port"]), CommandHandler, cfg)
    log(f"agent up as {common.short_hostname()} (class {common.classroom_prefix()}) "
        f"tcp={cfg['tcp_port']} scripts={cfg['scripts_dir']}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        srv.shutdown()


if __name__ == "__main__":
    sys.exit(main())
