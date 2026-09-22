use clap::Parser;
use fast_tools::mcp::run_mcp_server;
use fast_tools::view::{view_batch_files, view_single_file, ViewOptions};
use std::path::PathBuf;
use std::process;

#[derive(Parser)]
#[command(name = "view-file")]
#[command(about = "High-performance whole-file reader and stdio MCP server", version = "0.1.0")]
struct Cli {
    #[arg(help = "Path to file(s) to view")]
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

    #[arg(long, help = "Run in Model Context Protocol (MCP) stdio server mode")]
    mcp: bool,
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

    if cli.paths.is_empty() {
        eprintln!("Error: No path provided. Run view-file --help or view-file --mcp");
        process::exit(1);
    }

    let opts = ViewOptions {
        start_line: cli.start_line,
        end_line: cli.end_line,
        line_numbers: !cli.no_numbers,
        outline: cli.outline,
        force_all: cli.force_all,
    };

    if cli.paths.len() == 1 {
        match view_single_file(&cli.paths[0], &opts) {
            Ok(text) => print!("{}", text),
            Err(e) => {
                eprintln!("Error: {}", e);
                process::exit(1);
            }
        }
    } else {
        let out = view_batch_files(&cli.paths, &opts, 500 * 1024);
        print!("{}", out);
    }
}
