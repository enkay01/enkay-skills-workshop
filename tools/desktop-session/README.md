# Desktop session architectural slice

This is a bounded experiment for macOS. One Python process holds the controller,
screen capture, OCR, and Jev client in memory across several actions. It does not
modify the existing computer-use skill or CLI.

Install the dependencies in `requirements.txt` in an isolated Python environment.
Set `TYPESAFE_API_KEY` in that process's environment without putting its value in
this repository. Start `python session.py`; it prints `{"status":"ready"}`.
Send one JSON object per line:

```json
{"command":"run","subgoal":{"instruction":"Search the Steam library for Football Manager 26","expected_window":"Steam","search_text":"Football Manager 26","search_field_text":"Search","max_actions":5,"max_seconds":30}}
```

The controller observes visible text with macOS Vision, asks Jev to rank the
current candidates, checks the selected text and frontmost app on a fresh
capture, then clicks. The request describes each candidate's position and nearby
text; OCR alone cannot prove that text is interactive. The capture masks every
other app window before OCR, so background text cannot become a candidate.
Small image changes such as a cursor animation are tolerated; a substantial
layout change stops the click. If the chosen text contains `search_field_text`,
the controller types the supplied `search_text` and presses Enter.

The Steam example deliberately has no automatic completion rule. The game title
may already be visible in the sidebar. The controller returns an inspection
image for the vision agent to verify the destination. A subgoal may supply
`completion_labels` only when their combination is specific to the destination
screen. The controller checks them before its first action and after each action.
If a click leaves the screen unchanged, a subgoal may opt in to
`max_unchanged_retries` and set a lower `min_probability` for reversible
navigation. The next action uses Jev's already-returned probability distribution;
a changed screen gets a fresh Jev request. Text entry never uses this retry path.

A response with `needs_visual_inspection` has an `image_path` when a capture is
available. The vision agent should read that image, then send
`{"command":"ack_image"}` to delete it. It can send another `run` command with a
revised subgoal after the acknowledgement. `{"command":"close"}` deletes the session directory. The
directory is also removed on normal EOF or interruption; directories left by a
crash are swept on the next startup when their owner process is gone. Older
unidentified `run-*` directories are swept after 24 hours. Routine captures never go
to disk. This slice does not delete screenshots made by the older CLI.

The choice probability threshold of 0.7 is a provisional safety gate, not a
calibrated value. The selected option's probability is distinct from Jev's
`confidence` statistic. Steam exposes no useful accessibility child controls in
this environment, so its candidates come from OCR. A vision agent must still
inspect ambiguous outcomes.

TypeSafe request shape follows the [HTTP API](https://docs.typesafe.ai/api) and
[Choice](https://docs.typesafe.ai/primitives/choice) documentation.
