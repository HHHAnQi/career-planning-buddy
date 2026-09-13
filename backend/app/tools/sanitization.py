"""Intake sanitization for untrusted tool content (G-layer hardening).

Applies to third-party content entering the evidence pipeline (web-search
snippets, shared knowledge atoms) and any other untrusted tool output:

1. HTML comments are removed (a classic channel for hidden instructions).
2. Known prompt-injection phrasings are neutralized in place — replaced by
   ``[filtered-instruction]`` — so surrounding legitimate content stays
   usable instead of dropping the whole item.
3. Fake renderer section tags (``</evidence_catalog>``,
   ``<critical_constraints>`` …) have their angle brackets replaced with
   guillemets so they cannot pose as prompt structure even before the
   context renderer's HTML escaping runs.
4. Control characters, ``<script>`` blocks, and whitespace collapsing
   (the previous ``_clean`` behavior) are preserved.

``app.agent.resume_context_selection`` keeps its own frozen four-pattern
set: its drop-based filter is part of the resume agent's deterministic
eval contract (``resume-context-rrf-mmr-v2``), and this module's extended
set must not silently change those fixtures.
"""

from __future__ import annotations

import re

CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
SCRIPT_BLOCK = re.compile(r"(?is)<script\b[^>]*>.*?</script>")
HTML_COMMENT = re.compile(r"(?s)<!--.*?-->")
SECTION_TAG = re.compile(r"</?([a-z_]+)>")

# Section names rendered by app.prompts.context_renderer._section —
# impersonating these is the structural injection vector.
KNOWN_SECTION_TAGS = frozenset(
    {
        "user_request",
        "user_profile",
        "planning_window",
        "source_plan",
        "recent_execution",
        "history_summary",
        "retrieved_memories",
        "evidence_catalog",
        "critical_constraints",
    }
)

INJECTION_MARKER = "[filtered-instruction]"

_INJECTION_PATTERNS = (
    re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions", re.I),
    re.compile(r"disregard\s+(all\s+)?(previous|prior|above)", re.I),
    re.compile(r"override\s+(the\s+)?(system|previous|prior)\s+(prompt|instructions)", re.I),
    re.compile(r"system\s*prompt", re.I),
    re.compile(r"reveal\s+(your|the)\s+(system|instructions)", re.I),
    re.compile(r"you\s+are\s+now\s+a\b", re.I),
    re.compile(r"忽略.{0,8}(指令|要求|规则|上文)"),
    re.compile(r"无视.{0,8}(指令|要求|规则)"),
    re.compile(r"你现在是.{0,20}(助手|系统|角色)"),
    re.compile(r"泄露.{0,6}(系统|提示)"),
)


def neutralize_prompt_injection(value: str) -> str:
    """Replace known injection phrasings with an explicit marker."""

    for pattern in _INJECTION_PATTERNS:
        value = pattern.sub(INJECTION_MARKER, value)
    return value


def neutralize_section_tags(value: str) -> str:
    """Break fake renderer section tags while leaving other markup intact."""

    def _replace(match: re.Match[str]) -> str:
        if match.group(1) in KNOWN_SECTION_TAGS:
            return match.group(0).replace("<", "‹").replace(">", "›")
        return match.group(0)

    return SECTION_TAG.sub(_replace, value)


def sanitize_untrusted_text(value: str, limit: int) -> str:
    """Full intake pipeline for untrusted tool content."""

    cleaned = HTML_COMMENT.sub(" ", value)
    cleaned = SCRIPT_BLOCK.sub("", cleaned)
    cleaned = neutralize_prompt_injection(cleaned)
    cleaned = neutralize_section_tags(cleaned)
    cleaned = CONTROL_CHARACTERS.sub("", cleaned)
    cleaned = " ".join(cleaned.split())
    return cleaned[:limit]
