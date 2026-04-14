"""Transcription session service: state management for a single WebSocket connection.

Responsibilities:
  - Owns one TranscriptionSession (VAD + Whisper) per connection
  - Accumulates partial transcript in memory
  - Auto-saves encrypted transcript to DB every 30s (configurable)
  - Coordinates Whisper → postprocessor → optional pseudonymisation
  - Returns typed TranscriptionEvent dicts ready for WebSocket serialisation

Usage (from the WebSocket handler):
    svc = TranscriptionSessionService(
        consultation_id=...,
        patient_id=...,
        db=session,
        redis=redis,
        initial_prompt="...",
    )
    await svc.start()
    async for event in svc.feed(pcm_30ms_chunk):
        await ws.send_json(event)
    final_event = await svc.finish()
    if final_event:
        await ws.send_json(final_event)
"""
from __future__ import annotations

import asyncio
import datetime
import json
import logging
import time
import uuid
from typing import AsyncIterator, Optional

from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

_AUTO_SAVE_INTERVAL_S = 30   # seconds between auto-saves


class TranscriptionSessionService:
    """Per-WebSocket-connection transcription state manager."""

    def __init__(
        self,
        consultation_id: uuid.UUID,
        patient_id: uuid.UUID,
        cabinet_id: uuid.UUID,
        db: AsyncSession,
        redis,              # aioredis.Redis
        initial_prompt: str = "",
        auto_save_interval: int = _AUTO_SAVE_INTERVAL_S,
    ) -> None:
        self.consultation_id = consultation_id
        self.patient_id = patient_id
        self.cabinet_id = cabinet_id
        self._db = db
        self._redis = redis
        self._initial_prompt = initial_prompt
        self._auto_save_interval = auto_save_interval

        # Whisper session (lazy-initialised on first audio chunk)
        self._whisper_session = None

        # Accumulated transcript segments
        self._segments: list[dict] = []        # [{text, words, is_final, ts}]
        self._full_text_parts: list[str] = []  # ordered text fragments

        # Auto-save state
        self._last_save_ts: float = 0.0
        self._unsaved_chars: int = 0

        # Session lifecycle
        self._started_at: Optional[datetime.datetime] = None
        self._session_id = f"transcription:{consultation_id}"

    async def start(self) -> None:
        """Initialise the Whisper session. Call once before feeding audio."""
        from ia.transcription.whisper_pipeline import TranscriptionSession
        self._whisper_session = TranscriptionSession(initial_prompt=self._initial_prompt)
        self._started_at = datetime.datetime.utcnow()
        self._last_save_ts = time.monotonic()
        log.info("[transcription] Session started: consultation=%s", self.consultation_id)

    async def feed(self, pcm_30ms: bytes) -> AsyncIterator[dict]:
        """Feed one 30-ms PCM frame. Yields 0 or 1 transcription events.

        Each yielded event has shape:
            {
                "type": "partial",
                "text": "...",
                "words": [{word, start, end, probability}, ...],
                "is_final": bool,
                "session_duration_s": float
            }
        """
        if self._whisper_session is None:
            await self.start()

        result = await self._whisper_session.process(pcm_30ms)
        if result is None:
            return

        # Postprocess: abbreviation expansion + NER
        from ia.transcription.postprocessor import postprocess
        processed = postprocess(result.text)
        clean_text = processed.text

        # Accumulate
        self._full_text_parts.append(clean_text)
        self._unsaved_chars += len(clean_text)

        words_serialised = [
            {
                "word": w.word,
                "start": w.start,
                "end": w.end,
                "probability": w.probability,
            }
            for w in result.words
        ]
        segment = {
            "text": clean_text,
            "words": words_serialised,
            "is_final": result.is_final,
            "ts": time.monotonic() - self._started_at.timestamp() if self._started_at else 0.0,
        }
        self._segments.append(segment)

        # Auto-save if interval elapsed
        now = time.monotonic()
        if now - self._last_save_ts >= self._auto_save_interval and self._unsaved_chars > 0:
            await self._auto_save()

        duration_s = (
            (datetime.datetime.utcnow() - self._started_at).total_seconds()
            if self._started_at else 0.0
        )
        yield {
            "type": "partial",
            "text": clean_text,
            "words": words_serialised,
            "is_final": result.is_final,
            "session_duration_s": round(duration_s, 1),
        }

    async def finish(self) -> Optional[dict]:
        """Flush remaining audio, force-save final transcript, return final event."""
        if self._whisper_session is None:
            return None

        result = await self._whisper_session.flush()
        if result and result.text.strip():
            from ia.transcription.postprocessor import postprocess
            processed = postprocess(result.text)
            clean_text = processed.text
            self._full_text_parts.append(clean_text)
            self._unsaved_chars += len(clean_text)

        # Always save on finish regardless of interval
        if self._unsaved_chars > 0 or result:
            await self._auto_save(force=True)

        full_text = " ".join(self._full_text_parts).strip()
        duration_s = (
            (datetime.datetime.utcnow() - self._started_at).total_seconds()
            if self._started_at else 0.0
        )

        log.info(
            "[transcription] Session finished: consultation=%s, chars=%d, duration=%.1fs",
            self.consultation_id,
            len(full_text),
            duration_s,
        )

        return {
            "type": "final",
            "text": full_text,
            "session_duration_s": round(duration_s, 1),
            "segment_count": len(self._segments),
        }

    # ── Private ────────────────────────────────────────────────────────────────

    async def _auto_save(self, force: bool = False) -> None:
        """Encrypt accumulated transcript and persist to DB."""
        from app.security.encryption import encrypt
        from sqlalchemy import update
        from app.models.consultation import Consultation

        full_text = " ".join(self._full_text_parts).strip()
        if not full_text:
            return

        try:
            encrypted = encrypt(full_text, self.patient_id)
            encrypted_str = encrypted.to_db()

            await self._db.execute(
                update(Consultation)
                .where(Consultation.id == self.consultation_id)
                .values(transcript_encrypted=encrypted_str),
            )
            await self._db.commit()

            self._last_save_ts = time.monotonic()
            self._unsaved_chars = 0
            log.debug(
                "[transcription] Auto-saved %d chars for consultation %s",
                len(full_text),
                self.consultation_id,
            )
        except Exception as exc:
            log.error("[transcription] Auto-save failed: %s", exc)
            # Don't raise — auto-save failure must not crash the WS connection
