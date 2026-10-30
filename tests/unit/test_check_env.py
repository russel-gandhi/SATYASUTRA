import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("check_env", ROOT / "scripts" / "check_env.py")
ce = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = ce
spec.loader.exec_module(ce)

LISTING = r""" -V:3.14 *        D:\Python\python.exe
 -V:Astral/CPython3.11.15 C:\Users\x\uv\python\cpython-3.11.15\python.exe
"""


def test_discover_targets_parses_py_launcher_listing():
    ts = ce.discover_targets(LISTING)
    assert [t.label for t in ts] == ["3.14", "3.11.15"]
    assert ts[0].exe == r"D:\Python\python.exe"


def test_classify_failure_reasons():
    assert "no wheel" in ce.classify_failure("resolve", "ERROR: No matching distribution found for x")
    assert "build" in ce.classify_failure("install", "error: subprocess-exited-with-error")
    assert "imports fail" in ce.classify_failure("op", "ModuleNotFoundError: No module named 'x'")
    assert ce.classify_failure("op", "boom\nlast line here") == "last line here"
    assert ce.classify_failure("op", "") == "no error text"


def test_report_size_sums_downloads_and_is_unknown_when_any_is_unknown():
    report = {"install": [{"download_info": {"url": "https://a/x.whl"}}, {"download_info": {"url": "https://a/y.whl"}}]}
    assert ce.report_size_mb(report, head=lambda u: 1024 * 1024) == 2.0
    assert ce.report_size_mb(report, head=lambda u: None) is None
    assert ce.report_size_mb({"install": [{"download_info": {"url": "file:///x"}}]}, head=lambda u: 1) is None


def test_git_source_package_is_not_run_without_approval(tmp_path):
    pkg = {"name": "p", "spec": "p @ git+https://example.org/p.git", "op": "pass", "expect": "x", "requires_approval": True, "note": "n"}
    row = ce.check_package(ce.Target("3.14", "python"), pkg, tmp_path, install=True, max_mb=1, allow_large=False, approved=set())
    assert row.failing_step == "approval" and row.installed == "-"


def test_render_has_one_row_per_result_and_escapes_pipes():
    rows = [ce.Row("a", "3.14", resolved="yes", detail="x|y"), ce.Row("b", "3.11.15", failing_step="resolve", resolved="no")]
    text = ce.render(rows, [ce.Target("3.14", "p")], installed_run=False, stamp="T")
    assert text.count("\n| a |") == 1 and text.count("\n| b |") == 1 and "x/y" in text


def test_config_lists_every_required_package():
    import yaml

    names = {p["name"] for p in yaml.safe_load((ROOT / "config" / "env_check.yaml").read_text(encoding="utf-8"))["packages"]}
    assert {"torch", "onnxruntime", "mediapipe", "faster-whisper", "minicheck"} <= names


def test_regenerating_the_matrix_keeps_hand_written_decisions():
    old = "# old\n\n## Decisions\n\nkeep me\n"
    assert "keep me" in ce.keep_decisions("# new\n", old)
    assert ce.keep_decisions("# new\n", "# old\n") == "# new\n"
