# Environment matrix (ticket F1-01)

Generated 2026-10-30 20:06 UTC by `scripts/check_env.py --install`. Each package was tried in a throwaway virtual environment.

Pythons found: 3.14

| Package | Python | Resolves | Download MB | Installed | Imports and ran | Seconds | Failing step | Detail |
|---|---|---|---|---|---|---|---|---|
| torch | 3.14 | yes | 130.8 | yes | yes | 192.0 | - | 8 cpu / PyPI wheel on Windows is usually CPU-only; CUDA needs the PyTorch index (decide after this run) |
| onnxruntime | 3.14 | yes | 27.3 | yes | yes | 29.0 | - | providers AzureExecutionProvider,CPUExecutionProvider / import and provider list only; no model was run |
| mediapipe | 3.14 | yes | 107.7 | yes | yes | 50.2 | - | mediapipe 1.2.0 / import and version only; no task was run |
| faster-whisper | 3.14 | yes | 83.3 | yes | yes | 32.9 | - | faster_whisper 1.2.1 / import and version only; weights were not downloaded |
| minicheck | 3.14 | - | ? | - | - | 0.0 | approval | not run: needs --approve minicheck (installs from a git branch, which runs that repository's build code; run with --approve minicheck only after the team has looked at it) |

## Decisions

Interpreters on this machine: Python 3.14 (project) and Python 3.11.15 (uv-managed). **Python 3.12 is not installed.** A resolve-only pass on 2026-10-31 found wheels for all four packages on 3.11 as well (torch 130.7 MB, onnxruntime 26.8 MB, mediapipe 106.6 MB, faster-whisper 82.1 MB), so the 3.11 service fallback exists, but it was not installed or run because nothing failed on 3.14.

| Package | Decision | Reason and limits |
|---|---|---|
| torch | **In-process on 3.14, CPU** | Ran a tensor add. The PyPI wheel reports no CUDA, so the RTX 4060 is **not usable yet**. A CUDA build for 3.14 was not checked; do that before TruFor (Phase 2). If none exists, run TruFor as a service on 3.11 with the CUDA wheel |
| onnxruntime | **In-process on 3.14** | Import and provider list only; the providers are Azure and CPU, so no GPU provider in this wheel. No model was run |
| mediapipe | **In-process on 3.14** | Version 1.2.0 imports. No task or model was run, so face and landmark use is unproven |
| faster-whisper | **In-process on 3.14** | Version 1.2.1 imports. Weights were not downloaded, so transcription speed on this machine is unproven |
| minicheck | **Undecided** | Not run: the plan installs it from a git branch, which executes that repository's build code. Needs the team's approval (`--approve minicheck`) after looking at the repository. Fallback if it fails on 3.14: service on 3.11 |

