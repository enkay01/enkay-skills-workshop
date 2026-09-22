#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${HOME}/.local/bin"
GEMINI_DIR="${HOME}/.gemini"
GEMINI_CONFIG_DIR="${GEMINI_DIR}/config"

echo "Building fast-tools and view-file in release mode..."
cargo build --release --manifest-path "${SCRIPT_DIR}/tools/fast-tools/Cargo.toml"

mkdir -p "${BIN_DIR}"
echo "Installing binaries to ${BIN_DIR}..."
cp "${SCRIPT_DIR}/tools/fast-tools/target/release/fast-tools" "${BIN_DIR}/fast-tools"
cp "${SCRIPT_DIR}/tools/fast-tools/target/release/view-file" "${BIN_DIR}/view-file"
chmod +x "${BIN_DIR}/fast-tools" "${BIN_DIR}/view-file"

echo "Registering fast-tools in MCP configuration..."
mkdir -p "${GEMINI_CONFIG_DIR}"
MCP_CONFIG="${GEMINI_CONFIG_DIR}/mcp_config.json"

if [ -f "${MCP_CONFIG}" ]; then
    python3 -c "
import json
with open('${MCP_CONFIG}', 'r') as f:
    data = json.load(f)
servers = data.setdefault('mcpServers', {})
servers['fast-tools'] = {
    'command': '${BIN_DIR}/fast-tools',
    'args': ['--mcp']
}
servers['fast-view'] = {
    'command': '${BIN_DIR}/view-file',
    'args': ['--mcp']
}
with open('${MCP_CONFIG}', 'w') as f:
    json.dump(data, f, indent=2)
"
    echo "Updated ${MCP_CONFIG}"
else
    python3 -c "
import json
data = {
    'mcpServers': {
        'fast-tools': {
            'command': '${BIN_DIR}/fast-tools',
            'args': ['--mcp']
        },
        'fast-view': {
            'command': '${BIN_DIR}/view-file',
            'args': ['--mcp']
        }
    }
}
with open('${MCP_CONFIG}', 'w') as f:
    json.dump(data, f, indent=2)
"
    echo "Created ${MCP_CONFIG}"
fi

echo "Registering global hooks..."
HOOKS_CONFIG="${GEMINI_CONFIG_DIR}/hooks.json"
python3 -c "
import json, os
hooks_file = '${HOOKS_CONFIG}'
with open('${SCRIPT_DIR}/config/hooks.json') as f:
    incoming = json.load(f)

if os.path.exists(hooks_file):
    try:
        with open(hooks_file, 'r') as f:
            current = json.load(f)
    except Exception:
        current = {}
else:
    current = {}

current['fast-tools-optimizer'] = incoming['fast-tools-optimizer']
with open(hooks_file, 'w') as f:
    json.dump(current, f, indent=2)
"
echo "Registered fast-tools-optimizer in ${HOOKS_CONFIG}"

echo "Registering global agent rules in ~/.gemini/GEMINI.md and ~/.gemini/AGENTS.md..."
RULES_BLOCK='
## Deterministic Fast Tools

- **Batch & Whole-File Reading**: Avoid reading files one-by-one across multiple turns. Call `view-file <path1> <path2> ...` (or `fast-tools view`) to ingest multiple files in a single pass up to 5,000 lines / 500 KB per file.
- **Context Search**: Use `fast-tools grep "<pattern>" <path...> -c 10` for code search with clamped surrounding context lines instead of running raw search followed by manual line slicing.
- **Atomic Edit and Verify**: When modifying code that has an associated test or linter, use `fast-tools edit <file> "<target>" "<replacement>" --verify "<test/lint cmd>"` to apply changes with immediate validation and automatic rollback on failure.
- **Process & Port Polling**: Never run shell sleep loops, repeated `lsof`, or inline python scripts with `os.kill(pid, 0)` to check service state. Use `fast-tools poll --pid <pid> --state alive` or `fast-tools poll --port <port> --state ready`.
- **Working Tree Inspection**: Use `fast-tools git-snapshot` to retrieve branch name, dirty file list, and diff stats in a single call before preparing commits.
'

for rule_file in "${GEMINI_DIR}/GEMINI.md" "${GEMINI_DIR}/AGENTS.md"; do
    if [ -f "${rule_file}" ]; then
        if ! grep -q "## Deterministic Fast Tools" "${rule_file}"; then
            echo "${RULES_BLOCK}" >> "${rule_file}"
            echo "Added deterministic tool rules to ${rule_file}"
        else
            echo "Rules already present in ${rule_file}"
        fi
    else
        echo "# Global Rules${RULES_BLOCK}" > "${rule_file}"
        echo "Created ${rule_file} with deterministic tool rules"
    fi
done

echo "Installation complete."
echo "Binaries installed:"
echo "  - ${BIN_DIR}/fast-tools"
echo "  - ${BIN_DIR}/view-file"
