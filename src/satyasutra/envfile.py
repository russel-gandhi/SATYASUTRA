"""Loads KEY=VALUE lines from a local .env file into os.environ. Secrets stay in the environment; values are never logged or returned."""

from __future__ import annotations

import os
from pathlib import Path


def load_env_file(path: Path, environ: dict | None = None) -> list[str]:
    """Set variables from `path` that are not already set. Returns the NAMES it set, never values."""
    env = os.environ if environ is None else environ
    if not path.is_file():
        return []
    names: list[str] = []
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= len('""') and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and value and key not in env:
            env[key] = value
            names.append(key)
    return names
