import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("check_no_fixtures", ROOT / "scripts" / "check_no_fixtures.py")
guard = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = guard  # dataclasses looks the module up by name
spec.loader.exec_module(guard)


def run(tmp_path, code: str, name: str = "m.py"):
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    (src / name).write_text(code, encoding="utf-8")
    return guard.scan(src)


def rules(findings):
    return {f.rule for f in findings}


def test_url_literal_is_flagged_but_docstring_url_is_not(tmp_path):
    assert rules(run(tmp_path, 'X = "https://example.org/api"\n')) == {"url-literal"}
    assert run(tmp_path, '"""See https://example.org"""\n') == []


def test_data_file_in_src_is_flagged(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "claims.json").write_text("[]", encoding="utf-8")
    assert rules(guard.scan(src)) == {"data-file"}


def test_import_from_tests_is_flagged(tmp_path):
    assert rules(run(tmp_path, "from tests.helpers import x\n")) == {"tests-import"}
    assert rules(run(tmp_path, "import tests\n")) == {"tests-import"}


def test_demo_and_mock_names_are_flagged(tmp_path):
    assert rules(run(tmp_path, "def go(use_mock=False):\n    pass\n")) == {"demo-switch"}
    assert rules(run(tmp_path, "demo_mode = True\n")) == {"demo-switch"}
    assert run(tmp_path, "demographics = 1\n") == []


def test_numeric_literal_in_comparison_is_flagged_except_small(tmp_path):
    assert rules(run(tmp_path, "def f(s):\n    return s > 0.8\n")) == {"numeric-literal"}
    assert run(tmp_path, "def f(s):\n    return s > 0 and s != 1 and s != -1\n") == []


def test_allow_comment_with_reason_suppresses_but_bare_does_not(tmp_path):
    assert run(tmp_path, "def f(s):\n    return s > 60  # forge: allow-literal seconds per minute\n") == []
    assert rules(run(tmp_path, "def f(s):\n    return s > 60  # forge: allow-literal\n")) == {"numeric-literal"}


def test_real_src_tree_is_clean():
    assert guard.scan(ROOT / "src") == []


def test_main_exit_codes(tmp_path, capsys):
    run(tmp_path, "X = 'http://a.b'\n")
    assert guard.main([str(tmp_path / "src")]) == 1
    (tmp_path / "src" / "m.py").write_text("X = 1\n", encoding="utf-8")
    assert guard.main([str(tmp_path / "src")]) == 0
    assert guard.main([str(tmp_path / "nope")]) == 2
