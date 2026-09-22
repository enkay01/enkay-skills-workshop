use serde::{Deserialize, Serialize};
use std::fs;
use std::path::Path;
use std::process::Command;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EditParams {
    pub target_file: String,
    pub target_content: String,
    pub replacement_content: String,
    pub allow_multiple: bool,
    pub verify_command: Option<String>,
    pub rollback_on_failure: bool,
    pub cwd: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EditReport {
    pub success: bool,
    pub verified: bool,
    pub rolled_back: bool,
    pub replacements_made: usize,
    pub verify_output: String,
    pub message: String,
}

pub fn edit_and_verify(params: &EditParams) -> EditReport {
    let path = Path::new(&params.target_file);
    if !path.exists() {
        return EditReport {
            success: false,
            verified: false,
            rolled_back: false,
            replacements_made: 0,
            verify_output: String::new(),
            message: format!("Target file does not exist: {}", params.target_file),
        };
    }

    let original_content = match fs::read_to_string(path) {
        Ok(c) => c,
        Err(e) => {
            return EditReport {
                success: false,
                verified: false,
                rolled_back: false,
                replacements_made: 0,
                verify_output: String::new(),
                message: format!("Failed to read file: {}", e),
            }
        }
    };

    let occurrences = original_content.matches(&params.target_content).count();
    if occurrences == 0 {
        return EditReport {
            success: false,
            verified: false,
            rolled_back: false,
            replacements_made: 0,
            verify_output: String::new(),
            message: format!("Target content not found in {}", params.target_file),
        };
    }

    if occurrences > 1 && !params.allow_multiple {
        return EditReport {
            success: false,
            verified: false,
            rolled_back: false,
            replacements_made: 0,
            verify_output: String::new(),
            message: format!("Target content found {} times in {}. Set allow_multiple=true to replace all.", occurrences, params.target_file),
        };
    }

    let new_content = if params.allow_multiple {
        original_content.replace(&params.target_content, &params.replacement_content)
    } else {
        original_content.replacen(&params.target_content, &params.replacement_content, 1)
    };

    if let Err(e) = fs::write(path, &new_content) {
        return EditReport {
            success: false,
            verified: false,
            rolled_back: false,
            replacements_made: 0,
            verify_output: String::new(),
            message: format!("Failed to write modified file: {}", e),
        };
    }

    let replacements_count = if params.allow_multiple { occurrences } else { 1 };

    let Some(ref cmd_str) = params.verify_command else {
        return EditReport {
            success: true,
            verified: true,
            rolled_back: false,
            replacements_made: replacements_count,
            verify_output: String::new(),
            message: format!("Edit applied ({} replacement(s)). No verification command specified.", replacements_count),
        };
    };

    let mut cmd = Command::new("sh");
    cmd.arg("-c").arg(cmd_str);
    if let Some(ref cwd) = params.cwd {
        cmd.current_dir(cwd);
    }

    let output = match cmd.output() {
        Ok(out) => out,
        Err(e) => {
            if params.rollback_on_failure {
                let _ = fs::write(path, &original_content);
            }
            return EditReport {
                success: false,
                verified: false,
                rolled_back: params.rollback_on_failure,
                replacements_made: 0,
                verify_output: format!("Execution error: {}", e),
                message: format!("Verification command failed to execute. File rolled back: {}", params.rollback_on_failure),
            };
        }
    };

    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).to_string();
    let combined_output = format!("{}\n{}", stdout, stderr).trim().to_string();

    if output.status.success() {
        EditReport {
            success: true,
            verified: true,
            rolled_back: false,
            replacements_made: replacements_count,
            verify_output: combined_output,
            message: format!("Edit applied and verified successfully (exit 0)."),
        }
    } else {
        if params.rollback_on_failure {
            let _ = fs::write(path, &original_content);
        }
        EditReport {
            success: false,
            verified: false,
            rolled_back: params.rollback_on_failure,
            replacements_made: 0,
            verify_output: combined_output,
            message: format!(
                "Verification command exited with status {}. Changes rolled back: {}.",
                output.status.code().unwrap_or(-1),
                params.rollback_on_failure
            ),
        }
    }
}
