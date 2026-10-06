"""Giriş noktası.
  python auto/run.py tick              # DM tara -> yayını zamanı gelenleri yayınla -> bekleyen işleri üret (Task Scheduler bunu çağırır)
  python auto/run.py status            # iş listesi
  python auto/run.py add-topic "..."   # DM'siz elle konu ekle (test)
  python auto/run.py refresh-token     # IG token yenile
  python auto/run.py check             # .env / araç kontrolü
"""
import sys, shutil
from common import *
import pipeline, ig, steps

ACTIVE = pipeline.STAGES


def tick():
    with single_instance() as ok:
        if not ok:
            log("başka bir çalışma sürüyor, atlandı")
            return
        st = load_state()
        if env("IG_ACCESS_TOKEN") and env("OWNER_IGSID"):
            last = st.get("token_refreshed")
            if not last:
                st["token_refreshed"] = iso(now())  # ilk kez: token yeni üretildi varsayılır
            elif (now() - parse_iso(last)).days >= 30:
                try:
                    ig.refresh_token()
                    st["token_refreshed"] = iso(now())
                except Exception as e:
                    log("token yenileme hatası (tarama sürüyor):", e)
            try:
                pipeline.poll_inbox(st)
            except Exception as e:
                log("DM tarama hatası:", e)
            save_state(st)
        else:
            log("IG .env eksik -> DM taraması atlandı")
        pipeline.purge_trash()
        pipeline.publish_due(st)
        for j in sorted((j for j in st["jobs"].values() if j["status"] in ACTIVE), key=lambda j: j["created"]):
            pipeline.advance(st, j)
            pipeline.publish_due(st)  # uzun üretimler arasında zamanı gelen yayını kaçırma
        save_state(st)


def status():
    st = load_state()
    for j in st["jobs"].values():
        print(j.get("name") or j["id"], j["status"], j.get("publish_at") or "", j.get("posted") or "", j.get("error") or "")
    if not st["jobs"]:
        print("iş yok")


def add_topic(text):
    st = load_state()
    j = pipeline.new_job(st, "topic", "manual-" + iso(now()) + text[:20], topic=text)
    save_state(st)
    print("eklendi:", j["id"])


def whoami():
    """Token geçerli mi? Hesap ID'sini ve DM konuşmalarındaki gönderen ID'lerini (OWNER_IGSID adayı) listeler."""
    me = ig.g("GET", "me", params={"fields": "user_id,username,account_type"})
    print("Hesap:", me.get("username"), "| tür:", me.get("account_type"))
    print("IG_BUSINESS_USER_ID =", me.get("user_id") or me.get("id"))
    try:
        convs = ig.g("GET", "me/conversations", params={"platform": "instagram", "fields": "participants,updated_time"})
    except Exception as e:
        print("Konuşmalar okunamadı:", e)
        return
    rows = convs.get("data", [])
    if not rows:
        print("Henüz konuşma yok: kişisel hesabından bu hesaba bir DM (ör. 'merhaba') at, sonra tekrar çalıştır.")
    for c in rows:
        for p in c.get("participants", {}).get("data", []):
            if str(p.get("id")) != str(me.get("user_id") or me.get("id")):
                print("Gönderen adayı -> OWNER_IGSID =", p.get("id"), "| kullanıcı adı:", p.get("username"))


def check():
    for k in ("ANTHROPIC_API_KEY", "IG_ACCESS_TOKEN", "IG_BUSINESS_USER_ID", "OWNER_IGSID", "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID",
              "YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN"):
        print("%-24s %s" % (k, "ok" if env(k) else "EKSİK"))
    for t in ("claude", "ffmpeg", "ffprobe", "node", "npx"):
        print("%-24s %s" % (t, shutil.which(t) or "YOK"))


if __name__ == "__main__":
    c = sys.argv[1] if len(sys.argv) > 1 else "tick"
    {"tick": tick, "status": status, "check": check, "whoami": whoami, "refresh-token": lambda: print(ig.refresh_token()),
     "add-topic": lambda: add_topic(" ".join(sys.argv[2:]))}[c]()
