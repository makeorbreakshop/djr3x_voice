# Contributing to DJ R3X Voice

Thank you for your interest in contributing to DJ R3X Voice! This document provides guidelines and instructions for contributing.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [How to Contribute](#how-to-contribute)
- [Pull Request Process](#pull-request-process)
- [Coding Standards](#coding-standards)
- [Testing Requirements](#testing-requirements)
- [Documentation](#documentation)

## Code of Conduct

This project adheres to a [Code of Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code. Please report unacceptable behavior to the project maintainers.

## Getting Started

### Prerequisites

- Python 3.11 or higher
- Git
- Virtual environment tool (venv, conda, etc.)
- Arduino IDE (for hardware components)

### Development Setup

1. **Fork and clone the repository:**
   ```bash
   git clone https://github.com/YOUR_USERNAME/djr3x_voice.git
   cd djr3x_voice
   ```

2. **Create a virtual environment:**
   ```bash
   python3.11 -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   pip install -r cantina_os/requirements.txt
   ```

4. **Set up environment variables:**
   ```bash
   cp env.example .env
   # Edit .env with your API keys
   ```

5. **Verify installation:**
   ```bash
   cd cantina_os
   python -m pytest tests/ -v
   ```

## How to Contribute

### Reporting Bugs

Before creating a bug report:
- Check existing issues to avoid duplicates
- Collect relevant information (OS, Python version, error logs)

When creating a bug report, include:
- Clear, descriptive title
- Steps to reproduce the issue
- Expected vs. actual behavior
- Error messages and stack traces
- Environment details (OS, Python version, hardware)

### Suggesting Features

- Open an issue with the "feature request" label
- Describe the use case and benefits
- Include mockups or examples if applicable

### Code Contributions

1. **Find an issue to work on:**
   - Look for issues labeled `good first issue` or `help wanted`
   - Comment on the issue to claim it

2. **Create a branch:**
   ```bash
   git checkout -b feature/your-feature-name
   # or
   git checkout -b fix/your-bug-fix
   ```

3. **Make your changes following our coding standards**

4. **Write or update tests**

5. **Submit a pull request**

## Pull Request Process

1. **Before submitting:**
   - Run all tests: `pytest tests/ -v`
   - Run linters: `ruff check .` and `black --check .`
   - Update documentation if needed
   - Add yourself to CONTRIBUTORS.md (if exists)

2. **PR requirements:**
   - Clear title describing the change
   - Description of what and why
   - Reference to related issues
   - Screenshots for UI changes
   - Test coverage for new code

3. **Review process:**
   - Maintainers will review within a few days
   - Address feedback promptly
   - PRs need at least one approval to merge

## Coding Standards

### Python Style Guide

We follow PEP 8 with some project-specific conventions:

- **Line length:** 100 characters max
- **Imports:** Use `isort` for organization
- **Formatting:** Use `black` for auto-formatting
- **Type hints:** Required for all public functions
- **Docstrings:** Google-style format

### Example Code Style

```python
from typing import Optional, Dict, Any

from cantina_os.base_service import BaseService
from cantina_os.core.event_topics import EventTopics


class ExampleService(BaseService):
    """
    Brief description of the service.

    This service handles specific functionality within the CantinaOS
    architecture.

    Attributes:
        _config: Service configuration dictionary
    """

    def __init__(self, event_bus, config: Optional[Dict[str, Any]] = None):
        """
        Initialize the service.

        Args:
            event_bus: The event bus for inter-service communication
            config: Optional configuration dictionary
        """
        super().__init__(service_name="example", event_bus=event_bus)
        self._config = config or {}

    async def _start(self) -> None:
        """Start the service and subscribe to events."""
        await self.subscribe(EventTopics.SOME_EVENT, self._handle_event)

    async def _stop(self) -> None:
        """Clean up resources."""
        pass

    async def _handle_event(self, payload: Dict[str, Any]) -> None:
        """
        Handle incoming events.

        Args:
            payload: Event payload dictionary
        """
        self.logger.info(f"Received event: {payload}")
```

### Service Architecture

When creating new services:

1. Inherit from `BaseService`
2. Use event-driven communication (emit/subscribe)
3. Use Pydantic models for payloads
4. Implement `_start()` and `_stop()` lifecycle methods
5. Use the service logger (`self.logger`)

### Commit Messages

Follow conventional commits format:

```
type(scope): brief description

Longer description if needed.

Fixes #123
```

Types: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`

Examples:
- `feat(vision): add face recognition service`
- `fix(audio): resolve ducking timing issue`
- `docs(readme): update installation instructions`

## Testing Requirements

### Test Structure

```
tests/
├── unit/           # Unit tests (mocked dependencies)
├── integration/    # Integration tests (real service interactions)
├── performance/    # Performance benchmarks
└── mocks/          # Shared mock implementations
```

### Writing Tests

```python
import pytest
from unittest.mock import AsyncMock, MagicMock

from cantina_os.services.example_service import ExampleService


@pytest.fixture
def mock_event_bus():
    """Create a mock event bus for testing."""
    bus = MagicMock()
    bus.emit = AsyncMock()
    bus.on = MagicMock()
    return bus


@pytest.mark.asyncio
async def test_service_handles_event(mock_event_bus):
    """Test that service correctly handles incoming events."""
    service = ExampleService(event_bus=mock_event_bus)
    await service.start()

    # Your test assertions here
    assert service.status == ServiceStatus.RUNNING
```

### Running Tests

```bash
# All tests
pytest tests/ -v

# With coverage
pytest tests/ --cov=cantina_os --cov-report=html

# Specific test file
pytest tests/unit/test_example_service.py -v

# Integration tests only
pytest tests/integration/ -v
```

## Documentation

### Where to Add Documentation

- **Code:** Docstrings in modules, classes, and functions
- **Architecture:** `cantina_os/docs/`
- **User guides:** `docs/`
- **API changes:** Update `CLAUDE.md`

### Documentation Style

- Use clear, concise language
- Include code examples
- Keep documentation up to date with code changes
- Add diagrams for complex flows (Mermaid preferred)

## Questions?

- Open a discussion on GitHub
- Check existing documentation in `docs/` and `cantina_os/docs/`
- Review `CLAUDE.md` for architecture details

Thank you for contributing to DJ R3X Voice!
