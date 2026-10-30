from satyasutra.envfile import load_env_file


def test_loads_names_not_values_and_skips_comments_and_blanks(tmp_path):
    f = tmp_path / ".env"
    f.write_text('# note\n\nA=one\nexport B="two"\nC=\nbad line\n', encoding="utf-8")
    env: dict = {}
    names = load_env_file(f, env)
    assert names == ["A", "B"] and env == {"A": "one", "B": "two"}


def test_existing_environment_wins(tmp_path):
    f = tmp_path / ".env"
    f.write_text("A=from_file\n", encoding="utf-8")
    env = {"A": "from_shell"}
    assert load_env_file(f, env) == [] and env["A"] == "from_shell"


def test_missing_file_is_fine(tmp_path):
    assert load_env_file(tmp_path / "nope", {}) == []
