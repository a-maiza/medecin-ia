"""Celery tasks wrapping the Whisper transcription pipeline.

These tasks run in the 'ai' queue, typically on a GPU worker.

Task: transcribe_audio_file
    Input : path to a WAV/PCM file (already saved on shared storage)
    Output: TranscriptionResult dict stored in Celery result backend (Redis)

The WebSocket handler (Task 7) uses TranscriptionSession directly for real-time
streaming — these tasks are for async / batch transcription (e.g. uploaded recordings).
"""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from app.celery_app import celery_app

log = logging.getLogger(__name__)


@celery_app.task(
    name="app.tasks.transcription.transcribe_audio_file",
    bind=True,
    max_retries=2,
    default_retry_delay=10,
    queue="ai",
    acks_late=True,
)
def transcribe_audio_file(
    self,
    audio_path: str,
    initial_prompt: str = "",
    language: str = "fr",
) -> dict[str, Any]:
    """Transcribe a 16 kHz mono WAV/PCM file using faster-whisper large-v3.

    Args:
        audio_path:     Absolute path to the audio file on disk.
        initial_prompt: Medical context prompt (from prompt_builder.py).
        language:       Language code (default 'fr').

    Returns:
        dict with keys: text, words, language, duration_seconds.

    Raises:
        Retries on transient errors (e.g. GPU OOM). Raises after max_retries.
    """
    try:
        from faster_whisper import WhisperModel  # type: ignore[import]
        import numpy as np

        path = Path(audio_path)
        if not path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        log.info("[transcribe] Starting %s (prompt_len=%d)", path.name, len(initial_prompt))

        # Load model singleton via the ia/ module
        from ia.transcription.whisper_pipeline import get_whisper_model
        model = get_whisper_model()

        # Read PCM float32
        import soundfile as sf  # type: ignore[import]
        samples, sr = sf.read(str(path), dtype="float32", always_2d=False)
        if sr != 16_000:
            raise ValueError(f"Expected 16 kHz audio, got {sr} Hz")

        segments, info = model.transcribe(
            samples,
            language=language,
            task="transcribe",
            initial_prompt=initial_prompt or None,
            word_timestamps=True,
            vad_filter=False,
            beam_size=5,
            best_of=5,
            temperature=0.0,
        )

        words = []
        text_parts = []
        for seg in segments:
            text_parts.append(seg.text)
            if seg.words:
                for w in seg.words:
                    words.append({
                        "word": w.word,
                        "start": w.start,
                        "end": w.end,
                        "probability": w.probability,
                    })

        result = {
            "text": "".join(text_parts).strip(),
            "words": words,
            "language": info.language,
            "duration_seconds": info.duration,
        }
        log.info(
            "[transcribe] Done: %d chars, %d words, %.1fs",
            len(result["text"]),
            len(words),
            info.duration,
        )
        return result

    except Exception as exc:
        log.warning("[transcribe] Error: %s — retrying (%d/%d)", exc, self.request.retries, self.max_retries)
        raise self.retry(exc=exc)


@celery_app.task(
    name="app.tasks.transcription.transcribe_and_postprocess",
    bind=True,
    max_retries=2,
    default_retry_delay=10,
    queue="ai",
    acks_late=True,
)
def transcribe_and_postprocess(
    self,
    audio_path: str,
    initial_prompt: str = "",
    language: str = "fr",
) -> dict[str, Any]:
    """Transcribe + run postprocessor (abbreviation expansion + NER).

    Returns:
        dict with keys: text (normalised), raw_text, words, language,
        duration_seconds, entities, corrections_made.
    """
    try:
        from ia.transcription.postprocessor import postprocess

        raw = transcribe_audio_file.run(audio_path, initial_prompt, language)
        processed = postprocess(raw["text"])

        return {
            **raw,
            "raw_text": raw["text"],
            "text": processed.text,
            "entities": [
                {"label": e.label, "text": e.text, "start": e.start, "end": e.end}
                for e in processed.entities
            ],
            "corrections_made": processed.corrections_made,
        }

    except Exception as exc:
        log.warning("[transcribe_postprocess] Error: %s", exc)
        raise self.retry(exc=exc)
