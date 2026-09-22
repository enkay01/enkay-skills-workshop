use std::fs::File;
use std::io::{BufRead, BufReader, Read};
use std::path::{Path, PathBuf};

pub const DEFAULT_MAX_LINES: usize = 5000;
pub const DEFAULT_MAX_BYTES: usize = 500 * 1024;
pub const CAPPED_PREVIEW_LINES: usize = 1000;
pub const MAX_LINE_CHAR_THRESHOLD: usize = 2000;
pub const AVG_LINE_CHAR_THRESHOLD: usize = 400;

#[derive(Debug, Clone)]
pub struct ViewOptions {
    pub start_line: Option<usize>,
    pub end_line: Option<usize>,
    pub line_numbers: bool,
    pub outline: bool,
    pub force_all: bool,
}

impl Default for ViewOptions {
    fn default() -> Self {
        Self {
            start_line: None,
            end_line: None,
            line_numbers: true,
            outline: false,
            force_all: false,
        }
    }
}

pub fn is_binary_file(path: &Path) -> bool {
    let mut file = match File::open(path) {
        Ok(f) => f,
        Err(_) => return false,
    };
    let mut buffer = [0u8; 1024];
    let bytes_read = match file.read(&mut buffer) {
        Ok(n) => n,
        Err(_) => return false,
    };
    buffer[..bytes_read].contains(&0)
}

pub fn extract_outline(lines: &[String]) -> Vec<(usize, String)> {
    let mut outline = Vec::new();
    let keywords = [
        "fn ", "pub fn ", "async fn ", "pub async fn ",
        "function ", "export function ", "export async function ",
        "class ", "export class ", "struct ", "pub struct ",
        "enum ", "pub enum ", "trait ", "pub trait ",
        "interface ", "export interface ", "type ", "export type ",
        "impl ", "def ", "class "
    ];

    for (idx, line) in lines.iter().enumerate() {
        let trimmed = line.trim();
        for kw in &keywords {
            if trimmed.starts_with(kw) {
                outline.push((idx + 1, trimmed.to_string()));
                break;
            }
        }
    }
    outline
}

pub fn view_single_file(path: &Path, opts: &ViewOptions) -> Result<String, String> {
    if !path.exists() {
        return Err(format!("File not found: {}", path.display()));
    }
    if is_binary_file(path) {
        let meta = std::fs::metadata(path).map_err(|e| e.to_string())?;
        return Ok(format!(
            "[Binary file: {} ({} bytes)]",
            path.display(),
            meta.len()
        ));
    }

    let file = File::open(path).map_err(|e| e.to_string())?;
    let reader = BufReader::new(file);
    let all_lines: Vec<String> = reader.lines().collect::<Result<_, _>>().map_err(|e| e.to_string())?;
    let total_lines = all_lines.len();

    if total_lines == 0 {
        return Ok(String::new());
    }

    if opts.outline {
        let outline = extract_outline(&all_lines);
        if outline.is_empty() {
            return Ok(format!("File {} ({} lines): No top-level declarations found.", path.display(), total_lines));
        }
        let mut out = format!("Outline for {} ({} lines):\n", path.display(), total_lines);
        for (line_no, decl) in outline {
            out.push_str(&format!("{:5}: {}\n", line_no, decl));
        }
        return Ok(out);
    }

    // Minified guard
    if !opts.force_all && total_lines > 0 {
        let total_chars: usize = all_lines.iter().map(|l| l.len()).sum();
        let max_line_len = all_lines.iter().map(|l| l.len()).max().unwrap_or(0);
        let avg_line_len = total_chars / total_lines;

        if max_line_len > MAX_LINE_CHAR_THRESHOLD || avg_line_len > AVG_LINE_CHAR_THRESHOLD {
            return Ok(format!(
                "[Minified Line Guard: {} lines, max line length {}, average line length {}. File appears minified or bundled. Pass --force-all to bypass.]",
                total_lines, max_line_len, avg_line_len
            ));
        }
    }

    // Line cap
    let should_cap = !opts.force_all && total_lines > DEFAULT_MAX_LINES && opts.start_line.is_none() && opts.end_line.is_none();
    let max_render_line = if should_cap { CAPPED_PREVIEW_LINES } else { total_lines };

    let start = opts.start_line.unwrap_or(1).max(1);
    let end = opts.end_line.unwrap_or(max_render_line).min(total_lines);

    if start > total_lines && total_lines > 0 {
        return Err(format!("start_line {} exceeds total lines {}", start, total_lines));
    }
    if start > end {
        return Err(format!("start_line {} is greater than end_line {}", start, end));
    }

    let mut output = String::new();
    let pad_width = format!("{}", total_lines).len().max(4);

    for line_idx in start..=end {
        if line_idx == 0 || line_idx > total_lines {
            continue;
        }
        let line_content = &all_lines[line_idx - 1];
        if opts.line_numbers {
            output.push_str(&format!("{:width$}: {}\n", line_idx, line_content, width = pad_width));
        } else {
            output.push_str(line_content);
            output.push('\n');
        }
    }

    if should_cap && opts.end_line.is_none() {
        output.push_str(&format!(
            "\n[Showing first {} lines of {} lines. Pass --force-all to read entire file or specify -s and -e]\n",
            CAPPED_PREVIEW_LINES, total_lines
        ));
    }

    Ok(output)
}

pub fn view_batch_files(paths: &[PathBuf], opts: &ViewOptions, max_aggregate_bytes: usize) -> String {
    let mut out = String::new();
    let mut total_bytes = 0;

    for (idx, p) in paths.iter().enumerate() {
        out.push_str(&format!("=== File [{}/{}]: {} ===\n", idx + 1, paths.len(), p.display()));

        if total_bytes >= max_aggregate_bytes {
            // Degrade to outline to protect context window
            let mut outline_opts = opts.clone();
            outline_opts.outline = true;
            match view_single_file(p, &outline_opts) {
                Ok(content) => {
                    total_bytes += content.len();
                    out.push_str(&format!("[Aggregate budget reached; displaying outline]\n{}\n\n", content));
                }
                Err(err) => {
                    out.push_str(&format!("Error reading {}: {}\n\n", p.display(), err));
                }
            }
            continue;
        }

        match view_single_file(p, opts) {
            Ok(content) => {
                if total_bytes + content.len() > max_aggregate_bytes && !opts.outline {
                    // Content exceeds aggregate budget; degrade to outline
                    let mut outline_opts = opts.clone();
                    outline_opts.outline = true;
                    match view_single_file(p, &outline_opts) {
                        Ok(outline_content) => {
                            total_bytes += outline_content.len();
                            out.push_str(&format!("[Aggregate budget reached; displaying outline]\n{}\n\n", outline_content));
                        }
                        Err(err) => {
                            out.push_str(&format!("Error reading {}: {}\n\n", p.display(), err));
                        }
                    }
                } else {
                    total_bytes += content.len();
                    out.push_str(&content);
                    out.push_str("\n\n");
                }
            }
            Err(err) => {
                out.push_str(&format!("Error reading {}: {}\n\n", p.display(), err));
            }
        }
    }
    out
}
