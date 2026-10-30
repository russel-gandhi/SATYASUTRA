import importlib.util
import textwrap
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("forge", Path(__file__).resolve().parents[2] / "tools" / "forge.py")
forge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(forge)


def write(dirpath: Path, tid: str, status="todo", depends="[]", body="## Goal\n"):
    (dirpath / f"{tid}.md").write_text(
        textwrap.dedent(f"""\
        ---
        id: {tid}
        phase: {tid[1]}
        title: t {tid}
        status: {status}
        depends: {depends}
        plan: x
        ---
        {body}
        """),
        encoding="utf-8",
    )


@pytest.fixture
def tdir(tmp_path, monkeypatch):
    monkeypatch.setattr(forge, "TICKETS", tmp_path)
    return tmp_path


def test_real_tickets_are_valid():
    tickets = forge.load_all()
    assert tickets, "no tickets found"
    assert forge.validate(tickets) == []


def test_start_blocked_by_dependency(tdir):
    write(tdir, "F0-01")
    write(tdir, "F0-02", depends="[F0-01]")
    with pytest.raises(SystemExit):
        forge.main(["start", "F0-02"])


def test_lifecycle_records_evidence(tdir):
    write(tdir, "F0-01")
    assert forge.main(["start", "F0-01"]) == 0
    assert forge.main(["done", "F0-01", "--evidence", "pytest passed"]) == 0
    text = (tdir / "F0-01.md").read_text(encoding="utf-8")
    assert "status: done" in text and "Evidence: pytest passed" in text


def test_cannot_close_unstarted(tdir):
    write(tdir, "F0-01")
    with pytest.raises(SystemExit):
        forge.main(["done", "F0-01"])


def test_unknown_dependency_and_bad_status_reported(tdir):
    write(tdir, "F0-01", status="weird", depends="[F9-99]")
    problems = forge.validate(forge.load_all())
    assert any("bad status" in p for p in problems)
    assert any("unknown dependency" in p for p in problems)


def test_done_without_closed_section_reported(tdir):
    write(tdir, "F0-01", status="done")
    assert any("no Closed section" in p for p in forge.validate(forge.load_all()))


def test_new_ticket_gets_next_number(tdir):
    write(tdir, "F0-01")
    assert forge.main(["new", "--phase", "0", "--title", "next", "--depends", "F0-01"]) == 0
    assert (tdir / "F0-02.md").exists()
