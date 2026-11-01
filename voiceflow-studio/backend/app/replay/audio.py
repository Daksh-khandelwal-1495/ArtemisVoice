"""Synthesized speech for simulated sessions.

Simulations script the caller as text, so they have no recorded audio. When asked, each turn's
caller line and bot reply are rendered to WAV with the bot's configured TTS provider (the caller
gets a different voice of the same provider) so the session can be listened to. These artifacts
are labelled ``synthesized`` in the trace; they are never presented as recorded call audio.
Failures return ``None`` and the turn keeps its text artifacts only.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import uuid
import wave
from typing import Awaitable, Callable, Literal

import httpx
from loguru import logger

from app.schemas.providers import TTSConfig

Role = Literal["caller", "bot"]
Synthesizer = Callable[[str, Role], Awaitable[bytes | None]]

SAMPLE_RATE = 24000
SONIOX_TTS_URL = "wss://tts-rt.soniox.com/tts-websocket"

# A contrasting voice for the simulated caller, per provider.
_CALLER_VOICES = {
    "soniox": ("Adrian", "Bryce"),
    "openai": ("alloy", "echo"),
    "deepgram": ("aura-2-apollo-en", "aura-2-thalia-en"),
}


def _wav(pcm: bytes, rate: int = SAMPLE_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def _caller_voice(cfg: TTSConfig) -> str:
    first, second = _CALLER_VOICES.get(cfg.provider, ("", ""))
    return second if cfg.voice == first else first


async def _soniox(text: str, voice: str, cfg: TTSConfig, key: str) -> bytes:
    import websockets

    stream_id = uuid.uuid4().hex
    pcm = bytearray()
    async with websockets.connect(SONIOX_TTS_URL, additional_headers={"Authorization": f"Bearer {key}"}) as ws:
        await ws.send(json.dumps({
            "stream_id": stream_id, "model": cfg.model or "tts-rt-v2", "voice": voice,
            "language": (cfg.language or "en").split("-")[0], "audio_format": "pcm_s16le",
            "sample_rate": SAMPLE_RATE,
        }))
        await ws.send(json.dumps({"stream_id": stream_id, "text": text, "text_end": True}))
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("error_code"):
                raise RuntimeError(f"Soniox TTS error {msg.get('error_code')}: {msg.get('error_message')}")
            if msg.get("audio"):
                pcm += base64.b64decode(msg["audio"])
            if msg.get("terminated") or msg.get("audio_end"):
                break
    return _wav(bytes(pcm))


async def _openai(text: str, voice: str, cfg: TTSConfig, key: str) -> bytes:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            "https://api.openai.com/v1/audio/speech",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": cfg.model or "gpt-4o-mini-tts", "voice": voice, "input": text, "response_format": "wav"},
        )
        r.raise_for_status()
        return r.content


async def _deepgram(text: str, voice: str, cfg: TTSConfig, key: str) -> bytes:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            "https://api.deepgram.com/v1/speak",
            params={"model": voice, "encoding": "linear16", "container": "wav", "sample_rate": SAMPLE_RATE},
            headers={"Authorization": f"Token {key}"},
            json={"text": text},
        )
        r.raise_for_status()
        return r.content


_PROVIDERS = {"soniox": _soniox, "openai": _openai, "deepgram": _deepgram}


def make_synthesizer(cfg: TTSConfig, keys: dict[str, str], caller_voice: str | None = None) -> Synthesizer | None:
    """Synthesizer for the bot's TTS provider, or None when the provider/key is unavailable.

    ``caller_voice`` overrides the contrasting default (e.g. a Hindi voice for a Hinglish caller).
    """
    impl = _PROVIDERS.get(cfg.provider)
    key = (keys.get(cfg.provider) or "").strip()
    if impl is None or not key:
        return None
    bot_voice, caller_voice = cfg.voice, caller_voice or _caller_voice(cfg)

    async def synthesize(text: str, role: Role) -> bytes | None:
        if not text or not text.strip():
            return None
        try:
            # Bounded: a stalled provider must not hold up the simulation. Providers stream at roughly
            # half real time, so the budget scales with the text (~15 spoken chars/s).
            budget = min(60.0, 10.0 + 0.2 * len(text))
            return await asyncio.wait_for(impl(text, caller_voice if role == "caller" else bot_voice, cfg, key), budget)
        except Exception as e:  # noqa: BLE001 - audio is a convenience; the text trace stays authoritative
            logger.warning(f"simulation audio: {cfg.provider} synthesis failed for {role}: {e}")
            return None

    return synthesize
