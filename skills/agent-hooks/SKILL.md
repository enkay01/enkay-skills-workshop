---
name: agent-hooks
description: Lifecycle hooks configuration for Antigravity, providing transparent command rewriting, safety validation, and post-edit verification.
---

# Agent hooks

Antigravity lifecycle hooks intercept tool calls at runtime. Using `PreToolUse` and `PostToolUse` events, hooks enforce tool performance and safety standards without modifying the Antigravity binary.

## Event types

- `PreToolUse`: Runs before a tool executes. Supports `decision` ("allow", "deny", "ask") and `overwrite` (replaces tool arguments prior to execution).
- `PostToolUse`: Runs after a tool completes. Receives step results and automatically corrects em dashes on disk for written or modified markdown files.
- `Stop`: Runs when the agent execution loop attempts to terminate. Validates generated artifacts against banned words and style rules, returning `continue` if violations are found.

## Configuration

Place `hooks.json` in `~/.gemini/config/hooks.json` for machine-wide enforcement, or `.agents/hooks.json` for workspace-specific rules:

```json
{
  "fast-tools-optimizer": {
    "PreToolUse": [
      {
        "matcher": "run_command",
        "hooks": [
          {
            "type": "command",
            "command": "~/.local/bin/fast-tools hook-pre",
            "timeout": 5
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "replace_file_content|write_to_file",
        "hooks": [
          {
            "type": "command",
            "command": "~/.local/bin/fast-tools hook-post",
            "timeout": 5
          }
        ]
      }
    ],
    "Stop": [
      {
        "type": "command",
        "command": "~/.local/bin/fast-tools hook-stop",
        "timeout": 5
      }
    ]
  }
}
```

## Hook behavior

The `fast-tools hook-pre` handler intercepts slow command patterns:
- Calls to `git status`, `git status -s`, or `git status --short` are rewritten to `fast-tools git-snapshot`. The agent receives branch information, dirty file status, and diff statistics in a single step.
- Shell polling commands with sleep intervals or inline Python scripts are redirected to native `fast-tools poll` executions.

The `fast-tools hook-post` handler automates file hygiene:
- Automatically replaces em dashes (`—`) with hyphens (`-`) on disk when markdown files are written or edited.

The `fast-tools hook-stop` handler validates artifacts:
- Before the agent finishes, inspects all markdown artifacts for banned words (`delve`, `crucial`, `pivotal`, etc.) and banned constructions. If violations remain, it blocks `model_stop` and returns the exact line numbers and terms to fix.
