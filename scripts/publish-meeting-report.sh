#!/bin/bash
# 發布會議報告到同步網頁（會議報告閱讀站）
# 用法：scripts/publish-meeting-report.sh <會議資料夾> [自訂會議代碼]
# 說明：從資料夾內的 report.html 取出報告內容，推上 Firebase meetings/ 區，
#       印出可分享的閱讀站連結。重複執行同一代碼＝更新該連結的內容。
set -euo pipefail

DIR="${1:?用法：publish-meeting-report.sh <會議資料夾> [自訂會議代碼]}"
REPORT="$DIR/report.html"
DB="https://busan-trip-2026-201f8-default-rtdb.asia-southeast1.firebasedatabase.app"
SITE="https://zhuer0323-tech.github.io/dora-workspace/meeting-report-site/"

if [ ! -f "$REPORT" ]; then
  echo "找不到 $REPORT" >&2
  exit 1
fi

SLUG="${2:-}"
if [ -z "$SLUG" ]; then
  SLUG="m$(date +%Y%m%d)-$(python3 -c 'import secrets; print(secrets.token_hex(5))')"
fi

PAYLOAD="$(mktemp)"
trap 'rm "$PAYLOAD" 2>/dev/null || true' EXIT

python3 - "$REPORT" > "$PAYLOAD" <<'PYEOF'
import json, re, sys

raw = open(sys.argv[1], encoding="utf-8").read()
m = re.search(r'<div class="wrap">(.*?)</div>\s*(?:<!--.*?-->\s*)?<div class="edit-bar"', raw, re.S)
if not m:
    sys.exit("無法從 report.html 取出內容（找不到 wrap 區塊，請確認是會議分析 skill 產出的報告）")
html = m.group(1).strip()

t = re.search(r"<h1>(.*?)</h1>", html, re.S)
title = re.sub(r"<[^>]+>", "", t.group(1)).strip() if t else "會議報告"

print(json.dumps({"title": title, "html": html, "updatedAt": {".sv": "timestamp"}}, ensure_ascii=False))
PYEOF

curl -sf -X PUT -H "Content-Type: application/json" --data-binary "@$PAYLOAD" "$DB/meetings/$SLUG.json" > /dev/null

URL="${SITE}?m=$SLUG"
echo "$URL" > "$DIR/published-url.txt"
echo "已發布，分享連結："
echo "$URL"
