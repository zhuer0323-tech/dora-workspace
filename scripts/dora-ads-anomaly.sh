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
    "TOTO":  ("🏆", "engagement"),
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

def match_brand(name):
    lowered = name.lower()
    for keyword, cfg in BRAND_CONFIG.items():
        if keyword.lower() in lowered:
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
# 抓兩份清單再合併：回報日有花費的活動（含已暫停/結束）+ 目前進行中的活動
campaign_boxes  = []
brand_reported  = set()   # 已有數據區塊的品牌
brand_active    = set()   # 有進行中活動的品牌
account_errors  = []

for acc_id, _ in AD_ACCOUNTS:
    # 回報日有跑量的活動：帳號層級一次撈齊成效，不受活動目前狀態影響
    rows = {}
    try:
        resp = api(
            f"{acc_id}/insights",
            f'&level=campaign&fields=campaign_id,campaign_name,spend,actions,action_values,reach'
            f'&time_range={{"since":"{report_date}","until":"{report_date}"}}&limit=200'
        )
        for row in resp.get("data", []):
            rows[row["campaign_id"]] = row
    except Exception as e:
        account_errors.append(f"{acc_id} insights: {e}")
        print(f"Warning: {acc_id} insights error: {e}", file=sys.stderr)

    # 目前進行中的活動清單
    active = {}
    try:
        resp = api(
            f"{acc_id}/campaigns",
            '&fields=id,name&filtering=[{"field":"effective_status","operator":"IN","value":["ACTIVE"]}]&limit=100'
        )
        for c in resp.get("data", []):
            active[c["id"]] = c.get("name", "")
    except Exception as e:
        account_errors.append(f"{acc_id} campaigns: {e}")
        print(f"Warning: {acc_id} campaigns error: {e}", file=sys.stderr)

    for cid in list(rows.keys()) + [cid for cid in active if cid not in rows]:
        cname = rows[cid].get("campaign_name", "") if cid in rows else active[cid]
        brand, emoji, mtype = match_brand(cname)
        if not brand:
            continue
        if cid in active:
            brand_active.add(brand)
        if cid in rows:
            lines = metric_lines(rows[cid], mtype)
            if campaign_boxes:
                campaign_boxes.append({"type": "separator", "margin": "lg"})
            campaign_boxes.append(make_campaign_box(cname, emoji, lines))
            brand_reported.add(brand)

# 有進行中活動但當日尚無花費的品牌 → 固定顯示占位，不再無聲消失
for brand, (emoji, _) in BRAND_CONFIG.items():
    if brand in brand_active and brand not in brand_reported:
        if campaign_boxes:
            campaign_boxes.append({"type": "separator", "margin": "lg"})
        campaign_boxes.append(make_campaign_box(f"{brand}（進行中）", emoji, ["本日尚無花費"]))

if not campaign_boxes:
    if account_errors:
        payload = {
            "to": LINE_USER,
            "messages": [{
                "type": "text",
                "text": f"⚠️ 廣告日報產生失敗（{report_date} {report_label}）\n所有帳號資料抓取都失敗，請檢查 Meta Token 是否過期或網路狀態。"
            }]
        }
        print(json.dumps(payload, ensure_ascii=False))
        sys.exit(0)
    print("NO_DATA")
    sys.exit(0)

rd      = datetime.fromisoformat(report_date)
days_zh = {0:"週一",1:"週二",2:"週三",3:"週四",4:"週五",5:"週六",6:"週日"}
rd_str  = f"{rd.strftime('%Y/%m/%d')}（{days_zh[rd.weekday()]}）{report_label}"

footer_contents = []
if account_errors:
    footer_contents.append({
        "type": "text",
        "text": "⚠️ 部分帳號資料抓取失敗，數據可能不完整",
        "size": "xs",
        "color": "#CC3333",
        "align": "center"
    })
footer_contents.append({
    "type": "text",
    "text": "廣告穩穩跑，成效天天好！",
    "size": "xs",
    "color": "#7C5CBF",
    "align": "center",
    "margin": "sm" if account_errors else "none"
})

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
                    "contents": footer_contents
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
