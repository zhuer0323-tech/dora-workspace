#!/bin/bash
# 共用 LINE 推播腳本：把文字檔內容推播到朱兒的 LINE（Dora賺錢小能手）
# 用法：dora-line-push.sh <文字檔路徑>
# 憑證：LINE_PUSH_TOKEN、LINE_USER_ID 讀環境變數（存於 ~/.claude/settings.json env 區塊）

set -euo pipefail

FILE="${1:?用法: dora-line-push.sh <文字檔路徑>}"
[ -f "$FILE" ] || { echo "找不到檔案：$FILE" >&2; exit 1; }
: "${LINE_PUSH_TOKEN:?缺少 LINE_PUSH_TOKEN 環境變數}"
: "${LINE_USER_ID:?缺少 LINE_USER_ID 環境變數}"

TEXT_JSON=$(python3 -c 'import json,sys; print(json.dumps(open(sys.argv[1]).read()))' "$FILE")

HTTP_CODE=$(curl -s -o /tmp/dora_line_push_resp.txt -w "%{http_code}" -X POST 'https://api.line.me/v2/bot/message/push' \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $LINE_PUSH_TOKEN" \
  -d "{\"to\":\"$LINE_USER_ID\",\"messages\":[{\"type\":\"text\",\"text\":$TEXT_JSON}]}")

if [ "$HTTP_CODE" = "200" ]; then
  echo "推播成功"
else
  echo "推播失敗（HTTP $HTTP_CODE）：$(cat /tmp/dora_line_push_resp.txt)" >&2
  exit 1
fi
