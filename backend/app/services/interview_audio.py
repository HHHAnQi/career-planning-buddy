"""Bounded single-answer ASR and objective delivery metrics."""

import re
from http import HTTPStatus
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.database import session_transaction
from app.core.exceptions import AppError
from app.providers.asr import ASRProvider, ASRResult
from app.repositories.interviews import InterviewRepository
from app.schemas.interviews import (
    AudioAnalysis,
    AudioSegment,
    InterviewAnswerRequest,
    InterviewRunResponse,
)
from app.services.interviews import InterviewService

ALLOWED_AUDIO_TYPES = frozenset(
    {"audio/wav", "audio/x-wav", "audio/mpeg", "audio/webm", "audio/mp4"}
)


class InterviewAudioService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        provider: ASRProvider,
        interviews: InterviewService,
    ) -> None:
        self._session = session
        self._settings = settings
        self._provider = provider
        self._interviews = interviews
        self._repo = InterviewRepository(session)

    async def submit(
        self,
        *,
        interview_id: UUID,
        user_id: UUID,
        turn_id: UUID,
        version: int,
        audio: bytes,
        media_type: str,
        filename: str,
        fallback_text: str | None,
        idempotency_key: str,
    ) -> InterviewRunResponse:
        if media_type not in ALLOWED_AUDIO_TYPES:
            raise _audio_error("AUDIO_FORMAT_UNSUPPORTED", "unsupported audio format")
        if not audio or len(audio) > self._settings.asr_max_audio_bytes:
            raise _audio_error("AUDIO_SIZE_INVALID", "audio exceeds the configured size limit")
        try:
            result = await self._provider.transcribe(
                audio=audio,
                media_type=media_type,
                filename=filename,
                hint_text=fallback_text,
            )
            _validate_duration(result, self._settings.asr_max_duration_seconds)
            transcript = result.transcript.strip()
            analysis = _analysis(result)
        except AppError:
            raise
        except Exception as exc:
            if not fallback_text or not fallback_text.strip():
                raise _audio_error(
                    "ASR_FAILED",
                    "speech recognition failed; provide fallback_text to preserve a text answer",
                    status=HTTPStatus.BAD_GATEWAY,
                ) from exc
            transcript = fallback_text.strip()
            analysis = AudioAnalysis(
                transcript=transcript,
                filler_count=_filler_count(transcript),
                repeated_phrase_count=_repeat_count(transcript),
                timestamps_reliable=False,
                limitations=[
                    "ASR 失败，已保存用户提供的文本回答。",
                    "没有可靠时间戳，因此未计算停顿、语速或准备时间。",
                ],
            )
        async with session_transaction(self._session):
            turn = await self._repo.get_turn(turn_id, user_id, for_update=True)
            interview = await self._repo.get_session(interview_id, user_id, for_update=True)
            if turn is None or interview is None or turn.session_id != interview.id:
                raise AppError(
                    code="NOT_FOUND_INTERVIEW_RESOURCE",
                    message="interview turn was not found",
                    status_code=HTTPStatus.NOT_FOUND,
                )
            turn.audio_analysis_json = analysis.model_dump(mode="json")
        return await self._interviews.submit_answer(
            interview_id=interview_id,
            user_id=user_id,
            payload=InterviewAnswerRequest(
                answer_text=transcript, turn_id=turn_id, version=version
            ),
            idempotency_key=idempotency_key,
        )


def _analysis(result: ASRResult) -> AudioAnalysis:
    reliable = (
        result.timestamps_reliable
        and bool(result.segments)
        and result.duration_seconds is not None
    )
    limitations = ["ASR transcript 可能包含识别错误；指标只描述可观察的表达节奏。"]
    rate: float | None = None
    pauses: int | None = None
    preparation: float | None = None
    if reliable:
        speech_seconds = sum(
            max(0.0, item.end_seconds - item.start_seconds) for item in result.segments
        )
        units = len(re.findall(r"[A-Za-z0-9+#.]+|[\u4e00-\u9fff]", result.transcript))
        rate = round(units * 60 / speech_seconds, 1) if speech_seconds > 0 else 0.0
        pauses = sum(
            1
            for previous, current in zip(result.segments, result.segments[1:], strict=False)
            if current.start_seconds - previous.end_seconds >= 2.0
        )
        preparation = round(result.segments[0].start_seconds, 2)
    else:
        limitations.append("ASR 未提供可靠时间戳，未计算停顿、语速或准备时间。")
    return AudioAnalysis(
        transcript=result.transcript,
        segments=(
            [AudioSegment.model_validate(item.model_dump()) for item in result.segments]
            if reliable
            else []
        ),
        duration_seconds=result.duration_seconds,
        effective_words_per_minute=rate,
        long_pause_count=pauses,
        preparation_seconds=preparation,
        filler_count=_filler_count(result.transcript),
        repeated_phrase_count=_repeat_count(result.transcript),
        asr_confidence=result.confidence,
        timestamps_reliable=reliable,
        limitations=limitations,
    )


def _filler_count(text: str) -> int:
    return sum(text.casefold().count(item) for item in ("嗯", "呃", "那个", "就是", "um", "uh"))


def _repeat_count(text: str) -> int:
    tokens = re.findall(r"[A-Za-z0-9+#.]+|[\u4e00-\u9fff]{2,6}", text.casefold())
    return sum(1 for left, right in zip(tokens, tokens[1:], strict=False) if left == right)


def _validate_duration(result: ASRResult, maximum: float) -> None:
    if result.duration_seconds is not None and result.duration_seconds > maximum:
        raise _audio_error("AUDIO_DURATION_INVALID", "audio exceeds the configured duration limit")


def _audio_error(
    code: str,
    message: str,
    *,
    status: int = HTTPStatus.UNPROCESSABLE_ENTITY,
) -> AppError:
    return AppError(code=code, message=message, status_code=status)
