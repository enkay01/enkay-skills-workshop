---
name: fast-view
description: High-performance whole-file and multi-file batch reader for large context agents. Reads full files in a single pass with clean line numbers, minified line guards, and safety thresholds.
---

# Fast view

The fast-view tool reads whole files up to 5,000 lines or 500 KB in a single turn. It eliminates repetitive micro-chunking and takes advantage of prompt cache reuse.

## Usage

### Read entire file

Reads a complete file with line numbers:

```bash
view-file /path/to/file.rs
# or: fast-tools view /path/to/file.rs
```

### Read line range

Reads a targeted line slice:

```bash
view-file /path/to/file.rs -s 10 -e 40
```

### Read multiple files in batch

Reads multiple files in a single pass:

```bash
view-file file1.rs file2.rs tests/test_file.rs
```

If the aggregate byte budget is reached, secondary files automatically degrade to outline mode to prevent context overflow.

### Symbol outline mode

Scans top-level declarations (functions, structs, traits, classes, interfaces) with line numbers:

```bash
view-file /path/to/file.rs -o
```

### Safety thresholds

Files exceeding 5,000 lines display the first 1,000 lines with slice instructions. To read the entire file, pass `--force-all`. Files containing lines exceeding 2,000 characters or averaging over 400 bytes per line are caught by the minified line guard. Pass `--force-all` to inspect raw minified content.
