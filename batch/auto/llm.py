"""Anthropic API katmanı (Sonnet): konu çıkarma -> bağımsız araştırma (web_search) -> senaryo+storyboard+tasarım+caption -> fotoğraf seçimi (görsel).

Özgünlük tasarımı: kaynak reelin metni YALNIZCA konu çıkarma çağrısına gider; senaryoyu yazan çağrı sadece konu + araştırılmış olguları görür.
"""
import base64, json, os, re, subprocess
import anthropic
from common import *
from templates import DISPLAY_FONTS, BODY_FONTS, LAYOUTS

MODEL = env("ANTHROPIC_MODEL", "claude-sonnet-5-5")
FALLBACK_BETA = "server-side-fallback-2026-07-01"
_client = None


def client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=env("ANTHROPIC_API_KEY", required=True), max_retries=3, timeout=600.0)
    return _client


def _prompt(name):
    return open(os.path.join(AUTO, "prompts", name + ".md"), encoding="utf-8").read()


def _create(**kw):
    """messages.create + sunucu tarafı refusal fallback ('default'); fallback reddedilirse onsuz dener."""
    tag = kw.pop("_tag", "")
    try:
        r = client().messages.create(model=MODEL, extra_body={"fallbacks": "default"},
                                     extra_headers={"anthropic-beta": FALLBACK_BETA}, **kw)
    except anthropic.BadRequestError as e:
        if "fallback" not in str(e).lower():
            raise
        r = client().messages.create(model=MODEL, **kw)
    log("API %s in=%s out=%s" % (tag, r.usage.input_tokens, r.usage.output_tokens))
    if r.stop_reason == "refusal":
        raise RuntimeError("model reddetti (refusal): %s" % (getattr(r, "stop_details", None),))
    if r.stop_reason == "max_tokens":
        raise RuntimeError("çıktı max_tokens'ta kesildi")
    return r


def _text(r):
    return "".join(b.text for b in r.content if b.type == "text").strip()


def _json(r):
    return json.loads(_text(r))


# ---------- 1) konu çıkarma (kaynak reelin metni YALNIZCA burada görünür) ----------
TOPIC_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["topic", "usable"],
                "properties": {"topic": {"type": "string"}, "usable": {"type": "boolean"}}}


def extract_topic(transcript, frame_paths=(), note=""):
    content = []
    for p in frame_paths:
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                    "data": base64.standard_b64encode(open(p, "rb").read()).decode()}})
    ask = ("Aşağıdaki, bir sosyal medya videosunun konuşma metni%s. Bu videonun KONUSUNU tek bir nötr cümleyle "
           "(neyi/kimi/hangi olayı anlattığı) yaz. Cümlelerini, yapısını, sloganını aktarma; sadece konu. "
           "Konu anlaşılmıyorsa usable=false." % (" ve kareleri" if frame_paths else ""))
    if note:
        ask += (" ÖNEMLİ: Kullanıcı reel'i gönderirken şu notu yazdı: «%s». Bu not videonun odağını/açısını belirler; konu cümlesini "
                "nota göre kur (reel yalnızca bağlam). Not tek başına yeterliyse usable=true." % note)
    content.append({"type": "text", "text": ask + "\n\n<metin>\n" + (transcript[:6000] or "(ses yok)") + "\n</metin>"})
    r = _create(_tag="topic", max_tokens=1000, output_config={"effort": "low", "format": {"type": "json_schema", "schema": TOPIC_SCHEMA}},
                messages=[{"role": "user", "content": content}])
    return _json(r)


# ---------- 2) bağımsız araştırma ----------
def research(topic):
    messages = [{"role": "user", "content": "KONU: " + topic}]
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 6}]
    for _ in range(6):  # pause_turn: sunucu tarafı araç döngüsü sürsün
        r = _create(_tag="research", max_tokens=16000, system=_prompt("research_system"), tools=tools,
                    output_config={"effort": "medium"}, messages=messages)
        if r.stop_reason != "pause_turn":
            return _text(r)
        messages = [messages[0], {"role": "assistant", "content": r.content}]
    raise RuntimeError("araştırma tamamlanamadı (pause_turn döngüsü)")


# ---------- 3) senaryo + storyboard + tasarım + caption ----------
_S, _A = {"type": "string"}, {"type": "array"}
_LINE = {"type": "object", "additionalProperties": False, "required": ["text", "at_word"], "properties": {"text": _S, "at_word": _S}}
STORY_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["skip", "slug", "title", "domain", "script", "terms", "design", "scenes", "facts", "sources", "instagram", "youtube_title",
                 "youtube_desc", "tiktok", "hashtags"],
    "properties": {
        "skip": _S, "slug": _S, "title": _S, "script": _S, "domain": _S,
        "terms": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["term", "how_explained"],
                                              "properties": {"term": _S, "how_explained": _S}}},
        "design": {"type": "object", "additionalProperties": False, "required": ["display_font", "body_font", "bg", "ink", "accent", "mood"],
                   "properties": {"display_font": {"type": "string", "enum": list(DISPLAY_FONTS)},
                                  "body_font": {"type": "string", "enum": list(BODY_FONTS)},
                                  "bg": _S, "ink": _S, "accent": _S, "mood": _S}},
        "scenes": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["layout", "start_word", "photo_queries", "kicker", "emoji", "lines", "labels", "stat_value", "stat_prefix", "stat_suffix", "stat_at_word"],
            "properties": {"layout": {"type": "string", "enum": LAYOUTS}, "start_word": _S, "photo_queries": {"type": "array", "items": _S},
                           "kicker": _S, "emoji": _S, "lines": {"type": "array", "items": _LINE}, "labels": {"type": "array", "items": _LINE},
                           "stat_value": {"type": "number"}, "stat_prefix": _S, "stat_suffix": _S, "stat_at_word": _S}}},
        "facts": {"type": "array", "items": _S},
        "sources": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["title", "url"],
                                               "properties": {"title": _S, "url": _S}}},
        "instagram": _S, "youtube_title": _S, "youtube_desc": _S, "tiktok": _S, "hashtags": {"type": "array", "items": _S},
    },
}


def make_storyboard(topic, facts_text, existing_titles, recent_designs, note=""):
    user = ("KONU: %s\n\nDOĞRULANMIŞ OLGULAR (araştırma çıktısı):\n%s\n\nMevcut videolarımız (aynı/benzer konu olmasın): %s\n\n"
            "recent_designs (bunlardan farklı bir görünüm seç): %s\n\n"
            "Kullanılabilir display_font: %s\nKullanılabilir body_font: %s%s"
            % (topic, facts_text, existing_titles or "-", json.dumps(recent_designs, ensure_ascii=False) if recent_designs else "-",
               ", ".join(DISPLAY_FONTS), ", ".join(BODY_FONTS),
               ("\n\nKULLANICI İSTEĞİ (öncelikli yönerge, videonun odağı/açısı bu): " + note) if note else ""))
    r = _create(_tag="storyboard", max_tokens=16000, system=_prompt("storyboard_system"),
                output_config={"effort": "high", "format": {"type": "json_schema", "schema": STORY_SCHEMA}},
                messages=[{"role": "user", "content": user}])
    return _json(r)


# ---------- 4) fotoğraf seçimi (görsel) ----------
PICK_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["picks"],
               "properties": {"picks": {"type": "array", "items": {
                   "type": "object", "additionalProperties": False, "required": ["slot", "choices"],
                   "properties": {"slot": _S, "choices": {"type": "array", "items": {"type": "integer"}}}}}}}


def _thumb(path, w=420):
    out = path + ".t.jpg"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", path, "-vf", "scale=%d:-2" % w, "-q:v", "5", out], check=True)
    return base64.standard_b64encode(open(out, "rb").read()).decode()


def pick_photos(topic, slots):
    """slots: [{"slot": "s3_1", "query": "...", "context": "...", "cands": [path, ...]}] -> {slot: [index, ...]}"""
    content = [{"type": "text", "text":
                "Bir bilgilendirici video için her slota en uygun fotoğrafı seç. Konu: %s\nÖlçüt: sorguya/bağlama gerçekten uyan, net, "
                "kompozisyonu dikey kadrajda (kırpılınca) iyi duran, üstünde bol yazı/filigran/logo/kolaj olmayan, uygunsuz olmayan. "
                "Her slot için en fazla 2 aday seç (choices, 0 tabanlı numara; ilki en iyi, ikincisi aynı konuyu farklı açı/kompozisyonla gösteren, "
                "birincisine çok benzemeyen bir fotoğraf; ikincisi yoksa tek eleman ver). Hiçbiri uygun değilse boş liste ver." % topic}]
    for s in slots:
        content.append({"type": "text", "text": "\n[SLOT %s] sorgu: %s | sahne: %s" % (s["slot"], s["query"], s["context"])})
        for k, p in enumerate(s["cands"]):
            content.append({"type": "text", "text": "aday %d:" % k})
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": _thumb(p)}})
    r = _create(_tag="pick", max_tokens=4000, output_config={"effort": "low", "format": {"type": "json_schema", "schema": PICK_SCHEMA}},
                messages=[{"role": "user", "content": content}])
    return {p["slot"]: p["choices"] for p in _json(r)["picks"]}


# ---------- 5) otomatik görsel kalite kontrol ----------
QA_TYPES = ["text_only", "text_overlap", "unreadable_text", "text_cut_off", "emoji_overlap", "irrelevant_photo",
            "low_quality_photo", "empty_layout", "other"]
QA_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["overall", "scenes"], "properties": {
    "overall": {"type": "string", "enum": ["pass", "fix"]},
    "scenes": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["scene", "ok", "problems"], "properties": {
        "scene": {"type": "integer"}, "ok": {"type": "boolean"},
        "problems": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["type", "detail", "better_query"],
                                                "properties": {"type": {"type": "string", "enum": QA_TYPES}, "detail": _S, "better_query": _S}}}}}}}}


def review_frames(topic, items):
    """items: [{"scene": i, "frames": [path, ...], "ctx": "düzen/yazı/sorgu özeti"}] -> {"overall", "scenes": [...]}"""
    content = [{"type": "text", "text": "Video konusu: %s. Aşağıda sahne sahne kareler var." % topic}]
    for it in items:
        content.append({"type": "text", "text": "[SAHNE %d] amaçlanan: %s" % (it["scene"], it["ctx"])})
        for f in it["frames"]:
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": _thumb(f, 400)}})
    r = _create(_tag="qa", max_tokens=4000, system=_prompt("qa_system"),
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": QA_SCHEMA}},
                messages=[{"role": "user", "content": content}])
    return _json(r)


# ---------- 6) eski videolar için yayın metinleri (tek toplu çağrı) ----------
CAP_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["items"], "properties": {"items": {
    "type": "array", "items": {"type": "object", "additionalProperties": False,
                               "required": ["name", "title", "instagram", "youtube_title", "youtube_desc", "tiktok", "hashtags"],
                               "properties": {"name": _S, "title": _S, "instagram": _S, "youtube_title": _S, "youtube_desc": _S,
                                              "tiktok": _S, "hashtags": {"type": "array", "items": _S}}}}}}


CAPTION_RULE = ('Açıklama metni (caption) senaryonun özeti ya da tekrarı OLMAMALI; amacı videoyu izletmek, yorum ve paylaşım getirmek. KISA ve İLGİ ÇEKİCİ yaz. instagram: 1. satır ≤ 80 karakter bir kanca (şaşırtıcı iddia, soru ya da merak boşluğu; senaryonun ilk cümlesini TEKRAR ETME); sonra en fazla 2 kısa cümle: bilginin tamamını verme, merak bırak ve videoya yönlendir; en sonda tek satır soru/çağrı (örn. yorumlara yaz); ardından boş satır ve 5-8 hashtag. Hashtag hariç toplam 250 karakteri GEÇME, en fazla 1-2 emoji. youtube_title: ≤ 70 karakter, merak uyandıran ama dürüst. youtube_desc: en fazla 2 kısa satır + #Shorts + 3-5 hashtag (özet yazma). tiktok: ≤ 100 karakter, kanca + 3 hashtag. hashtags: 5-8 adet (# ile). Yeni olgu ekleme, yalanlı clickbait yapma.')


def make_captions(items):
    """items: [{"name": "01_kahve", "script": "..."}] -> [{"name", "title", "instagram", ...}] (yalnızca senaryodaki bilgilerle)."""
    rules = ("Aşağıdaki her Türkçe bilgilendirici kısa video için yayın metinlerini yaz. YALNIZCA verilen senaryodaki bilgileri kullan, yeni olgu ekleme. "
             "Her video için title (kısa Türkçe başlık) de ver. " + CAPTION_RULE + " Her videoyu birbirinden farklı yaz, aynı kalıbı tekrarlama.")
    body = " || ".join("[%s] %s" % (it["name"], it["script"].replace(chr(10), " ")) for it in items)
    r = _create(_tag="captions", max_tokens=16000, system=rules, output_config={"effort": "low", "format": {"type": "json_schema", "schema": CAP_SCHEMA}},
                messages=[{"role": "user", "content": body}])
    return _json(r)["items"]
