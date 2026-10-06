#!/bin/sh
# Ethogram in one line: the engine in ~/.ethogram, and the skill for every agent CLI installed here.
#   curl -fsSL https://raw.githubusercontent.com/CarterYoo/ethogram/main/install.sh | sh
# Run it again to update. ETHOGRAM_HOME moves the engine; ETHOGRAM_REPO clones from elsewhere.
set -eu
REPO="${ETHOGRAM_REPO:-https://github.com/CarterYoo/ethogram}"
E="${ETHOGRAM_HOME:-$HOME/.ethogram}"
command -v git >/dev/null 2>&1 || { echo "Ethogram needs git." >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "Ethogram needs Python 3.9 or later." >&2; exit 1; }
python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' || { echo "Ethogram needs Python 3.9 or later." >&2; exit 1; }

if [ -d "$E/.git" ]; then
  git -C "$E" pull -q --ff-only
else
  git clone -q --depth 1 "$REPO" "$E"
fi

done_in=""
for skills in "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills" "${CODEX_HOME:-$HOME/.codex}/skills"; do
  [ -d "$(dirname "$skills")" ] || continue          # only for the agents that are installed
  mkdir -p "$skills"
  rm -rf "$skills/ethogram"
  cp -R "$E/skills/ethogram" "$skills/ethogram"
  done_in="$done_in
  $skills/ethogram"
done

echo "Ethogram is in $E"
if [ -n "$done_in" ]; then
  echo "The skill is installed in:$done_in"
  echo 'Now ask Claude Code or Codex: "Run Ethogram on <folder with a multi-agent log>"'
else
  echo "No Claude Code or Codex found. Install one, then run this line again,"
  echo "or copy $E/skills/ethogram into your agent's skills folder."
fi
