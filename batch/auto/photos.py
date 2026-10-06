"""Fotoğraf toplama (AI'sız): Claude'un verdiği sorgularla Wikimedia Commons + Openverse'ten aday indirir.
Lisans filtresi YOK (kullanıcı kararı); meta (yazar/lisans/kaynak) yine de kaydedilir.
Seçim: llm.pick_photos (görsel) ya da API yoksa ilk aday.
"""
import json, os, re, time, urllib.parse
from common import *

UA = {"User-Agent": "faceless-explainers/1.0 (personal educational project)"}
PER_QUERY = 4


_DEADLINE = [0.0]  # fetch_slot'un süre bütçesi: aşılırsa istekler beklemeden vazgeçer (429 bekleme döngüleri dakikalarca sürebiliyor)


def _expired():
    return bool(_DEADLINE[0]) and time.time() > _DEADLINE[0]


def _get(url, params=None, binary=False, tries=4, delay=1.0):
    for a in range(tries):
        if _expired():
            return None
        try:
            time.sleep(delay)
            return http("GET", url, params=params, headers=UA, raw=binary, timeout=60)
        except HttpError as e:
            if e.code in (403, 404, 410):
                return None
            if _expired():
                return None
            time.sleep(min(15 * (a + 1), 30) if e.code == 429 else 3)
        except Exception:
            time.sleep(3)
    return None


def _google(q):
    """Google Görseller (Serper.dev API). SERPER_API_KEY yoksa boş döner. Boyut filtresi + kaynak bilgisi kaydedilir."""
    key = env("SERPER_API_KEY")
    if not key:
        return []
    try:
        r = http("POST", "https://google.serper.dev/images", json_body={"q": q, "num": 12}, headers={"X-API-KEY": key, "User-Agent": UA["User-Agent"]}, timeout=30)
    except Exception as e:
        log("Google görsel arama hatası:", str(e)[:120])
        return []
    out = []
    for x in r.get("images", []):
        w, h = x.get("imageWidth") or 0, x.get("imageHeight") or 0
        if not x.get("imageUrl") or w < 900 or h < 700:
            continue
        out.append({"url": x["imageUrl"], "w": w, "h": h, "title": x.get("title", ""), "artist": x.get("source", ""),
                    "license": "?", "page": x.get("link", ""), "src": "google"})
    return out


def _is_image(data):
    return data[:3] == bytes([0xFF, 0xD8, 0xFF]) or data[:4] == bytes([0x89, 0x50, 0x4E, 0x47]) or (data[:4] == b'RIFF' and data[8:12] == b'WEBP')


def _wikimedia(q):
    r = _get("https://commons.wikimedia.org/w/api.php", {
        "action": "query", "generator": "search", "gsrsearch": q + " filetype:bitmap", "gsrnamespace": "6", "gsrlimit": "10",
        "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata", "iiurlwidth": "1600", "format": "json"}, delay=1.5)
    out = []
    pages = sorted((r or {}).get("query", {}).get("pages", {}).values(), key=lambda x: x.get("index", 0))
    for pg in pages:
        ii = (pg.get("imageinfo") or [{}])[0]
        if ii.get("mime") not in ("image/jpeg", "image/png") or ii.get("width", 0) < 900 or ii.get("height", 0) < 700:
            continue
        md = ii.get("extmetadata", {})
        out.append({"url": ii.get("thumburl") or ii["url"], "w": ii["width"], "h": ii["height"], "title": pg["title"],
                    "artist": re.sub("<[^>]+>", "", md.get("Artist", {}).get("value", "?"))[:80],
                    "license": md.get("LicenseShortName", {}).get("value", "?"), "page": ii.get("descriptionurl", ""), "src": "wikimedia"})
    return out


def _openverse(q):
    r = _get("https://api.openverse.org/v1/images/", {"q": q, "page_size": 14, "mature": "false"})
    out = []
    for x in (r or {}).get("results", []):
        if x.get("source") == "wikimedia" or (x.get("width") or 0) < 900 or (x.get("height") or 0) < 700:
            continue
        out.append({"url": x["url"], "w": x.get("width"), "h": x.get("height"), "title": x.get("title", ""),
                    "artist": x.get("creator") or "?", "license": x.get("license", "?"), "page": x.get("foreign_landing_url", ""),
                    "src": "openverse"})
    return out


def variants(q):
    """Sonuç çıkmazsa denenecek daha kısa/geniş sorgular."""
    w = q.split()
    out = [q]
    if len(w) > 3:
        out.append(" ".join(w[:3]))
    if len(w) > 2:
        out.append(" ".join(w[-2:]))
    if len(w) >= 2:
        out.append(w[0])
    seen, res = set(), []
    for x in out:
        if x.lower() not in seen:
            seen.add(x.lower())
            res.append(x)
    return res


def fetch_slot(name, slot, query, prefer_tall=False, budget=None):
    """Slot için aday indirir (süre bütçesi: PHOTO_BUDGET_SEC, QA yeniden aramasında kısa)."""
    _DEADLINE[0] = time.time() + (budget or float(env("PHOTO_BUDGET_SEC", "90")))
    try:
        return _fetch_slot(name, slot, query, prefer_tall)
    finally:
        _DEADLINE[0] = 0.0


def _fetch_slot(name, slot, query, prefer_tall=False):
    """Slot için aday indirir; [{file, ...meta}] döndürür. Sonuç yoksa kısaltılmış sorgularla yeniden dener."""
    d = os.path.join(ROOT, "work", name, "cand")
    os.makedirs(d, exist_ok=True)
    cands = []
    for q in variants(query):
        cands = _google(q)  # anahtar varsa önce Google Görseller (en alakalı), yetmezse Wikimedia/Openverse
        if len(cands) < PER_QUERY:
            cands += _wikimedia(q)
        if len(cands) < PER_QUERY:
            cands += _openverse(q)
        if len(cands) >= 2:
            break
    if prefer_tall:  # tam ekran hero: büyütme bulanıklığı olmasın
        cands.sort(key=lambda c: 0 if (c["h"] or 0) >= 1100 else 1)
    got = []
    for c in cands:
        if len(got) >= PER_QUERY:
            break
        fn = os.path.join(d, "%s_%d.jpg" % (slot, len(got)))
        data = _get(c["url"], binary=True, delay=0.7)
        if not data or len(data) < 20000 or not _is_image(data):  # hotlink koruması/HTML hata sayfası olabilir
            continue
        open(fn, "wb").write(data)
        got.append(dict(c, file=fn))
    return got
