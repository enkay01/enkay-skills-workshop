#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${HOME}/.local/bin"
GEMINI_CONFIG_DIR="${HOME}/.gemini/config"

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
fi

echo "Registering global hooks..."
HOOKS_CONFIG="${GEMINI_CONFIG_DIR}/hooks.json"
if [ ! -f "${HOOKS_CONFIG}" ]; then
    cp "${SCRIPT_DIR}/config/hooks.json" "${HOOKS_CONFIG}"
    echo "Installed default ${HOOKS_CONFIG}"
else
    echo "Existing ${HOOKS_CONFIG} detected; please inspect ${SCRIPT_DIR}/config/hooks.json to merge custom hooks."
fi

echo "Installation complete."
echo "Binaries installed:"
echo "  - ${BIN_DIR}/fast-tools"
echo "  - ${BIN_DIR}/view-file"
