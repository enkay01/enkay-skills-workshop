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

## Known limitations

**Some applications mangle injected Unicode events.** The default `act type`
method uses `KEYEVENTF_UNICODE`. Modern Windows 11 Notepad has reproducibly
corrupted this input despite accepting every event. Delay and batching changes
did not resolve it. Per `docs/windows-text-input-apis.md`, delivery through
`WM_CHAR` is verbatim and no Win32-layer coalescing is documented; the rewrite
happens downstream in the TSF composition/correction layer, which is why only
committed-text insertion is robust there.

Use verified message delivery for this target:

```bash
wcu act type --method commit --text "wcu typed this sentence."
```

Commit sends one synchronous, undoable `EM_REPLACESEL` message to the focused
editor's own window (bounded by `SendMessageTimeoutW`), then checks the
expected replacement for up to two seconds. `verification: "matched"`
confirms the result; `text_mismatch` is an error. It touches neither the
clipboard (`clipboard: "unchanged"`) nor the input stream
(`events_injected: 0`, `messages_sent: 1`). Unicode mode reports
`verification: "unavailable"`; `typed` alone only confirms dispatch.
Dry runs dispatch nothing.

Commit requires a focused editor with one selection or caret and documents up
to 65536 UTF-16 units. It refuses NUL, `--delay-ms`, read-only targets, and
editors it cannot read back. Editors without TextPattern can only verify
insertion into an empty document. There is no automatic retry or fallback:
after a mismatch the single message was applied atomically or not at all,
so inspect the editor before acting again.

Clipboard delivery remains the fallback when an editor ignores edit messages:

```bash
wcu act type --method paste --text "wcu typed this sentence."
```

Paste uses `CF_UNICODETEXT` and virtual-key Ctrl+V. It reads the focused editor's
full text and selection, then checks the expected replacement for up to two
seconds. `verification: "matched"` confirms the result; `text_mismatch` is an
error. Unicode mode reports `verification: "unavailable"`; `typed` alone only
confirms dispatch. Dry runs do not inspect text or touch the clipboard.

Paste requires UIA TextPattern with one selection or caret and documents up to
65536 UTF-16 units. It refuses unsupported editors, NUL, `--delay-ms`, held
modifiers, and clipboard formats other than plain text and locale before
dispatch. Existing plain-text clipboard formats are restored after a confirmed
paste, unless another process changed the clipboard. Check the returned
`clipboard` field. After ambiguous delivery, verification failure, or cancellation,
staged text may remain on the clipboard so a late consumer cannot paste the old
contents. There is no automatic retry or fallback.

**Foreground is a real constraint.** Guarded actions refuse unless the
attached window is the foreground window, so they return `foreground_changed`
or `focus_refused` rather than typing into whatever happens to be in front.
Windows also declines to move foreground between processes that are not
eligible, so `focus`/`switch` can be refused when the calling terminal holds
focus. A freshly launched process is eligible; `wcu open` followed immediately
by `switch` and the action in one uninterrupted sequence is the reliable
pattern.
