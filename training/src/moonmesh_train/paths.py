"""Resolve training directories without touching the app cache or raw files."""

import os
from pathlib import Path

RAW_SOURCES = ("lunarrf", "pgda90", "pgda78", "lola_pds")
DERIVED_KINDS = ("terrain", "labels", "datasets")


def repo_root() -> Path:
    """Repository root for this source checkout / editable installation."""
    return Path(__file__).resolve().parents[3]


def data_root() -> Path:
    """Read the environment on each call; resolve only, do not create anything.

    Relative environment paths are interpreted relative to the repository root,
    so changing a script's working directory does not change its data location.
    """
    configured = os.environ.get("MOONMESH_DATA_ROOT")
    if configured is not None and not configured.strip():
        raise ValueError("MOONMESH_DATA_ROOT must not be empty")
    path = Path(configured).expanduser() if configured is not None else repo_root() / "data"
    if not path.is_absolute():
        path = repo_root() / path
    return path.resolve()


def _directory(*parts: str, create: bool = True) -> Path:
    root = data_root()
    path = root.joinpath(*parts).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"Training directory must stay under data root: {path}")
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def raw_dir(source: str, *, create: bool = True) -> Path:
    """Get a source directory; never create or overwrite source files."""
    if source not in RAW_SOURCES:
        raise ValueError(f"Unknown raw source {source!r}; choose from {RAW_SOURCES}")
    return _directory("raw", source, create=create)


def interim_dir(*, create: bool = True) -> Path:
    return _directory("interim", create=create)


def derived_dir(kind: str, *, create: bool = True) -> Path:
    if kind not in DERIVED_KINDS:
        raise ValueError(f"Unknown derived kind {kind!r}; choose from {DERIVED_KINDS}")
    return _directory("derived", kind, create=create)


def models_dir(*, create: bool = True) -> Path:
    return _directory("models", create=create)


def reports_dir(*, create: bool = True) -> Path:
    return _directory("reports", create=create)


def ensure_data_dirs() -> dict[str, Path]:
    """Explicitly create the complete empty layout when a pipeline needs it."""
    directories = {f"raw/{source}": raw_dir(source) for source in RAW_SOURCES}
    directories.update({f"derived/{kind}": derived_dir(kind) for kind in DERIVED_KINDS})
    directories.update(interim=interim_dir(), models=models_dir(), reports=reports_dir())
    return directories
