import cantina_os.main as main_module


class CapturingMusicController:
    def __init__(self, event_bus, config) -> None:
        self.config = config


def test_semantic_music_environment_reaches_the_controller(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_SEMANTIC_MUSIC_SEARCH", "true")
    monkeypatch.setenv("SEMANTIC_MUSIC_DEVICE", "cpu")
    monkeypatch.setenv("SEMANTIC_MUSIC_NEGATIVE_WEIGHT", "0.75")
    monkeypatch.setattr(main_module, "MusicControllerService", CapturingMusicController)

    app = main_module.CantinaOS()
    service = app._create_service("music_controller")

    assert service.config["enable_semantic_search"] is True
    assert service.config["semantic_device"] == "cpu"
    assert service.config["semantic_negative_weight"] == 0.75
