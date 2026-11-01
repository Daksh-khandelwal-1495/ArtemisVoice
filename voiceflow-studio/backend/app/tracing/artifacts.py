"""On-disk, content-addressed-by-checksum artifact store.

Files live at ``<ARTIFACTS_DIR>/<session_id>/<file>`` (default ``backend/artifacts``; a
relative ``ARTIFACTS_DIR`` is resolved against the backend directory) and are referenced as
``artifact://<session_id>/<file>``. Names are sanitised and every path is checked to stay
inside the root, so neither writing nor serving can escape it.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import os
import re
import uuid
import wave
from pathlib import Path
from typing import Any

from app.tracing.schema import Artifact

BACKEND_DIR = Path(__file__).resolve().parents[2]
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def default_artifacts_dir() -> Path:
    raw = os.environ.get("ARTIFACTS_DIR", "").strip()
    if not raw:
        try:  # optional settings field (added by whoever owns config.py)
            from app.core.config import settings

            raw = str(getattr(settings, "artifacts_dir", "") or "").strip()
        except Exception:  # noqa: BLE001
            raw = ""
    p = Path(raw) if raw else BACKEND_DIR / "artifacts"
    return p if p.is_absolute() else (BACKEND_DIR / p)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pcm_to_wav(pcm: bytes, sample_rate: int, channels: int = 1, sample_width: int = 2) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(sample_width)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


def valid_session_id(session_id: Any) -> str | None:
    try:
        return str(uuid.UUID(str(session_id)))
    except (ValueError, TypeError, AttributeError):
        return None


def valid_name(name: str) -> bool:
    return bool(_SAFE_NAME.match(name or "")) and ".." not in name


class ArtifactStore:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root).resolve() if root else default_artifacts_dir().resolve()

    # ------------------------------------------------------------------ paths

    def path(self, session_id: Any, name: str) -> Path | None:
        """Absolute path of an artifact, or None if the id/name is unsafe."""
        sid = valid_session_id(session_id)
        if sid is None or not valid_name(name):
            return None
        p = (self.root / sid / name).resolve()
        try:
            p.relative_to(self.root)
        except ValueError:
            return None
        return p

    @staticmethod
    def uri(session_id: Any, name: str) -> str:
        return f"artifact://{session_id}/{name}"

    def path_for_uri(self, uri: str) -> Path | None:
        if not isinstance(uri, str) or not uri.startswith("artifact://"):
            return None
        rest = uri[len("artifact://"):]
        if rest.count("/") != 1:
            return None
        sid, name = rest.split("/", 1)
        return self.path(sid, name)

    # ------------------------------------------------------------------ io

    def save_sync(self, session_id: Any, name: str, data: bytes, media_type: str) -> Artifact:
        p = self.path(session_id, name)
        if p is None:
            raise ValueError(f"unsafe artifact name: {name!r}")
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, p)
        return Artifact(uri=self.uri(valid_session_id(session_id), name), sha256=sha256_hex(data),
                        media_type=media_type, bytes=len(data))

    async def save(self, session_id: Any, name: str, data: bytes, media_type: str) -> Artifact:
        return await asyncio.to_thread(self.save_sync, session_id, name, bytes(data), media_type)

    def verify(self, art: dict[str, Any]) -> str | None:
        """None if the artifact file exists and matches its checksum; else a problem string."""
        p = self.path_for_uri(art.get("uri", ""))
        if p is None:
            return "has an invalid uri"
        if not p.is_file():
            return "file missing"
        data = p.read_bytes()
        if sha256_hex(data) != art.get("sha256"):
            return "sha256 mismatch"
        return None


def media_type_for(name: str) -> str:
    n = name.lower()
    if n.endswith(".wav"):
        return "audio/wav"
    if n.endswith(".txt"):
        return "text/plain; charset=utf-8"
    if n.endswith(".json"):
        return "application/json"
    return "application/octet-stream"
