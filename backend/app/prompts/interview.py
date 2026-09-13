"""Evidence-bounded prompts for Batch 1 interview operations."""

import json
from typing import Any

from pydantic import BaseModel

from app.providers.llm_contracts import LLMMessage
from app.schemas.interviews import (
    InterviewAnswerCandidate,
    InterviewContext,
    InterviewQuestionCandidate,
    InterviewReport,
)

PROMPT_VERSION = "interview-v1"


def interview_messages(operation: str, context: InterviewContext) -> list[LLMMessage]:
    schema = _schema_for(operation)
    operation_rule = (
        "For question generation, do not repeat any question or topic already present "
        "in recent_turns, earlier_turn_summary, or asked_fingerprints. After a skipped "
        "follow-up, move to a different main topic and do not create another follow-up."
        if operation == "question"
        else (
            "For reports, a skipped or unanswered turn is insufficient evidence and "
            "may appear only in limitations; it must never support a weakness. Before "
            "claiming that a metric, number, tool, or implementation detail is missing, "
            "scan every supplied original answer. Do not criticize information that is "
            "already present. Organize conclusions as conclusion, evidence, then action."
            if operation == "report"
            else ""
        )
    )
    if operation == "question" and context.retest_weakness_keys:
        operation_rule += (
            " This is a retest. Prefer retest_weakness_keys and ask a new question "
            "that measures the same topic and dimension as baseline_weaknesses."
        )
    rules = (
        "Use only the supplied resume, job target, questions, and answers. "
        "Never invent experience or technical errors. When evidence is missing, use "
        "insufficient_evidence. Every source excerpt must be copied from its referenced "
        "input. Source ref values are identifiers: use resume_version_id for resume, "
        "job_target_id for job_target, and the parent turn_id for answer. Every report "
        "weakness must cite a supplied turn id. In factual_findings, evidence_refs may "
        "contain only supplied InterviewTurn turn_id values; for claims from the current "
        "answer, cite current_turn.turn_id. Return JSON only."
    )
    return [
        LLMMessage(
            role="system",
            content=(
                f"{rules}\n{operation_rule}\nOperation: {operation}.\n"
                "Return exactly one object matching this JSON Schema; do not add a usage "
                f"field or markdown fences:\n{json.dumps(schema, ensure_ascii=False)}"
            ),
        ),
        LLMMessage(role="user", content=context.model_dump_json()),
    ]


def repair_messages(
    operation: str,
    context: InterviewContext,
    raw_output: object,
    error: str,
) -> list[LLMMessage]:
    schema = _schema_for(operation)
    report_repair_rule = (
        "For report weaknesses, evidence_turn_ids may cite only turns whose "
        "answer_status is submitted and analysis_status is ready. A skipped or "
        "unanswered turn may appear only in limitations and must never support a "
        "weakness. Remove any weakness and linked training action that has no valid "
        "submitted analyzed turn evidence. "
        if operation == "report"
        else ""
    )
    return [
        LLMMessage(
            role="system",
            content=(
                "Repair the JSON to the requested interview schema. Preserve meaning, "
                "use only supplied evidence, and return JSON only. Do not add a usage "
                "field or markdown fences. For source ref, copy the matching UUID from "
                "resume_version_id, job_target_id, or current_turn.turn_id. "
                "For factual_findings evidence_refs, use only supplied InterviewTurn "
                "turn_id values, normally current_turn.turn_id. "
                f"{report_repair_rule}"
                f"JSON Schema:\n"
                f"{json.dumps(schema, ensure_ascii=False)}"
            ),
        ),
        LLMMessage(
            role="user",
            content=(
                f"operation={operation}\nvalidation_error={error[:1000]}\n"
                f"context={context.model_dump_json()}\nraw={str(raw_output)[:12000]}"
            ),
        ),
    ]


def _schema_for(operation: str) -> dict[str, object]:
    schemas: dict[str, type[BaseModel]] = {
        "question": InterviewQuestionCandidate,
        "answer": InterviewAnswerCandidate,
        "report": InterviewReport,
    }
    try:
        schema: dict[str, Any] = schemas[operation].model_json_schema()
        return schema
    except KeyError as exc:
        raise ValueError(f"unsupported interview operation: {operation}") from exc
