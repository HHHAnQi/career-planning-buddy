"""Deterministic Interview business validators."""

import re
from hashlib import sha256

from app.agent.errors import StructuredOutputError
from app.schemas.interviews import (
    InterviewAnswerCandidate,
    InterviewContext,
    InterviewQuestionCandidate,
    InterviewReport,
)


def question_fingerprint(question: str) -> str:
    normalized = re.sub(r"\s+", " ", question.strip().lower())
    return sha256(normalized.encode()).hexdigest()


def validate_question(
    candidate: InterviewQuestionCandidate, context: InterviewContext
) -> InterviewQuestionCandidate:
    fingerprint = question_fingerprint(candidate.question_text)
    if fingerprint in context.asked_fingerprints:
        raise StructuredOutputError("duplicate interview question")
    allowed = {
        "resume": (str(context.resume_version_id), context.resume_text),
        "job_target": (str(context.job_target_id), context.jd_text),
    }
    if context.current_turn is not None and context.current_turn.answer_text:
        allowed["answer"] = (
            str(context.current_turn.turn_id),
            context.current_turn.answer_text,
        )
    for source in candidate.sources:
        expected = allowed.get(source.kind)
        if expected is None or source.ref != expected[0] or source.excerpt not in expected[1]:
            raise StructuredOutputError("question source is outside visible evidence")
    if candidate.question_type == "followup":
        if context.followup_count >= context.followup_limit:
            raise StructuredOutputError("followup budget exceeded")
        if candidate.parent_turn_id is None or context.current_turn is None:
            raise StructuredOutputError("followup requires a parent turn")
        if context.current_turn.parent_turn_id is not None:
            raise StructuredOutputError("a followup cannot have another followup")
    return candidate


def validate_answer(
    candidate: InterviewAnswerCandidate, context: InterviewContext
) -> InterviewAnswerCandidate:
    valid_turn_ids = {str(item.turn_id) for item in context.recent_turns} | {
        str(item.get("turn_id")) for item in context.earlier_turn_summary
    }
    if context.current_turn is not None:
        valid_turn_ids.add(str(context.current_turn.turn_id))
    for finding in candidate.analysis.factual_findings:
        if any(ref not in valid_turn_ids for ref in finding.evidence_refs):
            raise StructuredOutputError("analysis cites an unavailable turn")
    if candidate.next_question is not None:
        validate_question(candidate.next_question, context)
        if (
            candidate.next_action == "followup"
            and candidate.next_question.question_type != "followup"
        ):
            raise StructuredOutputError("followup action requires a followup question")
    return candidate


def validate_report(report: InterviewReport, context: InterviewContext) -> InterviewReport:
    valid_turn_ids = {
        str(item.turn_id)
        for item in context.recent_turns
        if item.answer_status == "submitted" and item.analysis_status == "ready"
    } | {
        str(item.turn_id)
        for item in [context.current_turn]
        if item is not None
        and item.answer_status == "submitted"
        and item.analysis_status == "ready"
    }
    valid_turn_ids |= {
        value
        for item in context.earlier_turn_summary
        if item.get("answer_status") == "submitted"
        and isinstance((value := item.get("turn_id")), str)
    }
    if not valid_turn_ids:
        raise StructuredOutputError("report requires at least one evidence turn")
    for weakness in report.weaknesses:
        if any(str(turn_id) not in valid_turn_ids for turn_id in weakness.evidence_turn_ids):
            raise StructuredOutputError("report weakness cites an unavailable turn")
    keys = {item.weakness_key for item in report.weaknesses}
    for action in report.recommended_training_actions:
        if any(key not in keys for key in action.source_weakness_keys):
            raise StructuredOutputError("training action cites an unavailable weakness")
    supplied_answers = " ".join(
        item.answer_text or ""
        for item in [*context.recent_turns, context.current_turn]
        if item is not None and item.answer_status == "submitted"
    ).lower()
    if supplied_answers:
        report_text = " ".join(
            [
                report.overall_summary,
                *[item.topic for item in report.weaknesses],
                *report.limitations,
            ]
        ).lower()
        denial_terms = ("missing", "lacked", "failed to address", "did not provide")
        protected_terms = ("metric", "metrics", "p95", "latency", "bottleneck", "performance")
        if any(term in report_text for term in denial_terms):
            contradicted = any(
                term in supplied_answers and term in report_text for term in protected_terms
            )
            numeric_evidence = bool(
                re.search(
                    r"\b\d+(?:\.\d+)?\s*(?:ms|s|sec|seconds?|minutes?|%)\b",
                    supplied_answers,
                )
            )
            if contradicted or (numeric_evidence and "metric" in report_text):
                raise StructuredOutputError("report contradicts supplied answer evidence")
    return report
