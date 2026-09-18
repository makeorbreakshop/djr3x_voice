---
title: DJ R3X ready chime and turn reliability
status: complete
artifact_readiness: verified
execution: code
---

## Outcome

The startup chime is the final readiness signal: after it sounds, semantic music search is
available (or has explicitly degraded), click-to-record reflects the microphone's real state,
and the first voice/music turn can complete without duplicate terminal output.

## Non-goals and protected behavior

- Preserve Jev as the fast intent classifier and Claude Sonnet as the conversational responder.
- Preserve deterministic title matching before semantic search and provider fallback.
- Do not require Spotify, Arduino, face recognition, or other optional hardware to be healthy.
- Do not alter unrelated dashboard or dependency changes already present in the worktree.

## Prior Learnings

- The September 18 runtime log showed CLAP warming after the chime, so the chime did not mean
  semantic search was ready and the warm-up could compete with later audio work.
- Mouse input toggled its own optimistic state even when Deepgram rejected a start while R3X was
  speaking, requiring an extra click to recover.
- Jev's fixed mood labels collapsed distinct requests into the same query, while CLAP's top
  results were close enough that replaying the current winner was common.
- CLIService rendered every streaming fragment and then the final response, with a prompt after
  each fragment; internal track replacement also announced a stop before the new track.

## Key Decisions

- Start semantic indexing as soon as the music library is loaded, expose an awaitable readiness
  boundary, and await it before playing the chime. Initialization failure counts as settled
  degraded readiness and must not prevent startup.
- Derive mouse recording state only from VOICE_LISTENING_STARTED/STOPPED acknowledgements.
- Preserve bounded mood words from the actual utterance and exclude the current track from the
  ranked semantic result before playing a winner.
- Render only complete LLM responses in the terminal and suppress user-facing stop messages for
  internal track replacement.
- Correlate stop intent completion with MUSIC_PLAYBACK_STOPPED and avoid duplicating fast-router
  results in Claude memory.

## Acceptance Contract

- CLAP initialization completes or degrades before the startup chime begins; no fixed sleep is
  used as a proxy for readiness.
- A click rejected while R3X is speaking leaves mouse state stopped, so the next click starts.
- Two equivalent semantic requests do not select the currently playing track when another ranked
  local candidate exists.
- Jev's semantic query retains meaningful request words such as `funky`, `spooky`, or `banger`
  while excluding unrelated rambling and explicit negative constraints.
- One voice reply produces one final terminal response and one prompt.
- Switching tracks prints only the new-track message; explicit stop still prints its result.
- Fast-routed stop resolves the outcome gate without waiting for the timeout.
- API key fragments are never written to startup logs.

## Work Units

- [x] Add failing focused tests for readiness ordering, acknowledged recording state, semantic
  selection/query preservation, terminal rendering, quiet replacement, and stop correlation.
- [x] Implement the smallest coherent changes and make focused tests pass.
- [x] Run the affected test group and the full non-live suite.
- [x] Exercise the real launcher and verify semantic-ready precedes the chime.
- [x] Review the owned diff for regressions and secret leakage.

## Verified Result

- Focused regression suite: 92 passed.
- Full suite: 438 passed, 54 skipped, 43 pre-existing async-mock warnings.
- Real launcher ordering: semantic warm-up began at 11:12:27, the cached 22-track index became
  ready at 11:12:33, and only then did the startup sound begin at 11:12:33.
- Real launcher confirmed `anthropic/claude-sonnet-5` through OpenRouter and completed a clean
  shutdown.
- Static review found no startup logging of API-key prefixes or suffixes.

## Verification Handoff

- Focused pytest files for semantic search, Jev extraction, mouse input, CLI rendering, intent
  routing, Claude fast-router memory, and main startup ordering.
- Full `cantina_os/tests` suite.
- PTY smoke of `/usr/local/bin/dj-r3x`, checking log timestamps around semantic readiness/chime.

## Risks and Rollback

- CLAP model failure must degrade and unblock startup rather than suppressing the chime forever.
- Event ordering is asynchronous; latches must be armed before dispatch and cleared after timeout.
- Current-track exclusion must fall back to the top result when it is the only valid result.
- Rollback is the task-owned commit; existing unrelated worktree edits remain untouched.

## Stop Conditions and Budgets

- Re-plan after two failed fixes at the same event boundary.
- Do not perform OAuth consent, macOS permission changes, paid API calls, or hardware writes.
