import shutil
from pathlib import Path

import pytest

from satyasutra.config import get_secret, has_secret, load_config
from satyasutra.errors import ConfigError, SchemaChanged, SourceError, Unreachable

REAL = Path(__file__).resolve().parents[2] / "config"


@pytest.fixture
def cfgdir(tmp_path):
    for f in REAL.glob("*.yaml"):
        shutil.copy(f, tmp_path / f.name)
    return tmp_path


def test_real_config_loads_and_is_unverified():
    cfg = load_config(REAL)
    assert cfg.sources.sources
    assert all(s.status == "unverified" for s in cfg.sources.sources)
    assert len(cfg.checksum) == 64
    assert cfg.source("pib").tier == 1


def test_checksum_changes_with_content(cfgdir):
    before = load_config(cfgdir).checksum
    runtime = (cfgdir / "runtime.yaml").read_text(encoding="utf-8")
    (cfgdir / "runtime.yaml").write_text(runtime.replace("timeout_s: 8", "timeout_s: 3"), encoding="utf-8")
    assert load_config(cfgdir).checksum != before


def test_missing_file_is_a_config_error(cfgdir):
    (cfgdir / "policy.yaml").unlink()
    with pytest.raises(ConfigError, match="policy.yaml"):
        load_config(cfgdir)


def test_unknown_field_rejected(cfgdir):
    text = (cfgdir / "policy.yaml").read_text(encoding="utf-8") + "surprise: 1\n"
    (cfgdir / "policy.yaml").write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match="surprise"):
        load_config(cfgdir)


def test_bad_type_and_bad_url_rejected(cfgdir):
    (cfgdir / "sources.yaml").write_text(
        "sources:\n  - id: a\n    role: r\n    probe_url: ftp://nope\n    tier: 9\n", encoding="utf-8"
    )
    with pytest.raises(ConfigError) as exc:
        load_config(cfgdir)
    assert "probe_url" in str(exc.value) and "tier" in str(exc.value)


def test_duplicate_source_ids_rejected(cfgdir):
    entry = "  - id: a\n    role: r\n    probe_url: https://x.test/\n    tier: 1\n"
    (cfgdir / "sources.yaml").write_text("sources:\n" + entry + entry, encoding="utf-8")
    with pytest.raises(ConfigError, match="duplicate"):
        load_config(cfgdir)


def test_invalid_yaml_is_a_config_error(cfgdir):
    (cfgdir / "thresholds.yaml").write_text("thresholds: [unclosed", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_config(cfgdir)


def test_unknown_source_lookup(cfgdir):
    with pytest.raises(ConfigError):
        load_config(cfgdir).source("nope")


def test_secrets_only_from_environment(monkeypatch):
    monkeypatch.delenv("FORGE_TEST_KEY", raising=False)
    assert not has_secret("FORGE_TEST_KEY")
    with pytest.raises(ConfigError, match="FORGE_TEST_KEY"):
        get_secret("FORGE_TEST_KEY")
    monkeypatch.setenv("FORGE_TEST_KEY", "abc")
    assert get_secret("FORGE_TEST_KEY") == "abc" and has_secret("FORGE_TEST_KEY")
    assert not has_secret(None)


def test_typed_source_errors_carry_source_id():
    err = Unreachable("pib", "timeout")
    assert isinstance(err, SourceError) and err.source_id == "pib" and "timeout" in str(err)
    assert isinstance(SchemaChanged("x"), SourceError)
