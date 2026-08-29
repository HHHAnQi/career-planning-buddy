"""Deterministic structure-aware chunking for user documents.

Chunking strategy (in priority order):

1. Split on heading-like lines (Markdown ``#``/``##`` or lines that look
   like section titles) — resume/JD documents are section-structured.
2. Split on blank lines into paragraphs; pack consecutive paragraphs up
   to ``max_chars``.
3. Overlong paragraphs are hard-split on sentence enders (CJK + Latin),
   then on raw character windows as the last resort.

The splitter is fully deterministic: same input, same chunks — a chunking
contract the retrieval evaluation depends on.
"""

from __future__ import annotations

import re
from hashlib import sha256

_SENTENCE_END = re.compile(r"(?<=[。！？!?\.])\s+")
_HEADING = re.compile(r"^\s{0,3}(#{1,6}\s+\S|[^\s，。：:]{2,24}\s*[:：]\s*$)")

MIN_CHUNK_CHARS = 40
DEFAULT_MAX_CHUNK_CHARS = 800


def chunk_document(
    text: str, *, max_chars: int = DEFAULT_MAX_CHUNK_CHARS
) -> list[str]:
    """Split ``text`` into deterministic chunks, each capped at ``max_chars``.

    Sections are hard chunk boundaries: a heading and its content never
    merge with the previous section, because a section is the retrieval
    semantic unit for resume/JD documents.
    """

    if max_chars < MIN_CHUNK_CHARS:
        raise ValueError("max_chars must be at least MIN_CHUNK_CHARS")
    cleaned = text.strip()
    if not cleaned:
        return []
    chunks: list[str] = []
    for section in _split_sections(cleaned):
        paragraphs = [part for part in _split_paragraphs(section) if part]
        chunks.extend(chunk for chunk in _pack(paragraphs, max_chars) if chunk.strip())
    return chunks


def chunk_hash(content: str) -> str:
    return sha256(content.encode("utf-8")).hexdigest()


def _split_sections(text: str) -> list[str]:
    """Split on heading-like lines, keeping the heading with its section."""

    lines = text.splitlines()
    sections: list[str] = []
    current: list[str] = []
    for line in lines:
        if _HEADING.match(line) and current:
            sections.append("\n".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        sections.append("\n".join(current))
    return sections


def _split_paragraphs(section: str) -> list[str]:
    return [part.strip() for part in re.split(r"\n\s*\n", section) if part.strip()]


def _pack(paragraphs: list[str], max_chars: int) -> list[str]:
    chunks: list[str] = []
    buffer: list[str] = []
    buffer_size = 0
    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            if buffer:
                chunks.append("\n\n".join(buffer))
                buffer, buffer_size = [], 0
            chunks.extend(_split_long(paragraph, max_chars))
            continue
        if buffer_size + len(paragraph) + 2 > max_chars and buffer:
            chunks.append("\n\n".join(buffer))
            buffer, buffer_size = [], 0
        buffer.append(paragraph)
        buffer_size += len(paragraph) + 2
    if buffer:
        chunks.append("\n\n".join(buffer))
    return chunks


def _split_long(paragraph: str, max_chars: int) -> list[str]:
    sentences = [part for part in _SENTENCE_END.split(paragraph) if part]
    if len(sentences) <= 1:
        return _window(paragraph, max_chars)
    pieces: list[str] = []
    buffer = ""
    for sentence in sentences:
        if len(sentence) > max_chars:
            if buffer:
                pieces.append(buffer)
                buffer = ""
            pieces.extend(_window(sentence, max_chars))
            continue
        if len(buffer) + len(sentence) + 1 > max_chars and buffer:
            pieces.append(buffer)
            buffer = ""
        buffer = f"{buffer} {sentence}".strip() if buffer else sentence
    if buffer:
        pieces.append(buffer)
    return pieces


def _window(text: str, max_chars: int) -> list[str]:
    return [text[start : start + max_chars] for start in range(0, len(text), max_chars)]
