---
title: DJ R3X CLI and startup reliability
status: complete
artifact_readiness: implementation-ready
execution: code
---

## Outcome

`dj-r3x` starts with a readable terminal, accepts commands without corrupting stdout, prints
one response and one prompt per command, and degrades optional hardware integrations without
misleading error storms.

## Non-goals and protected behavior

- Keep the event-driven command architecture and existing command grammar.
- Keep detailed diagnostics in the file log while making the interactive console concise.
- Do not fabricate hardware availability, macOS permissions, or Spotify authorization.
- Do not change unrelated dashboard work already present in the worktree.

## Prior Learnings

- The September 17 live run proved that stdin's non-blocking flag can affect the terminal's
  stdout path. Retrying a whole string after a flush-time `BlockingIOError` duplicates content.
- Existing tests model only all-or-nothing writes, so they missed partial/flush-time writes.
- CLI prompt rendering currently has three owners: startup, the input loop, and the response
  handler. The event bus schedules async listeners, so their writes are not ordered.

## Key Decisions

- Reopen the concrete terminal device for the interactive Unix read transport so asyncio may
  make that separate open file description non-blocking without changing stdout.
- Serialize banners, responses, errors, and prompts through the CLI output queue.
- Discover macOS cameras from AVFoundation's enumerated device list, with system profiler as a
  non-probing fallback.
- Treat unavailable optional integrations as degraded capability, not repeated system errors.

## Acceptance Contract

- One `help` input produces exactly one `Available commands:` listing and one following prompt.
- The CLI never sets the original terminal stdin/stdout open file description non-blocking.
- Large output cannot be duplicated by a flush-time EAGAIN retry.
- This Mac selects `Studio Display Camera` without probing nonexistent OpenCV indices.
- A missing configured Arduino port falls back without three connection failures.
- Missing Accessibility permission and stale Spotify auth each produce at most one actionable
  warning; optional face recognition and mock LED control do not warn as failures.
- Both local files named `Utinni` remain playable under unique library keys.
- `q` emits the shutdown topic consumed by the application and exits cleanly.

## Work Units

- [x] Reproduce terminal duplication and prompt ownership failures with outcome-based tests.
- [x] Replace the terminal read/write boundary and make one writer own prompt ordering.
- [x] Add deterministic camera discovery and optional-integration degradation tests and fixes.
- [x] Preserve colliding music tracks with stable unique keys.
- [x] Run focused and broad tests, lint the diff, and exercise `/usr/local/bin/dj-r3x` in a PTY.
- [x] Review the final diff and record the verified operational result.

## Verified Result

- `413 passed, 54 skipped` across `cantina_os/tests` on 2026-09-17.
- Focused import/error lint passed for the newly introduced and terminal-boundary files.
- The real `/usr/local/bin/dj-r3x` launcher printed one help listing and one following prompt,
  then shut down cleanly after `q`.
- Startup selected the Studio Display through AVFoundation enumeration without OpenCV probe
  errors. The only console warnings were the real revoked Spotify authorization and absent
  Arduino hardware.

## Verification Handoff

- Focused pytest files for CLI console, camera discovery, startup hygiene, eye controller, and
  music library behavior.
- Full non-live pytest suite where hardware/API tests are explicitly excluded by repository
  markers or existing skips.
- Real launcher smoke: wait for startup, enter `help`, assert one listing/no output-drop errors,
  then enter `q` and observe clean shutdown.

## Risks and Rollback

- Terminal input changes can affect shutdown. Preserve the existing asyncio pipe transport and
  isolate only its file description; prove both command input and SIGINT/`q` shutdown.
- Camera enumeration formats can vary. Keep a system-profiler fallback and explicit preferred
  index behavior.
- Unique music keys can affect callers. Keep non-colliding title keys unchanged and alter only
  collisions.

## Stop Conditions and Budgets

- Re-plan after two failed fixes at the same terminal boundary.
- Do not attempt paid API calls, OAuth consent, macOS permission changes, or hardware writes.
