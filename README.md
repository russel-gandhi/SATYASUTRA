# SATYASUTRA

A real-time fact-verification and deepfake audit engine for Indian social media (hackathon Theme 1, Civic Pulse; problem statement 4).
You give it a claim, a forwarded message, a voice note, a photo, a video or a screenshot of a notice. It checks what it can against live
sources and tells you what it found. When nobody reliable has checked a claim it says **"cannot tell"** instead of guessing, and every
verdict is written to a tamper-evident ledger with its sources.

This repository (FORGE) is where it was built. The design is in [`docs/plan/`](docs/plan); working notes for contributors are in [`CLAUDE.md`](CLAUDE.md).

## What works today, and what does not

Read this before the demo. "Works" means it ran live on the date shown; the limits are stated next to each line.

| Capability | State | What it does, and the limit |
|---|---|---|
| Claim check, English | **Works** | Gemini extracts the claim, the Google Fact Check API finds fact-checks, plain rules decide. Only a fact-check of the *same* claim from a trusted source can refute or support it. On our 22-claim sheet ([`docs/evaluation_2026-10-31.md`](docs/evaluation_2026-10-31.md)): 7 of 10 debunked claims refuted, 0 true claims wrongly refuted, the rest "cannot tell". It is a small hand-made sheet, not a benchmark. |
| Claim check, Hindi | Partly | Gemini translates to English, keeps the Hindi quote; the Fact Check API is searched with the English text. A handful of Hindi claims tried, quality beyond that unproven. |
| "Supported" verdicts | Works, rare | Seen live on 2026-10-31: "Some 90% of Americans support universal background checks" came back SUPPORTED (high) from Snopes and PolitiFact reviews rated True, and a CDC life-expectancy claim SUPPORTED (medium). Positive ratings are rare (7 of 71 reviews collected) and none were for Indian claims, so most true Indian claims will still say "cannot tell". |
| Official text (RBI, SEBI) | Works, thin | A claim that quotes a different number than an official release is refuted. Only the latest 10 RBI releases and 30 SEBI titles are held. PIB, MoHFW and cybercrime.gov.in refuse our client, so they are not used. |
| Web search (news outlets across the web) | **Works** | The system searches live news for the claim and reads what outlets say. Two or more reputable outlets reporting it as fact give SUPPORTED (medium at most); a fact-checker, or two reputable outlets, calling it false give REFUTED; unknown blogs are shown but never decide. Live: "Nana Patekar is dead" SUPPORTED (NDTV, The Hindu, Hindustan Times, ETV Bharat), "Chandrayaan-3 landed near the lunar south pole" SUPPORTED, the microchip claim REFUTED. **Not measured:** how often an outlet reporting a rumour is mistaken for confirmation, and old headlines matching a new claim. The outlet lists and counts are policy choices. |
| Fast clock (what is spreading) | Works, Bluesky only | Clusters near-identical live posts, ranks by distinct authors per minute, shows first-seen and wording changes. "First we saw" is not the true origin. |
| Officials' view (`/officials`) | Works | Triage list of spreading clusters, disaster words first, with any verdict we already made. Word lists, not a model. |
| Image edits (TruFor) | **Works, with misses** | Heatmap and a score; cut 0.7. Measured after sharing (shrunk, JPEG): flags 63% of spliced and 30% of copy-moved photos, and 7% of untouched ones ([`docs/calibration/trufor_map_2026-10-31.txt`](docs/calibration/trufor_map_2026-10-31.txt)). We tried adding the localisation map as a second trigger; it only raised false alarms. So a clean result is weak evidence. False-alarm rate on real social-media photos is unknown. |
| AI-generated images (Smaug) | Works, limited | 36 of 40 AI images caught, 1 of 40 real flagged, on Commons files (`docs/calibration/smaug_2026-10-31.txt`). Misses logos and screenshots. Not tested on WhatsApp or Instagram re-uploads or newer generators. A lone hit is labelled SYNTHETIC_SUSPECTED, never a finding. |
| Debunked-image index | Works | 117 images from fact-check feeds, matched by perceptual hash. Crops do not match. A match means "appeared in a fact-check", never "false". |
| Video | Partly | Samples 8 frames for edits, AI-generated look and index matches. **Face swaps and AI-generated video are not detected.** We tested two pretrained face-forgery classifiers on 54 FaceForensics++ videos; the better one flagged 6 of 18 real videos, so we show nothing ([`docs/calibration/faceswap_2026-10-31.txt`](docs/calibration/faceswap_2026-10-31.txt)). A clean result is weak evidence. |
| Voice notes to text | **Works** | Whisper small on the GPU; English clear, Hindi has word errors. Max 120 s. |
| Real or cloned voice | Works on a small test | A wav2vec2 classifier trained on modern TTS and cloning output (Apache 2.0) runs in the GPU service. On our 39 clips (Gemini and Windows speech vs old and noisy real recordings, also after phone-style compression) the median window separated them: 19/19 caught, 0/20 real flagged ([`docs/calibration/audio_synthetic_2026-10-31.txt`](docs/calibration/audio_synthetic_2026-10-31.txt)). The earlier model, W2V2-AASIST, failed the same test. **Weak test: no cloned voices of real people, no ElevenLabs-style output, real clips are old.** A flag says "possibly synthetic", never proof; no flag never means "real". |
| Lookalike links | Works | Checks link *names* against 27 official domains; links are never opened. "Unknown" never means "safe". |
| Next steps (1930, cybercrime portal, Chakshu) | Works, unconfirmed | Routes come from secondary sources; the official sites refused our client, so they are marked unconfirmed. We file and send nothing. |
| Notice checker | Works on a made-up notice | Gemini vision copies the text; code checks links, scam wording and whether an RBI/SEBI claim matches our official text. Real photographed notices not tested. |
| Correction cards (English, Hindi) | Works | Drawn in the browser with Noto Sans Devanagari. Hindi wording is ours and not reviewed by a native speaker. |
| Telegram bot | Works (text) | Text, voice and photo go through the same API. Voice and photo replies in Telegram were not checked live. |
| Explanations | Works | Exact Shapley credit per evidence item and per detector. The language-model steps are not explained, and the output says so. |
| Resilience | Works | Gemini is retried once and falls back to the next model, then to a local multilingual NLI model for the "same claim?" step. Out of quota is said plainly. |
| Raksha (help for leaked intimate images) | **Guidance only** | A message about leaked or threatening private images gets a support plan first: keep a record, do not pay, StopNCII.org (18 and over) or Take It Down (under 18), the cyber-crime portal, report on the platform, 112 if in danger. It never asks for or handles the images, files nothing, and its Indian steps are unconfirmed on official sites. Triggered by word lists, so it will miss unusual wording. Counselling, takedown handling and legal advice are roadmap. |
| Other networks (X, WhatsApp, Facebook) | **Roadmap** | The live stream is Bluesky only. |
| Reverse image search | **Roadmap** | Paid APIs only; lookups stay manual (Lens, Yandex). |

Things we know we have not measured: how often TruFor and Smaug fire on ordinary social-media photos; escalation of spreading clusters
to the claim agent has never fired on a real item; Hindi extraction beyond a few items; the thresholds above come from small sets.

## Run it

Needs Windows, Python 3.14 for the API, Python 3.11 with CUDA for the GPU service, and ffmpeg on the PATH. Keys go in a git-ignored `.env`
(names in `.env.example`: `GEMINI_API_KEY`, `GOOGLE_FACTCHECK_API_KEY`, `FORGE_CONTACT`, optionally `DATA_GOV_IN_API_KEY`, `TELEGRAM_BOT_TOKEN`).

```powershell
python -m venv .venv ; .\.venv\Scripts\pip install -e ".[dev]"
.\scripts\start_demo.ps1                                   # starts the GPU service (TruFor, Smaug, Whisper, NLI) and the API, then checks everything
.\.venv\Scripts\python scripts\telegram_bot.py             # optional, needs the API running
```

Open http://127.0.0.1:8000/ (checker) and http://127.0.0.1:8000/officials. Models are not in the repository (`models/` and `forensics/`
are git-ignored): Whisper small, W2V2-AASIST, mDeBERTa NLI and the TruFor and Smaug weights are downloaded separately; `CLAUDE.md` and
`docs/licences.md` say where from.

Checks: `.\.venv\Scripts\python -m pytest -q` (446 unit tests), `scripts\check_no_fixtures.py` (the no-hardcoded-data guard),
`scripts\preflight.py` (live sources and services), `scripts\eval_claims.py` (the claim sheet).

## Rules we hold ourselves to

- **No mock, static or hardcoded data in product code.** Live sources only; values live in `config/*.yaml`; a guard script enforces it on `src/`.
- Every verdict needs evidence from a Tier 1 or 2 source; "cannot tell" is a valid answer; media checks never say "authentic".
- We never file to a government portal, defeat a CAPTCHA or bypass a login. Our client is honest about what it is, so sources that refuse it are not used.
- Say what was not tested. Nothing in this table is claimed beyond what it says.

## Credits and licences

- **TruFor** (GRIP-UNINA, University Federico II of Naples): edit detection and heatmaps. Informational and non-profit use; credit required.
- **Smaug AI-Image-Detector** (Abacus.AI, Apache 2.0): AI-generated image score.
- **Whisper** (OpenAI, MIT) through faster-whisper (Systran); **W2V2-AASIST** (Tak et al., MIT); **mDeBERTa-v3 NLI** (Moritz Laurer, MIT).
- **Noto Sans Devanagari** (Google, SIL Open Font License). Full table, including what was not read: [`docs/licences.md`](docs/licences.md).
- Data from the Google Fact Check Tools API, Wikidata, Wikimedia Commons, Bluesky Jetstream, RBI and SEBI feeds, and the RSS feeds of the news outlets named in `config/runtime.yaml`.
