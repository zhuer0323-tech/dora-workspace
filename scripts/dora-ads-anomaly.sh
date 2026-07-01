#!/bin/bash
# 廣告活動每日回報 - 每天下午 6:00 執行

set -euo pipefail

for i in $(seq 1 6); do
    if curl -s --max-time 3 https://api.line.me > /dev/null 2>&1; then
        break
    fi
    sleep 5
done

source /Users/angela/Library/Scripts/dora.env

if [ -z "${META_TOKEN:-}" ]; then
    echo "ERROR: META_TOKEN not set in dora.env" >&2
    exit 1
fi

PAYLOAD=$(META_TOKEN="$META_TOKEN" LINE_PUSH_TOKEN="$LINE_PUSH_TOKEN" LINE_USER_ID="$LINE_USER_ID" python3 << 'PYEOF'
import json, os, sys
from datetime import datetime, timezone, timedelta
from urllib.request import urlopen, Request

META_TOKEN = os.environ['META_TOKEN']
LINE_TOKEN = os.environ['LINE_PUSH_TOKEN']
LINE_USER  = os.environ['LINE_USER_ID']

AD_ACCOUNTS = [
    ("act_1011359997807756", "TO / 十八 / 優逸 / H / 卡威"),
    ("act_1711564422807708", "李老闆 / YO / 漁互動"),
    ("act_1082805773432972", "M互動 / 漁KOL / 工研 /花徑 / 沐"),
]

BRAND_CONFIG = {
    "李老闆": ("🛒", "sales"),
    "漁三":   ("🎣", "engagement"),
    "優逸":   ("💬", "messages"),
}

now_utc  = datetime.now(timezone.utc)
tw_now   = now_utc + timedelta(hours=8)   # 台灣時間 UTC+8
NTD      = "NT$"

# 早上跑 → 回報昨日；下午跑 → 回報今日
if tw_now.hour < 12:
    report_date  = (tw_now - timedelta(days=1)).strftime("%Y-%m-%d")
    report_label = "昨日"
else:
    report_date  = tw_now.strftime("%Y-%m-%d")
    report_label = "今日截至目前"

def api(path, params=""):
    url = f"https://graph.facebook.com/v25.0/{path}?access_token={META_TOKEN}{params}"
    req = Request(url, headers={"User-Agent": "DoraMonitor/1.0"})
    with urlopen(req, timeout=20) as r:
        return json.loads(r.read())

def safe_float(v):
    try: return float(v)
    except: return 0.0

def safe_int(v):
    try: return int(float(v))
    except: return 0

def find_action(items, *types):
    for a in (items or []):
        if a.get("action_type") in types:
            return a
    return {}

def get_insights(cid):
    resp = api(
        f"{cid}/insights",
        f'&fields=spend,actions,action_values,reach'
        f'&time_range={{"since":"{report_date}","until":"{report_date}"}}'
    )
    data = resp.get("data", [])
    return data[0] if data else {}

def match_brand(name):
    for keyword, cfg in BRAND_CONFIG.items():
        if keyword in name:
            return keyword, cfg[0], cfg[1]
    return None, None, None

def metric_lines(row, metric_type):
    spend   = safe_float(row.get("spend"))
    actions = row.get("actions", [])
    values  = row.get("action_values", [])
    reach   = safe_int(row.get("reach", 0))

    if metric_type == "sales":
        purchase     = find_action(actions, "purchase", "offsite_conversion.fb_pixel_purchase", "omni_purchase")
        purchase_val = find_action(values,  "purchase", "offsite_conversion.fb_pixel_purchase", "omni_purchase")
        qty  = safe_int(purchase.get("value", 0))
        rev  = safe_float(purchase_val.get("value", 0))
        roas = rev / spend if spend > 0 else 0
        cpa  = spend / qty if qty > 0 else 0
        return [
            f"花費：{NTD}{spend:,.0f}",
            f"購買數：{qty}",
            f"購買金額：{NTD}{rev:,.0f}",
            f"ROAS：{roas:.2f}",
            f"CPA：{NTD}{cpa:,.0f}" if qty > 0 else "CPA：-",
        ]
    elif metric_type == "engagement":
        eng = find_action(actions, "post_engagement")
        qty = safe_int(eng.get("value", 0))
        cpe = spend / qty if qty > 0 else 0
        return [
            f"花費：{NTD}{spend:,.0f}",
            f"互動數：{qty:,}",
            f"觸及人數：{reach:,}",
            f"CPE：{NTD}{cpe:.2f}" if qty > 0 else "CPE：-",
        ]
    elif metric_type == "messages":
        msg = find_action(
            actions,
            "onsite_conversion.messaging_conversation_started_7d",
            "messaging_conversation_started_7d",
        )
        qty     = safe_int(msg.get("value", 0))
        cpm_msg = spend / qty if qty > 0 else 0
        return [
            f"花費：{NTD}{spend:,.0f}",
            f"對話開始數：{qty}",
            f"每則對話成本：{NTD}{cpm_msg:.0f}" if qty > 0 else "每則對話成本：-",
        ]
    return []

def make_campaign_box(name, emoji, lines):
    contents = [
        {
            "type": "text",
            "text": f"{emoji} {name}",
            "weight": "bold",
            "size": "sm",
            "color": "#5C4A8A",
            "wrap": True
        }
    ]
    for line in lines:
        contents.append({
            "type": "text",
            "text": line,
            "size": "xs",
            "color": "#555555",
            "margin": "sm"
        })
    return {"type": "box", "layout": "vertical", "margin": "lg", "contents": contents}

# 收集各品牌活動廣告
campaign_boxes = []

for acc_id, _ in AD_ACCOUNTS:
    try:
        resp = api(
            f"{acc_id}/campaigns",
            '&fields=id,name&filtering=[{"field":"effective_status","operator":"IN","value":["ACTIVE"]}]&limit=100'
        )
        for c in resp.get("data", []):
            cname = c.get("name", "")
            brand, emoji, mtype = match_brand(cname)
            if not brand:
                continue
            row = get_insights(c["id"])
            if not row:
                continue
            lines = metric_lines(row, mtype)
            if campaign_boxes:
                campaign_boxes.append({"type": "separator", "margin": "lg"})
            campaign_boxes.append(make_campaign_box(cname, emoji, lines))
    except Exception as e:
        print(f"Warning: {acc_id} error: {e}", file=sys.stderr)
        continue

if not campaign_boxes:
    print("NO_DATA")
    sys.exit(0)

rd      = datetime.fromisoformat(report_date)
days_zh = {0:"週一",1:"週二",2:"週三",3:"週四",4:"週五",5:"週六",6:"週日"}
rd_str  = f"{rd.strftime('%Y/%m/%d')}（{days_zh[rd.weekday()]}）{report_label}"

payload = {
    "to": LINE_USER,
    "messages": [
        {
            "type": "flex",
            "altText": f"廣告日報 {report_date} {report_label}",
            "contents": {
                "type": "bubble",
                "header": {
                    "type": "box",
                    "layout": "vertical",
                    "backgroundColor": "#9C88CC",
                    "paddingAll": "20px",
                    "contents": [
                        {
                            "type": "text",
                            "text": "📊 廣告日報",
                            "weight": "bold",
                            "size": "xl",
                            "color": "#FFFFFF",
                            "align": "center"
                        },
                        {
                            "type": "text",
                            "text": rd_str,
                            "size": "sm",
                            "color": "#EDE7F6",
                            "align": "center",
                            "margin": "sm"
                        }
                    ]
                },
                "body": {
                    "type": "box",
                    "layout": "vertical",
                    "spacing": "none",
                    "paddingAll": "16px",
                    "contents": campaign_boxes
                },
                "footer": {
                    "type": "box",
                    "layout": "vertical",
                    "backgroundColor": "#F0EBF8",
                    "paddingAll": "12px",
                    "contents": [
                        {
                            "type": "text",
                            "text": "廣告穩穩跑，成效天天好！",
                            "size": "xs",
                            "color": "#7C5CBF",
                            "align": "center"
                        }
                    ]
                }
            }
        }
    ]
}
print(json.dumps(payload, ensure_ascii=False))
PYEOF
)

if [ "$PAYLOAD" = "NO_DATA" ]; then
    echo "No matching active campaigns. No notification sent."
    exit 0
fi

curl -s -X POST 'https://api.line.me/v2/bot/message/push' \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $LINE_PUSH_TOKEN" \
  -d "$PAYLOAD"

echo "LINE sent: brand campaign daily report (Flex)"
