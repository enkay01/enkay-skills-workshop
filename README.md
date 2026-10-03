# Enkay skills workshop

A repository of developer skills designed for low-latency agent workflows.

## Contents

- `skills/`: Markdown skill definitions for Antigravity, Claude, and agent runtimes.
  - `google-developer-docs-style/`: Google technical documentation guidelines.
  - `mac-computer-use/`: Desktop automation on macOS via PyAutoGUI and Quartz event taps.
  - `pdf-renderer/`: Renders PDF files as viewable HTML artifacts with base64-encoded page images.
- `tools/macos-use-sdk/`: Git submodule pointing at the MacosUseSDK reference implementation.

## Status

The bespoke `windows-computer-use` stack (Rust engine, Python session server, CLI) has been retired. The computer-use direction moves to a fork of [trycua/cua](https://github.com/trycua/cua), building on its computer-use SDK — see issue #15 for the migration and rebuild spec.
