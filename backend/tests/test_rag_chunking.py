"""Tests for deterministic structure-aware document chunking.

Pins:
* Headings start new sections and stay with their content.
* Paragraph packing respects max_chars; overlong paragraphs split on
  sentence enders, then raw windows.
* Determinism: same input yields identical chunks and hashes.
* Empty input yields no chunks; undersized max_chars is rejected.
"""

from __future__ import annotations

import pytest

from app.rag.chunking import chunk_document, chunk_hash

RESUME = """# 个人信息
张三，计算机科学本科，求职方向：AI 应用工程师。

# 项目经历
FastAPI 求职规划系统：受控 Agent 状态机，LangGraph 编排，含预算守卫与租约恢复。

# 技能
Python、FastAPI、SQLAlchemy、LangGraph。英语 CET-6。"""

LONG_CN = "负责后端服务的开发与维护。" * 60


def test_headings_start_sections_and_content_is_packed() -> None:
    chunks = chunk_document(RESUME, max_chars=200)
    assert len(chunks) >= 2
    joined = "\n\n".join(chunks)
    # No content loss.
    for fragment in (
        "张三",
        "FastAPI 求职规划系统",
        "预算守卫与租约恢复",
        "LangGraph",
    ):
        assert fragment in joined
    # Heading stays with the first line of its section.
    first = next(chunk for chunk in chunks if "个人信息" in chunk)
    assert "张三" in first
    skills = next(chunk for chunk in chunks if "技能" in chunk)
    assert "CET-6" in skills


def test_every_chunk_respects_max_chars() -> None:
    chunks = chunk_document(RESUME + "\n\n" + LONG_CN, max_chars=200)
    assert all(len(chunk) <= 200 for chunk in chunks)
    assert len(chunks) >= 3


def test_determinism() -> None:
    first = chunk_document(RESUME, max_chars=150)
    second = chunk_document(RESUME, max_chars=150)
    assert first == second
    assert [chunk_hash(chunk) for chunk in first] == [
        chunk_hash(chunk) for chunk in second
    ]


def test_empty_and_invalid_inputs() -> None:
    assert chunk_document("") == []
    assert chunk_document("   \n  ") == []
    with pytest.raises(ValueError):
        chunk_document("x", max_chars=10)


def test_long_paragraph_splits_on_sentence_enders() -> None:
    text = "第一句。第二句。第三句。" * 10
    chunks = chunk_document(text, max_chars=100)
    assert all(len(chunk) <= 100 for chunk in chunks)
    assert len(chunks) >= 2
