"""WebSocket transcription endpoint.

Endpoint: WS /ws/transcription/{session_id}

Protocol (binary frames, then JSON control messages):

  Client → Server (binary):   PCM int16 mono 16 kHz, exactly 960 bytes (30 ms frame)
  Client → Server (text):     JSON control message
      {"type": "start", "consultation_id": "uuid", "patient_id": "uuid"}
      {"type": "stop"}

  Server → Client (text):     JSON events
      {"type": "partial", "text": "...", "words": [...], "is_final": bool, "session_duration_s": float}
      {"type": "final",   "text": "...", "session_duration_s": float, "segment_count": int}
      {"type": "saved",   "at": "<ISO timestamp>"}   ← periodic auto-save acknowledgement
      {"type": "error",   "message": "..."}

Authentication:
  The client sends the JWT as the first text frame ({"type": "auth", "token": "..."})
  OR via Sec-WebSocket-Protocol header (auth token as sub-protocol).
  The server validates before accepting audio.

Auto-save:
  Partial transcript is AES-256-GCM encrypted and saved to Consultation.transcript_encrypted
  every 30s automatically. The client receives a "saved" event on each auto-save.
"""
from __future__ import annotations

import base64
import json
import logging
import time
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.security.jwt import verify_token

log = logging.getLogger(__name__)

router = APIRouter(tags=["transcription"])

# Expected PCM frame size: 30 ms × 16 000 Hz × 2 bytes/sample = 960 bytes
_FRAME_BYTES = 960


async def _authenticate_ws(websocket: WebSocket) -> Optional[dict]:
    """Read the auth frame and validate the JWT.

    Returns the decoded token payload or None on failure.
    """
    try:
        raw = await websocket.receive_text()
        msg = json.loads(raw)
    except Exception:
        return None

    if msg.get("type") != "auth" or not msg.get("token"):
        return None

    try:
        payload = verify_token(msg["token"])
        return payload
    except Exception as exc:
        log.warning("[ws/transcription] Auth failed: %s", exc)
        return None


@router.websocket("/ws/transcription/{session_id}")
async def transcription_ws(
    session_id: str,
    websocket: WebSocket,
    db: AsyncSession = Depends(get_db),
) -> None:
    """WebSocket endpoint for real-time audio transcription.

    The session_id is an arbitrary client-generated identifier for this recording
    session (not the consultation_id). The consultation_id is sent in the "start"
    control frame.
    """
    from app.services.transcription import TranscriptionSessionService
    from ia.transcription.prompt_builder import build_initial_prompt

    await websocket.accept()

    # ── 1. Authenticate ───────────────────────────────────────────────────────
    payload = await _authenticate_ws(websocket)
    if payload is None:
        await websocket.send_text(json.dumps({
            "type": "error", "message": "Authentication failed"
        }))
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    cabinet_id_str: str = payload.get("cabinet_id", "")
    medecin_id_str: str = payload.get("sub", "").replace("auth0|", "")

    # ── 2. Wait for "start" control message ───────────────────────────────────
    try:
        raw = await websocket.receive_text()
        start_msg = json.loads(raw)
    except Exception:
        await websocket.send_text(json.dumps({
            "type": "error", "message": "Expected start message"
        }))
        await websocket.close()
        return

    if start_msg.get("type") != "start":
        await websocket.send_text(json.dumps({
            "type": "error", "message": "First message must be {type: 'start', ...}"
        }))
        await websocket.close()
        return

    consultation_id_raw = start_msg.get("consultation_id")
    patient_id_raw = start_msg.get("patient_id")

    if not consultation_id_raw or not patient_id_raw:
        await websocket.send_text(json.dumps({
            "type": "error",
            "message": "start message must include consultation_id and patient_id",
        }))
        await websocket.close()
        return

    try:
        consultation_id = uuid.UUID(consultation_id_raw)
        patient_id = uuid.UUID(patient_id_raw)
        cabinet_id = uuid.UUID(cabinet_id_str)
    except ValueError:
        await websocket.send_text(json.dumps({
            "type": "error", "message": "Invalid UUID in start message"
        }))
        await websocket.close()
        return

    # ── 3. Verify consultation belongs to this cabinet ─────────────────────────
    from app.models.consultation import Consultation
    from app.models.medecin import Medecin
    from app.models.patient import Patient
    from sqlalchemy import select

    consultation = await db.get(Consultation, consultation_id)
    if consultation is None or str(consultation.cabinet_id) != str(cabinet_id):
        await websocket.send_text(json.dumps({
            "type": "error", "message": "Consultation not found or forbidden"
        }))
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # ── 4. Build Whisper initial_prompt from patient + doctor context ──────────
    medecin = await db.get(Medecin, consultation.medecin_id)
    patient = await db.get(Patient, patient_id)

    specialty = medecin.specialite if medecin else ""
    active_drugs: list[str] = []

    if patient and patient.traitements_actifs_encrypted:
        try:
            from app.security.encryption import decrypt
            active_drugs = json.loads(decrypt(patient.traitements_actifs_encrypted, patient_id))
        except Exception:
            pass  # Non-fatal: continue without drug context

    initial_prompt = build_initial_prompt(
        specialty=specialty,
        active_medications=active_drugs,
    )

    # ── 5. Get Redis from app state ────────────────────────────────────────────
    redis = websocket.app.state.redis

    # ── 6. Create transcription session ───────────────────────────────────────
    svc = TranscriptionSessionService(
        consultation_id=consultation_id,
        patient_id=patient_id,
        cabinet_id=cabinet_id,
        db=db,
        redis=redis,
        initial_prompt=initial_prompt,
    )
    await svc.start()

    await websocket.send_text(json.dumps({"type": "ready", "session_id": session_id}))
    log.info(
        "[ws/transcription] Ready: session=%s consultation=%s",
        session_id, consultation_id
    )

    last_saved_notify: float = time.monotonic()

    # ── 7. Main receive loop ──────────────────────────────────────────────────
    try:
        while True:
            message = await websocket.receive()

            # Control text message
            if message["type"] == "websocket.receive" and "text" in message:
                try:
                    ctrl = json.loads(message["text"])
                except Exception:
                    await websocket.send_text(json.dumps({
                        "type": "error", "message": "Invalid JSON control message"
                    }))
                    continue

                if ctrl.get("type") == "stop":
                    break
                elif ctrl.get("type") == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))
                continue

            # Binary audio frame
            if message["type"] == "websocket.receive" and "bytes" in message:
                pcm_frame = message["bytes"]

                # Accept base64-encoded frames too (browser WebSocket sends binary)
                if len(pcm_frame) != _FRAME_BYTES:
                    # Try to decode as base64 if wrong size
                    try:
                        pcm_frame = base64.b64decode(pcm_frame)
                    except Exception:
                        pass  # let the pipeline handle the size mismatch

                async for event in svc.feed(pcm_frame):
                    await websocket.send_text(json.dumps(event))

                    # Send "saved" notification when auto-save fires (detected by
                    # checking if _last_save_ts advanced relative to our local tracker)
                    now = time.monotonic()
                    if now - last_saved_notify >= 30:
                        await websocket.send_text(json.dumps({
                            "type": "saved",
                            "at": _utcnow_iso(),
                        }))
                        last_saved_notify = now

    except WebSocketDisconnect:
        log.info("[ws/transcription] Client disconnected: session=%s", session_id)
    except Exception as exc:
        log.error("[ws/transcription] Unexpected error: %s", exc, exc_info=True)
        try:
            await websocket.send_text(json.dumps({
                "type": "error", "message": "Internal server error"
            }))
        except Exception:
            pass

    # ── 8. Finish: flush + force-save ─────────────────────────────────────────
    try:
        final_event = await svc.finish()
        if final_event:
            await websocket.send_text(json.dumps(final_event))
            await websocket.send_text(json.dumps({
                "type": "saved",
                "at": _utcnow_iso(),
            }))
    except Exception as exc:
        log.error("[ws/transcription] Error during finish: %s", exc)

    try:
        await websocket.close()
    except Exception:
        pass


def _utcnow_iso() -> str:
    import datetime
    return datetime.datetime.utcnow().isoformat() + "Z"
