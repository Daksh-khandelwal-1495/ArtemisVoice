import pytest

from app.schemas.providers import BotConfig
from app.voice.providers import MissingProviderKey, ProviderKeys, build_services, check_keys

ALL = ProviderKeys(deepgram="dg", soniox="sx", openai="oa")


def cfg(**kw):
    return BotConfig.model_validate(kw)


def test_missing_keys_lists_env_vars():
    with pytest.raises(MissingProviderKey) as ei:
        check_keys(cfg(), ProviderKeys())
    e = ei.value
    assert e.env_keys == ["DEEPGRAM_API_KEY", "OPENAI_API_KEY", "SONIOX_API_KEY"]
    assert {m["role"] for m in e.missing} == {"stt", "llm", "tts"}
    assert "DEEPGRAM_API_KEY" in str(e)


def test_missing_single_key():
    with pytest.raises(MissingProviderKey) as ei:
        check_keys(cfg(tts={"provider": "openai", "model": "gpt-4o-mini-tts", "voice": "nova"}),
                   ProviderKeys(deepgram="x", openai=""))
    assert ei.value.env_keys == ["OPENAI_API_KEY"]
    check_keys(cfg(), ALL)  # no raise


def test_default_stack():
    s = build_services(cfg(stt={"keywords": ["Nexon", "Harrier"]}, tts={"speed": 1.5}), ALL)
    assert type(s.stt).__name__ == "DeepgramSTTService"
    assert s.stt._settings.keyterm == ["Nexon", "Harrier"]  # nova-3 -> keyterm prompting
    assert s.stt._settings.language == "en-IN"
    assert type(s.llm).__name__ == "OpenAILLMService"
    assert s.llm._settings.model == "gpt-6-luna" and s.llm._settings.temperature == 0.4
    assert s.llm._settings.extra == {"reasoning_effort": "none"}
    assert s.llm._settings.max_completion_tokens == 300
    assert type(s.tts).__name__ == "SonioxTTSService"
    assert s.tts._settings.voice == "Bryce" and s.tts._settings.speed == 1.3  # clamped to Soniox range


def test_deepgram_nova2_keywords():
    s = build_services(cfg(stt={"model": "nova-2", "keywords": ["Seltos"]}), ALL)
    assert s.stt._settings.keywords == ["Seltos:2"]


def test_soniox_stt_and_deepgram_tts():
    s = build_services(cfg(stt={"provider": "soniox", "model": "stt-rt-v5", "language": "hi", "keywords": ["DCB"]},
                           tts={"provider": "deepgram", "model": "aura-2", "voice": "aura-2-thalia-en", "speed": 0.9}), ALL)
    assert [str(x.value) for x in s.stt._settings.language_hints] == ["hi"]
    assert s.stt._settings.context.terms == ["DCB"]
    assert s.tts._settings.voice == "aura-2-thalia-en" and s.tts._settings.speed == 0.9


def test_openai_stt_tts_and_llm_base_url():
    s = build_services(cfg(stt={"provider": "openai", "model": "gpt-4o-transcribe", "language": "en", "keywords": ["Kia"]},
                           llm={"model": "gpt-5-mini", "base_url": "http://localhost:1234/v1", "max_tokens": 200},
                           tts={"provider": "openai", "model": "gpt-4o-mini-tts", "voice": "coral", "speed": 1.1}), ALL)
    assert "Kia" in s.stt._settings.prompt
    assert s.tts._settings.voice == "coral" and s.tts._settings.speed == 1.1
    assert s.llm._settings.max_completion_tokens == 1024  # reasoning model: no temperature/max_tokens
    assert "localhost:1234" in str(s.llm._client.base_url)
