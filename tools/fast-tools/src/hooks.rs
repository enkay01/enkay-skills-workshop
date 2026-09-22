use crate::style;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::fs;
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PreToolPayload {
    pub tool_call: Option<ToolCall>,
    pub step_idx: Option<i64>,
    pub conversation_id: Option<String>,
    pub workspace_paths: Option<Vec<String>>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolCall {
    pub name: String,
    pub args: Option<Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PreToolResponse {
    pub decision: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub overwrite: Option<Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PostToolPayload {
    pub step_idx: Option<i64>,
    pub error: Option<String>,
    pub conversation_id: Option<String>,
    pub workspace_paths: Option<Vec<String>>,
    pub transcript_path: Option<String>,
    pub artifact_directory_path: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct StopPayload {
    pub execution_num: Option<i64>,
    pub termination_reason: Option<String>,
    pub error: Option<String>,
    pub fully_idle: Option<bool>,
    pub conversation_id: Option<String>,
    pub workspace_paths: Option<Vec<String>>,
    pub artifact_directory_path: Option<String>,
    pub transcript_path: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StopResponse {
    pub decision: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,
}

pub fn route_pre_tool(input_json: &str) -> PreToolResponse {
    let payload: PreToolPayload = match serde_json::from_str(input_json) {
        Ok(p) => p,
        Err(_) => {
            return PreToolResponse {
                decision: "allow".to_string(),
                reason: None,
                overwrite: None,
            };
        }
    };

    let Some(tool_call) = payload.tool_call else {
        return PreToolResponse {
            decision: "allow".to_string(),
            reason: None,
            overwrite: None,
        };
    };

    if tool_call.name == "run_command" {
        if let Some(ref args) = tool_call.args {
            let cmd_str = args
                .get("CommandLine")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .trim();

            if cmd_str == "git status"
                || cmd_str == "git status -s"
                || cmd_str == "git status --short"
            {
                let mut overwrite_obj = serde_json::Map::new();
                overwrite_obj.insert(
                    "CommandLine".to_string(),
                    Value::String("fast-tools git-snapshot".to_string()),
                );
                return PreToolResponse {
                    decision: "allow".to_string(),
                    reason: Some("Rewrote git status to fast-tools git-snapshot".to_string()),
                    overwrite: Some(Value::Object(overwrite_obj)),
                };
            }

            if cmd_str.starts_with("cat ")
                && !cmd_str.contains('|')
                && !cmd_str.contains('>')
                && !cmd_str.contains('<')
                && !cmd_str.contains('&')
                && !cmd_str.contains(';')
            {
                if let Some(rest) = cmd_str.strip_prefix("cat ") {
                    let target_file = rest.trim();
                    if !target_file.is_empty() && !target_file.starts_with('-') {
                        let mut overwrite_obj = serde_json::Map::new();
                        overwrite_obj.insert(
                            "CommandLine".to_string(),
                            Value::String(format!("fast-tools view {}", target_file)),
                        );
                        return PreToolResponse {
                            decision: "allow".to_string(),
                            reason: Some("Rewrote cat to fast-tools view".to_string()),
                            overwrite: Some(Value::Object(overwrite_obj)),
                        };
                    }
                }
            }

            // Intercept standalone lsof -p <pid> polling
            if !cmd_str.contains('|')
                && !cmd_str.contains('&')
                && !cmd_str.contains(';')
                && !cmd_str.contains('>')
                && !cmd_str.contains('<')
            {
                if let Some(rest) = cmd_str.strip_prefix("lsof -p ") {
                    let trimmed_pid = rest.trim();
                    if let Ok(pid) = trimmed_pid.parse::<i32>() {
                        let mut overwrite_obj = serde_json::Map::new();
                        overwrite_obj.insert(
                            "CommandLine".to_string(),
                            Value::String(format!(
                                "fast-tools poll --pid {} --state alive --timeout 5000",
                                pid
                            )),
                        );
                        return PreToolResponse {
                            decision: "allow".to_string(),
                            reason: Some("Rewrote lsof polling to fast-tools poll".to_string()),
                            overwrite: Some(Value::Object(overwrite_obj)),
                        };
                    }
                }
            }

            // Intercept standalone python os.kill(pid, 0) inline polling
            if !cmd_str.contains('|') && !cmd_str.contains('>') && !cmd_str.contains('<') {
                let re = regex::Regex::new(
                    r#"^python[23]?\s+-c\s+["'](?:import\s+os\s*;\s*)?os\.kill\(\s*(\d+)\s*,\s*0\s*\);?["']$"#,
                )
                .ok();
                if let Some(re) = re {
                    if let Some(caps) = re.captures(cmd_str) {
                        if let Some(pid_match) = caps.get(1) {
                            let pid_str = pid_match.as_str();
                            let mut overwrite_obj = serde_json::Map::new();
                            overwrite_obj.insert(
                                "CommandLine".to_string(),
                                Value::String(format!(
                                    "fast-tools poll --pid {} --state alive --timeout 5000",
                                    pid_str
                                )),
                            );
                            return PreToolResponse {
                                decision: "allow".to_string(),
                                reason: Some(
                                    "Rewrote python os.kill polling to fast-tools poll".to_string(),
                                ),
                                overwrite: Some(Value::Object(overwrite_obj)),
                            };
                        }
                    }
                }
            }
        }
    }

    PreToolResponse {
        decision: "allow".to_string(),
        reason: None,
        overwrite: None,
    }
}

fn collect_markdown_files(
    artifact_dir: Option<&str>,
    workspace_paths: Option<&[String]>,
) -> Vec<PathBuf> {
    let mut files = Vec::new();

    if let Some(dir_str) = artifact_dir {
        let p = Path::new(dir_str);
        if p.is_dir() {
            if let Ok(entries) = fs::read_dir(p) {
                for entry in entries.flatten() {
                    let path = entry.path();
                    if path.is_file() && path.extension().and_then(|s| s.to_str()) == Some("md") {
                        files.push(path);
                    }
                }
            }
        }
    }

    if let Some(workspaces) = workspace_paths {
        for ws in workspaces {
            let p = Path::new(ws);
            if p.is_dir() {
                // Check top-level markdown files in workspace
                if let Ok(entries) = fs::read_dir(p) {
                    for entry in entries.flatten() {
                        let path = entry.path();
                        if path.is_file() && path.extension().and_then(|s| s.to_str()) == Some("md") {
                            files.push(path);
                        }
                    }
                }
            }
        }
    }

    files.sort();
    files.dedup();
    files
}

pub fn route_post_tool(input_json: &str) -> Value {
    let payload: Result<PostToolPayload, _> = serde_json::from_str(input_json);
    if let Ok(p) = payload {
        let files = collect_markdown_files(
            p.artifact_directory_path.as_deref(),
            p.workspace_paths.as_deref(),
        );

        for file in files {
            // Automatically fix em-dashes directly on disk
            let _ = style::check_and_fix_file(&file, true);
        }
    }

    Value::Object(serde_json::Map::new())
}

pub fn route_stop_hook(input_json: &str) -> StopResponse {
    let payload: Result<StopPayload, _> = serde_json::from_str(input_json);
    let p = match payload {
        Ok(p) => p,
        Err(_) => {
            return StopResponse {
                decision: "allow".to_string(),
                reason: None,
            };
        }
    };

    let reason = p.termination_reason.as_deref().unwrap_or("model_stop");
    if reason != "model_stop" {
        return StopResponse {
            decision: "allow".to_string(),
            reason: None,
        };
    }

    let files = collect_markdown_files(
        p.artifact_directory_path.as_deref(),
        p.workspace_paths.as_deref(),
    );

    let mut violations_found = Vec::new();

    for file in files {
        if let Ok(report) = style::check_and_fix_file(&file, true) {
            if !report.is_clean() {
                for v in report.violations {
                    violations_found.push((file.to_string_lossy().to_string(), v));
                }
            }
        }
    }

    if !violations_found.is_empty() {
        let mut msg = String::from(
            "Style check failed. Remove the following violations before completing:\n",
        );
        for (file_name, v) in violations_found {
            msg.push_str(&format!(
                "- {}:{} [{}] found '{}' in: {}\n",
                file_name, v.line_number, v.rule, v.term, v.snippet
            ));
        }
        StopResponse {
            decision: "continue".to_string(),
            reason: Some(msg),
        }
    } else {
        StopResponse {
            decision: "allow".to_string(),
            reason: None,
        }
    }
}
