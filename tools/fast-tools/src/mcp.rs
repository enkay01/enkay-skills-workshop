use crate::edit::{edit_and_verify, EditParams};
use crate::git::get_working_tree_snapshot;
use crate::grep::search_context;
use crate::poll::{poll_service, PollConfig, TargetState};
use crate::view::{view_batch_files, view_single_file, ViewOptions};
use serde_json::{json, Value};
use std::io::{self, BufRead, Write};
use std::path::PathBuf;

pub fn run_mcp_server() -> Result<(), io::Error> {
    let stdin = io::stdin();
    let mut stdout = io::stdout();

    for line in stdin.lock().lines() {
        let line = match line {
            Ok(l) => l,
            Err(_) => break,
        };
        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }

        let request: Value = match serde_json::from_str(trimmed) {
            Ok(v) => v,
            Err(_) => continue,
        };

        if let Some(response) = handle_mcp_request(&request) {
            let resp_str = response.to_string();
            writeln!(stdout, "{}", resp_str)?;
            stdout.flush()?;
        }
    }

    Ok(())
}

pub fn handle_mcp_request(request: &Value) -> Option<Value> {
    let req_id = request.get("id").cloned();
    let method = request.get("method").and_then(|v| v.as_str()).unwrap_or("");

    let response = match method {
        "initialize" => {
            json!({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {
                        "tools": {}
                    },
                    "serverInfo": {
                        "name": "fast-tools",
                        "version": "0.1.0"
                    }
                }
            })
        }
        "notifications/initialized" => {
            return None;
        }
        "tools/list" => {
            json!({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "tools": [
                        {
                            "name": "fast_view",
                            "description": "High-performance whole-file reader with line numbers, outline mode, and safety caps.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "path": { "type": "string", "description": "Path to file" },
                                    "start_line": { "type": "integer", "description": "Optional 1-indexed start line" },
                                    "end_line": { "type": "integer", "description": "Optional 1-indexed end line" },
                                    "line_numbers": { "type": "boolean", "default": true },
                                    "outline": { "type": "boolean", "default": false },
                                    "force_all": { "type": "boolean", "default": false }
                                },
                                "required": ["path"]
                            }
                        },
                        {
                            "name": "fast_read_batch",
                            "description": "Reads multiple files in one turn with safety threshold degrading to outline on secondary files.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "paths": { "type": "array", "items": { "type": "string" }, "description": "List of file paths to read" },
                                    "line_numbers": { "type": "boolean", "default": true }
                                },
                                "required": ["paths"]
                            }
                        },
                        {
                            "name": "grep_context",
                            "description": "Boundary-clamped search returning matching lines with surrounding context.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "paths": { "type": "array", "items": { "type": "string" } },
                                    "pattern": { "type": "string" },
                                    "context_lines": { "type": "integer", "default": 10 },
                                    "max_matches": { "type": "integer", "default": 5 },
                                    "is_regex": { "type": "boolean", "default": false },
                                    "case_sensitive": { "type": "boolean", "default": false }
                                },
                                "required": ["paths", "pattern"]
                            }
                        },
                        {
                            "name": "poll_service",
                            "description": "Deterministic process or port liveness poller with timeout.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "pid": { "type": "integer" },
                                    "port": { "type": "integer" },
                                    "state": { "type": "string", "enum": ["alive", "terminated", "ready"], "default": "ready" },
                                    "timeout_ms": { "type": "integer", "default": 5000 },
                                    "interval_ms": { "type": "integer", "default": 200 }
                                }
                            }
                        },
                        {
                            "name": "edit_and_verify",
                            "description": "Applies a file edit and executes a verification command, auto-rolling back if verification fails.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "target_file": { "type": "string" },
                                    "target_content": { "type": "string" },
                                    "replacement_content": { "type": "string" },
                                    "allow_multiple": { "type": "boolean", "default": false },
                                    "verify_command": { "type": "string" },
                                    "rollback_on_failure": { "type": "boolean", "default": true },
                                    "cwd": { "type": "string" }
                                },
                                "required": ["target_file", "target_content", "replacement_content"]
                            }
                        },
                        {
                            "name": "git_snapshot",
                            "description": "Sub-3ms working tree status and diff summary.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "repo_dir": { "type": "string", "default": "." }
                                }
                            }
                        }
                    ]
                }
            })
        }
        "tools/call" => {
            let params = request.get("params").cloned().unwrap_or(json!({}));
            let name = params.get("name").and_then(|v| v.as_str()).unwrap_or("");
            let arguments = params.get("arguments").cloned().unwrap_or(json!({}));

            let (content_str, is_error) = match name {
                "fast_view" => {
                    let path_str = arguments.get("path").and_then(|v| v.as_str()).unwrap_or("");
                    let opts = ViewOptions {
                        start_line: arguments.get("start_line").and_then(|v| v.as_u64()).and_then(|v| usize::try_from(v).ok()),
                        end_line: arguments.get("end_line").and_then(|v| v.as_u64()).and_then(|v| usize::try_from(v).ok()),
                        line_numbers: arguments.get("line_numbers").and_then(|v| v.as_bool()).unwrap_or(true),
                        outline: arguments.get("outline").and_then(|v| v.as_bool()).unwrap_or(false),
                        force_all: arguments.get("force_all").and_then(|v| v.as_bool()).unwrap_or(false),
                    };
                    match view_single_file(&PathBuf::from(path_str), &opts) {
                        Ok(text) => (text, false),
                        Err(e) => (format!("Error: {}", e), true),
                    }
                }
                "fast_read_batch" => {
                    let paths: Vec<PathBuf> = arguments.get("paths")
                        .and_then(|v| v.as_array())
                        .map(|arr| arr.iter().filter_map(|v| v.as_str()).map(PathBuf::from).collect())
                        .unwrap_or_default();
                    let opts = ViewOptions {
                        line_numbers: arguments.get("line_numbers").and_then(|v| v.as_bool()).unwrap_or(true),
                        ..Default::default()
                    };
                    (view_batch_files(&paths, &opts, 500 * 1024), false)
                }
                "grep_context" => {
                    let paths: Vec<PathBuf> = arguments.get("paths")
                        .and_then(|v| v.as_array())
                        .map(|arr| arr.iter().filter_map(|v| v.as_str()).map(PathBuf::from).collect())
                        .unwrap_or_default();
                    let pattern = arguments.get("pattern").and_then(|v| v.as_str()).unwrap_or("");
                    let context_lines = arguments.get("context_lines").and_then(|v| v.as_u64()).and_then(|v| usize::try_from(v).ok()).unwrap_or(10);
                    let max_matches = arguments.get("max_matches").and_then(|v| v.as_u64()).and_then(|v| usize::try_from(v).ok()).unwrap_or(5);
                    let is_regex = arguments.get("is_regex").and_then(|v| v.as_bool()).unwrap_or(false);
                    let case_sensitive = arguments.get("case_sensitive").and_then(|v| v.as_bool()).unwrap_or(false);

                    match search_context(&paths, pattern, context_lines, max_matches, is_regex, case_sensitive) {
                        Ok(matches) => (serde_json::to_string_pretty(&matches).unwrap_or_default(), false),
                        Err(e) => (format!("Error: {}", e), true),
                    }
                }
                "poll_service" => {
                    let pid_res = match arguments.get("pid") {
                        Some(v) => match v.as_i64() {
                            Some(val) => match i32::try_from(val) {
                                Ok(parsed) => Ok(Some(parsed)),
                                Err(_) => Err(format!("pid {} out of range for i32", val)),
                            },
                            None => Err("Invalid pid: expected integer".to_string()),
                        },
                        None => Ok(None),
                    };

                    let port_res = match arguments.get("port") {
                        Some(v) => match v.as_u64() {
                            Some(val) => match u16::try_from(val) {
                                Ok(parsed) => Ok(Some(parsed)),
                                Err(_) => Err(format!("port {} out of range for u16 (1-65535)", val)),
                            },
                            None => Err("Invalid port: expected integer".to_string()),
                        },
                        None => Ok(None),
                    };

                    match (pid_res, port_res) {
                        (Err(e), _) | (_, Err(e)) => (format!("Error: {}", e), true),
                        (Ok(pid), Ok(port)) => {
                            let state_str = arguments.get("state").and_then(|v| v.as_str()).unwrap_or("ready");
                            let state = match state_str {
                                "alive" => TargetState::Alive,
                                "terminated" => TargetState::Terminated,
                                _ => TargetState::Ready,
                            };
                            let timeout_ms = arguments.get("timeout_ms").and_then(|v| v.as_u64()).unwrap_or(5000);
                            let interval_ms = arguments.get("interval_ms").and_then(|v| v.as_u64()).unwrap_or(200);

                            let config = PollConfig { pid, port, state, timeout_ms, interval_ms };
                            let report = poll_service(&config);
                            let is_error = !report.success;
                            (serde_json::to_string_pretty(&report).unwrap_or_default(), is_error)
                        }
                    }
                }
                "edit_and_verify" => {
                    let edit_params = EditParams {
                        target_file: arguments.get("target_file").and_then(|v| v.as_str()).unwrap_or("").to_string(),
                        target_content: arguments.get("target_content").and_then(|v| v.as_str()).unwrap_or("").to_string(),
                        replacement_content: arguments.get("replacement_content").and_then(|v| v.as_str()).unwrap_or("").to_string(),
                        allow_multiple: arguments.get("allow_multiple").and_then(|v| v.as_bool()).unwrap_or(false),
                        verify_command: arguments.get("verify_command").and_then(|v| v.as_str()).map(|s| s.to_string()),
                        rollback_on_failure: arguments.get("rollback_on_failure").and_then(|v| v.as_bool()).unwrap_or(true),
                        cwd: arguments.get("cwd").and_then(|v| v.as_str()).map(|s| s.to_string()),
                    };
                    let report = edit_and_verify(&edit_params);
                    let is_error = !report.success;
                    (serde_json::to_string_pretty(&report).unwrap_or_default(), is_error)
                }
                "git_snapshot" => {
                    let repo_dir = arguments.get("repo_dir").and_then(|v| v.as_str()).unwrap_or(".");
                    match get_working_tree_snapshot(repo_dir) {
                        Ok(snap) => (serde_json::to_string_pretty(&snap).unwrap_or_default(), false),
                        Err(e) => (format!("Error: {}", e), true),
                    }
                }
                _ => {
                    return Some(json!({
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {
                            "code": -32602,
                            "message": format!("Unknown tool: {}", name)
                        }
                    }));
                }
            };

            let mut result_obj = serde_json::Map::new();
            result_obj.insert("content".to_string(), json!([{"type": "text", "text": content_str}]));
            if is_error {
                result_obj.insert("isError".to_string(), Value::Bool(true));
            }

            json!({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": Value::Object(result_obj)
            })
        }
        _ => {
            json!({
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32601,
                    "message": "Method not found"
                }
            })
        }
    };

    Some(response)
}
