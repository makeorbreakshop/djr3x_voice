import cantina_os.main as main_module


class CapturingClaudeService:
    def __init__(self, event_bus, config, logger, memory_service) -> None:
        self.config = config


def test_claude_model_from_environment_reaches_runtime_config(monkeypatch) -> None:
    monkeypatch.setenv("CLAUDE_MODEL", "claude-sonnet-5")
    monkeypatch.setattr(main_module, "ClaudeService", CapturingClaudeService)

    app = main_module.CantinaOS()
    app._services["memory_service"] = object()
    service = app._create_service("claude")

    assert app._config["CLAUDE_MODEL"] == "claude-sonnet-5"
    assert service.config["CLAUDE_MODEL"] == "claude-sonnet-5"
