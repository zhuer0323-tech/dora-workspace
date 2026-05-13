#!/bin/bash
# PreCompact hook：Context 壓縮前，存下當前 git 狀態與最近修改的檔案

SNAPSHOT_DIR="/Users/angela/Downloads/Dora專屬/000_Agent/memory/snapshots"
mkdir -p "$SNAPSHOT_DIR"

TIMESTAMP=$(date '+%Y-%m-%d_%H%M%S')
FILE="$SNAPSHOT_DIR/$TIMESTAMP.md"

{
  echo "# 快照 $TIMESTAMP（Context 壓縮前自動儲存）"
  echo ""
  echo "## Git 狀態"
  cd "/Users/angela/Downloads/Dora專屬" && git status --short 2>/dev/null || echo "（無 git 變更）"
  echo ""
  echo "## 最近修改的 Markdown 檔"
  find "/Users/angela/Downloads/Dora專屬" -name "*.md" -newer "/Users/angela/Downloads/Dora專屬/.git/index" 2>/dev/null | head -10 || echo "（無）"
} > "$FILE"
