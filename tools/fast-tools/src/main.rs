use clap::{Parser, Subcommand};
use fast_tools::edit::{edit_and_verify, EditParams};
use fast_tools::git::get_working_tree_snapshot;
use fast_tools::grep::search_context;
use fast_tools::hooks::{route_post_tool, route_pre_tool};
use fast_tools::mcp::run_mcp_server;
use fast_tools::poll::{poll_service, PollConfig, TargetState};
use fast_tools::view::{view_batch_files, view_single_file, ViewOptions};
use std::io::{self, Read};
use std::path::PathBuf;
use std::process;

#[derive(Parser)]
#[command(name = "fast-tools")]
#[command(about = "Deterministic developer tools, batch file reader, and Antigravity lifecycle hooks", version = "0.1.0")]
struct Cli {
    #[arg(long, help = "Run in Model Context Protocol (MCP) stdio server mode")]
    mcp: bool,

    #[command(subcommand)]
    command: Option<Commands>,
}

#[derive(Subcommand)]
enum Commands {
    #[command(about = "Read one or more files in a single pass with line numbers and safety caps")]
    View {
        #[arg(required = true, help = "File path(s) to view")]
        paths: Vec<PathBuf>,

        #[arg(short = 's', long, help = "1-indexed start line (single file only)")]
        start_line: Option<usize>,

        #[arg(short = 'e', long, help = "1-indexed end line (single file only)")]
        end_line: Option<usize>,

        #[arg(long, help = "Disable line numbers")]
        no_numbers: bool,

        #[arg(short = 'o', long, help = "Show symbol outline instead of full content")]
        outline: bool,

        #[arg(long, help = "Force display even if exceeding line caps or minified guards")]
        force_all: bool,
    },

    #[command(about = "Search for pattern with boundary-clamped surrounding context lines")]
    Grep {
        #[arg(help = "Pattern to search for")]
        pattern: String,

        #[arg(required = true, help = "Target file(s) or directory paths")]
        paths: Vec<PathBuf>,

        #[arg(short = 'c', long, default_value_t = 10, help = "Number of context lines surrounding each match")]
        context: usize,

        #[arg(short = 'm', long, default_value_t = 10, help = "Maximum number of matches to return")]
        max_matches: usize,

        #[arg(long, help = "Treat pattern as regular expression")]
        regex: bool,

        #[arg(short = 's', long, help = "Match case sensitively")]
        case_sensitive: bool,

        #[arg(long, help = "Output as structured JSON")]
        json: bool,
    },

    #[command(about = "Poll process status or port availability with timeout")]
    Poll {
        #[arg(long, help = "Process ID to check")]
        pid: Option<i32>,

        #[arg(long, help = "TCP port to check on localhost")]
        port: Option<u16>,

        #[arg(long, default_value = "ready", help = "Expected state: alive, terminated, or ready")]
        state: String,

        #[arg(long, default_value_t = 5000, help = "Timeout in milliseconds")]
        timeout: u64,

        #[arg(long, default_value_t = 200, help = "Poll interval in milliseconds")]
        interval: u64,
    },

    #[command(about = "Atomically edit a file and run a verification command, with rollback on failure")]
    Edit {
        #[arg(help = "Target file path")]
        target_file: String,

        #[arg(help = "Target string to replace")]
        target_content: String,

        #[arg(help = "Replacement string")]
        replacement_content: String,

        #[arg(long, help = "Allow replacing all occurrences if target occurs multiple times")]
        allow_multiple: bool,

        #[arg(long, help = "Shell verification command to run (e.g. cargo check, npx oxlint)")]
        verify: Option<String>,

        #[arg(long, help = "Do not roll back changes if verification command fails")]
        no_rollback: bool,

        #[arg(long, help = "Working directory for verification command")]
        cwd: Option<String>,
    },

    #[command(about = "Get instantaneous working tree status, current branch, and diff summary")]
    GitSnapshot {
        #[arg(default_value = ".", help = "Path to git repository")]
        repo: String,
    },

    #[command(about = "Antigravity PreToolUse lifecycle hook handler (reads stdin, outputs JSON)")]
    HookPre,

    #[command(about = "Antigravity PostToolUse lifecycle hook handler (reads stdin, outputs JSON)")]
    HookPost,
}

fn main() {
    let cli = Cli::parse();

    if cli.mcp {
        if let Err(e) = run_mcp_server() {
            eprintln!("MCP server error: {}", e);
            process::exit(1);
        }
        return;
    }

    match cli.command {
        Some(Commands::View {
            paths,
            start_line,
            end_line,
            no_numbers,
            outline,
            force_all,
        }) => {
            let opts = ViewOptions {
                start_line,
                end_line,
                line_numbers: !no_numbers,
                outline,
                force_all,
            };

            if paths.len() == 1 {
                match view_single_file(&paths[0], &opts) {
                    Ok(text) => print!("{}", text),
                    Err(e) => {
                        eprintln!("Error: {}", e);
                        process::exit(1);
                    }
                }
            } else {
                let out = view_batch_files(&paths, &opts, 500 * 1024);
                print!("{}", out);
            }
        }

        Some(Commands::Grep {
            pattern,
            paths,
            context,
            max_matches,
            regex,
            case_sensitive,
            json,
        }) => {
            match search_context(&paths, &pattern, context, max_matches, regex, case_sensitive) {
                Ok(matches) => {
                    if json {
                        println!("{}", serde_json::to_string_pretty(&matches).unwrap_or_default());
                    } else if matches.is_empty() {
                        println!("No matches found for '{}'", pattern);
                    } else {
                        for m in matches {
                            println!("=== Match in {}:{} (lines {}-{}) ===", m.path, m.line_number, m.start_line, m.end_line);
                            println!("{}\n", m.snippet);
                        }
                    }
                }
                Err(e) => {
                    eprintln!("Error: {}", e);
                    process::exit(1);
                }
            }
        }

        Some(Commands::Poll {
            pid,
            port,
            state,
            timeout,
            interval,
        }) => {
            let target_state = match state.as_str() {
                "alive" => TargetState::Alive,
                "terminated" => TargetState::Terminated,
                _ => TargetState::Ready,
            };
            let config = PollConfig {
                pid,
                port,
                state: target_state,
                timeout_ms: timeout,
                interval_ms: interval,
            };
            let report = poll_service(&config);
            println!("{}", report.message);
            if !report.success {
                process::exit(1);
            }
        }

        Some(Commands::Edit {
            target_file,
            target_content,
            replacement_content,
            allow_multiple,
            verify,
            no_rollback,
            cwd,
        }) => {
            let params = EditParams {
                target_file,
                target_content,
                replacement_content,
                allow_multiple,
                verify_command: verify,
                rollback_on_failure: !no_rollback,
                cwd,
            };
            let report = edit_and_verify(&params);
            println!("{}", report.message);
            if !report.verify_output.is_empty() {
                println!("\nVerification output:\n{}", report.verify_output);
            }
            if !report.success {
                process::exit(1);
            }
        }

        Some(Commands::GitSnapshot { repo }) => {
            match get_working_tree_snapshot(&repo) {
                Ok(snap) => {
                    println!("Branch: {}", snap.branch);
                    println!("Clean: {}", snap.clean);
                    if !snap.status_lines.is_empty() {
                        println!("\nStatus ({} modified/untracked files):", snap.status_lines.len());
                        for l in &snap.status_lines {
                            println!("  {}", l);
                        }
                    }
                    if !snap.diff_stat.is_empty() {
                        println!("\nDiff summary:\n{}", snap.diff_stat);
                    }
                }
                Err(e) => {
                    eprintln!("Error: {}", e);
                    process::exit(1);
                }
            }
        }

        Some(Commands::HookPre) => {
            let mut input = String::new();
            let _ = io::stdin().read_to_string(&mut input);
            let resp = route_pre_tool(&input);
            println!("{}", serde_json::to_string(&resp).unwrap_or_default());
        }

        Some(Commands::HookPost) => {
            let mut input = String::new();
            let _ = io::stdin().read_to_string(&mut input);
            let resp = route_post_tool(&input);
            println!("{}", serde_json::to_string(&resp).unwrap_or_default());
        }

        None => {
            eprintln!("No command provided. Run fast-tools --help or fast-tools --mcp");
            process::exit(1);
        }
    }
}
