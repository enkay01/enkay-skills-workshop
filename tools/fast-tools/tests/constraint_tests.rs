use fast_tools::edit::{edit_and_verify, EditParams};
use fast_tools::git::get_working_tree_snapshot;
use fast_tools::grep::{clamp_range, search_context};
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

#[test]
fn test_view_start_past_preview_cap() {
    let dir = tempdir().expect("Failed to create temp dir");
    let file_path = dir.path().join("long.txt");
    let content = (1..=6000).map(|i| format!("line {}", i)).collect::<Vec<_>>().join("\n");
    fs::write(&file_path, content).expect("Failed to write file");

    let opts = ViewOptions {
        start_line: Some(3000),
        end_line: None,
        line_numbers: true,
        ..Default::default()
    };

    let result = view_single_file(&file_path, &opts).expect("view_single_file failed");
    assert!(result.contains("3000: line 3000"));
    assert!(result.contains("3050: line 3050"));
    assert!(result.contains("3999: line 3999"));
    assert!(!result.contains("4000: line 4000"));
    assert!(!result.contains("[Showing first 1000 lines"));
    assert!(result.contains("[Showing lines 3000-3999 of 6000 lines (capped preview)."));
}

#[test]
fn test_view_start_at_beginning_on_large_file_is_capped() {
    let dir = tempdir().expect("Failed to create temp dir");
    let file_path = dir.path().join("huge.txt");
    let content = (1..=6000).map(|i| format!("line {}", i)).collect::<Vec<_>>().join("\n");
    fs::write(&file_path, content).expect("Failed to write file");

    let opts = ViewOptions {
        start_line: Some(1),
        end_line: None,
        line_numbers: true,
        ..Default::default()
    };

    let result = view_single_file(&file_path, &opts).expect("view_single_file failed");
    assert!(result.contains("   1: line 1"));
    assert!(result.contains("1000: line 1000"));
    assert!(!result.contains("1001: line 1001"));
    assert!(result.contains("[Showing first 1000 lines of 6000 lines."));
}

#[test]
fn test_view_batch_enforces_byte_budget() {
    let dir = tempdir().expect("Failed to create temp dir");
    let file_a = dir.path().join("a.rs");
    let file_b = dir.path().join("b.rs");
    let code_a = "pub fn function_a() {\n    println!(\"hello\");\n}\n".repeat(20);
    let code_b = "pub fn function_b() {\n    println!(\"world\");\n}\n".repeat(20);
    fs::write(&file_a, code_a).expect("Failed to write a");
    fs::write(&file_b, code_b).expect("Failed to write b");

    // Very small budget: 200 bytes
    let opts = ViewOptions::default();
    let batch_output = view_batch_files(&[file_a, file_b], &opts, 200);
    assert!(batch_output.contains("[Aggregate budget reached; displaying outline]"));
}

#[test]
fn test_grep_clamp_range_overflow_safety() {
    // Huge context value must not overflow match_line + context
    let (start, end) = clamp_range(5, usize::MAX, 10);
    assert_eq!(start, 1);
    assert_eq!(end, 10);
}

#[test]
fn test_grep_symlink_recursion_safety() {
    let dir = tempdir().expect("Failed to create temp dir");
    let sub = dir.path().join("subdir");
    fs::create_dir(&sub).expect("Failed to create subdir");
    let sample = sub.join("sample.txt");
    fs::write(&sample, "target string inside file").expect("Failed to write sample");

    // Create a symlink loop: subdir/loop -> dir
    #[cfg(unix)]
    {
        use std::os::unix::fs::symlink;
        let loop_link = sub.join("loop");
        let _ = symlink(dir.path(), &loop_link);
    }

    let matches = search_context(
        &[dir.path().to_path_buf()],
        "target string",
        2,
        5,
        false,
        false,
    ).expect("search_context must succeed without infinite recursion");

    assert_eq!(matches.len(), 1);
}

#[test]
fn test_poll_service_rejects_empty_config() {
    let config = PollConfig {
        pid: None,
        port: None,
        state: TargetState::Ready,
        timeout_ms: 100,
        interval_ms: 10,
    };

    let report = poll_service(&config);
    assert!(!report.success);
    assert!(report.message.contains("Neither pid nor port"));
}

#[test]
fn test_edit_and_verify_no_command_reports_unverified() {
    let dir = tempdir().expect("Failed to create temp dir");
    let file_path = dir.path().join("plain.txt");
    fs::write(&file_path, "before edit").expect("Failed to write file");

    let params = EditParams {
        target_file: file_path.to_string_lossy().to_string(),
        target_content: "before".to_string(),
        replacement_content: "after".to_string(),
        allow_multiple: false,
        verify_command: None,
        rollback_on_failure: false,
        cwd: None,
    };

    let report = edit_and_verify(&params);
    assert!(report.success);
    assert!(!report.verified); // verified must be false when no verify_command executed
}

#[test]
fn test_hook_router_preserves_chained_and_redirected_commands() {
    // cat with redirection
    let input_redirect = json!({
        "toolCall": {
            "name": "run_command",
            "args": { "CommandLine": "cat < README.md" }
        }
    }).to_string();
    assert!(route_pre_tool(&input_redirect).overwrite.is_none());

    // cat with here-doc
    let input_heredoc = json!({
        "toolCall": {
            "name": "run_command",
            "args": { "CommandLine": "cat <<EOF\nhello\nEOF" }
        }
    }).to_string();
    assert!(route_pre_tool(&input_heredoc).overwrite.is_none());

    // lsof in pipeline
    let input_lsof_pipe = json!({
        "toolCall": {
            "name": "run_command",
            "args": { "CommandLine": "lsof -p 1234 | grep LISTEN" }
        }
    }).to_string();
    assert!(route_pre_tool(&input_lsof_pipe).overwrite.is_none());

    // python os.kill chained with &&
    let input_py_chained = json!({
        "toolCall": {
            "name": "run_command",
            "args": { "CommandLine": "python3 -c \"import os; os.kill(1234, 0); print(1)\" && echo done" }
        }
    }).to_string();
    assert!(route_pre_tool(&input_py_chained).overwrite.is_none());

    // python with literal print mentioning os.kill
    let input_py_literal = json!({
        "toolCall": {
            "name": "run_command",
            "args": { "CommandLine": "python3 -c \"print('os.kill(1234, 0)')\"" }
        }
    }).to_string();
    assert!(route_pre_tool(&input_py_literal).overwrite.is_none());
}

#[test]
fn test_style_em_dash_auto_fixing() {
    let dir = tempdir().expect("Failed to create temp dir");
    let file_path = dir.path().join("report.md");
    fs::write(&file_path, "The release was fast — really fast — and reliable.").expect("Failed to write file");

    let report = fast_tools::style::check_and_fix_file(&file_path, true).expect("check_and_fix_file failed");
    assert_eq!(report.em_dashes_fixed, 2);
    assert!(report.is_clean());

    let updated = fs::read_to_string(&file_path).expect("Failed to read file");
    assert_eq!(updated, "The release was fast - really fast - and reliable.");
}

#[test]
fn test_style_banned_words_detection() {
    let dir = tempdir().expect("Failed to create temp dir");
    let file_path = dir.path().join("draft.md");
    let bad_text = "This is a crucial fix.\nAdditionally, we delve into the system.\nIt is not just fast, but reliable.\n";
    fs::write(&file_path, bad_text).expect("Failed to write file");

    let report = fast_tools::style::check_and_fix_file(&file_path, false).expect("check_and_fix_file failed");
    assert!(!report.is_clean());
    let rules: Vec<_> = report.violations.iter().map(|v| v.rule.as_str()).collect();
    assert!(rules.contains(&"banned-word"));
    assert!(rules.contains(&"banned-construction"));
}

#[test]
fn test_style_ignores_code_blocks() {
    let content = "Normal text here.\n```rust\nlet crucial = 42;\n// not just x, but y\n```\nAll clean afterwards.\n";
    let (_, fixed, violations) = fast_tools::style::check_and_fix_content(content, false);
    assert_eq!(fixed, 0);
    assert!(violations.is_empty(), "Violations inside code blocks must be ignored");
}

#[test]
fn test_hook_stop_blocks_violations_and_allows_clean() {
    let dir = tempdir().expect("Failed to create temp dir");
    let bad_file = dir.path().join("artifact.md");
    fs::write(&bad_file, "This is a pivotal finding.\n").expect("Failed to write bad file");

    let stop_input = json!({
        "executionNum": 1,
        "terminationReason": "model_stop",
        "artifactDirectoryPath": dir.path().to_string_lossy().to_string()
    }).to_string();

    let stop_resp = fast_tools::hooks::route_stop_hook(&stop_input);
    assert_eq!(stop_resp.decision, "continue");
    assert!(stop_resp.reason.unwrap().contains("pivotal"));

    // Fix the file
    fs::write(&bad_file, "This is an important finding.\n").expect("Failed to write clean file");
    let clean_resp = fast_tools::hooks::route_stop_hook(&stop_input);
    assert_eq!(clean_resp.decision, "allow");
    assert!(clean_resp.reason.is_none());
}
