#!/bin/bash
# PostToolUse hook: her Edit/Write sonrasi degisen .py dosyalarini otomatik formatlar.
# Claude Code bu scripti calistirir; cikis kodu Claude'un akisini etkilemez (bilgi amaclidir).

INPUT=$(cat)
PYBIN=python3
command -v python3 >/dev/null 2>&1 || PYBIN=python
FILE=$(echo "$INPUT" | "$PYBIN" -c "import json,sys; print(json.load(sys.stdin).get('tool_input',{}).get('file_path',''))" 2>/dev/null)

if [[ "$FILE" == *.py ]] && [ -f "$FILE" ]; then
  if command -v ruff >/dev/null 2>&1; then
    ruff format -- "$FILE" >/dev/null 2>&1
  fi
  if command -v black >/dev/null 2>&1; then
    black -q -- "$FILE" >/dev/null 2>&1
  fi
fi

exit 0
