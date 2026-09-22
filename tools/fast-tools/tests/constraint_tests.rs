use fast_tools::edit::{edit_and_verify, EditParams};
use fast_tools::git::get_working_tree_snapshot;
use fast_tools::grep::search_context;
use fast_tools::hooks::route_pre_tool;
use fast_tools::poll::{poll_service, PollConfig, TargetState};
use fast_tools::view::{view_batch_files, view_single_file, ViewOptions};
use serde_json::json;
use std::fs;
use tempfile::tempdir;

#[test]
fn test_view_single_file_contract() {
    let dir = tempdir().expect("Failed to create temp dir");
    let file_path = dir.path().join("sample.txt");
    fs::write(&file_path, "alpha\nbeta\ngamma\n").expect("Failed to write file");

    let opts = ViewOptions {
        line_numbers: true,
        ..Default::default()
    };

    let result = view_single_file(&file_path, &opts).expect("view_single_file failed");
    assert!(result.contains("1: alpha"));
    assert!(result.contains("2: beta"));
    assert!(result.contains("3: gamma"));
}

#[test]
fn test_view_sliced_range_contract() {
    let dir = tempdir().expect("Failed to create temp dir");
    let file_path = dir.path().join("lines.txt");
    let content = (1..=20).map(|i| format!("line {}", i)).collect::<Vec<_>>().join("\n");
    fs::write(&file_path, content).expect("Failed to write file");

    let opts = ViewOptions {
        start_line: Some(5),
        end_line: Some(8),
        line_numbers: true,
        ..Default::default()
    };

    let result = view_single_file(&file_path, &opts).expect("view_single_file failed");
    assert!(result.contains("5: line 5"));
    assert!(result.contains("8: line 8"));
    assert!(!result.contains("4: line 4"));
    assert!(!result.contains("9: line 9"));
}

#[test]
fn test_view_batch_contract() {
    let dir = tempdir().expect("Failed to create temp dir");
    let file_a = dir.path().join("a.txt");
    let file_b = dir.path().join("b.txt");
    fs::write(&file_a, "content_a").expect("Failed to write file a");
    fs::write(&file_b, "content_b").expect("Failed to write file b");

    let opts = ViewOptions::default();
    let batch_output = view_batch_files(&[file_a.clone(), file_b.clone()], &opts, 1024 * 1024);

    assert!(batch_output.contains("File [1/2]"));
    assert!(batch_output.contains("content_a"));
    assert!(batch_output.contains("File [2/2]"));
    assert!(batch_output.contains("content_b"));
}

#[test]
fn test_grep_boundary_clamping_contract() {
    let dir = tempdir().expect("Failed to create temp dir");
    let file_path = dir.path().join("short.txt");
    // 5 lines total
    fs::write(&file_path, "one\ntwo\ntarget\nfour\nfive\n").expect("Failed to write file");

    // Requesting 10 context lines on a 5-line file must NOT error; it must clamp cleanly
    let matches = search_context(
        &[file_path],
        "target",
        10, // context lines > total file lines
        5,
        false,
        true,
    ).expect("search_context must succeed with boundary clamping");

    assert_eq!(matches.len(), 1);
    let m = &matches[0];
    assert_eq!(m.line_number, 3);
    assert_eq!(m.start_line, 1);
    assert_eq!(m.end_line, 5);
    assert!(m.snippet.contains(">   3: target"));
}

#[test]
fn test_poll_service_live_pid_contract() {
    let current_pid = std::process::id() as i32;
    let config = PollConfig {
        pid: Some(current_pid),
        port: None,
        state: TargetState::Alive,
        timeout_ms: 500,
        interval_ms: 50,
    };

    let report = poll_service(&config);
    assert!(report.success);
    assert!(report.elapsed_ms < 500);
}

#[test]
fn test_poll_service_dead_pid_contract() {
    // 9999999 is an unused PID
    let config = PollConfig {
        pid: Some(9999999),
        port: None,
        state: TargetState::Terminated,
        timeout_ms: 500,
        interval_ms: 50,
    };

    let report = poll_service(&config);
    assert!(report.success);
}

#[test]
fn test_edit_and_verify_success_contract() {
    let dir = tempdir().expect("Failed to create temp dir");
    let file_path = dir.path().join("code.rs");
    fs::write(&file_path, "fn old_name() {}").expect("Failed to write file");

    let params = EditParams {
        target_file: file_path.to_string_lossy().to_string(),
        target_content: "old_name".to_string(),
        replacement_content: "new_name".to_string(),
        allow_multiple: false,
        verify_command: Some("test -f code.rs".to_string()),
        rollback_on_failure: true,
        cwd: Some(dir.path().to_string_lossy().to_string()),
    };

    let report = edit_and_verify(&params);
    assert!(report.success);
    assert!(report.verified);
    assert!(!report.rolled_back);

    let updated = fs::read_to_string(&file_path).expect("Failed to read file");
    assert_eq!(updated, "fn new_name() {}");
}

#[test]
fn test_edit_and_verify_rollback_contract() {
    let dir = tempdir().expect("Failed to create temp dir");
    let file_path = dir.path().join("code.rs");
    let original = "fn stable_function() {}";
    fs::write(&file_path, original).expect("Failed to write file");

    let params = EditParams {
        target_file: file_path.to_string_lossy().to_string(),
        target_content: "stable_function".to_string(),
        replacement_content: "broken_syntax".to_string(),
        allow_multiple: false,
        verify_command: Some("false".to_string()), // Command deliberately fails
        rollback_on_failure: true,
        cwd: None,
    };

    let report = edit_and_verify(&params);
    assert!(!report.success);
    assert!(!report.verified);
    assert!(report.rolled_back);

    // Verify file content was restored byte-for-byte
    let current_content = fs::read_to_string(&file_path).expect("Failed to read file");
    assert_eq!(current_content, original);
}

#[test]
fn test_edit_rejects_missing_target() {
    let dir = tempdir().expect("Failed to create temp dir");
    let file_path = dir.path().join("text.txt");
    fs::write(&file_path, "hello world").expect("Failed to write file");

    let params = EditParams {
        target_file: file_path.to_string_lossy().to_string(),
        target_content: "nonexistent".to_string(),
        replacement_content: "replacement".to_string(),
        allow_multiple: false,
        verify_command: None,
        rollback_on_failure: true,
        cwd: None,
    };

    let report = edit_and_verify(&params);
    assert!(!report.success);
    assert!(report.message.contains("Target content not found"));
}

#[test]
fn test_git_snapshot_contract() {
    let dir = tempdir().expect("Failed to create temp dir");
    let repo_path = dir.path().to_string_lossy().to_string();

    // Initialize git repo in tempdir
    std::process::Command::new("git")
        .args(["-C", &repo_path, "init", "-b", "main"])
        .output()
        .expect("git init failed");

    let snap = get_working_tree_snapshot(&repo_path).expect("get_working_tree_snapshot failed");
    assert_eq!(snap.branch, "main");
    assert!(snap.clean);
}

#[test]
fn test_hook_router_pre_tool_contract() {
    let raw_input = json!({
        "toolCall": {
            "name": "run_command",
            "args": {
                "CommandLine": "git status"
            }
        },
        "stepIdx": 4
    }).to_string();

    let response = route_pre_tool(&raw_input);
    assert_eq!(response.decision, "allow");
    assert!(response.overwrite.is_some());

    let overwrite = response.overwrite.unwrap();
    let new_cmd = overwrite.get("CommandLine").and_then(|v| v.as_str()).unwrap();
    assert_eq!(new_cmd, "fast-tools git-snapshot");
}

#[test]
fn test_hook_router_intercepts_cat() {
    let raw_input = json!({
        "toolCall": {
            "name": "run_command",
            "args": {
                "CommandLine": "cat src/lib.rs"
            }
        },
        "stepIdx": 10
    }).to_string();

    let response = route_pre_tool(&raw_input);
    assert_eq!(response.decision, "allow");
    assert!(response.overwrite.is_some());
    let new_cmd = response.overwrite.unwrap().get("CommandLine").and_then(|v| v.as_str()).unwrap().to_string();
    assert_eq!(new_cmd, "fast-tools view src/lib.rs");
}

#[test]
fn test_hook_router_intercepts_lsof() {
    let raw_input = json!({
        "toolCall": {
            "name": "run_command",
            "args": {
                "CommandLine": "lsof -p 12345"
            }
        },
        "stepIdx": 11
    }).to_string();

    let response = route_pre_tool(&raw_input);
    assert_eq!(response.decision, "allow");
    assert!(response.overwrite.is_some());
    let new_cmd = response.overwrite.unwrap().get("CommandLine").and_then(|v| v.as_str()).unwrap().to_string();
    assert_eq!(new_cmd, "fast-tools poll --pid 12345 --state alive --timeout 5000");
}

#[test]
fn test_hook_router_intercepts_os_kill_python() {
    let raw_input = json!({
        "toolCall": {
            "name": "run_command",
            "args": {
                "CommandLine": "python3 -c \"import os; os.kill(7788, 0)\""
            }
        },
        "stepIdx": 12
    }).to_string();

    let response = route_pre_tool(&raw_input);
    assert_eq!(response.decision, "allow");
    assert!(response.overwrite.is_some());
    let new_cmd = response.overwrite.unwrap().get("CommandLine").and_then(|v| v.as_str()).unwrap().to_string();
    assert_eq!(new_cmd, "fast-tools poll --pid 7788 --state alive --timeout 5000");
}
