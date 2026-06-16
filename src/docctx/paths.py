"""
Path resolution for docctx data directories.
Priority: DOCCTX_HOME env var → ~/.docctx → platformdirs fallback.
"""
from __future__ import annotations

import os
from pathlib import Path


def get_docctx_home() -> Path:
    """Return the docctx home directory, creating it if needed."""
    env_home = os.environ.get("DOCCTX_HOME")
    if env_home:
        home = Path(env_home).expanduser().resolve()
    else:
        home = Path.home() / ".docctx"

    home.mkdir(parents=True, exist_ok=True)
    return home


def get_db_path() -> Path:
    """Return path to the SQLite database file."""
    return get_docctx_home() / "store.db"


def get_cache_dir() -> Path:
    """Return path to the HTML cache directory."""
    cache = get_docctx_home() / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    return cache


def get_config_path() -> Path:
    """Return path to config.toml."""
    return get_docctx_home() / "config.toml"
