use regex::Regex;
use std::fs;
use std::io;
use std::path::Path;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StyleViolation {
    pub line_number: usize,
    pub rule: String,
    pub term: String,
    pub snippet: String,
}

#[derive(Debug, Clone, Default)]
pub struct StyleReport {
    pub file_path: String,
    pub em_dashes_fixed: usize,
    pub violations: Vec<StyleViolation>,
}

impl StyleReport {
    pub fn is_clean(&self) -> bool {
        self.violations.is_empty()
    }
}

pub fn get_banned_words_regex() -> &'static Regex {
    static REGEX: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    REGEX.get_or_init(|| {
        Regex::new(r"(?i)\b(delve|delves|delved|delving|tapestry|tapestries|testament|underscore|underscores|underscored|underscoring|pivotal|crucial|intricate|meticulous|vibrant|foster|fosters|fostered|fostering|garner|garners|garnered|garnering|bolster|bolsters|bolstered|bolstering|interplay|robust|enduring|showcase|showcases|showcased|showcasing|leverage|leverages|leveraged|leveraging|seamless)\b").expect("Valid regex")
    })
}

pub fn get_additionally_regex() -> &'static Regex {
    static REGEX: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    REGEX.get_or_init(|| {
        Regex::new(r"(?i)(?:^|[.!?]\s+)\badditionally\b").expect("Valid regex")
    })
}

pub fn get_not_just_regex() -> &'static Regex {
    static REGEX: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    REGEX.get_or_init(|| {
        Regex::new(r"(?i)\bnot\s+just\b.+\bbut\b").expect("Valid regex")
    })
}

pub fn get_its_not_regex() -> &'static Regex {
    static REGEX: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    REGEX.get_or_init(|| {
        Regex::new(r"(?i)\bit's\s+not\b.+\bit's\b").expect("Valid regex")
    })
}

pub fn check_and_fix_content(
    content: &str,
    auto_fix_em_dashes: bool,
) -> (String, usize, Vec<StyleViolation>) {
    let mut em_dashes_fixed = 0;
    let mut fixed_content = content.to_string();

    if auto_fix_em_dashes && content.contains('—') {
        em_dashes_fixed = content.matches('—').count();
        fixed_content = content.replace(" — ", " - ").replace('—', "-");
    }

    let banned_words_re = get_banned_words_regex();
    let additionally_re = get_additionally_regex();
    let not_just_re = get_not_just_regex();
    let its_not_re = get_its_not_regex();

    let mut violations = Vec::new();
    let mut in_code_block = false;

    for (line_idx, line) in fixed_content.lines().enumerate() {
        let line_number = line_idx + 1;
        let trimmed = line.trim_start();

        if trimmed.starts_with("```") || trimmed.starts_with("~~~") {
            in_code_block = !in_code_block;
            continue;
        }

        if in_code_block {
            continue;
        }

        if !auto_fix_em_dashes && line.contains('—') {
            violations.push(StyleViolation {
                line_number,
                rule: "em-dash".to_string(),
                term: "—".to_string(),
                snippet: line.trim().to_string(),
            });
        }

        for mat in banned_words_re.find_iter(line) {
            violations.push(StyleViolation {
                line_number,
                rule: "banned-word".to_string(),
                term: mat.as_str().to_string(),
                snippet: line.trim().to_string(),
            });
        }

        if additionally_re.is_match(line) {
            violations.push(StyleViolation {
                line_number,
                rule: "banned-construction".to_string(),
                term: "Additionally".to_string(),
                snippet: line.trim().to_string(),
            });
        }

        if not_just_re.is_match(line) {
            violations.push(StyleViolation {
                line_number,
                rule: "banned-construction".to_string(),
                term: "not just X, but Y".to_string(),
                snippet: line.trim().to_string(),
            });
        }

        if its_not_re.is_match(line) {
            violations.push(StyleViolation {
                line_number,
                rule: "banned-construction".to_string(),
                term: "it's not X, it's Y".to_string(),
                snippet: line.trim().to_string(),
            });
        }
    }

    (fixed_content, em_dashes_fixed, violations)
}

pub fn check_and_fix_file(path: &Path, auto_fix_em_dashes: bool) -> Result<StyleReport, io::Error> {
    let content = fs::read_to_string(path)?;
    let (fixed_content, em_dashes_fixed, violations) =
        check_and_fix_content(&content, auto_fix_em_dashes);

    if auto_fix_em_dashes && em_dashes_fixed > 0 {
        fs::write(path, fixed_content)?;
    }

    Ok(StyleReport {
        file_path: path.to_string_lossy().to_string(),
        em_dashes_fixed,
        violations,
    })
}
