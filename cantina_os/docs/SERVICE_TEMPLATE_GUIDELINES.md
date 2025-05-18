# Cantina OS Service Template Guidelines

> **Use this doc verbatim in Cursor's "Prompt to… file" field when you spin
> up a new service.**

---

## 0 · Purpose

A one‑page checklist + rationale that bakes our Architecture Standards and
recurring bug‑fixes directly into day‑zero development.  Follow it and
your service will:

* boot without explosions,
* shut down cleanly,
* never leak tasks or threads,
* talk to the bus with validated payloads,
* and satisfy the automated lint gate.

---

## 1 · Repo Boiler‑plate

```text
cantina_os/
└── cantina_os/
    └── services/
        └── <your_service>/
            ├── __init__.py
            ├── <your_service>.py   ← copy of service_template.py, renamed
            └── tests/
                └── test_<your_service>.py
```

IMPORTANT: Note the nested cantina_os directory structure. Always place services in the inner cantina_os/services/ directory.

---

## 2 · Mandatory steps (check them *all*)

| #  | Step                                                                  | Why it matters                                                         |
| -- | --------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| 1  | **Duplicate & rename** `service_template.py` → `<your_service>.py`    | Keeps imports & CLSID unique (pre‑commit will yell otherwise).         |
| 2  | Rename the class from `ServiceTemplate` → `YourServiceName`           | Duplicate class‑names cause service‑loader collisions.                 |
| 3  | Extend the inner `_Config` Pydantic model with your settings          | Prevents runtime KeyErrors & instantly documents the API.              |
| 4  | Implement `_setup_subscriptions()` using `_subscribe(topic, handler)` | Guards against forgotten `await` & tracks tasks for cleanup.           |
| 5  | Use `_emit_dict()` for *every* event to auto‑dump Pydantic payloads   | Avoids the "object has no attribute get" bug.                          |
| 6  | Store thread callbacks via `run_threadsafe()`                         | Eliminates *"no running loop in thread"* exceptions with audio/serial. |
| 7  | Put long‑running coroutines in `self._tasks`                          | Lets `_stop()` cancel them gracefully.                                 |
| 8  | Flesh out `_stop()` to close hardware, cancel tasks, and unsubscribe  | Prevents resource leaks and hanging processes.                         |
| 9  | Emit statuses via `_emit_status()` (OK, WARN, ERROR)                  | Surfaces problems to monitoring & CLI dashboards.                      |
| 10 | Write tests: **init**, **event flow**, **error path**                 | CI gates for regressions.                                              |

Tick all ten boxes before opening a PR.  The reviewer will copy/paste
this grid and mark ☑️ or ❌.

---

## 3 · Service Initialization Requirements (CRITICAL)

The StandardService base class has specific initialization requirements:

```python
# CORRECT: StandardService requires event_bus as first positional parameter
class MyService(StandardService):
    def __init__(self, event_bus, config=None, name="my_service"):
        super().__init__(event_bus, config, name=name)
        # Service-specific initialization
```

CRITICAL REQUIREMENTS:
- `event_bus` must be the first positional parameter
- `config` must be the second positional parameter
- DO NOT use keyword-only arguments syntax (`*,`) in your `__init__` method
- Always pass event_bus and config to super().__init__
- Your service will not initialize if these requirements are not met

```python
# INCORRECT: This pattern will fail at runtime
class MyService(StandardService):
    def __init__(
        self,
        *,  # DO NOT USE keyword-only arguments syntax
        name: str = "my_service",
        config: Dict[str, Any] | None = None,
    ) -> None:
        super().__init__(name=name)  # WRONG: missing event_bus and config
```

---

## 4 · Event‑bus patterns

* **Emit is sync** – *never* `await self.emit(...)`.  The helper already
  handles this.
* Validation should happen **before** side‑effects; log & `_emit_status` on
  `ValidationError`.
* Keep topic names in the shared `EventTopics` enum; don't invent raw
  strings.

---

## 5 · Thread bridging cheat‑sheet

```python
audio_interface.on_peak(lambda level:  # called from audio thread
    service.run_threadsafe(service.handle_peak(level))
)
```

`run_threadsafe` drops the coroutine back onto the service's main loop.

---

## 6 · Common anti‑patterns to repel

* **Bare `subscribe(...)`** – you'll forget to await, handler never fires.
* **Duplicate class names** – only the last one imported wins.
* **Emitting Pydantic objects raw** – downstream consumers explode.
* **Leaving tasks un‑cancelled** – hangs pytest & dev‑server shutdown.
* **Missing event_bus parameter** – service will fail to initialize.

---

## 7 · Reference links

* `ARCHITECTURE_STANDARDS.md` – §3 Async, §4 Config, §7 New‑Service checklist
* `CANTINA_OS_SYSTEM_ARCHITECTURE.md` – high‑level flow diagrams
* `service_template.py` – living source of truth

---

### Finish line

When every box is ☑️, run `make test && make lint`.  If CI is green, you
are cleared for hyperspace.
