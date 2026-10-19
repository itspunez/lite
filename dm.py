"""MyDM Lite 4.1 - a small, plain download manager.
Multi-connection downloads, resume, queue (and taking files out of the queue and back in).
Standard library only."""
import http.client, json, os, re, sys, threading, time, uuid, urllib.request, urllib.parse, urllib.error
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

VERSION = "4.1"
DATA = os.path.join(os.path.expanduser("~"), ".mydm")
STATE = os.path.join(DATA, "lite.json")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.1 Safari/537.36"
MIN_SEG = 1 << 20

DEFAULT = {"folder": os.path.join(os.path.expanduser("~"), "Downloads"),
           "segments": 8, "parallel": 3, "retries": 8}
CFG = dict(DEFAULT)

# statuses
QUEUED, RUNNING, OFF, DONE, ERROR = "Queued", "Downloading", "Off queue", "Done", "Error"


def fmt_size(n):
    if n is None or n < 0:
        return "?"
    n = float(n)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return f"{int(n)} B" if u == "B" else f"{n:.1f} {u}"
        n /= 1024


def clean_name(n):
    n = re.sub(r'[\\/:*?"<>|\r\n]', "_", n).strip().strip(".")
    return n[:150] or "download"


def find_urls(text):
    out = []
    for u in re.findall(r"https?://[^\s\"'<>]+", text):
        u = u.rstrip(".,);]")
        if u not in out:
            out.append(u)
    return out


class Stop(Exception):
    pass


class Fatal(IOError):
    pass


class Seg:
    def __init__(self, start, end, pos, fin=False):
        self.start, self.end, self.pos, self.fin = start, end, pos, fin


class Job:
    def __init__(self, url, folder, jid=None):
        self.id = jid or uuid.uuid4().hex[:10]
        self.url = url
        self.folder = folder
        self.name = ""
        self.total = -1
        self.ranged = False
        self.status = QUEUED
        self.msg = ""
        self.segs = []
        self.done = 0
        self.speed = 0.0
        self.gen = 0
        self.stop = False
        self.fail = None
        self.conns = []
        self.thread = None
        self.lock = threading.Lock()

    @property
    def path(self):
        return os.path.join(self.folder, self.name)

    def pct(self):
        return min(100, int(self.done * 100 / self.total)) if self.total > 0 else 0

    def to_json(self):
        return {"id": self.id, "url": self.url, "folder": self.folder, "name": self.name,
                "total": self.total, "ranged": self.ranged, "status": self.status,
                "segs": [[s.start, s.end, s.pos, s.fin] for s in self.segs]}

    @staticmethod
    def from_json(d):
        j = Job(d["url"], d.get("folder", CFG["folder"]), d.get("id"))
        j.name = d.get("name", "")
        j.total = d.get("total", -1)
        j.ranged = d.get("ranged", False)
        j.status = d.get("status", OFF)
        if j.status == RUNNING:                 # was interrupted: back to the queue
            j.status = QUEUED
        j.segs = [Seg(*x) for x in d.get("segs", [])]
        j.done = j.total if j.status == DONE else sum(s.pos - s.start for s in j.segs)
        return j


class Engine:
    def __init__(self):
        self.jobs = []
        self.last_save = 0
        self.load()

    # ---- state
    def load(self):
        try:
            with open(STATE, encoding="utf-8") as f:
                d = json.load(f)
            CFG.update(d.get("cfg", {}))
            self.jobs = [Job.from_json(x) for x in d.get("jobs", [])]
        except Exception:
            pass

    def save(self):
        try:
            os.makedirs(DATA, exist_ok=True)
            tmp = STATE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"cfg": CFG, "jobs": [j.to_json() for j in self.jobs]}, f)
            os.replace(tmp, STATE)
            self.last_save = time.time()
        except Exception:
            pass

    # ---- queue control
    def add(self, url, folder=None):
        j = Job(url, folder or CFG["folder"])
        self.jobs.append(j)
        self.save()
        self.pump()
        return j

    def enqueue(self, j):
        """put a job (back) into the queue"""
        if j.status in (OFF, ERROR):
            j.status, j.msg, j.fail = QUEUED, "", None
            self.save()
            self.pump()

    def dequeue(self, j):
        """take a job out of the queue; partial data is kept"""
        if j.status in (QUEUED, RUNNING, ERROR):
            j.stop = True
            j.status, j.msg, j.speed = OFF, "", 0
            for c in list(j.conns):
                try:
                    c.close()
                except Exception:
                    pass
            self.save()
            self.pump()

    def remove(self, j, delete_file=False):
        self.dequeue(j)
        if j in self.jobs:
            self.jobs.remove(j)
        for p in (j.path + ".part",) + ((j.path,) if delete_file else ()):
            try:
                if j.name:
                    os.remove(p)
            except OSError:
                pass
        self.save()

    def pump(self):
        running = sum(1 for j in self.jobs if j.status == RUNNING)
        for j in self.jobs:
            if running >= CFG["parallel"]:
                break
            if j.status == QUEUED:
                self.start(j)
                running += 1

    def start(self, j):
        j.gen += 1
        j.stop, j.fail, j.msg = False, None, ""
        j.status = RUNNING
        prev, g = j.thread, j.gen
        def go():
            if prev:
                prev.join(5)
            self.run(j, g)
        j.thread = threading.Thread(target=go, daemon=True)
        j.thread.start()

    # ---- worker
    def alive(self, j, g):
        return not j.stop and j.gen == g and j.fail is None

    def backoff(self, j, g, k, e):
        if not self.alive(j, g):
            raise Stop()
        if isinstance(e, Fatal) or k >= CFG["retries"]:
            raise e
        wait = min(60, 2 << min(k, 5))
        for s in range(wait, 0, -1):
            if not self.alive(j, g):
                raise Stop()
            j.msg = f"Connection lost - retry in {s}s ({k + 1}/{CFG['retries']})"
            j.speed = 0
            time.sleep(1)
        j.msg = ""
        return k + 1

    def open(self, j, rng=None):
        h = {"User-Agent": UA, "Accept-Encoding": "identity", "Accept": "*/*"}
        if rng:
            h["Range"] = rng
        try:
            r = urllib.request.urlopen(urllib.request.Request(j.url, headers=h), timeout=15)
        except urllib.error.HTTPError as e:
            raise (Fatal if e.code in (400, 401, 403, 404, 410) else IOError)(f"HTTP {e.code}")
        except ValueError as e:
            raise Fatal(str(e))
        j.conns.append(r)
        return r

    def drop(self, j, r):
        try:
            j.conns.remove(r)
        except ValueError:
            pass
        try:
            r.close()
        except Exception:
            pass

    def guess_name(self, r):
        cd = r.headers.get("Content-Disposition") or ""
        m = re.search(r"filename\*\s*=\s*[^']*''([^;]+)", cd, re.I)
        if m:
            return clean_name(urllib.parse.unquote(m.group(1).strip()))
        m = re.search(r'filename\s*=\s*"?([^";]+)"?', cd, re.I)
        if m:
            return clean_name(m.group(1).strip())
        return clean_name(urllib.parse.unquote(urllib.parse.urlparse(r.geturl()).path.rsplit("/", 1)[-1]))

    def probe(self, j, g):
        k = 0
        while True:
            try:
                r = self.open(j, "bytes=0-0")
                total, ranged = -1, False
                if r.status == 206:
                    m = re.search(r"/(\d+)", r.headers.get("Content-Range", ""))
                    if m:
                        total, ranged = int(m.group(1)), True
                else:
                    total = int(r.headers.get("Content-Length") or -1)
                if not j.name:
                    j.name = self.guess_name(r)
                self.drop(j, r)
                j.total, j.ranged = total, ranged
                return
            except (IOError, OSError, http.client.HTTPException) as e:
                k = self.backoff(j, g, k, e)

    def plan(self, j):
        j.done = 0
        if j.ranged:
            n = int(max(1, min(CFG["segments"], j.total // MIN_SEG)))
            size = j.total // n
            j.segs = [Seg(i * size, j.total - 1 if i == n - 1 else (i + 1) * size - 1, i * size)
                      for i in range(n)]
        else:
            j.segs = [Seg(0, -1, 0)]

    def run(self, j, g):
        try:
            if not j.segs:
                self.probe(j, g)
                os.makedirs(j.folder, exist_ok=True)
                base, n = j.name, 1
                while (os.path.exists(j.path) or any(o is not j and o.name == j.name and o.folder == j.folder
                                                     for o in self.jobs)):
                    stem, dot, e = base.rpartition(".")
                    j.name = f"{stem} ({n}).{e}" if dot else f"{base} ({n})"
                    n += 1
                self.plan(j)
            os.makedirs(j.folder, exist_ok=True)
            part = j.path + ".part"
            if not j.ranged:
                for s in j.segs:
                    s.pos, s.fin = 0, False
                j.done = 0
            if not os.path.exists(part) or (j.ranged and os.path.getsize(part) != j.total):
                with open(part, "wb") as f:
                    if j.ranged:
                        f.truncate(j.total)
            self.save()
            ths = []
            for s in j.segs:
                if not s.fin:
                    t = threading.Thread(target=self.seg, args=(j, g, s, part), daemon=True)
                    t.start()
                    ths.append(t)
            win = [(time.time(), j.done)]
            while any(t.is_alive() for t in ths):
                time.sleep(0.5)
                now = time.time()
                win.append((now, j.done))
                while len(win) > 2 and now - win[0][0] > 4:
                    win.pop(0)
                j.speed = (win[-1][1] - win[0][1]) / max(0.001, win[-1][0] - win[0][0])
                if not self.alive(j, g):
                    j.speed = 0
                if now - self.last_save > 3:
                    self.save()
            if j.gen != g:
                return
            if j.fail is not None:
                j.status, j.msg, j.speed = ERROR, str(j.fail) or type(j.fail).__name__, 0
                self.save()
                self.pump()
                return
            if j.stop:
                return
            if j.total > 0 and os.path.getsize(part) < j.total:
                raise IOError("Incomplete file")
            os.replace(part, j.path)
            j.done, j.msg, j.speed, j.status = max(j.total, j.done), "", 0, DONE
            self.save()
            self.pump()
        except Stop:
            pass
        except BaseException as e:
            if j.gen == g and not j.stop:
                j.status, j.msg, j.speed = ERROR, str(e) or type(e).__name__, 0
                self.save()
                self.pump()

    def seg(self, j, g, s, part):
        k = 0
        try:
            while self.alive(j, g) and not s.fin:
                if s.end >= 0 and s.pos > s.end:
                    s.fin = True
                    break
                r = None
                got = s.pos
                try:
                    rng = f"bytes={s.pos}-{s.end if s.end >= 0 else ''}" if j.ranged else None
                    r = self.open(j, rng)
                    if j.ranged and r.status != 206:
                        raise Fatal("Server ignored range request")
                    with open(part, "r+b") as f:
                        f.seek(s.pos)
                        while True:
                            b = r.read(65536)
                            if not b:
                                break
                            if not self.alive(j, g):
                                raise Stop()
                            f.write(b)
                            s.pos += len(b)
                            j.done += len(b)
                            k = 0
                            if s.end >= 0 and s.pos > s.end:
                                break
                    if s.end < 0 or s.pos > s.end:
                        s.fin = True
                    elif self.alive(j, g):
                        continue           # server closed the connection early: just reconnect
                except (IOError, OSError, ValueError, http.client.HTTPException) as e:
                    if s.pos > got and self.alive(j, g) and not isinstance(e, Fatal):
                        k = 0              # it was working: reconnect at once, no waiting
                    else:
                        k = self.backoff(j, g, k, e)
                finally:
                    if r is not None:
                        self.drop(j, r)
        except Stop:
            pass
        except BaseException as e:
            if j.gen == g and not j.stop and j.fail is None:
                j.fail = e


# ------------------------------------------------------------------ GUI
class App:
    COLS = (("name", "File", 300), ("size", "Size", 90), ("prog", "Progress", 80),
            ("speed", "Speed", 90), ("status", "Status", 180))

    def __init__(self, root, eng):
        self.root, self.eng = root, eng
        root.title("MyDM Lite")
        root.geometry("820x460")
        root.minsize(620, 300)
        try:
            ttk.Style().theme_use("vista")
        except tk.TclError:
            pass
        bar = ttk.Frame(root, padding=6)
        bar.pack(fill="x")
        for text, cmd in (("+ Add links", self.add_dialog), ("Add to queue", self.do_enqueue),
                          ("Remove from queue", self.do_dequeue), ("Delete", self.do_delete),
                          ("Open folder", self.open_folder), ("Settings", self.settings)):
            ttk.Button(bar, text=text, command=cmd).pack(side="left", padx=2)
        self.tree = ttk.Treeview(root, columns=[c[0] for c in self.COLS], show="headings", selectmode="extended")
        for k, t, w in self.COLS:
            self.tree.heading(k, text=t)
            self.tree.column(k, width=w, anchor="w" if k in ("name", "status") else "e")
        sb = ttk.Scrollbar(root, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)
        self.status = ttk.Label(root, anchor="w", padding=4)
        self.status.pack(fill="x", side="bottom")
        self.tree.bind("<Double-1>", self.dbl)
        self.tree.bind("<Delete>", lambda e: self.do_delete())
        self.menu = tk.Menu(root, tearoff=0)
        for t, c in (("Add to queue", self.do_enqueue), ("Remove from queue", self.do_dequeue),
                     ("Open file", self.open_file), ("Open folder", self.open_folder),
                     ("Copy link", self.copy_link), ("Delete", self.do_delete)):
            self.menu.add_command(label=t, command=c)
        self.tree.bind("<Button-3>", self.popup)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.tick()

    def sel(self):
        ids = set(self.tree.selection())
        return [j for j in self.eng.jobs if j.id in ids]

    def popup(self, e):
        r = self.tree.identify_row(e.y)
        if r and r not in self.tree.selection():
            self.tree.selection_set(r)
        self.menu.tk_popup(e.x_root, e.y_root)

    def dbl(self, e):
        for j in self.sel():
            if j.status == DONE:
                self.open_file()
            elif j.status in (OFF, ERROR):
                self.eng.enqueue(j)
            else:
                self.eng.dequeue(j)

    def do_enqueue(self):
        for j in self.sel():
            self.eng.enqueue(j)

    def do_dequeue(self):
        for j in self.sel():
            self.eng.dequeue(j)

    def do_delete(self):
        js = self.sel()
        if not js:
            return
        files = any(j.status == DONE for j in js)
        if files:
            a = messagebox.askyesnocancel("Delete", "Also delete the downloaded files from disk?\n\n"
                                          "Yes = delete files too, No = remove from list only")
            if a is None:
                return
        else:
            a = False
        for j in js:
            self.eng.remove(j, delete_file=bool(a))

    def open_file(self):
        for j in self.sel():
            p = j.path if j.status == DONE else None
            if p and os.path.exists(p):
                os.startfile(p) if sys.platform == "win32" else None

    def open_folder(self):
        js = self.sel()
        d = js[0].folder if js else CFG["folder"]
        if os.path.isdir(d) and sys.platform == "win32":
            os.startfile(d)

    def copy_link(self):
        js = self.sel()
        if js:
            self.root.clipboard_clear()
            self.root.clipboard_append("\n".join(j.url for j in js))

    def add_dialog(self):
        w = tk.Toplevel(self.root)
        w.title("Add links")
        w.transient(self.root)
        w.geometry("560x260")
        ttk.Label(w, text="One or more links (any text containing links works):").pack(anchor="w", padx=10, pady=(10, 2))
        t = tk.Text(w, height=8)
        t.pack(fill="both", expand=True, padx=10)
        try:
            c = self.root.clipboard_get()
            if find_urls(c):
                t.insert("1.0", c)
        except tk.TclError:
            pass
        t.focus_set()
        row = ttk.Frame(w)
        row.pack(fill="x", padx=10, pady=10)
        queue = tk.BooleanVar(value=True)
        ttk.Checkbutton(row, text="Start now (add to queue)", variable=queue).pack(side="left")

        def ok():
            urls = find_urls(t.get("1.0", "end"))
            if not urls:
                messagebox.showinfo("MyDM", "No link found.", parent=w)
                return
            for u in urls:
                j = Job(u, CFG["folder"])
                if not queue.get():
                    j.status = OFF
                self.eng.jobs.append(j)
            self.eng.save()
            self.eng.pump()
            w.destroy()
        ttk.Button(row, text="Add", command=ok).pack(side="right")
        ttk.Button(row, text="Cancel", command=w.destroy).pack(side="right", padx=6)

    def settings(self):
        w = tk.Toplevel(self.root)
        w.title("Settings")
        w.transient(self.root)
        w.resizable(False, False)
        f = ttk.Frame(w, padding=12)
        f.pack()
        folder = tk.StringVar(value=CFG["folder"])
        seg, par, ret = (tk.IntVar(value=CFG[k]) for k in ("segments", "parallel", "retries"))
        ttk.Label(f, text="Save to").grid(row=0, column=0, sticky="w")
        ttk.Entry(f, textvariable=folder, width=40).grid(row=0, column=1, padx=6)
        ttk.Button(f, text="...", width=3,
                   command=lambda: folder.set(filedialog.askdirectory(initialdir=folder.get()) or folder.get())
                   ).grid(row=0, column=2)
        for i, (lab, var, hi) in enumerate((("Connections per file", seg, 16), ("Simultaneous downloads", par, 6),
                                            ("Retries when connection drops", ret, 30)), 1):
            ttk.Label(f, text=lab).grid(row=i, column=0, sticky="w", pady=6)
            ttk.Spinbox(f, from_=1, to=hi, textvariable=var, width=6).grid(row=i, column=1, sticky="w", padx=6)

        def ok():
            CFG["folder"] = folder.get() or DEFAULT["folder"]
            try:
                CFG["segments"], CFG["parallel"], CFG["retries"] = seg.get(), par.get(), ret.get()
            except tk.TclError:
                pass
            self.eng.save()
            self.eng.pump()
            w.destroy()
        ttk.Button(f, text="OK", command=ok).grid(row=4, column=1, sticky="e", pady=(8, 0))

    def tick(self):
        self.eng.pump()
        have = set(self.tree.get_children())
        ids = []
        for j in self.eng.jobs:
            ids.append(j.id)
            if j.status == RUNNING:
                st = j.msg or "Downloading"
            elif j.status == ERROR:
                st = "Error: " + j.msg
            elif j.status == OFF:
                st = "Off queue" + (f" ({j.pct()}%)" if j.done else "")
            else:
                st = j.status
            vals = (j.name or j.url, fmt_size(j.total) if j.total > 0 else "?",
                    f"{j.pct()}%" if j.total > 0 and j.status != DONE else ("100%" if j.status == DONE else ""),
                    fmt_size(j.speed) + "/s" if j.status == RUNNING and j.speed else "", st)
            if j.id in have:
                if self.tree.item(j.id, "values") != tuple(str(v) for v in vals):
                    self.tree.item(j.id, values=vals)
            else:
                self.tree.insert("", "end", iid=j.id, values=vals)
        for i in have - set(ids):
            self.tree.delete(i)
        run = [j for j in self.eng.jobs if j.status == RUNNING]
        q = sum(1 for j in self.eng.jobs if j.status == QUEUED)
        off = sum(1 for j in self.eng.jobs if j.status == OFF)
        self.status.config(text=f"{len(run)} active  |  {q} queued  |  {off} off queue  |  "
                                f"{fmt_size(sum(j.speed for j in run))}/s")
        self.root.after(500, self.tick)

    def close(self):
        for j in self.eng.jobs:
            if j.status == RUNNING:
                j.stop = True
        self.eng.save()
        self.root.destroy()


def main():
    eng = Engine()
    root = tk.Tk()
    App(root, eng)
    root.mainloop()


if __name__ == "__main__":
    main()
