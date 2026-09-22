use regex::RegexBuilder;
use serde::{Deserialize, Serialize};
use std::collections::HashSet;
use std::fs::File;
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GrepMatch {
    pub path: String,
    pub line_number: usize,
    pub start_line: usize,
    pub end_line: usize,
    pub snippet: String,
}

pub fn clamp_range(match_line: usize, context: usize, total_lines: usize) -> (usize, usize) {
    if total_lines == 0 {
        return (1, 1);
    }
    let start = match_line.saturating_sub(context).max(1);
    let end = match_line.saturating_add(context).min(total_lines);
    (start, end)
}

fn collect_files_recursive(dir: &Path, files: &mut Vec<PathBuf>, visited: &mut HashSet<PathBuf>) {
    let canonical = match dir.canonicalize() {
        Ok(c) => c,
        Err(_) => return,
    };
    if !visited.insert(canonical) {
        return;
    }
    if let Ok(entries) = std::fs::read_dir(dir) {
        for entry in entries.flatten() {
            let Ok(file_type) = entry.file_type() else { continue; };
            if file_type.is_symlink() {
                if let Ok(target_meta) = std::fs::metadata(entry.path()) {
                    if target_meta.is_file() {
                        files.push(entry.path());
                    }
                }
            } else if file_type.is_dir() {
                let p = entry.path();
                let name = p.file_name().and_then(|s| s.to_str()).unwrap_or("");
                if !name.starts_with('.') && name != "target" && name != "node_modules" {
                    collect_files_recursive(&p, files, visited);
                }
            } else if file_type.is_file() {
                files.push(entry.path());
            }
        }
    }
}

pub fn search_context(
    targets: &[PathBuf],
    pattern: &str,
    context_lines: usize,
    max_matches: usize,
    is_regex: bool,
    case_sensitive: bool,
) -> Result<Vec<GrepMatch>, String> {
    let regex = if is_regex {
        RegexBuilder::new(pattern)
            .case_insensitive(!case_sensitive)
            .build()
            .map_err(|e| format!("Invalid regex '{}': {}", pattern, e))?
    } else {
        let escaped = regex::escape(pattern);
        RegexBuilder::new(&escaped)
            .case_insensitive(!case_sensitive)
            .build()
            .map_err(|e| format!("Failed to build matcher: {}", e))?
    };

    let mut file_list = Vec::new();
    let mut visited = HashSet::new();
    for t in targets {
        if t.is_dir() {
            collect_files_recursive(t, &mut file_list, &mut visited);
        } else if t.is_file() {
            file_list.push(t.clone());
        }
    }

    let mut matches = Vec::new();

    for file_path in &file_list {
        if matches.len() >= max_matches {
            break;
        }

        let file = match File::open(file_path) {
            Ok(f) => f,
            Err(_) => continue,
        };

        let reader = BufReader::new(file);
        let lines: Vec<String> = match reader.lines().collect::<Result<_, _>>() {
            Ok(l) => l,
            Err(_) => continue,
        };

        let total_lines = lines.len();

        for (idx, line) in lines.iter().enumerate() {
            if matches.len() >= max_matches {
                break;
            }

            if regex.is_match(line) {
                let line_num = idx + 1;
                let (start, end) = clamp_range(line_num, context_lines, total_lines);

                let pad_width = format!("{}", end).len().max(4);
                let snippet = lines[start - 1..end]
                    .iter()
                    .enumerate()
                    .map(|(offset, text)| {
                        let cur_line = start + offset;
                        let marker = if cur_line == line_num { ">" } else { " " };
                        format!("{}{:width$}: {}", marker, cur_line, text, width = pad_width)
                    })
                    .collect::<Vec<_>>()
                    .join("\n");

                matches.push(GrepMatch {
                    path: file_path.to_string_lossy().to_string(),
                    line_number: line_num,
                    start_line: start,
                    end_line: end,
                    snippet,
                });
            }
        }
    }

    Ok(matches)
}
