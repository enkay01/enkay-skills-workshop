use fast_tools::edit::{edit_and_verify, EditParams};
use fast_tools::grep::search_context;
use fast_tools::hooks::route_pre_tool;
use fast_tools::mcp::handle_mcp_request;
use fast_tools::poll::{poll_service, PollConfig, TargetState};
use fast_tools::view::{view_single_file, ViewOptions};
use serde_json::json;
use std::fs;
use tempfile::tempdir;

#[test]
fn throwaway_test_zero_byte_file() {
    let dir = tempdir().unwrap();
    let p = dir.path().join("empty.txt");
    fs::write(&p, "").unwrap();

    let v = view_single_file(&p, &ViewOptions::default()).unwrap();
    assert_eq!(v, "");

    let g = search_context(&[p], "needle", 10, 5, false, false).unwrap();
    assert_eq!(g.len(), 0);
}

#[test]
fn throwaway_test_massive_context_overread_one_liner() {
    let dir = tempdir().unwrap();
    let p = dir.path().join("oneliner.txt");
    fs::write(&p, "single solitary line with target").unwrap();

    let g = search_context(&[p], "target", 999999, 10, false, false).unwrap();
    assert_eq!(g.len(), 1);
    assert_eq!(g[0].start_line, 1);
    assert_eq!(g[0].end_line, 1);
}

#[test]
fn throwaway_test_minified_guard_and_force_bypass() {
    let dir = tempdir().unwrap();
    let p = dir.path().join("bundle.min.js");
    let dense_line = "a".repeat(4000);
    fs::write(&p, dense_line).unwrap();

    // Default opts should trigger minified guard
    let guarded = view_single_file(&p, &ViewOptions::default()).unwrap();
    assert!(guarded.contains("[Minified Line Guard:"));

    // force_all must bypass guard
    let forced = view_single_file(&p, &ViewOptions { force_all: true, ..Default::default() }).unwrap();
    assert!(forced.contains("1: aaaaa"));
}

#[test]
fn throwaway_test_binary_file_detection() {
    let dir = tempdir().unwrap();
    let p = dir.path().join("data.bin");
    fs::write(&p, b"\x7fELF\x02\x01\x01\x00\x00\x00\x00\x00").unwrap();

    let v = view_single_file(&p, &ViewOptions::default()).unwrap();
    assert!(v.contains("[Binary file:"));
}

#[test]
fn throwaway_test_nonexistent_file_errors() {
    let dir = tempdir().unwrap();
    let p = dir.path().join("ghost_file.txt");

    let err = view_single_file(&p, &ViewOptions::default()).unwrap_err();
    assert!(err.contains("File not found"));

    let edit_rep = edit_and_verify(&EditParams {
        target_file: p.to_string_lossy().to_string(),
        target_content: "foo".to_string(),
        replacement_content: "bar".to_string(),
        allow_multiple: false,
        verify_command: None,
        rollback_on_failure: true,
        cwd: None,
    });
    assert!(!edit_rep.success);
    assert!(edit_rep.message.contains("does not exist"));
}

#[test]
fn throwaway_test_invalid_regex_handling() {
    let dir = tempdir().unwrap();
    let p = dir.path().join("sample.txt");
    fs::write(&p, "hello").unwrap();

    let err = search_context(&[p], "[a-z(broken_regex", 5, 5, true, false).unwrap_err();
    assert!(err.contains("Invalid regex"));
}

#[test]
fn throwaway_test_poll_service_zero_timeout_and_interval() {
    let config = PollConfig {
        pid: Some(9999999),
        port: None,
        state: TargetState::Alive,
        timeout_ms: 0,
        interval_ms: 0,
    };
    let report = poll_service(&config);
    assert!(!report.success);
    assert!(report.message.contains("Timeout"));
}

#[test]
fn throwaway_test_edit_multiple_occurrences_blocked_unless_allowed() {
    let dir = tempdir().unwrap();
    let p = dir.path().join("dup.txt");
    fs::write(&p, "word word word").unwrap();

    let blocked = edit_and_verify(&EditParams {
        target_file: p.to_string_lossy().to_string(),
        target_content: "word".to_string(),
        replacement_content: "token".to_string(),
        allow_multiple: false,
        verify_command: None,
        rollback_on_failure: false,
        cwd: None,
    });
    assert!(!blocked.success);
    assert!(blocked.message.contains("found 3 times"));

    let allowed = edit_and_verify(&EditParams {
        target_file: p.to_string_lossy().to_string(),
        target_content: "word".to_string(),
        replacement_content: "token".to_string(),
        allow_multiple: true,
        verify_command: None,
        rollback_on_failure: false,
        cwd: None,
    });
    assert!(allowed.success);
    assert_eq!(fs::read_to_string(&p).unwrap(), "token token token");
}

#[test]
fn throwaway_test_hook_router_garbage_json() {
    let resp = route_pre_tool("not_valid_json!@#$");
    assert_eq!(resp.decision, "allow");
    assert!(resp.overwrite.is_none());

    let resp_empty = route_pre_tool("{}");
    assert_eq!(resp_empty.decision, "allow");
}

#[test]
fn throwaway_test_large_file_line_capping() {
    let dir = tempdir().unwrap();
    let p = dir.path().join("big.txt");
    let content = (1..=6000).map(|i| format!("line {}", i)).collect::<Vec<_>>().join("\n");
    fs::write(&p, content).unwrap();

    let capped = view_single_file(&p, &ViewOptions::default()).unwrap();
    assert!(capped.contains("[Showing first 1000 lines of 6000 lines."));
}

#[test]
fn throwaway_test_mcp_unknown_tool_returns_error() {
    let req = json!({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "unknown_magic_tool",
            "arguments": {}
        }
    });

    let resp = handle_mcp_request(&req).expect("Expected response");
    assert!(resp.get("error").is_some());
    let err = resp.get("error").unwrap();
    assert_eq!(err.get("code").and_then(|c| c.as_i64()), Some(-32602));
    assert!(err.get("message").and_then(|m| m.as_str()).unwrap().contains("Unknown tool"));
}

#[test]
fn throwaway_test_mcp_tool_failure_sets_is_error() {
    let req = json!({
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "edit_and_verify",
            "arguments": {
                "target_file": "/nonexistent/path/for/sure.txt",
                "target_content": "foo",
                "replacement_content": "bar"
            }
        }
    });

    let resp = handle_mcp_request(&req).expect("Expected response");
    let result = resp.get("result").expect("Expected result object");
    assert_eq!(result.get("isError").and_then(|v| v.as_bool()), Some(true));
}

#[test]
fn throwaway_test_mcp_port_overflow_handled() {
    let req = json!({
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": "poll_service",
            "arguments": {
                "port": 70000
            }
        }
    });

    let resp = handle_mcp_request(&req).expect("Expected response");
    let result = resp.get("result").expect("Expected result object");
    assert_eq!(result.get("isError").and_then(|v| v.as_bool()), Some(true));
    let text = result.get("content").unwrap().as_array().unwrap()[0].get("text").unwrap().as_str().unwrap();
    assert!(text.contains("out of range for u16"));
}
