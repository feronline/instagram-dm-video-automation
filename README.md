# Instagram DM → Otomatik Türkçe Bilgilendirici Video Hattı

Instagram'da bir Reels'i kendi hesabından işletme hesabına **DM olarak gönder**, sistem konuyu anlasın, **bağımsız kaynaklardan araştırsın**, özgün bir Türkçe senaryo yazsın, seslendirsin, fotoğrafları bulup videoyu üretsin, kendi kendini görsel olarak kontrol etsin, onayını **DM ile** alsın ve istediğin saatte Instagram'a **Reels olarak yayınlasın**. Bilgisayarında arka planda çalışır; Claude Code gibi bir araç açık olmak zorunda değildir.

> English summary: a Windows automation that turns a Reel you DM to your own Instagram professional account into an original Turkish explainer video (research with web search → script/storyboard JSON → ElevenLabs voice → photos → rendered 1080x1920 video → vision QA → approval via DM commands → scheduled Reels publishing). Pure Python + the Anthropic API.

## Akış

```
DM (reel + isteğe bağlı not)
  → whisper (yerel) → konu çıkarma        ← kaynak videonun metni YALNIZCA burada görünür
  → web araştırması (Anthropic web_search) ← olgular bağımsız doğrulanır
  → senaryo + sahneler + tasarım + caption (JSON şema; yalnızca konu + olgular görünür)
  → fotoğraf arama (Google/Serper → Wikimedia → Openverse) → görsel seçim (vision)
  → ElevenLabs ses → whisper kelime zamanlaması
  → şablon motoru (hero/card/stat/trio/quote) → HyperFrames render
  → vision QA (alakasız fotoğraf, taşma, boş ekran → otomatik düzeltme)
  → DM/Discord ile önizleme + onay → planlı yayın (Instagram Graph API)
```

**Özgünlük tasarımı:** Kaynak reel'in konuşma metni yalnızca "konu çıkarma" çağrısına gider. Senaryoyu yazan model kaynak videoyu görmez; yalnızca konuyu ve araştırılmış olguları görür. Videonun kendisi, sesi ve cümleleri kullanılmaz.

## DM komutları (yalnızca sahibin hesabından)

| Yazdığın | Anlamı |
|---|---|
| (reel gönder) | Üretimi başlatır; "konu/not eklemek ister misin?" diye sorar: `evet` → 90 sn içinde not yaz · `hayır` → hemen başla |
| `onay kristal şimdi` | Hemen yayınla |
| `onay kristal 16.45` · `onay kristal yarın 15.00` · `onay kristal 3 gün sonra` · `onay kristal cuma` · `onay kristal 20 ekim 14.00` | Planla |
| `iptal kristal` | İptal et ve çöpe taşı (`batch/_trash`, 7 gün sonra silinir) |
| `durum` | İş listesi |

Saati kaçan video kendiliğinden yayınlanmaz, bilgisayar kapalıyken saati geçerse sana sorar.
Günlük video sayısı sınırı yoktur.

## Gereksinimler

- Windows 10/11, Python 3.12 (`tkinter` dahil), `ffmpeg`/`ffprobe`, Node.js
- [whisper.cpp](https://github.com/ggml-org/whisper.cpp) + `ggml-large-v3.bin` modeli (`WHISPER_CLI`, `WHISPER_MODEL`)
- [HyperFrames](https://github.com/heygen-com/hyperframes) CLI (`npx hyperframes@0.8.114`, otomatik indirilir)
- [cloudflared](https://github.com/cloudflare/cloudflared/releases) → `batch/tools/cloudflared.exe` (Reels yüklemesi için geçici HTTPS adresi)
- `pip install anthropic python-dotenv yt-dlp`
- Hesaplar/anahtarlar (`.env.example`'a bak): **Anthropic API**, **ElevenLabs** (ücretli plan; kütüphane sesleri API'de ücretli plan ister), **Meta uygulaması** (Instagram Login, *Live* modda; izinler: `instagram_business_basic`, `instagram_business_manage_messages`, `instagram_business_content_publish`), isteğe bağlı: Serper (Google Görseller), Discord webhook, YouTube OAuth

## Kurulum

1. `.env.example` → `.env` kopyala, doldur (**asla commit etme**).
2. `python batch/auto/run.py check` ile eksikleri gör, `python batch/auto/run.py whoami` ile Instagram kimliklerini bul.
3. Otomatik çalıştırma: `powershell -ExecutionPolicy Bypass -File batch\auto\install_task.ps1` (Görev Zamanlayıcı, dakikada bir tarar, yönetici izni gerekmez).
4. İzlemek ve başlat/durdur için: `pythonw batch\monitor.py` (durum ışığı, işler, canlı log, günlük Claude harcaması).

## ÖNEMLİ: bu depoda bulunmayanlar

Bu depo yalnızca **otomasyon katmanını** içerir (`batch/auto`, `batch/monitor.py`). Video kompozisyonu için şu yardımcı modüllere ihtiyaç duyar ve **burada yoktur**: `batch/eng2.py` ve `batch/lib.py` (HyperFrames tabanlı sahne yardımcıları). Bunları kendi projenden sağlamalısın. Ayrıca telifli içerik (fotoğraflar, sesler, fontlar, üretilmiş videolar) bilerek depoya konmamıştır (`.gitignore`).

## Uyarılar / sorumluluk

- **Instagram videosunu indirmek** (yt-dlp) Instagram kullanım şartlarına aykırı olabilir ve sık bozulur. Yalnızca kendi sorumluluğunla kullan.
- İnternetten indirilen **fotoğrafların telif hakkı** sahiplerine aittir; bu araç lisans filtrelemesi yapmaz. Yayınlamadan önce hakları kontrol etmek senin sorumluluğundur.
- Platform kuralları (AI içerik etiketi, tekrarlı/otomatik içerik) için kendi hesabını ve içeriğini sen denetle. İlk haftalarda onay kapısını (`APPROVAL_REQUIRED=1`) açık tut.
- Maliyet: video başına yaklaşık $0.2–0.35 (Anthropic) + ElevenLabs karakter kotası.
- Bu proje Meta, Instagram, Anthropic, ElevenLabs, HeyGen veya Discord ile **bağlantılı değildir**.

## Lisans

Henüz belirtilmedi (yazar ekleyecek).
