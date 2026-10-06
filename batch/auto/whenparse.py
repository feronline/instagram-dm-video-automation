"""'onay' komutundaki tarih ifadeleri: yarın, bugün, haftaya, 2 gün sonra, 1 hafta sonra, iki hafta sonra, cuma, 20 ekim, 20/10, 20.10.2026.
Saat (16.45) ayrı ele alınır; burada yalnızca TARİH çıkarılır. Çıkaramadığı jetonları olduğu gibi geri verir."""
import datetime, re

WEEKDAYS = {"pazartesi": 0, "salı": 1, "sali": 1, "çarşamba": 2, "carsamba": 2, "perşembe": 3, "persembe": 3,
            "cuma": 4, "cumartesi": 5, "pazar": 6}
MONTHS = {"ocak": 1, "şubat": 2, "subat": 2, "mart": 3, "nisan": 4, "mayıs": 5, "mayis": 5, "haziran": 6, "temmuz": 7,
          "ağustos": 8, "agustos": 8, "eylül": 9, "eylul": 9, "ekim": 10, "kasım": 11, "kasim": 11, "aralık": 12, "aralik": 12}
UNITS = {"gün": 1, "gun": 1, "hafta": 7, "ay": 30}
WORDNUM = {"bir": 1, "iki": 2, "üç": 3, "uc": 3, "dört": 4, "dort": 4, "beş": 5, "bes": 5, "altı": 6, "alti": 6, "yedi": 7, "on": 10}
DAYNAMES = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]


def _num(t):
    return int(t) if t.isdigit() else WORDNUM.get(t)


def extract_date(tokens, today):
    """-> (date | None, kalan_jetonlar, hata | None)"""
    rest, date, i = [], None, 0
    d1 = datetime.timedelta(days=1)
    try:
        while i < len(tokens):
            t = tokens[i]
            nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
            nn = tokens[i + 2] if i + 2 < len(tokens) else ""
            n = _num(t)
            if t in ("yarın", "yarin"):
                date, i = today + d1, i + 1
            elif t in ("bugün", "bugun"):
                date, i = today, i + 1
            elif t == "haftaya":
                date, i = today + 7 * d1, i + 1
            elif n is not None and nxt in UNITS:  # "2 gün sonra", "1 hafta", "iki hafta sonra"
                date = today + n * UNITS[nxt] * d1
                i += 3 if nn == "sonra" else 2
            elif t.isdigit() and nxt in MONTHS:  # "20 ekim"
                date = datetime.date(today.year, MONTHS[nxt], int(t))
                if date < today:
                    date = date.replace(year=today.year + 1)
                i += 2
            elif t in WEEKDAYS:  # "cuma": bugünden sonraki ilk cuma
                date = today + ((WEEKDAYS[t] - today.weekday()) % 7 or 7) * d1
                i += 1
            elif re.fullmatch(r"\d{1,2}/\d{1,2}(/\d{2,4})?", t) or re.fullmatch(r"\d{1,2}\.\d{1,2}\.\d{2,4}", t):
                parts = [int(x) for x in re.split(r"[/.]", t)]
                y = parts[2] if len(parts) == 3 else today.year
                y += 2000 if y < 100 else 0
                date = datetime.date(y, parts[1], parts[0])
                if len(parts) == 2 and date < today:
                    date = date.replace(year=today.year + 1)
                i += 1
            elif t == "sonra":
                i += 1
            else:
                rest.append(t)
                i += 1
    except ValueError:
        return None, tokens, "Geçersiz tarih: " + " ".join(tokens)
    return date, rest, None


def label(dt, now):
    """Kullanıcıya gösterilecek gün etiketi: bugün / yarın / 20.10 Salı."""
    if dt.date() == now.date():
        return "bugün"
    if dt.date() == now.date() + datetime.timedelta(days=1):
        return "yarın"
    return "%s %s" % (dt.strftime("%d.%m"), DAYNAMES[dt.weekday()])
