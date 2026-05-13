#!/bin/bash
# SessionEnd hook：對話結束時，在當日 daily log 追加結束時間與檔案變更摘要

DAILY_DIR="/Users/angela/Downloads/Dora專屬/000_Agent/memory/daily"
mkdir -p "$DAILY_DIR"

DATE=$(date '+%Y-%m-%d')
TIME=$(date '+%H:%M')
FILE="$DAILY_DIR/$DATE.md"

if [ ! -f "$FILE" ]; then
  echo "# Daily Log $DATE" > "$FILE"
  echo "" >> "$FILE"
fi

{
  echo ""
  echo "---"
  echo "**Session 結束**：$TIME"
  echo ""
  CHANGES=$(cd "/Users/angela/Downloads/Dora專屬" && git status --short 2>/dev/null)
  if [ -n "$CHANGES" ]; then
    echo "本次有修改的檔案："
    echo "$CHANGES"
  fi
} >> "$FILE"
