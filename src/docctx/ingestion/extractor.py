"""
HTML extractor — converts HTML to structured markdown with heading tree and code blocks.
Uses trafilatura as primary extractor with selectolax fallback.
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from docctx.exceptions import ExtractionEmptyError

logger = logging.getLogger(__name__)


@dataclass
class ExtractedDocument:
    url: str
    title: Optional[str]
    markdown: str               # full extracted markdown
    heading_tree: list[dict]    # [{level, text, anchor}]
    code_blocks: list[str]      # extracted code blocks
    content_hash: str           # SHA256 of markdown


def extract(html: str, url: str) -> ExtractedDocument:
    """
    Extract structured content from HTML.
    Primary: trafilatura (best quality markdown)
    Fallback: selectolax (basic extraction)
    """
    title = _extract_title(html)
    markdown = _try_trafilatura(html, url)

    if not markdown or len(markdown.strip()) < 50:
        logger.debug("trafilatura returned little content for %s, trying selectolax", url)
        markdown = _try_selectolax(html)

    if not markdown or len(markdown.strip()) < 50:
        raise ExtractionEmptyError(
            f"Could not extract content from {url}",
            hint="The page may be JS-rendered. JS support is planned for Phase 2.",
        )

    heading_tree = _extract_heading_tree(html)
    code_blocks = _extract_code_blocks(markdown)
    content_hash = hashlib.sha256(markdown.encode()).hexdigest()

    return ExtractedDocument(
        url=url,
        title=title,
        markdown=markdown,
        heading_tree=heading_tree,
        code_blocks=code_blocks,
        content_hash=content_hash,
    )


def _try_trafilatura(html: str, url: str) -> str:
    """Extract text using trafilatura, returning markdown."""
    try:
        import trafilatura

        result = trafilatura.extract(
            html,
            url=url,
            output_format="markdown",
            include_comments=False,
            include_tables=True,
            include_images=False,
            no_fallback=False,
            favor_recall=True,
        )
        return result or ""
    except Exception as e:
        logger.debug("trafilatura failed for %s: %s", url, e)
        return ""


def _try_selectolax(html: str) -> str:
    """Fallback extractor using selectolax."""
    try:
        from selectolax.parser import HTMLParser

        parser = HTMLParser(html)

        # Remove noise elements
        for tag in parser.css("script, style, nav, footer, header, aside, [role=navigation]"):
            tag.decompose()

        # Extract main content area preferentially
        main = parser.css_first("main, article, [role=main], .content, #content")
        if main:
            text = main.text(separator="\n", strip=True)
        else:
            body = parser.css_first("body")
            text = body.text(separator="\n", strip=True) if body else ""

        # Clean up excessive whitespace
        lines = [line.strip() for line in text.splitlines()]
        cleaned = "\n".join(line for line in lines if line)
        return cleaned
    except Exception as e:
        logger.debug("selectolax fallback failed: %s", e)
        return ""


def _extract_title(html: str) -> Optional[str]:
    """Extract page title from HTML."""
    try:
        from selectolax.parser import HTMLParser

        parser = HTMLParser(html)

        # Try <title> tag
        title_node = parser.css_first("title")
        if title_node and title_node.text(strip=True):
            return title_node.text(strip=True)

        # Try <h1>
        h1 = parser.css_first("h1")
        if h1 and h1.text(strip=True):
            return h1.text(strip=True)

        return None
    except Exception:
        return None


def _extract_heading_tree(html: str) -> list[dict]:
    """
    Extract heading hierarchy from HTML.
    Returns list of {level, text, anchor}.
    """
    try:
        from selectolax.parser import HTMLParser

        parser = HTMLParser(html)
        headings = []
        for tag in parser.css("h1, h2, h3, h4, h5, h6"):
            level = int(tag.tag[1])
            text = tag.text(strip=True)
            if not text:
                continue
            anchor = tag.attributes.get("id", "")
            headings.append({"level": level, "text": text, "anchor": anchor})
        return headings
    except Exception as e:
        logger.debug("Heading extraction failed: %s", e)
        return []


def _extract_code_blocks(markdown: str) -> list[str]:
    """Extract fenced code blocks from markdown."""
    pattern = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
    blocks = pattern.findall(markdown)
    # Also catch indented code blocks (4 spaces)
    indent_pattern = re.compile(r"(?:(?:^    .+\n?)+)", re.MULTILINE)
    indent_blocks = [m.group(0).strip() for m in indent_pattern.finditer(markdown)]
    return blocks + indent_blocks
