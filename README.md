# FORGE

Build repository for **SATYASUTRA**: a multilingual claim-verification, provenance and media-audit engine for Indian civic
misinformation. The design lives in [`docs/plan/`](docs/plan) (Bible, master build map, claims, provenance, forensics and the
Raksha roadmap). This repository is where it is built, in phases and tickets: see [`docs/PHASES.md`](docs/PHASES.md).

## Status

Nothing here is a claim until a ticket's "done when" command has passed. Check the board:

```sh
python tools/forge.py board
```

## Setup (Windows, Python 3.11+)

```sh
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
pytest -q
```

## Rules

- No URL, key, threshold or demo data in `src/`. Config is in `config/`, secrets are in environment variables.
- Recorded data may exist in `tests/` only.
- Every verdict needs evidence; "cannot tell" is a valid answer.
- One commit per finished chunk, message `F<phase>-<nn>: title`.
