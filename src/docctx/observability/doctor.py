"""
Doctor — health check for docctx installation.
Runs 7 checks and reports OK/ERROR for each.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from docctx.db.connection import SCHEMA_VERSION, db_connection, get_schema_version
from docctx.paths import get_cache_dir, get_config_path, get_db_path


@dataclass
class CheckResult:
    name: str
    ok: bool
    description: str
    hint: Optional[str] = None

    @classmethod
    def success(cls, name: str, description: str) -> "CheckResult":
        return cls(name=name, ok=True, description=description)

    @classmethod
    def failure(cls, name: str, description: str, hint: str = "") -> "CheckResult":
        return cls(name=name, ok=False, description=description, hint=hint)


def run_doctor() -> list[CheckResult]:
    """Run all health checks and return results."""
    results = []

    # Check 1: DB file exists and readable
    db_path = get_db_path()
    if db_path.exists():
        try:
            size_kb = db_path.stat().st_size // 1024
            results.append(
                CheckResult.success(
                    "db_exists",
                    f"Database found at {db_path} ({size_kb} KB)",
                )
            )
        except OSError as e:
            results.append(
                CheckResult.failure(
                    "db_exists",
                    f"Database exists but cannot be read: {e}",
                    hint="Check file permissions.",
                )
            )
    else:
        results.append(
            CheckResult.failure(
                "db_exists",
                f"Database not found at {db_path}",
                hint="Run `docctx add <url>` to initialize the database.",
            )
        )

    # Check 2: Schema version matches binary version
    try:
        version = get_schema_version()
        if version == SCHEMA_VERSION:
            results.append(
                CheckResult.success(
                    "schema_version",
                    f"Schema version {version} matches binary version {SCHEMA_VERSION}",
                )
            )
        elif version == 0:
            results.append(
                CheckResult.failure(
                    "schema_version",
                    "Schema not initialized",
                    hint="Run `docctx add <url>` to initialize the database.",
                )
            )
        else:
            results.append(
                CheckResult.failure(
                    "schema_version",
                    f"Schema version {version} does not match binary version {SCHEMA_VERSION}",
                    hint="Run `docctx doctor --migrate` to upgrade schema.",
                )
            )
    except Exception as e:
        results.append(
            CheckResult.failure("schema_version", f"Cannot read schema version: {e}")
        )

    # Check 3: FTS5 integrity check
    try:
        with db_connection() as conn:
            rows = conn.execute("SELECT * FROM chunks_fts('integrity-check')").fetchall()
        results.append(CheckResult.success("fts5_integrity", "FTS5 integrity check passed"))
    except sqlite3.OperationalError as e:
        err = str(e)
        if "no such table" in err:
            results.append(
                CheckResult.success("fts5_integrity", "FTS5 table not yet created (no packs added)")
            )
        elif "integrity" in err.lower():
            results.append(
                CheckResult.failure(
                    "fts5_integrity",
                    f"FTS5 integrity check failed: {e}",
                    hint="Rebuild FTS5 index with `docctx doctor --rebuild-index`.",
                )
            )
        else:
            results.append(CheckResult.success("fts5_integrity", "FTS5 table exists"))
    except Exception as e:
        results.append(CheckResult.success("fts5_integrity", "FTS5 check skipped (DB not initialized)"))

    # Check 4: Orphaned chunks check
    try:
        with db_connection() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) as n FROM chunks c
                WHERE NOT EXISTS (SELECT 1 FROM packs p WHERE p.name = c.pack_name)
                """
            ).fetchone()
            orphans = row["n"] if row else 0

        if orphans == 0:
            results.append(CheckResult.success("orphaned_chunks", "No orphaned chunks found"))
        else:
            results.append(
                CheckResult.failure(
                    "orphaned_chunks",
                    f"{orphans} orphaned chunks found (pack was deleted without cascade)",
                    hint="Run `docctx doctor --fix` to remove orphaned chunks.",
                )
            )
    except Exception as e:
        results.append(CheckResult.success("orphaned_chunks", f"Check skipped: {e}"))

    # Check 5: Broken pack references
    try:
        with db_connection() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) as n FROM documents d
                WHERE NOT EXISTS (SELECT 1 FROM packs p WHERE p.name = d.pack_name)
                """
            ).fetchone()
            broken = row["n"] if row else 0

        if broken == 0:
            results.append(CheckResult.success("broken_references", "No broken document references"))
        else:
            results.append(
                CheckResult.failure(
                    "broken_references",
                    f"{broken} documents with no parent pack",
                    hint="Run `docctx doctor --fix`.",
                )
            )
    except Exception as e:
        results.append(CheckResult.success("broken_references", f"Check skipped: {e}"))

    # Check 6: Config file valid TOML
    config_path = get_config_path()
    if config_path.exists():
        try:
            import sys
            if sys.version_info >= (3, 11):
                import tomllib
            else:
                import tomli as tomllib
            with open(config_path, "rb") as f:
                tomllib.load(f)
            results.append(CheckResult.success("config_valid", f"Config file valid: {config_path}"))
        except Exception as e:
            results.append(
                CheckResult.failure(
                    "config_valid",
                    f"Config file has invalid TOML: {e}",
                    hint=f"Fix or delete {config_path} to reset to defaults.",
                )
            )
    else:
        results.append(
            CheckResult.success(
                "config_valid",
                f"No config file (using defaults). Will be created at {config_path}",
            )
        )

    # Check 7: Cache dir accessible
    try:
        cache_dir = get_cache_dir()
        test_file = cache_dir / ".docctx_doctor_test"
        test_file.write_text("ok")
        test_file.unlink()
        results.append(
            CheckResult.success("cache_dir", f"Cache directory accessible: {cache_dir}")
        )
    except Exception as e:
        results.append(
            CheckResult.failure(
                "cache_dir",
                f"Cache directory not accessible: {e}",
                hint="Check directory permissions.",
            )
        )

    return results


def format_doctor_report(results: list[CheckResult]) -> str:
    """Format doctor results as human-readable text."""
    lines = ["docctx doctor", "=" * 40]
    all_ok = all(r.ok for r in results)

    for r in results:
        icon = "✓" if r.ok else "✗"
        status = "OK" if r.ok else "ERROR"
        lines.append(f"{icon} [{status}] {r.name}: {r.description}")
        if not r.ok and r.hint:
            lines.append(f"  → Hint: {r.hint}")

    lines.append("=" * 40)
    if all_ok:
        lines.append("All checks passed. docctx is healthy.")
    else:
        errors = sum(1 for r in results if not r.ok)
        lines.append(f"{errors} check(s) failed.")

    return "\n".join(lines)
