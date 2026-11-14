import pytest

from moonmesh_train import paths


def test_default_root(monkeypatch):
    monkeypatch.delenv("MOONMESH_DATA_ROOT", raising=False)
    assert paths.data_root() == paths.repo_root() / "data"


def test_environment_root_and_on_demand_creation(monkeypatch, tmp_path):
    root = tmp_path / "data with spaces"
    monkeypatch.setenv("MOONMESH_DATA_ROOT", str(root))
    assert paths.data_root() == root
    assert paths.raw_dir("lunarrf", create=False) == root / "raw" / "lunarrf"
    assert not root.exists()
    assert paths.reports_dir().is_dir()
    assert not (root / "raw").exists()
    directories = paths.ensure_data_dirs()
    assert set(directories) == {
        "raw/lunarrf", "raw/pgda90", "raw/pgda78", "raw/lola_pds",
        "interim", "derived/terrain", "derived/labels", "derived/datasets", "models", "reports",
    }
    assert all(path.is_dir() and path.is_relative_to(root) for path in directories.values())
    # Repeated calls are safe and do not create files in the raw directories.
    assert paths.ensure_data_dirs() == directories
    assert not any(path.is_file() for path in root.rglob("*"))


def test_relative_root_is_independent_of_cwd(monkeypatch, tmp_path):
    monkeypatch.setenv("MOONMESH_DATA_ROOT", "relative-data")
    monkeypatch.chdir(tmp_path)
    assert paths.data_root() == paths.repo_root() / "relative-data"


def test_empty_root_rejected(monkeypatch):
    monkeypatch.setenv("MOONMESH_DATA_ROOT", " ")
    with pytest.raises(ValueError, match="must not be empty"):
        paths.data_root()


def test_unknown_or_escaping_subdirectories_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("MOONMESH_DATA_ROOT", str(tmp_path / "unused"))
    with pytest.raises(ValueError, match="Unknown raw source"):
        paths.raw_dir("../../escape")
    with pytest.raises(ValueError, match="Unknown derived kind"):
        paths.derived_dir("typo")
    assert not (tmp_path / "unused").exists()
