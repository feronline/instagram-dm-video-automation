"""Ortak: .env, yollar, log, HTTP, state.json (kilitli), kilit dosyası."""
import json, os, sys, time, datetime, urllib.request, urllib.error, urllib.parse, contextlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # .../batch
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)  # eng2 / lib import'ları için
REPO = os.path.dirname(ROOT)
AUTO = os.path.join(ROOT, "auto")
STATE_PATH = os.path.join(AUTO, "state.json")
LOCK_PATH = os.path.join(AUTO, "run.lock")
LOG_PATH = os.path.join(AUTO, "auto.log")
INBOX = os.path.join(ROOT, "work", "_inbox")
JOBS = os.path.join(ROOT, "work", "_jobs")


def load_env():
    try:
        from dotenv import dotenv_values
        for k, v in dotenv_values(os.path.join(REPO, ".env")).items():
            if v and not v.lstrip().startswith("#"):  # 'KEY=   # yorum' boş değer sayılır
                os.environ[k] = v.strip()
    except ImportError:
        p = os.path.join(REPO, ".env")
        if os.path.exists(p):
            for ln in open(p, encoding="utf-8"):
                ln = ln.split("#", 1)[0].strip()
                if "=" in ln:
                    k, v = ln.split("=", 1)
                    os.environ[k.strip()] = v.strip()


load_env()


def env(k, default=None, required=False):
    v = os.environ.get(k, default)
    if required and not v:
        raise RuntimeError("Eksik .env değeri: " + k)
    return v


def log(*a):
    s = "%s  %s" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), " ".join(str(x) for x in a))
    print(s, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(s + "\n")
    except OSError:
        pass


class HttpError(Exception):
    def __init__(self, code, body):
        super().__init__("HTTP %s: %s" % (code, body[:500]))
        self.code, self.body = code, body


def http(method, url, params=None, json_body=None, data=None, headers=None, timeout=60, raw=False):
    """stdlib HTTP. json_body -> JSON gönderir; yanıt JSON ise dict döner (raw=True ise bytes)."""
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    h = dict(headers or {})
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        h.setdefault("Content-Type", "application/json")
    elif isinstance(data, dict):
        data = urllib.parse.urlencode(data).encode("utf-8")
        h.setdefault("Content-Type", "application/x-www-form-urlencoded")
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            b = r.read()
    except urllib.error.HTTPError as e:
        raise HttpError(e.code, e.read().decode("utf-8", "replace"))
    if raw:
        return b
    if not b:
        return {}
    try:
        return json.loads(b.decode("utf-8"))
    except ValueError:
        return {"_text": b.decode("utf-8", "replace")}


def download(url, dest, timeout=120):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    b = http("GET", url, raw=True, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
    with open(dest, "wb") as f:
        f.write(b)
    return len(b)


# ---------- state ----------
def _blank():
    return {"seen_msgs": [], "jobs": {}, "next_no": None, "owner_cursor": None}


def load_state():
    if os.path.exists(STATE_PATH):
        return json.load(open(STATE_PATH, encoding="utf-8"))
    return _blank()


def save_state(st):
    tmp = STATE_PATH + ".tmp"
    json.dump(st, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, STATE_PATH)  # atomik


def user_busy():
    """Tam ekran oyun/uygulama ya da sunum açık mı? (Windows SHQueryUserNotificationState: 2=meşgul, 3=D3D tam ekran, 4=sunum)"""
    if env("DEFER_WHEN_BUSY", "1") != "1":
        return False
    try:
        import ctypes
        st = ctypes.c_int()
        if ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(st)) == 0:
            return st.value in (2, 3, 4)
    except Exception:
        pass
    return False


# ağır alt süreçler: düşük öncelik (BELOW_NORMAL) + pencere açma (CREATE_NO_WINDOW)
LOW_PRIO = 0x00004000 | 0x08000000


def now():
    return datetime.datetime.now()


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def parse_iso(s):
    return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%S")


@contextlib.contextmanager
def single_instance():
    """Aynı anda tek çalıştırma. Kilit 3 saatten eskiyse (çökmüş) çalınır."""
    if os.path.exists(LOCK_PATH) and time.time() - os.path.getmtime(LOCK_PATH) < 3 * 3600:
        yield False
        return
    open(LOCK_PATH, "w").write(str(os.getpid()))
    try:
        yield True
    finally:
        with contextlib.suppress(OSError):
            os.remove(LOCK_PATH)


def next_number(st):
    """Sıradaki video numarası: mevcut renders/scripts/state içindeki en büyük + 1."""
    import re
    mx = 0
    for d in ("renders", "scripts", "voiceovers"):
        p = os.path.join(ROOT, d)
        if os.path.isdir(p):
            for f in os.listdir(p):
                m = re.match(r"(\d\d)_", f)
                if m:
                    mx = max(mx, int(m.group(1)))
    for j in st["jobs"].values():
        if j.get("no"):
            mx = max(mx, j["no"])
    return mx + 1
