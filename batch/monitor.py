"""Video Otomasyonu Monitörü (Tkinter, ek kurulum gerektirmez).
Çalıştır: pythonw batch/monitor.py  (masaüstü kısayolu: "Otomasyon Monitoru")
- Durumu canlı gösterir: çalışıyor / hazır / durduruldu / takılmış olabilir, aktif iş ve aşama, planlı yayınlar, canlı log, bugünkü Claude harcaması.
- Başlat/Durdur: Windows Görev Zamanlayıcı'daki 'ClaudeVideoAuto' görevini açar/kapatır (monitörü kapatmak otomasyonu durdurmaz).
"""
import datetime, json, os, re, subprocess, threading, time, tkinter as tk
from tkinter import messagebox, ttk

ROOT = os.path.dirname(os.path.abspath(__file__))
AUTO = os.path.join(ROOT, "auto")
STATE, LOG, LOCK = (os.path.join(AUTO, f) for f in ("state.json", "auto.log", "run.lock"))
TASK = "ClaudeVideoAuto"
NOWIN = 0x08000000
STAGE_TR = {"script": "Senaryo", "photos": "Fotoğraf", "voice": "Ses", "transcribe": "Zamanlama", "compose": "Video / render",
            "caption": "Başlık", "schedule": "Planlama", "review": "Onay bekliyor", "ready": "Planlı", "missed": "Kaçtı - karar bekliyor", "published": "Yayınlandı",
            "failed": "Başarısız", "cancelled": "İptal", "skipped": "Atlandı"}
ACTIVE = ("script", "photos", "voice", "transcribe", "compose", "caption", "schedule")
STUCK_MIN = 12  # log bu kadar dakikadır sessizse ve tarama sürüyorsa "takılmış olabilir"
GREEN, RED, ORANGE, GRAY = "#1b8a3a", "#c62828", "#e08a00", "#666666"


def sh(args, timeout=20):
    try:
        r = subprocess.run(args, capture_output=True, text=True, errors="replace", timeout=timeout, creationflags=NOWIN)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return 1, str(e)


def pid_alive(pid):
    rc, out = sh(["tasklist", "/FI", "PID eq %d" % pid, "/NH"])
    return str(pid) in out


def task_info():
    """-> (durum: enabled|disabled|missing, sonraki çalışma metni)"""
    rc, out = sh(["schtasks", "/Query", "/TN", TASK, "/FO", "LIST"])
    if rc != 0:
        return "missing", ""
    low = out.lower()
    nxt = ""
    m = re.search(r"(?im)^\s*(?:next run time|sonraki çalıştırma zamanı)\s*:\s*(.+)$", out)
    if m:
        nxt = m.group(1).strip()
    disabled = ("disabled" in low) or ("devre dışı" in low)
    return ("disabled" if disabled else "enabled"), nxt


def lock_info():
    """-> (pid, yaş_sn) | None"""
    try:
        age = time.time() - os.path.getmtime(LOCK)
        pid = int(open(LOCK).read().strip() or 0)
    except (OSError, ValueError):
        return None
    return (pid, age) if pid and pid_alive(pid) else None


def read_state():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except Exception:
        return {"jobs": {}}


def tail_log(n=22):
    try:
        with open(LOG, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 90000))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []
    return lines[-n:], (os.path.getmtime(LOG) if os.path.exists(LOG) else 0)


def spend_today():
    day = datetime.date.today().isoformat()
    cost, calls = 0.0, 0
    try:
        for ln in open(LOG, encoding="utf-8", errors="replace"):
            if ln.startswith(day):
                m = re.search(r"API \w+ in=(\d+) out=(\d+)", ln)
                if m:
                    cost += int(m.group(1)) * 2 / 1e6 + int(m.group(2)) * 10 / 1e6
                    calls += 1
    except OSError:
        pass
    return cost, calls


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Video Otomasyonu Monitörü")
        self.root.geometry("980x720")
        self.snap = {}
        top = tk.Frame(self.root, padx=14, pady=10)
        top.pack(fill="x")
        self.dot = tk.Label(top, text="●", font=("Segoe UI", 26), fg=GRAY)
        self.dot.pack(side="left")
        box = tk.Frame(top)
        box.pack(side="left", padx=10)
        self.title = tk.Label(box, text="Kontrol ediliyor...", font=("Segoe UI", 16, "bold"), anchor="w")
        self.title.pack(anchor="w")
        self.sub = tk.Label(box, text="", font=("Segoe UI", 10), fg=GRAY, anchor="w", justify="left")
        self.sub.pack(anchor="w")

        bar = tk.Frame(self.root, padx=14)
        bar.pack(fill="x")
        for text, cmd in (("▶ Başlat", self.start), ("■ Durdur", self.stop), ("⟳ Şimdi tara", self.scan_now),
                          ("⚠ Takıldıysa yeniden başlat", self.restart), ("Log dosyası", lambda: os.startfile(LOG)),
                          ("Klasör: renders", lambda: os.startfile(os.path.join(ROOT, "renders")))):
            ttk.Button(bar, text=text, command=cmd).pack(side="left", padx=(0, 6), pady=4)

        self.stats = tk.Label(self.root, text="", font=("Segoe UI", 10), anchor="w", padx=14, fg=GRAY)
        self.stats.pack(fill="x")

        ttk.Label(self.root, text="Videolar", font=("Segoe UI", 10, "bold"), padding=(14, 8, 0, 0)).pack(anchor="w")
        cols = ("ad", "durum", "zaman", "platform")
        frame = tk.Frame(self.root, padx=14)
        frame.pack(fill="both", expand=False)
        self.tree = ttk.Treeview(frame, columns=cols, show="headings", height=11)
        for c, w, t in (("ad", 260, "Video"), ("durum", 160, "Durum"), ("zaman", 210, "Yayın zamanı"), ("platform", 260, "Yayınlandığı yer")):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w")
        sb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        ttk.Label(self.root, text="Canlı log", font=("Segoe UI", 10, "bold"), padding=(14, 8, 0, 0)).pack(anchor="w")
        lf = tk.Frame(self.root, padx=14, pady=4)
        lf.pack(fill="both", expand=True)
        self.log = tk.Text(lf, height=12, font=("Consolas", 9), bg="#111", fg="#ddd", wrap="none", state="disabled")
        self.log.tag_config("err", foreground="#ff6b6b")
        self.log.tag_config("ok", foreground="#7ee787")
        self.log.tag_config("warn", foreground="#ffcc66")
        self.log.pack(fill="both", expand=True)

        self.worker = threading.Thread(target=self.poll_loop, daemon=True)
        self.worker.start()
        self.root.after(1500, self.render)
        self.root.mainloop()

    # --- arka plan: yavaş sorgular (schtasks/tasklist) burada, arayüz donmasın
    def poll_loop(self):
        while True:
            try:
                st, nxt = task_info()
                self.snap = {"task": st, "next": nxt, "lock": lock_info(), "state": read_state(), "log": tail_log(), "spend": spend_today(),
                             "t": time.time()}
            except Exception as e:
                self.snap = {"err": str(e)}
            time.sleep(3)

    def render(self):
        s = self.snap
        if s and "task" in s:
            self.paint(s)
        self.root.after(1500, self.render)

    def paint(self, s):
        jobs = list(s["state"].get("jobs", {}).values())
        active = [j for j in jobs if j["status"] in ACTIVE]
        review = [j for j in jobs if j["status"] == "review"]
        ready = [j for j in jobs if j["status"] == "ready"]
        lock = s["lock"]
        lines, logmt = s["log"] if s["log"] else ([], 0)
        idle_min = (time.time() - logmt) / 60 if logmt else 0
        name = lambda j: (j.get("name") or j["id"])
        if s["task"] == "missing":
            color, head, sub = RED, "Zamanlayıcı kurulu değil", "batch\\auto\\install_task.ps1 çalıştırılmalı"
        elif s["task"] == "disabled":
            color, head, sub = RED, "DURDURULDU", "Otomasyon kapalı: DM taranmıyor, yayın yapılmıyor. ▶ Başlat'a basın."
        elif lock:
            cur = active[0] if active else None
            what = ("%s · %s" % (name(cur), STAGE_TR.get(cur["status"], cur["status"]))) if cur else "tarama/yayın"
            if idle_min >= STUCK_MIN:
                color, head = ORANGE, "Çalışıyor ama %d dk'dır sessiz, takılmış olabilir" % idle_min
                sub = "%s · tarama %d dk'dır sürüyor. Bekleyin; çok uzarsa '⚠ Takıldıysa yeniden başlat'." % (what, lock[1] / 60)
            else:
                color, head, sub = GREEN, "ÇALIŞIYOR", "%s · tarama %d dk %d sn'dir sürüyor" % (what, lock[1] // 60, lock[1] % 60)
        else:
            color, head = GREEN, "HAZIR · bekliyor"
            sub = "Sonraki tarama: %s · bekleyen onay: %d · planlı yayın: %d" % (s["next"] or "?", len(review), len(ready))
        self.dot.config(fg=color)
        self.title.config(text=head)
        self.sub.config(text=sub)
        cost, calls = s["spend"]
        nxt_pub = min((j["publish_at"] for j in ready if j.get("publish_at")), default=None)
        self.stats.config(text="Bugünkü Claude harcaması: $%.2f (%d çağrı)   ·   Sıradaki yayın: %s   ·   Bilgisayar saati: %s" % (
            cost, calls, (nxt_pub or "-").replace("T", " "), datetime.datetime.now().strftime("%H:%M")))
        # tablo
        order = {"missed": 0, "review": 0, "script": 1, "photos": 1, "voice": 1, "transcribe": 1, "compose": 1, "caption": 1, "schedule": 1, "ready": 2,
                 "failed": 3, "published": 4}
        rows = sorted((j for j in jobs if j["status"] not in ("cancelled", "skipped")),
                      key=lambda j: (order.get(j["status"], 5), j.get("publish_at") or "", j.get("created", "")))
        sel = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        for j in rows[:60]:
            self.tree.insert("", "end", values=(name(j), STAGE_TR.get(j["status"], j["status"]), (j.get("publish_at") or "").replace("T", " "),
                                                ", ".join("%s" % k for k in (j.get("posted") or {}))))
        # log
        self.log.config(state="normal")
        self.log.delete("1.0", "end")
        for ln in lines:
            tag = "err" if ("HATA" in ln or "hata" in ln or "Traceback" in ln) else "ok" if "yayınlandı" in ln or "✅" in ln else "warn" if "⚠" in ln or "bekletil" in ln else None
            self.log.insert("end", ln + "\n", tag)
        self.log.see("end")
        self.log.config(state="disabled")

    # --- komutlar
    def start(self):
        sh(["schtasks", "/Change", "/TN", TASK, "/ENABLE"])
        sh(["schtasks", "/Run", "/TN", TASK])
        messagebox.showinfo("Otomasyon", "Başlatıldı. 2 dakikada bir DM taranır, yayınlar zamanında çıkar.")

    def kill_running(self):
        info = lock_info()
        if info:
            sh(["taskkill", "/PID", str(info[0]), "/F", "/T"])
        try:
            os.remove(LOCK)
        except OSError:
            pass

    def stop(self):
        if not messagebox.askyesno("Durdur", "Otomasyon durdurulsun mu?\n\nDM taraması ve yayınlar durur. Çalışan bir üretim varsa o da kesilir; iş kaldığı yerden sonra devam eder (kredi yeniden harcanmaz)."):
            return
        sh(["schtasks", "/Change", "/TN", TASK, "/DISABLE"])
        self.kill_running()
        messagebox.showinfo("Otomasyon", "Durduruldu. Tekrar açmak için ▶ Başlat.")

    def scan_now(self):
        sh(["schtasks", "/Run", "/TN", TASK])

    def restart(self):
        if messagebox.askyesno("Yeniden başlat", "Çalışan tarama sonlandırılıp yeniden başlatılsın mı?\nÜretim kaldığı yerden devam eder."):
            self.kill_running()
            sh(["schtasks", "/Change", "/TN", TASK, "/ENABLE"])
            sh(["schtasks", "/Run", "/TN", TASK])


if __name__ == "__main__":
    App()
