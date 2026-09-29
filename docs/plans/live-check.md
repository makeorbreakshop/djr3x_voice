# Live check before retiring CantinaOS

Everything below has passed offline (replay fixtures, null audio, mock drivers:
`cargo test --workspace`, plus the `#[ignore]`d `r3x-runtime --test standalone_acceptance`). What is left
needs real keys, real audio, and the real robot. **Nothing here has been run yet.** It is the
Phase 2-7 deferred checks merged into one pass. Budget: about **12 paid requests** plus
~20 s of Deepgram audio (the optional DJ step adds ~8). Stop at the first failure and keep
`logs/r3x-runtime.log` plus the session log (`logs/session-*.jsonl`).

## 0. Before starting (free)

```bash
cd ~/DJ-R3X\ Voice && git pull
cd rust && cargo test --workspace && cd ..        # green, offline
cargo test --manifest-path rust/Cargo.toml -p r3x-runtime --test standalone_acceptance -- --ignored   # ~90 s, offline
ls ~/.cache/dj-r3x/vision     # YuNet + SFace present (else rust/crates/r3x-vision/scripts/download_models.sh)
cargo run --release --manifest-path rust/Cargo.toml -p r3x-vision -- enroll cantina_os/vision_data/training
```

Plug in both Arduinos and set `ARDUINO_SERIAL_PORT` / `CHEST_SERIAL_PORT` in `.env`. Use
Terminal.app (not Warp) so the global click gets Accessibility. `R3X_VISION_SCENES=0` in
`.env` keeps vision from making paid scene captures on its own.

First start costs up to one extra Claude call per person with unsummarised turns: the
one-time CantinaOS memory import is followed by the summary catch-up.

## 1. Start (free until you talk)

```bash
./r3x                     # builds rust/target/release/r3x-runtime, starts it + the panel, opens the browser
```

Check in the log / panel:
- `r3x runtime standalone=true brain=Rust music=Rust voice=true vision=true`
- face and chest boards found (not `mock`); the chest runs its boot sweep
- `vision` Running (not Degraded); `camera status` in `cargo run -p r3x-cli` shows the camera
- panel shows the gateway connected, 3D R3X follows frames

## 2. LED boards and camera (free)

- Panel: **engage** -> eyes go to the engaged state on the real face; **disengage** -> idle.
- Panel: play an emote / cue (`nod`, `hype_drop`) -> eyes/chest react on the boards.
- Stand in front of the camera -> log `person detected: Brandon (0.xx)`; step away ~10 s ->
  `person exited`.

## 3. Voice loop at the Mac, by ear (~6 paid: Claude warm-up + Jev prewarm on engage, then per turn Jev + Claude + ElevenLabs)

Engage, then click anywhere (global click push-to-talk), speak, click again:
1. "hey Rex, play some music" -> music starts within ~0.3 s of the second click, R3X answers
   over it (music ducks), mouth LEDs move with the words.
2. "make your eyes red" -> the eyes **flash** (the face firmware has no colour; a colour
   request is shown as a flash), R3X answers.

`debug latency` in `r3x-cli` after the turns: stop->intent ~0.3 s, stop->first audible
sample <= the Phase 0 medians (297 ms / 3,019 ms, plan §9).

## 4. Remote browser over HTTPS/WSS (~3 paid)

Browsers only open the mic in a secure context. With Tailscale on the Mac:

```bash
tailscale serve --bg --https=443  http://127.0.0.1:5391     # the panel (voice.html)
tailscale serve --bg --https=8443 http://127.0.0.1:8780     # the gateway
# restart with the phone's page origin allowed, and Vite told the host is fine:
R3X_ALLOWED_ORIGINS=https://<mac>.<tailnet>.ts.net __VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS=<mac>.<tailnet>.ts.net ./r3x --no-open -- --bind 127.0.0.1:8780
```

On the phone: `https://<mac>.<tailnet>.ts.net/voice.html?gw=<mac>.<tailnet>.ts.net:8443#token=<~/.config/dj-r3x/tap_token>`.
Hold to talk: "stop the music" -> music stops, the reply plays **on the phone** and on the Mac.
`tailscale serve reset` afterwards.

## 5. Optional: DJ mode by ear (~8 paid)

"start dj mode" -> a track starts, the intro commentary plays with mouth movement; let it run
to the first transition (~30 s before the track ends): music ducks, commentary, 8 s
crossfade, unduck. Then "stop dj mode". Switch the panel to **Bench** first to confirm no
transition happens there (autonomy off), then back to Show.

## 6. Optional: public mode (~3 paid, own low-limit keys)

Offline first (free): `cargo test -p r3x-runtime --test public`. Then, on the Mac with
`R3X_PUBLIC_SECRET` (32+ chars), `R3X_PUBLIC_ADMIN_TOKEN` and the tailnet page origin in
`R3X_ALLOWED_ORIGINS`:

```bash
cargo run --release --manifest-path rust/Cargo.toml -p r3x-runtime -- --public --bind 127.0.0.1:8790
tailscale serve --bg --https=8444 http://127.0.0.1:8790
curl -s -X POST -H "Authorization: Bearer $R3X_PUBLIC_ADMIN_TOKEN" http://127.0.0.1:8790/token   # -> token
```

On the phone: `https://<mac>.<tailnet>.ts.net/visit.html?gw=<mac>.<tailnet>.ts.net:8444#token=<token>`
(the panel's dev server serves `visit.html`). Check: R3X idles and follows frames; type
"what is your favourite cantina band" -> a reply, spoken on the phone only (the Mac stays
silent, no music anywhere); "play some music" -> R3X says it can't here, nothing plays; a
second phone with its own token does not see the first one's turns. Set
`R3X_PUBLIC_VISITOR_LLM_TOKENS=1`, restart, one turn -> the next is refused in character.
`tailscale serve reset` afterwards.

## 7. Headless voice roundtrip (2 paid, only if step 3 fails)

```bash
cd rust && cargo run -p r3x-voice -- roundtrip     # ElevenLabs -> Deepgram, prints both legs
```

## After a clean pass

Phase 7: archive `cantina_os/` and `src/`, drop `--legacy` and bridge mode, rewrite CLAUDE.md
(plan §11). Until then `./r3x --legacy` still starts CantinaOS.
