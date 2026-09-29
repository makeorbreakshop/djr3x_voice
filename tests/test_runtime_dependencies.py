import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_textual_dashboard_dependency_is_declared() -> None:
    """The main module imports the Textual dashboard during every startup."""
    requirements = (ROOT / "cantina_os" / "requirements.txt").read_text()

    assert re.search(r"^textual(?:\[.*\])?\s*[<>=!~]", requirements, re.MULTILINE)


def test_semantic_music_runtime_dependencies_are_declared() -> None:
    requirements = (ROOT / "cantina_os" / "requirements.txt").read_text()

    for package in ("torch", "transformers", "librosa"):
        assert re.search(
            rf"^{package}(?:\[.*\])?\s*[<>=!~]",
            requirements,
            re.MULTILINE,
        ), f"{package} is required by local CLAP music search"
