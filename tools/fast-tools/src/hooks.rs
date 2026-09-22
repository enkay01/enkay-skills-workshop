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
