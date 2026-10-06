"""Yerel üretim adımları: ElevenLabs ses, whisper, kare çıkarma, hyperframes check + render, render doğrulama."""
import glob, json, os, re, shutil, subprocess
from common import *

HF = "hyperframes@0.8.114"


# ---------- ElevenLabs ----------
def tts(script_path, mp3_path):
    key = env("ELEVENLABS_API_KEY", required=True)
    voice = env("ELEVENLABS_VOICE_ID", required=True)
    text = open(script_path, encoding="utf-8").read().strip()
    body = {"text": text, "model_id": env("ELEVENLABS_MODEL", "eleven_multilingual_v2"),
            "voice_settings": {"stability": float(env("EL_STABILITY", "0.5")),
                               "similarity_boost": float(env("EL_SIMILARITY", "0.8")),
                               "style": float(env("EL_STYLE", "0.0")), "use_speaker_boost": True}}
    b = http("POST", "https://api.elevenlabs.io/v1/text-to-speech/%s" % voice, params={"output_format": "mp3_44100_128"},
             json_body=body, headers={"xi-api-key": key, "Accept": "audio/mpeg"}, raw=True, timeout=300)
    if len(b) < 5000:
        raise RuntimeError("ElevenLabs boş/kısa yanıt")
    os.makedirs(os.path.dirname(mp3_path), exist_ok=True)
    open(mp3_path, "wb").write(b)


def mp3_duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                       capture_output=True, text=True)
    return float(r.stdout.strip())


def fit_voice(mp3, target=None, max_speed=1.2):
    """Ses hedeften uzunsa ücretsiz olarak yerelde hızlandırır (atempo). Dönen: son süre."""
    d = mp3_duration(mp3)
    if env("VOICE_SPEEDUP", "0") != "1":  # varsayılan: ses doğal hızında kalır
        return d
    target = target or float(env("MAX_VO_SEC", "38"))
    if d <= target + 1.5:
        return d
    f = min(max_speed, d / target)
    tmp = mp3 + ".fast.mp3"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", mp3, "-filter:a", "atempo=%.3f" % f, "-q:a", "2", tmp], check=True)
    os.replace(tmp, mp3)
    log("ses hızlandırıldı: %.1f sn -> %.1f sn (x%.2f)" % (d, mp3_duration(mp3), f))
    return mp3_duration(mp3)


# ---------- whisper ----------
WH = env("WHISPER_CLI", "whisper-cli")
WMODEL = env("WHISPER_MODEL", "models/ggml-large-v3.bin")


def transcribe_all():
    """voiceovers/*.mp3 içinde work/<ad>/words.json'u olmayanları whisper ile kelime zamanlamasına çevirir."""
    for mp3 in sorted(glob.glob(os.path.join(ROOT, "voiceovers", "*.mp3"))):
        vid = os.path.basename(mp3)[:-4]
        out = os.path.join(ROOT, "work", vid)
        wj = os.path.join(out, "words.json")
        if os.path.exists(wj):
            continue
        os.makedirs(out, exist_ok=True)
        wav = os.path.join(out, "vo.wav")
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", mp3, "-ac", "1", "-ar", "16000", wav], check=True, creationflags=LOW_PRIO)
        subprocess.run([WH, "-m", WMODEL, "-l", "tr", "-mc", "0", "-t", "8", "-ml", "1", "-sow", "-oj", "-of", os.path.join(out, "vo"), wav],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1800, creationflags=LOW_PRIO)
        data = json.load(open(os.path.join(out, "vo.json"), encoding="utf-8"))
        words = [{"t": x["text"].strip(), "s": x["offsets"]["from"] / 1000, "e": x["offsets"]["to"] / 1000}
                 for x in data["transcription"] if x["text"].strip()]
        json.dump(words, open(wj, "w", encoding="utf-8"), ensure_ascii=False)
        log("whisper kelime zamanlaması:", vid, len(words), "kelime")


def source_transcript(media, out_dir):
    """Kaynak reelin konuşma metni (dil otomatik). Yalnızca konu anlamak için, geçici."""
    os.makedirs(out_dir, exist_ok=True)
    wav = out_dir + "/src.wav"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", media, "-vn", "-ac", "1", "-ar", "16000", wav], check=True)
    subprocess.run([WH, "-m", WMODEL, "-l", env("SOURCE_LANG", "auto"), "-t", "8", "-otxt", "-of", out_dir + "/src", wav],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1800, creationflags=LOW_PRIO)
    p = out_dir + "/src.txt"
    return open(p, encoding="utf-8").read().strip() if os.path.exists(p) else ""


def extract_frames(video, out_dir, n=6):
    os.makedirs(out_dir, exist_ok=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", video, "-vf", "fps=%f,scale=480:-2" % (n / 40.0),
                    "-frames:v", str(n), out_dir + "/f_%02d.jpg"], check=False)
    return sorted(os.path.join(out_dir, f) for f in os.listdir(out_dir) if f.endswith(".jpg"))


# ---------- hyperframes ----------
def _hf(args, cwd, timeout):
    npx = shutil.which("npx") or "npx"
    return subprocess.run([npx, "--yes", HF] + args, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, creationflags=LOW_PRIO)


def parse_check(out):
    """check çıktısı: Lint/Runtime '✗' (veya N>0 error) ölümcül; Layout/Contrast '✗' uyarı (elle yapılmış videolarda da çıkıyor)."""
    sec, fatal, warn = "", [], []
    for l in out.splitlines():
        if l and not l.startswith(" ") and re.fullmatch(r"[A-Z][a-z]+", l.strip()):
            sec = l.strip()
            continue
        m = re.search(r"\b([1-9]\d*) errors?(\(s\))?\b", l)
        if sec in ("Lint", "Runtime") and ("✗" in l or m):
            fatal.append(l.strip()[:160])
        elif "✗" in l:
            warn.append("%s: %s" % (sec, l.strip()[:160]))
    return fatal, warn


def render(name):
    """Projeyi check eder (Lint/Runtime hatası varsa durur) ve renders/<name>.mp4 üretir. Dönen: (dosya, uyarılar)."""
    pd = os.path.join(ROOT, "projects", name)
    c = _hf(["check"], pd, 600)
    out = (c.stdout or "") + (c.stderr or "")
    open(os.path.join(pd, "check.log"), "w", encoding="utf-8").write(out)
    fatal, warn = parse_check(out)
    if fatal:
        raise RuntimeError("hyperframes check: " + " | ".join(fatal)[:400])
    os.makedirs(os.path.join(ROOT, "renders"), exist_ok=True)
    dst = os.path.join(ROOT, "renders", name + ".mp4")
    r = _hf(["render", ".", "-o", dst, "--fps", "30"], pd, 3600)
    if r.returncode or not os.path.exists(dst):
        raise RuntimeError("render hatası: " + ((r.stderr or r.stdout)[-400:]))
    return dst, warn


def make_preview(src, name, max_mb=8):
    """Discord'a yüklenebilir küçük önizleme (540x960). Dönen: dosya yolu."""
    os.makedirs(os.path.join(ROOT, "previews"), exist_ok=True)
    dst = os.path.join(ROOT, "previews", name + ".mp4")
    for crf, rate in ((30, "1200k"), (34, "700k")):
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", src, "-vf", "scale=540:-2", "-c:v", "libx264", "-preset", "veryfast",
                        "-crf", str(crf), "-maxrate", rate, "-bufsize", "2400k", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", dst],
                       check=True, creationflags=LOW_PRIO)
        if os.path.getsize(dst) <= max_mb * 1e6:
            break
    return dst


def need(path, what):
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        raise RuntimeError("%s üretilmedi: %s" % (what, path))
    return path


def verify_render(path):
    """1080x1920, 20–60 sn, ses var."""
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height,duration",
                        "-of", "json", path], capture_output=True, text=True)
    info = json.loads(r.stdout)["streams"]
    v = next(s for s in info if s["codec_type"] == "video")
    if (v["width"], v["height"]) != (1080, 1920):
        raise RuntimeError("render boyutu yanlış: %sx%s" % (v["width"], v["height"]))
    if not any(s["codec_type"] == "audio" for s in info):
        raise RuntimeError("render sessiz")
    dur = float(v.get("duration") or 0)
    if not 20 <= dur <= 56:
        raise RuntimeError("render süresi beklenmedik: %.1f sn" % dur)
    return dur
