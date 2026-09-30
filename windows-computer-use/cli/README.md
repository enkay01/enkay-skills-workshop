# wcu — direct-agent Windows computer-use CLI

An installable Windows CLI and companion skill that let an agent host use the
computer directly: observe the desktop, choose actions from real evidence, and
dispatch guarded input through the Rust engine. No model endpoint, no proxy,
no second model call.

## Install

```bash
# 1. Build the engine (release)
cd ../engine
cargo build --release

# 2. Install the CLI (editable, from the repo)
cd ../cli
pip install -e .

# 3. Verify
wcu --version
wcu session start
```

The CLI locates the engine at `WCU_ENGINE_PATH`, then
`../engine/target/release/wcu-engine.exe`, then the debug build.

## Use

One persistent session per Windows desktop. Start it once, then run commands:

```bash
wcu session start                 # start the session (returns session id)
wcu windows [--filter TEXT]       # list visible windows
wcu attach <hwnd>                 # attach capture to a window
wcu observe                       # capture a screenshot (PNG on disk)
wcu act click --observation-id N --point X,Y   # guarded action
wcu inspect [--question TEXT]     # bounded accessibility inspection
wcu history                       # bounded operation history
wcu session status                # session/engine/target state
wcu session stop                  # stop and clean up
```

Run `wcu help` for the full command overview and `wcu <command> --help` for
per-command syntax.

## Result envelope

Every command prints one JSON envelope on stdout (diagnostics on stderr):

```json
{
  "v": 1,
  "session_id": "...",
  "operation_id": "...",
  "ok": true,
  "status": "ok",
  "result": {}
}
```

`status` is `ok`, `dry_run`, `refused`, `cancelled`, or `error`. A `refused`
action includes updated evidence for reconsideration. Exit codes: `0` a result
was produced, `1` transport failure, `2` usage error.

## Layout

- `wcu/cli.py` — command line interface and result envelope
- `wcu/session.py` — session lifecycle and IPC client
- `wcu/session_server.py` — persistent session process (owns the engine)
- `wcu/ipc.py` — named-pipe transport
- `wcu/imaging.py` — BGRA to PNG encoding
- `wcu/history.py` — bounded operation history

The session process owns the `WcuClient` (and therefore the Rust engine)
across independent CLI invocations, so capture state, attachments, and
observation identity survive between commands.
