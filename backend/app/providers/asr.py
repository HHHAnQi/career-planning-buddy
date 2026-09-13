"""ASR Provider protocol for bounded, single-answer transcription."""

import io
import wave
from typing import Protocol

import httpx
from pydantic import Field

from app.core.config import Settings
from app.schemas.base import StrictModel


class ASRSegment(StrictModel):
    text: str = Field(min_length=1, max_length=1000)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0)


class ASRResult(StrictModel):
    transcript: str = Field(min_length=1, max_length=10_000)
    duration_seconds: float | None = Field(default=None, gt=0)
    segments: list[ASRSegment] = Field(default_factory=list, max_length=500)
    confidence: float | None = Field(default=None, ge=0, le=1)
    timestamps_reliable: bool = False


class ASRProvider(Protocol):
    async def transcribe(
        self,
        *,
        audio: bytes,
        media_type: str,
        filename: str,
        hint_text: str | None,
    ) -> ASRResult: ...

    async def aclose(self) -> None: ...


class MockASRProvider:
    async def transcribe(
        self,
        *,
        audio: bytes,
        media_type: str,
        filename: str,
        hint_text: str | None,
    ) -> ASRResult:
        del filename
        transcript = (hint_text or "这是一次用于验证语音回答流程的模拟面试回答。")[:10_000]
        duration = _wav_duration(audio) if media_type in {"audio/wav", "audio/x-wav"} else None
        segments = []
        if duration is not None:
            segments = [ASRSegment(text=transcript, start_seconds=0, end_seconds=duration)]
        return ASRResult(
            transcript=transcript,
            duration_seconds=duration,
            segments=segments,
            confidence=1.0,
            timestamps_reliable=duration is not None,
        )

    async def aclose(self) -> None:
        return None


class OpenAICompatibleASRProvider:
    def __init__(self, settings: Settings) -> None:
        assert settings.asr_api_key is not None and settings.asr_base_url is not None
        self._model = settings.asr_model
        self._transcriptions_url = (
            f"{str(settings.asr_base_url).rstrip('/')}/audio/transcriptions"
        )
        self._client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {settings.asr_api_key.get_secret_value()}"},
            timeout=settings.asr_timeout_seconds,
        )

    async def transcribe(
        self,
        *,
        audio: bytes,
        media_type: str,
        filename: str,
        hint_text: str | None,
    ) -> ASRResult:
        del hint_text
        response = await self._client.post(
            self._transcriptions_url,
            data={"model": self._model, "response_format": "verbose_json"},
            files={"file": (filename, audio, media_type)},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("ASR response must be an object")
        raw_segments = payload.get("segments", [])
        segments = [
            ASRSegment(
                text=str(item.get("text", "")).strip(),
                start_seconds=float(item.get("start", 0)),
                end_seconds=float(item.get("end", 0)),
            )
            for item in raw_segments
            if isinstance(item, dict)
            and str(item.get("text", "")).strip()
            and isinstance(item.get("start"), (int, float))
            and isinstance(item.get("end"), (int, float))
            and float(item["end"]) >= float(item["start"])
        ]
        duration_value = payload.get("duration")
        duration = float(duration_value) if isinstance(duration_value, (int, float)) else None
        confidence_value = payload.get("confidence")
        confidence = (
            float(confidence_value) if isinstance(confidence_value, (int, float)) else None
        )
        return ASRResult(
            transcript=str(payload.get("text", "")).strip(),
            duration_seconds=duration,
            segments=segments,
            confidence=confidence,
            timestamps_reliable=bool(segments and duration),
        )

    async def aclose(self) -> None:
        await self._client.aclose()


def build_asr_provider(settings: Settings) -> ASRProvider:
    if settings.asr_provider == "mock":
        return MockASRProvider()
    return OpenAICompatibleASRProvider(settings)


def _wav_duration(audio: bytes) -> float | None:
    try:
        with wave.open(io.BytesIO(audio), "rb") as stream:
            rate = stream.getframerate()
            if rate <= 0:
                return None
            return stream.getnframes() / rate
    except (EOFError, wave.Error):
        return None
