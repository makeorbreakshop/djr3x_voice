"""Which ElevenLabs model R3X speaks with, and which voice_settings each model gets."""

from cantina_os.services.elevenlabs_service import (
    DEFAULT_TTS_MODEL,
    ElevenLabsConfig,
    _takes_speed_and_style,
)


def test_v4_turbo_is_the_default_everywhere():
    assert DEFAULT_TTS_MODEL == "eleven_v4_turbo"
    assert ElevenLabsConfig(api_key="k").model_id == "eleven_v4_turbo"


def test_speed_and_style_only_go_to_models_that_have_them():
    assert _takes_speed_and_style("eleven_flash_v2_5")
    assert _takes_speed_and_style("eleven_turbo_v2_5")
    assert not _takes_speed_and_style("eleven_v4_turbo")
    assert not _takes_speed_and_style("eleven_v4")
    assert not _takes_speed_and_style("eleven_v3")
