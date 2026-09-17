from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def source(relative_path: str) -> str:
    return (ROOT / relative_path).read_text()


def test_api_key_fragments_are_not_logged() -> None:
    sensitive_sources = (
        "archive/legacy_code/rex_talk.py",
        "archive/legacy_code/run_rex.py",
        "archive/experiments/get_new_elevenlabs_key.py",
    )

    for relative_path in sensitive_sources:
        contents = source(relative_path)
        assert "api_key[:" not in contents
        assert "api_key[-" not in contents
        assert "ELEVENLABS_API_KEY[:" not in contents
        assert "ELEVENLABS_API_KEY[-" not in contents
        assert "elevenlabs_key[:" not in contents
        assert "elevenlabs_key[-" not in contents


def test_api_key_helper_does_not_persist_the_secret() -> None:
    contents = source("archive/experiments/get_new_elevenlabs_key.py")

    assert "env.visible" not in contents
    assert "write(new_content)" not in contents


def test_vision_service_uses_secure_temporary_files() -> None:
    contents = source("cantina_os/cantina_os/services/vision_service.py")

    assert "tempfile.mktemp" not in contents
    assert "tempfile.NamedTemporaryFile" in contents


def test_http_handlers_do_not_return_raw_exception_text() -> None:
    vulnerable_handlers = (
        "cantina_os/cantina_os/services/web_bridge_service.py",
        "dj-r3x-bridge/main.py",
    )

    for relative_path in vulnerable_handlers:
        assert '"error": str(e)' not in source(relative_path)
