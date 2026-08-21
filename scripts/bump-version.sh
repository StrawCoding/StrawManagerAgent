#!/usr/bin/env bash
# Bump VERSION a.b.c.d (d = preview; 0 = release)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FILE="$ROOT/VERSION"
cur="$(tr -d '[:space:]' < "$FILE")"
IFS=. read -r a b c d <<<"$cur"
part="${1:-preview}"
case "$part" in
  major) a=$((a+1)); b=0; c=0; d=1 ;;
  minor) b=$((b+1)); c=0; d=1 ;;
  patch) c=$((c+1)); d=1 ;;
  preview) d=$((d+1)) ;;
  release) d=0 ;;
  *) echo "usage: $0 [major|minor|patch|preview|release]"; exit 1 ;;
esac
next="$a.$b.$c.$d"
echo "$next" > "$FILE"
# sync pyproject
python3 - <<PY
from pathlib import Path
p = Path("$ROOT/pyproject.toml")
text = p.read_text()
import re
text2 = re.sub(r'version = "[^"]+"', 'version = "$next"', text, count=1)
p.write_text(text2)
print("bumped to $next")
PY
