"""
Unit tests for FTS5 tokenizer.
"""
import pytest
from docctx.retrieval.tokenizer import build_fts5_query, normalize_for_index


def test_camel_case_expansion():
    result = normalize_for_index("useEffect")
    assert "useEffect" in result
    assert "use" in result
    assert "Effect" in result


def test_snake_case_expansion():
    result = normalize_for_index("fetch_data")
    assert "fetch_data" in result
    assert "fetch" in result
    assert "data" in result


def test_pascal_case_expansion():
    result = normalize_for_index("HTMLParser")
    assert "HTMLParser" in result
    assert "HTML" in result
    assert "Parser" in result


def test_plain_text_unchanged():
    result = normalize_for_index("hello world")
    assert "hello" in result
    assert "world" in result


def test_empty_string():
    assert normalize_for_index("") == ""


def test_no_duplicates():
    result = normalize_for_index("useEffect")
    tokens = result.lower().split()
    assert len(tokens) == len(set(tokens))


def test_build_fts5_query_returns_tuple():
    phrase, or_q = build_fts5_query("useEffect cleanup")
    assert isinstance(phrase, str)
    assert isinstance(or_q, str)


def test_build_fts5_query_phrase_format():
    phrase, _ = build_fts5_query("useEffect cleanup")
    assert phrase.startswith('"')


def test_build_fts5_query_or_format():
    _, or_q = build_fts5_query("useEffect cleanup")
    assert "OR" in or_q
