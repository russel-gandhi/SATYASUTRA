"""No-fixtures guard (hackathon rule: no mock, static or hardcoded data in the product code).

Scans src/ and reports:
  url-literal      a http(s):// string in code (sources live in config/sources.yaml)
  data-file        a data file shipped inside src/
  tests-import     product code importing from tests
  demo-switch      a name that toggles demo/mock/fake behaviour
  numeric-literal  a number (other than 0, 1, -1) in a comparison; thresholds belong in config/thresholds.yaml

A line may opt out with `# forge: allow-literal <reason>` (the reason is required).
Exit 0 when clean, 1 when findings exist.
"""

from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_SUFFIXES = {".json", ".csv", ".tsv", ".yaml", ".yml", ".sqlite", ".sqlite3", ".db", ".parquet", ".pkl", ".jsonl"}
DEMO_NAME = re.compile(r"(^|_)(demo|mock|mocks|fake|stub|dummy|fixture|fixtures|sample_data)(_|$)", re.IGNORECASE)
URL = re.compile(r"https?://", re.IGNORECASE)
ALLOW = re.compile(r"#\s*forge:\s*allow-literal\s+\S+")
SMALL = {0, 1, -1}


@dataclass(frozen=True)
class Finding:
    rule: str
    path: str
    line: int
    detail: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: [{self.rule}] {self.detail}"


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
                ids.add(id(body[0].value))
    return ids


def _numeric(node: ast.AST) -> float | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        inner = _numeric(node.operand)
        return -inner if inner is not None else None
    return None


def scan_file(path: Path, rel: str) -> list[Finding]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    tree = ast.parse(text, filename=rel)
    docs = _docstring_nodes(tree)
    found: list[Finding] = []

    def allowed(lineno: int) -> bool:
        return 0 < lineno <= len(lines) and bool(ALLOW.search(lines[lineno - 1]))

    def add(rule: str, node: ast.AST, detail: str) -> None:
        if not allowed(node.lineno):
            found.append(Finding(rule, rel, node.lineno, detail))

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs and URL.search(node.value):
            add("url-literal", node, f"URL in code: {node.value[:60]!r}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "tests":
                    add("tests-import", node, f"imports {alias.name}")
        elif isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "tests":
            add("tests-import", node, f"imports from {node.module}")
        elif isinstance(node, ast.Compare):
            for operand in [node.left, *node.comparators]:
                value = _numeric(operand)
                if value is not None and value not in SMALL:
                    add("numeric-literal", node, f"number {value} in a comparison")
        names: list[str] = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(node.name)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names += [a.arg for a in [*node.args.args, *node.args.kwonlyargs]]
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.append(node.id)
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
            names.append(node.attr)
        for name in names:
            if DEMO_NAME.search(name):
                add("demo-switch", node, f"name {name!r}")
    return found


def scan(src: Path, base: Path | None = None) -> list[Finding]:
    base = base or src.parent
    found: list[Finding] = []
    for path in sorted(src.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        rel = path.relative_to(base).as_posix()
        if path.suffix.lower() in DATA_SUFFIXES:
            found.append(Finding("data-file", rel, 1, "data file inside src/"))
        elif path.suffix == ".py":
            found.extend(scan_file(path, rel))
    return found


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    src = Path(args[0]) if args else ROOT / "src"
    if not src.is_dir():
        print(f"no such directory: {src}", file=sys.stderr)
        return 2
    findings = scan(src)
    for f in findings:
        print(f)
    print(f"{len(findings)} finding(s)" if findings else "ok: no fixtures, hardcoded sources or magic thresholds")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
