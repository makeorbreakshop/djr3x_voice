## Description

Brief description of what this PR does.

## Related Issues

Fixes #(issue number)

## Type of Change

- [ ] Bug fix (non-breaking change which fixes an issue)
- [ ] New feature (non-breaking change which adds functionality)
- [ ] Breaking change (fix or feature that would cause existing functionality to not work as expected)
- [ ] Documentation update
- [ ] Refactoring (no functional changes)
- [ ] Performance improvement
- [ ] Test addition or modification

## Changes Made

- Change 1
- Change 2
- Change 3

## Testing

Describe the tests you ran to verify your changes:

- [ ] Unit tests pass (`pytest tests/unit/`)
- [ ] Integration tests pass (`pytest tests/integration/`)
- [ ] Manual testing performed

### Test Details

```bash
# Commands used to test
pytest tests/ -v
```

## Screenshots (if applicable)

Add screenshots for UI changes or visual demonstrations.

## Checklist

- [ ] My code follows the project's coding standards
- [ ] I have added/updated docstrings for new/modified functions
- [ ] I have added/updated tests for my changes
- [ ] I have updated documentation if needed
- [ ] My changes generate no new warnings
- [ ] I have run linters (`ruff`, `black`, `isort`)
- [ ] All existing tests still pass

## Service Architecture (if applicable)

If this PR adds or modifies a service:
- [ ] Service inherits from `BaseService`
- [ ] Uses event-driven communication
- [ ] Implements proper `_start()` and `_stop()` lifecycle
- [ ] Uses Pydantic payloads for events
- [ ] Follows patterns documented in CLAUDE.md

## Additional Notes

Any additional information reviewers should know.
