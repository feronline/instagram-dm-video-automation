"""İş durum makinesi: DM -> konu -> araştırma -> storyboard -> fotoğraf -> ses -> whisper -> render -> caption -> zamanla -> yayın."""
import glob, hashlib, json, os, re, shutil, unicodedata
from common import *
import ig, yt, steps, notify, whenparse

STAGES = ["script", "photos", "voice", "transcribe", "compose", "caption", "schedule"]
MAX_ATTEMPTS = 3


def _disp(j):
    """Mesajlarda numara yerine video adı: '16_kristal_cam' -> 'kristal_cam'."""
    return re.sub(r"^\d+_", "", j.get("name") or j["id"])


def jdir(j):
    return os.path.join(JOBS, j["id"])


def dm(text, important=True):
    notify.notify(text, important)


# ---------- gelen kutusu ----------
def _jobs_today(st):
    d = now().strftime("%Y-%m-%d")
    return sum(1 for j in st["jobs"].values() if j["created"].startswith(d) and j.get("kind") != "legacy")


def new_job(st, kind, mid, **kw):
    jid = "j" + hashlib.sha1(mid.encode()).hexdigest()[:10]
    j = {"id": jid, "kind": kind, "mid": mid, "status": "script", "attempts": 0, "error": None,
         "created": iso(now()), "no": None, "name": None, "publish_at": None, "posted": {}, **kw}
    st["jobs"][jid] = j
    os.makedirs(jdir(j), exist_ok=True)
    return j


ACTIVE_STATUSES = ("script", "photos", "voice", "transcribe", "compose", "caption", "schedule", "review", "ready", "missed")


def _parse_args(st, t, statuses, default_statuses):
    """'onay 16 16.45' / 'onay 16.45' / 'onay şimdi' / 'iptal 16' argümanlarını çözer. Dönen: (iş | None, zaman | None, hata | None).
    Video numarası verilmezse default_statuses içindeki en yeni iş seçilir."""
    no, hm, nowflag, names = None, None, False, []
    norm = lambda x: re.sub(r"[^a-z0-9ğüşöçı]", "", x.lower())
    date, tokens, derr = whenparse.extract_date(t.split()[1:], now().date())  # yarın, 2 gün sonra, cuma, 20 ekim...
    if derr:
        return None, None, derr
    for tok in tokens:
        if re.fullmatch(r"\d{1,2}[.:]\d{2}", tok):
            hh, mm = map(int, re.split(r"[.:]", tok))
            if hh > 23 or mm > 59:
                return None, None, "Geçersiz saat: " + tok
            hm = (hh, mm)
        elif tok in ("şimdi", "simdi", "hemen"):
            nowflag = True
        elif tok.isdigit():
            n = int(tok)
            if any(j.get("no") == n for j in st["jobs"].values()):
                no = n
            elif 0 <= n <= 23:
                hm = (n, 0)
            else:
                return None, None, "Anlamadım: " + tok
        elif tok != "saat":
            names.append(norm(tok))  # video adı (ya da bir kısmı): 'kristal', 'kristal_cam'
    when = {"now": True} if nowflag else ({"hm": hm, "date": date} if (hm or date) else None)
    if names:
        def hit(j):
            hay = norm((j.get("name") or "") + (j.get("title") or ""))
            return all(n and n in hay for n in names)
        pool = sorted((j for j in st["jobs"].values() if j["status"] in statuses and hit(j)), key=lambda j: j["created"])[-1:]
        if not pool:
            return None, when, "Bu isimle video bulamadım: " + " ".join(names)
        return pool[0], when, None
    if no is not None:
        pool = [j for j in st["jobs"].values() if j.get("no") == no and j["status"] in statuses]
    else:
        pool = sorted((j for j in st["jobs"].values() if j["status"] in default_statuses), key=lambda j: j["created"])[-1:]
    return (pool[0] if pool else None), when, None


class _PastToday(ValueError):
    """Verilen saat bugün için geçmiş."""


def _when_dt(when):
    """when: {"now"} | {"hm": (h, m) | None, "date": date | None} -> datetime. Geçersizse ValueError (kullanıcıya mesaj)."""
    n = now()
    if when.get("now"):
        return n - datetime.timedelta(seconds=1)  # hemen: aynı taramada yayınlanır
    hm, d = when.get("hm"), when.get("date")
    if hm is None:  # yalnızca tarih verildi: ilk yayın slotu saati
        hh, mm = map(int, env("PUBLISH_SLOTS", "12:30,20:00").split(",")[0].strip().split(":"))
    else:
        hh, mm = hm
    if d is None:  # yalnızca saat: bugün; geçtiyse SESSİZCE ertesi güne kaydırma, kullanıcıya sor
        dt = n.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if dt <= n:
            raise _PastToday("%02d.%02d" % (hh, mm))
        return dt
    dt = datetime.datetime(d.year, d.month, d.day, hh, mm)
    if dt <= n:
        raise ValueError("Bu tarih/saat geçmişte: %s" % dt.strftime("%d.%m %H:%M"))
    maxd = int(env("MAX_SCHEDULE_DAYS", "30"))
    if (dt - n).days > maxd:
        raise ValueError("En fazla %d gün sonrasına planlayabilirim." % maxd)
    return dt


def trash_job(j):
    """İptal edilen videonun dosyalarını batch/_trash/<ad>/ altına taşır (7 gün sonra tick siler)."""
    name = j.get("name")
    if not name:
        return
    dest = os.path.join(ROOT, "_trash", name)
    os.makedirs(dest, exist_ok=True)
    for src in ("renders/%s.mp4" % name, "previews/%s.mp4" % name, "captions/%s.json" % name, "scripts/%s.txt" % name,
                "voiceovers/%s.mp3" % name, "projects/%s" % name, "work/%s" % name):
        p = os.path.join(ROOT, src)
        if os.path.exists(p):
            shutil.move(p, os.path.join(dest, os.path.basename(p)))
    os.utime(dest, None)


def purge_trash(days=7):
    base = os.path.join(ROOT, "_trash")
    if not os.path.isdir(base):
        return
    for d in os.listdir(base):
        p = os.path.join(base, d)
        if time.time() - os.path.getmtime(p) > days * 86400:
            shutil.rmtree(p, ignore_errors=True)


def handle_commands(st, text):
    t = (text or "").strip().lower()
    if t == "durum":
        lines = ["%s %s %s" % (j.get("name") or j["id"], j["status"], j.get("publish_at") or "")
                 for j in list(st["jobs"].values())[-8:]]
        dm("Durum:\n" + ("\n".join(lines) or "iş yok"))
        return True
    if re.match(r"onay\b", t):
        j, when, err = _parse_args(st, t, ("review", "ready", "missed"), ("review", "missed"))
        if err:
            dm(err)
            return True
        if not j:
            dm("Onay bekleyen video yok.")
            return True
        try:
            dt = _when_dt(when) if when else next_slot(st)
        except _PastToday as e:
            t = str(e)
            dm("⚠ **%s**: %s bugün için geçti, planlamadım. Yarın %s'e mi? → onay %s yarın %s | Hemen yayınlansın mı? → onay %s şimdi | "
               "Bugün başka saat → onay %s 23.30 | İptal → iptal %s" % (_disp(j), t.replace(".", ":"), t.replace(".", ":"), _disp(j), t, _disp(j), _disp(j), _disp(j)))
            return True
        except ValueError as e:
            dm(str(e))
            return True
        j["publish_at"] = iso(dt)
        if j.get("held"):  # otomatik kontrolden geçemeyen: başlık/planlama aşamaları şimdi çalışır
            j["held"], j["approved"], j["status"] = False, True, "caption"
        else:
            j["status"] = "ready"
        gun = whenparse.label(dt, now())
        wanted = [x.strip() for x in env("PLATFORMS", "instagram,youtube").split(",") if x.strip()]
        missing = [x for x in wanted if not _configured(x)]
        uyari = (" | ⚠ ŞU AN YAYINLANAMAZ: %s bağlı değil. Bağlanınca sırayla yayınlanır." % ", ".join(missing)) if (missing and env("ALLOW_PARTIAL", "0") != "1") else ""
        dm("✅ **%s** onaylandı: %s %s yayınlanacak. İptal için: iptal %s%s" % (_disp(j), gun, dt.strftime("%H:%M"), _disp(j), uyari))
        return True
    if re.match(r"iptal\b", t):
        j, _, err = _parse_args(st, t, ACTIVE_STATUSES, ("review", "missed"))
        if err:
            dm(err)
            return True
        if not j:
            dm("İptal edilecek video bulunamadı.")
            return True
        trash_job(j)
        j["status"] = "cancelled"
        dm("🗑 **%s** iptal edildi ve çöpe atıldı (batch/_trash, 7 gün sonra silinir)." % _disp(j))
        return True
    return False


NOTE_WAIT_SEC = 60  # reel geldikten sonra notun ulaşması için beklenen süre
NOTE_WINDOW = (-60, 60)  # reel mesajına göre notun olabileceği zaman aralığı (sn)
GREETINGS = {"merhaba", "selam", "test", "tamam", "ok", "deneme", "hey"}


def _msg_time(m):
    try:
        return datetime.datetime.strptime(m["created_time"][:19], "%Y-%m-%dT%H:%M:%S")
    except (KeyError, ValueError):
        return None


def _note_for(msgs, m):
    """Reel'in yanındaki not: paylaşım mesajındaki metin ya da çevresindeki (−60/+90 sn) anlamlı düz metin. Yoksa ''."""
    t0 = _msg_time(m)
    cands = [((m.get("message") or "").strip(), 0)]
    for o in msgs:
        if o is m or ig.page_links(o) or ig.extract_media_url(o):
            continue
        t1 = _msg_time(o)
        if t0 and t1 and NOTE_WINDOW[0] <= (t1 - t0).total_seconds() <= NOTE_WINDOW[1]:
            cands.append(((o.get("message") or "").strip(), abs((t1 - t0).total_seconds())))
    for txt, _ in sorted(cands, key=lambda c: c[1]):
        low = txt.lower()
        if len(txt) >= 10 and low not in GREETINGS and not re.match(r"(onay|iptal|durum)\b", low):
            return re.sub(r"(?is)^\s*konu\s*:\s*", "", txt).strip()  # "konu: ..." yazılmışsa ön eki at
    return ""


YES_WORDS = {"evet", "e", "evt", "var", "yes", "y"}
NO_WORDS = {"hayır", "hayir", "h", "yok", "no", "n"}


def _ask_note(st, m):
    """Reel geldikten sonra konu/not sorar. 'evet' -> NOTE_AFTER_YES_SEC (90 sn) içinde notu bekler; 'hayır' -> hemen başlar;
    direkt uzun bir metin yazılırsa not sayılır; ASK_TIMEOUT_SEC (180 sn) cevap gelmezse notsuz başlar. Bu sürede 8 sn'de bir DM taranır."""
    t_reel = _msg_time(m)
    state, deadline = "ask", time.time() + float(env("ASK_TIMEOUT_SEC", "180"))
    while True:
        time.sleep(8)
        for o in ig.list_owner_messages():
            t_o = _msg_time(o)
            if o["id"] in st["seen_msgs"] or ig.page_links(o) or ig.extract_media_url(o) or (t_reel and t_o and t_o < t_reel):
                continue
            st["seen_msgs"].append(o["id"])
            txt = (o.get("message") or "").strip()
            low = txt.lower()
            if not txt or handle_commands(st, txt):
                continue
            if low in NO_WORDS and state == "ask":
                dm("👍 Tamam, hemen başlıyorum.")
                return ""
            if low in YES_WORDS and state == "ask":
                state, deadline = "note", time.time() + float(env("NOTE_AFTER_YES_SEC", "90"))
                dm("✍️ Notunu yaz (90 saniye bekliyorum). Örn: kristal bardak ile cam bardak farkını anlat")
                continue
            if state == "note" or (len(txt) >= 10 and low not in GREETINGS):
                note = re.sub(r"(?is)^\s*konu\s*:\s*", "", txt).strip()
                dm("📝 Notun alındı: «%s», başlıyorum." % note[:80])
                return note
        if time.time() > deadline:
            dm("⏱ Cevap gelmedi, notsuz başlıyorum (konuyu videodan çıkarırım)." if state == "ask" else "⏱ Not gelmedi, notsuz başlıyorum.")
            return ""


def poll_inbox(st):
    msgs = ig.list_owner_messages()
    for m in msgs:
        if m["id"] in st["seen_msgs"]:
            continue
        st["seen_msgs"].append(m["id"])
        st["seen_msgs"] = st["seen_msgs"][-500:]
        try:  # created_time UTC; 24 saatten eski geçmişi işleme (reel URL'leri zaten kısa ömürlü)
            created = datetime.datetime.strptime(m["created_time"][:19], "%Y-%m-%dT%H:%M:%S")
            if created < datetime.datetime.utcnow() - datetime.timedelta(hours=24):
                continue
        except (KeyError, ValueError):
            pass
        ig.dump_raw(m)
        text = m.get("message") or ""
        if handle_commands(st, text):
            continue
        limit = int(env("MAX_JOBS_PER_DAY", "0"))  # 0 = sınırsız (varsayılan)
        if limit and _jobs_today(st) >= limit:
            dm("Günlük üretim sınırına ulaşıldı; yarın devam.")
            continue
        topic = re.match(r"(?is)\s*konu\s*:\s*(.+)", text)
        if topic:
            # Varsayılan: yalnızca reel (+ istenirse not) ile çalışır; tek başına metin iş açmaz. Reel'in yanındaki "konu: ..." not sayılır.
            if env("ALLOW_TOPIC_ONLY", "0") == "1":
                new_job(st, "topic", m["id"], topic=topic.group(1).strip())
                dm("Konu alındı, üretiyorum.")
            continue
        links = ig.page_links(m)
        if not links and not ig.extract_media_url(m):
            continue  # düz sohbet metni ("merhaba" vb.): iş açma
        if links and any(o.get("link") == links[0] for o in st["jobs"].values()):
            dm("Bu reel zaten işlendi, atlıyorum.")
            continue
        note = _note_for(msgs, m)
        ask = (not note) and env("ASK_NOTE", "1") == "1"
        dm("📥 Video alındı, indiriliyor...%s" % ((" Bu arada bu video için bir konu/not eklemek ister misin? → evet (90 sn içinde yaz) · hayır (hemen başlayayım). "
                                                  "3 dk cevap vermezsen notsuz başlarım.") if ask else ""))
        j = new_job(st, "reel", m["id"], link=links[0] if links else None, note=note)
        src = os.path.join(jdir(j), "source.mp4")
        got = ig.fetch_reel(m, src)
        if ask:
            note = _ask_note(st, m)
            j["note"] = note
        if got:
            sha = hashlib.sha256(open(src, "rb").read()).hexdigest()
            if any(o.get("sha") == sha for o in st["jobs"].values() if o is not j):
                os.remove(src)
                del st["jobs"][j["id"]]
                dm("Bu reel zaten işlendi, atlıyorum.")
                continue
            j["sha"] = sha
            dm("Reel alındı%s, özgün video üretiyorum." % ((" (notun: «%s»)" % note[:80]) if note else ", konuyu analiz ediyorum"))
        elif note:  # reel inmedi ama kullanıcı ne istediğini yazmış: nottan üret
            del st["jobs"][j["id"]]
            shutil.rmtree(jdir(j), ignore_errors=True)
            new_job(st, "topic", m["id"], topic=note)
            dm("Reel indirilemedi ama notundan üretiyorum: «%s»" % note[:80])
        else:
            del st["jobs"][j["id"]]
            shutil.rmtree(jdir(j), ignore_errors=True)
            dm("Reel indirilemedi. Videoyu tekrar paylaşır mısın? (olmazsa 'konu: ...' yazıp konuyu söyleyebilirsin)")


# ---------- aşamalar ----------
def _existing_names():
    return ", ".join(sorted({re.sub(r"^\d\d_", "", os.path.basename(f)[:-4]) for f in glob.glob(ROOT + "/scripts/*_*.txt")}))


def _ascii_slug(s):
    s = unicodedata.normalize("NFKD", (s or "video").replace("ı", "i").replace("İ", "i")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:30] or "video"


def validate_sb(sb):
    n = len(sb["script"].split())
    if not 50 <= n <= 95:
        raise RuntimeError("senaryo uzunluğu uygunsuz: %d kelime" % n)
    if not 4 <= len(sb["scenes"]) <= 12:
        raise RuntimeError("sahne sayısı uygunsuz: %d" % len(sb["scenes"]))
    if sum(1 for s in sb["scenes"] if s["photo_queries"]) < 3:
        raise RuntimeError("fotoğraflı sahne sayısı yetersiz")


def stage_script(st, j):
    import llm
    if j.get("no") is None:
        j["no"] = next_number(st)
    no = "%02d" % j["no"]
    if j["kind"] == "reel" and not j.get("topic"):  # yeniden denemede konu zaten çıkarılmışsa atla
        src = os.path.join(jdir(j), "source.mp4")
        tr = steps.source_transcript(src, jdir(j) + "/an")
        frames = []
        if env("SEND_FRAMES", "0") == "1" or len(tr) < 40:  # sessiz/müzikli reel: konu için kareler gerekir
            frames = steps.extract_frames(src, jdir(j) + "/an/frames")
        t = llm.extract_topic(tr, frames, j.get("note", ""))
        if not t["usable"] and j.get("note"):
            t = {"topic": j["note"], "usable": True}  # reel anlaşılmadı ama kullanıcı ne istediğini yazmış
        if not t["usable"]:
            j["status"], j["error"] = "skipped", "konu anlaşılamadı"
            dm("Reel'in konusunu anlayamadım, atladım. 'konu: ...' yazarak konuyu verebilirsin.")
            return False
        j["topic"] = t["topic"]
        shutil.rmtree(jdir(j) + "/an", ignore_errors=True)  # kaynak analizi kalıcı tutulmaz
        os.remove(src)
    topic = j["topic"]
    rp = jdir(j) + "/research.md"
    if not os.path.exists(rp):
        open(rp, "w", encoding="utf-8").write(llm.research(topic))
    recent = [json.load(open(p, encoding="utf-8")).get("design") for p in sorted(glob.glob(JOBS + "/*/brief.json"))[-5:]]
    sb = llm.make_storyboard(topic, open(rp, encoding="utf-8").read(), _existing_names(), [d for d in recent if d],
                             j.get("note", ""))
    if sb["skip"]:
        j["status"], j["error"] = "skipped", sb["skip"]
        dm("Bu konuyu atladım: " + sb["skip"])
        return False
    validate_sb(sb)
    j["name"] = "%s_%s" % (no, _ascii_slug(sb["slug"]))
    j["title"] = sb["title"]
    json.dump(sb, open(jdir(j) + "/brief.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open("%s/scripts/%s.txt" % (ROOT, j["name"]), "w", encoding="utf-8").write(sb["script"].strip() + "\n")


def _sb(j):
    return json.load(open(jdir(j) + "/brief.json", encoding="utf-8"))


def stage_photos(st, j):
    import llm, photos
    sb = _sb(j)
    slots, meta = [], {}
    for i, s in enumerate(sb["scenes"]):
        if s["layout"] == "quote":
            continue
        for k, q in enumerate(s["photo_queries"]):
            slot = "s%d_%d" % (i, k)
            cands = photos.fetch_slot(j["name"], slot, q, prefer_tall=s["layout"] == "hero")
            if cands:
                slots.append({"slot": slot, "query": q, "context": " / ".join(l["text"] for l in s["lines"]), "cands": [c["file"] for c in cands]})
                meta[slot] = cands
    if len(slots) < 3:
        raise RuntimeError("yeterli fotoğraf bulunamadı (%d slot)" % len(slots))
    try:
        picks = llm.pick_photos(j["topic"], slots)
    except Exception as e:  # görsel seçim çökerse ilk adaya düş
        log("fotoğraf seçimi API hatası, ilk aday kullanılıyor:", e)
        picks = {s["slot"]: [0] for s in slots}
    chosen, credits = {}, []
    for s in slots:
        files = []
        for c in picks.get(s["slot"], [0])[:2]:
            if isinstance(c, int) and 0 <= c < len(s["cands"]) and s["cands"][c] not in files:
                files.append(s["cands"][c])
                m = meta[s["slot"]][c]
                credits.append({"file": os.path.basename(m["file"]), "title": m["title"], "author": m["artist"], "license": m["license"], "source_url": m["page"] or m["url"]})
        if files:
            chosen[s["slot"]] = files
    # eksik sahnelere (fotoğraf bulunamayan) kullanılmayan adaylardan / diğer sahnelerden fotoğraf ödünç ver: ekran yazıya kalmasın
    borrowed = []
    pool = [f for s_ in slots for f in s_["cands"] if all(f not in v for v in chosen.values())]
    reuse = [f for v in chosen.values() for f in v]
    for i, sc in enumerate(sb["scenes"]):
        if sc["layout"] == "quote":
            continue
        keys = ["s%d_%d" % (i, k) for k in range(max(len(sc["photo_queries"]), 1))]
        want = 3 if sc["layout"] == "trio" else 1
        have = [k for k in keys if k in chosen]
        for k in [k for k in keys if k not in chosen][:max(0, want - len(have))]:
            src = pool.pop(0) if pool else (reuse[(i + len(have)) % len(reuse)] if reuse else None)
            if src:
                chosen[k] = [src]
                borrowed.append(k)
                have.append(k)
                log("fotoğraf ödünç verildi:", j["name"], k)
    if borrowed:
        chosen["_borrowed"] = borrowed  # şablon: ödünç fotoğraf tam ekran hero yerine çerçeveli kartta gösterilir
    out = "%s/work/%s" % (ROOT, j["name"])
    json.dump(chosen, open(out + "/photos.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(credits, open(out + "/credits.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if len(chosen) < 3:
        raise RuntimeError("seçilen fotoğraf sayısı yetersiz (%d)" % len(chosen))


def stage_voice(st, j):
    mp3 = "%s/voiceovers/%s.mp3" % (ROOT, j["name"])
    if not os.path.exists(mp3):
        steps.tts("%s/scripts/%s.txt" % (ROOT, j["name"]), mp3)
    steps.fit_voice(mp3)


def stage_transcribe(st, j):
    steps.transcribe_all()
    steps.need("%s/work/%s/words.json" % (ROOT, j["name"]), "words.json")


def _render_once(j, sb, chosen, ov):
    import templates
    _, log_ = templates.build_video(j["name"], sb, chosen, ov)
    json.dump(log_, open("%s/work/%s/timing.json" % (ROOT, j["name"]), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    _, warn = steps.render(j["name"])
    j["warnings"] = warn
    return warn


def stage_compose(st, j):
    import qa
    out = "%s/renders/%s.mp4" % (ROOT, j["name"])
    hold = None
    if not os.path.exists(out):
        chosen = json.load(open("%s/work/%s/photos.json" % (ROOT, j["name"]), encoding="utf-8"))
        sb, ov = _sb(j), {}
        warn = _render_once(j, sb, chosen, ov)
        if warn:
            log("render uyarıları:", j["name"], len(warn))
        if env("QA_ENABLED", "1") == "1":
            res = qa.review(j["name"], j["topic"], sb)
            for rnd in range(int(env("QA_MAX_FIX_ROUNDS", "1"))):
                if not res or res["overall"] == "pass" or not any(not s["ok"] for s in res["scenes"]):
                    break
                ov, notes = qa.apply_fixes(j["name"], res, ov, chosen, sb)
                if not notes:
                    break
                dm("🔧 **%s** — otomatik kontrol sorun buldu, düzeltiyorum: %s" % (_disp(j), "; ".join(notes)), important=False)
                _render_once(j, sb, chosen, ov)
                res = qa.review(j["name"], j["topic"], sb)
            bad = qa.serious(res) if res else []
            j["qa"] = {"overall": res["overall"] if res else "skipped", "remaining": [list(x) for x in bad]}
            if bad:
                hold = "; ".join("sahne %d %s" % (a, b_) for a, b_, _ in bad)
    j["duration"] = steps.verify_render(out)
    try:  # hemen izleyebilmeniz için küçük önizleme: previews/ klasörü + Discord
        pv = steps.make_preview(out, j["name"])
        j["preview"] = pv
        notify.discord_file("▶️ **%s** önizleme (%.0f sn)" % (j["name"], j["duration"]), pv)
    except Exception as e:
        log("önizleme üretilemedi:", e)
    if hold:  # düzeltmeden sonra hâlâ ciddi sorun var: yayına gitmesin, kullanıcıya sor
        j["status"], j["held"] = "review", True
        dm("⚠️ **%s** otomatik kontrolden geçemedi (%s). Yayına ALINMADI. Önizlemeye bak: batch/previews/%s.mp4 | "
           "Uygunsa DM: onay %s · atmak için: iptal %s" % (_disp(j), hold, j["name"], _disp(j), _disp(j)))
        return False


def stage_caption(st, j):
    sb = _sb(j)
    credits = json.load(open("%s/work/%s/credits.json" % (ROOT, j["name"]), encoding="utf-8"))
    cap = {"instagram": sb["instagram"], "youtube_title": sb["youtube_title"][:100], "youtube_desc": sb["youtube_desc"],
           "tiktok": sb["tiktok"], "hashtags": sb["hashtags"], "credits": credits}
    for k in ("instagram", "youtube_title", "youtube_desc"):
        if not cap[k].strip():
            raise RuntimeError("caption eksik alan: " + k)
    os.makedirs(ROOT + "/captions", exist_ok=True)
    json.dump(cap, open("%s/captions/%s.json" % (ROOT, j["name"]), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def next_slot(st, after=None):
    """PUBLISH_SLOTS (yerel saat) içinde, boş ve gelecekteki ilk slot."""
    slots = [s.strip() for s in env("PUBLISH_SLOTS", "12:30,20:00").split(",")]
    taken = {j["publish_at"] for j in st["jobs"].values() if j.get("publish_at") and j["status"] in ("ready",)}
    t0 = (after or now()) + datetime.timedelta(minutes=30)
    for d in range(0, 30):
        day = (t0 + datetime.timedelta(days=d)).date()
        for s in slots:
            hh, mm = map(int, s.split(":"))
            cand = datetime.datetime(day.year, day.month, day.day, hh, mm)
            if cand >= t0 and iso(cand) not in taken:
                return cand
    raise RuntimeError("boş slot yok")


def stage_schedule(st, j):
    if j.pop("approved", False):  # kullanıcı onay verirken saati zaten belirtti
        j["status"] = "ready"
        return
    if env("APPROVAL_REQUIRED", "1") == "1":  # varsayılan: yayın için senin onayın şart
        j["status"], j["held"] = "review", False
        w = (" | ⚠ %d render uyarısı (layout/kontrast)." % len(j["warnings"])) if j.get("warnings") else ""
        dm("🎞️ **%s** hazır, onayını bekliyor: «%s»%s | Önizleme: batch/previews/%s.mp4 (Discord'a da yüklendi) | "
           "Yayın için DM: onay 16.45 (saat) · onay şimdi · onay (sıradaki slot: %s) · atmak için: iptal | "
           "birden fazla bekleyen varsa adını ekle: onay %s 16.45"
           % (_disp(j), j.get("title", ""), w, j["name"], next_slot(st).strftime("%d.%m %H:%M"), _disp(j)))
        return False
    j["publish_at"] = iso(next_slot(st))
    j["status"] = "ready"
    dm("📅 **%s** planlandı: «%s» | %s'de yayınlanacak. İptal için DM: iptal %s" % (
        _disp(j), j.get("title", ""), j["publish_at"].replace("T", " "), _disp(j)))


STAGE_MSG = {
    "script": "📝 **{name}** — konu belirlendi, araştırıldı, senaryo yazıldı: «{title}»",
    "photos": "🖼 **{name}** — fotoğraflar toplandı ve seçildi",
    "voice": "🎙 **{name}** — ses oluşturuldu (ElevenLabs)",
    "transcribe": "🔤 **{name}** — ses zamanlaması çıkarıldı (whisper)",
    "compose": "🎬 **{name}** — video oluşturuldu ({dur:.0f} sn, render uyarısı: {warn}) · önizleme: batch/previews/{full}.mp4",
    "caption": "✍️ **{name}** — başlık/açıklama/hashtag hazır",
}
FUNCS = {"script": stage_script, "photos": stage_photos, "voice": stage_voice, "transcribe": stage_transcribe,
         "compose": stage_compose, "caption": stage_caption, "schedule": stage_schedule}


HEAVY = {"transcribe", "compose"}  # whisper + render: GPU/CPU ağır
_last_poll = [0.0]


def _poll_between(st):
    """Uzun üretim sırasında da komutlara (onay/iptal) ve yeni DM'lere ≥45 sn arayla bak."""
    if time.time() - _last_poll[0] < 45 or not (env("IG_ACCESS_TOKEN") and env("OWNER_IGSID")):
        return
    _last_poll[0] = time.time()
    try:
        poll_inbox(st)
        publish_due(st)
        save_state(st)
    except Exception as e:
        log("aşama arası tarama hatası:", e)


def advance(st, j):
    """İşi tamamlanana (ready) ya da hataya kadar ilerletir. Her aşama sonrası state kaydedilir."""
    while j["status"] in STAGES:
        stage = j["status"]
        _poll_between(st)
        if j["status"] not in STAGES:  # tarama sırasında bu iş iptal edildi
            return
        if stage in HEAVY and user_busy():  # oyun/tam ekran açık: ağır adımı bekle, iş kaldığı yerden sürer
            log("kullanıcı meşgul (tam ekran), ağır aşama bekletildi:", j.get("name"), stage)
            return
        try:
            log("[%s] aşama: %s" % (j.get("name") or j["id"], stage))
            ok = FUNCS[stage](st, j)
            if ok is False:  # skip
                save_state(st)
                return
            j["attempts"], j["error"] = 0, None
            if stage in STAGE_MSG:
                dm(STAGE_MSG[stage].format(name=_disp(j), full=j.get("name") or j["id"], title=j.get("title", ""), dur=j.get("duration") or 0,
                                           warn=len(j.get("warnings") or [])), important=False)
            if j["status"] == stage:
                j["status"] = STAGES[STAGES.index(stage) + 1]
        except Exception as e:
            j["attempts"] += 1
            j["error"] = "%s: %s" % (stage, e)
            log("HATA", j.get("name") or j["id"], j["error"])
            if j["attempts"] >= MAX_ATTEMPTS:
                j["status"] = "failed"
                dm("Üretim başarısız (%s): %s" % (j.get("name") or j["id"], str(e)[:300]))
            save_state(st)
            return
        save_state(st)


# ---------- yayın ----------
def _configured(p):
    if p == "instagram":
        return bool(env("IG_ACCESS_TOKEN") and env("IG_BUSINESS_USER_ID"))
    if p == "youtube":
        return bool(env("YOUTUBE_REFRESH_TOKEN") and env("YOUTUBE_CLIENT_ID") and env("YOUTUBE_CLIENT_SECRET"))
    return False


def _next_daily(st, hhmm):
    """Sabit saatli (günlük) yayınlar için bir sonraki boş gün: bugün saat geçmediyse bugün, yoksa ertesi gün..."""
    hh, mm = hhmm
    taken = {j["publish_at"] for j in st["jobs"].values() if j.get("publish_at") and j["status"] == "ready"}
    day = now().date()
    for _ in range(400):
        cand = datetime.datetime(day.year, day.month, day.day, hh, mm)
        if cand > now() + datetime.timedelta(minutes=2) and iso(cand) not in taken:
            return cand
        day += datetime.timedelta(days=1)


def publish_due(st):
    wanted = [p.strip() for p in env("PLATFORMS", "instagram,youtube").split(",") if p.strip()]
    platforms = [p for p in wanted if _configured(p)]
    if len(platforms) < len(wanted) and env("ALLOW_PARTIAL", "0") != "1":
        due = [j for j in st["jobs"].values() if j["status"] == "ready" and parse_iso(j["publish_at"]) <= now()]
        if due:
            log("yayın bekletiliyor: yapılandırılmamış platform var:", [p for p in wanted if p not in platforms])
            fresh = [j for j in due if not j.get("hold_notified")]
            if fresh:
                for j in fresh:
                    j["hold_notified"] = True
                dm("⏸ %d video yayın saatine geldi ama bekletiliyor: %s bağlı değil. Bağlanınca günde 1 video 12:00'de çıkar." %
                   (len(due), ", ".join(p for p in wanted if p not in platforms)))
        return  # varsayılan: tüm platformlar hazır değilse hiçbir video yarım yayınlanmaz
    # Saati KAÇAN video (bilgisayar kapalıydı) kendiliğinden yayınlanmaz: kullanıcıya sorulur ("onay <ad> şimdi / 16.45 / yarın 12.00" ya da "iptal <ad>").
    # Küçük gecikmeler (zamanlayıcı/uyku sapması, MISSED_GRACE_MIN dk) normal yayınlanır. Günlük video sayısı sınırı YOKTUR.
    grace = float(env("MISSED_GRACE_MIN", "15"))
    due = sorted((j for j in st["jobs"].values() if j["status"] == "ready" and parse_iso(j["publish_at"]) <= now()), key=lambda j: j["publish_at"])
    missed = [j for j in due if (now() - parse_iso(j["publish_at"])).total_seconds() / 60 > grace]
    if missed:
        for j in missed:
            j["status"], j["missed_from"] = "missed", j["publish_at"]
        save_state(st)
        satirlar = " | ".join("%s (planlanan %s)" % (_disp(j), whenparse.label(parse_iso(j["missed_from"]), now()) + " " + j["missed_from"][11:16]) for j in missed)
        ornek = _disp(missed[0])
        dm("⏰ %d video yayın saatini kaçırdı (bilgisayar kapalıydı): %s | Ne zaman yayınlansın? Her biri için yazın: "
           "onay %s şimdi · onay %s 16.45 · onay %s yarın 12.00 · iptal %s" % (len(missed), satirlar, ornek, ornek, ornek, ornek))
        due = [j for j in due if j not in missed]
    for j in due:
        late_min = (now() - parse_iso(j["publish_at"])).total_seconds() / 60
        cap = json.load(open("%s/captions/%s.json" % (ROOT, j["name"]), encoding="utf-8"))
        video = "%s/renders/%s.mp4" % (ROOT, j["name"])
        for p in platforms:
            if p in j["posted"]:
                continue
            try:
                if p == "instagram":
                    j["posted"][p] = ig.publish_reel(video, cap["instagram"])
                elif p == "youtube":
                    j["posted"][p] = yt.upload_short(video, cap["youtube_title"], cap["youtube_desc"],
                                                     [h.lstrip("#") for h in cap.get("hashtags", [])])
                log("yayınlandı:", j["name"], p, j["posted"][p])
            except Exception as e:
                j["error"] = "%s yayın: %s" % (p, e)
                log("YAYIN HATASI", j["name"], p, e)
            save_state(st)
        if all(p in j["posted"] for p in platforms):
            j["status"], j["error"], j["published_at"] = "published", None, iso(now())
            dm("✅ **%s** yayınlandı (%s)" % (_disp(j), ", ".join("%s: %s" % kv for kv in j["posted"].items())))
        else:
            j["publish_attempts"] = j.get("publish_attempts", 0) + 1
            if j["publish_attempts"] >= 4:
                j["status"] = "failed"
                dm("Yayın başarısız (%s): %s" % (_disp(j), j["error"][:300]))
        save_state(st)
