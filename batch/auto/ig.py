"""Instagram Graph API (Instagram Login): DM okuma (polling), reel indirme, DM gönderme, Reels yayını, token yenileme.

NOT: Paylaşılan reel ekinin JSON şekli resmi dokümanda net değil -> extract_media_url savunmalı yazıldı,
ham mesaj work/_inbox/raw/ altına dökülür; ilk gerçek DM'de şekil doğrulanıp sadeleştirilecek.
"""
import json, os, re, time, subprocess, urllib.request
from common import *

GRAPH = "https://graph.instagram.com/" + env("IG_API_VERSION", "v25.0")
RUPLOAD = "https://rupload.facebook.com/ig-api-upload/" + env("IG_API_VERSION", "v25.0")


def _tok():
    return env("IG_ACCESS_TOKEN", required=True)


def g(method, path, **kw):
    kw.setdefault("params", {})
    kw["params"]["access_token"] = _tok()
    return http(method, GRAPH + "/" + path.lstrip("/"), **kw)


# ---------- gelen kutusu ----------
def list_owner_messages(limit=20):
    """Yalnızca OWNER_IGSID ile olan konuşmanın son mesajlarını döndürür (eskiden yeniye)."""
    owner = env("OWNER_IGSID", required=True)
    convs = g("GET", "me/conversations", params={"platform": "instagram", "user_id": owner})
    out = []
    for c in convs.get("data", [])[:1]:
        for fields in ("messages.limit(%d){id,created_time,from,to,message,attachments,shares}" % limit,
                       "messages.limit(%d){id,created_time,from,to,message}" % limit):
            try:
                d = g("GET", c["id"], params={"fields": fields})
                break
            except HttpError as e:
                log("conversation fields reddedildi, yedek alan seti deneniyor:", e)
        else:
            return []
        for m in d.get("messages", {}).get("data", []):
            sender = str((m.get("from") or {}).get("id", ""))
            if sender != str(owner):  # kesin allowlist: başka gönderen -> yok say, loglama
                continue
            out.append(m)
    out.sort(key=lambda m: m.get("created_time", ""))
    return out


def dump_raw(m):
    os.makedirs(INBOX + "/raw", exist_ok=True)
    json.dump(m, open("%s/raw/%s.json" % (INBOX, re.sub(r"\W", "_", m["id"])[:80]), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)


def extract_media_url(m):
    """Mesajdaki (ek/paylaşım) olası video/medya URL'lerini toplar; mp4 olanı öne alır."""
    urls = []

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
        elif isinstance(o, str) and o.startswith("http"):
            urls.append(o)

    walk(m.get("attachments"))
    walk(m.get("shares"))
    urls = [u for u in urls if not _is_page_link(u) and "graph.instagram.com" not in u]
    urls.sort(key=lambda u: (0 if ".mp4" in u else 1 if "video" in u else 2))
    return urls


def _is_page_link(u):
    return bool(re.search(r"instagram\.com/(reel|reels|p|tv)/", u))


def page_links(m):
    """DM'de paylaşılan reel yalnızca permalink olarak gelir (shares.data[].link)."""
    out = []
    for s in (m.get("shares") or {}).get("data", []):
        if isinstance(s, dict) and _is_page_link(s.get("link", "")):
            out.append(s["link"])
    return out


def ytdlp_download(link, dest):
    """Herkese açık reel'i yt-dlp ile geçici indirir (yalnızca analiz için; yayınlanmaz, iş sonunda silinir)."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    base = dest[:-4]
    r = subprocess.run(["python", "-m", "yt_dlp", "--no-playlist", "--no-warnings", "-f", "mp4/best",
                        "--max-filesize", "150M", "--merge-output-format", "mp4", "-o", base + ".%(ext)s", link],
                       capture_output=True, text=True, timeout=300, encoding="utf-8", errors="replace")
    if r.returncode:
        log("yt-dlp hata:", (r.stderr or r.stdout)[-300:])
        return False
    return os.path.exists(dest) or _rename_any(base, dest)


def _rename_any(base, dest):
    import glob
    for f in glob.glob(base + ".*"):
        if f != dest and os.path.getsize(f) > 0:
            os.replace(f, dest)
            return True
    return False


def has_video(path):
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_type",
                            "-of", "csv=p=0", path], capture_output=True, text=True, timeout=30)
        return "video" in r.stdout
    except Exception:
        return False


def fetch_reel(m, dest):
    """Reel'i indirir. Başarılıysa True. Önce DM'de doğrudan medya URL'si varsa, yoksa permalink'ten yt-dlp ile."""
    for u in extract_media_url(m):
        try:
            download(u, dest)
            if has_video(dest):
                return True
        except Exception as e:
            log("indirme denemesi başarısız:", u[:80], e)
    for link in page_links(m):
        try:
            if ytdlp_download(link, dest) and has_video(dest):
                return True
        except Exception as e:
            log("yt-dlp denemesi başarısız:", link, e)
    if os.path.exists(dest):
        os.remove(dest)
    return False


def send_dm(text):
    owner = env("OWNER_IGSID", required=True)
    try:
        g("POST", "me/messages", json_body={"recipient": {"id": owner}, "message": {"text": text[:900]}})
        return True
    except Exception as e:
        log("DM gönderilemedi:", e)
        return False


# ---------- yayın ----------
# Resumable upload bu hesapta çalışmadı ("video_url is required"). Bunun yerine video, yayın süresince geçici bir yerel sunucu +
# Cloudflare quick tunnel üzerinden tek seferlik rastgele bir HTTPS adresinde sunulur; Meta bu adresten indirir. Sonra kapatılır.
CLOUDFLARED = os.path.join(ROOT, "tools", "cloudflared.exe")


def _make_handler(path, token):
    import http.server

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _serve(self, head):
            if self.path.split("?")[0] != "/" + token + ".mp4":
                self.send_error(404)
                return
            size = os.path.getsize(path)
            start, end, code = 0, size - 1, 200
            rng = self.headers.get("Range")
            m = re.match(r"bytes=(\d*)-(\d*)", rng or "")
            if m and (m.group(1) or m.group(2)):
                if m.group(1):
                    start = int(m.group(1))
                    end = int(m.group(2)) if m.group(2) else size - 1
                else:
                    start = max(0, size - int(m.group(2)))
                end = min(end, size - 1)
                code = 206
            self.send_response(code)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            if code == 206:
                self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
            self.end_headers()
            if head:
                return
            with open(path, "rb") as f:
                f.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = f.read(min(1 << 20, left))
                    if not chunk:
                        break
                    try:
                        self.wfile.write(chunk)
                    except OSError:
                        return
                    left -= len(chunk)

        def do_GET(self):
            self._serve(False)

        def do_HEAD(self):
            self._serve(True)

    return H


class public_url:
    """with public_url(video_path) as url: ... -> geçici, tahmin edilemez HTTPS adresi (bloktan çıkınca kapanır)."""

    def __init__(self, path):
        self.path = path

    def __enter__(self):
        import http.server, threading, uuid
        if not os.path.exists(CLOUDFLARED):
            raise RuntimeError("cloudflared yok: " + CLOUDFLARED)
        self.token = uuid.uuid4().hex
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(self.path, self.token))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        port = self.server.server_address[1]
        self.proc = subprocess.Popen([CLOUDFLARED, "tunnel", "--url", "http://127.0.0.1:%d" % port, "--no-autoupdate"],
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                     creationflags=0x08000000)
        base = {"url": None}

        def reader():
            for line in self.proc.stdout:
                m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
                if m and not base["url"]:
                    base["url"] = m.group(0)

        threading.Thread(target=reader, daemon=True).start()
        t0 = time.time()
        while not base["url"] and time.time() - t0 < 60 and self.proc.poll() is None:
            time.sleep(0.5)
        if not base["url"]:
            self.__exit__()
            raise RuntimeError("tünel adresi alınamadı")
        self.url = "%s/%s.mp4" % (base["url"], self.token)
        for _ in range(40):  # tünel dışarıdan erişilebilir olana kadar bekle (DNS yayılımı birkaç sn sürer)
            try:
                req = urllib.request.Request(self.url, method="HEAD", headers={"User-Agent": "Mozilla/5.0"})
                if urllib.request.urlopen(req, timeout=10).status == 200:
                    return self.url
            except Exception:
                time.sleep(3)
        self.__exit__()
        raise RuntimeError("tünel dışarıdan erişilemedi")

    def __exit__(self, *a):
        try:
            self.proc.terminate()
        except Exception:
            pass
        try:
            self.server.shutdown()
        except Exception:
            pass
        return False


def publish_reel(video_path, caption):
    """Reels yayınlar (geçici HTTPS adresi + video_url). Dönen: media id."""
    uid = env("IG_BUSINESS_USER_ID", required=True)
    with public_url(video_path) as url:
        c = g("POST", "%s/media" % uid, data={"media_type": "REELS", "video_url": url, "caption": caption[:2200], "share_to_feed": "true",
                                              "thumb_offset": env("IG_COVER_OFFSET_MS", "1000")})  # kapak: videonun 1. saniyesi (0. kare değil)
        cid = c["id"]
        log("IG konteyner oluştu:", cid)
        for _ in range(60):  # en fazla ~15 dk (15 sn aralıkla)
            time.sleep(15)
            s = g("GET", cid, params={"fields": "status_code,status"})
            sc = s.get("status_code")
            if sc == "FINISHED":
                break
            if sc in ("ERROR", "EXPIRED"):
                raise RuntimeError("IG konteyner hata: %s" % s)
        else:
            raise RuntimeError("IG işleme zaman aşımı")
    p = g("POST", "%s/media_publish" % uid, data={"creation_id": cid})
    return p["id"]


def refresh_token():
    """Uzun ömürlü token'ı yeniler (≥24 saat yaşlı olmalı); yeni değeri .env'ye yazar."""
    r = http("GET", "https://graph.instagram.com/refresh_access_token",
             params={"grant_type": "ig_refresh_token", "access_token": _tok()})
    new = r["access_token"]
    p = os.path.join(REPO, ".env")
    s = open(p, encoding="utf-8").read()
    s = re.sub(r"(?m)^IG_ACCESS_TOKEN=.*$", "IG_ACCESS_TOKEN=" + new, s)
    open(p, "w", encoding="utf-8").write(s)
    os.environ["IG_ACCESS_TOKEN"] = new
    return r.get("expires_in")
