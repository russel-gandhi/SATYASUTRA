# Licences of added dependencies

Checked before adding, per ticket F1-10. "Checked" means the licence text was read on the date shown, not assumed.

| Dependency | Used for | Licence | Checked |
|---|---|---|---|
| `pdqhash` 0.2.8 (PyPI, Python bindings by Fausto Morales) | PDQ perceptual hash of images | MIT (the `LICENSE` file in the source package) | 2026-10-31 |
| PDQ algorithm and C++ source bundled inside `pdqhash` (Meta, `facebook/ThreatExchange`) | the hash itself | BSD (the repository `LICENSE`, "BSD License For ThreatExchange software", read at `raw.githubusercontent.com/facebook/ThreatExchange/main/LICENSE`). The `pdqhash` source package ships no copy of it, only Meta copyright headers in the source files | 2026-10-31 |
| `Pillow` 13.0.0 | decoding, resizing and re-encoding images | not read for this ticket | not checked |
| `numpy` 2.5.4 | DCT for pHash, array handling | not read for this ticket | not checked |

Notes

- `pdqhash` has no prebuilt wheel for this machine (Python 3.14, Windows): pip built it from source, which needs a C++ compiler. Any
  other machine or CI image that installs the project must be able to do the same, or install a wheel if one exists for its Python.
- `pdqhash` also bundles `CImg.h`, which is dual-licensed CeCILL / CeCILL-C. It is part of Meta's C++ tools, not of the hash computation
  called from Python. Its terms were not studied here; read them before redistributing a built wheel.
- Pillow and numpy are widely used permissive-licence libraries, but that is general knowledge, not something this ticket verified.

## Media detectors (added 2026-10-31)

| Component | Used for | Licence and what was read | Checked |
|---|---|---|---|
| Smaug AI-Image-Detector (Abacus.AI), weights `abacusai/Smaug-AI-Image-Detector` on Hugging Face, loaded through `sentence-transformers` | probability that an image is AI-generated | Apache License 2.0: the `LICENSE` file in the downloaded model folder (first lines read; header says Apache License Version 2.0). Credit Abacus.AI in the README. The training data's own terms were not read | 2026-10-31 |
| TruFor (GRIP-UNINA, University Federico II of Naples) | edit detection with a heatmap | informational and nonprofit use only, credit the authors. `LICENSE.txt` of the repository was **not read in full** (open item in CLAUDE.md) | not fully checked |
| RBI and SEBI RSS feeds | official text (Tier 1) | public feeds fetched with a descriptive User-Agent and a host gate; their terms of use were **not read**. PIB, MoHFW, PMIndia and cybercrime.gov.in refused our client (HTTP 403) and are not used | feeds reachable 2026-10-31, terms not read |

## Added 2026-10-31 (models and fonts used by the prototype)

| Item | Licence | Source | Notes |
|---|---|---|---|
| faster-whisper small (voice notes) | MIT (Systran conversion of OpenAI Whisper, MIT) | Hugging Face Systran/faster-whisper-small | runs locally, not redistributed in the repo (`models/` is git-ignored) |
| W2V2-AASIST (audio authenticity) | MIT per its model card (source repo TakHemlata/SSL_Anti-spoofing); trained on ASVspoof 2019, whose data licence we did not read | Hugging Face SpeechAntiSpoofingBenchmarks/W2V2-AASIST | failed our own test, gives no verdict; see docs/calibration/audio_2026-10-31.txt |
| mDeBERTa-v3 multilingual NLI (fallback judge) | MIT per its model card | Hugging Face MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7 | local only; the training data licences (XNLI, multilingual NLI sets) were not read |
| Noto Sans Devanagari (correction card) | SIL Open Font License 1.1 (OFL.txt sits beside the font) | Google Fonts | served from `assets/fonts` |
| News RSS feeds (headlines and links only) | publishers' terms not read | The Hindu, Indian Express, NDTV, HT, TOI, BBC News, Mint, Al Jazeera | only headlines and links are shown; stored locally for a day or two |

