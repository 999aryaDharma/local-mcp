"""
Chunker — splits extracted markdown into retrieval-sized chunks.

Algorithm:
1. Split document at heading boundaries (# ## ### etc.)
2. Each heading section = candidate chunk
3. If section > max_tokens: split at paragraph boundary
4. If section < min_tokens: merge with next sibling
5. Assign heading_path based on heading hierarchy (breadcrumb)
6. Generate summary for each chunk
7. Link prev/next chunk IDs
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from docctx.models import Chunk


def estimate_tokens(text: str) -> int:
    """Approximate token count: chars // 4."""
    return max(1, len(text) // 4)


def generate_summary(heading_path: str, content: str) -> str:
    """
    Rule-based summary: [last_2_headings] first_meaningful_sentence.
    Max 150 chars. No LLM call.
    """
    # Get last 2 elements of heading path
    parts = heading_path.split(" > ")
    path_suffix = " > ".join(parts[-2:]) if len(parts) >= 2 else heading_path

    # Find first meaningful sentence from content
    text = content.strip()
    # Remove code blocks from the search for first sentence
    text_no_code = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    text_no_code = re.sub(r"`[^`]+`", "", text_no_code)  # inline code
    text_no_code = text_no_code.strip()

    # Split on sentence boundaries
    sentences = re.split(r"(?<=[.!?])\s+", text_no_code)
    first_sentence = ""
    for s in sentences:
        s = s.strip()
        if len(s) > 10:  # skip very short fragments
            first_sentence = s
            break

    if not first_sentence and text_no_code:
        # Take first 80 chars as fallback
        first_sentence = text_no_code[:80]

    summary = f"[{path_suffix}] {first_sentence}"
    return summary[:150]


@dataclass
class _Section:
    """Intermediate structure during chunking."""
    heading_stack: list[tuple[int, str]]   # [(level, text), ...]
    content_lines: list[str]
    code_blocks: list[str] = field(default_factory=list)

    @property
    def heading_path(self) -> str:
        if not self.heading_stack:
            return "Document"
        return " > ".join(text for _, text in self.heading_stack)

    @property
    def heading_title(self) -> str:
        if not self.heading_stack:
            return ""
        return self.heading_stack[-1][1]

    @property
    def content(self) -> str:
        return "\n".join(self.content_lines).strip()

    @property
    def token_count(self) -> int:
        return estimate_tokens(self.content)


def _split_at_headings(markdown: str) -> list[_Section]:
    """
    Split markdown into sections at heading boundaries.
    Maintains a heading stack to build the breadcrumb path.
    """
    lines = markdown.splitlines()
    sections: list[_Section] = []
    current_lines: list[str] = []
    heading_stack: list[tuple[int, str]] = []
    in_code_block = False
    code_block_lines: list[str] = []
    code_blocks: list[str] = []

    def flush_section():
        if current_lines or heading_stack:
            content = "\n".join(current_lines).strip()
            if content:
                sections.append(
                    _Section(
                        heading_stack=list(heading_stack),
                        content_lines=list(current_lines),
                        code_blocks=list(code_blocks),
                    )
                )

    for line in lines:
        # Track code fences (don't split inside code blocks)
        if line.startswith("```"):
            in_code_block = not in_code_block
            if not in_code_block and code_block_lines:
                code_blocks.append("\n".join(code_block_lines))
                code_block_lines = []
            current_lines.append(line)
            continue

        if in_code_block:
            code_block_lines.append(line)
            current_lines.append(line)
            continue

        # Detect ATX headings
        heading_match = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading_match:
            level = len(heading_match.group(1))
            text = heading_match.group(2).strip()

            # Flush existing section before starting new heading
            flush_section()
            current_lines = []
            code_blocks = []

            # Update heading stack: pop headings of same or deeper level
            heading_stack = [
                (lvl, txt) for lvl, txt in heading_stack if lvl < level
            ]
            heading_stack.append((level, text))
            # Don't add heading line to content — it's part of heading_path
        else:
            current_lines.append(line)

    # Flush the last section
    flush_section()

    return sections


def _split_oversized_section(
    section: _Section, max_tokens: int
) -> list[_Section]:
    """Split a section larger than max_tokens at paragraph boundaries."""
    if section.token_count <= max_tokens:
        return [section]

    paragraphs = re.split(r"\n\n+", section.content)
    chunks: list[_Section] = []
    current_lines: list[str] = []
    current_tokens = 0

    for para in paragraphs:
        para_tokens = estimate_tokens(para)

        if current_tokens + para_tokens > max_tokens and current_lines:
            sub_content = "\n".join(current_lines)
            chunks.append(
                _Section(
                    heading_stack=list(section.heading_stack),
                    content_lines=current_lines,
                    code_blocks=["\n".join(b) for b in re.findall(r"```[^\n]*\n(.*?)```", sub_content, re.DOTALL)],
                )
            )
            current_lines = [para]
            current_tokens = para_tokens
        else:
            current_lines.append(para)
            current_tokens += para_tokens

    if current_lines:
        sub_content = "\n".join(current_lines)
        chunks.append(
            _Section(
                heading_stack=list(section.heading_stack),
                content_lines=current_lines,
                code_blocks=["\n".join(b) for b in re.findall(r"```[^\n]*\n(.*?)```", sub_content, re.DOTALL)],
            )
        )

    return chunks


def chunk_document(
    markdown: str,
    pack_name: str,
    doc_url: str,
    trust_tier: int = 1,
    max_tokens: int = 800,
    min_tokens: int = 80,
    target_tokens: int = 400,
) -> list[Chunk]:
    """
    Main chunking function. Returns a list of Chunk objects with IDs,
    heading paths, summaries, and prev/next links.
    """
    # Step 1: Split at headings
    sections = _split_at_headings(markdown)

    if not sections:
        # Document has no headings — treat whole doc as one chunk
        sections = [
            _Section(
                heading_stack=[],
                content_lines=markdown.splitlines(),
            )
        ]

    # Step 2: Split oversized sections
    split_sections: list[_Section] = []
    for section in sections:
        if section.token_count > max_tokens:
            split_sections.extend(_split_oversized_section(section, max_tokens))
        else:
            split_sections.append(section)

    # Step 3: Merge undersized sections with next sibling
    merged: list[_Section] = []
    i = 0
    while i < len(split_sections):
        s = split_sections[i]
        if s.token_count < min_tokens and i + 1 < len(split_sections):
            # Merge with next
            next_s = split_sections[i + 1]
            merged_content = s.content + "\n\n" + next_s.content
            merged.append(
                _Section(
                    heading_stack=list(s.heading_stack),
                    content_lines=merged_content.splitlines(),
                    code_blocks=s.code_blocks + next_s.code_blocks,
                )
            )
            i += 2  # skip next since we merged it
        else:
            merged.append(s)
            i += 1

    # Remove empty chunks and micro terminal chunks
    MIN_FINAL_TOKENS = 20
    merged = [
        s for i, s in enumerate(merged) 
        if s.content.strip() and (s.token_count >= MIN_FINAL_TOKENS or i < len(merged) - 1 or len(merged) == 1)
    ]

    # Step 4: Build Chunk objects
    chunks: list[Chunk] = []
    for idx, section in enumerate(merged):
        chunk_id = Chunk.make_id(pack_name, doc_url, idx)
        content = section.content
        heading_path = section.heading_path
        heading_title = section.heading_title

        # Extract code content from this section
        code_content = "\n\n".join(section.code_blocks) if section.code_blocks else ""

        # Generate summary
        summary = generate_summary(heading_path, content)

        # Content preview (first 200 chars, no code fences)
        preview_text = re.sub(r"```.*?```", "[code]", content, flags=re.DOTALL)
        content_preview = preview_text[:200].strip()

        chunk = Chunk(
            id=chunk_id,
            pack_name=pack_name,
            doc_url=doc_url,
            heading_path=heading_path,
            heading_title=heading_title,
            content=content,
            summary=summary,
            content_preview=content_preview,
            code_content=code_content,
            token_count=estimate_tokens(content),
            chunk_index=idx,
            trust_tier=trust_tier,
        )
        chunks.append(chunk)

    # Step 5: Link prev/next IDs
    for i, chunk in enumerate(chunks):
        chunk.prev_chunk_id = chunks[i - 1].id if i > 0 else None
        chunk.next_chunk_id = chunks[i + 1].id if i < len(chunks) - 1 else None

    return chunks
