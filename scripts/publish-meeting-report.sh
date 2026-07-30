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

# 用字串定位切片，不用單一正則：報告卡片之間可能夾註解（<!-- 通則 -->…），
# 若用 <div class="wrap">(.*?)</div>...<div class="edit-bar"，可選註解的 .*? 會
# 從第一個 <!-- 一路吃到最後一個 -->，把中間所有卡片吞掉，只剩第一張卡。
start_tag = '<div class="wrap">'
si = raw.find(start_tag)
ei = raw.find('<div class="edit-bar"')
if si == -1 or ei == -1 or ei < si:
    sys.exit("無法從 report.html 取出內容（找不到 wrap／edit-bar 區塊，請確認是會議分析 skill 產出的報告）")
chunk = raw[si + len(start_tag):ei]
# 尾端清理用純字串定位，不用正則：正則的 .*? 配 $ 會跨越多個註解，
# 把中間卡片全吃掉（曾兩次踩到）。這裡只切最尾端那一段。
# 1) 移除 edit-bar 前的註解（<!-- 編輯工具列… -->）
c = chunk.rfind('<!--')
if c != -1 and chunk[c:].strip().endswith('-->'):
    chunk = chunk[:c]
chunk = chunk.rstrip()
# 2) 移除包住 wrap 的最後一個 </div>
if chunk.endswith('</div>'):
    chunk = chunk[:-len('</div>')]
html = chunk.strip()

t = re.search(r"<h1>(.*?)</h1>", html, re.S)
title = re.sub(r"<[^>]+>", "", t.group(1)).strip() if t else "會議報告"

print(json.dumps({"title": title, "html": html, "updatedAt": {".sv": "timestamp"}}, ensure_ascii=False))
PYEOF

curl -sf -X PUT -H "Content-Type: application/json" --data-binary "@$PAYLOAD" "$DB/meetings/$SLUG.json" > /dev/null

URL="${SITE}?m=$SLUG"
echo "$URL" > "$DIR/published-url.txt"
echo "已發布，分享連結："
echo "$URL"
