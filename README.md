# Enkay skills workshop

A repository of developer skills, deterministic CLI utilities, and Antigravity customization configs designed for low-latency agent workflows.

## Contents

- `skills/`: Markdown skill definitions for Antigravity, Claude, and agent runtimes.
  - `fast-tools/`: Reference guide for deterministic search, polling, transactional editing, and git snapshotting.
  - `fast-view/`: Guide for whole-file and multi-file batch reading without micro-chunking.
  - `agent-hooks/`: Configuration guide for Antigravity lifecycle hooks.
  - `google-developer-docs-style/`: Google technical documentation guidelines.
- `tools/fast-tools/`: Native Rust crate compiling to `fast-tools` and `view-file` binaries with CLI and stdio MCP server modes.
- `config/hooks.json`: Lifecycle hook configurations for runtime tool rewriting and verification.
- `install.sh`: Installation script to build release binaries, install them to `~/.local/bin/`, and configure MCP and hooks.

## Quickstart

### 1. Build and install

Run the installation script:

```bash
./install.sh
```

This compiles the release binaries, copies `fast-tools` and `view-file` into `~/.local/bin/`, and registers `fast-tools` and `fast-view` inside `~/.gemini/config/mcp_config.json`.

### 2. Testing

The test suite in `tools/fast-tools/tests/` is separated into two categories:

- **Constraint tests** (`tests/constraint_tests.rs`): Clean, regression-oriented tests verifying behavioral contracts.
- **Throwaway tests** (`tests/throwaway_tests.rs`): Boundary torture tests, adversarial inputs, and chaos scenarios.

Run all tests:

```bash
cargo test --manifest-path tools/fast-tools/Cargo.toml
```

Run only constraint tests:

```bash
cargo test --test constraint_tests --manifest-path tools/fast-tools/Cargo.toml
```

Run only throwaway tests:

```bash
cargo test --test throwaway_tests --manifest-path tools/fast-tools/Cargo.toml
```

## Available tools

### `fast-tools view` / `view-file`

Reads full files up to 5,000 lines or 500 KB in under 4 milliseconds:

```bash
view-file src/lib.rs
view-file src/main.rs src/lib.rs tests/constraint_tests.rs
view-file src/lib.rs -s 10 -e 50
view-file src/lib.rs -o
```

### `fast-tools grep`

Searches files with boundary-clamped context lines:

```bash
fast-tools grep "search_context" src/ -c 10
fast-tools grep "struct" src/ -c 5 --json
```

### `fast-tools poll`

Deterministic process and port liveness checking with timeout:

```bash
fast-tools poll --pid 12345 --state alive --timeout 5000
fast-tools poll --port 8080 --state ready --timeout 10000
```

### `fast-tools edit`

Applies string replacement and runs an immediate verification command with automatic rollback on error:

```bash
fast-tools edit src/lib.rs "old_code" "new_code" --verify "cargo check"
```

### `fast-tools git-snapshot`

Returns current branch, clean status, dirty file list, and diff stats:

```bash
fast-tools git-snapshot
```

### MCP server mode

Runs the stdio JSON-RPC 2.0 MCP server:

```bash
fast-tools --mcp
```
