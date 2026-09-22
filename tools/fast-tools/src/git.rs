use serde::{Deserialize, Serialize};
use std::process::Command;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GitSnapshot {
    pub branch: String,
    pub clean: bool,
    pub status_lines: Vec<String>,
    pub diff_stat: String,
}

pub fn get_working_tree_snapshot(repo_dir: &str) -> Result<GitSnapshot, String> {
    let status_out = Command::new("git")
        .args(["-C", repo_dir, "status", "--porcelain=v1", "-b"])
        .output()
        .map_err(|e| format!("Failed to run git status: {}", e))?;

    if !status_out.status.success() {
        let err = String::from_utf8_lossy(&status_out.stderr);
        return Err(format!("git status failed: {}", err.trim()));
    }

    let status_str = String::from_utf8_lossy(&status_out.stdout);
    let mut lines = status_str.lines();

    let branch_line = lines.next().unwrap_or("## unknown");
    let raw_branch = branch_line.trim_start_matches("## ").trim();
    let branch = if let Some(stripped) = raw_branch.strip_prefix("No commits yet on ") {
        stripped.to_string()
    } else if let Some((local, _upstream)) = raw_branch.split_once("...") {
        local.to_string()
    } else {
        raw_branch.to_string()
    };

    let status_lines: Vec<String> = lines.map(|s| s.to_string()).collect();
    let clean = status_lines.is_empty();

    let diff_out = Command::new("git")
        .args(["-C", repo_dir, "diff", "--stat"])
        .output()
        .map_err(|e| format!("Failed to run git diff: {}", e))?;

    let diff_stat = String::from_utf8_lossy(&diff_out.stdout).trim().to_string();

    Ok(GitSnapshot {
        branch,
        clean,
        status_lines,
        diff_stat,
    })
}
