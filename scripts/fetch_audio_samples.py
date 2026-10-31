"""Collect labelled speech for calibrating the audio authenticity cut.

real  = Wikimedia Commons speech recordings (people speaking; searched, at least MIN_S seconds)
fake  = Gemini text-to-speech (modern neural TTS) in several voices, English and Hindi, plus the two Windows SAPI voices (older TTS)
Writes WAV/OGG files into <out>/real and <out>/fake. Not product code: these files only calibrate a threshold (docs/calibration/).
"""
import argparse
import base64
import os
import subprocess
import sys
import time
import wave
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from satyasutra.config import load_config          # noqa: E402
from satyasutra.envfile import load_env_file       # noqa: E402
from satyasutra.sources.probe import build_user_agent   # noqa: E402

MIN_S, MAX_S = 6, 60
SEARCHES = ["filetype:audio speech Hindi", "filetype:audio spoken Wikipedia English", "filetype:audio interview", "filetype:audio speech India",
            "filetype:audio lecture", "filetype:audio spoken Tamil", "filetype:audio spoken Bengali", "filetype:audio podcast"]
TEXTS = {
    "en": ["The Reserve Bank of India announced today that the repo rate will remain unchanged for the third consecutive meeting.",
           "Heavy rain is expected across Kerala and Karnataka over the next three days, and the weather department has issued an orange alert.",
           "Please do not share any one time password with anyone, even if the caller says they are from your bank.",
           "The election commission has said that voting will be held in five phases beginning next month."],
    "hi": ["भारतीय रिज़र्व बैंक ने आज घोषणा की कि रेपो दर लगातार तीसरी बैठक में भी स्थिर रहेगी।",
           "अगले तीन दिनों तक केरल और कर्नाटक में भारी बारिश की संभावना है, मौसम विभाग ने ऑरेंज अलर्ट जारी किया है।",
           "कृपया किसी के साथ भी अपना ओटीपी साझा न करें, भले ही फोन करने वाला खुद को आपके बैंक का बताए।",
           "चुनाव आयोग ने कहा है कि अगले महीने से पाँच चरणों में मतदान होगा।"],
}
VOICES = ["Kore", "Puck", "Charon", "Aoede", "Fenrir", "Zephyr"]


def fetch_real(out: Path, want: int, cfg) -> int:
    got = 0
    headers = {"User-Agent": build_user_agent(cfg.runtime.probe.user_agent)}
    api = cfg.runtime.provenance.commons.endpoint
    with httpx.Client(headers=headers, timeout=60, follow_redirects=True) as http:
        for q in SEARCHES:
            r = http.get(api, params={"action": "query", "format": "json", "generator": "search", "gsrsearch": q, "gsrnamespace": 6, "gsrlimit": 30,
                                      "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata", "iiextmetadatafilter": "LicenseShortName"})
            for page in (r.json().get("query", {}).get("pages", {}) or {}).values():
                info = (page.get("imageinfo") or [{}])[0]
                if not str(info.get("mime", "")).startswith(("audio/", "application/ogg")) or info.get("size", 0) > 6_000_000:
                    continue
                dest = out / f"real_{got:03d}.bin"
                dest.write_bytes(http.get(info["url"]).content)
                dur = probe_seconds(dest)
                if dur is None or dur < MIN_S:
                    dest.unlink()
                    continue
                if dur > MAX_S:                        # keep the first minute only
                    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(dest), "-t", str(MAX_S), "-ac", "1", "-ar", "16000", str(dest.with_suffix(".wav"))], check=False)
                    dest.unlink()
                    dest = dest.with_suffix(".wav")
                (out / (dest.stem + ".txt")).write_text(f"{page['title']} | {q}", encoding="utf-8")
                got += 1
                print("real", got, page["title"][:70], f"{min(dur, MAX_S):.0f}s")
                if got >= want:
                    return got
    return got


def probe_seconds(path: Path) -> float | None:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return None


def fetch_fake_gemini(out: Path, want: int, model: str) -> int:
    key = os.environ["GEMINI_API_KEY"]
    got, fails = 0, 0
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    jobs = [(lang, text, VOICES[i % len(VOICES)]) for i, (lang, text) in enumerate((l, t) for t in zip(*TEXTS.values()) for l, t in zip(TEXTS, t))]
    for n, (lang, text, voice) in enumerate(jobs * 2):
        if got >= want or fails > 6:
            break
        voice = VOICES[(n + 1) % len(VOICES)]
        body = {"contents": [{"parts": [{"text": text}]}], "generationConfig": {"responseModalities": ["AUDIO"],
                "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}}}
        try:
            r = httpx.post(url, params={"key": key}, json=body, timeout=120)
        except httpx.HTTPError as exc:
            print("tts error", type(exc).__name__); fails += 1; continue
        if r.status_code != 200:
            print("tts HTTP", r.status_code); fails += 1; time.sleep(20); continue
        try:
            pcm = base64.b64decode(r.json()["candidates"][0]["content"]["parts"][0]["inlineData"]["data"])
        except (KeyError, IndexError):
            print("tts: no audio"); fails += 1; continue
        dest = out / f"fake_gemini_{lang}_{got:03d}.wav"
        with wave.open(str(dest), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000); w.writeframes(pcm)
        (out / (dest.stem + ".txt")).write_text(f"{model} {voice} {lang}: {text}", encoding="utf-8")
        got += 1
        print("fake", got, lang, voice, f"{len(pcm) / 48000:.1f}s")
        time.sleep(8)
    return got


def fetch_fake_sapi(out: Path) -> int:
    n = 0
    for voice in ("Microsoft David Desktop", "Microsoft Zira Desktop"):
        for i, text in enumerate(TEXTS["en"]):
            dest = out / f"fake_sapi_{voice.split()[1].lower()}_{i}.wav"
            ps = (f"Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; $s.SelectVoice('{voice}'); "
                  f"$s.SetOutputToWaveFile('{dest}'); $s.Speak('{text}'); $s.Dispose()")
            subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=False)
            n += dest.exists()
    return n


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--real", type=int, default=24)
    ap.add_argument("--fake", type=int, default=16)
    ap.add_argument("--tts-model", default="gemini-2.5-flash-preview-tts")
    a = ap.parse_args()
    load_env_file(ROOT / ".env")
    out = Path(a.out); (out / "real").mkdir(parents=True, exist_ok=True); (out / "fake").mkdir(parents=True, exist_ok=True)
    print("real:", fetch_real(out / "real", a.real, load_config()))
    print("sapi:", fetch_fake_sapi(out / "fake"))
    print("gemini tts:", fetch_fake_gemini(out / "fake", a.fake, a.tts_model))
