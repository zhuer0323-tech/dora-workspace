#!/bin/bash
# 釜山 Pass 48 小時倒數提醒 - 每 15 分鐘檢查一次
# 剩最後 3 小時內，推播一次 LINE 提醒（不重複發送）

set -euo pipefail

for i in $(seq 1 6); do
    if curl -s --max-time 3 https://api.line.me > /dev/null 2>&1; then
        break
    fi
    sleep 5
done

source /Users/angela/Library/Scripts/dora.env

if [ -z "${LINE_PUSH_TOKEN:-}" ] || [ -z "${LINE_USER_ID:-}" ]; then
    echo "ERROR: LINE_PUSH_TOKEN or LINE_USER_ID not set in dora.env" >&2
    exit 1
fi

FIREBASE_URL="https://busan-trip-2026-201f8-default-rtdb.asia-southeast1.firebasedatabase.app/busan2026/busanPass.json"

python3 << PYEOF
import json, sys, time
from urllib.request import urlopen, Request
from urllib.error import URLError

LINE_TOKEN = '${LINE_PUSH_TOKEN}'
LINE_USER  = '${LINE_USER_ID}'
FIREBASE_URL = '${FIREBASE_URL}'
PASS_DURATION_SEC = 48 * 3600
URGENT_SEC = 3 * 3600

def send_line(msg):
    payload = json.dumps({"to": LINE_USER, "messages": [{"type": "text", "text": msg}]}).encode()
    req = Request(
        "https://api.line.me/v2/bot/message/push",
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {LINE_TOKEN}"},
    )
    with urlopen(req, timeout=10) as r:
        r.read()

try:
    with urlopen(FIREBASE_URL, timeout=10) as r:
        raw = r.read().decode()
        data = json.loads(raw) if raw and raw != "null" else None
except URLError as e:
    print(f"Firebase read error: {e}", file=sys.stderr)
    sys.exit(0)

if not data or not data.get('activatedAt'):
    print("Pass not activated yet. No action.")
    sys.exit(0)

if data.get('notified'):
    print("Already notified. No action.")
    sys.exit(0)

now_ms = int(time.time() * 1000)
remaining_ms = data['activatedAt'] + PASS_DURATION_SEC * 1000 - now_ms
remaining_sec = remaining_ms // 1000

if remaining_sec <= 0:
    print("Pass already expired. No notification (missed window).")
    sys.exit(0)

if remaining_sec > URGENT_SEC:
    print(f"Not urgent yet. {remaining_sec}s remaining.")
    sys.exit(0)

hours_left = remaining_sec // 3600
mins_left = (remaining_sec % 3600) // 60

msg = (
    "釜山 Pass 即將到期！\n\n"
    f"剩餘時間：約 {hours_left} 小時 {mins_left} 分鐘\n"
    "把握時間使用完畢喔～"
)
send_line(msg)
print(f"LINE sent. {hours_left}h{mins_left}m remaining.")

try:
    patch_req = Request(
        FIREBASE_URL,
        data=json.dumps({"notified": True}).encode(),
        headers={"Content-Type": "application/json"},
        method="PATCH",
    )
    with urlopen(patch_req, timeout=10) as r:
        r.read()
except URLError as e:
    print(f"Warning: failed to mark notified: {e}", file=sys.stderr)
PYEOF
