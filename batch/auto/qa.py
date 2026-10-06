"""Otomatik görsel kalite kontrol: render'dan sahne kareleri al -> Sonnet incele -> sorunları yerelde düzelt (fotoğraf değiştir,
karta çevir, yazıyı küçült, emoji'yi kaldır). Düzeltme sonrası bir kez daha incelenir; hâlâ ciddi sorun varsa video 'review' durumuna alınır."""
import glob, json, os, subprocess
from common import *
import llm

FRAME_DIR = "qa_frames"


def extract(name, timing):
    """Her sahne için 2 kare (başlangıç+1 sn, orta). Dönen: [{"scene", "frames", "ctx"}]"""
    src = os.path.join(ROOT, "renders", name + ".mp4")
    out_dir = os.path.join(ROOT, "work", name, FRAME_DIR)
    os.makedirs(out_dir, exist_ok=True)
    items = []
    for sc in timing:
        i, t0, t1 = sc["scene"], sc["t0"], sc["t1"]
        # sahne oturduktan sonraki anlar (yazılar gelmiş olsun): orta ve sahne bitmeden hemen önce
        times = sorted({round(max(t0 + 0.5, min((t0 + t1) / 2, t1 - 0.4)), 2), round(max(t0 + 0.8, t1 - 0.7), 2)})
        frames = []
        for k, t in enumerate(times):
            f = os.path.join(out_dir, "s%d_%d.jpg" % (i, k))
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", str(t), "-i", src, "-frames:v", "1", "-vf", "scale=400:-2", f],
                           check=True, creationflags=LOW_PRIO)
            frames.append(f)
        items.append({"scene": i, "frames": frames,
                      "ctx": "düzen=%s; yazılar=%s; %.1f–%.1f sn" % (sc["layout"], " / ".join(sc["lines"]) or "-", t0, t1)})
    return items


def review(name, topic, sb):
    """Dönen: (sonuç dict | None). API hatasında None (kontrol atlanır, üretim durmaz)."""
    try:
        timing = json.load(open(os.path.join(ROOT, "work", name, "timing.json"), encoding="utf-8"))
        res = llm.review_frames(topic, extract(name, timing))
        json.dump(res, open(os.path.join(ROOT, "work", name, "qa.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        return res
    except Exception as e:
        log("QA atlandı (hata):", e)
        return None


def _used_photos(chosen):
    return {f for k, v in chosen.items() if k != "_borrowed" for f in (v if isinstance(v, list) else [v])}


def _spare(name, chosen, taken):
    norm = lambda p: os.path.normcase(os.path.abspath(p))
    used = {norm(u) for u in (_used_photos(chosen) | set(taken))}
    return [c for c in sorted(glob.glob(os.path.join(ROOT, "work", name, "cand", "*.jpg")))
            if not c.endswith(".t.jpg") and norm(c) not in used]


def apply_fixes(name, res, ov, chosen, sb):
    """QA sorunlarını override'lara çevirir. Dönen: (yeni override'lar, yapılan düzeltmelerin özeti listesi)."""
    ov = {int(k): dict(v) for k, v in ov.items()}
    notes, taken = [], []
    for sc in res["scenes"]:
        if sc["ok"] or not sc["problems"]:
            continue
        i = sc["scene"]
        o = ov.setdefault(i, {})
        for p in sc["problems"]:
            t = p["type"]
            if t in ("irrelevant_photo", "low_quality_photo"):
                spare = []
                q = (p.get("better_query") or "").strip()
                if q:  # QA'nın önerdiği daha iyi sorguyla yeniden ara (API'siz, ücretsiz arama)
                    try:
                        import photos
                        got = photos.fetch_slot(name, "qa%d_%d" % (i, len(taken)), q, budget=40)
                        spare = [g["file"] for g in got]
                    except Exception as e:
                        log("QA yeniden arama hatası:", e)
                spare = spare or _spare(name, chosen, taken)
                if spare:
                    o["photos"] = [spare[0]]
                    taken.append(spare[0])
                    notes.append("sahne %d: fotoğraf değiştirildi" % i)
                else:
                    o["layout"] = "quote" if sb["scenes"][i]["layout"] == "quote" else o.get("layout")
                    notes.append("sahne %d: yedek fotoğraf yok" % i)
            elif t in ("text_only", "empty_layout"):
                o["layout"] = "card"
                notes.append("sahne %d: fotoğraflı karta çevrildi" % i)
            elif t in ("text_overlap", "unreadable_text", "text_cut_off"):
                o["fs_scale"] = round(o.get("fs_scale", 1.0) * 0.86, 2)
                notes.append("sahne %d: yazı küçültüldü" % i)
            elif t == "emoji_overlap":
                o["drop_emoji"] = True
                notes.append("sahne %d: emoji kaldırıldı" % i)
    return ov, notes


def serious(res):
    """Düzeltmeden sonra hâlâ kalan sorunlardan 'ciddi' olanlar (okunmama, taşma, çakışma, yazıdan ibaret ekran, alakasız foto)."""
    bad = {"text_only", "text_overlap", "unreadable_text", "text_cut_off", "irrelevant_photo"}
    return [(s["scene"], p["type"], p["detail"]) for s in res["scenes"] for p in s["problems"] if p["type"] in bad]
