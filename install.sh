#!/usr/bin/env bash
# Install both skills into ~/.claude/skills/ (pass --symlink to link instead
# of copy, so a git pull updates the installed skills in place).
set -euo pipefail

SRC="$(cd "$(dirname "$0")/skills" && pwd)"
DST="$HOME/.claude/skills"
mkdir -p "$DST"

for skill in poly-state-scan poly-bet-plan; do
    rm -rf "${DST:?}/$skill"
    if [[ "${1:-}" == "--symlink" ]]; then
        ln -s "$SRC/$skill" "$DST/$skill"
        echo "linked  $DST/$skill -> $SRC/$skill"
    else
        cp -R "$SRC/$skill" "$DST/$skill"
        echo "copied  $DST/$skill"
    fi
done

python3 -c "import matplotlib" 2>/dev/null || \
    echo "note: charts need matplotlib -> pip install matplotlib certifi"
echo "done — restart Claude Code to pick up the skills"
