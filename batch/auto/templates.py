"""Storyboard JSON -> HyperFrames projesi (eng2 üzerinden). Tasarımı (font/renk) Claude seçer; kod kontrastı, sığdırmayı,
safe-zone'u ve kelime senkronunu garanti eder. Sahne düzenleri: hero, card, stat, trio, quote."""
import html, json, os, re

LAYOUTS = ["hero", "card", "stat", "trio", "quote"]
# ad -> (google font spec, css weight, ortalama karakter genişliği / punto) -- hepsi fontcache'te var ve latin-ext içerir
DISPLAY_FONTS = {
    "Abril Fatface": ("", 400, 0.56), "Alfa Slab One": ("", 400, 0.66), "Anton": ("", 400, 0.45),
    "Archivo Black": ("", 400, 0.66), "Baloo 2": ("wght@800", 800, 0.58), "Barlow Condensed": ("wght@700;800", 800, 0.42),
    "Bricolage Grotesque": ("wght@700;800", 800, 0.56), "Cinzel": ("wght@700;900", 900, 0.72),
    "DM Serif Display": ("ital@0;1", 400, 0.52), "Fraunces": ("wght@700;900", 900, 0.60), "Lilita One": ("", 400, 0.50),
    "Passion One": ("wght@900", 900, 0.50), "Playfair Display": ("wght@900", 900, 0.62), "Rozha One": ("", 400, 0.60),
    "Shrikhand": ("", 400, 0.62), "Fredoka": ("wght@500;700", 700, 0.55),
}
BODY_FONTS = {
    "DM Sans": "wght@500;700", "Nunito": "wght@700;800", "IBM Plex Sans": "wght@500;700", "Space Grotesk": "wght@500;700",
    "Outfit": "wght@500;700", "Rubik": "wght@500;700", "Barlow": "wght@500;700", "Quicksand": "wght@700",
}
SFX = ["ui_select", "glitter", "pop_pin", "bell_n", "marimba", "correct"]
X0, W0 = 80, 920


# ---------- renk ----------
def _hex(s, default):
    s = (s or "").strip()
    return s.upper() if re.fullmatch(r"#[0-9a-fA-F]{6}", s) else default


def _rgb(h):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def _tohex(c):
    return "#%02X%02X%02X" % tuple(max(0, min(255, int(round(x)))) for x in c)


def lum(h):
    def f(c):
        c /= 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = _rgb(h)
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def contrast(a, b):
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def mix(a, b, t):
    return _tohex([x + (y - x) * t for x, y in zip(_rgb(a), _rgb(b))])


def make_theme(d):
    bg = _hex(d.get("bg"), "#1B1F3B")
    ink = _hex(d.get("ink"), "#F4F1E8")
    if contrast(ink, bg) < 5.0:
        ink = "#F6F3EC" if lum(bg) < 0.35 else "#14161C"
    acc = _hex(d.get("accent"), "#D4A53A")
    if contrast(acc, bg) < 3.0:  # zemin üstünde okunmuyorsa vurguyu zemine karşı açıp/koyulaştır
        acc = mix(acc, "#FFFFFF" if lum(bg) < 0.35 else "#000000", 0.5)
    accp = acc if lum(acc) >= 0.3 else mix(acc, "#FFFFFF", 0.5)  # fotoğraf üstü (koyu scrim) vurgu
    scrim = mix(bg, "#000000", 0.7)
    return {"bg": bg, "ink": ink, "acc": acc, "accp": accp, "scrim": scrim, "light": lum(bg) >= 0.35,
            "grad": "background:radial-gradient(ellipse at 50%% 30%%, %s 0%%, %s 72%%)" % (mix(bg, "#FFFFFF" if lum(bg) < 0.35 else "#000000", 0.10), bg)}


def rgba(h, a):
    r, g, b = _rgb(h)
    return "rgba(%d,%d,%d,%.2f)" % (r, g, b, a)


# ---------- zaman çözümü ----------
def _norm(x):
    x = x.replace("İ", "i").replace("I", "ı").lower()
    return re.sub(r"[^a-zçğıöşü0-9]", "", x)


def _frac_time(phrase, script, dur):
    """Kelime-eşleşmesi başarısızsa script içindeki karakter konumundan orantılı tahmin."""
    ns, np_ = _norm(script), _norm(phrase)
    i = ns.find(np_) if np_ else -1
    return None if i < 0 else dur * 0.92 * i / max(1, len(ns))


def _at(v, phrase, after, script):
    phrase = (phrase or "").strip()
    if not phrase:
        return None
    words = phrase.split()
    for q in (phrase, words[0], words[-1]):
        if len(_norm(q)) < 2:
            continue
        try:
            return v.t(q, 1, after)
        except KeyError:
            pass
    return _frac_time(phrase, script, v.DUR)


def resolve_starts(v, sb):
    sc, script, dur = sb["scenes"], sb["script"], v.DUR
    starts, prev = [0.0], 0.0
    for s in sc[1:]:
        t = _at(v, s["start_word"], prev + 1.0, script)
        t = (t - 0.1) if t is not None else prev + (dur - prev) / max(2, len(sc))
        t = max(t, prev + 1.8)
        starts.append(round(min(t, dur - 1.6), 2))
        prev = starts[-1]
    return starts


# ---------- sığdırma ----------
_FS = 1.0  # sahne bazlı yazı ölçeği (QA düzeltmesi için)


def fit(lines, base, wf, width=W0, minfs=54):
    longest = max((len(l) for l in lines), default=1)
    return max(int(minfs * _FS), min(int(base * _FS), int(width * _FS / (wf * max(longest, 1)))))


def esc(s):
    return html.escape(s, quote=False)


# ---------- hareket yardımcıları ----------
def seq(v, files, box, t0, t1, enter, **kw):
    """Aynı kutuda 1–3 fotoğrafı sırayla çapraz geçişle gösterir (ekran durağan kalmasın)."""
    n = len(files)
    span = t1 - t0
    for k, f in enumerate(files):
        a = t0 if k == 0 else t0 + span * k / n
        kk = dict(kw)
        if k % 2 == 1 and "kb" in kk:  # her fotoğraf farklı yöne zoom yapsın
            kk["kb"] = (kk["kb"][1], kk["kb"][0])
        v.photo(f, box, a, t1, enter=(enter if k == 0 else "fade"), **kk)


def drift(v, t0, t1, color, k):
    """Arka planda yavaşça kayan yumuşak ışık lekesi (sakin hareket)."""
    from lib import FT, TO
    i = v.uid("dr")
    x0, y0 = [(-260, 180), (520, 820), (80, 1260)][k % 3]
    dx, dy = [(260, -120), (-240, 150), (200, -200)][k % 3]
    html = ('<div id="%s" class="el" style="left:%dpx;top:%dpx;width:900px;height:900px;border-radius:50%%;'
            'background:radial-gradient(circle, %s 0%%, rgba(0,0,0,0) 65%%);filter:blur(24px)"></div>' % (i, x0, y0, rgba(color, 0.20)))
    v.raw(html, FT("#" + i, {"opacity": 0, "x": 0, "y": 0}, {"opacity": 1, "x": dx, "y": dy, "duration": max(1.0, t1 - t0), "ease": "none"}, t0)
          + TO("#" + i, {"opacity": 0, "duration": 0.5}, t1))


BAD_EMOJI = set("🤝🤭🥹🪷🫡🫶🩵")


def clean_emoji(em):
    em = (em or "").strip()
    if not em or len(em) > 6 or any(c in BAD_EMOJI for c in em):
        return ""
    return em


def emoji_badge(v, em, x, y, size, t_in, t_out, rot=-8):
    """Emoji: pop ile girer, sonra sahne boyunca yavaşça süzülür (durgun anları canlandırır)."""
    from lib import FT, TO
    i = v.uid("em")
    v.raw('<div id="%s" class="el" style="left:%dpx;top:%dpx;font-size:%dpx;line-height:1.1;filter:drop-shadow(0 6px 14px rgba(0,0,0,.55))">%s</div>' % (i, x, y, size, em),
          FT("#" + i, {"opacity": 0, "scale": 0.4, "y": 24, "rotation": rot - 10}, {"opacity": 1, "scale": 1, "y": 0, "rotation": rot, "duration": 0.65, "ease": "back.out(1.5)"}, t_in)
          + "tl.to(%s,{y:-16,duration:0.95,yoyo:true,repeat:%d,ease:'sine.inOut'},%.2f);" % (json.dumps("#" + i), max(1, int((t_out - t_in - 0.7) / 0.95)), t_in + 0.7)
          + TO("#" + i, {"opacity": 0, "duration": 0.4}, t_out))


def push_in(v, tid, a, b):
    """Yazı bloğu görünürken çok hafif büyür (ekran durağan kalmasın)."""
    v.raw("", "tl.fromTo(%s,{scale:1,transformOrigin:'0%% 50%%'},{scale:1.04,duration:%.2f,ease:'none'},%.2f);" % (json.dumps("#" + tid), max(1.0, b - a), a))


def early(ts, t0, cap=0.45):
    """Sahnenin ilk öğesi, konuşma beklemeden en geç t0+cap'te gelsin."""
    if ts:
        ts[0] = round(min(ts[0], t0 + cap), 2)
    return ts


def as_list(x):
    return [] if not x else (x if isinstance(x, list) else [x])


# ---------- ana ----------
def build_video(name, sb, photos, overrides=None):
    """photos: {"s{i}_{k}": dosya yolu | None}. Dönen: (proje dizini, zamanlama log'u)."""
    from eng2 import V
    d = sb["design"]
    dfam = d["display_font"] if d["display_font"] in DISPLAY_FONTS else "Rozha One"
    bfam = d["body_font"] if d["body_font"] in BODY_FONTS else "DM Sans"
    dspec, dw, wf = DISPLAY_FONTS[dfam]
    th = make_theme(d)
    css = """
body, #root { font-family: "%s", sans-serif; color: %s; }
.D { font-family: "%s", serif; font-weight: %d; line-height: 1.06; }
.N { font-family: "%s", sans-serif; font-weight: 700; letter-spacing: .24em; text-transform: uppercase; font-size: 28px; }
.sh { text-shadow: 0 4px 28px rgba(0,0,0,.6); }
.pill { font-family: "%s", sans-serif; font-weight: 700; border-radius: 999px; text-align: center; white-space: nowrap; padding: 8px 0 12px; box-shadow: 0 10px 28px rgba(0,0,0,.35); }
""" % (bfam, th["ink"], dfam, dw, bfam, bfam)
    v = V(name, fonts=[(dfam, dspec), (bfam, BODY_FONTS[bfam])], bg=th["bg"], css=css, vignette=0.25)
    sc, script = sb["scenes"], sb["script"]
    starts = resolve_starts(v, sb)
    all_files = [f for k, v_ in photos.items() if k != "_borrowed" for f in as_list(v_)]
    borrowed = set(photos.get("_borrowed", []))
    spare_n = 0
    tids = []
    _text = v.text

    def _tx(*a, **k):
        i = _text(*a, **k)
        if "N" not in (k.get("cls") or "").split() and "pill" not in (k.get("cls") or ""):
            tids.append((i, a[4], a[5]))
        return i
    v.text = _tx
    ends = starts[1:] + [v.DUR]
    log, sfx_used = [], 0

    global _FS
    overrides = overrides or {}
    for i, s in enumerate(sc):
        t0, t1 = starts[i], ends[i]
        ov = overrides.get(i) or overrides.get(str(i)) or {}
        _FS = ov.get("fs_scale", 1.0)
        lay = s["layout"]
        got = []
        for k in range(max(len(s["photo_queries"]), 1)):
            got += [g for g in as_list(photos.get("s%d_%d" % (i, k))) if g and g not in got]
        if ov.get("photos"):  # QA: fotoğrafı değiştir
            got = list(ov["photos"])
        if ov.get("layout") == "card" and lay in ("quote", "hero") and not got and all_files:
            got = [all_files[(i * 2 + 1) % len(all_files)]]
        if ov.get("layout"):
            lay = ov["layout"]
        own = [k for k in range(max(len(s["photo_queries"]), 1)) if "s%d_%d" % (i, k) not in borrowed]
        if lay == "hero" and not own and got:  # ödünç/alakasız fotoğraf tam ekran büyütülmesin: çerçeveli kart
            lay = "card"
        if lay in ("hero", "card") and not got:
            lay = "quote"
        if lay == "quote" and (t1 - t0) > 3.2 and all_files:  # uzun, yalnızca yazılı sahne = durgun ekran: fotoğraflı karta çevir
            got = [all_files[(i * 2 + 1 + spare_n) % len(all_files)]]
            spare_n += 1
            lay = "card"
        if lay == "trio":
            got = [as_list(photos.get("s%d_%d" % (i, k)))[:1] for k in range(3)]
            got = [g[0] for g in got if g]
        if lay == "trio" and len(got) < 3:
            lay = "card" if got else "quote"
        tx1 = t1 - 0.35 if i < len(sc) - 1 else v.DUR  # metin, sonraki sahne başlamadan söner
        ph1 = t1 + 0.25 if i < len(sc) - 1 else v.DUR

        def line_times(items, base):
            ts, last = [], base - 0.2
            for it in items:
                t = _at(v, it["at_word"], max(0.0, t0 - 0.3), script)
                t = (t - 0.18) if t is not None else last + 0.5
                t = min(max(t, t0 + 0.05, last + 0.25), max(t0 + 0.1, tx1 - 0.9))
                ts.append(round(t, 2))
                last = ts[-1]
            return ts

        lines = [l["text"] for l in s["lines"]][:3]
        ltimes = early(line_times(s["lines"][:3], t0), t0)
        seed = i % 3
        log.append({"scene": i, "layout": lay, "t0": t0, "t1": t1, "lines": lines, "times": ltimes})

        if lay == "hero":
            seq(v, got[:3], (0, 0, 1080, 1920), t0, ph1, "fade", kb=(1.0, 1.18), kb_xy=((-30, 30, 0)[seed], 0), dur=0.5)
            v.box("background:linear-gradient(180deg,%s 0%%,%s 30%%,%s 42%%,%s 100%%)" % (rgba(th["scrim"], .45), rgba(th["scrim"], 0), rgba(th["scrim"], 0), rgba(th["scrim"], .92)),
                  (0, 0, 1080, 1920), t0, ph1, enter="fade")
            fs = fit(lines, 96, wf)
            lh = int(fs * 1.14)
            y0 = 1460 - lh * len(lines)
            if s["kicker"]:
                v.text(esc(s["kicker"]), X0, y0 - 64, W0, ltimes[0] if ltimes else t0 + 0.2, tx1, cls="N sh", style="color:%s" % th["accp"], enter="fade")
            for k, l in enumerate(lines):
                last = k == len(lines) - 1 and len(lines) > 1
                v.text(esc(l), X0, y0 + k * lh, W0, ltimes[k], tx1, cls="D sh", style="font-size:%dpx;color:%s" % (fs, th["accp"] if last else "#FFFFFF"), enter="mask")

        elif lay == "card":
            v.bg(th["grad"], t0 - 0.1, ph1, 0.5, 0.5)
            drift(v, t0, ph1, th["acc"], i)
            seq(v, got[:3], (X0, 250, W0, 880), t0 + 0.05, ph1, "up", kb=(1.0, 1.1), radius="18px",
                shadow="0 22px 56px rgba(0,0,0,.45)", border="6px solid %s" % th["acc"])
            fs = fit(lines, 88, wf)
            lh = int(fs * 1.14)
            y0 = 1210 + (60 if s["kicker"] else 0)
            while y0 + lh * len(lines) > 1500 and fs > 56:
                fs -= 6
                lh = int(fs * 1.14)
            if s["kicker"]:
                v.text(esc(s["kicker"]), X0, 1190, W0, ltimes[0] if ltimes else t0 + 0.3, tx1, cls="N", style="color:%s" % th["acc"], enter="fade")
            for k, l in enumerate(lines):
                last = k == len(lines) - 1 and len(lines) > 1
                v.text(esc(l), X0, y0 + k * lh, W0, ltimes[k], tx1, cls="D sh", style="font-size:%dpx;color:%s" % (fs, th["acc"] if last else th["ink"]), enter="rise")

        elif lay == "stat":
            v.bg(th["grad"], t0 - 0.1, ph1, 0.5, 0.5)
            drift(v, t0, ph1, th["acc"], i)
            y = 420
            if got:
                seq(v, got[:2], (X0, 250, W0, 620), t0 + 0.05, ph1, "up", kb=(1.0, 1.1), radius="18px",
                    shadow="0 22px 56px rgba(0,0,0,.45)", border="6px solid %s" % th["acc"])
                y = 910
            val = s["stat_value"]
            pre, post = s["stat_prefix"], s["stat_suffix"]
            ts = _at(v, s["stat_at_word"], max(0.0, t0 - 0.3), script)
            ts = max(t0 + 0.1, (ts - 0.15) if ts is not None else t0 + 0.4)
            ts = min(ts, t0 + 1.3)  # sayaç konuşmayı çok beklemesin
            n = len("%d" % abs(val)) + len(pre) + len(post)
            fs = max(120, min(260, int(W0 / (0.66 * max(n, 1)))))
            if float(val) == int(val) and abs(val) < 10000:
                v.counter(0, int(val), ts, tx1, X0, y, W0, cls="D sh", style="font-size:%dpx;color:%s;text-align:left" % (fs, th["acc"]),
                          dur=1.1, pre=esc(pre), post=esc(post))
            else:
                txt = esc(pre) + (("{:,.0f}".format(val).replace(",", ".")) if float(val) == int(val) else str(val).replace(".", ",")) + esc(post)
                v.text(txt, X0, y, W0, ts, tx1, cls="D sh", style="font-size:%dpx;color:%s" % (fs, th["acc"]), enter="rise")
            lfs = fit(lines, 76, wf)
            lh = int(lfs * 1.14)
            ly = y + int(fs * 1.12) + 24
            while ly + lh * len(lines) > 1500 and lfs > 52:
                lfs -= 6
                lh = int(lfs * 1.14)
            for k, l in enumerate(lines):
                v.text(esc(l), X0, ly + k * lh, W0, max(ltimes[k], ts + 0.3), tx1, cls="D sh", style="font-size:%dpx;color:%s" % (lfs, th["ink"]), enter="rise")

        elif lay == "trio":
            v.bg(th["grad"], t0 - 0.1, ph1, 0.5, 0.5)
            drift(v, t0, ph1, th["acc"], i)
            fs = fit(lines, 84, wf)
            lh = int(fs * 1.14)
            for k, l in enumerate(lines[:2]):
                v.text(esc(l), X0, 250 + k * lh, W0, ltimes[k], tx1, cls="D sh", style="font-size:%dpx;color:%s" % (fs, th["ink"]), enter="rise")
            yp = 250 + lh * max(1, min(2, len(lines))) + 50
            labels = s["labels"][:3] + [{"text": "", "at_word": ""}] * (3 - len(s["labels"][:3]))
            lt = line_times(labels, t0 + 0.4)
            for k in range(3):
                cx = X0 + k * 315
                tk = max(lt[k], t0 + 0.2 + 0.2 * k)
                v.photo(got[k], (cx, yp, 290, 760), tk, ph1, enter="up", kb=(1.0, 1.1), radius="18px",
                        shadow="0 22px 56px rgba(0,0,0,.45)", border="6px solid %s" % th["acc"])
                if labels[k]["text"]:
                    v.text(esc(labels[k]["text"]), cx - 10, yp + 790, 310, tk + 0.1, tx1, cls="pill", enter="rise", align="center",
                           style="font-size:30px;background:%s;color:%s" % (th["ink"], th["bg"]))

        else:  # quote
            v.bg(th["grad"], t0 - 0.1, ph1, 0.5, 0.5)
            drift(v, t0, ph1, th["acc"], i)
            fs = fit(lines, 108, wf)
            lh = int(fs * 1.14)
            y0 = max(300, 960 - lh * len(lines) // 2)
            v.box("background:%s" % th["acc"], (X0, y0 - 50, 120, 8), t0 + 0.1, tx1, enter="grow")
            if s["kicker"]:
                v.text(esc(s["kicker"]), X0, y0 - 110, W0, ltimes[0] if ltimes else t0 + 0.3, tx1, cls="N", style="color:%s" % th["acc"], enter="fade")
            for k, l in enumerate(lines):
                last = k == len(lines) - 1 and len(lines) > 1
                v.text(esc(l), X0, y0 + k * lh, W0, ltimes[k], tx1, cls="D sh", style="font-size:%dpx;color:%s" % (fs, th["acc"] if last else th["ink"]), enter="rise")

        em = "" if ov.get("drop_emoji") else clean_emoji(s.get("emoji"))
        if em:
            t_em = max(t0 + 0.6, (ltimes[0] + 0.25) if ltimes else t0 + 0.6)
            ex, ey, es = {"hero": (X0, y0 - (250 if s["kicker"] else 190), 150) if lay == "hero" else None,
                          "card": (770, 270, 170), "trio": (X0 + W0 - 140, (yp + 890) if lay == "trio" else 0, 120),
                          "stat": (830, (y - 150) if lay == "stat" else 0, 150),
                          "quote": (X0, (y0 - 290) if lay == "quote" else 0, 150)}.get(lay) or (X0, 300, 140)
            emoji_badge(v, em, ex, ey, es, t_em, tx1, rot=(-8 if lay in ("card", "stat") else 0))
        for tid, a, b in tids:
            push_in(v, tid, a, b)
        tids.clear()

        if i > 0 and sfx_used < 5 and i % 2 == 1:  # 4–6 adet, çok kısık, her seferinde farklı
            v.cue(SFX[sfx_used % len(SFX)], t0 + 0.05, 0.07)
            sfx_used += 1

    return v.build(), log
