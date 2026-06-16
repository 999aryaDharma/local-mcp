"""
Custom exception hierarchy for docctx.
"""
from __future__ import annotations


class DocctxError(Exception):
    """Base exception for all docctx errors."""

    error_code: str = "DOCCTX_ERROR"

    def __init__(self, message: str, hint: str | None = None):
        super().__init__(message)
        self.hint = hint


class PackExistsError(DocctxError):
    """Pack already exists. Use `refresh` to update."""

    error_code = "PACK_EXISTS"


class PackNotFoundError(DocctxError):
    """Pack does not exist."""

    error_code = "PACK_NOT_FOUND"


class ScopeAmbiguousError(DocctxError):
    """Root URL provided without explicit --scope flag."""

    error_code = "SCOPE_AMBIGUOUS"


class FetchError(DocctxError):
    """Failed to fetch a URL."""

    error_code = "FETCH_FAILED"


class ExtractionEmptyError(DocctxError):
    """Extraction returned empty content. May be JS-rendered."""

    error_code = "EXTRACTION_EMPTY"


class SchemaVersionError(DocctxError):
    """DB schema version does not match binary version."""

    error_code = "SCHEMA_VERSION_MISMATCH"


class ConfigError(DocctxError):
    """Configuration file is invalid."""

    error_code = "CONFIG_INVALID"
