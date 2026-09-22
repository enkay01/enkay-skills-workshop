use serde::{Deserialize, Serialize};
use serde_json::Value;

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
            let cmd_str = args.get("CommandLine")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .trim();

            if cmd_str == "git status" || cmd_str == "git status -s" || cmd_str == "git status --short" {
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

            if cmd_str.starts_with("cat ") && !cmd_str.contains('|') && !cmd_str.contains('>') {
                let target_file = cmd_str.trim_start_matches("cat ").trim();
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

            // Intercept lsof -p <pid> polling
            if cmd_str.starts_with("lsof -p ") {
                let rest = cmd_str.trim_start_matches("lsof -p ").trim();
                let pid_str = rest.split_whitespace().next().unwrap_or("");
                if let Ok(pid) = pid_str.parse::<i32>() {
                    let mut overwrite_obj = serde_json::Map::new();
                    overwrite_obj.insert(
                        "CommandLine".to_string(),
                        Value::String(format!("fast-tools poll --pid {} --state alive --timeout 5000", pid)),
                    );
                    return PreToolResponse {
                        decision: "allow".to_string(),
                        reason: Some("Rewrote lsof polling to fast-tools poll".to_string()),
                        overwrite: Some(Value::Object(overwrite_obj)),
                    };
                }
            }

            // Intercept python os.kill(pid, 0) inline polling
            if (cmd_str.contains("os.kill") || cmd_str.contains("os.kill(")) && cmd_str.contains("python") {
                let re = regex::Regex::new(r"os\.kill\(\s*(\d+)\s*,\s*0\s*\)").ok();
                if let Some(re) = re {
                    if let Some(caps) = re.captures(cmd_str) {
                        if let Some(pid_match) = caps.get(1) {
                            let pid_str = pid_match.as_str();
                            let mut overwrite_obj = serde_json::Map::new();
                            overwrite_obj.insert(
                                "CommandLine".to_string(),
                                Value::String(format!("fast-tools poll --pid {} --state alive --timeout 5000", pid_str)),
                            );
                            return PreToolResponse {
                                decision: "allow".to_string(),
                                reason: Some("Rewrote python os.kill polling to fast-tools poll".to_string()),
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

pub fn route_post_tool(_input_json: &str) -> Value {
    Value::Object(serde_json::Map::new())
}
