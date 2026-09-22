---
name: fast-tools
description: Deterministic high-performance developer tools for low-latency search context, process polling, transactional file edits with rollback, and instant Git working tree inspection.
---

# Fast tools

The fast-tools suite compiles repeated multi-turn agent operations into deterministic, low-latency primitives. Each tool runs natively in compiled Rust, executing in under 5 milliseconds.

## CLI commands

The binary `fast-tools` provides dedicated subcommands for interactive use or subshell execution:

### Search context

Searches files or directories for a pattern and extracts matching lines along with surrounding context:

```bash
fast-tools grep <pattern> <path...> -c 10
```

Context lines automatically clamp to file boundaries. If a match occurs near the beginning or end of a file, line ranges clamp to 1 and total line count without error.

Pass `--json` for machine-readable JSON output, `--regex` for regular expressions, or `-s` for case-sensitive matching.

### Process and port liveness polling

Polls a process ID or TCP port on localhost until it reaches the expected state:

```bash
fast-tools poll --pid <pid> --state alive --timeout 5000
fast-tools poll --port 8080 --state ready --timeout 10000
fast-tools poll --pid <pid> --state terminated --timeout 5000
```

Exits with code 0 if the condition is met before the timeout expires, or code 1 on timeout.

### Transactional edit and verify

Applies a string replacement to a file and executes a verification command in the same operation:

```bash
fast-tools edit <file> <target> <replacement> --verify "cargo check"
```

If the verification command returns a non-zero exit code, `fast-tools` restores the original file content immediately and prints the verification output. To preserve modifications even on verification failure, pass `--no-rollback`.

### Git working tree snapshot

Inspects working tree status, current branch, dirty file list, and diff stats:

```bash
fast-tools git-snapshot --repo .
```

Executes in under 3 milliseconds without launching interactive pagers.

## MCP server integration

The binary runs as an MCP stdio JSON-RPC 2.0 server when invoked with `--mcp`:

```bash
fast-tools --mcp
```

The server exposes five tools:

- `grep_context`: Boundary-clamped search returning line matches and surrounding code.
- `poll_service`: Process ID and port liveness checking with configurable timeout.
- `edit_and_verify`: Transactional file modification with automatic rollback on test failure.
- `git_snapshot`: Instant branch status and unified diff statistics.
- `fast_view`: Whole-file and sliced reading with line numbers.
