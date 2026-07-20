"""
classctl.diagnose  —  find out why stations are not showing up.

Run on BOTH machines and compare the output:

    python diagnose.py --config C:\\ProgramData\\ClassCtl\\controller.json

To also test a specific machine directly (bypasses discovery):

    python diagnose.py --config ... --peer 10.0.0.55
"""

import argparse
import hashlib
import json
import os
import platform
import socket
import subprocess
import sys
import time

import common

IS_WINDOWS = platform.system() == "Windows"
OK, BAD, WARN = "[ OK ]", "[FAIL]", "[WARN]"


def line(mark, text, detail=""):
    print(f"{mark} {text}" + (f"\n         {detail}" if detail else ""))


def key_fingerprint(net_key: str) -> str:
    """Short, safe fingerprint. Same key -> same fingerprint on both machines."""
    return hashlib.sha256(net_key.encode()).hexdigest()[:12].upper()


def check_identity():
    print("\n--- IDENTITY ---")
    host = common.short_hostname()
    prefix = common.classroom_prefix()
    label = common.station_label(host)
    line(OK, f"Computer name : {host}")
    line(OK, f"Group prefix  : {prefix}")
    line(OK, f"Shown as      : {label}")
    if "-" not in host:
        line(BAD, "Name has no '-' separator.",
             "Rename as <group>-<number|INS>, e.g. SYS-01 / SYS-INS, then reboot.")
    return prefix, host


def check_config(cfg_path):
    print("\n--- CONFIG ---")
    if not os.path.exists(cfg_path):
        line(BAD, f"Config not found: {cfg_path}")
        return None
    cfg = common.load_config(cfg_path)
    line(OK, f"Config        : {cfg_path}")
    line(OK, f"Ports         : TCP {cfg.get('tcp_port')} / UDP {cfg.get('udp_port')}")
    fp = key_fingerprint(cfg["net_key"])
    print(f"\n  >>> NETWORK KEY FINGERPRINT: {fp} <<<")
    print("      This MUST be identical on every machine.")
    print("      If it differs, copy agent.json + controller.json from one")
    print("      machine to the others and restart the ClassCtl Agent task.\n")
    return cfg


def check_agent(cfg):
    print("--- LOCAL AGENT ---")
    port = cfg.get("tcp_port", common.DEFAULT_TCP_PORT)
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=3):
            line(OK, f"Agent is listening on TCP {port}")
    except Exception as e:
        line(BAD, f"Agent NOT listening on TCP {port}", str(e))
        if IS_WINDOWS:
            print("         Fix: schtasks /run /tn \"ClassCtl Agent\"")
        return False

    # signed ping to ourselves = proves the key works end to end
    try:
        msg = common.build_command(cfg["net_key"], "ping")
        with socket.create_connection(("127.0.0.1", port), timeout=4) as s:
            common.send_msg(s, msg)
            r = common.recv_msg(s)
        if r.get("ok"):
            line(OK, f"Signed ping accepted (agent reports host {r.get('host')})")
        else:
            line(BAD, "Agent rejected our signed command", str(r.get("error")))
    except Exception as e:
        line(BAD, "Signed ping failed", str(e))
    return True


def check_firewall(cfg):
    if not IS_WINDOWS:
        return
    print("\n--- FIREWALL ---")
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-NetFirewallRule -DisplayName 'ClassCtl Agent*' | "
             "Select-Object -ExpandProperty DisplayName"],
            capture_output=True, text=True, timeout=25)
        names = [l.strip() for l in out.stdout.splitlines() if l.strip()]
        if len(names) >= 2:
            line(OK, "Firewall rules present: " + ", ".join(names))
        else:
            line(BAD, "Firewall rules missing.",
                 "Re-run install_agent.ps1 as administrator.")
    except Exception as e:
        line(WARN, "Could not read firewall rules", str(e))


def check_discovery(cfg):
    print("\n--- DISCOVERY (UDP broadcast) ---")
    port = cfg.get("udp_port", common.DEFAULT_UDP_PORT)
    core = {"magic": common.DISCOVERY_MAGIC, "ts": time.time(),
            "nonce": os.urandom(8).hex()}
    pkt = dict(core)
    pkt["sig"] = common.sign(cfg["net_key"], core)
    data = json.dumps(pkt).encode()

    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    udp.settimeout(0.4)
    try:
        udp.sendto(data, ("255.255.255.255", port))
    except OSError as e:
        line(BAD, "Could not broadcast", str(e))
        return

    replies = []
    end = time.time() + 3.0
    while time.time() < end:
        try:
            raw, addr = udp.recvfrom(2048)
            replies.append((json.loads(raw.decode()), addr[0]))
        except socket.timeout:
            continue
        except Exception:
            continue
    udp.close()

    if not replies:
        line(BAD, "No agent answered the broadcast (not even this machine).",
             "Usually: different network key, blocked firewall, or agent not running.")
        return

    mine = common.classroom_prefix()
    print(f"  {len(replies)} agent(s) answered:\n")
    for r, ip in replies:
        h = common.short_hostname(r.get("host", "?"))
        same = common.classroom_prefix(h) == mine
        mark = "  same group" if same else "  DIFFERENT GROUP - will be filtered out"
        print(f"    {h:<20} {ip:<16}{mark}")
    if not any(common.classroom_prefix(common.short_hostname(r.get('host', '')))
               == mine and common.short_hostname(r.get('host', '')) !=
               common.short_hostname() for r, _ in replies):
        print("\n" + WARN + " No OTHER machine in this group answered the broadcast.")
        print("         Broadcast is often filtered on managed switches / VLANs.")
        scan_fallback(cfg)


def scan_fallback(cfg):
    """Broadcast found nothing -> probe the local /24 directly."""
    print("\n--- DIRECT SUBNET SCAN (broadcast fallback) ---")
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import controller
    except Exception as e:
        line(WARN, "Could not load scanner", str(e))
        return
    print("  scanning this subnet, please wait...")
    rejected = []
    found = controller.scan_subnet(cfg, rejected=rejected)
    if found:
        line(OK, f"{len(found)} station(s) found by direct scan:")
        for f in found:
            print(f"      {f['host']:<20} {f['ip']}")
        print("\n         Keys and firewall are fine - only BROADCAST is blocked.")
        print("         The console falls back to this scan automatically.")
        return

    if rejected:
        line(BAD, f"{len(rejected)} agent(s) answered but REFUSED our commands:")
        for r in rejected:
            print(f"      {r['ip']:<18} {r['error']}")
        print()
        print("         >>> THE NETWORK KEYS DO NOT MATCH. <<<")
        print("         Those machines are running and reachable; they just do not")
        print("         share this machine's key, so every command is discarded.")
        print()
        print("         Fix: copy agent.json and controller.json from ONE machine")
        print("         to all the others, then restart the agent there:")
        print('             schtasks /end /tn "ClassCtl Agent"')
        print('             schtasks /run /tn "ClassCtl Agent"')
        return

    line(BAD, "No agent answered anywhere on this subnet.",
         "They are on another subnet, powered off, or their agent is not running.")


def check_peer(cfg, ip):
    print(f"\n--- DIRECT TEST TO {ip} ---")
    port = cfg.get("tcp_port", common.DEFAULT_TCP_PORT)
    try:
        with socket.create_connection((ip, port), timeout=5):
            line(OK, f"TCP {port} reachable")
    except Exception as e:
        line(BAD, f"Cannot reach {ip}:{port}", str(e))
        print("         Firewall on the far machine, or its agent is not running.")
        return
    try:
        msg = common.build_command(cfg["net_key"], "ping")
        with socket.create_connection((ip, port), timeout=6) as s:
            common.send_msg(s, msg)
            r = common.recv_msg(s)
        if r.get("ok"):
            line(OK, f"Signed ping accepted - remote host is {r.get('host')}")
            print("         Keys match. If discovery still fails, broadcast is blocked.")
        else:
            line(BAD, "Remote agent REJECTED our command: " + str(r.get("error")))
            print("         -> The network keys are DIFFERENT. Copy agent.json +")
            print("            controller.json from this machine to that one.")
    except Exception as e:
        line(BAD, "Signed ping failed", str(e))


def main():
    ap = argparse.ArgumentParser()
    default_cfg = (r"C:\ProgramData\ClassCtl\controller.json" if IS_WINDOWS
                   else "/opt/classctl/controller.json")
    ap.add_argument("--config", default=default_cfg)
    ap.add_argument("--peer", help="IP of another station to test directly")
    args = ap.parse_args()

    print("=" * 62)
    print("  ClassCtl diagnostics")
    print("=" * 62)

    check_identity()
    cfg = check_config(args.config)
    if not cfg:
        return 1
    if check_agent(cfg):
        check_firewall(cfg)
        check_discovery(cfg)
    if args.peer:
        check_peer(cfg, args.peer)

    print("\n" + "=" * 62)
    print("  Compare the KEY FINGERPRINT on both machines first.")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
