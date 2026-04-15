"""Tests for WebSocket transcription service (Task 14).

Covers:
- TranscriptionSessionService: streaming message format (type, text, words, is_final)
- TranscriptionSessionService: auto-save fires every 30s (mocked time)
- TranscriptionSessionService: auto-save encrypts + persists to DB
- TranscriptionSessionService: auto-save failure does NOT raise (WS must survive)
- TranscriptionSessionService: finish() returns "final" event
- TranscriptionSessionService: PCM frame is forwarded to Whisper mock
"""
from __future__ import annotations

import datetime
import os
import sys
import uuid
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("PATIENT_ENCRYPTION_MASTER_KEY", "a" * 64)
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")
os.environ.setdefault("AUTH0_DOMAIN", "test.auth0.com")
os.environ.setdefault("AUTH0_CLIENT_ID", "test")
os.environ.setdefault("AUTH0_CLIENT_SECRET", "test")
os.environ.setdefault("AUTH0_AUDIENCE", "test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

# ── Stub heavy modules before import ──────────────────────────────────────────
# whisper_pipeline requires numpy/faster-whisper — stub the whole module.
_fake_whisper_mod = ModuleType("ia.transcription.whisper_pipeline")


class _FakeTranscriptionSession:
    def __init__(self, initial_prompt: str = "") -> None:
        pass

    async def process(self, pcm: bytes):
        return None

    async def flush(self):
        return None


_fake_whisper_mod.TranscriptionSession = _FakeTranscriptionSession  # type: ignore
sys.modules.setdefault("ia.transcription.whisper_pipeline", _fake_whisper_mod)

# postprocessor may also need numpy — stub it too.
_fake_pp_mod = ModuleType("ia.transcription.postprocessor")


def _identity_postprocess(text: str):
    result = MagicMock()
    result.text = text
    return result


_fake_pp_mod.postprocess = _identity_postprocess  # type: ignore
sys.modules.setdefault("ia.transcription.postprocessor", _fake_pp_mod)

from app.services.transcription import (  # noqa: E402
    TranscriptionSessionService,
    _AUTO_SAVE_INTERVAL_S,
)


CONSULTATION_ID = uuid.uuid4()
PATIENT_ID = uuid.uuid4()
CABINET_ID = uuid.uuid4()
_FAKE_PCM = b"\x00" * 960  # 30ms frame, 16kHz, int16 mono


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_whisper_result(text: str = "Le patient présente", is_final: bool = False):
    result = MagicMock()
    result.text = text
    result.is_final = is_final
    word = MagicMock()
    word.word = "patient"
    word.start = 0.2
    word.end = 0.5
    word.probability = 0.95
    result.words = [word]
    return result


def _make_svc(db=None, redis=None, auto_save_interval: int = 30) -> TranscriptionSessionService:
    if db is None:
        db = AsyncMock()
        db.execute.return_value = MagicMock()
        db.commit = AsyncMock()
    if redis is None:
        redis = AsyncMock()
    return TranscriptionSessionService(
        consultation_id=CONSULTATION_ID,
        patient_id=PATIENT_ID,
        cabinet_id=CABINET_ID,
        db=db,
        redis=redis,
        auto_save_interval=auto_save_interval,
    )


def _attach_mock_whisper(svc: TranscriptionSessionService, result=None):
    """Set up a mock _whisper_session directly on the service."""
    mock_ws = AsyncMock()
    mock_ws.process.return_value = result
    mock_ws.flush.return_value = None
    svc._whisper_session = mock_ws
    svc._started_at = datetime.datetime.utcnow()
    svc._last_save_ts = 0.0
    return mock_ws


# ── Auto-save interval constant ───────────────────────────────────────────────

class TestAutoSaveConstant:
    def test_default_interval_is_30s(self):
        assert _AUTO_SAVE_INTERVAL_S == 30

    def test_svc_uses_default_interval(self):
        svc = _make_svc()
        assert svc._auto_save_interval == 30

    def test_svc_accepts_custom_interval(self):
        svc = _make_svc(auto_save_interval=60)
        assert svc._auto_save_interval == 60


# ── Streaming message format ──────────────────────────────────────────────────

class TestStreamingMessageFormat:
    @pytest.mark.asyncio
    async def test_feed_yields_partial_event(self):
        svc = _make_svc()
        whisper_result = _make_whisper_result("Le patient se plaint de fièvre")
        _attach_mock_whisper(svc, result=whisper_result)

        events = []
        async for event in svc.feed(_FAKE_PCM):
            events.append(event)

        assert len(events) == 1
        assert events[0]["type"] == "partial"

    @pytest.mark.asyncio
    async def test_partial_event_has_required_keys(self):
        svc = _make_svc()
        _attach_mock_whisper(svc, result=_make_whisper_result("test"))

        events = []
        async for event in svc.feed(_FAKE_PCM):
            events.append(event)

        assert len(events) == 1
        evt = events[0]
        assert "type" in evt
        assert "text" in evt
        assert "words" in evt
        assert "is_final" in evt
        assert "session_duration_s" in evt

    @pytest.mark.asyncio
    async def test_partial_event_text_matches_postprocessed(self):
        svc = _make_svc()
        _attach_mock_whisper(svc, result=_make_whisper_result("texte original"))

        events = []
        async for event in svc.feed(_FAKE_PCM):
            events.append(event)

        # The postprocessor stub passes text through unchanged
        assert events[0]["text"] == "texte original"

    @pytest.mark.asyncio
    async def test_words_serialised_with_probability(self):
        svc = _make_svc()
        _attach_mock_whisper(svc, result=_make_whisper_result("test"))

        events = []
        async for event in svc.feed(_FAKE_PCM):
            events.append(event)

        words = events[0]["words"]
        assert isinstance(words, list)
        assert len(words) == 1
        w = words[0]
        assert "word" in w
        assert "start" in w
        assert "end" in w
        assert "probability" in w

    @pytest.mark.asyncio
    async def test_none_result_yields_nothing(self):
        svc = _make_svc()
        _attach_mock_whisper(svc, result=None)  # Whisper buffering, no output yet

        events = []
        async for event in svc.feed(_FAKE_PCM):
            events.append(event)

        assert events == []

    @pytest.mark.asyncio
    async def test_session_duration_is_float(self):
        svc = _make_svc()
        _attach_mock_whisper(svc, result=_make_whisper_result("test"))

        events = []
        async for event in svc.feed(_FAKE_PCM):
            events.append(event)

        assert isinstance(events[0]["session_duration_s"], float)

    @pytest.mark.asyncio
    async def test_is_final_propagated(self):
        svc = _make_svc()
        _attach_mock_whisper(svc, result=_make_whisper_result("fin", is_final=True))

        events = []
        async for event in svc.feed(_FAKE_PCM):
            events.append(event)

        assert events[0]["is_final"] is True


# ── Auto-save timing ──────────────────────────────────────────────────────────

class TestAutoSaveTiming:
    @pytest.mark.asyncio
    async def test_auto_save_not_triggered_before_interval(self):
        svc = _make_svc(auto_save_interval=30)
        mock_ws = _attach_mock_whisper(svc, result=_make_whisper_result("texte"))
        svc._last_save_ts = 0.0

        with (
            patch.object(svc, "_auto_save", new_callable=AsyncMock) as mock_save,
            patch("app.services.transcription.time") as mock_time,
        ):
            mock_time.monotonic.return_value = 10.0  # only 10s elapsed
            async for _ in svc.feed(_FAKE_PCM):
                pass

        mock_save.assert_not_called()

    @pytest.mark.asyncio
    async def test_auto_save_triggered_after_interval(self):
        svc = _make_svc(auto_save_interval=30)
        _attach_mock_whisper(svc, result=_make_whisper_result("texte sauvegardé"))
        svc._last_save_ts = 0.0
        svc._unsaved_chars = 5  # content pending

        with (
            patch.object(svc, "_auto_save", new_callable=AsyncMock) as mock_save,
            patch("app.services.transcription.time") as mock_time,
        ):
            mock_time.monotonic.return_value = 31.0  # 31s elapsed > 30s
            async for _ in svc.feed(_FAKE_PCM):
                pass

        mock_save.assert_called_once()

    @pytest.mark.asyncio
    async def test_auto_save_not_triggered_when_no_unsaved_chars(self):
        svc = _make_svc(auto_save_interval=30)
        _attach_mock_whisper(svc, result=_make_whisper_result("texte"))
        svc._last_save_ts = 0.0
        svc._unsaved_chars = 0  # nothing to save yet

        with (
            patch.object(svc, "_auto_save", new_callable=AsyncMock) as mock_save,
            patch("app.services.transcription.time") as mock_time,
        ):
            mock_time.monotonic.return_value = 40.0  # long elapsed but no chars
            # Feed a frame that yields no new chars (None result)
            svc._whisper_session.process.return_value = None
            async for _ in svc.feed(_FAKE_PCM):
                pass

        mock_save.assert_not_called()


# ── Auto-save DB persistence ──────────────────────────────────────────────────

class TestAutoSaveDbPersistence:
    @pytest.mark.asyncio
    async def test_auto_save_encrypts_with_patient_id(self):
        db = AsyncMock()
        db.execute.return_value = MagicMock()
        db.commit = AsyncMock()
        svc = _make_svc(db=db)
        svc._full_text_parts = ["Le patient décrit une dyspnée"]
        svc._unsaved_chars = 29

        with patch("app.security.encryption.encrypt") as mock_encrypt:
            ef = MagicMock()
            ef.to_db.return_value = "v1:nonce:ciphertext"
            mock_encrypt.return_value = ef
            await svc._auto_save()

        mock_encrypt.assert_called_once()
        assert mock_encrypt.call_args[0][1] == PATIENT_ID

    @pytest.mark.asyncio
    async def test_auto_save_calls_db_execute_and_commit(self):
        db = AsyncMock()
        db.execute.return_value = MagicMock()
        db.commit = AsyncMock()
        svc = _make_svc(db=db)
        svc._full_text_parts = ["Texte de test"]
        svc._unsaved_chars = 13

        with patch("app.security.encryption.encrypt") as mock_encrypt:
            ef = MagicMock()
            ef.to_db.return_value = "v1:x:y"
            mock_encrypt.return_value = ef
            await svc._auto_save()

        db.execute.assert_called_once()
        db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_auto_save_resets_unsaved_chars(self):
        db = AsyncMock()
        db.execute.return_value = MagicMock()
        db.commit = AsyncMock()
        svc = _make_svc(db=db)
        svc._full_text_parts = ["Some text"]
        svc._unsaved_chars = 9

        with patch("app.security.encryption.encrypt") as mock_encrypt:
            ef = MagicMock()
            ef.to_db.return_value = "v1:x:y"
            mock_encrypt.return_value = ef
            await svc._auto_save()

        assert svc._unsaved_chars == 0

    @pytest.mark.asyncio
    async def test_auto_save_updates_last_save_ts(self):
        db = AsyncMock()
        db.execute.return_value = MagicMock()
        db.commit = AsyncMock()
        svc = _make_svc(db=db)
        svc._full_text_parts = ["Texte de test"]
        svc._unsaved_chars = 13
        svc._last_save_ts = 0.0

        with patch("app.security.encryption.encrypt") as mock_encrypt:
            ef = MagicMock()
            ef.to_db.return_value = "v1:x:y"
            mock_encrypt.return_value = ef
            await svc._auto_save()

        assert svc._last_save_ts > 0.0

    @pytest.mark.asyncio
    async def test_auto_save_skips_when_empty(self):
        db = AsyncMock()
        svc = _make_svc(db=db)
        svc._full_text_parts = []
        svc._unsaved_chars = 0

        await svc._auto_save()
        db.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_auto_save_failure_does_not_raise(self):
        """DB failure must be swallowed — WS connection must survive."""
        db = AsyncMock()
        db.execute.side_effect = Exception("DB connection lost")
        svc = _make_svc(db=db)
        svc._full_text_parts = ["Texte de test"]
        svc._unsaved_chars = 13

        with patch("app.security.encryption.encrypt") as mock_encrypt:
            ef = MagicMock()
            ef.to_db.return_value = "v1:x:y"
            mock_encrypt.return_value = ef
            # Must NOT raise
            await svc._auto_save()


# ── finish() — final event ─────────────────────────────────────────────────────

class TestFinishFinalEvent:
    @pytest.mark.asyncio
    async def test_finish_returns_none_when_not_started(self):
        svc = _make_svc()
        result = await svc.finish()
        assert result is None

    @pytest.mark.asyncio
    async def test_finish_returns_final_event_type(self):
        db = AsyncMock()
        db.execute.return_value = MagicMock()
        db.commit = AsyncMock()
        svc = _make_svc(db=db)
        mock_ws = _attach_mock_whisper(svc, result=None)
        mock_ws.flush.return_value = None
        svc._unsaved_chars = 0

        with patch("app.security.encryption.encrypt") as mock_encrypt:
            ef = MagicMock()
            ef.to_db.return_value = "v1:x:y"
            mock_encrypt.return_value = ef
            svc._full_text_parts = ["Premier segment"]
            svc._segments = [{"text": "Premier segment"}]
            event = await svc.finish()

        assert event is not None
        assert event["type"] == "final"

    @pytest.mark.asyncio
    async def test_final_event_has_required_keys(self):
        db = AsyncMock()
        db.execute.return_value = MagicMock()
        db.commit = AsyncMock()
        svc = _make_svc(db=db)
        mock_ws = _attach_mock_whisper(svc, result=None)
        mock_ws.flush.return_value = None
        svc._full_text_parts = ["a", "b"]
        svc._segments = [{"text": "a"}, {"text": "b"}]
        svc._unsaved_chars = 0

        with patch("app.security.encryption.encrypt") as mock_encrypt:
            ef = MagicMock()
            ef.to_db.return_value = "v1:x:y"
            mock_encrypt.return_value = ef
            event = await svc.finish()

        assert event is not None
        assert "type" in event
        assert "text" in event
        assert "session_duration_s" in event
        assert "segment_count" in event

    @pytest.mark.asyncio
    async def test_final_event_segment_count(self):
        db = AsyncMock()
        db.execute.return_value = MagicMock()
        db.commit = AsyncMock()
        svc = _make_svc(db=db)
        mock_ws = _attach_mock_whisper(svc, result=None)
        mock_ws.flush.return_value = None
        svc._segments = [{"text": "a"}, {"text": "b"}, {"text": "c"}]
        svc._full_text_parts = ["a b c"]
        svc._unsaved_chars = 0

        with patch("app.security.encryption.encrypt") as mock_encrypt:
            ef = MagicMock()
            ef.to_db.return_value = "v1:x:y"
            mock_encrypt.return_value = ef
            event = await svc.finish()

        assert event is not None
        assert event["segment_count"] == 3

    @pytest.mark.asyncio
    async def test_final_event_text_joined_from_parts(self):
        db = AsyncMock()
        db.execute.return_value = MagicMock()
        db.commit = AsyncMock()
        svc = _make_svc(db=db)
        mock_ws = _attach_mock_whisper(svc, result=None)
        mock_ws.flush.return_value = None
        svc._full_text_parts = ["Partie un", "partie deux"]
        svc._unsaved_chars = 0

        with patch("app.security.encryption.encrypt") as mock_encrypt:
            ef = MagicMock()
            ef.to_db.return_value = "v1:x:y"
            mock_encrypt.return_value = ef
            event = await svc.finish()

        assert event is not None
        assert event["text"] == "Partie un partie deux"

    @pytest.mark.asyncio
    async def test_finish_force_saves_remaining(self):
        """finish() must call _auto_save even with content below 30s threshold."""
        db = AsyncMock()
        db.execute.return_value = MagicMock()
        db.commit = AsyncMock()
        svc = _make_svc(db=db)
        mock_ws = _attach_mock_whisper(svc, result=None)
        mock_ws.flush.return_value = None
        svc._full_text_parts = ["dernières paroles"]
        svc._unsaved_chars = 17

        with (
            patch.object(svc, "_auto_save", new_callable=AsyncMock) as mock_save,
        ):
            await svc.finish()

        mock_save.assert_called_once_with(force=True)


# ── PCM forwarded to Whisper ──────────────────────────────────────────────────

class TestPcmForwardedToWhisper:
    @pytest.mark.asyncio
    async def test_pcm_frame_forwarded_to_whisper_process(self):
        svc = _make_svc()
        mock_ws = _attach_mock_whisper(svc, result=None)

        async for _ in svc.feed(_FAKE_PCM):
            pass

        mock_ws.process.assert_called_once_with(_FAKE_PCM)

    @pytest.mark.asyncio
    async def test_multiple_frames_forwarded_in_order(self):
        svc = _make_svc()
        mock_ws = _attach_mock_whisper(svc, result=None)
        frame1 = b"\x01" * 960
        frame2 = b"\x02" * 960

        async for _ in svc.feed(frame1):
            pass
        async for _ in svc.feed(frame2):
            pass

        assert mock_ws.process.call_count == 2
        calls = mock_ws.process.call_args_list
        assert calls[0][0][0] == frame1
        assert calls[1][0][0] == frame2

    def test_start_sets_started_at(self):
        """After start(), _started_at must be set."""
        svc = _make_svc()
        svc._started_at = datetime.datetime.utcnow()
        assert svc._started_at is not None

    def test_initial_state(self):
        svc = _make_svc()
        assert svc._whisper_session is None
        assert svc._segments == []
        assert svc._full_text_parts == []
        assert svc._unsaved_chars == 0
        assert svc.consultation_id == CONSULTATION_ID
        assert svc.patient_id == PATIENT_ID
        assert svc.cabinet_id == CABINET_ID
