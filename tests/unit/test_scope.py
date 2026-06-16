"""
Unit tests for scope resolution.
"""
import pytest
from docctx.exceptions import ScopeAmbiguousError
from docctx.ingestion.scope import resolve_scope, url_in_scope


def test_deep_url_defaults_to_siblings():
    scope = resolve_scope("https://react.dev/reference/react/useEffect", None)
    assert scope.rule == "siblings"


def test_root_url_without_scope_raises():
    with pytest.raises(ScopeAmbiguousError):
        resolve_scope("https://react.dev", None)


def test_domain_url_without_scope_raises():
    with pytest.raises(ScopeAmbiguousError):
        resolve_scope("https://docs.python.org/", None)


def test_explicit_scope_overrides_inference():
    scope = resolve_scope("https://react.dev/reference/react/useEffect", "page-only")
    assert scope.rule == "page-only"


def test_explicit_site_scope():
    scope = resolve_scope("https://react.dev/reference/react/useEffect", "site")
    assert scope.rule == "site"
    assert scope.base_prefix == "https://react.dev/"


def test_siblings_scope_boundary():
    scope = resolve_scope("https://react.dev/reference/react/useEffect", "siblings")
    # Sibling should be in scope
    assert url_in_scope("https://react.dev/reference/react/useState", scope)
    # Different section should NOT be in scope
    assert not url_in_scope("https://react.dev/learn/thinking-in-react", scope)


def test_page_only_scope():
    scope = resolve_scope("https://react.dev/reference/react/useEffect", "page-only")
    assert url_in_scope("https://react.dev/reference/react/useEffect", scope)
    assert not url_in_scope("https://react.dev/reference/react/useState", scope)


def test_cross_domain_always_excluded():
    scope = resolve_scope("https://react.dev/reference/react/useEffect", "site")
    assert not url_in_scope("https://other.com/page", scope)


def test_subtree_scope():
    scope = resolve_scope("https://react.dev/reference/react/useEffect", "subtree")
    # Subtree includes descendants
    assert url_in_scope("https://react.dev/reference/react/useEffect/examples", scope)
    # Siblings should NOT be in subtree
    assert not url_in_scope("https://react.dev/reference/react/useState", scope)


def test_fragment_urls_stripped():
    scope = resolve_scope("https://react.dev/reference/react/useEffect", "page-only")
    # URL with fragment should be treated as the same page
    assert url_in_scope("https://react.dev/reference/react/useEffect#cleanup", scope)
