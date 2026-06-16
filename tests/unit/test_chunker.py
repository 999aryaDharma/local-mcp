"""
Unit tests for chunker module.
"""
import pytest
from docctx.ingestion.chunker import (
    chunk_document,
    estimate_tokens,
    generate_summary,
)


def test_estimate_tokens_basic():
    assert estimate_tokens("hello world") == max(1, len("hello world") // 4)
    assert estimate_tokens("") == 1


def test_generate_summary_with_heading_and_sentence():
    summary = generate_summary("React > Hooks > useEffect", "The useEffect hook runs after every render.")
    assert "[Hooks > useEffect]" in summary
    assert "useEffect hook" in summary
    assert len(summary) <= 150


def test_generate_summary_truncates():
    long_content = "A" * 300 + ". More text."
    summary = generate_summary("A > B > C", long_content)
    assert len(summary) <= 150


def test_chunk_document_basic():
    markdown = """# Overview

This is the overview section with some content.

## Installation

Install using npm or yarn. Here is how you do it.

## Usage

Basic usage example. You can run this directly in your terminal.
It is very easy to use and provides a lot of features out of the box.
Make sure to check the documentation for more details.
This section is now long enough to be kept!
"""
    chunks = chunk_document(markdown, pack_name="test", doc_url="http://example.com/doc")
    assert len(chunks) >= 2
    # All chunks should have IDs
    assert all(c.id for c in chunks)
    # All chunks should have heading paths
    assert all(c.heading_path for c in chunks)


def test_chunk_document_links_prev_next():
    markdown = """# A

Content A.

## B

Content B.

## C

Content C.
"""
    chunks = chunk_document(markdown, pack_name="test", doc_url="http://example.com/")
    assert chunks[0].prev_chunk_id is None
    assert chunks[-1].next_chunk_id is None
    if len(chunks) > 1:
        assert chunks[0].next_chunk_id == chunks[1].id
        assert chunks[1].prev_chunk_id == chunks[0].id


def test_chunk_document_no_headings():
    markdown = "Just some plain text without any headings. It should still produce a chunk. This is extra text to ensure that the token count exceeds twenty tokens, so that the new filter for removing micro terminal chunks does not drop this chunk."
    chunks = chunk_document(markdown, pack_name="test", doc_url="http://example.com/")
    assert len(chunks) == 1
    assert chunks[0].content


def test_chunk_document_code_blocks_preserved():
    markdown = """# Code Example

Here is some code:

```python
def hello():
    return "world"
```

More text after.
"""
    chunks = chunk_document(markdown, pack_name="test", doc_url="http://example.com/")
    assert any("hello" in c.code_content for c in chunks)
    # Code block should not be split mid-block
    for c in chunks:
        assert c.content.count("```") % 2 == 0


def test_chunk_ids_are_deterministic():
    markdown = "# Section\n\nSome content here."
    chunks1 = chunk_document(markdown, pack_name="test", doc_url="http://example.com/")
    chunks2 = chunk_document(markdown, pack_name="test", doc_url="http://example.com/")
    assert [c.id for c in chunks1] == [c.id for c in chunks2]
