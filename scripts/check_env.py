"""Does each heavy package install and run on this machine? (ticket F1-01)

Everything happens in throwaway virtual environments under the system temp folder; the project venv is never touched.

    python scripts/check_env.py                      # resolve only: which wheels exist, how big (downloads nothing large)
    python scripts/check_env.py --install            # also install and run the tiny operation (within max_download_mb)
    python scripts/check_env.py --install --write-report
    python scripts/check_env.py --install --approve minicheck --allow-large
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "docs" / "env_matrix.md"
BYTES_PER_MB = 1024 * 1024
PIP_TIMEOUT_S = 1800
OP_TIMEOUT_S = 300


@dataclass
class Target:
    label: str
    exe: str


@dataclass
class Row:
    package: str
    python: str
    resolved: str = "-"
    size_mb: float | None = None
    installed: str = "-"
    imported_and_ran: str = "-"
    seconds: float = 0.0
    failing_step: str = ""
    detail: str = ""
    output: str = field(default="", repr=False)


def discover_targets(text: str) -> list[Target]:
    """Parse `py -0p` output: lines like ' -V:3.14 *  D:\\Python\\python.exe'."""
    targets = []
    for line in text.splitlines():
        m = re.match(r"\s*-V:(\S+)\s+\*?\s*(.+python(?:\.exe)?)\s*$", line, re.IGNORECASE)
        if m:
            label = m.group(1).split("/")[-1].replace("CPython", "")
            targets.append(Target(label, m.group(2).strip()))
    return targets


def venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def report_size_mb(report: dict, head=None) -> float | None:
    """Total download size of everything pip would fetch, from the report's URLs. None when it cannot be learned."""
    head = head or _content_length
    total = 0
    for item in report.get("install", []):
        url = (item.get("download_info") or {}).get("url", "")
        if not url.startswith("http"):
            return None
        size = head(url)
        if size is None:
            return None
        total += size
    return round(total / BYTES_PER_MB, 1)


def _content_length(url: str) -> int | None:
    try:
        r = httpx.head(url, follow_redirects=True, timeout=30)
        return int(r.headers["content-length"]) if "content-length" in r.headers else None
    except (httpx.HTTPError, ValueError):
        return None


def classify_failure(step: str, stderr: str) -> str:
    """One short reason for a failed step, from its error text."""
    low = stderr.lower()
    if step == "resolve" and ("no matching distribution" in low or "could not find a version" in low):
        return "no wheel or version for this Python"
    if step == "resolve" and "requires-python" in low:
        return "package excludes this Python version"
    if step == "install" and ("failed building" in low or "error: subprocess-exited-with-error" in low):
        return "needs a build from source that failed"
    if "modulenotfounderror" in low or "importerror" in low:
        return "imports fail after install"
    last = [ln for ln in stderr.strip().splitlines() if ln.strip()]
    return (last[-1][:160] if last else "no error text")


def run(cmd: list[str], timeout: int) -> tuple[int, str, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"timed out after {timeout}s"


def check_package(target: Target, pkg: dict, venv: Path, install: bool, max_mb: float, allow_large: bool, approved: set[str]) -> Row:
    row = Row(pkg["name"], target.label)
    py = str(venv_python(venv))
    started = time.perf_counter()

    if pkg.get("requires_approval") and pkg["name"] not in approved:
        row.failing_step, row.detail = "approval", f"not run: needs --approve {pkg['name']} ({pkg.get('note', '')})"
        return row

    report_file = venv / f"{pkg['name']}.report.json"
    code, _, err = run([py, "-m", "pip", "install", "--dry-run", "--ignore-installed", "--quiet", "--report", str(report_file), pkg["spec"]], PIP_TIMEOUT_S)
    if code != 0:
        row.resolved, row.failing_step, row.detail = "no", "resolve", classify_failure("resolve", err)
        row.seconds = round(time.perf_counter() - started, 1)
        return row
    row.resolved = "yes"
    row.size_mb = report_size_mb(json.loads(report_file.read_text(encoding="utf-8")))

    if not install:
        row.detail = "resolve only (run with --install to install and test)"
    elif row.size_mb is not None and row.size_mb > max_mb and not allow_large:
        row.failing_step, row.detail = "size", f"not installed: {row.size_mb} MB is over the {max_mb} MB cap (use --allow-large)"
    else:
        code, _, err = run([py, "-m", "pip", "install", "--quiet", pkg["spec"]], PIP_TIMEOUT_S)
        if code != 0:
            row.installed, row.failing_step, row.detail = "no", "install", classify_failure("install", err)
        else:
            row.installed = "yes"
            code, out, err = run([py, "-I", "-c", pkg["op"]], OP_TIMEOUT_S)
            row.output = out.strip()
            if code == 0 and pkg["expect"] in out:
                row.imported_and_ran = "yes"
                row.detail = f"{row.output} | {pkg.get('note', '')}".strip(" |")
            else:
                row.imported_and_ran, row.failing_step = "no", "import_or_op"
                row.detail = classify_failure("op", err or out)
    row.seconds = round(time.perf_counter() - started, 1)
    return row


def render(rows: list[Row], targets: list[Target], installed_run: bool, stamp: str) -> str:
    out = [
        "# Environment matrix (ticket F1-01)",
        "",
        f"Generated {stamp} by `scripts/check_env.py{' --install' if installed_run else ''}`. Each package was tried in a throwaway virtual environment.",
        "",
        "Pythons found: " + ", ".join(f"{t.label}" for t in targets),
        "",
        "| Package | Python | Resolves | Download MB | Installed | Imports and ran | Seconds | Failing step | Detail |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        size = "?" if r.size_mb is None else f"{r.size_mb}"
        out.append(f"| {r.package} | {r.python} | {r.resolved} | {size} | {r.installed} | {r.imported_and_ran} | {r.seconds} | {r.failing_step or '-'} | {r.detail.replace('|', '/')} |")
    return "\n".join(out) + "\n"


DECISIONS_MARK = "## Decisions"


def keep_decisions(new_text: str, old_text: str) -> str:
    """The matrix is regenerated; the hand-written decisions below it are not."""
    if DECISIONS_MARK in old_text:
        return new_text + chr(10) + old_text[old_text.index(DECISIONS_MARK):]
    return new_text


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--install", action="store_true", help="install into the throwaway venv and run the tiny operation")
    ap.add_argument("--allow-large", action="store_true", help="install downloads above max_download_mb")
    ap.add_argument("--approve", action="append", default=[], help="package name that needs approval (git sources)")
    ap.add_argument("--only", action="append", default=[], help="limit to these package names")
    ap.add_argument("--python", action="append", default=[], help="limit to these labels, e.g. 3.14")
    ap.add_argument("--write-report", action="store_true", help=f"write {REPORT.relative_to(ROOT)}")
    args = ap.parse_args(argv)

    cfg = yaml.safe_load((ROOT / "config" / "env_check.yaml").read_text(encoding="utf-8"))
    _, listing, _ = run(["py", "-0p"], 30)
    targets = [t for t in discover_targets(listing) if not args.python or any(t.label.startswith(p) for p in args.python)]
    if not targets:
        print("no Python interpreters found via `py -0p`", file=sys.stderr)
        return 2
    packages = [p for p in cfg["packages"] if not args.only or p["name"] in args.only]

    rows: list[Row] = []
    work = Path(tempfile.mkdtemp(prefix="forge-envcheck-"))
    try:
        for t in targets:
            venv = work / t.label.replace("/", "_")
            code, _, err = run([t.exe, "-m", "venv", str(venv)], 300)
            if code != 0:
                rows.append(Row("(venv)", t.label, failing_step="venv", detail=classify_failure("venv", err)))
                continue
            for pkg in packages:
                row = check_package(t, pkg, venv, args.install, cfg["max_download_mb"], args.allow_large, set(args.approve))
                rows.append(row)
                print(f"{row.package:16} py{row.python:10} resolves={row.resolved:3} mb={row.size_mb} installed={row.installed:3} ran={row.imported_and_ran:3} {row.failing_step} {row.detail[:90]}", flush=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    if args.write_report:
        from datetime import datetime, timezone
        old = REPORT.read_text(encoding="utf-8") if REPORT.exists() else ""
        text = render(rows, targets, args.install, datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
        REPORT.write_text(keep_decisions(text, old), encoding="utf-8")
        print(f"wrote {REPORT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
