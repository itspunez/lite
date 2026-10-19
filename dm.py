import os, re, logging, logging.handlers, struct, zlib, sys, json, math, time, queue, mimetypes, threading, subprocess
import tkinter as tk
import tkinter.font as tkfont
from datetime import datetime
from pathlib import Path
from tkinter import ttk, filedialog, messagebox
from urllib.parse import urlparse, unquote


try:
    from tkinterdnd2 import TkinterDnD, DND_TEXT, DND_FILES
    BaseTk = TkinterDnD.Tk
except Exception:
    TkinterDnD, BaseTk = None, tk.Tk

DATA = Path(os.environ.get("MYDM_HOME") or Path.home() / ".mydm")
STATE = DATA / "state.json"
LOGDIR = DATA / "logs"
LOGFILE = LOGDIR / "app.log"
LOG = logging.getLogger("mydm")


def setup_log():
    """Write everything that goes wrong to ~/.mydm/logs/app.log (3 files x 1 MB)."""
    LOG.setLevel(logging.DEBUG)
    if LOG.handlers:
        return
    try:
        LOGDIR.mkdir(parents=True, exist_ok=True)
        h = logging.handlers.RotatingFileHandler(LOGFILE, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(threadName)s  %(message)s"))
        LOG.addHandler(h)
    except OSError:
        LOG.addHandler(logging.NullHandler())
    LOG.info("MyDM %s started (%s)", VERSION, sys.platform)


def log_crash(kind, value, tb, *a):
    LOG.error("Unhandled %s: %s", getattr(kind, "__name__", kind), value, exc_info=(kind, value, tb))


UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
CATS = {
    "Video": "mp4 mkv avi mov wmv flv webm ts m4v mpg mpeg 3gp",
    "Music": "mp3 flac wav aac ogg m4a wma opus",
    "Documents": "pdf doc docx xls xlsx ppt pptx txt epub csv rtf",
    "Compressed": "zip rar 7z tar gz bz2 xz",
    "Programs": "exe msi dmg pkg apk deb rpm iso",
    "Images": "jpg jpeg png gif webp bmp svg tif tiff",
}
EXT2CAT = {e: c for c, s in CATS.items() for e in s.split()}
CAT_LIST = list(CATS) + ["Other"]
CAT_COL = {"Video": "#7286cc", "Music": "#c574a1", "Documents": "#628aca",
           "Compressed": "#c39953", "Programs": "#4d9b85", "Images": "#57a2af",
           "Other": "#85919e"}
EXTS = set(EXT2CAT) | {"torrent", "bin", "img", "m3u8", "mpd"}
RUNNING = ("Waiting", "Connecting", "Downloading")
ACTIONS = ["Do nothing", "Exit MyDM", "Lock screen", "Log off", "Sleep", "Hibernate",
           "Restart", "Shut down", "Shut down (force close apps)"]
WIN = sys.platform.startswith("win")
VERSION = "4.1"
PORT = 17890                      # lets a second launch bring the running window to the front
SCHED = {"start_on": False, "start": "02:00", "stop_on": False, "stop": "07:00",
         "daily": True, "action": "Do nothing", "on_finish": False}
CFG = {"folder": str(Path.home() / "Downloads"), "segments": 8, "parallel": 3,
       "limit_kb": 0, "proxy_mode": "system", "proxy": "", "watch": True,
       "autostart": False, "subfolders": True, "sound": False,
       "theme": "system", "density": "compact", "geom": "", "sched": dict(SCHED), 
       "clip_mode": "ask", "colw": {}, "side_w": 214, "wins": {},
       "tray": True}
JOBS = []
FONT, SC = "TkDefaultFont", 1.0

# Semantic desktop design tokens, shared by all windows.
LIGHT = dict(dark=False, bg="#f4f5f7", panel="#ffffff", fg="#1b1d22", sub="#737782",
             accent="#2f6df0", accent2="#2559cc", border="#e2e4e9", sel="#e8f0ff",
             hdr="#eceef2", hover="#eef1f7", ok="#1f9e66", err="#d93a45", bar="#dfe2e8", warn="#d98a1b")
DARK = dict(dark=True, bg="#0b0b0c", panel="#141416", fg="#e9eaee", sub="#8b8d96",
            accent="#4c8dff", accent2="#3a78e8", border="#232327", sel="#18212f",
            hdr="#0f0f11", hover="#121217", ok="#3ecf8e", err="#ff5a5f", bar="#26262b", warn="#f2a33a")
P = dict(LIGHT)


def S(x):
    return int(x * SC)


def human(n):
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024


def fmt_eta(s):
    s = int(s)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def clean(name):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return name or "file"


def category(name):
    return EXT2CAT.get(os.path.splitext(name)[1].lstrip(".").lower(), "Other")


def unique(folder, name, taken):
    base, ext = os.path.splitext(name)
    p, n = os.path.join(folder, name), 1
    while os.path.exists(p) or os.path.exists(p + ".part") or p in taken:
        p = os.path.join(folder, f"{base} ({n}){ext}")
        n += 1
    return p


def mix(a, b, t):
    x = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    y = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(int(p + (q - p) * t) for p, q in zip(x, y))


def fit(text, font, width):
    if font.measure(text) <= width:
        return text
    while text and font.measure(text + "...") > width:
        text = text[:-1]
    return text + "..."


def rrect(c, x0, y0, x1, y1, r, **kw):
    pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1, x1 - r, y1,
           x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
    return c.create_polygon(pts, smooth=True, **kw)


def http_get(url, **kw):
    import requests               # loaded on first use: keeps start-up fast
    s = requests.Session()
    mode = CFG["proxy_mode"]
    s.trust_env = mode == "system"          # system = Windows proxy settings + env vars
    if mode == "manual" and CFG["proxy"].strip():
        p = CFG["proxy"].strip()
        if "://" not in p:
            p = "http://" + p
        s.proxies = {"http": p, "https": p}
    r = s.get(url, **kw)
    r._sess = s
    return r






def system_proxy():
    try:
        import urllib.request
        p = urllib.request.getproxies()
        return p.get("https") or p.get("http") or p.get("socks") or ""
    except Exception:
        LOG.debug("system_proxy", exc_info=True)
        return ""


class Cancel(Exception):
    pass


def app_dir():
    return os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else \
        os.path.dirname(os.path.abspath(__file__))




































PAIR = {"busy": False, "deny_until": 0.0}




def open_path(p, select=False):
    try:
        if WIN:
            if select and os.path.exists(p):
                subprocess.Popen(f'explorer /select,"{os.path.normpath(p)}"')
            else:
                os.startfile(p)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", p] if select else ["open", p])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(p) if select else p])
    except Exception:
        LOG.debug("open_path", exc_info=True)


def system_dark():
    try:
        if WIN:
            import winreg
            k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                               r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
        if sys.platform == "darwin":
            r = subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"],
                               capture_output=True, text=True)
            return "Dark" in r.stdout
        r = subprocess.run(["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
                           capture_output=True, text=True)
        return "dark" in r.stdout
    except Exception:
        LOG.debug("system_dark", exc_info=True)
        return False


def titlebar(win, dark):
    if not WIN:
        return
    try:
        import ctypes
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        v = ctypes.c_int(1 if dark else 0)
        for attr in (20, 19):
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), 4) == 0:
                break
    except Exception:
        LOG.debug("titlebar", exc_info=True)


def tr(win, root):
    """transient() only while the main window is visible (a transient of a hidden window stays hidden)."""
    try:
        if root.winfo_viewable():
            win.transient(root)
    except tk.TclError:
        pass


class _Where:
    """Where dialogs are centred: the main window, or the screen when that is hidden (tray / minimised)."""
    hidden = True

    def __init__(self, root):
        self.w, self.h = root.winfo_screenwidth(), root.winfo_screenheight()

    def winfo_rootx(self):
        return 0

    def winfo_rooty(self):
        return self.h // 6

    def winfo_width(self):
        return self.w

    def winfo_height(self):
        return self.h * 2 // 3


def anchor(root):
    try:
        if root.winfo_viewable():
            return root
    except tk.TclError:
        pass
    return _Where(root)


def ontop(win, app):
    """Bring a window in front of the browser when it was asked for by the browser (or the main window is hidden)."""
    try:
        if time.time() < app.front_until or getattr(anchor(app.root), "hidden", False):
            win.attributes("-topmost", True)
            win.lift()
            win.focus_force()
            win.after(1500, lambda: win.winfo_exists() and win.attributes("-topmost", False))
    except tk.TclError:
        pass


def make_ico(path):
    """The tray icon: blue rounded square with a white download arrow, written as a 32x32 PNG-in-ICO."""
    N, SS = 32, 4
    acc = (0x2f, 0x6d, 0xf0)

    def tri(px, py, a, b, c):
        d = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
        u = ((b[1] - c[1]) * (px - c[0]) + (c[0] - b[0]) * (py - c[1])) / d
        v = ((c[1] - a[1]) * (px - c[0]) + (a[0] - c[0]) * (py - c[1])) / d
        return u >= 0 and v >= 0 and u + v <= 1

    def shape(x, y):
        r = 7
        cx, cy = min(max(x, r), N - r), min(max(y, r), N - r)
        if (x - cx) ** 2 + (y - cy) ** 2 > r * r:
            return 0
        if 13 <= x <= 19 and 6 <= y <= 15 or tri(x, y, (8, 14), (24, 14), (16, 22)) or (8 <= x <= 24 and 24.5 <= y <= 27):
            return 2
        return 1
    rows = bytearray()
    for y in range(N):
        rows.append(0)
        for x in range(N):
            cov = [shape(x + (i + .5) / SS, y + (j + .5) / SS) for i in range(SS) for j in range(SS)]
            a = sum(1 for c in cov if c) / len(cov)
            w = sum(1 for c in cov if c == 2) / max(1, sum(1 for c in cov if c))
            rows += bytes([int(acc[k] * (1 - w) + 255 * w) for k in range(3)]) + bytes([int(255 * a)])

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", N, N, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(bytes(rows), 9)) + chunk(b"IEND", b""))
    ico = struct.pack("<HHH", 0, 1, 1) + struct.pack("<BBBBHHII", N, N, 0, 0, 1, 32, len(png), 22) + png
    Path(path).write_bytes(ico)


class Tray:
    """Notification-area icon (Windows, plain ctypes). It lives in its own thread and talks to the app
    through a queue, so if anything fails the app simply runs without it."""
    WM_TRAY = 0x0400 + 20

    def __init__(self, tip, ico):
        self.q, self.ready, self.up, self.hwnd = queue.Queue(), threading.Event(), False, None
        self.tip, self.ico = tip, ico
        threading.Thread(target=self._run, daemon=True, name="tray").start()
        self.ready.wait(3)

    def stop(self):
        try:
            if self.hwnd:
                import ctypes
                ctypes.windll.user32.PostMessageW(self.hwnd, 0x0010, 0, 0)     # WM_CLOSE
        except Exception:
            LOG.debug("tray.stop", exc_info=True)

    def _run(self):
        try:
            self._loop()
        except Exception:
            LOG.warning("tray failed", exc_info=True)
        finally:
            self.up = False
            self.ready.set()

    def _loop(self):
        import ctypes
        from ctypes import wintypes as wt
        u, sh, k = ctypes.windll.user32, ctypes.windll.shell32, ctypes.windll.kernel32
        LRESULT = ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

        class WNDCLASS(ctypes.Structure):
            _fields_ = [("style", wt.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                        ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                        ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH), ("lpszMenuName", wt.LPCWSTR),
                        ("lpszClassName", wt.LPCWSTR)]

        class NID(ctypes.Structure):
            _fields_ = [("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uID", wt.UINT), ("uFlags", wt.UINT),
                        ("uCallbackMessage", wt.UINT), ("hIcon", wt.HICON), ("szTip", wt.WCHAR * 128),
                        ("dwState", wt.DWORD), ("dwStateMask", wt.DWORD), ("szInfo", wt.WCHAR * 256),
                        ("uVersion", wt.UINT), ("szInfoTitle", wt.WCHAR * 64), ("dwInfoFlags", wt.DWORD),
                        ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", wt.HICON)]
        u.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
        u.DefWindowProcW.restype = LRESULT
        u.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASS)]
        u.RegisterClassW.restype = wt.ATOM
        u.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
                                      ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
        u.CreateWindowExW.restype = wt.HWND
        k.GetModuleHandleW.argtypes = [wt.LPCWSTR]
        k.GetModuleHandleW.restype = wt.HMODULE
        u.LoadImageW.argtypes = [wt.HINSTANCE, wt.LPCWSTR, wt.UINT, ctypes.c_int, ctypes.c_int, wt.UINT]
        u.LoadImageW.restype = wt.HANDLE
        u.LoadIconW.argtypes = [wt.HINSTANCE, ctypes.c_void_p]
        u.LoadIconW.restype = wt.HICON
        sh.Shell_NotifyIconW.argtypes = [wt.DWORD, ctypes.POINTER(NID)]
        sh.Shell_NotifyIconW.restype = wt.BOOL
        u.CreatePopupMenu.restype = wt.HMENU
        u.AppendMenuW.argtypes = [wt.HMENU, wt.UINT, ctypes.c_size_t, wt.LPCWSTR]
        u.TrackPopupMenu.argtypes = [wt.HMENU, wt.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HWND, wt.LPVOID]
        u.TrackPopupMenu.restype = ctypes.c_int
        u.DestroyMenu.argtypes = [wt.HMENU]
        u.SetForegroundWindow.argtypes = [wt.HWND]
        u.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
        u.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
        u.DestroyWindow.argtypes = [wt.HWND]
        u.PostQuitMessage.argtypes = [ctypes.c_int]
        u.RegisterWindowMessageW.argtypes = [wt.LPCWSTR]
        u.RegisterWindowMessageW.restype = wt.UINT
        u.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
        u.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
        u.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
        u.DispatchMessageW.restype = LRESULT

        hicon = u.LoadImageW(None, self.ico, 1, 0, 0, 0x10 | 0x40)          # IMAGE_ICON, from file, default size
        if not hicon:
            hicon = u.LoadIconW(None, 32512)                               # the standard application icon
        tb_created = u.RegisterWindowMessageW("TaskbarCreated")
        me = self

        def nid():
            n = NID()
            n.cbSize = ctypes.sizeof(NID)
            n.hWnd, n.uID, n.uFlags = me.hwnd, 1, 1 | 2 | 4
            n.uCallbackMessage, n.hIcon, n.szTip = Tray.WM_TRAY, hicon, me.tip
            return n

        def add():
            return bool(sh.Shell_NotifyIconW(0, ctypes.byref(nid())))      # NIM_ADD

        def menu():
            m = u.CreatePopupMenu()
            u.AppendMenuW(m, 0, 1, "Open MyDM")
            u.AppendMenuW(m, 0x800, 0, None)
            u.AppendMenuW(m, 0, 2, "Exit")
            pt = wt.POINT()
            u.GetCursorPos(ctypes.byref(pt))
            u.SetForegroundWindow(me.hwnd)
            cmd = u.TrackPopupMenu(m, 0x102, pt.x, pt.y, 0, me.hwnd, None)   # RETURNCMD | RIGHTBUTTON
            u.PostMessageW(me.hwnd, 0, 0, 0)
            u.DestroyMenu(m)
            if cmd:
                me.q.put("open" if cmd == 1 else "exit")

        def proc(hwnd, msg, wp, lp):
            try:
                if msg == Tray.WM_TRAY:
                    ev = lp & 0xFFFF
                    if ev in (0x202, 0x203):                                # left click / double click
                        me.q.put("open")
                    elif ev in (0x205, 0x7B):                               # right click
                        menu()
                    return 0
                if msg == tb_created:                                       # Explorer restarted
                    add()
                    return 0
                if msg == 0x0010:                                           # WM_CLOSE
                    u.DestroyWindow(hwnd)
                    return 0
                if msg == 0x0002:                                           # WM_DESTROY
                    sh.Shell_NotifyIconW(2, ctypes.byref(nid()))            # NIM_DELETE
                    u.PostQuitMessage(0)
                    return 0
            except Exception:
                LOG.debug("tray proc", exc_info=True)
            return u.DefWindowProcW(hwnd, msg, wp, lp)
        self._proc = WNDPROC(proc)                                          # keep a reference
        wc = WNDCLASS()
        wc.lpfnWndProc, wc.lpszClassName = self._proc, "MyDMTray"
        wc.hInstance = k.GetModuleHandleW(None)
        u.RegisterClassW(ctypes.byref(wc))
        self.hwnd = u.CreateWindowExW(0, "MyDMTray", "MyDM", 0, 0, 0, 0, 0, None, None, wc.hInstance, None)
        if not self.hwnd:
            raise OSError("no tray window")
        self.up = add()
        self.ready.set()
        if not self.up:
            return
        msg = wt.MSG()
        while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            u.TranslateMessage(ctypes.byref(msg))
            u.DispatchMessageW(ctypes.byref(msg))


def already_running():
    """If MyDM is already running (maybe hidden in the tray), ask it to show its window."""
    try:
        import socket
        with socket.create_connection(("127.0.0.1", PORT), timeout=0.5) as c:
            c.sendall(b"MyDM-show\n")
            return c.recv(8) == b"ok"
    except Exception:
        return False


def start_listener(inbox):
    """Tiny local listener: a second launch of MyDM just asks this one to show its window."""
    import socket
    try:
        srv = socket.socket()
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", PORT))
        srv.listen(4)
    except OSError:
        return None

    def loop():
        while True:
            try:
                c, _ = srv.accept()
                with c:
                    if c.recv(64).startswith(b"MyDM-show"):
                        inbox.put("show")
                        c.sendall(b"ok")
            except OSError:
                return
    threading.Thread(target=loop, daemon=True).start()
    return srv


def set_startup(on):
    if not (WIN and getattr(sys, "frozen", False)):
        return
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                           r"Software\Microsoft\Windows\CurrentVersion\Run", 0,
                           winreg.KEY_SET_VALUE)
        if on:
            winreg.SetValueEx(k, "MyDM", 0, winreg.REG_SZ, f'"{sys.executable}" --min')
        else:
            winreg.DeleteValue(k, "MyDM")
    except Exception:
        LOG.debug("set_startup", exc_info=True)


def power_cmd(action):
    if WIN:
        return {"Lock screen": ["rundll32.exe", "user32.dll,LockWorkStation"],
                "Log off": ["shutdown", "/l"],
                "Sleep": ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
                "Hibernate": ["shutdown", "/h"],
                "Restart": ["shutdown", "/r", "/t", "0"],
                "Shut down": ["shutdown", "/s", "/t", "0"],
                "Shut down (force close apps)": ["shutdown", "/s", "/f", "/t", "0"]}.get(action)
    return {"Lock screen": ["loginctl", "lock-session"], "Sleep": ["systemctl", "suspend"],
            "Hibernate": ["systemctl", "hibernate"], "Restart": ["systemctl", "reboot"],
            "Shut down": ["systemctl", "poweroff"],
            "Shut down (force close apps)": ["systemctl", "poweroff", "-f"]}.get(action)


def init_fonts(root):
    global FONT
    fams = set(tkfont.families(root))
    FONT = next((f for f in ("Segoe UI", "SF Pro Text", "Inter", "Helvetica Neue", "Ubuntu",
                             "DejaVu Sans") if f in fams),
                tkfont.nametofont("TkDefaultFont").actual("family"))
    for n in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
        tkfont.nametofont(n).configure(family=FONT, size=10)


def apply_theme(root):
    dark = system_dark() if CFG["theme"] == "system" else CFG["theme"] == "dark"
    P.clear()
    P.update(DARK if dark else LIGHT)
    st = ttk.Style(root)
    st.theme_use("clam")
    flat = dict(lightcolor=P["panel"], darkcolor=P["panel"], bordercolor=P["border"])
    st.configure(".", background=P["bg"], foreground=P["fg"], fieldbackground=P["panel"],
                 troughcolor=P["bar"], focuscolor=P["bg"], font=(FONT, 10))
    st.configure("TFrame", background=P["bg"])
    st.configure("TLabel", background=P["bg"], foreground=P["fg"])
    st.configure("Sub.TLabel", foreground=P["sub"])
    st.configure("TMenubutton", background=P["panel"], foreground=P["fg"],
                 padding=(S(11), S(7)), relief="flat", borderwidth=1, **flat)
    st.configure("TButton", background=P["panel"], foreground=P["fg"], padding=(S(11), S(7)),
                 relief="flat", borderwidth=1, width=0, **flat)
    st.map("TButton", background=[("active", P["sel"]), ("disabled", P["bg"])],
           foreground=[("disabled", P["sub"])])
    acc = dict(lightcolor=P["accent"], darkcolor=P["accent"], bordercolor=P["accent"])
    st.configure("Accent.TButton", background=P["accent"], foreground="#ffffff", **acc)
    st.map("Accent.TButton", background=[("active", P["accent2"])],
           foreground=[("disabled", "#ffffff")])
    for w in ("TEntry", "TSpinbox"):
        st.configure(w, fieldbackground=P["panel"], foreground=P["fg"], insertcolor=P["fg"],
                     arrowcolor=P["sub"], padding=S(7), **flat)
    st.configure("TCombobox", fieldbackground=P["panel"], background=P["panel"],
                 foreground=P["fg"], arrowcolor=P["sub"], padding=S(5), **flat)
    st.map("TCombobox", fieldbackground=[("readonly", P["panel"])],
           foreground=[("readonly", P["fg"])], selectbackground=[("readonly", P["panel"])],
           selectforeground=[("readonly", P["fg"])])
    root.option_add("*TCombobox*Listbox.background", P["panel"])
    root.option_add("*TCombobox*Listbox.foreground", P["fg"])
    root.option_add("*TCombobox*Listbox.selectBackground", P["accent"])
    root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
    st.configure("TCheckbutton", background=P["bg"], foreground=P["fg"])
    st.map("TCheckbutton", background=[("active", P["bg"])])
    st.configure("Treeview", background=P["panel"], fieldbackground=P["panel"],
                 foreground=P["fg"], rowheight=S(32), borderwidth=0, **flat)
    st.map("Treeview", background=[("selected", P["sel"])], foreground=[("selected", P["fg"])])
    st.configure("Treeview.Heading", background=P["hdr"], foreground=P["sub"], relief="flat",
                 padding=(S(8), S(7)), font=(FONT, 9, "bold"), bordercolor=P["hdr"],
                 lightcolor=P["hdr"], darkcolor=P["hdr"])
    st.configure("TScrollbar", background=P["hdr"], troughcolor=P["bg"], arrowcolor=P["sub"],
                 bordercolor=P["bg"], lightcolor=P["hdr"], darkcolor=P["hdr"], gripcount=0)
    st.map("TScrollbar", background=[("active", P["border"])])
    st.configure("Horizontal.TProgressbar", background=P["accent"], troughcolor=P["bar"],
                 thickness=S(10), lightcolor=P["accent"], darkcolor=P["accent"],
                 bordercolor=P["bar"])
    st.configure("TNotebook", background=P["bg"], borderwidth=0, tabmargins=(0, 0, 0, 0))
    st.configure("TNotebook.Tab", background=P["hdr"], foreground=P["sub"], borderwidth=0,
                 padding=(S(16), S(8)), font=(FONT, 10))
    st.map("TNotebook.Tab", background=[("selected", P["panel"])],
           foreground=[("selected", P["accent"])])
    root.configure(bg=P["bg"])


def make_icon():
    img = tk.PhotoImage(width=64, height=64)
    img.put(LIGHT["accent"], to=(0, 0, 64, 64))
    img.put("#ffffff", to=(26, 12, 38, 34))
    for i in range(14):
        img.put("#ffffff", to=(18 + i, 32 + i, 46 - i, 33 + i))
    img.put("#ffffff", to=(16, 50, 48, 54))
    return img


# ===================================================================== engine
class Limiter:
    def __init__(self):
        self.lock = threading.Lock()
        self.nxt = 0.0

    def wait(self, n, ev):
        rate = CFG["limit_kb"] * 1024
        if rate <= 0:
            return
        with self.lock:
            now = time.monotonic()
            self.nxt = max(self.nxt, now) + n / rate
            delay = self.nxt - now
        if delay > 0:
            ev.wait(delay)


LIM = Limiter()


class Job:
    KEYS = ("url", "folder", "name", "path", "total", "ranged", "segs", "status", "referer",
            "added", "at")

    def __init__(self, url, folder, kind="file"):
        self.url, self.folder = url, folder
        self.referer, self.ua = "", ""
        self.kind, self.note, self.at, self.force = kind, "", 0.0, False
        self.added = time.time()
        self.name = clean(unquote(os.path.basename(urlparse(url).path)))
        self.path, self.total, self.ranged, self.segs = "", 0, False, []
        self.status, self.err = "Queued", None
        self.prev = self.status
        self.stop = threading.Event()
        self.id = os.urandom(4).hex()
        self.speed, self.hist = 0.0, []
        self.last = (time.monotonic(), 0)

    @property
    def done(self):
        return sum(max(0, min(p, e + 1) - s) for s, e, p in self.segs)

    @property
    def cat(self):
        return category(self.name)

    @property
    def dir(self):
        return os.path.dirname(self.path) if self.path else self.folder

    def to_dict(self):
        return {k: getattr(self, k) for k in self.KEYS}

    @classmethod
    def from_dict(cls, d):
        j = cls(d["url"], d.get("folder") or CFG["folder"])
        for k in cls.KEYS[2:]:
            if k in d:
                setattr(j, k, d[k])
        if j.status not in ("Done", "Off queue", "Scheduled"):
            j.status = "Paused"
        j.prev = j.status
        return j

    def hdrs(self):
        h = dict(UA)
        if self.ua:
            h["User-Agent"] = self.ua
        if self.referer:
            h["Referer"] = self.referer
        return h

    def probe(self):
        r = http_get(self.url, headers={**self.hdrs(), "Range": "bytes=0-0"}, stream=True, timeout=30)
        try:
            r.raise_for_status()
            cr = r.headers.get("Content-Range", "")
            if r.status_code == 206 and cr.split("/")[-1].isdigit():
                total, ranged = int(cr.split("/")[-1]), True
            else:
                total, ranged = int(r.headers.get("Content-Length") or 0), False
            m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)',
                          r.headers.get("Content-Disposition", ""), re.I)
            ctype = r.headers.get("Content-Type", "").split(";")[0].strip()
            return total, ranged, (clean(unquote(m.group(1))) if m else None), ctype
        finally:
            r.close()

    def run(self):
        try:
            self.err = None
            total, ranged, name, ctype = self.probe()
            part = self.path + ".part"
            resume = bool(self.segs and self.ranged and ranged and total == self.total
                          and self.path and os.path.exists(part))
            if not resume:
                if self.path and os.path.exists(part):
                    os.remove(part)
                self.name = name or self.name
                if not os.path.splitext(self.name)[1] and ctype:
                    self.name += mimetypes.guess_extension(ctype) or ""
                folder = os.path.join(self.folder, self.cat) if CFG["subfolders"] else self.folder
                os.makedirs(folder, exist_ok=True)
                taken = {j.path for j in JOBS if j is not self}
                self.path = unique(folder, self.name, taken)
                part = self.path + ".part"
                self.total, self.ranged = total, ranged
                if ranged and total:
                    n = max(1, min(int(CFG["segments"]), total // 262144))
                    size = total // n
                    self.segs = [[i * size, total - 1 if i == n - 1 else (i + 1) * size - 1,
                                  i * size] for i in range(n)]
                else:
                    self.segs = [[0, total - 1 if total else 2 ** 62, 0]]
                with open(part, "wb") as f:
                    if ranged and total:
                        f.truncate(total)
            self.status = "Downloading"
            if self.ranged and self.total:
                ts = [threading.Thread(target=self.seg, args=(s,), daemon=True)
                      for s in self.segs if s[2] <= s[1]]
                for t in ts:
                    t.start()
                for t in ts:
                    t.join()
            else:
                self.single()
            if self.err:
                self.status = "Error: " + self.err[:60]
            elif self.stop.is_set():
                if self.status not in ("Off queue", "Scheduled"):
                    self.status = "Paused"
            elif self.ranged and self.total and any(s[2] <= s[1] for s in self.segs):
                self.status = "Error: incomplete"
            else:
                os.replace(self.path + ".part", self.path)
                self.status = "Done"
        except Exception as ex:
            LOG.warning("download failed: %s  (%s)", self.url, ex, exc_info=True)
            self.status = "Error: " + re.sub(r"\x1b\[[0-9;]*m", "", str(ex))[:160]

    def seg(self, s):
        tries = 0
        while not self.stop.is_set() and s[2] <= s[1]:
            before = s[2]
            try:
                h = {**self.hdrs(), "Range": f"bytes={s[2]}-{s[1]}"}
                with http_get(self.url, headers=h, stream=True, timeout=30) as r:
                    if r.status_code != 206:
                        raise IOError(f"HTTP {r.status_code}")
                    with open(self.path + ".part", "r+b") as f:
                        f.seek(s[2])
                        for c in r.iter_content(65536):
                            if self.stop.is_set():
                                return
                            c = c[: s[1] - s[2] + 1]
                            f.write(c)
                            f.flush()
                            s[2] += len(c)
                            LIM.wait(len(c), self.stop)
                            if s[2] > s[1]:
                                break
                if s[2] == before and s[2] <= s[1]:
                    raise IOError("no data received")
                tries = 0
            except Exception as ex:
                if s[2] > before and not self.stop.is_set():
                    tries = 0                    # it was working: reconnect right away, no waiting
                    continue
                tries += 1
                LOG.info("segment retry %s/5: %s  (%s)", tries, self.url, ex)
                if tries > 8:
                    self.err = str(ex)
                    self.stop.set()
                    return
                self.stop.wait(min(30, 2 * tries))

    def single(self):
        s = self.segs[0]
        s[2] = 0
        with http_get(self.url, headers=self.hdrs(), stream=True, timeout=30) as r:
            r.raise_for_status()
            with open(self.path + ".part", "wb") as f:
                for c in r.iter_content(65536):
                    if self.stop.is_set():
                        return
                    f.write(c)
                    s[2] += len(c)
                    LIM.wait(len(c), self.stop)
        if self.total and s[2] < self.total:
            raise IOError("connection closed early")








# ================================================================== widgets
class Hint(ttk.Entry):
    def __init__(self, parent, hint):
        super().__init__(parent)
        self.hint, self.empty = hint, False
        self.bind("<FocusIn>", self._in)
        self.bind("<FocusOut>", self._out)
        self._out()

    def _in(self, _=None):
        if self.empty:
            self.delete(0, "end")
            self.empty = False
        self.restyle()

    def _out(self, _=None):
        if not self.get():
            self.empty = True
            self.insert(0, self.hint)
        self.restyle()

    def restyle(self):
        self.configure(foreground=P["sub"] if self.empty else P["fg"])

    def value(self):
        return "" if self.empty else self.get()

    def clear(self):
        self.delete(0, "end")
        self.empty = False
        self._out()


def glyph(c, kind, cx, cy, s, col, w=None):
    """Small line icons drawn straight on a canvas. (cx, cy) = centre, s = size in pixels."""
    h = s / 2
    w = w or max(1, round(s / 11))
    L = lambda *p: c.create_line(*p, fill=col, width=w, capstyle="round", joinstyle="round")
    R = lambda *p: c.create_rectangle(*p, outline=col, width=w)
    O = lambda *p: c.create_oval(*p, outline=col, width=w)
    PG = lambda *p: c.create_polygon(*p, outline=col, fill="", width=w, joinstyle="round")
    o = lambda r: (cx - r * h, cy - r * h, cx + r * h, cy + r * h)
    if kind == "plus":
        L(cx - .6 * h, cy, cx + .6 * h, cy)
        L(cx, cy - .6 * h, cx, cy + .6 * h)
    elif kind == "play":
        PG(cx - .45 * h, cy - .65 * h, cx + .7 * h, cy, cx - .45 * h, cy + .65 * h)
    elif kind == "stop":
        R(cx - .55 * h, cy - .55 * h, cx + .55 * h, cy + .55 * h)
    elif kind == "stopall":
        O(*o(.8))
        L(cx - .3 * h, cy - .3 * h, cx + .3 * h, cy + .3 * h)
        L(cx - .3 * h, cy + .3 * h, cx + .3 * h, cy - .3 * h)
    elif kind == "trash":
        L(cx - .75 * h, cy - .45 * h, cx + .75 * h, cy - .45 * h)
        L(cx - .25 * h, cy - .45 * h, cx - .25 * h, cy - .75 * h, cx + .25 * h, cy - .75 * h, cx + .25 * h, cy - .45 * h)
        PG(cx - .55 * h, cy - .45 * h, cx - .42 * h, cy + .8 * h, cx + .42 * h, cy + .8 * h, cx + .55 * h, cy - .45 * h)
    elif kind == "eraser":
        PG(cx - .8 * h, cy + .05 * h, cx + .05 * h, cy - .8 * h, cx + .8 * h, cy - .05 * h, cx - .05 * h, cy + .8 * h)
        L(cx - .35 * h, cy - .4 * h, cx + .4 * h, cy + .35 * h)
    elif kind == "gear":
        O(*o(.3))
        O(*o(.62))
        for i in range(8):
            a = i * math.pi / 4
            L(cx + .62 * h * math.cos(a), cy + .62 * h * math.sin(a),
              cx + .92 * h * math.cos(a), cy + .92 * h * math.sin(a))
    elif kind == "clock":
        O(*o(.85))
        L(cx, cy - .5 * h, cx, cy, cx + .4 * h, cy + .25 * h)
    elif kind in ("startq", "stopq"):
        for dy in (-.6, -.05, .5):
            L(cx - .85 * h, cy + dy * h, cx + .1 * h, cy + dy * h)
        if kind == "startq":
            PG(cx + .35 * h, cy - .35 * h, cx + .9 * h, cy + .05 * h, cx + .35 * h, cy + .45 * h)
        else:
            L(cx + .4 * h, cy - .3 * h, cx + .85 * h, cy + .2 * h)
            L(cx + .4 * h, cy + .2 * h, cx + .85 * h, cy - .3 * h)
    elif kind == "layers":
        PG(cx, cy - .8 * h, cx + .85 * h, cy - .4 * h, cx, cy, cx - .85 * h, cy - .4 * h)
        L(cx - .85 * h, cy + .05 * h, cx, cy + .45 * h, cx + .85 * h, cy + .05 * h)
        L(cx - .85 * h, cy + .5 * h, cx, cy + .9 * h, cx + .85 * h, cy + .5 * h)
    elif kind == "circle":
        O(*o(.75))
    elif kind == "check":
        O(*o(.78))
        L(cx - .35 * h, cy, cx - .08 * h, cy + .3 * h, cx + .4 * h, cy - .3 * h)
    elif kind == "video":
        R(cx - .8 * h, cy - .6 * h, cx + .8 * h, cy + .6 * h)
        L(cx - .4 * h, cy - .6 * h, cx - .4 * h, cy + .6 * h)
        L(cx + .4 * h, cy - .6 * h, cx + .4 * h, cy + .6 * h)
    elif kind == "music":
        c.create_oval(cx - .65 * h, cy + .15 * h, cx - .05 * h, cy + .7 * h, fill=col, outline=col)
        L(cx - .05 * h, cy + .4 * h, cx - .05 * h, cy - .7 * h, cx + .6 * h, cy - .45 * h)
    elif kind in ("doc", "zip", "file"):
        PG(cx - .55 * h, cy - .8 * h, cx + .2 * h, cy - .8 * h, cx + .55 * h, cy - .45 * h,
           cx + .55 * h, cy + .8 * h, cx - .55 * h, cy + .8 * h)
        if kind == "doc":
            L(cx - .25 * h, cy, cx + .25 * h, cy)
            L(cx - .25 * h, cy + .35 * h, cx + .25 * h, cy + .35 * h)
        elif kind == "zip":
            L(cx, cy - .6 * h, cx, cy - .05 * h)
            R(cx - .15 * h, cy, cx + .15 * h, cy + .4 * h)
    elif kind == "app":
        R(cx - .8 * h, cy - .65 * h, cx + .8 * h, cy + .65 * h)
        L(cx - .8 * h, cy - .25 * h, cx + .8 * h, cy - .25 * h)
    elif kind == "image":
        R(cx - .8 * h, cy - .65 * h, cx + .8 * h, cy + .65 * h)
        L(cx - .8 * h, cy + .45 * h, cx - .25 * h, cy - .05 * h, cx + .1 * h, cy + .3 * h,
          cx + .4 * h, cy + .05 * h, cx + .8 * h, cy + .45 * h)
    elif kind == "sun":
        O(*o(.38))
        for i in range(8):
            a = i * math.pi / 4
            L(cx + .62 * h * math.cos(a), cy + .62 * h * math.sin(a),
              cx + .92 * h * math.cos(a), cy + .92 * h * math.sin(a))
    elif kind == "moon":
        c.create_arc(*o(.8), start=50, extent=270, style="arc", outline=col, width=w)
        L(cx + .5 * h, cy - .62 * h, cx + .05 * h, cy - .05 * h)
    elif kind == "search":
        O(cx - .75 * h, cy - .75 * h, cx + .15 * h, cy + .15 * h)
        L(cx + .1 * h, cy + .1 * h, cx + .75 * h, cy + .75 * h)


def keep_size(win, key, minw=360, minh=240):
    """Let a window be resized by the user, and remember its size for next time."""
    kf = key if callable(key) else (lambda: key)         # a window may keep one size per mode
    win.resizable(True, True)

    def apply():
        win.update_idletasks()
        win.minsize(min(S(minw), win.winfo_reqwidth()), min(S(minh), win.winfo_reqheight()))
        sz = (CFG.get("wins") or {}).get(kf())
        if isinstance(sz, list) and len(sz) == 2:
            try:
                win.geometry(f"{max(int(sz[0]), S(minw) // 2)}x{max(int(sz[1]), S(minh) // 2)}")
                return
            except (ValueError, tk.TclError):
                pass
        win.geometry("")                                   # no saved size: natural size
    apply()
    win._apply_size = apply
    win._ks_hold = False

    def mem(e):
        if e.widget is win and e.width > 80 and e.height > 80 and not win._ks_hold:
            CFG.setdefault("wins", {})[kf()] = [e.width, e.height]
    win.bind("<Configure>", mem, add="+")


class Splitter(tk.Canvas):
    """Thin draggable bar between the sidebar and the list."""
    def __init__(self, parent, on_start, on_drag):
        super().__init__(parent, width=S(7), highlightthickness=0, bd=0, cursor="sb_h_double_arrow")
        self.bind("<ButtonPress-1>", on_start)
        self.bind("<B1-Motion>", on_drag)
        self.bind("<Configure>", lambda e: self.restyle())

    def restyle(self):
        self.delete("all")
        self.configure(bg=P["bg"])
        self.create_line(S(3), 0, S(3), self.winfo_height(), fill=P["border"])


class MenuBar(tk.Frame):
    def __init__(self, parent, items, on_theme):
        super().__init__(parent, bd=0, highlightthickness=0)
        self.labels = []
        for title, menu in items:
            l = tk.Label(self, text=title, padx=S(12), pady=S(9), font=(FONT, 10), cursor="hand2", bd=0)
            l.pack(side="left")
            l.bind("<Enter>", lambda e, l=l: l.configure(bg=P["hover"]))
            l.bind("<Leave>", lambda e, l=l: l.configure(bg=P["bg"]))
            l.bind("<Button-1>", lambda e, l=l, m=menu: self.pop(l, m))
            self.labels.append(l)
        self.theme = tk.Canvas(self, width=S(46), height=S(34), highlightthickness=0, bd=0, cursor="hand2")
        self.theme.pack(side="right")
        self.theme.bind("<Button-1>", lambda e: on_theme())

    def pop(self, l, m):
        try:
            m.tk_popup(l.winfo_rootx(), l.winfo_rooty() + l.winfo_height())
        finally:
            m.grab_release()

    def restyle(self):
        self.configure(bg=P["bg"])
        for l in self.labels:
            l.configure(bg=P["bg"], fg=P["fg"])
        t = self.theme
        t.delete("all")
        t.configure(bg=P["bg"])
        glyph(t, "sun" if P["dark"] else "moon", S(23), S(17), S(17), P["sub"], 2)


class Toolbar(tk.Canvas):
    BTNS = [("add", "plus", "Add URL"), None,
            ("resume", "play", "Resume"), ("stop", "stop", "Stop"), ("stopall", "stopall", "Stop All"), None,
            ("delete", "trash", "Delete"), ("clear", "eraser", "Clear Done"), None,
            ("options", "gear", "Options"), ("sched", "clock", "Scheduler"),
            ("startq", "startq", "Start Queue"), ("stopq", "stopq", "Stop Queue")]

    def __init__(self, parent, cmds):
        super().__init__(parent, height=S(80), highlightthickness=0, bd=0)
        self.cmds, self.en, self.hover, self.hits = cmds, {}, None, []
        self.f = tkfont.Font(family=FONT, size=9)
        self.bind("<Configure>", lambda e: self.redraw())
        self.bind("<Motion>", self.motion)
        self.bind("<Leave>", lambda e: self.set_hover(None))
        self.bind("<Button-1>", self.click)

    def enable(self, d):
        if d != self.en:
            self.en = d
            self.redraw()

    def at(self, e):
        return next((k for x0, x1, k in self.hits if x0 <= e.x < x1), None)

    def set_hover(self, k):
        if k != self.hover:
            self.hover = k
            self.configure(cursor="hand2" if k and self.en.get(k, True) else "")
            self.redraw()

    def motion(self, e):
        self.set_hover(self.at(e))

    def click(self, e):
        k = self.at(e)
        if k and self.en.get(k, True):
            self.cmds[k]()

    def redraw(self):
        self.delete("all")
        self.configure(bg=P["bg"])
        self.hits = []
        x, bw = S(14), S(74)
        for b in self.BTNS:
            if b is None:
                self.create_line(x + S(5), S(20), x + S(5), S(62), fill=P["border"])
                x += S(10)
                continue
            key, ic, label = b
            on = self.en.get(key, True)
            col = P["accent"] if key == "add" else P["err"] if key in ("stop", "stopall") else P["fg"]
            if not on:
                col = mix(P["sub"], P["bg"], .4)
            mid = x + bw // 2
            if self.hover == key and on:
                rrect(self, x, S(6), x + bw, S(74), S(8), fill=P["hover"], outline="")
            if key == "add":
                rrect(self, mid - S(19), S(9), mid + S(19), S(47), S(8), fill=mix(P["accent"], P["bg"], .82), outline="")
            glyph(self, ic, mid, S(28), S(20), col, 2)
            self.create_text(mid, S(60), text=label, font=self.f, fill=col if on else mix(P["sub"], P["bg"], .4))
            self.hits.append((x, x + bw, key))
            x += bw


class SearchBox(tk.Frame):
    HINT = "Search downloads"

    def __init__(self, parent, on_change):
        super().__init__(parent, bd=0, highlightthickness=1)
        self.on_change, self.empty = on_change, True
        self.icon = tk.Canvas(self, width=S(32), height=S(36), highlightthickness=0, bd=0)
        self.icon.pack(side="left")
        self.e = tk.Entry(self, relief="flat", bd=0, highlightthickness=0, font=(FONT, 10), width=26)
        self.e.pack(side="left", fill="both", expand=True, padx=(0, S(10)), pady=S(8))
        self.e.bind("<FocusIn>", self._in)
        self.e.bind("<FocusOut>", self._out)
        self.e.bind("<KeyRelease>", lambda e: self.on_change())
        self.e.bind("<Escape>", lambda e: (self.clear(), self.on_change()))
        self.e.insert(0, self.HINT)

    def _in(self, _=None):
        if self.empty:
            self.e.delete(0, "end")
            self.empty = False
        self.restyle()

    def _out(self, _=None):
        if not self.e.get():
            self.empty = True
            self.e.insert(0, self.HINT)
        self.restyle()

    def value(self):
        return "" if self.empty else self.e.get().strip()

    def clear(self):
        self.e.delete(0, "end")
        self.empty = False
        self._out()

    def restyle(self):
        self.configure(bg=P["panel"], highlightbackground=P["border"], highlightcolor=P["accent"])
        self.icon.configure(bg=P["panel"])
        self.icon.delete("all")
        glyph(self.icon, "search", S(17), S(18), S(15), P["sub"], 2)
        self.e.configure(bg=P["panel"], fg=P["sub"] if self.empty else P["fg"], insertbackground=P["fg"])


CAT_GLYPH = {"Video": "video", "Music": "music", "Documents": "doc", "Compressed": "zip",
             "Programs": "app", "Images": "image", "Other": "file"}
CAT_NAME = {"Documents": "Document", "Programs": "Program"}


class Side(tk.Canvas):
    def __init__(self, parent, on_pick):
        super().__init__(parent, width=S(214), highlightthickness=0, bd=0)
        self.on_pick, self.cur, self.counts, self.rows = on_pick, "all", {}, []
        self.f = tkfont.Font(family=FONT, size=10)
        self.fh = tkfont.Font(family=FONT, size=8, weight="bold")
        self.bind("<Button-1>", self.click)
        self.bind("<Configure>", lambda e: self.redraw())

    def items(self):
        c = self.counts
        rows = [("h", "CATEGORIES", None, 0), ("all", "All Downloads", "layers", 0)]
        for cat in CAT_LIST:
            if c.get("c:" + cat, 0) or self.cur == "c:" + cat:
                rows.append(("c:" + cat, CAT_NAME.get(cat, cat), CAT_GLYPH[cat], 1))
        return rows + [("unfinished", "Unfinished", "circle", 0), ("completed", "Finished", "check", 0),
                       ("h", "QUEUES", None, 0), ("queue", "Main queue", "clock", 0)]

    def set_counts(self, counts):
        if counts != self.counts:
            self.counts = counts
            self.redraw()

    def click(self, e):
        for y0, y1, key in self.rows:
            if y0 <= e.y < y1:
                self.cur = key
                self.on_pick(key)
                self.redraw()
                return

    def redraw(self):
        self.delete("all")
        self.configure(bg=P["bg"])
        W, y = self.winfo_width(), S(6)
        self.rows = []
        for key, label, ic, lvl in self.items():
            if key == "h":
                y += S(10)
                self.create_text(S(18), y + S(8), anchor="w", text=label, fill=P["sub"], font=self.fh)
                y += S(24)
                continue
            h = S(36)
            on = key == self.cur
            if on:
                rrect(self, S(4), y, W - S(4), y + h - S(2), S(6), fill=P["sel"], outline="")
            col = P["accent"] if on else P["sub"] if lvl else P["fg"]
            cy = y + (h - S(2)) // 2
            glyph(self, ic, S(24) + lvl * S(14), cy, S(15), col, 1 if lvl else 2)
            self.create_text(S(44) + lvl * S(14), cy, anchor="w", text=label, font=self.f,
                             fill=P["accent"] if on else P["fg"])
            self.create_text(W - S(16), cy, anchor="e", text=str(self.counts.get(key, 0)),
                             fill=P["sub"], font=self.f)
            self.rows.append((y, y + h, key))
            y += h


class JobList(tk.Canvas):
    def __init__(self, parent, app):
        super().__init__(parent, highlightthickness=0, bd=0, takefocus=1)
        self.app, self.jobs, self.sel, self.anchor = app, [], set(), None
        self.off, self.hover, self.drop, self.sb, self.drag = 0, None, False, None, None
        self.fn = tkfont.Font(family=FONT, size=10)
        self.fs = tkfont.Font(family=FONT, size=9)
        self.fh = tkfont.Font(family=FONT, size=8, weight="bold")
        self.bind("<Configure>", lambda e: self.redraw())
        self.bind("<Button-1>", self.click)
        self.bind("<Double-1>", self.double)
        self.bind("<B1-Motion>", lambda e: self.drag and self.edge_move(e))
        self.bind("<ButtonRelease-1>", lambda e: setattr(self, "drag", None))
        for b in ("<Button-3>", "<Button-2>"):
            self.bind(b, self.rclick)
        self.bind("<Motion>", self.motion)
        self.bind("<Leave>", lambda e: self.set_hover(None))
        self.bind("<Delete>", lambda e: app.remove())
        self.bind("<Control-a>", lambda e: self.select_all())

    @property
    def RH(self):                       # row height follows the "Download list size" setting
        return S(40) if CFG.get("density", "compact") == "compact" else S(54)

    @property
    def HH(self):
        return S(40)

    # --- model
    def set_jobs(self, jobs):
        self.jobs = jobs
        ids = {j.id for j in jobs}
        self.sel &= ids
        self.redraw()

    def selected(self):
        return [j for j in self.jobs if j.id in self.sel]

    def select_all(self):
        self.sel = {j.id for j in self.jobs}
        self.redraw()
        self.app.update_tb()

    def total_h(self):
        return len(self.jobs) * self.RH + S(4)

    def view_h(self):
        return max(1, self.winfo_height() - self.HH)

    def yview(self, *a):
        if a[0] == "moveto":
            self.off = int(float(a[1]) * self.total_h())
        else:
            step = self.RH if a[2] == "units" else self.view_h() * 9 // 10
            self.off += int(a[1]) * step
        self.redraw()

    def scroll_px(self, d):
        self.off += d
        self.redraw()

    # --- events
    def idx_at(self, y):
        if y < self.HH:
            return None
        i = (y - self.HH + self.off) // self.RH
        return i if 0 <= i < len(self.jobs) else None

    def click(self, e):
        self.focus_set()
        if self.edge_press(e):
            return
        i = self.idx_at(e.y)
        if i is None:
            self.sel.clear()
        else:
            j = self.jobs[i]
            ids = [x.id for x in self.jobs]
            if e.state & 0x1 and self.anchor in ids:
                a = ids.index(self.anchor)
                self.sel = set(ids[min(a, i): max(a, i) + 1])
            elif e.state & 0x4:
                self.sel ^= {j.id}
                self.anchor = j.id
            else:
                self.sel, self.anchor = {j.id}, j.id
        self.redraw()
        self.app.update_tb()

    def rclick(self, e):
        self.focus_set()
        i = self.idx_at(e.y)
        if i is None:
            return
        if self.jobs[i].id not in self.sel:
            self.sel, self.anchor = {self.jobs[i].id}, self.jobs[i].id
            self.redraw()
            self.app.update_tb()
        try:
            self.app.menu.tk_popup(e.x_root, e.y_root)
        finally:
            self.app.menu.grab_release()

    def double(self, e):
        i = self.idx_at(e.y)
        if i is not None:
            self.app.dbl(self.jobs[i])

    def motion(self, e):
        self.configure(cursor="sb_h_double_arrow" if self.edge_at(e) else "")
        i = self.idx_at(e.y)
        self.set_hover(self.jobs[i].id if i is not None else None)

    def set_hover(self, jid):
        if jid != self.hover:
            self.hover = jid
            self.redraw()

    # --- drawing
    DEFW = dict(size=96, status=224, left=88, rate=104, date=84)
    ORDER = ("size", "status", "left", "rate", "date")

    def cw(self, k):
        try:
            v = float((CFG.get("colw") or {}).get(k, self.DEFW[k]))
        except (TypeError, ValueError):
            v = self.DEFW[k]
        return S(min(520, max(50, v)))

    def cols(self, W):
        x, X = W - S(8), {"name": S(18)}
        for k in reversed(self.ORDER):
            x -= self.cw(k)
            X[k] = x
        return X

    def edge_at(self, e):
        """Which column's left edge is the mouse on (in the header)? Drag it to resize that column."""
        if e.y >= self.HH:
            return None
        X = self.cols(self.winfo_width())
        return next((k for k in self.ORDER if abs(e.x - X[k]) <= S(4)), None)

    def edge_press(self, e):
        k = self.edge_at(e)
        if k:
            self.drag = (k, e.x, self.cw(k))
        return bool(k)

    def edge_move(self, e):
        k, x0, w0 = self.drag
        W = self.winfo_width()
        others = sum(self.cw(o) for o in self.ORDER if o != k)
        room = W - S(8) - S(200) - others                 # keep the file-name column readable
        w = max(S(50), min(S(520), room, w0 + (x0 - e.x)))
        CFG.setdefault("colw", {})[k] = round(w / SC)
        self.redraw()

    def stripes(self, x0, x1, y0, y1, col):
        per, h = S(10), y1 - y0
        x = x0 - h - per + int(time.time() * 20) % per
        while x < x1:
            xa, ya, xb, yb = x, y1, x + h, y0
            if xa < x0:
                ya, xa = y1 - (x0 - xa), x0
            if xb > x1:
                yb, xb = y0 + (xb - x1), x1
            if xa < xb:
                self.create_line(xa, ya, xb, yb, fill=col, width=S(2))
            x += per

    def row(self, j, y, W, X):
        RH = self.RH
        sel, hov = j.id in self.sel, self.hover == j.id
        if sel or hov:
            self.create_rectangle(0, y, W, y + RH, fill=P["sel"] if sel else P["hover"], outline="")
        self.create_line(0, y + RH - 1, W, y + RH - 1, fill=P["border"])
        cy = y + RH // 2
        glyph(self, CAT_GLYPH[j.cat], X["name"] + S(9), cy, S(18), P["fg"] if sel else P["sub"], 1)
        self.create_text(X["name"] + S(34), cy, anchor="w", font=self.fn, fill=P["fg"],
                         text=fit(j.name, self.fn, X["size"] - X["name"] - S(46)))
        d, t, st = j.done, j.total, j.status
        self.create_text(X["size"], cy, anchor="w", font=self.fs, fill=P["fg"],
                         text=fit(human(t or d) if (t or d) else "—", self.fs, self.cw("size") - S(8)))
        sx, ws, bh = X["status"], self.cw("status"), S(6)
        bw = max(S(30), ws - S(120))
        err, run = st.startswith("Error"), st in RUNNING
        if st == "Done":
            glyph(self, "check", sx + S(8), cy, S(15), P["ok"], 1)
            self.create_text(sx + S(24), cy, anchor="w", text="Complete", font=self.fs, fill=P["ok"])
        else:
            by0 = cy - bh // 2
            rrect(self, sx, by0, sx + bw, by0 + bh, S(3), fill=P["bar"], outline="")
            pct = min(100.0, d * 100 / t) if t else 0.0
            col = (P["err"] if err else P["accent"] if run else P["warn"] if st in ("Paused", "Scheduled") else P["sub"])
            w = int(bw * pct / 100)
            if run and not t and st == "Downloading":
                w = bw
            if w > S(6) and st not in ("Queued", "Waiting", "Scheduled"):
                rrect(self, sx, by0, sx + w, by0 + bh, S(3), fill=col, outline="")
                if run:
                    self.stripes(sx + S(2), sx + w - S(2), by0 + 1, by0 + bh - 1, mix(col, "#ffffff", .3))
            if j.note and st == "Downloading":
                txt = fit(j.note, self.fs, min(S(88), ws - S(40)))
            elif err:
                txt = "Error"
            elif st == "Downloading":
                txt = f"{pct:.1f}%" if t else "Downloading"
            elif st in ("Waiting", "Queued"):
                txt = "Queued"
            elif st == "Scheduled":
                a = time.localtime(j.at)
                txt = time.strftime("%H:%M" if time.strftime("%Y%m%d", a) == time.strftime("%Y%m%d")
                                    else "%b %d %H:%M", a)
            else:
                txt = st if st != "Connecting" else "Connecting"
            self.create_text(sx + ws - S(26), cy, anchor="e", text=txt, font=self.fs,
                             fill=P["fg"] if run else P["err"] if err else P["sub"])
        left = fmt_eta((t - d) / j.speed) if st == "Downloading" and j.speed > 1 and t else "—"
        rate = f"{human(j.speed)}/s" if st == "Downloading" and j.speed else "—"
        for k, txt in (("left", left), ("rate", rate),
                       ("date", time.strftime("%b %d", time.localtime(j.added)) if j.added else "")):
            self.create_text(X[k], cy, anchor="w", font=self.fs, fill=P["fg"] if k != "date" and txt != "—" else P["sub"],
                             text=fit(txt, self.fs, self.cw(k) - S(8)))

    def redraw(self):
        self.delete("all")
        self.configure(bg=P["bg"])
        W, H, HH = self.winfo_width(), self.winfo_height(), self.HH
        self.off = max(0, min(self.off, max(0, self.total_h() - (H - HH))))
        X = self.cols(W)
        if not self.jobs:
            self.create_text(W // 2, H // 2 + S(10), text="Nothing here yet", fill=P["fg"], font=(FONT, 14, "bold"))
            self.create_text(W // 2, H // 2 + S(40), fill=P["sub"], font=self.fs,
                             text="Click Add URL, copy links anywhere, or drop them onto this window")
        else:
            i0 = max(0, self.off // self.RH)
            i1 = min(len(self.jobs), (self.off + H - HH) // self.RH + 1)
            for i in range(i0, i1):
                self.row(self.jobs[i], HH + i * self.RH - self.off, W, X)
        self.create_rectangle(0, 0, W, HH, fill=P["bg"], outline="")
        self.create_line(0, HH - 1, W, HH - 1, fill=P["border"])
        for k, t in (("name", "FILE NAME"), ("size", "SIZE"), ("status", "STATUS"),
                     ("left", "TIME LEFT"), ("rate", "SPEED"), ("date", "DATE")):
            self.create_text(X[k], HH // 2, anchor="w", text=t, font=self.fh, fill=P["sub"])
        if self.drop:
            rrect(self, S(6), S(6), W - S(6), H - S(6), S(14), fill=mix(P["accent"], P["bg"], .88),
                  outline=P["accent"], width=2, dash=(6, 4))
            self.create_text(W // 2, H // 2, text="Drop links here to download", fill=P["accent"],
                             font=(FONT, 16, "bold"))
        if self.sb:
            T, V = max(self.total_h(), 1), self.view_h()
            self.sb.set(self.off / T, min(1.0, (self.off + V) / T))


class LinkDialog(tk.Toplevel):
    """Give a half-finished download a new link (for sites whose links expire)."""
    def __init__(self, app, job):
        super().__init__(app.root)
        self.app, self.job = app, job
        self.title("Change link")
        self.configure(bg=P["bg"])
        tr(self, app.root)
        f = ttk.Frame(self, padding=S(18))
        f.pack(fill="both", expand=True)
        ttk.Label(f, text=job.name, font=(FONT, 12, "bold")).pack(anchor="w")
        pct = f"{job.done * 100 / job.total:.1f}%" if job.total else human(job.done)
        ttk.Label(f, style="Sub.TLabel", wraplength=S(520), justify="left", text=(
            f"Downloaded so far: {pct}. Paste the new link below. If the new link points to the same file "
            "(same size) the download continues from where it stopped; otherwise it starts again.")).pack(
            anchor="w", pady=(2, S(12)))
        ttk.Label(f, text="New link").pack(anchor="w")
        self.e = ttk.Entry(f, width=72)
        self.e.pack(fill="x", pady=(2, S(10)))
        self.e.insert(0, job.url)
        ttk.Label(f, text="Referer (optional - some sites only accept the link from their own page)").pack(anchor="w")
        self.r = ttk.Entry(f, width=72)
        self.r.pack(fill="x", pady=(2, 0))
        self.r.insert(0, job.referer)
        row = ttk.Frame(f)
        row.pack(fill="x", pady=(S(16), 0))
        ttk.Button(row, text="Save and resume", style="Accent.TButton",
                   command=lambda: self.go(True)).pack(side="right")
        ttk.Button(row, text="Save", command=lambda: self.go(False)).pack(side="right", padx=8)
        ttk.Button(row, text="Cancel", command=self.destroy).pack(side="right")
        self.bind("<Escape>", lambda e: self.destroy())
        self.e.bind("<Return>", lambda e: self.go(True))
        self.update_idletasks()
        r = anchor(app.root)
        self.geometry(f"+{r.winfo_rootx() + max(0, (r.winfo_width() - self.winfo_reqwidth()) // 2)}"
                      f"+{r.winfo_rooty() + S(110)}")
        titlebar(self, P["dark"])
        ontop(self, app)
        keep_size(self, "link", 420, 200)
        self.e.focus_set()
        self.e.select_range(0, "end")

    def go(self, resume):
        url = self.e.get().strip()
        if not url.startswith(("http://", "https://")):
            messagebox.showerror("Change link", "The link must start with http:// or https://", parent=self)
            return
        ref = self.r.get().strip()
        self.destroy()
        self.app.change_link(self.job, url, ref, resume)


class AddDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.title("Add URL")
        self.configure(bg=P["bg"])
        tr(self, app.root)
        f = ttk.Frame(self, padding=S(18))
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="Add download links", font=(FONT, 12, "bold")).pack(anchor="w")
        ttk.Label(f, text="Paste one or more links, one per line.", style="Sub.TLabel").pack(
            anchor="w", pady=(0, S(8)))
        self.t = tk.Text(f, width=64, height=7, wrap="none", relief="flat", bd=0, highlightthickness=1,
                         highlightbackground=P["border"], highlightcolor=P["accent"], bg=P["panel"],
                         fg=P["fg"], insertbackground=P["fg"], font=(FONT, 10), padx=S(8), pady=S(8))
        self.t.pack(fill="both", expand=True)
        try:                                    # start with the links on the clipboard, if any
            self.t.insert("1.0", "\n".join(app.urls_in(app.root.clipboard_get())))
        except tk.TclError:
            pass
        row = ttk.Frame(f)
        row.pack(fill="x", pady=(S(14), 0))
        ttk.Button(row, text="Add", style="Accent.TButton", command=self.go).pack(side="right")
        ttk.Button(row, text="Cancel", command=self.destroy).pack(side="right", padx=8)
        self.bind("<Control-Return>", lambda e: self.go())
        self.bind("<Escape>", lambda e: self.destroy())
        self.update_idletasks()
        r = anchor(app.root)
        self.geometry(f"+{r.winfo_rootx() + max(0, (r.winfo_width() - self.winfo_reqwidth()) // 2)}"
                      f"+{r.winfo_rooty() + S(110)}")
        titlebar(self, P["dark"])
        ontop(self, app)
        keep_size(self, "add", 420, 260)
        self.t.focus_set()

    def go(self):
        text = self.t.get("1.0", "end")
        self.destroy()
        self.app.offer(self.app.urls_in(text))


class Pop(tk.Toplevel):
    def __init__(self, app, job):
        super().__init__(app.root)
        self.app, self.job, self.mode = app, job, None
        self.configure(bg=P["bg"])
        self.protocol("WM_DELETE_WINDOW", self.hide)
        W = S(400)
        body = ttk.Frame(self, padding=(S(16), S(12), S(16), S(12)))
        body.pack(fill="both", expand=True)
        self.name = ttk.Label(body, text=job.name, font=(FONT, 11, "bold"), wraplength=W)
        self.name.pack(anchor="w")
        row = ttk.Frame(body)
        row.pack(fill="x", pady=(S(4), 0))
        self.pct = ttk.Label(row, text="0%", font=(FONT, 18, "bold"))
        self.pct.pack(side="left")
        self.stat = ttk.Label(row, text="", style="Sub.TLabel")
        self.stat.pack(side="right", anchor="s", pady=(0, S(3)))
        self.bar = ttk.Progressbar(body, maximum=100, length=W)
        self.bar.pack(fill="x", pady=(S(4), S(8)))
        self.btns = ttk.Frame(body)
        self.btns.pack(fill="x")
        self.tog = ttk.Label(body, text="", style="Sub.TLabel", cursor="hand2")
        self.tog.pack(anchor="w", pady=(S(8), 0))
        self.tog.bind("<Button-1>", lambda e: self.toggle())
        self.det = ttk.Frame(body)                       # everything else: opens when the user wants it
        u = job.url if len(job.url) < 66 else job.url[:65] + "..."
        ttk.Label(self.det, text=u, style="Sub.TLabel").pack(anchor="w", pady=(S(6), S(8)))
        ttk.Label(self.det, text="Connections", style="Sub.TLabel").pack(anchor="w")
        self.cv = tk.Canvas(self.det, width=W, height=S(14), highlightthickness=0, bd=0)
        self.cv.pack(fill="x", pady=(S(3), S(10)))
        ttk.Label(self.det, text="Speed", style="Sub.TLabel").pack(anchor="w")
        self.gv = tk.Canvas(self.det, width=W, height=S(56), highlightthickness=0, bd=0)
        self.gv.pack(fill="x", pady=(S(3), S(10)))
        grid = ttk.Frame(self.det)
        grid.pack(fill="x")
        self.v = {}
        for i, k in enumerate(("Size", "Downloaded", "Speed", "Time left", "Save to")):
            ttk.Label(grid, text=k, style="Sub.TLabel").grid(row=i, column=0, sticky="nw", pady=S(2))
            self.v[k] = ttk.Label(grid, text="", wraplength=S(300), justify="left")
            self.v[k].grid(row=i, column=1, sticky="w", padx=S(16))
        self.show_details(CFG["pop_open"], first=True)
        self.refresh()
        self.update_idletasks()
        r, n = anchor(app.root), len(app.pops)
        x = r.winfo_rootx() + max(0, (r.winfo_width() - self.winfo_reqwidth()) // 2) + S(28) * n
        y = r.winfo_rooty() + max(0, (r.winfo_height() - self.winfo_reqheight()) // 4) + S(28) * n
        self.geometry(f"+{x}+{y}")
        titlebar(self, P["dark"])
        ontop(self, app)
        keep_size(self, lambda: "pop_l" if CFG["pop_open"] else "pop_s", 320, 120)
        self.attributes("-topmost", True)
        self.after(400, self.untop)

    def show_details(self, on, first=False):
        CFG["pop_open"] = on
        self.tog.config(text=("\u25be  Details" if on else "\u25b8  Details"))
        if on:
            self.det.pack(fill="both", expand=True)
        else:
            self.det.pack_forget()
        if not first:
            self._ks_hold = True
            self._apply_size()
            self.after(300, lambda: setattr(self, "_ks_hold", False))
            self.refresh()

    def toggle(self):
        self.show_details(not CFG["pop_open"])

    def untop(self):
        try:
            self.attributes("-topmost", False)
        except tk.TclError:
            pass

    def hide(self):
        self.app.pops.pop(self.job.id, None)
        self.destroy()

    def retheme(self):
        self.configure(bg=P["bg"])
        titlebar(self, P["dark"])
        self.refresh()

    def set_mode(self, mode):
        if mode == self.mode:
            return
        self.mode = mode
        for w in self.btns.winfo_children():
            w.destroy()
        j, a = self.job, self.app

        def b(text, cmd, side="left", accent=False):
            ttk.Button(self.btns, text=text, command=cmd,
                       style="Accent.TButton" if accent else "TButton").pack(
                side=side, padx=(0, 8) if side == "left" else (8, 0))

        if mode == "run":
            b("\u275a\u275a  Pause", lambda: a.pause_jobs([j]))
            b("Hide", self.hide, "right")
        elif mode == "done":
            b("Open file", lambda: open_path(j.path), accent=True)
            b("Open folder", lambda: open_path(j.path, select=True))
            b("Close", self.hide, "right")
        else:
            b("\u25b6  Resume", lambda: a.start([j]), accent=True)
            b("Close", self.hide, "right")

    def draw(self):
        c, j = self.cv, self.job
        c.delete("all")
        c.configure(bg=P["bar"])
        W, H = max(c.winfo_width(), 10), c.winfo_height()
        if j.total and j.segs:
            T = j.total
            for s, e, p in j.segs:
                x0, x1 = s * W // T, min(W, (e + 1) * W // T)
                end = x1 - 2 if x1 < W else x1
                fill = min(end, x0 + (min(p, e + 1) - s) * W // T)
                c.create_rectangle(x0, 0, end, H, fill=P["border"], outline="")
                if fill > x0:
                    c.create_rectangle(x0, 0, fill, H, fill=P["accent"], outline="")
        g = self.gv
        g.delete("all")
        g.configure(bg=P["bar"])
        W, H = max(g.winfo_width(), 10), g.winfo_height()
        h = j.hist[-90:]
        if len(h) >= 2:
            m = (max(h) * 1.15) or 1
            pts = []
            for i, v in enumerate(h):
                pts += [W - (len(h) - 1 - i) * W / 89, H - 3 - (H - 8) * v / m]
            g.create_polygon([pts[0], H] + pts + [pts[-2], H], fill=mix(P["accent"], P["bar"], .6),
                             outline="")
            g.create_line(pts, fill=P["accent"], width=2, smooth=True)

    def refresh(self):
        j, d = self.job, self.job.done
        done = j.status == "Done"
        pct = 100 if done else (d * 100 // j.total if j.total else 0)
        self.set_mode("run" if j.status in RUNNING else "done" if done else "idle")
        known = bool(j.total) or done
        self.title(f"{pct}%  {j.name}" if known else j.name)
        self.name.config(text=j.name)
        self.pct.config(text=f"{pct}%" if known else "...")
        self.bar.config(value=pct)
        sp = f"{human(j.speed)}/s" if j.speed else ""
        self.stat.config(text="Download complete" if done else j.status + (f"   {sp}" if sp else ""))
        v = self.v
        v["Size"].config(text=human(j.total) if j.total else "Unknown")
        v["Downloaded"].config(text=human(d))
        v["Speed"].config(text=sp or "-")
        v["Time left"].config(text=fmt_eta((j.total - d) / j.speed) if j.speed > 1 and j.total else "-")
        v["Save to"].config(text=j.dir)
        if CFG["pop_open"]:
            self.draw()


class BatchDialog(tk.Toplevel):
    def __init__(self, app, urls, start, ctx=None):
        super().__init__(app.root)
        self.app, self.urls, self.ctx = app, urls, ctx
        self.title("Link found" if len(urls) == 1 else f"{len(urls)} links found")
        self.configure(bg=P["bg"])
        tr(self, app.root)
        f = ttk.Frame(self, padding=S(16))
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="Choose what to download", font=(FONT, 12, "bold")).pack(anchor="w")
        ttk.Label(f, text="Click a row to check or uncheck it.", style="Sub.TLabel").pack(
            anchor="w", pady=(0, S(8)))
        box = ttk.Frame(f)
        box.pack(fill="both", expand=True)
        self.tv = ttk.Treeview(box, columns=("type", "host"), show="tree headings",
                               selectmode="none", height=10)
        self.tv.heading("#0", text="File", anchor="w")
        self.tv.heading("type", text="Type", anchor="w")
        self.tv.heading("host", text="From", anchor="w")
        self.tv.column("#0", width=S(330))
        self.tv.column("type", width=S(110), stretch=False)
        self.tv.column("host", width=S(190), stretch=False)
        sb = ttk.Scrollbar(box, command=self.tv.yview)
        self.tv.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tv.pack(side="left", fill="both", expand=True)
        self.on, self.names = {}, {}
        for i, u in enumerate(urls):
            p = urlparse(u)
            base = os.path.basename(p.path)
            name = clean(unquote(base)) if base else p.netloc
            dup = any(j.url == u for j in JOBS)
            k = str(i)
            self.on[k], self.names[k] = not dup, name
            self.tv.insert("", "end", iid=k, text="", values=(
                category(name), p.netloc + ("  (in list)" if dup else "")))
            self.mark(k)
        self.tv.bind("<Button-1>", self.toggle)
        row = ttk.Frame(f)
        row.pack(fill="x", pady=(S(12), 0))
        ttk.Button(row, text="Select all", command=lambda: self.setall(True)).pack(side="left")
        ttk.Button(row, text="Select none", command=lambda: self.setall(False)).pack(
            side="left", padx=6)
        ttk.Button(row, text="Download selected", style="Accent.TButton",
                   command=lambda: self.go(True)).pack(side="right")
        ttk.Button(row, text="Add to list", command=lambda: self.go(False)).pack(
            side="right", padx=6)
        ttk.Button(row, text="Cancel", command=self.destroy).pack(side="right")
        self.update_idletasks()
        r = anchor(app.root)
        self.geometry(f"+{r.winfo_rootx() + max(0, (r.winfo_width() - self.winfo_reqwidth()) // 2)}"
                      f"+{r.winfo_rooty() + S(70)}")
        titlebar(self, P["dark"])
        ontop(self, app)
        keep_size(self, "batch", 480, 320)
        self.attributes("-topmost", True)
        self.after(400, lambda: self.attributes("-topmost", False))

    def mark(self, k):
        self.tv.item(k, text=("\u2611  " if self.on[k] else "\u2610  ") + self.names[k])

    def toggle(self, e):
        k = self.tv.identify_row(e.y)
        if k:
            self.on[k] = not self.on[k]
            self.mark(k)

    def setall(self, v):
        for k in self.on:
            self.on[k] = v
            self.mark(k)

    def go(self, start):
        chosen = [u for i, u in enumerate(self.urls) if self.on[str(i)]]
        self.destroy()
        if chosen:
            self.app.add_many(chosen, auto=start, ctx=self.ctx)






class Countdown(tk.Toplevel):
    def __init__(self, app, action, why, seconds=30):
        super().__init__(app.root)
        self.app, self.action, self.why, self.n, self.dead = app, action, why, seconds, False
        self.title("MyDM")
        self.configure(bg=P["bg"])
        self.resizable(False, False)
        self.lbl = ttk.Label(self, padding=S(24), font=(FONT, 11), justify="center")
        self.lbl.pack()
        ttk.Button(self, text="Cancel", command=self.cancel).pack(pady=(0, S(16)))
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        a_ = anchor(app.root)
        self.geometry(f"+{a_.winfo_rootx() + S(120)}+{a_.winfo_rooty() + S(120)}")
        titlebar(self, P["dark"])
        ontop(self, app)
        self.attributes("-topmost", True)
        self.step()

    def cancel(self):
        self.dead = True
        self.destroy()

    def step(self):
        if self.dead:
            return
        if self.n <= 0:
            self.destroy()
            cmd = power_cmd(self.action)
            if cmd:
                try:
                    subprocess.Popen(cmd, creationflags=0x08000000 if WIN else 0)
                except Exception:
                    LOG.debug("step", exc_info=True)
            return
        self.lbl.config(text=f"{self.why}\n\n{self.action} in {self.n} s")
        self.n -= 1
        self.after(1000, self.step)


# ======================================================================== app
class App:
    def __init__(self, root):
        self.root = root
        self.pops, self.session, self.flt, self.fired = {}, set(), "all", {}
        self.pending, self.closing, self.dragging = None, False, False
        self.inbox = queue.Queue()
        self.srv = start_listener(self.inbox)
        self.last_clip, self.note, self.note_until, self.ticks = "", "", 0, 0
        setup_log()
        self.front_until, self.tray = 0.0, None
        self.load()
        root.title("MyDM " + VERSION)
        root.geometry(CFG["geom"] or f"{S(1220)}x{S(780)}")
        root.minsize(S(1100), S(520))
        init_fonts(root)
        apply_theme(root)
        self.icon = make_icon()
        root.iconphoto(True, self.icon)
        try:
            self.last_clip = root.clipboard_get()
        except tk.TclError:
            pass

        self.menus, self.lines = [], []
        self.status, self.speed_txt, self.sched_txt = tk.StringVar(), tk.StringVar(), tk.StringVar()

        def line(parent, **pk):
            f = tk.Frame(parent, bd=0, highlightthickness=0, **({"height": 1} if pk.get("fill") == "x" else {"width": 1}))
            f.pack(**pk)
            self.lines.append(f)
            return f

        def menu(items):
            m = tk.Menu(root, tearoff=0)
            for it in items:
                if it is None:
                    m.add_separator()
                else:
                    m.add_command(label=it[0], command=it[1])
            self.menus.append(m)
            return m

        def theme_menu():
            m = tk.Menu(root, tearoff=0)
            for lab, key in (("System", "system"), ("Light", "light"), ("Dark", "dark")):
                m.add_command(label="Theme: " + lab, command=lambda k=key: self.set_theme(k))
            m.add_separator()
            for lab, key in (("Compact", "compact"), ("Comfortable", "comfortable")):
                m.add_command(label="List size: " + lab, command=lambda k=key: self.set_density(k))
            m.add_separator()
            m.add_command(label="Reset layout (columns, sidebar, windows)", command=self.reset_layout)
            self.menus.append(m)
            return m

        stop_running = lambda: self.pause_jobs([j for j in JOBS if j.status in RUNNING])
        stop_queued = lambda: self.pause_jobs([j for j in JOBS if j.status == "Waiting"])
        tasks = menu([("Add URL...", self.add), ("Paste links", self.paste), None,
                      ("Import downloads...", self.imp), ("Export downloads...", self.exp), None,
                      ("Scheduler", self.scheduler), ("Options", self.settings),
                      ("Open log folder", lambda: open_path(str(LOGDIR))), None,
                      ("Exit MyDM", self.close)])
        files = menu([("Open file", self.open_file), ("Open folder", self.openf),
                      ("Show progress window", self.show_pop), ("Copy link", self.copy_link)])
        downs = menu([("Resume (start now)", lambda: self.start(self.selected(), True)),
                      ("Stop", lambda: self.pause_jobs(self.selected())), ("Stop all", stop_running), None,
                      ("Start queue", lambda: self.start(JOBS)), ("Stop queue", stop_queued), None,
                      ("Add to queue", self.enqueue), ("Remove from queue", self.dequeue),
                      ("Download at...", self.schedule_dialog), None,
                      ("Change link...", self.edit_link), ("Download again", self.redo),
                      ("Remove from list", self.remove),
                      ("Remove and delete file", lambda: self.remove(True)), None,
                      ("Clear completed downloads", self.clear)])
        self.menubar = MenuBar(root, [("Tasks", tasks), ("File", files), ("Downloads", downs),
                                      ("View", theme_menu())], self.toggle_theme)
        self.menubar.pack(fill="x")
        line(root, fill="x")

        trow = tk.Frame(root, bd=0, highlightthickness=0)
        trow.pack(fill="x")
        self.trow = trow
        self.tb = Toolbar(trow, {
            "add": self.add, "resume": lambda: self.start(self.selected(), True),
            "stop": lambda: self.pause_jobs(self.selected()), "stopall": stop_running,
            "delete": self.remove, "clear": self.clear, "options": self.settings,
            "sched": self.scheduler, "startq": lambda: self.start(JOBS), "stopq": stop_queued})
        self.search = SearchBox(trow, self.refilter)
        self.search.pack(side="right", padx=(S(8), S(18)), pady=S(20))
        self.tb.pack(side="left", fill="x", expand=True)
        line(root, fill="x")

        foot = tk.Frame(root, bd=0, highlightthickness=0)
        foot.pack(side="bottom", fill="x")
        self.foot = foot
        self.flbls = [tk.Label(foot, textvariable=self.status, bd=0, padx=S(18), pady=S(7), font=(FONT, 9)),
                      tk.Label(foot, textvariable=self.speed_txt, bd=0, padx=S(18), pady=S(7), font=(FONT, 9)),
                      tk.Label(foot, textvariable=self.sched_txt, bd=0, padx=S(4), pady=S(7), font=(FONT, 9))]
        self.flbls[0].pack(side="left")
        self.flbls[1].pack(side="right")
        self.flbls[2].pack(side="right")
        fl = tk.Frame(root, height=1, bd=0, highlightthickness=0)
        fl.pack(side="bottom", fill="x")
        self.lines.append(fl)

        body = tk.Frame(root, bd=0, highlightthickness=0)
        body.pack(fill="both", expand=True)
        self.body = body
        self.side = Side(body, self.set_filter)
        self.side.configure(width=S(CFG.get("side_w", 214)))
        self.side.pack(side="left", fill="y")
        self.split = Splitter(body, self.side_start, self.side_drag)
        self.split.pack(side="left", fill="y")
        right = tk.Frame(body, bd=0, highlightthickness=0)
        right.pack(side="left", fill="both", expand=True)
        self.lst = JobList(right, self)
        self.sb = ttk.Scrollbar(right, command=self.lst.yview)
        self.lst.sb = self.sb
        self.sb.pack(side="right", fill="y")
        self.lst.pack(side="left", fill="both", expand=True)
        root.bind_all("<MouseWheel>", self.wheel)
        root.bind_all("<Button-4>", self.wheel)
        root.bind_all("<Button-5>", self.wheel)
        root.bind("<Control-v>", self.ctrl_v)

        self.menu = tk.Menu(root, tearoff=0)
        for label, cmd in [("Start now", lambda: self.start(self.selected(), True)), ("Pause", self.pause), (None, None),
                           ("Add to queue", self.enqueue), ("Remove from queue", self.dequeue),
                           ("Download at...", self.schedule_dialog), (None, None),
                           ("Open file", self.open_file), ("Open folder", self.openf),
                           ("Show progress window", self.show_pop), ("Copy link", self.copy_link),
                           ("Change link...", self.edit_link),
                           (None, None), ("Download again", self.redo), ("Remove from list", self.remove),
                           ("Remove and delete file", lambda: self.remove(True))]:
            self.menu.add_separator() if not label else self.menu.add_command(
                label=label, command=cmd)

        if TkinterDnD:
            try:
                for w in (root, self.lst, self.side):
                    w.drop_target_register(DND_TEXT, DND_FILES)
                    w.dnd_bind("<<Drop>>", self.on_drop)
                    w.dnd_bind("<<DropEnter>>", lambda e: self.drag(True))
                    w.dnd_bind("<<DropLeave>>", lambda e: self.drag(False))
            except Exception:
                LOG.debug("theme_menu", exc_info=True)

        self.retheme()
        root.protocol("WM_DELETE_WINDOW", self.on_x)
        root.after(80, lambda: titlebar(root, P["dark"]))
        if WIN and CFG["tray"]:
            try:
                ico = DATA / "tray.ico"
                if not ico.exists():
                    make_ico(ico)
                t = Tray("MyDM", str(ico))
                self.tray = t if t.up else None
            except Exception:
                LOG.warning("tray not available", exc_info=True)
        if "--min" in sys.argv:
            if self.tray:
                root.withdraw()
            else:
                root.iconify()
        self.tick()

    # ---- theme ----
    def retheme(self):
        apply_theme(self.root)
        for m in self.menus + [self.menu]:
            m.configure(bg=P["panel"], fg=P["fg"], activebackground=P["sel"],
                        activeforeground=P["accent"], bd=0, relief="flat")
        for f in (self.trow, self.foot, self.body, self.lst.master):
            f.configure(bg=P["bg"])
        for f in self.lines:
            f.configure(bg=P["border"])
        for l in self.flbls:
            l.configure(bg=P["bg"], fg=P["sub"])
        self.menubar.restyle()
        self.split.restyle()
        self.search.restyle()
        titlebar(self.root, P["dark"])
        self.tb.redraw()
        self.side.redraw()
        self.lst.redraw()
        for p in self.pops.values():
            p.retheme()

    def set_theme(self, t):
        CFG["theme"] = t
        self.save()
        self.retheme()

    def toggle_theme(self):
        self.set_theme("light" if P["dark"] else "dark")

    def side_start(self, e):
        self._sw = (e.x_root, self.side.winfo_width())

    def side_drag(self, e):
        w = max(S(150), min(S(420), self._sw[1] + e.x_root - self._sw[0]))
        CFG["side_w"] = round(w / SC)
        self.side.configure(width=w)

    def reset_layout(self):
        CFG["colw"], CFG["side_w"], CFG["wins"] = {}, 214, {}
        self.side.configure(width=S(214))
        self.lst.redraw()
        self.save()
        self.say("Layout reset (window sizes apply the next time they open)")

    def edit_link(self):
        s = self.selected()
        if not s:
            self.say("Select a download first")
        elif s[0].status == "Done":
            self.say("This download is already finished")
        elif s[0].status in ("Connecting", "Downloading"):
            messagebox.showinfo("Change link", "Stop this download first, then change its link.")
        else:
            LinkDialog(self, s[0])

    def change_link(self, j, url, referer, resume):
        if j.status in ("Connecting", "Downloading"):
            return
        j.url, j.referer, j.err = url, referer, None
        if j.status == "Waiting" or j.status.startswith("Error"):
            j.status = "Paused"
        j.prev = j.status
        self.save()
        self.say("Link updated: " + j.name)
        if resume:
            self.start([j])

    def set_density(self, d):
        CFG["density"] = d
        self.save()
        self.lst.redraw()



    def ctrl_v(self, e):
        if not isinstance(self.root.focus_get(), (tk.Entry, tk.Text, ttk.Entry)):
            self.paste()

    def refilter(self):
        self.lst.off = 0
        self.lst.set_jobs([j for j in JOBS if self.match(j)])

    def update_tb(self):
        sel = self.selected()
        can = lambda j: j.status in ("Queued", "Paused", "Off queue", "Scheduled") or j.status.startswith("Error")
        self.tb.enable({"resume": any(can(j) for j in sel), "stop": any(j.status in RUNNING for j in sel),
                        "stopall": any(j.status in RUNNING for j in JOBS), "delete": bool(sel),
                        "clear": any(j.status == "Done" for j in JOBS),
                        "startq": any(can(j) for j in JOBS),
                        "stopq": any(j.status == "Waiting" for j in JOBS)})

    # ---- state ----
    def load(self):
        if not STATE.exists():
            return
        try:
            d = json.loads(STATE.read_text(encoding="utf-8"))
            CFG.update(d.get("cfg", {}))
            CFG["sched"] = {**SCHED, **CFG.get("sched", {})}
            for x in d.get("jobs", []):
                try:
                    JOBS.append(Job.from_dict(x))
                except Exception:
                    LOG.debug("load", exc_info=True)
        except Exception:
            LOG.debug("load", exc_info=True)

    def save(self):
        try:
            DATA.mkdir(exist_ok=True)
            tmp = STATE.with_suffix(".tmp")
            tmp.write_text(json.dumps({"cfg": CFG, "jobs": [j.to_dict() for j in JOBS]}),
                           encoding="utf-8")
            os.replace(tmp, STATE)
        except Exception:
            LOG.debug("save", exc_info=True)

    def close(self):
        self.closing = True
        for j in JOBS:
            j.stop.set()
        try:
            CFG["geom"] = f"{self.root.winfo_width()}x{self.root.winfo_height()}"
        except tk.TclError:
            pass
        try:
            self.srv and self.srv.close()
        except Exception:
            LOG.debug("close", exc_info=True)
        if self.tray:
            self.tray.stop()
        time.sleep(0.4)
        self.save()
        self.root.destroy()

    # ---- adding links ----
    def say(self, text):
        self.note, self.note_until = text, time.time() + 6

    def urls_in(self, text):
        return list(dict.fromkeys(re.findall(r'https?://[^\s<>"\']+', text)))

    def add_many(self, urls, auto=False, ctx=None):
        new = []
        for u in urls:
            if not any(j.url == u for j in JOBS):
                j = Job(u, CFG["folder"])
                if ctx:
                    j.referer, j.ua = ctx.get("referer", ""), ctx.get("ua", "")
                JOBS.append(j)
                new.append(j)
        self.say(f"{len(new)} new link(s) added" if urls else "No valid link found")
        if new:
            self.save()
            if auto:
                self.start(new)
        return new

    def offer(self, urls, start=False):
        urls = list(dict.fromkeys(urls))
        if not urls:
            self.say("No link found")
        elif len(urls) == 1:
            self.add_many(urls, auto=start)
        else:
            BatchDialog(self, urls, start)




    def add(self):
        AddDialog(self)

    def paste(self):
        try:
            self.offer(self.urls_in(self.root.clipboard_get()))
        except tk.TclError:
            self.say("Clipboard is empty")

    def imp(self):
        p = filedialog.askopenfilename(filetypes=[("Text", "*.txt"), ("All", "*.*")])
        if p:
            with open(p, encoding="utf-8", errors="ignore") as f:
                self.offer(self.urls_in(f.read()))

    def exp(self):
        p = filedialog.asksaveasfilename(defaultextension=".txt", filetypes=[("Text", "*.txt")])
        if not p:
            return
        only = messagebox.askyesno("Export", "Export only unfinished links?\n(No = all links)")
        urls = [j.url for j in JOBS if not only or j.status != "Done"]
        with open(p, "w", encoding="utf-8") as f:
            f.write("\n".join(urls))
        self.say(f"{len(urls)} link(s) exported")


    def show_main(self):
        r = self.root
        r.deiconify()
        r.state("normal")
        r.lift()
        r.attributes("-topmost", True)
        r.after(300, lambda: r.attributes("-topmost", False))
        r.focus_force()

    def on_x(self):
        """The window's X button: keep running in the notification area when that is possible."""
        if CFG["tray"] and self.tray and self.tray.up:
            self.root.withdraw()
        else:
            self.close()





    def drag(self, on):
        if on != self.lst.drop:
            self.lst.drop = on
            self.lst.redraw()
        return "copy"

    def on_drop(self, e):
        self.drag(False)
        urls = []
        for item in self.root.tk.splitlist(e.data):
            if os.path.isfile(item):
                if os.path.splitext(item)[1].lower() in (".txt", ".url", ".lst", ".csv", ".html", ".htm"):
                    try:
                        with open(item, encoding="utf-8", errors="ignore") as f:
                            urls += self.urls_in(f.read(2_000_000))
                    except OSError:
                        pass
            else:
                urls += self.urls_in(item)
        if not urls:
            urls = self.urls_in(e.data)
        self.offer(urls, start=True)
        return "copy"

    # ---- list ops ----
    def match(self, j):
        q = self.search.value().lower()
        if q and q not in j.name.lower() and q not in j.url.lower():
            return False
        f = self.flt
        if f == "queue":
            return j.status in ("Queued", "Waiting", "Scheduled")
        if f == "all":
            return True
        if f == "unfinished":
            return j.status != "Done"
        if f == "completed":
            return j.status == "Done"
        return j.cat == f[2:]

    def set_filter(self, key):
        self.flt = key
        self.lst.sel.clear()
        self.lst.off = 0
        self.lst.set_jobs([j for j in JOBS if self.match(j)])

    def selected(self):
        return self.lst.selected()

    def start(self, jobs=None, force=False):
        """force=True is a manual start: it runs even if the parallel limit is already used."""
        ok = ("Queued", "Paused", "Off queue", "Scheduled") if force else ("Queued", "Paused")
        for j in (jobs if jobs is not None else (self.selected() or JOBS)):
            if j.status == "Waiting" and force:
                j.force = True
            elif j.status in ok or j.status.startswith("Error"):
                j.status, j.at, j.force = "Waiting", 0.0, force
                j.prev = j.status

    def enqueue(self, jobs=None):
        """put files (back) into the queue: they start by themselves when a slot is free"""
        n = 0
        for j in (jobs if jobs is not None else self.selected()):
            if j.status in ("Off queue", "Scheduled", "Paused") or j.status.startswith("Error"):
                j.status, j.at, j.force = "Waiting", 0.0, False
                n += 1
        n and self.say(f"{n} file(s) added to the queue")

    def dequeue(self, jobs=None):
        """take files out of the queue (a running one stops, what is downloaded is kept)"""
        n = 0
        for j in (jobs if jobs is not None else self.selected()):
            if j.status != "Done":
                if j.status in RUNNING:
                    j.stop.set()
                j.status, j.at, j.force = "Off queue", 0.0, False
                j.prev = j.status
                n += 1
        self.save()
        n and self.say(f"{n} file(s) taken out of the queue")

    def schedule_dialog(self):
        jobs = [j for j in self.selected() if j.status != "Done"]
        if not jobs:
            return
        w = tk.Toplevel(self.root)
        w.title("Download at...")
        tr(w, self.root)
        w.configure(bg=P["bg"])
        f = ttk.Frame(w, padding=S(18))
        f.pack(fill="both", expand=True)
        ttk.Label(f, text=f"Start {len(jobs)} file(s) at:", font=(FONT, 11, "bold")).grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0, S(10)))
        first = jobs[0].at or time.time() + 3600
        h = tk.StringVar(value=time.strftime("%H", time.localtime(first)))
        m = tk.StringVar(value=time.strftime("%M", time.localtime(first)))
        ttk.Spinbox(f, from_=0, to=23, width=3, wrap=True, format="%02.0f", textvariable=h).grid(row=1, column=0)
        ttk.Label(f, text=":").grid(row=1, column=1)
        ttk.Spinbox(f, from_=0, to=59, width=3, wrap=True, format="%02.0f", textvariable=m).grid(row=1, column=2)
        ttk.Label(f, style="Sub.TLabel", wraplength=S(300), justify="left", text=(
            "If that time has already passed today, it means tomorrow. MyDM must be running "
            "(it can stay in the notification area) and the PC awake.")).grid(
            row=2, column=0, columnspan=4, sticky="w", pady=(S(10), 0))

        def ok():
            try:
                hh, mm = min(23, max(0, int(h.get()))), min(59, max(0, int(m.get())))
            except ValueError:
                return
            n = datetime.now().replace(hour=hh, minute=mm, second=0, microsecond=0)
            t = n.timestamp()
            if t <= time.time():
                t += 86400
            for j in jobs:
                if j.status in RUNNING:
                    j.stop.set()
                j.status, j.at, j.force = "Scheduled", t, False
                j.prev = j.status
            self.save()
            self.say(f"{len(jobs)} file(s) will start at {hh:02d}:{mm:02d}")
            w.destroy()

        row = ttk.Frame(f)
        row.grid(row=3, column=0, columnspan=4, sticky="e", pady=(S(14), 0))
        ttk.Button(row, text="Cancel", command=w.destroy).pack(side="left", padx=6)
        ttk.Button(row, text="OK", style="Accent.TButton", command=ok).pack(side="left")
        self.place_dialog(w, "at")

    def pause_jobs(self, jobs):
        for j in jobs:
            if j.status != "Done":
                j.stop.set()
                if j.status == "Waiting":
                    j.status = "Paused"
        self.save()

    def pause(self):
        self.pause_jobs(self.selected() or JOBS)

    def remove(self, files=False):
        sel = self.selected()
        if files and sel and not messagebox.askyesno(
                "Delete", f"Delete {len(sel)} file(s) from disk too?"):
            return
        for j in sel:
            j.stop.set()
            if j.path:
                victim = j.path + ".part" if j.status != "Done" else (j.path if files else None)
                try:
                    victim and os.remove(victim)
                except OSError:
                    pass
            if j.id in self.pops:
                self.pops[j.id].hide()
            JOBS.remove(j)
        self.save()

    def clear(self):
        for j in [j for j in JOBS if j.status == "Done"]:
            JOBS.remove(j)
        self.save()

    def redo(self):
        for j in self.selected():
            if j.status not in RUNNING:
                if j.path and j.status != "Done":
                    try:
                        os.remove(j.path + ".part")
                    except OSError:
                        pass
                j.segs, j.path, j.speed = [], "", 0.0
                j.status = "Waiting"

    def openf(self):
        s = self.selected()
        if s and s[0].path and os.path.exists(s[0].path):
            open_path(s[0].path, select=True)
        else:
            open_path(s[0].dir if s else CFG["folder"])

    def open_file(self):
        s = self.selected()
        if s and s[0].status == "Done" and os.path.exists(s[0].path):
            open_path(s[0].path)

    def copy_link(self):
        s = self.selected()
        if s:
            t = "\n".join(j.url for j in s)
            self.root.clipboard_clear()
            self.root.clipboard_append(t)
            self.last_clip = t

    def show_pop(self):
        for j in self.selected()[:3]:
            self.open_pop(j)

    def open_pop(self, j):
        if j.id in self.pops:
            self.pops[j.id].lift()
            return
        while len(self.pops) >= 4:
            old = next((p for p in self.pops.values() if p.job.status not in RUNNING), None)
            if not old:
                return
            old.hide()
        self.pops[j.id] = Pop(self, j)

    def row_action(self, act, jid):
        j = next((x for x in JOBS if x.id == jid), None)
        if not j:
            return
        if act == "pause":
            self.pause_jobs([j])
        elif act == "start":
            self.start([j], True)
        elif act == "open":
            open_path(j.path)
        elif act == "folder":
            open_path(j.path, select=True) if j.path else open_path(j.folder)
        elif act == "details":
            self.open_pop(j)

    def dbl(self, j):
        if j.status == "Done" and os.path.exists(j.path):
            open_path(j.path)
        elif j.status in RUNNING:
            self.open_pop(j)
        else:
            self.start([j], True)

    def wheel(self, e):
        try:
            w = self.root.winfo_containing(e.x_root, e.y_root)
        except (KeyError, tk.TclError):
            return
        if w is self.lst:
            d = -1 if (getattr(e, "num", 0) == 4 or e.delta > 0) else 1
            self.lst.scroll_px(d * self.lst.RH // 2)

    # ---- settings ----

    def settings(self, tab=None):
        w = tk.Toplevel(self.root)
        w.title("Settings")
        tr(w, self.root)
        w.configure(bg=P["bg"])
        outer = ttk.Frame(w, padding=(S(16), S(14), S(16), S(14)))
        outer.pack(fill="both", expand=True)
        nb = ttk.Notebook(outer)
        nb.pack(fill="both", expand=True)
        vs = {}

        def page(title):
            f = ttk.Frame(nb, padding=(S(14), S(14), S(14), S(10)))
            nb.add(f, text=title)
            return f

        def field(f, r, label, key, width=30):
            ttk.Label(f, text=label).grid(row=r, column=0, sticky="w", pady=S(4), padx=(0, S(14)))
            vs[key] = tk.StringVar(value=str(CFG[key]))
            ttk.Entry(f, textvariable=vs[key], width=width).grid(row=r, column=1, sticky="we")

        def check(f, r, label, key):
            vs[key] = tk.BooleanVar(value=CFG[key])
            ttk.Checkbutton(f, text=label, variable=vs[key]).grid(
                row=r, column=0, columnspan=3, sticky="w", pady=S(3))

        def combo(f, r, label, key, values, value, width=26):
            ttk.Label(f, text=label).grid(row=r, column=0, sticky="w", pady=S(4), padx=(0, S(14)))
            vs[key] = tk.StringVar(value=value)
            ttk.Combobox(f, textvariable=vs[key], state="readonly", width=width, values=values).grid(
                row=r, column=1, sticky="w")

        g = page("General")
        field(g, 0, "Download folder", "folder")
        ttk.Button(g, text="...", width=3, command=lambda: vs["folder"].set(
            filedialog.askdirectory() or vs["folder"].get())).grid(row=0, column=2, padx=(S(6), 0))
        check(g, 1, "Sort files into category folders (Video, Music, ...)", "subfolders")
        check(g, 3, "Play a sound when a download completes", "sound")
        combo(g, 4, "Theme", "theme", ["System", "Light", "Dark"], CFG["theme"].capitalize(), 12)
        combo(g, 6, "Download list size", "density", ["Compact", "Comfortable"],
              CFG.get("density", "compact").capitalize(), 12)
        if WIN:
            check(g, 7, "Keep running in the notification area when the window is closed (X)", "tray")
        else:
            vs["tray"] = tk.BooleanVar(value=CFG["tray"])
        vs["startup"] = tk.BooleanVar(value=False)
        if WIN and getattr(sys, "frozen", False):
            ttk.Checkbutton(g, text="Start MyDM with Windows (needed for scheduled downloads)",
                            variable=vs["startup"]).grid(row=5, column=0, columnspan=3, sticky="w")

        c = page("Connection")
        field(c, 0, "Segments per file (1-32)", "segments", 8)
        field(c, 1, "Parallel downloads (1-10)", "parallel", 8)
        field(c, 2, "Speed limit KB/s (0 = unlimited)", "limit_kb", 8)
        modes = {"system": "System proxy (automatic)", "direct": "No proxy", "manual": "Manual"}
        combo(c, 3, "Proxy", "mode", list(modes.values()), modes[CFG["proxy_mode"]])
        ttk.Label(c, style="Sub.TLabel", text="System proxy detected: " + (system_proxy() or "none")
                  ).grid(row=4, column=1, sticky="w")
        field(c, 5, "Manual proxy", "proxy")
        ttk.Label(c, style="Sub.TLabel", text="http://host:port   or   socks5://host:port").grid(
            row=6, column=1, sticky="w")

        b = page("Clipboard")
        check(b, 1, "Watch the clipboard for download links", "watch")
        clips = {"ask": "Ask me (show a window)", "add": "Add to the list automatically"}
        combo(b, 2, "When a link is found", "clip_mode", list(clips.values()), clips[CFG["clip_mode"]], 28)
        check(b, 3, "Also start the download when links are added automatically", "autostart")

        def ok():
            try:
                seg = min(32, max(1, int(vs["segments"].get())))
                par = min(10, max(1, int(vs["parallel"].get())))
                lim = max(0, int(vs["limit_kb"].get()))
            except ValueError:
                messagebox.showerror("Settings", "Numeric fields must be numbers.")
                return
            CFG.update(segments=seg, parallel=par, limit_kb=lim,
                       folder=vs["folder"].get().strip() or CFG["folder"],
                       proxy=vs["proxy"].get().strip(), theme=vs["theme"].get().lower(), density=vs["density"].get().lower(),
                       proxy_mode=next(k for k, x in modes.items() if x == vs["mode"].get()),
                       subfolders=vs["subfolders"].get(),
                       sound=vs["sound"].get(), watch=vs["watch"].get(),
                       autostart=vs["autostart"].get(), tray=vs["tray"].get(),
                       clip_mode=next(k for k, x in clips.items() if x == vs["clip_mode"].get()))
            if WIN and getattr(sys, "frozen", False):
                set_startup(vs["startup"].get())
            self.save()
            self.retheme()
            self.lst.redraw()
            w.destroy()

        row = ttk.Frame(outer)
        row.pack(fill="x", pady=(S(12), 0))
        ttk.Button(row, text="Save", style="Accent.TButton", command=ok).pack(side="right")
        ttk.Button(row, text="Cancel", command=w.destroy).pack(side="right", padx=8)
        self.place_dialog(w, "settings")

    def place_dialog(self, w, key=None):
        w.update_idletasks()
        r = anchor(self.root)
        w.geometry(f"+{r.winfo_rootx() + max(0, (r.winfo_width() - w.winfo_reqwidth()) // 2)}"
                   f"+{r.winfo_rooty() + S(50)}")
        titlebar(w, P["dark"])
        ontop(w, self)
        if key:
            keep_size(w, key, 380, 260)
        w.grab_set()

    # ---- scheduler ----
    def scheduler(self):
        s = CFG["sched"]
        w = tk.Toplevel(self.root)
        w.title("Scheduler")
        tr(w, self.root)
        w.configure(bg=P["bg"])
        f = ttk.Frame(w, padding=S(18))
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="Scheduler", font=(FONT, 13, "bold")).grid(row=0, column=0, columnspan=4,
                                                                    sticky="w", pady=(0, S(10)))
        v = {k: tk.BooleanVar(value=s[k]) for k in ("start_on", "stop_on", "daily", "on_finish")}
        tv = {}

        def time_row(r, label, key, onkey):
            ttk.Checkbutton(f, text=label, variable=v[onkey]).grid(row=r, column=0, sticky="w", pady=S(5))
            hh, mm = s[key].split(":")
            h = tk.StringVar(value=hh)
            m = tk.StringVar(value=mm)
            ttk.Spinbox(f, from_=0, to=23, width=3, wrap=True, format="%02.0f",
                        textvariable=h).grid(row=r, column=1, padx=(S(12), 0))
            ttk.Label(f, text=":").grid(row=r, column=2)
            ttk.Spinbox(f, from_=0, to=59, width=3, wrap=True, format="%02.0f",
                        textvariable=m).grid(row=r, column=3)
            tv[key] = (h, m)

        time_row(1, "Start downloads at", "start", "start_on")
        time_row(2, "Stop downloads at", "stop", "stop_on")
        ttk.Checkbutton(f, text="Repeat every day", variable=v["daily"]).grid(
            row=3, column=0, columnspan=4, sticky="w", pady=S(5))
        ttk.Label(f, text="When the stop time arrives, or all\ndownloads finish, do this:").grid(
            row=4, column=0, columnspan=4, sticky="w", pady=(S(10), S(4)))
        act = tk.StringVar(value=s["action"])
        ttk.Combobox(f, textvariable=act, values=ACTIONS, state="readonly", width=30).grid(
            row=5, column=0, columnspan=4, sticky="w")
        ttk.Checkbutton(f, text="Also do it when all downloads finish", variable=v["on_finish"]
                        ).grid(row=6, column=0, columnspan=4, sticky="w", pady=S(8))
        ttk.Label(f, style="Sub.TLabel", wraplength=S(360), justify="left",
                  text="MyDM must be running and the PC awake at the start time. Power actions "
                       "show a 30 second countdown you can cancel.").grid(
            row=7, column=0, columnspan=4, sticky="w")

        def ok():
            for k in ("start", "stop"):
                h, m = tv[k]
                try:
                    s[k] = f"{min(23, max(0, int(h.get()))):02d}:{min(59, max(0, int(m.get()))):02d}"
                except ValueError:
                    pass
            for k in v:
                s[k] = v[k].get()
            s["action"] = act.get()
            self.fired.clear()
            self.save()
            w.destroy()

        row = ttk.Frame(f)
        row.grid(row=8, column=0, columnspan=4, sticky="e", pady=(S(16), 0))
        ttk.Button(row, text="Cancel", command=w.destroy).pack(side="left", padx=6)
        ttk.Button(row, text="Save", style="Accent.TButton", command=ok).pack(side="left")
        self.place_dialog(w, "sched")

    def sched_summary(self):
        s, parts = CFG["sched"], []
        if s["start_on"]:
            parts.append("start " + s["start"])
        if s["stop_on"]:
            parts.append("stop " + s["stop"])
        if s["action"] != "Do nothing" and (s["stop_on"] or s["on_finish"]):
            parts.append("then " + s["action"].lower())
        return ("\u23f0 " + ", ".join(parts)) if parts else ""

    def run_action(self, action, why):
        if action == "Do nothing":
            return
        self.save()
        if action == "Exit MyDM":
            self.close()
        elif action == "Lock screen":
            cmd = power_cmd(action)
            cmd and subprocess.Popen(cmd, creationflags=0x08000000 if WIN else 0)
        else:
            Countdown(self, action, why)

    def sched_check(self):
        s = CFG["sched"]
        n = datetime.now()
        hm, today = n.strftime("%H:%M"), n.strftime("%Y%m%d")
        if s["start_on"] and hm == s["start"] and self.fired.get("start") != today:
            self.fired["start"] = today
            jobs = [j for j in JOBS if j.status != "Done"]
            self.start(jobs)
            self.say(f"Scheduler: started {len(jobs)} download(s)")
            if not s["daily"]:
                s["start_on"] = False
                self.save()
        if s["stop_on"] and hm == s["stop"] and self.fired.get("stop") != today:
            self.fired["stop"] = today
            self.pause_jobs([j for j in JOBS if j.status in RUNNING])
            self.say("Scheduler: stopped downloads")
            self.pending = (s["action"], "Scheduled stop time reached")
            if not s["daily"]:
                s["stop_on"] = False
                self.save()

    # ---- main loop ----
    def tick(self):
        if self.closing:
            return
        now = time.monotonic()
        self.ticks += 1
        self.sched_check()
        while True:
            try:
                self.inbox.get_nowait()
                self.show_main()
            except queue.Empty:
                break
        while self.tray:
            try:
                cmd = self.tray.q.get_nowait()
            except queue.Empty:
                break
            if cmd == "open":
                self.show_main()
            elif cmd == "exit":
                self.close()
                return
        for j in JOBS:
            if j.status == "Scheduled" and time.time() >= j.at:
                j.status, j.at = "Waiting", 0.0
                self.say("Scheduled download started: " + j.name)
        active = [j for j in JOBS if j.status in ("Connecting", "Downloading")]
        for j in JOBS:
            if j.status == "Waiting" and (j.force or len(active) < CFG["parallel"]):
                j.force = False
                j.status = "Connecting"
                j.stop.clear()
                j.speed, j.hist, j.last = 0.0, [], (now, j.done)
                self.session.add(j.id)
                threading.Thread(target=j.run, daemon=True).start()
                active.append(j)

        if CFG["watch"]:
            try:
                t = self.root.clipboard_get()
            except tk.TclError:
                t = ""
            if t and t != self.last_clip:
                self.last_clip = t
                urls = self.urls_in(t)
                known = [u for u in urls if os.path.splitext(urlparse(u).path)[1].lstrip(".").lower() in EXTS]
                auto = CFG["clip_mode"] == "add"
                if len(urls) == 1 and known:
                    if auto:
                        self.add_many(known, auto=CFG["autostart"])
                    elif not any(j.url == known[0] for j in JOBS):
                        BatchDialog(self, known, False)          # ask: add it or not?
                elif len(urls) > 1:
                    if auto:
                        self.add_many(urls, auto=CFG["autostart"])
                    else:
                        BatchDialog(self, urls, False)

        if self.ticks % 10 == 0 and CFG["theme"] == "system" and system_dark() != P["dark"]:
            self.retheme()

        total_speed = 0.0
        counts = {k: 0 for k in ["all", "unfinished", "completed", "queue"] + ["c:" + c for c in CAT_LIST]}
        for j in JOBS:
            d = j.done
            t0, d0 = j.last
            if j.status == "Downloading":
                if now - t0 >= 1:
                    j.speed, j.last = max(0.0, (d - d0) / (now - t0)), (now, d)
                    j.hist = (j.hist + [j.speed])[-120:]
            else:
                j.speed = 0.0
            total_speed += j.speed
            counts["all"] += 1
            counts["completed" if j.status == "Done" else "unfinished"] += 1
            counts["c:" + j.cat] += 1
            counts["queue"] += j.status in ("Queued", "Waiting", "Scheduled")
            if j.status != j.prev:
                if j.status.startswith("Error"):
                    LOG.warning("%s -> %s   %s", j.prev, j.status, j.url)
                else:
                    LOG.info("%s -> %s   %s", j.prev, j.status, j.name)
                if j.status == "Done" and CFG["sound"]:
                    try:
                        import winsound
                        winsound.MessageBeep()
                    except Exception:
                        LOG.debug("tick", exc_info=True)
                        self.root.bell()
                j.prev = j.status
        self.side.set_counts(counts)
        self.lst.set_jobs([j for j in JOBS if self.match(j)])
        for jid, p in list(self.pops.items()):
            try:
                p.refresh()
            except tk.TclError:
                self.pops.pop(jid, None)

        busy = any(j.status in RUNNING for j in JOBS)
        if not busy and self.session:
            mine = [j for j in JOBS if j.id in self.session]
            self.session.clear()
            s = CFG["sched"]
            if mine and all(j.status == "Done" for j in mine) and s["on_finish"] \
                    and s["action"] != "Do nothing":
                self.pending = (s["action"], "All downloads finished")
                if not (s["start_on"] or s["stop_on"]):
                    s["on_finish"] = False
                    self.save()
        if self.pending and not busy:
            act, why = self.pending
            self.pending = None
            self.run_action(act, why)

        info = f"{len(JOBS)} items   {len(active)} active"
        sel = self.selected()
        if len(sel) == 1 and sel[0].status.startswith("Error"):
            info += "   -   " + sel[0].status
        elif time.time() < self.note_until:
            info += "   -   " + self.note
        self.status.set(info)
        self.speed_txt.set(f"\u2193 {human(total_speed)}/s")
        self.sched_txt.set(self.sched_summary())
        self.update_tb()
        if self.ticks % 10 == 0:
            self.save()
        self.root.after(500, self.tick)


if __name__ == "__main__":
    if "--again" not in sys.argv and already_running():
        sys.exit(0)
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        LOG.debug("tick", exc_info=True)
    try:
        root = BaseTk()
    except Exception:          # drag-and-drop library failed to load
        TkinterDnD = None
        root = tk.Tk()
    SC = max(1.0, root.winfo_fpixels("1i") / 96)
    setup_log()
    sys.excepthook = log_crash
    threading.excepthook = lambda a: log_crash(a.exc_type, a.exc_value, a.exc_traceback)
    root.report_callback_exception = log_crash
    App(root)
    root.mainloop()
