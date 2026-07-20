"""
classctl.controller  —  the console. Runs from any station.

Flow:  sign in  ->  discover stations in the same group (name prefix)
       ->  pick an action (each file in the scripts folder is an action)
       ->  confirm target count  ->  run  ->  see which stations failed.

Run:  python controller.py --config controller.json
"""

import argparse
import base64
import json
import os
import shutil
import socket
import sys
import subprocess
import threading
import time

import common
import i18n
from i18n import t as _
import ui
from ui import (CANVAS, SURFACE, INK, MUTED, LINE, SIGNAL, ALERT, OK, AMBER,
                FIELD, UI, MONO, RButton)

DISCOVER_TIMEOUT = 2.5
CMD_TIMEOUT = 6.0
SCAN_TIMEOUT = 0.35     # per-host TCP probe when scanning the subnet
SCAN_WORKERS = 128


def local_ipv4s() -> list[str]:
    """IPv4 addresses of this machine, excluding loopback."""
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))          # no traffic sent, just picks the route
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                ips.add(ip)
    except Exception:
        pass
    return sorted(ips)


def scan_subnet(cfg: dict, on_progress=None, rejected: list | None = None) -> list[dict]:
    """
    Probe every host on our /24 for a listening agent, then ask it who it is.
    Used when UDP broadcast is filtered by the switch (common with VLANs).
    """
    port = cfg.get("tcp_port", common.DEFAULT_TCP_PORT)
    my_prefix = common.classroom_prefix()
    my_host = common.short_hostname()

    candidates = []
    for ip in local_ipv4s():
        base = ip.rsplit(".", 1)[0]
        candidates += [f"{base}.{i}" for i in range(1, 255)]
    candidates = [c for c in dict.fromkeys(candidates)]

    found: dict[str, dict] = {}
    lock = threading.Lock()
    done = {"n": 0}

    def probe(ip):
        try:
            with socket.create_connection((ip, port), timeout=SCAN_TIMEOUT):
                pass
        except Exception:
            with lock:
                done["n"] += 1
                if on_progress:
                    on_progress(done["n"], len(candidates))
            return
        res = send_command(cfg, ip, "ping")
        with lock:
            done["n"] += 1
            if on_progress:
                on_progress(done["n"], len(candidates))
            if res.get("ok"):
                host = common.short_hostname(res.get("host", ""))
                if (host and host != my_host
                        and common.classroom_prefix(host) == my_prefix):
                    found[host] = {"host": host, "ip": ip}
            elif rejected is not None and "host" in res:
                # An agent answered but refused the command: it is running and
                # reachable, its network key simply does not match ours.
                rejected.append({"ip": ip, "error": res.get("error", "")})

    threads = []
    for ip in candidates:
        while threading.active_count() > SCAN_WORKERS:
            time.sleep(0.01)
        t = threading.Thread(target=probe, args=(ip,), daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join(timeout=SCAN_TIMEOUT + CMD_TIMEOUT + 1)

    return sorted(found.values(), key=lambda x: common.natural_key(x["host"]))

# ---------- networking ----------
def discover_agents(cfg: dict) -> list[dict]:
    """UDP broadcast, collect replies, keep only the same group (same name prefix)."""
    my_prefix = common.classroom_prefix()
    my_host = common.short_hostname()
    port = cfg.get("udp_port", common.DEFAULT_UDP_PORT)

    core = {"magic": common.DISCOVERY_MAGIC, "ts": time.time(),
            "nonce": os.urandom(8).hex()}
    packet = dict(core)
    packet["sig"] = common.sign(cfg["net_key"], core)
    data = json.dumps(packet).encode("utf-8")

    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    udp.settimeout(0.4)
    try:
        udp.sendto(data, ("255.255.255.255", port))
    except OSError:
        pass

    found: dict[str, dict] = {}
    end = time.time() + DISCOVER_TIMEOUT
    while time.time() < end:
        try:
            reply, addr = udp.recvfrom(2048)
        except socket.timeout:
            continue
        except OSError:
            break
        try:
            r = json.loads(reply.decode("utf-8"))
        except Exception:
            continue
        host = common.short_hostname(r.get("host", ""))
        if not host:
            continue
        if common.classroom_prefix(host) != my_prefix:
            continue
        if host == my_host:
            continue
        found[host] = {"host": host, "ip": addr[0]}
    udp.close()
    return sorted(found.values(), key=lambda x: common.natural_key(x["host"]))


def send_command(cfg: dict, ip: str, cmd: str, args: dict | None = None) -> dict:
    msg = common.build_command(cfg["net_key"], cmd, args)
    try:
        with socket.create_connection((ip, cfg.get("tcp_port", common.DEFAULT_TCP_PORT)),
                                      timeout=CMD_TIMEOUT) as s:
            s.settimeout(CMD_TIMEOUT)
            common.send_msg(s, msg)
            return common.recv_msg(s)
    except Exception as e:
        return {"ok": False, "error": str(e)}


def broadcast_command(cfg: dict, targets: list[dict], cmd: str,
                      args: dict | None, on_result) -> None:
    def worker(t):
        on_result(t["host"], send_command(cfg, t["ip"], cmd, args))
    threads = [threading.Thread(target=worker, args=(t,), daemon=True) for t in targets]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=CMD_TIMEOUT + 2)


def list_local_scripts(scripts_dir: str, include_hidden: bool = False) -> list[str]:
    """Files in the scripts folder. A leading underscore marks a one-off push
    (like Open an app) rather than an action button."""
    if not os.path.isdir(scripts_dir):
        return []
    return sorted((f for f in os.listdir(scripts_dir)
                   if os.path.isfile(os.path.join(scripts_dir, f))
                   and (include_hidden or not f.startswith("_"))),
                  key=str.lower)


def action_title(filename: str) -> str:
    """
    close-windows.ps1 -> 'Close windows' -> translated when one exists.
    Scripts you add yourself keep their own name.
    """
    stem = os.path.splitext(os.path.basename(filename))[0]

    # scripts that open an application read better as "Open Word"
    if stem.lower().startswith("open-") and len(stem) > 5:
        app = stem[5:].replace("_", " ").replace("-", " ").strip()
        app = " ".join(w[:1].upper() + w[1:] for w in app.split())
        return _("Open {app}", app=app)

    words = stem.replace("_", " ").replace("-", " ").strip()
    label = (words[:1].upper() + words[1:]) if words else filename
    return _(label)


# ---------- widgets ----------
def _round_pts(x1, y1, x2, y2, r):
    return [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
            x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]


class StationTile:
    """A station in the room map: number (or INS) in a mono face, colour = status."""

    SIZE = 62

    def __init__(self, parent, label, bg=SURFACE, command=None):
        import tkinter as tk
        s = self.SIZE
        self.c = tk.Canvas(parent, width=s, height=s, highlightthickness=0, bd=0, bg=bg)
        pts = ui.round_pts(2, 2, s - 2, s - 2, 12)
        self.shape = self.c.create_polygon(pts, smooth=True, splinesteps=20,
                                           fill=SURFACE, outline=LINE)
        self.text = self.c.create_text(s // 2, s // 2, text=label,
                                       fill=INK, font=(MONO, 15, "bold"))
        self.strike = self.c.create_line(14, s // 2, s - 14, s // 2,
                                         fill=MUTED, width=2, state="hidden")
        if command:
            self.c.configure(cursor="hand2")
            self.c.bind("<Button-1>", lambda e: command())

    def status(self, state):
        colors = {"idle": (SURFACE, LINE, INK),
                  "ok": ("#E6F4EF", OK, OK),
                  "fail": ("#FBE9EC", ALERT, ALERT),
                  "skip": ("#E9EDF3", LINE, MUTED)}
        fill, outline, fg = colors[state]
        self.c.itemconfig(self.shape, fill=fill, outline=outline)
        self.c.itemconfig(self.text, fill=fg)
        self.c.itemconfig(self.strike, state=("normal" if state == "skip" else "hidden"))

    def grid(self, **kw):
        self.c.grid(**kw); return self


# ---------- app ----------
def run_gui(cfg_path: str):
    import tkinter as tk
    from tkinter import filedialog

    cfg = common.load_config(cfg_path)
    base_dir = os.path.dirname(os.path.abspath(cfg_path))
    i18n.set_lang(common.read_lang(base_dir))
    state = {"targets": []}

    root = tk.Tk()

    def _on_error(exc, val, tb):
        # pythonw has no console, so an unhandled callback error would vanish
        import traceback
        try:
            ui.error(root, _("Something went wrong"),
                     "".join(traceback.format_exception_only(exc, val)).strip())
        except Exception:
            pass
    tk.Tk.report_callback_exception = staticmethod(_on_error)
    root.report_callback_exception = _on_error

    root.title("ClassCtl")
    root.geometry("760x680")
    root.minsize(620, 480)
    root.overrideredirect(True)
    root.configure(highlightbackground=LINE, highlightthickness=1)
    root.configure(bg=CANVAS)
    try:
        ico = os.path.join(os.path.dirname(os.path.abspath(cfg_path)), "classctl.ico")
        if os.path.exists(ico):
            root.iconbitmap(ico)
    except Exception:
        pass

    # ================= sign in =================
    login = tk.Frame(root, bg=CANVAS)

    lw = tk.Frame(login, bg=SURFACE, highlightbackground=LINE, highlightthickness=1)
    lw.place(relx=0.5, rely=0.45, anchor="center", width=380, height=300)

    tk.Label(lw, text=_("ClassCtl"), bg=SURFACE, fg=INK,
             font=(UI, 22, "bold")).pack(pady=(34, 2))
    tk.Label(lw, text=_("Classroom operations console"), bg=SURFACE, fg=MUTED,
             font=(UI, 9)).pack()
    tk.Label(lw, text=_("GROUP  ") + common.classroom_prefix(), bg=SURFACE, fg=SIGNAL,
             font=(MONO, 10, "bold")).pack(pady=(14, 0))

    pw = tk.Entry(lw, show="\u2022", width=24, justify="center", font=(UI, 12),
                  relief="flat", bg="#F2F6FB", fg=INK, insertbackground=INK)
    pw.pack(pady=(22, 6), ipady=7)
    pw.focus_set()

    show_pw = tk.BooleanVar(value=False)
    ui.Checkbox(lw, "Show password", variable=show_pw, bg=SURFACE, font_size=9,
                fg=MUTED,
                command=lambda: pw.config(show="" if show_pw.get() else "\u2022")
                ).pack()

    def try_login(*_evt):
        if common.verify_password(pw.get(), cfg["password"]):
            login.pack_forget()
            main.pack(fill="both", expand=True)
            refresh()
        else:
            ui.error(root, _("Wrong password"), _("That password does not match. Try again."))
            pw.delete(0, "end")

    RButton(lw, _("Sign in"), try_login, width=200, height=42, bg=SURFACE).pack(pady=16)
    pw.bind("<Return>", try_login)
    login.pack(fill="both", expand=True)

    # ================= main =================
    main = tk.Frame(root, bg=CANVAS)

    header = tk.Frame(main, bg=INK, height=58)
    header.pack(fill="x")
    header.pack_propagate(False)
    tk.Label(header, text="CLASSCTL", bg=INK, fg="#FFFFFF",
             font=(UI, 13, "bold")).pack(side=i18n.side("left"), padx=18)
    # two labels, not one string: mixing Hebrew and a Latin group name in a
    # single label leaves the reading order to chance
    tk.Label(header, text=_("GROUP"), bg=INK, fg="#8FB6E4",
             font=(UI, 10, "bold")).pack(side=i18n.side("left"))
    tk.Label(header, text=common.classroom_prefix(), bg=INK, fg="#8FB6E4",
             font=(MONO, 10, "bold")).pack(side=i18n.side("left"), padx=(6, 0))

    def switch_language():
        new = "he" if i18n.get_lang() == "en" else "en"
        try:
            common.write_lang(base_dir, new)
            ticket = common.issue_resume_ticket(base_dir)
        except Exception as e:
            ui.error(root, _("Could not add"), str(e)); return
        # relaunch: a live tkinter layout cannot be mirrored reliably. The
        # ticket carries the signed-in session across, so the password is not
        # asked for again just because the language changed.
        argv = [a for a in sys.argv if not a.startswith("--resume=")]
        root.destroy()
        os.execv(sys.executable, [sys.executable] + argv + [f"--resume={ticket}"])

    # window buttons first, so they sit at the very edge
    ui.make_chrome(root, header, on_close=root.destroy, min_w=620, min_h=460)

    lang_lbl = tk.Label(header, text=("עברית" if i18n.get_lang() == "en" else "English"),
                        bg=INK, fg="#8FB6E4", font=(UI, 9, "bold"), cursor="hand2")
    lang_lbl.pack(side=i18n.side("right"), padx=18)
    lang_lbl.bind("<Button-1>", lambda e: switch_language())

    body_scroll = ui.ScrollFrame(main, bg=CANVAS)
    body_scroll.pack(fill="both", expand=True, padx=18, pady=14)
    body = body_scroll.inner

    # ---- stations panel ----
    st_head = tk.Frame(body, bg=CANVAS)
    st_head.pack(fill="x")
    stations_lbl = tk.Label(st_head, text=_("STATIONS"), bg=CANVAS, fg=MUTED,
                            font=(UI, 9, "bold"))
    stations_lbl.pack(side=i18n.side("left"))
    count_lbl = tk.Label(st_head, text="", bg=CANVAS, fg=INK, font=(UI, 9, "bold"))
    count_lbl.pack(side=i18n.side("left"), padx=8)

    RButton(st_head, _("Rescan"), lambda: refresh(), kind="ghost",
            width=92, height=30, radius=8, font_size=9, bg=CANVAS).pack(side=i18n.side("right"))
    RButton(st_head, _("Test"), lambda: test_stations(), kind="ghost",
            width=74, height=30, radius=8, font_size=9, bg=CANVAS
            ).pack(side=i18n.side("right"), padx=6)

    grid_card_w = ui.Card(body, bg=CANVAS, pad=14)
    grid_card_w.pack(fill="x", pady=(8, 16))
    grid_card = grid_card_w.inner
    grid_inner = tk.Frame(grid_card, bg=SURFACE)
    grid_inner.pack(anchor=i18n.anchor("w"))
    tiles = {}

    empty_lbl = tk.Label(grid_card, text="", bg=SURFACE, fg=MUTED,
                         font=(UI, 10), justify=i18n.justify("left"), wraplength=660)

    def draw_tiles(targets, statuses=None):
        for w in grid_inner.winfo_children():
            w.destroy()
        tiles.clear()
        for i, t in enumerate(targets):
            tile = StationTile(grid_inner, common.station_label(t["host"]))
            tile.grid(row=i // 9, column=i18n.column(i % 9, 9), padx=4, pady=4)
            tile.status((statuses or {}).get(t["host"], "idle"))
            tiles[t["host"]] = tile

    # ---- actions panel ----
    tk.Label(body, text=_("ACTIONS"), bg=CANVAS, fg=MUTED,
             font=(UI, 9, "bold")).pack(anchor=i18n.anchor("w"))
    act_card_w = ui.Card(body, bg=CANVAS, pad=14)
    act_card_w.pack(fill="both", expand=True, pady=(8, 0))
    actions = act_card_w.inner

    footer = tk.Frame(main, bg=CANVAS)
    footer.pack(fill="x", padx=18, pady=(0, 14))
    RButton(footer, _("Manage scripts"), lambda: manage_scripts(), kind="quiet",
            width=150, height=36, radius=9, font_size=9, bg=CANVAS).pack(side=i18n.side("right"))

    # ---- run pipeline ----
    def execute_on(targets, cmd, args):
        """Blocking send. Only call this from a worker thread."""
        results = {}
        broadcast_command(cfg, targets, cmd, args,
                          lambda h, r: results.__setitem__(h, r))
        return results

    def send_async(title, cmd, args, targets, on_done):
        """
        Send without freezing the window. A station that is switched off costs
        the full timeout, so on the UI thread the app would look hung.
        """
        busy = ui.Window(root, title, 380, 190)
        tk.Label(busy.body, text=_("Sending to {n} stations\u2026", n=len(targets)),
                 bg=CANVAS, fg=INK, font=(UI, 12, "bold")).pack(pady=(26, 6))
        tk.Label(busy.body, text=_("Stations that are switched off take a few seconds."),
                 bg=CANVAS, fg=MUTED, font=(UI, 9)).pack()
        bar = ui.ProgressBar(busy.body, width=300, height=8, bg=CANVAS)
        bar.pack(pady=18)

        state_bar = {"v": 0.0, "stop": False}

        def creep():
            if state_bar["stop"]:
                return
            state_bar["v"] = min(0.92, state_bar["v"] + 0.03)
            try:
                bar.set(state_bar["v"])
                root.after(200, creep)
            except Exception:
                pass
        creep()

        def work():
            results = execute_on(targets, cmd, args)

            def finish():
                state_bar["stop"] = True
                try:
                    bar.set(1.0)
                    busy.destroy()
                except Exception:
                    pass
                on_done(results)
            root.after(0, finish)
        threading.Thread(target=work, daemon=True).start()

    def run_local_script(name):
        path = os.path.join(cfg["scripts_dir"], name)
        if not os.path.exists(path):
            ui.error(root, _("Script missing"),
                     _("'{name}' is not in the scripts folder.", name=name)); return
        try:
            subprocess.Popen(common.interpreter_for(path), cwd=cfg["scripts_dir"])
        except Exception as e:
            ui.error(root, _("Could not run script"), str(e))

    def show_results(title, cmd, args, all_targets, results):
        """Only what matters after sending: did it work, and if not - who."""
        ok_list = [t for t in all_targets if results.get(t["host"], {}).get("ok")]
        fail_list = [t for t in all_targets if not results.get(t["host"], {}).get("ok")]

        draw_tiles(all_targets,
                   {t["host"]: ("ok" if results.get(t["host"], {}).get("ok") else "fail")
                    for t in all_targets})

        accent = ALERT if fail_list else OK
        rows = (len(fail_list) + 5) // 6
        height = 250 + (rows * 74 + 60 if fail_list else 0)
        win = ui.Window(root, title, 470, min(height, 620), accent=accent)

        banner = tk.Frame(win.body, bg=accent, height=96)
        banner.pack(fill="x"); banner.pack_propagate(False)
        headline = (_("All {n} stations done", n=len(all_targets)) if not fail_list
                    else _("{ok} of {total} succeeded", ok=len(ok_list),
                           total=len(all_targets)))
        tk.Label(banner, text=headline, bg=accent, fg="#FFFFFF",
                 font=(UI, 17, "bold")).pack(pady=(30, 0))

        if fail_list:
            tk.Label(win.body, text=_("DID NOT RESPOND"), bg=CANVAS, fg=ALERT,
                     font=(UI, 9, "bold")).pack(anchor=i18n.anchor("w"), padx=24, pady=(18, 8))
            card = tk.Frame(win.body, bg=SURFACE, highlightbackground=LINE,
                            highlightthickness=1)
            card.pack(fill="both", expand=True, padx=24)
            wrap = tk.Frame(card, bg=SURFACE); wrap.pack(padx=12, pady=12, anchor=i18n.anchor("w"))
            for i, t in enumerate(fail_list):
                tile = StationTile(wrap, common.station_label(t["host"]), bg=SURFACE)
                tile.grid(row=i // 6, column=i18n.column(i % 6, 6), padx=3, pady=3)
                tile.status("fail")

        row = tk.Frame(win.body, bg=CANVAS); row.pack(pady=16)
        RButton(row, _("Close"), win.destroy, kind="quiet",
                width=96, height=42, bg=CANVAS).pack(side=i18n.side("left"), padx=5)
        if fail_list:
            def retry():
                win.destroy()

                def after(res2):
                    results.update(res2)
                    show_results(title, cmd, args, all_targets, results)
                send_async(f"Retrying {len(fail_list)}", cmd, args, fail_list, after)
            RButton(row, _("Retry {n}", n=len(fail_list)), retry, kind="amber",
                    width=118, height=42, bg=CANVAS).pack(side=i18n.side("left"), padx=5)
        self_name = (args or {}).get("name")
        if self_name:
            def run_on_self():
                if ui.confirm(win, _("Run here too"),
                              _("Run '{name}' on this PC now?", name=action_title(self_name)),
                              ok_text=_("Run here")):
                    run_local_script(self_name); win.destroy()
            RButton(row, _("Run on this PC"), run_on_self, kind="primary",
                    width=148, height=42, bg=CANVAS).pack(side=i18n.side("left"), padx=5)


    def confirm_and_run(title, cmd, args=None):
        targets = list(state["targets"])
        if not targets:
            ui.info(root, _("No stations"),
                    _("No stations answered the last scan. Press Rescan first.")); return

        win = ui.Window(root, title, 470, 500)
        skipped = set()
        tiles = {}

        big = tk.Label(win.body, text=str(len(targets)), bg=CANVAS, fg=INK,
                       font=(MONO, 46, "bold"))
        big.pack(pady=(18, 0))
        sub = tk.Label(win.body, text=_("stations will run this"), bg=CANVAS, fg=MUTED,
                       font=(UI, 10))
        sub.pack()

        holder = tk.Frame(win.body, bg=CANVAS)
        holder.pack(fill="both", expand=True, padx=20, pady=(10, 0))
        hint = tk.Label(win.body, text="", bg=CANVAS, fg=MUTED, font=(UI, 9))
        shown = {"v": False}

        def refresh_count():
            n = len(targets) - len(skipped)
            big.config(text=str(n))
            sub.config(text=_("stations will run this") if n != 1
                       else "station will run this")

        def toggle_station(host):
            if host in skipped:
                skipped.discard(host); tiles[host].status("idle")
            else:
                skipped.add(host); tiles[host].status("skip")
            refresh_count()

        def draw_grid():
            for w in holder.winfo_children():
                w.destroy()
            tiles.clear()
            for i, t in enumerate(targets):
                h = t["host"]
                tile = StationTile(holder, common.station_label(h), bg=CANVAS,
                                   command=lambda hh=h: toggle_station(hh))
                tile.grid(row=i // 6, column=i18n.column(i % 6, 6), padx=3, pady=3)
                tile.status("skip" if h in skipped else "idle")
                tiles[h] = tile

        def toggle_view():
            if shown["v"]:
                for w in holder.winfo_children():
                    w.destroy()
                tiles.clear()
                hint.pack_forget()
                shown["v"] = False
                tog.set_text("Choose stations")
            else:
                draw_grid()
                hint.config(text=_("Tap a station to leave it out of this action."))
                hint.pack(pady=(6, 0))
                shown["v"] = True
                tog.set_text("Hide stations")

        tog = RButton(win.body, _("Choose stations"), toggle_view, kind="ghost",
                      width=150, height=32, radius=8, font_size=9, bg=CANVAS)
        tog.pack(pady=(8, 0))

        row = tk.Frame(win.body, bg=CANVAS); row.pack(pady=14)

        def go():
            chosen = [t for t in targets if t["host"] not in skipped]
            if not chosen:
                ui.info(win, _("Nothing selected"),
                        _("Every station is set to be skipped.")); return
            win.destroy()
            send_async(title, cmd, args, chosen,
                       lambda results: show_results(title, cmd, args, chosen, results))

        RButton(row, _("Cancel"), win.destroy, kind="quiet",
                width=110, height=44, bg=CANVAS).pack(side=i18n.side("left"), padx=6)
        RButton(row, _("Run now"), go, kind="primary",
                width=150, height=44, bg=CANVAS).pack(side=i18n.side("left"), padx=6)

    def test_stations():
        """Harmless check before class: who answers, who does not."""
        targets = list(state["targets"])
        if not targets:
            ui.info(root, _("No stations"),
                    _("No stations answered the last scan. Press Rescan first.")); return
        count_lbl.config(text="testing\u2026")
        root.update_idletasks()

        def done(results):
            count_lbl.config(text=_("{n} online", n=len(targets)))
            show_results(_("Station test"), "ping", None, targets, results)
        send_async(_("Station test"), "ping", None, targets, done)

    def run_script_on_class(name):
        path = os.path.join(cfg["scripts_dir"], name)
        try:
            content_b64 = common.encode_file(path)
        except Exception as e:
            ui.error(root, _("Could not read script"), str(e)); return
        confirm_and_run(action_title(name), "run_script",
                        {"name": name, "content_b64": content_b64})

    def build_action_buttons():
        for w in actions.winfo_children():
            w.destroy()
        scripts = list_local_scripts(cfg["scripts_dir"])
        if not scripts:
            tk.Label(actions,
                     text="No actions yet.\nAdd a script file and it becomes a button.",
                     bg=SURFACE, fg=MUTED, font=(UI, 10), justify=i18n.justify("left")
                     ).pack(anchor=i18n.anchor("w"), pady=20)
            return
        for i, name in enumerate(scripts):
            kind = "danger" if os.path.splitext(name)[0].lower() in (
                "shutdown", "restart") else "quiet"
            RButton(actions, action_title(name),
                    (lambda n=name: run_script_on_class(n)),
                    kind=kind, width=330, height=46, bg=SURFACE
                    ).grid(row=i // 2, column=i18n.column(i % 2, 2), padx=5, pady=5)

    # ---- open an application on every station ----
    COMMON_APPS = [
        ("Word", "winword.exe"),
        ("Excel", "excel.exe"),
        ("PowerPoint", "powerpnt.exe"),
        ("Notepad", "notepad.exe"),
        ("Calculator", "calc.exe"),
        ("File Explorer", "explorer.exe"),
        ("Edge", "msedge.exe"),
        ("Chrome", "chrome.exe"),
    ]

    def open_app():
        """Pick an application and start it on every station, in the user's session."""
        win = ui.Window(root, _("Open an app on every station"), 470, 520)

        tk.Label(win.body, text=_("PICK AN APP"), bg=CANVAS, fg=MUTED,
                 font=(UI, 9, "bold")).pack(anchor=i18n.anchor("w"), padx=24, pady=(18, 8))

        card = tk.Frame(win.body, bg=SURFACE, highlightbackground=LINE,
                        highlightthickness=1)
        card.pack(fill="x", padx=24)
        lb = tk.Listbox(card, bd=0, highlightthickness=0, bg=SURFACE, fg=INK,
                        font=(UI, 10), selectbackground="#DCE8F7",
                        selectforeground=INK, activestyle="none", height=8)
        lb.pack(fill="x", padx=10, pady=10)
        for label, _cmd in COMMON_APPS:
            lb.insert("end", label)

        tk.Label(win.body, text=_("OR TYPE A COMMAND / PATH"), bg=CANVAS, fg=MUTED,
                 font=(UI, 9, "bold")).pack(anchor=i18n.anchor("w"), padx=24, pady=(16, 6))
        row = tk.Frame(win.body, bg=CANVAS); row.pack(fill="x", padx=24)
        custom = tk.Entry(row, font=(MONO, 9), relief="flat", bg=ui.FIELD, fg=INK,
                          insertbackground=INK)
        custom.pack(side=i18n.side("left"), fill="x", expand=True, ipady=6)

        def browse():
            f = filedialog.askopenfilename(
                parent=win, title="Choose a program",
                initialdir=("C:\\Program Files" if os.name == "nt" else "/usr/bin"),
                filetypes=[("Programs", "*.exe *.bat *.cmd *.lnk"),
                           ("All files", "*.*")])
            win.after(60, win.focus_force)
            if f:
                custom.delete(0, "end"); custom.insert(0, f)
        RButton(row, _("Browse"), browse, kind="quiet", width=88, height=30,
                radius=8, font_size=9, bg=CANVAS).pack(side=i18n.side("left"), padx=8)

        tk.Label(win.body, text=_("It opens on the station screens, not here."),
                 bg=CANVAS, fg=MUTED, font=(UI, 9)).pack(anchor=i18n.anchor("w"), padx=24,
                                                          pady=(8, 0))

        def go():
            target = custom.get().strip()
            name = target
            if not target:
                sel = lb.curselection()
                if not sel:
                    ui.info(win, _("Pick an app"),
                            _("Choose one from the list, or type a command.")); return
                name, target = COMMON_APPS[sel[0]]

            # Saved as an ordinary script, so it becomes a button like any other
            # and can be edited or deleted from this same window.
            stem = os.path.splitext(os.path.basename(name))[0]
            stem = "".join(c if (c.isalnum() or c in "-_") else "-"
                           for c in stem).strip("-").lower() or "app"
            fname = f"open-{stem}" + (".ps1" if os.name == "nt" else ".sh")
            path = os.path.join(cfg["scripts_dir"], fname)
            script = common.open_app_script(target, is_windows=(os.name == "nt"))
            try:
                enc = "utf-8-sig" if fname.endswith(".ps1") else "utf-8"
                nl = "\r\n" if fname.endswith(".ps1") else "\n"
                with open(path, "w", encoding=enc, newline=nl) as f:
                    f.write(script)
                if os.name != "nt":
                    os.chmod(path, 0o755)
            except Exception as e:
                ui.error(win, _("Could not add script"), str(e)); return
            win.destroy()
            build_action_buttons()
            ui.success(root, _("Action added"),
                       _("'{name}' is now a button. Press it to open the app on "
                         "every station.", name=action_title(fname)))

        brow = tk.Frame(win.body, bg=CANVAS); brow.pack(pady=18)
        RButton(brow, _("Cancel"), win.destroy, kind="quiet",
                width=110, height=42, bg=CANVAS).pack(side=i18n.side("left"), padx=6)
        RButton(brow, _("Open on all"), go, kind="primary",
                width=150, height=42, bg=CANVAS).pack(side=i18n.side("left"), padx=6)

    # ---- scripts manager (folder is ACL-protected; Explorer cannot open it) ----
    def manage_scripts():
        win = ui.Window(root, _("Manage scripts"), 460, 470)

        tk.Label(win.body, text=_("Each file here is one action button."),
                 bg=CANVAS, fg=INK, font=(UI, 10)).pack(pady=(16, 10))

        card = tk.Frame(win.body, bg=SURFACE, highlightbackground=LINE,
                        highlightthickness=1)
        card.pack(fill="both", expand=True, padx=18)
        lb = tk.Listbox(card, bd=0, highlightthickness=0, bg=SURFACE, fg=INK,
                        font=(UI, 10), selectbackground="#DCE8F7", selectforeground=INK,
                        activestyle="none")
        lb.pack(fill="both", expand=True, padx=10, pady=10)

        def reload_list():
            lb.delete(0, "end")
            for f in list_local_scripts(cfg["scripts_dir"]):
                lb.insert("end", f)
        reload_list()

        def picked():
            sel = lb.curselection()
            if not sel:
                ui.info(win, _("Pick a script"), _("Select a script from the list first."))
                return None
            return lb.get(sel[0])

        def add_script():
            src = filedialog.askopenfilename(
                parent=win, title="Add a script",
                initialdir=os.path.expanduser("~"),
                filetypes=[("Scripts", "*.ps1 *.bat *.cmd *.py *.sh *.exe"),
                           ("All files", "*.*")])
            win.after(60, win.focus_force)
            if not src:
                return
            try:
                shutil.copy2(src, os.path.join(cfg["scripts_dir"],
                                               os.path.basename(src)))
                reload_list(); build_action_buttons()
            except Exception as e:
                ui.error(win, _("Could not add script"), str(e))

        def delete_script():
            name = picked()
            if not name:
                return
            if not ui.confirm(win, _("Delete script"),
                              _("Delete '{name}'?\nIts action button disappears.", name=name),
                              ok_text=_("Delete"), danger=True):
                return
            try:
                os.remove(os.path.join(cfg["scripts_dir"], name))
                reload_list(); build_action_buttons()
            except Exception as e:
                ui.error(win, _("Could not delete script"), str(e))

        def edit_script():
            name = picked()
            if not name:
                return
            path = os.path.join(cfg["scripts_dir"], name)
            try:
                subprocess.Popen(["notepad", path] if os.name == "nt"
                                 else ["xdg-open", path])
            except Exception as e:
                ui.error(win, _("Could not open editor"), str(e))

        tk.Label(win.body,
                 text=_("An app can become an action too: pick one and it is "
                        "saved here as a script."),
                 bg=CANVAS, fg=MUTED, font=(UI, 9), wraplength=400,
                 justify=i18n.justify("left")).pack(anchor=i18n.anchor("w"),
                                                    padx=24, pady=(10, 0))

        row = tk.Frame(win.body, bg=CANVAS); row.pack(pady=(12, 4))
        RButton(row, _("Close"), win.destroy, kind="quiet",
                width=86, height=40, bg=CANVAS).pack(side=i18n.side("left"), padx=4)
        RButton(row, _("Edit"), edit_script, kind="ghost",
                width=86, height=40, bg=CANVAS).pack(side=i18n.side("left"), padx=4)
        RButton(row, _("Delete"), delete_script, kind="danger",
                width=96, height=40, bg=CANVAS).pack(side=i18n.side("left"), padx=4)

        row2 = tk.Frame(win.body, bg=CANVAS); row2.pack(pady=(0, 16))
        RButton(row2, _("Add an app"), (lambda: (win.destroy(), open_app())),
                kind="quiet", width=130, height=40,
                bg=CANVAS).pack(side=i18n.side("left"), padx=4)
        RButton(row2, _("Add script"), add_script, kind="primary",
                width=120, height=40, bg=CANVAS).pack(side=i18n.side("left"), padx=4)

    # ---- discovery ----
    def refresh():
        count_lbl.config(text="scanning\u2026")
        root.update_idletasks()

        def work():
            targets = discover_agents(cfg)          # fast path: UDP broadcast

            if not targets:
                # broadcast is often filtered on managed switches / VLANs.
                def prog(done, total):
                    if done % 25 == 0 or done == total:
                        root.after(0, lambda: count_lbl.config(
                            text=_("deep scan {pct}%", pct=int(done * 100 / total))))
                root.after(0, lambda: count_lbl.config(text="deep scan\u2026"))
                targets = scan_subnet(cfg, on_progress=prog)

            state["targets"] = targets

            def apply():
                empty_lbl.pack_forget()
                draw_tiles(targets)
                count_lbl.config(text=_("{n} online", n=len(targets)))
                if not targets:
                    my_host = common.short_hostname()
                    if "-" not in my_host:
                        msg = (f"This PC is named '{my_host}', which has no group prefix. "
                               f"Rename it as <group>-<number>, for example 101-12 or "
                               f"101-INS, then restart.")
                    else:
                        msg = ("No other stations answered, by broadcast or by scanning "
                               "this subnet. Check that the agent runs on them, that "
                               "their names start with the same prefix, and that TCP "
                               "48720 / UDP 48719 are allowed. If the stations sit on "
                               "another subnet, run diagnose.py --peer <ip> from here.")
                    empty_lbl.config(text=msg)
                    empty_lbl.pack(pady=(6, 0), anchor=i18n.anchor("w"))
                build_action_buttons()
            root.after(0, apply)
        threading.Thread(target=work, daemon=True).start()

    main.pack_forget()

    resumed = False
    for a in sys.argv:
        if a.startswith("--resume="):
            resumed = common.redeem_resume_ticket(base_dir, a.split("=", 1)[1])
            break
    if resumed:
        login.pack_forget()
        main.pack(fill="both", expand=True)
        root.after(80, refresh)

    root.mainloop()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="controller.json")
    ap.add_argument("--resume", default=None,
                    help="internal: carry the session across a language switch")
    args = ap.parse_args()
    run_gui(args.config)


if __name__ == "__main__":
    main()
