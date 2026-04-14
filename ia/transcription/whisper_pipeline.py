"""Faster-Whisper large-v3 transcription pipeline with webrtcvad VAD.

Architecture:
  - WhisperModel is a module-level singleton (loaded once on first use).
  - TranscriptionSession is per-WebSocket-connection (manages VAD state + buffer).

VAD parameters:
  - Frame size:      30 ms (960 bytes at 16 kHz int16 mono)
  - Aggressiveness:  2  (0 = least aggressive, 3 = most aggressive)
  - Silence flush:   15 consecutive silence frames → flush buffer to Whisper (= 450 ms)

Usage (from the WebSocket handler in Task 7):
    session = TranscriptionSession(initial_prompt="...")
    for pcm_chunk in audio_stream:          # 30 ms chunks
        result = await session.process(pcm_chunk)
        if result:                          # flush occurred
            yield result
    final = await session.flush()           # end of stream
    if final:
        yield final
"""
from __future__ import annotations

import asyncio
import logging
import os
import struct
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Optional

import numpy as np
import webrtcvad

log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
_SAMPLE_RATE = 16_000          # Hz — Whisper fixed
_FRAME_MS = 30                 # ms per VAD frame (10 / 20 / 30 supported)
_FRAME_SAMPLES = _SAMPLE_RATE * _FRAME_MS // 1000   # 480
_FRAME_BYTES = _FRAME_SAMPLES * 2                   # 960 (int16, mono)
_VAD_AGGRESSIVENESS = 2
_SILENCE_FLUSH_FRAMES = 15     # 450 ms of silence before flushing to Whisper
_MAX_BUFFER_FRAMES = 300       # ~9 s — force-flush if buffer too large


# ── Data types ────────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class WordInfo:
    word: str
    start: float
    end: float
    probability: float


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    text: str
    words: list[WordInfo]
    language: str
    is_final: bool    # True = flushed by silence; False = force-flushed (end of session)


# ── Global Whisper model singleton ────────────────────────────────────────────

@lru_cache(maxsize=1)
def get_whisper_model():
    """Load and cache the Whisper large-v3 model (runs once at startup)."""
    from faster_whisper import WhisperModel  # type: ignore[import]

    device = os.environ.get("WHISPER_DEVICE", "cpu")
    compute = "float16" if device == "cuda" else "int8"
    model_size = os.environ.get("WHISPER_MODEL_SIZE", "large-v3")

    log.info("Loading Whisper %s on %s (%s)…", model_size, device, compute)
    model = WhisperModel(model_size, device=device, compute_type=compute)
    log.info("Whisper model ready.")
    return model


# ── Per-session state ─────────────────────────────────────────────────────────

class TranscriptionSession:
    """Stateful per-connection transcription session.

    Thread-safety: not thread-safe — use one instance per async task (one per WS connection).
    """

    def __init__(self, initial_prompt: str = "") -> None:
        self._prompt = initial_prompt
        self._vad = webrtcvad.Vad(_VAD_AGGRESSIVENESS)
        self._buffer = bytearray()         # accumulates speech PCM
        self._silence_frames = 0
        self._total_speech_frames = 0
        self._running_text: list[str] = [] # for building rolling prompt

    async def process(self, pcm_30ms: bytes) -> Optional[TranscriptionResult]:
        """Feed a 30 ms PCM chunk. Returns a result if a flush is triggered."""
        if len(pcm_30ms) != _FRAME_BYTES:
            log.warning("Unexpected frame size: %d (expected %d)", len(pcm_30ms), _FRAME_BYTES)
            return None

        is_speech = self._vad.is_speech(pcm_30ms, _SAMPLE_RATE)

        if is_speech:
            self._buffer.extend(pcm_30ms)
            self._total_speech_frames += 1
            self._silence_frames = 0
        else:
            self._silence_frames += 1
            # Keep a small trailing buffer to avoid cutting off word endings
            if len(self._buffer) > 0:
                self._buffer.extend(pcm_30ms)

        # Flush on sustained silence or when buffer grows too large
        if self._silence_frames >= _SILENCE_FLUSH_FRAMES and len(self._buffer) > 0:
            return await self._flush(is_final=True)
        if self._total_speech_frames >= _MAX_BUFFER_FRAMES:
            return await self._flush(is_final=False)

        return None

    async def flush(self) -> Optional[TranscriptionResult]:
        """Force-flush remaining audio at end of session."""
        if not self._buffer:
            return None
        return await self._flush(is_final=False)

    async def _flush(self, *, is_final: bool) -> Optional[TranscriptionResult]:
        if not self._buffer:
            return None

        pcm = bytes(self._buffer)
        self._buffer.clear()
        self._silence_frames = 0
        self._total_speech_frames = 0

        # Run synchronous Whisper in a thread pool to avoid blocking the event loop
        result = await asyncio.get_event_loop().run_in_executor(
            None, self._transcribe_sync, pcm, is_final
        )
        if result and result.text.strip():
            self._running_text.append(result.text.strip())
            # Cap rolling prompt to last 224 tokens (Whisper limit)
            self._prompt = " ".join(self._running_text[-5:])
        return result

    def _transcribe_sync(self, pcm: bytes, is_final: bool) -> Optional[TranscriptionResult]:
        """CPU/GPU-bound Whisper call — runs in executor."""
        model = get_whisper_model()

        # Convert PCM int16 → float32 normalised [-1, 1]
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0

        segments, info = model.transcribe(
            samples,
            language="fr",
            task="transcribe",
            initial_prompt=self._prompt or None,
            word_timestamps=True,
            vad_filter=False,   # We manage VAD ourselves
            beam_size=5,
            best_of=5,
            temperature=0.0,
        )

        words: list[WordInfo] = []
        text_parts: list[str] = []

        for seg in segments:
            text_parts.append(seg.text)
            if seg.words:
                for w in seg.words:
                    words.append(WordInfo(
                        word=w.word,
                        start=w.start,
                        end=w.end,
                        probability=w.probability,
                    ))

        full_text = "".join(text_parts).strip()
        if not full_text:
            return None

        return TranscriptionResult(
            text=full_text,
            words=words,
            language=info.language,
            is_final=is_final,
        )
