#!/bin/bash
# UserPromptSubmit hook：每天第一次送訊息時，自動注入今日日期與最近 session 記錄

FLAG="/tmp/dora_context_$(date +%Y%m%d)"
if [ -f "$FLAG" ]; then
  exit 0
fi
touch "$FLAG"

echo "【自動注入】今天：$(date '+%Y-%m-%d %A')"

DAILY_DIR="/Users/angela/Downloads/Dora專屬/000_Agent/memory/daily"
LATEST=$(ls -t "$DAILY_DIR"/*.md 2>/dev/null | head -1)
if [ -n "$LATEST" ]; then
  echo ""
  echo "上次 session 紀錄（$(basename "$LATEST" .md)）："
  head -25 "$LATEST"
fi
