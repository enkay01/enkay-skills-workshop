# Enkay skills workshop

A repository of developer skills designed for low-latency agent workflows.

## Contents

- `skills/`: Markdown skill definitions for Antigravity, Claude, and agent runtimes.
  - `google-developer-docs-style/`: Google technical documentation guidelines.
  - `mac-computer-use/`: Desktop automation on macOS via PyAutoGUI and Quartz event taps.
  - `pdf-renderer/`: Renders PDF files as viewable HTML artifacts with base64-encoded page images.
  - `windows-computer-use/`: Direct Windows desktop automation via Cua Driver (observe-act-verify loop, background UIA delivery, deterministic state verification).
- `tools/macos-use-sdk/`: Git submodule pointing at the MacosUseSDK reference implementation.

## Computer-Use Foundation

This repository ships agent skill definitions and validation suites. Desktop automation runtime code lives in our fork of Cua:
- **Fork repository**: [enkay01/cua](https://github.com/enkay01/cua) (upstream: [trycua/cua](https://github.com/trycua/cua))
- **Pinned release**: `cua-driver-rs-v0.32.0` (Cua Driver 0.32.0, `cua-core` 0.3.2, `cua-sdk` 0.2.0)
- **Licensing**: Built strictly on the MIT-licensed tier of Cua (Cua Driver, cua SDK/CLI). Excludes AGPL (`cua-som`), non-MIT (`cua-perception`), and FSL (`cua-spacesd`) packages.
