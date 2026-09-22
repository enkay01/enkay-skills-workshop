#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${HOME}/.local/bin"
GEMINI_DIR="${HOME}/.gemini"
GEMINI_CONFIG_DIR="${GEMINI_DIR}/config"

echo "Building fast-tools in release mode..."
cargo build --release --bin fast-tools --manifest-path "${SCRIPT_DIR}/tools/fast-tools/Cargo.toml"

mkdir -p "${BIN_DIR}"
echo "Installing fast-tools to ${BIN_DIR}..."
cp "${SCRIPT_DIR}/tools/fast-tools/target/release/fast-tools" "${BIN_DIR}/fast-tools"
chmod +x "${BIN_DIR}/fast-tools"

# Clean up legacy view-file binary if present
if [ -f "${BIN_DIR}/view-file" ]; then
    rm -f "${BIN_DIR}/view-file"
    echo "Removed legacy ${BIN_DIR}/view-file"
fi

# Clean up redundant MCP servers from mcp_config.json
MCP_CONFIG="${GEMINI_CONFIG_DIR}/mcp_config.json"
if [ -f "${MCP_CONFIG}" ]; then
    python3 -c "
import json
with open('${MCP_CONFIG}', 'r') as f:
    data = json.load(f)
servers = data.get('mcpServers', {})
changed = False
if 'fast-tools' in servers:
    del servers['fast-tools']
    changed = True
if 'fast-view' in servers:
    del servers['fast-view']
    changed = True
if changed:
    with open('${MCP_CONFIG}', 'w') as f:
        json.dump(data, f, indent=2)
    print('Cleaned redundant MCP servers from ${MCP_CONFIG}')
"
fi

echo "Registering global hooks in ${GEMINI_CONFIG_DIR}/hooks.json..."
mkdir -p "${GEMINI_CONFIG_DIR}"
HOOKS_CONFIG="${GEMINI_CONFIG_DIR}/hooks.json"
python3 -c '
import json, os, shutil, time, sys
hooks_file = sys.argv[1]
incoming_file = sys.argv[2]
with open(incoming_file) as f:
    incoming = json.load(f)

if os.path.exists(hooks_file):
    try:
        with open(hooks_file, "r") as f:
            current = json.load(f)
    except Exception as e:
        backup = hooks_file + ".bak." + str(int(time.time()))
        shutil.copy2(hooks_file, backup)
        sys.stderr.write(f"Error parsing existing {hooks_file}: {e}\nBacked up original to {backup}. Aborting.\n")
        sys.exit(1)
else:
    current = {}

current["fast-tools-optimizer"] = incoming["fast-tools-optimizer"]
with open(hooks_file, "w") as f:
    json.dump(current, f, indent=2)
' "${HOOKS_CONFIG}" "${SCRIPT_DIR}/config/hooks.json"
echo "Registered fast-tools-optimizer with PreToolUse, PostToolUse, and Stop hooks."

# Strip redundant prompt rules from ~/.gemini/GEMINI.md and ~/.gemini/AGENTS.md
echo "Cleaning redundant prompt rules from GEMINI.md and AGENTS.md..."
python3 -c '
import re, sys

def clean_rules(file_path):
    try:
        with open(file_path, "r") as f:
            content = f.read()
    except FileNotFoundError:
        return

    pattern = r"\n?## Deterministic Fast Tools\n+(?:- \*\*[^\n]+\n*)*"
    cleaned = re.sub(pattern, "\n", content)
    if cleaned != content:
        with open(file_path, "w") as f:
            f.write(cleaned)
        print(f"Removed Deterministic Fast Tools block from {file_path}")

clean_rules(sys.argv[1] + "/GEMINI.md")
clean_rules(sys.argv[1] + "/AGENTS.md")
' "${GEMINI_DIR}"

echo "Installation complete."
echo "Active hooks in ${HOOKS_CONFIG}:"
echo "  - PreToolUse: Rewrites git status to fast-tools git-snapshot"
echo "  - PostToolUse: Auto-fixes em-dashes on disk for written/edited markdown"
echo "  - Stop: Validates generated artifacts against banned words and style rules"
