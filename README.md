# Enkay skills workshop

A repository of developer skills, deterministic CLI utilities, and Antigravity lifecycle hooks designed for low-latency agent workflows.

## Contents

- `skills/`: Markdown skill definitions for Antigravity, Claude, and agent runtimes.
  - `agent-hooks/`: Configuration guide for Antigravity lifecycle hooks.
  - `google-developer-docs-style/`: Google technical documentation guidelines.
- `tools/fast-tools/`: Native Rust crate compiling to `fast-tools`, providing Git snapshots, style enforcement, and lifecycle hook handlers.
- `config/hooks.json`: Lifecycle hook configurations for runtime tool rewriting, auto-fixing, and style guards.
- `install.sh`: Installation script to build the release binary, install it to `~/.local/bin/`, and configure global lifecycle hooks.

## Quickstart

### 1. Build and install

Run the installation script:

```bash
./install.sh
```

This command sets up the hook environment:
- Compiles `fast-tools` in release mode and copies it to `~/.local/bin/`.
- Removes legacy `view-file` and redundant MCP server registrations from `~/.gemini/config/mcp_config.json`.
- Merges the `fast-tools-optimizer` configuration into `~/.gemini/config/hooks.json` to enable `PreToolUse`, `PostToolUse`, and `Stop` hooks.
- Cleans obsolete prompt rules from `~/.gemini/GEMINI.md` and `~/.gemini/AGENTS.md`.

### 2. Testing

The test suite in `tools/fast-tools/tests/` is separated into two categories:

- **Constraint tests** (`tests/constraint_tests.rs`): Clean, regression-oriented tests verifying behavioral contracts and style rules.
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

## Hook features

### `PreToolUse`: Git snapshot rewrite

Intercepts calls to `git status`, `git status -s`, or `git status --short` and rewrites them to `fast-tools git-snapshot`. The agent receives branch status, untracked files, and diff statistics in a single turn.

### `PostToolUse`: Em dash auto-fixer

Runs after `write_to_file` and `replace_file_content` on markdown files. Automatically replaces em dashes (`—`) with hyphens (`-`) directly on disk in zero agent turns.

### `Stop`: Style guard

Runs when the agent execution loop attempts to terminate (`model_stop`). Inspects all generated markdown artifacts for banned words (`delve`, `crucial`, `pivotal`, `intricate`, etc.) and banned constructions. If violations remain, it blocks `model_stop` and returns the exact line numbers and terms to fix.

### `fast-tools style-check`

Validates files against banned words and style rules from the command line:

```bash
fast-tools style-check path/to/file.md
fast-tools style-check path/to/file.md --fix
```
