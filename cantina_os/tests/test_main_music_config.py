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


def test_jev_environment_survives_final_config_assembly(monkeypatch) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", "jev-secret")
    monkeypatch.setenv("JEV_CONFIDENCE_THRESHOLD", "0.91")
    monkeypatch.setenv("JEV_COMMAND_THRESHOLD", "0.61")
    monkeypatch.setenv("JEV_TIMEOUT_S", "1.1")
    monkeypatch.setenv("JEV_SPECULATE", "false")

    app = main_module.CantinaOS()

    assert app._config["TYPESAFE_API_KEY"] == "jev-secret"
    assert app._config["JEV_CONFIDENCE_THRESHOLD"] == 0.91
    assert app._config["JEV_COMMAND_THRESHOLD"] == 0.61
    assert app._config["JEV_TIMEOUT_S"] == 1.1
    assert app._config["JEV_SPECULATE"] is False
