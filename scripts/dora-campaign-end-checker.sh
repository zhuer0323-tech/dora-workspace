#!/bin/bash
# 廣告排程快結束提醒 - 每天早上 9:00 執行
# 檢查所有廣告帳號中，3 天內即將結束的廣告活動並推播 LINE 通知

set -euo pipefail

# 等網路就緒。這支要打 graph.facebook.com（抓活動）跟 api.line.me（推播），
# 剛開機／睡醒時網路可能還沒好，兩個都等到通再往下走，最多等 3 分鐘
# （2026-08-28 比照廣告日報／廣告回報提醒那兩支的教訓：只等 LINE 通不代表 Facebook 也通）
for i in $(seq 1 18); do
    curl -s --max-time 5 https://graph.facebook.com/v25.0/ > /dev/null 2>&1 \
      && curl -s --max-time 5 https://api.line.me > /dev/null 2>&1 \
      && break
    sleep 10
done

source /Users/zhuer/Library/Scripts/dora.env

# 確認 META_TOKEN 存在
if [ -z "${META_TOKEN:-}" ]; then
    echo "ERROR: META_TOKEN not set in dora.env" >&2
    exit 1
fi

python3 << PYEOF
import json, os, sys, time
from datetime import datetime, timedelta, timezone
from urllib.request import urlopen, Request
from urllib.error import URLError

META_TOKEN         = os.environ.get('META_TOKEN') or '${META_TOKEN}'
META_TOKEN_EXPIRES = int('${META_TOKEN_EXPIRES:-0}')
LINE_TOKEN = '${LINE_PUSH_TOKEN}'
LINE_USER  = '${LINE_USER_ID}'

# 要監控的廣告帳號：**問 Meta「這把 token 看得到哪些」**，不寫死。
# 2026-08-25 改：原本寫死那 10 個是舊的個人 token 看得到的，換成系統使用者 token 之後
# 其中 4 個空的個人帳戶（陳妍霖、廣告帳號、Jia Xain Guo、JiaYu Chu）沒指派給它，
# 每次都噴 403 雜訊。日報那支早就是動態問的，這裡跟它一致。
def _list_accounts():
    url = ("https://graph.facebook.com/v25.0/me/adaccounts"
           f"?fields=account_id,name&limit=100&access_token={META_TOKEN}")
    with urlopen(Request(url, headers={"User-Agent": "DoraMonitor/1.0"}), timeout=25) as r:
        data = json.loads(r.read())
    return [(f"act_{a['account_id']}", a.get('name') or a['account_id'])
            for a in data.get('data', [])]

try:
    AD_ACCOUNTS = _list_accounts()
except Exception as e:
    # 問不到就退回原本那份清單，寧可多問幾個也不要整支不跑
    print(f"Warning: 拿不到帳戶清單，退回寫死的名單：{e}", file=sys.stderr)
    AD_ACCOUNTS = [
        ("act_594623605697795",  "屬於花藝 / Y"),
        ("act_1011359997807756", "TO / 十八 / 優逸 / H / 卡威"),
        ("act_1711564422807708", "李老闆 / YO / 漁互動"),
        ("act_1082805773432972", "M互動 / 漁KOL / 工研 /花徑 / 沐"),
        ("act_2454052435035039", "翠芙思"),
        ("act_973020305475229",  "華信"),
    ]

DAYS_AHEAD = 3  # 幾天內即將結束要提醒
now = datetime.now(timezone.utc)
deadline = now + timedelta(days=DAYS_AHEAD)

def send_line(msg):
    payload = json.dumps({"to": LINE_USER, "messages": [{"type": "text", "text": msg}]}).encode()
    req = Request(
        "https://api.line.me/v2/bot/message/push",
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {LINE_TOKEN}"},
    )
    with urlopen(req, timeout=10) as r:
        r.read()

# --- Token 到期提醒 ---
if META_TOKEN_EXPIRES > 0:
    import time
    secs_left = META_TOKEN_EXPIRES - int(time.time())
    days_left  = secs_left // 86400
    if 0 <= days_left <= 3:
        exp_date = datetime.fromtimestamp(META_TOKEN_EXPIRES).strftime("%Y-%m-%d")
        label = "🔴 今天到期！" if days_left == 0 else f"⚠️ 剩 {days_left} 天到期"
        warning = (
            f"🔑 Meta API Token 即將失效\n"
            f"{label}（{exp_date}）\n\n"
            f"請盡快更新 Token：\n"
            f"1. 到 Graph API Explorer 生成新 Short Token\n"
            f"2. 執行：bash ~/Library/Scripts/dora-meta-token-setup.sh <APP_SECRET> <NEW_TOKEN>"
        )
        send_line(warning)
        print(f"Token expiry warning sent: {days_left} days left")

def graph_api(path, params=""):
    url = f"https://graph.facebook.com/v25.0/{path}?access_token={META_TOKEN}{params}"
    req = Request(url, headers={"User-Agent": "DoraMonitor/1.0"})
    with urlopen(req, timeout=15) as r:
        return json.loads(r.read())

ending_soon = []

for acc_id, acc_name in AD_ACCOUNTS:
    try:
        # 取得該帳號下 ACTIVE 且有結束時間的廣告活動
        filter_param = '[{"field":"effective_status","operator":"IN","value":["ACTIVE","PAUSED"]}]'
        resp = graph_api(
            f"{acc_id}/campaigns",
            f"&fields=name,effective_status,stop_time&filtering={filter_param}&limit=200"
        )
        campaigns = resp.get("data", [])
        for c in campaigns:
            stop = c.get("stop_time")
            if not stop:
                continue
            # 解析日期（格式：2025-07-04T00:00:00+0000）
            try:
                stop_dt = datetime.fromisoformat(stop.replace("+0000", "+00:00"))
            except ValueError:
                continue
            if now <= stop_dt <= deadline:
                days_left = (stop_dt - now).days
                ending_soon.append({
                    "account": acc_name,
                    "campaign": c["name"],
                    "status": c.get("effective_status", ""),
                    "stop_time": stop_dt.strftime("%m/%d %H:%M"),
                    "days_left": days_left,
                })
    except Exception as e:
        print(f"Warning: {acc_id} ({acc_name}) error: {e}", file=sys.stderr)
        continue

if not ending_soon:
    print("No campaigns ending soon. No notification sent.")
    sys.exit(0)

# 排序：剩餘天數少的排前面
ending_soon.sort(key=lambda x: x["days_left"])

# 組合 LINE 訊息
lines = ["⚠️ 廣告排程提醒", ""]
lines.append(f"以下 {len(ending_soon)} 個廣告活動將在 {DAYS_AHEAD} 天內結束：")
lines.append("")
for item in ending_soon:
    label = "🔴" if item["days_left"] == 0 else ("🟠" if item["days_left"] == 1 else "🟡")
    lines.append(f"{label} {item['account']}")
    lines.append(f"  {item['campaign']}")
    lines.append(f"  結束：{item['stop_time']}（剩 {item['days_left']} 天）")
    lines.append("")

msg = "\n".join(lines).strip()
send_line(msg)
print(f"LINE sent: {len(ending_soon)} campaigns ending soon")
PYEOF
