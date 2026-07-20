import os, socket, json, time, threading, tempfile, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common, agent

def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    assert cond, name

# 1) classroom prefix
cases = {"LAB1-12":"LAB1","LAB2-05-INS":"LAB2-05","LAB2-05-07":"LAB2-05","LAB4-ins":"LAB4","LAB3-08":"LAB3","LAB5-1":"LAB5","LAB6-DAN":"LAB6"}
for h,exp in cases.items():
    check(f"prefix {h}->{exp}", common.classroom_prefix(h)==exp)

# 2) password hash/verify
rec = common.hash_password("test-password-not-real")
check("pw correct", common.verify_password("test-password-not-real", rec))
check("pw wrong",  not common.verify_password("nope", rec))

# 3) sign/verify + check_command
key = common.gen_net_key()
msg = common.build_command(key, "run_script", {"name":"x"})
core = {k:msg[k] for k in ("v","cmd","args","ts","nonce")}
check("sig ok", common.verify_sig(key, core, msg["sig"]))
tampered = dict(msg); tampered["cmd"]="shutdown"
seen=set()
ok,_why = common.check_command(key, tampered, seen); check("tamper rejected", not ok)
ok,_why = common.check_command(key, msg, seen);       check("valid accepted", ok)
ok,r = common.check_command(key, msg, seen);       check("replay rejected", not ok and r=="replay")
stale = common.build_command(key,"ping"); stale["ts"]=time.time()-9999
stale["sig"]=common.sign(key,{k:stale[k] for k in ("v","cmd","args","ts","nonce")})
ok,r = common.check_command(key, stale, set()); check("stale rejected", not ok)

# 4) end-to-end: real agent on localhost runs a script
base = tempfile.mkdtemp()
scripts = os.path.join(base,"scripts"); os.makedirs(scripts)
marker = os.path.join(base,"ran.txt")
cfg = {"net_key":key,"scripts_dir":scripts,"tcp_port":48991,"udp_port":48992}

stop = threading.Event()
threading.Thread(target=agent.discovery_listener, args=(cfg,stop), daemon=True).start()
srv = agent.ThreadedTCP(("",cfg["tcp_port"]), agent.CommandHandler, cfg)
threading.Thread(target=srv.serve_forever, daemon=True).start()
time.sleep(0.4)

# 4a) discovery responder answers a signed packet
udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); udp.settimeout(2)
core = {"magic":common.DISCOVERY_MAGIC,"ts":time.time(),"nonce":os.urandom(8).hex()}
pkt = dict(core); pkt["sig"]=common.sign(key, core)
udp.sendto(json.dumps(pkt).encode(), ("127.0.0.1", cfg["udp_port"]))
data,_addr = udp.recvfrom(2048); rep=json.loads(data.decode())
check("discovery reply has host", "host" in rep and "prefix" in rep)

# 4b) run_script with inline content -> agent writes + executes it.
# The script has to match the host: interpreter_for() runs a .sh only on Linux,
# and on Windows a .sh falls through to "execute the file itself", which the OS
# refuses with WinError 193.
if common.IS_WINDOWS:
    script_name = "marker.ps1"
    # BOM + CRLF, or Windows PowerShell mangles the file
    script_body = "﻿'ran' | Set-Content -LiteralPath '%s'\r\n" % marker
else:
    script_name = "marker.sh"
    script_body = "#!/bin/bash\necho ran > '%s'\n" % marker
import base64
b64 = base64.b64encode(script_body.encode()).decode()
cmd = common.build_command(key, "run_script", {"name":script_name,"content_b64":b64})
with socket.create_connection(("127.0.0.1", cfg["tcp_port"]), timeout=3) as s:
    common.send_msg(s, cmd); resp = common.recv_msg(s)
check("run_script ok reply", resp.get("ok") is True)
check("script file synced to folder", os.path.exists(os.path.join(scripts,script_name)))
time.sleep(0.6)
check("script actually executed (marker created)", os.path.exists(marker))

# 4c) bad key is rejected
badcmd = common.build_command(common.gen_net_key(), "run_script", {"name":script_name})
with socket.create_connection(("127.0.0.1", cfg["tcp_port"]), timeout=3) as s:
    common.send_msg(s, badcmd); resp = common.recv_msg(s)
check("wrong key rejected", resp.get("ok") is False)

stop.set(); srv.shutdown()

# 5) static check: in any module that imports the translator, `_` is that
# translator and nothing else. Using it as a throwaway silently shadows it and
# breaks every later _("...") call in the file — this has bitten twice.
import ast

def underscore_rebinds(src: str) -> list[int]:
    lines = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            targets = [node.target]
        elif isinstance(node, ast.withitem):
            targets = [node.optional_vars] if node.optional_vars else []
        else:
            continue
        for target in targets:
            for sub in ast.walk(target):
                if (isinstance(sub, ast.Name) and sub.id == "_"
                        and isinstance(sub.ctx, ast.Store)):
                    # the Name carries the line; ast.comprehension does not
                    lines.append(sub.lineno)
    return sorted(set(lines))

here = os.path.dirname(os.path.abspath(__file__))
self_name = os.path.basename(__file__)   # this file quotes the import it looks for
for fname in sorted(f for f in os.listdir(here) if f.endswith(".py")):
    if fname == self_name:
        continue
    src = open(os.path.join(here, fname), encoding="utf-8").read()
    if "from i18n import t as _" not in src:
        continue
    hits = underscore_rebinds(src)
    check(f"{fname}: '_' stays the translator" +
          (f" (rebound on line {hits})" if hits else ""), not hits)

print("\nALL TESTS PASSED")
