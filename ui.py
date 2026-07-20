"""
classctl.ui  —  the shared look. No native Windows dialogs anywhere:
every window in ClassCtl is drawn by this module, including its title bar.
"""

import os
import tkinter as tk

import i18n
from i18n import t as _

# ---------- tokens ----------
CANVAS   = "#EDF1F6"
SURFACE  = "#FFFFFF"
INK      = "#10233A"
MUTED    = "#5C7089"
LINE     = "#C9D4E2"
SIGNAL   = "#0B6BCB"
SIGNAL_D = "#095AAC"
ALERT    = "#C2334D"
ALERT_D  = "#A62A41"
OK       = "#17795E"
AMBER    = "#B26A00"
AMBER_D  = "#8F5500"
FIELD    = "#F2F6FB"

UI   = "Segoe UI"
MONO = "Consolas"


def round_pts(x1, y1, x2, y2, r):
    return [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
            x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]


class RButton:
    """Rounded button. tkinter has no native one, so it is drawn on a canvas."""

    STYLES = {
        "primary": (SIGNAL, SIGNAL_D, "#FFFFFF", None),
        "danger":  (ALERT, ALERT_D, "#FFFFFF", None),
        "amber":   (AMBER, AMBER_D, "#FFFFFF", None),
        "quiet":   (SURFACE, FIELD, INK, LINE),
        "ghost":   (CANVAS, "#E2E9F2", INK, LINE),
    }

    def __init__(self, parent, text, command, kind="primary",
                 width=220, height=44, radius=10, font_size=10, bg=None):
        self.fill, self.hover, self.fg, self.border = self.STYLES[kind]
        self.command = command
        self.c = tk.Canvas(parent, width=width, height=height,
                           highlightthickness=0, bd=0, bg=bg or parent.cget("bg"))
        self.shape = self.c.create_polygon(
            round_pts(1, 1, width - 1, height - 1, radius),
            smooth=True, splinesteps=24,
            fill=self.fill, outline=self.border or self.fill)
        self.label = self.c.create_text(width // 2, height // 2, text=text,
                                        fill=self.fg, font=(UI, font_size, "bold"))
        self.c.bind("<Enter>", lambda e: self.c.itemconfig(self.shape, fill=self.hover))
        self.c.bind("<Leave>", lambda e: self.c.itemconfig(self.shape, fill=self.fill))
        self.c.bind("<Button-1>", lambda e: self.command())
        self.c.configure(cursor="hand2")

    def set_text(self, text):
        self.c.itemconfig(self.label, text=text)

    def pack(self, **kw):
        self.c.pack(**kw); return self

    def grid(self, **kw):
        self.c.grid(**kw); return self


class Window(tk.Toplevel):
    """
    A ClassCtl window: our own title bar, no Windows chrome.
    Drag by the header, close with the x on the right.
    """

    def __init__(self, parent, title, width, height, accent=INK):
        super().__init__(parent, bg=CANVAS)
        self.overrideredirect(True)
        self.configure(highlightbackground=LINE, highlightthickness=1)

        px = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
        py = parent.winfo_rooty() + (parent.winfo_height() - height) // 3
        # keep it on screen even if the parent sits near an edge
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        px = max(0, min(px, sw - width))
        py = max(0, min(py, sh - height))
        self.geometry(f"{width}x{height}+{px}+{py}")

        bar = tk.Frame(self, bg=accent, height=44)
        bar.pack(fill="x"); bar.pack_propagate(False)
        tk.Label(bar, text=title, bg=accent, fg="#FFFFFF",
                 font=(UI, 10, "bold")).pack(side=i18n.side("left"), padx=16)
        close = tk.Label(bar, text="\u2715", bg=accent, fg="#FFFFFF",
                         font=(UI, 11), cursor="hand2")
        close.pack(side=i18n.side("right"), padx=14)
        close.bind("<Button-1>", lambda e: self._on_close())

        # drag by the header
        self._drag = {"x": 0, "y": 0}
        for w in (bar,) + tuple(bar.winfo_children()):
            if w is close:
                continue
            w.bind("<Button-1>", self._start_drag)
            w.bind("<B1-Motion>", self._do_drag)

        self.body = tk.Frame(self, bg=CANVAS)
        self.body.pack(fill="both", expand=True)

        self.result = None
        self.transient(parent)

        # Deliberately no grab_set(): a borderless window can fail to come to the
        # front on Windows, and a modal grab would then lock the whole app with
        # nothing visible to click. Staying on top of the parent is enough.
        self.lift()
        self.attributes("-topmost", True)
        self.after(400, self._drop_topmost)
        try:
            self.focus_force()
        except Exception:
            pass
        self.bind("<Escape>", lambda e: self._on_close())
        self.after(50, lambda: round_window_corners(self))

    def _settle_stack(self):
        """
        Stay above the window that opened this one. A borderless window has no
        taskbar entry, so if the parent is clicked and rises above it, there is
        no way left to bring it back.
        """
        try:
            self.attributes("-topmost", False)
            self.lift()
            parent = self.master
            if parent and parent.winfo_exists():
                self._keeper = lambda e=None: (self.winfo_exists() and self.lift())
                parent.bind("<FocusIn>", self._keeper, add="+")
                parent.bind("<Button-1>", self._keeper, add="+")
        except Exception:
            pass

    def _drop_topmost(self):
        self._settle_stack()

    def _start_drag(self, e):
        self._drag = {"x": e.x_root - self.winfo_x(), "y": e.y_root - self.winfo_y()}

    def _do_drag(self, e):
        self.geometry(f"+{e.x_root - self._drag['x']}+{e.y_root - self._drag['y']}")

    def _on_close(self):
        self.destroy()

    def destroy(self):
        """
        Hand focus back to the window underneath. Without this a borderless
        parent keeps no focus after a dialog closes, and typing goes nowhere -
        the app looks stuck.
        """
        parent = self.master
        try:
            if parent and parent.winfo_exists() and getattr(self, "_keeper", None):
                parent.unbind("<FocusIn>")
                parent.unbind("<Button-1>")
        except Exception:
            pass
        try:
            super().destroy()
        finally:
            try:
                if parent and parent.winfo_exists():
                    parent.lift()
                    parent.focus_force()
            except Exception:
                pass

    def wait(self):
        self.wait_window()
        return self.result


def _message(parent, title, message, accent, ok_text=_("OK"),
             cancel_text=None, ok_kind="primary", height=None):
    lines = max(1, len(message) // 46 + message.count("\n") + 1)
    h = height or min(150 + lines * 20, 460)
    win = Window(parent, title, 430, h, accent=accent)

    tk.Label(win.body, text=message, bg=CANVAS, fg=INK, font=(UI, 10),
             wraplength=380, justify=i18n.justify("left")).pack(padx=24, pady=(22, 10),
                                                  anchor=i18n.anchor("w"), expand=True)

    row = tk.Frame(win.body, bg=CANVAS)
    row.pack(pady=(0, 20))

    def done(val):
        win.result = val
        win.destroy()

    if cancel_text:
        RButton(row, cancel_text, lambda: done(False), kind="quiet",
                width=110, height=42, bg=CANVAS).pack(side=i18n.side("left"), padx=6)
    RButton(row, ok_text, lambda: done(True), kind=ok_kind,
            width=140, height=42, bg=CANVAS).pack(side=i18n.side("left"), padx=6)
    win.bind("<Return>", lambda e: done(True))
    win.bind("<Escape>", lambda e: done(False))
    return win.wait()


def info(parent, title, message):
    return _message(parent, title, message, INK)


def error(parent, title, message):
    return _message(parent, title, message, ALERT)


def success(parent, title, message):
    return _message(parent, title, message, OK)


def confirm(parent, title, message, ok_text=_("Confirm"), danger=False):
    return bool(_message(parent, title, message,
                         ALERT if danger else INK,
                         ok_text=ok_text, cancel_text=_("Cancel"),
                         ok_kind="danger" if danger else "primary"))


def round_window_corners(win):
    """Windows 11 rounds normal windows, but not borderless ones. Ask DWM to."""
    try:
        import ctypes
        from ctypes import wintypes
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()
        DWMWA_WINDOW_CORNER_PREFERENCE = 33
        DWMWCP_ROUND = 2
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(hwnd),
            ctypes.c_uint(DWMWA_WINDOW_CORNER_PREFERENCE),
            ctypes.byref(ctypes.c_int(DWMWCP_ROUND)),
            ctypes.sizeof(ctypes.c_int))
    except Exception:
        pass


class Checkbox:
    """Our own checkbox: rounded, in our colours, no Windows square."""

    def __init__(self, parent, text, variable=None, command=None,
                 bg=None, font_size=10, fg=None):
        import tkinter as tk
        self.var = variable if variable is not None else tk.BooleanVar(value=False)
        self.command = command
        self.bg = bg or parent.cget("bg")
        box = 19
        self.frame = tk.Frame(parent, bg=self.bg, cursor="hand2")
        self.c = tk.Canvas(self.frame, width=box + 2, height=box + 2,
                           highlightthickness=0, bd=0, bg=self.bg)
        self.c.pack(side=i18n.side("left"))
        self.shape = self.c.create_polygon(round_pts(2, 2, box, box, 6),
                                           smooth=True, splinesteps=16,
                                           fill=SURFACE, outline=LINE)
        self.tick = self.c.create_line(6, 11, 9, 14, 15, 7, fill="#FFFFFF",
                                       width=2, capstyle="round",
                                       joinstyle="round", state="hidden")
        self.label = tk.Label(self.frame, text=text, bg=self.bg,
                              fg=fg or INK, font=(UI, font_size), cursor="hand2")
        self.label.pack(side=i18n.side("left"), padx=8)
        for w in (self.frame, self.c, self.label):
            w.bind("<Button-1>", self._toggle)
        self._draw()

    def _toggle(self, _evt=None):
        self.var.set(not self.var.get())
        self._draw()
        if self.command:
            self.command()

    def _draw(self):
        on = bool(self.var.get())
        self.c.itemconfig(self.shape, fill=(SIGNAL if on else SURFACE),
                          outline=(SIGNAL if on else LINE))
        self.c.itemconfig(self.tick, state=("normal" if on else "hidden"))

    def pack(self, **kw):
        self.frame.pack(**kw); return self

    def grid(self, **kw):
        self.frame.grid(**kw); return self


class Card:
    """
    A white panel with rounded corners. Put your widgets in .inner

    The rounded shape is a canvas placed behind a normal frame, so tkinter's
    ordinary geometry management still sizes the content. Putting the frame
    inside the canvas instead leaves it unmanaged and it collapses to nothing.
    """

    def __init__(self, parent, bg=None, radius=14, pad=16, fill=None):
        import tkinter as tk
        self.radius = radius
        self.bg = bg or parent.cget("bg")
        self.fill = fill or SURFACE

        self.holder = tk.Frame(parent, bg=self.bg)
        self.bgcanvas = tk.Canvas(self.holder, highlightthickness=0, bd=0,
                                  bg=self.bg)
        self.bgcanvas.place(x=0, y=0, relwidth=1, relheight=1)
        self.shape = self.bgcanvas.create_polygon(
            round_pts(1, 1, 10, 10, radius), smooth=True, splinesteps=24,
            fill=self.fill, outline=LINE)

        self.inner = tk.Frame(self.holder, bg=self.fill)
        self.inner.pack(padx=pad, pady=pad, fill="both", expand=True)
        self.holder.bind("<Configure>", self._draw)

    def _draw(self, e):
        self.bgcanvas.coords(
            self.shape, *round_pts(1, 1, e.width - 1, e.height - 1, self.radius))

    def pack(self, **kw):
        self.holder.pack(**kw); return self

    def grid(self, **kw):
        self.holder.grid(**kw); return self


class ScrollFrame:
    """
    A region that scrolls when its contents outgrow the window. Put widgets in
    .inner. Without this, adding twenty scripts or forty stations would simply
    push the buttons off the bottom of a fixed-size window.
    """

    def __init__(self, parent, bg=None):
        import tkinter as tk
        self.bg = bg or parent.cget("bg")
        self.holder = tk.Frame(parent, bg=self.bg)
        self.canvas = tk.Canvas(self.holder, bg=self.bg, highlightthickness=0, bd=0)
        self.vbar = tk.Scrollbar(self.holder, orient="vertical",
                                 command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self._on_scroll)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=self.bg)
        self.item = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._fit_inner)
        self.canvas.bind("<Configure>", self._fit_canvas)
        for w in (self.holder, self.canvas, self.inner):
            w.bind("<Enter>", lambda e: self._bind_wheel(True))
            w.bind("<Leave>", lambda e: self._bind_wheel(False))

    def _on_scroll(self, first, last):
        # only show the scrollbar when there is something to scroll
        if float(first) <= 0.0 and float(last) >= 1.0:
            self.vbar.pack_forget()
        else:
            self.vbar.pack(side="right", fill="y")
        self.vbar.set(first, last)

    def _fit_inner(self, _evt=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _fit_canvas(self, e):
        self.canvas.itemconfigure(self.item, width=e.width)

    def _bind_wheel(self, on):
        try:
            if on:
                self.canvas.bind_all("<MouseWheel>", self._wheel)
                self.canvas.bind_all("<Button-4>", self._wheel)
                self.canvas.bind_all("<Button-5>", self._wheel)
            else:
                self.canvas.unbind_all("<MouseWheel>")
                self.canvas.unbind_all("<Button-4>")
                self.canvas.unbind_all("<Button-5>")
        except Exception:
            pass

    def _wheel(self, e):
        step = 0
        if getattr(e, "num", None) == 4:
            step = -1
        elif getattr(e, "num", None) == 5:
            step = 1
        elif getattr(e, "delta", 0):
            step = -1 if e.delta > 0 else 1
        if step:
            self.canvas.yview_scroll(step, "units")

    def pack(self, **kw):
        self.holder.pack(**kw); return self


def work_area(win):
    """The screen minus the taskbar, so 'maximised' does not hide behind it."""
    try:
        import ctypes
        from ctypes import wintypes
        rect = wintypes.RECT()
        ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)
        w, h = rect.right - rect.left, rect.bottom - rect.top
        if w > 200 and h > 200:
            return rect.left, rect.top, w, h
    except Exception:
        pass
    return 0, 0, win.winfo_screenwidth(), win.winfo_screenheight()


def make_chrome(win, bar, on_close=None, resizable=True, min_w=620, min_h=460):
    """
    Give a borderless window our own title bar: drag to move, minimise,
    maximise and close, plus a corner grip to resize. Used so the main window
    looks like the rest of the app instead of wearing the system frame.
    """
    import tkinter as tk

    state = {"max": False, "geo": None}

    def do_close():
        (on_close or win.destroy)()

    def do_minimise():
        # a borderless window cannot be iconified directly
        win.overrideredirect(False)
        win.iconify()

    def _remap(_evt=None):
        if win.state() == "normal":
            win.overrideredirect(True)
    win.bind("<Map>", _remap)

    def do_maximise():
        if state["max"]:
            if state["geo"]:
                win.geometry(state["geo"])
            state["max"] = False
        else:
            state["geo"] = win.geometry()
            x, y, w, h = work_area(win)
            win.geometry(f"{w}x{h}+{x}+{y}")
            state["max"] = True

    btn_side = i18n.side("right")
    for text, cmd, size in (("\u2715", do_close, 12),
                            ("\u25a1", do_maximise, 10),
                            ("\u2500", do_minimise, 12)):
        b = tk.Label(bar, text=text, bg=bar.cget("bg"), fg="#FFFFFF",
                     font=(UI, size), cursor="hand2", padx=10)
        b.pack(side=btn_side)
        b.bind("<Button-1>", lambda e, c=cmd: c())
        b.bind("<Enter>", lambda e, w=b: w.config(fg="#8FB6E4"))
        b.bind("<Leave>", lambda e, w=b: w.config(fg="#FFFFFF"))

    drag = {"x": 0, "y": 0}

    def start(e):
        drag.update(x=e.x_root - win.winfo_x(), y=e.y_root - win.winfo_y())

    def move(e):
        if state["max"]:
            return
        win.geometry(f"+{e.x_root - drag['x']}+{e.y_root - drag['y']}")
    bar.bind("<Button-1>", start)
    bar.bind("<B1-Motion>", move)
    bar.bind("<Double-Button-1>", lambda e: do_maximise())
    for child in bar.winfo_children():
        if isinstance(child, tk.Label) and child.cget("cursor") != "hand2":
            child.bind("<Button-1>", start)
            child.bind("<B1-Motion>", move)

    if resizable:
        grip = tk.Canvas(win, width=16, height=16, highlightthickness=0, bd=0,
                         bg=CANVAS, cursor=("bottom_left_corner" if i18n.is_rtl()
                                            else "bottom_right_corner"))
        for i in (4, 8, 12):
            if i18n.is_rtl():
                grip.create_line(2, 14, i + 2, 14 - i, fill=LINE, width=1)
            else:
                grip.create_line(14, i, i, 14, fill=LINE, width=1)
        grip.place(relx=(0.0 if i18n.is_rtl() else 1.0), rely=1.0,
                   anchor=("sw" if i18n.is_rtl() else "se"))

        rs = {"x": 0, "y": 0, "w": 0, "h": 0}

        def rstart(e):
            rs.update(x=e.x_root, y=e.y_root,
                      w=win.winfo_width(), h=win.winfo_height())

        def rmove(e):
            dx = e.x_root - rs["x"]
            dy = e.y_root - rs["y"]
            if i18n.is_rtl():
                dx = -dx
            w = max(min_w, rs["w"] + dx)
            h = max(min_h, rs["h"] + dy)
            win.geometry(f"{w}x{h}")
        grip.bind("<Button-1>", rstart)
        grip.bind("<B1-Motion>", rmove)

    win.after(60, lambda: round_window_corners(win))
    return do_close


def size_window(win, needed_h, width=None, min_h=430, max_frac=0.88):
    """
    Give a borderless window the height its content needs, capped to the screen.
    Content inside a scrolling region does not raise the window's own requested
    height, so the caller measures the parts and passes the total in.
    """
    try:
        win.update_idletasks()
        screen = win.winfo_screenheight()
        h = max(min_h, min(int(needed_h), int(screen * max_frac)))
        w = width or win.winfo_width() or win.winfo_reqwidth()
        x, y = win.winfo_x(), win.winfo_y()
        if y + h > screen:
            y = max(0, (screen - h) // 2)
        win.geometry(f"{w}x{h}+{x}+{y}")
    except Exception:
        pass


class ProgressBar:
    """Rounded progress bar."""

    def __init__(self, parent, width=380, height=10, bg=None):
        self.w, self.h = width, height
        self.c = tk.Canvas(parent, width=width, height=height,
                           highlightthickness=0, bd=0, bg=bg or parent.cget("bg"))
        self.c.create_polygon(round_pts(0, 0, width, height, height // 2),
                              smooth=True, splinesteps=12, fill="#DCE4EE", outline="")
        self.fill = self.c.create_polygon(round_pts(0, 0, 1, height, height // 2),
                                          smooth=True, splinesteps=12,
                                          fill=SIGNAL, outline="")

    def set(self, frac):
        frac = max(0.0, min(1.0, frac))
        w = max(self.h, int(self.w * frac))
        self.c.coords(self.fill, *round_pts(0, 0, w, self.h, self.h // 2))
        self.c.update_idletasks()

    def pack(self, **kw):
        self.c.pack(**kw); return self


class StepDots:
    """Shows which step of the install we are on."""

    def __init__(self, parent, count, bg=None):
        self.count = count
        self.dots = []
        self.c = tk.Canvas(parent, width=count * 26, height=14,
                           highlightthickness=0, bd=0, bg=bg or parent.cget("bg"))
        for i in range(count):
            x = 8 + i * 26
            self.dots.append(self.c.create_oval(x - 5, 2, x + 5, 12,
                                                fill="#C9D4E2", outline=""))

    def set(self, index):
        for i, d in enumerate(self.dots):
            self.c.itemconfig(d, fill=(SIGNAL if i <= index else "#C9D4E2"))

    def pack(self, **kw):
        self.c.pack(**kw); return self


def pick_folder(parent, title="Choose a folder", initialdir=None):
    """Folder-only browser, same look as pick_file."""
    win = Window(parent, title, 480, 500)
    cur = {"dir": os.path.abspath(initialdir or os.path.expanduser("~"))}

    path_lbl = tk.Label(win.body, text="", bg=CANVAS, fg=MUTED, font=(MONO, 9),
                        anchor=i18n.anchor("w"), wraplength=430, justify=i18n.justify("left"))
    path_lbl.pack(fill="x", padx=20, pady=(14, 6))

    card = tk.Frame(win.body, bg=SURFACE, highlightbackground=LINE,
                    highlightthickness=1)
    card.pack(fill="both", expand=True, padx=20)
    lb = tk.Listbox(card, bd=0, highlightthickness=0, bg=SURFACE, fg=INK,
                    font=(UI, 10), selectbackground="#DCE8F7",
                    selectforeground=INK, activestyle="none")
    lb.pack(fill="both", expand=True, padx=8, pady=8)

    entries = []

    def load():
        lb.delete(0, "end")
        entries.clear()
        d = cur["dir"]
        path_lbl.config(text=d)
        up = os.path.dirname(d)
        if up and up != d:
            entries.append(up); lb.insert("end", "\u2191  ..")
        try:
            for n in sorted(os.listdir(d), key=str.lower):
                p = os.path.join(d, n)
                if os.path.isdir(p):
                    entries.append(p); lb.insert("end", "\U0001F4C1  " + n)
        except Exception as e:
            lb.insert("end", f"(cannot open: {e})")

    def enter(_evt=None):
        sel = lb.curselection()
        if sel and sel[0] < len(entries):
            cur["dir"] = entries[sel[0]]; load()

    lb.bind("<Double-Button-1>", enter)
    load()

    row = tk.Frame(win.body, bg=CANVAS); row.pack(pady=14)
    RButton(row, _("Cancel"), win.destroy, kind="quiet",
            width=104, height=42, bg=CANVAS).pack(side=i18n.side("left"), padx=5)
    RButton(row, _("Open"), enter, kind="ghost",
            width=104, height=42, bg=CANVAS).pack(side=i18n.side("left"), padx=5)

    def choose():
        win.result = cur["dir"]; win.destroy()
    RButton(row, _("Use this folder"), choose, kind="primary",
            width=150, height=42, bg=CANVAS).pack(side=i18n.side("left"), padx=5)
    return win.wait()


def pick_file(parent, title="Choose a file", initialdir=None,
              extensions=(".ps1", ".bat", ".cmd", ".py", ".sh", ".exe")):
    """Our own file browser, so no native Windows dialog ever appears."""
    win = Window(parent, title, 480, 520)
    cur = {"dir": os.path.abspath(initialdir or os.path.expanduser("~"))}

    path_lbl = tk.Label(win.body, text="", bg=CANVAS, fg=MUTED, font=(MONO, 9),
                        anchor=i18n.anchor("w"), wraplength=430, justify=i18n.justify("left"))
    path_lbl.pack(fill="x", padx=20, pady=(14, 6))

    card = tk.Frame(win.body, bg=SURFACE, highlightbackground=LINE,
                    highlightthickness=1)
    card.pack(fill="both", expand=True, padx=20)
    lb = tk.Listbox(card, bd=0, highlightthickness=0, bg=SURFACE, fg=INK,
                    font=(UI, 10), selectbackground="#DCE8F7",
                    selectforeground=INK, activestyle="none")
    lb.pack(fill="both", expand=True, padx=8, pady=8)

    show_all = tk.BooleanVar(value=False)
    entries = []

    def load():
        lb.delete(0, "end")
        entries.clear()
        d = cur["dir"]
        path_lbl.config(text=d)
        parent_dir = os.path.dirname(d)
        if parent_dir and parent_dir != d:
            entries.append(("up", parent_dir)); lb.insert("end", "\u2191  ..")
        try:
            names = sorted(os.listdir(d), key=str.lower)
        except Exception as e:
            lb.insert("end", f"(cannot open: {e})"); return
        for n in names:
            p = os.path.join(d, n)
            if os.path.isdir(p):
                entries.append(("dir", p)); lb.insert("end", "\U0001F4C1  " + n)
        for n in names:
            p = os.path.join(d, n)
            if os.path.isfile(p) and (show_all.get()
                                      or n.lower().endswith(extensions)):
                entries.append(("file", p)); lb.insert("end", "     " + n)

    def activate(_evt=None):
        sel = lb.curselection()
        if not sel or sel[0] >= len(entries):
            return
        kind, p = entries[sel[0]]
        if kind in ("dir", "up"):
            cur["dir"] = p; load()
        else:
            win.result = p; win.destroy()

    lb.bind("<Double-Button-1>", activate)
    lb.bind("<Return>", activate)
    load()

    Checkbox(win.body, _("Show all file types"), variable=show_all, command=load,
             bg=CANVAS, font_size=9, fg=MUTED).pack(anchor=i18n.anchor("w"), padx=22, pady=(6, 0))

    row = tk.Frame(win.body, bg=CANVAS); row.pack(pady=14)
    RButton(row, _("Cancel"), win.destroy, kind="quiet",
            width=110, height=42, bg=CANVAS).pack(side=i18n.side("left"), padx=6)
    RButton(row, _("Choose"), activate, kind="primary",
            width=140, height=42, bg=CANVAS).pack(side=i18n.side("left"), padx=6)
    return win.wait()
