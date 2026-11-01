"""Serve trace artifacts: ``GET /api/artifacts/{session_id}/{file}`` (path-traversal safe)."""

from __future__ import annotations

import io
import re
import wave

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response

from app.tracing.artifacts import ArtifactStore, media_type_for, sha256_hex

router = APIRouter(tags=["artifacts"])


def get_store() -> ArtifactStore:
    return ArtifactStore()


@router.get("/artifacts/{session_id}/{file}")
async def get_artifact(session_id: str, file: str) -> FileResponse:
    store = get_store()
    p = store.path(session_id, file)
    if p is None:
        raise HTTPException(status_code=400, detail="Invalid artifact path")
    if not p.is_file():
        raise HTTPException(status_code=404, detail="Artifact not found")
    return FileResponse(
        p,
        media_type=media_type_for(p.name),
        headers={"X-Artifact-SHA256": sha256_hex(p.read_bytes()), "Cache-Control": "private, max-age=3600"},
    )


_TURN_AUDIO = re.compile(r"^turn-(\d{3})-(caller|tts)\.wav$")


@router.get("/sessions/{session_id}/recording.wav")
async def get_recording(session_id: str, gap_ms: int = 350) -> Response:
    """The whole conversation as one WAV: each turn's caller audio then the bot's reply, in order.

    Built from the per-turn audio artifacts (recorded or synthesized); 404 when the session has none.
    """
    store = get_store()
    probe = store.path(session_id, "recording.wav")
    if probe is None:
        raise HTTPException(status_code=400, detail="Invalid session id")
    folder = probe.parent
    parts = sorted(
        (int(m.group(1)), 0 if m.group(2) == "caller" else 1, p)
        for p in (folder.iterdir() if folder.is_dir() else [])
        if (m := _TURN_AUDIO.match(p.name))
    )
    if not parts:
        raise HTTPException(status_code=404, detail="No audio recorded for this session")
    params, frames = None, []
    for _, _, p in parts:
        with wave.open(str(p), "rb") as w:
            cur = (w.getnchannels(), w.getsampwidth(), w.getframerate())
            if params is None:
                params = cur
            elif cur != params:
                continue  # mixed formats are skipped rather than resampled
            frames.append(w.readframes(w.getnframes()))
    channels, width, rate = params
    silence = b"\x00" * (int(rate * gap_ms / 1000) * channels * width)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(width)
        out.setframerate(rate)
        out.writeframes(silence.join(frames))
    return Response(buf.getvalue(), media_type="audio/wav", headers={"Cache-Control": "no-store"})
