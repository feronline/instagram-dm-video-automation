"""Bildirim: Discord webhook (tüm aşamalar + önizleme videosu) + Instagram DM (yalnızca önemli olaylar: alındı, planlandı, yayınlandı, hata)."""
import json, os, urllib.request, uuid
from common import *


def _discord(text):
    url = env("DISCORD_WEBHOOK_URL")
    if not url:
        return
    try:
        http("POST", url, json_body={"content": text[:1900], "username": "Video Hattı"}, timeout=20,
             headers={"User-Agent": "VideoHatti/1.0"})  # Discord (Cloudflare) varsayılan Python User-Agent'ını 1010 ile engelliyor
    except Exception as e:
        log("Discord bildirimi gönderilemedi:", e)


def notify(text, important=False):
    """important=True -> Instagram DM'ine de gider (DM_NOTIFY=1 ise)."""
    log("bildirim:", text.replace("\n", " | ")[:160])
    _discord(text)
    if important and env("DM_NOTIFY", "1") == "1":
        import ig
        ig.send_dm(text)


def discord_file(text, path):
    """Discord webhook'una dosya (önizleme videosu) yükler (multipart, stdlib)."""
    url = env("DISCORD_WEBHOOK_URL")
    if not url or not os.path.exists(path):
        return False
    b = "----v" + uuid.uuid4().hex
    crlf = "\r\n"
    head = (("--%s" % b) + crlf + 'Content-Disposition: form-data; name="payload_json"' + crlf + "Content-Type: application/json" + crlf + crlf
            + json.dumps({"content": text[:1900], "username": "Video Hattı"}) + crlf
            + ("--%s" % b) + crlf + 'Content-Disposition: form-data; name="files[0]"; filename="%s"' % os.path.basename(path) + crlf
            + "Content-Type: video/mp4" + crlf + crlf)
    body = head.encode("utf-8") + open(path, "rb").read() + (crlf + "--%s--" % b + crlf).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "multipart/form-data; boundary=" + b, "User-Agent": "VideoHatti/1.0"})
    try:
        urllib.request.urlopen(req, timeout=120).read()
        return True
    except Exception as e:
        log("Discord dosya yüklenemedi:", e)
        return False
