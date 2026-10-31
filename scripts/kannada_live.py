"""Kannada end to end on live services: Gemini TTS speaks a Kannada claim, the voice route transcribes it, the claim agent checks the transcript.

The audio is machine speech, so this tests the pipeline (language ID, transcription, extraction, translation, search), not how Whisper copes with real
Kannada speakers. Prints what each step returned; writes nothing but the wav.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.parse
import wave
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from satyasutra.envfile import load_env_file  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
SAMPLE_RATE = 24000
BYTES_PER_SAMPLE = 2


def speak(text: str, model: str, voice: str, dest: Path) -> None:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = {"contents": [{"parts": [{"text": text}]}], "generationConfig": {"responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}}}
    try:
        r = httpx.post(url, headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]}, json=body, timeout=120)
    except httpx.HTTPError as exc:
        raise SystemExit(f"TTS request failed ({type(exc).__name__})")      # httpx messages can carry the URL, so only the class is shown
    if r.status_code != httpx.codes.OK:
        raise SystemExit(f"TTS answered HTTP {r.status_code}")
    pcm = base64.b64decode(r.json()["candidates"][0]["content"]["parts"][0]["inlineData"]["data"])
    with wave.open(str(dest), "wb") as w:
        w.setnchannels(1); w.setsampwidth(BYTES_PER_SAMPLE); w.setframerate(SAMPLE_RATE); w.writeframes(pcm)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--text", default="ಕೋವಿಡ್ ಲಸಿಕೆಗಳಲ್ಲಿ ಮೈಕ್ರೋಚಿಪ್ ಇದೆ")
    ap.add_argument("--model", default="gemini-2.5-flash-preview-tts")
    ap.add_argument("--voice", default="Kore")
    ap.add_argument("--out", default=str(ROOT / "data" / "kannada_live.wav"))
    ap.add_argument("--reuse", action="store_true", help="use the wav from an earlier run instead of calling TTS again (it is rate limited)")
    a = ap.parse_args()
    load_env_file(ROOT / ".env")
    wav = Path(a.out)
    wav.parent.mkdir(parents=True, exist_ok=True)
    if not (a.reuse and wav.exists()):
        speak(a.text, a.model, a.voice, wav)
    print("spoken:", a.text)
    t = httpx.post(a.base + "/voice/transcribe", content=wav.read_bytes(), headers={"Content-Type": "application/octet-stream"}, timeout=180).json()
    print("transcript:", t.get("text"), "| language", t.get("language"), t.get("language_prob"))
    with httpx.stream("GET", a.base + "/agent/stream?text=" + urllib.parse.quote(t["text"]), timeout=180) as r:
        event, result = "", None
        for line in r.iter_lines():
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: ") and event == "result":
                result = json.loads(line[6:])
    if not result:
        print("no checkable claim")
        return
    for c in result["claims"]:
        print("claim:", c["text"], "| original:", c["quote"], "| lang", c["lang"], "| flags", c["flags"], "| verdict", c["label"], c["confidence_band"])


if __name__ == "__main__":
    main()
