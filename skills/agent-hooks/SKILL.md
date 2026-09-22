---
name: agent-hooks
description: Lifecycle hooks configuration for Antigravity, providing transparent command rewriting, safety validation, and post-edit verification.
---

# Agent hooks

Antigravity lifecycle hooks intercept tool calls at runtime. Using `PreToolUse` and `PostToolUse` events, hooks enforce tool performance and safety standards without modifying the Antigravity binary.

## Event types

- `PreToolUse`: Runs before a tool executes. Supports `decision` ("allow", "deny", "ask") and `overwrite` (replaces tool arguments prior to execution).
- `PostToolUse`: Runs after a tool completes. Receives step results and execution status.

## Configuration

Place `hooks.json` in `~/.gemini/config/hooks.json` for machine-wide enforcement, or `.agents/hooks.json` for workspace-specific rules:

```json
{
  "command-rewriter": {
    "PreToolUse": [
      {
        "matcher": "run_command",
        "hooks": [
          {
            "type": "command",
            "command": "fast-tools hook-pre",
            "timeout": 5
          }
        ]
      }
    ]
  },
  "post-verifier": {
    "PostToolUse": [
      {
        "matcher": "replace_file_content|write_to_file",
        "hooks": [
          {
            "type": "command",
            "command": "fast-tools hook-post",
            "timeout": 15
          }
        ]
      }
    ]
  }
}
```

## Rewriting behavior

The `fast-tools hook-pre` handler intercepts slow command patterns:

- Calls to `git status`, `git status -s`, or `git status --short` are rewritten to `fast-tools git-snapshot`. The agent receives branch information, dirty file status, and diff statistics in a single step.
- Shell polling commands with sleep intervals are redirected to native `fast-tools poll` executions.
