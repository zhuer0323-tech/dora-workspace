#!/bin/bash
# 廣告日報：每天 9:00（昨日）與 17:00（今日截至目前）推一張卡片到 LINE
#
# 2026-08-23 改版重點（原本只認李老闆／漁三／優逸／TOTO 四家）：
#   1. 客戶名單改成去「工作台」拿（進行中的客戶），不再寫死在這支裡
#   2. 廣告帳戶改成問 Meta「我看得到哪些」，不再寫死三個（原本漏掉「屬於花藝 / Y」）
#   3. 要列的數字照廣告目標自動選（銷售／訊息／名單／流量／互動），不用先設定
#   4. 每家多一行走期與預算進度（走期與預算都讀工作台）
#   5. 今天在走期內卻抓不到花費的客戶照樣列，標「抓不到」，不會無聲消失
#
# 用法：
#   bash dora-ads-anomaly.sh          # 正常跑，推到 LINE
#   bash dora-ads-anomaly.sh --dry    # 只印出卡片內容，不推播（測試用）

set -euo pipefail

DRY_RUN=""
[ "${1:-}" = "--dry" ] && DRY_RUN="1"

# 開機後 launchd 可能比網路早醒，等一下再跑
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

PAYLOAD=$(
  META_TOKEN="$META_TOKEN" \
  LINE_PUSH_TOKEN="$LINE_PUSH_TOKEN" \
  LINE_USER_ID="$LINE_USER_ID" \
  WS_SA_KEY="${WS_SA_KEY:-}" WS_DB_URL="${WS_DB_URL:-}" WS_ROOM="${WS_ROOM:-}" \
  python3 << 'PYEOF'
import base64, json, os, re, subprocess, sys, tempfile, time
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode
from urllib.request import urlopen, Request

META_TOKEN = os.environ['META_TOKEN']
LINE_USER  = os.environ['LINE_USER_ID']

NTD = "NT$"

# 工作台讀不到時的退路：至少維持改版前那四家，不要整則日報消失
FALLBACK_CLIENTS = [
    {'name': '李老闆', 'short': '李老闆', 'kw': '', 'runs': [], 'budget': ''},
    {'name': '漁三',   'short': '漁三',   'kw': '', 'runs': [], 'budget': ''},
    {'name': '優逸',   'short': '優逸',   'kw': '', 'runs': [], 'budget': ''},
    {'name': 'TOTO',   'short': 'TOTO',   'kw': '', 'runs': [], 'budget': ''},
]

# 舊版四家的圖示留著，其他客戶照當天主要的廣告目標給圖示
BRAND_EMOJI = {'李老闆': '🛒', '漁三': '🎣', '優逸': '💬', 'TOTO': '🏆'}
KIND_EMOJI  = {'sales': '🛒', 'messages': '💬', 'leads': '📋', 'traffic': '🔗', 'engagement': '📣'}
KIND_NAME   = {'sales': '銷售', 'messages': '訊息', 'leads': '名單', 'traffic': '流量', 'engagement': '互動'}

tw_now = datetime.now(timezone.utc) + timedelta(hours=8)   # 台灣時間

# 早上跑 → 回報昨日；下午跑 → 回報今日
if tw_now.hour < 12:
    report_date  = (tw_now - timedelta(days=1)).strftime("%Y-%m-%d")
    report_label = "昨日"
else:
    report_date  = tw_now.strftime("%Y-%m-%d")
    report_label = "今日截至目前"

warnings = []          # 卡片底部要提醒她的事
ws_ok = True           # 工作台讀得到嗎。讀不到就退回舊行為，不套走期條件


# ---------- Meta ----------
def api(path, params=""):
    url = f"https://graph.facebook.com/v25.0/{path}?access_token={META_TOKEN}{params}"
    req = Request(url, headers={"User-Agent": "DoraMonitor/1.0"})
    with urlopen(req, timeout=25) as r:
        return json.loads(r.read())


def api_all(path, params="", max_pages=6):
    """會翻頁的版本：走期累計那份資料一天一列，量比較大"""
    out, url = [], None
    for _ in range(max_pages):
        if url is None:
            data = api(path, params)
        else:
            with urlopen(Request(url, headers={"User-Agent": "DoraMonitor/1.0"}), timeout=25) as r:
                data = json.loads(r.read())
        out.extend(data.get("data", []))
        url = (data.get("paging") or {}).get("next")
        if not url:
            break
    return out


# ---------- 工作台（唯讀）----------
def _b64u(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b'=')


def ws_token(key_path):
    """自己簽 JWT 換 OAuth token。RSA 簽章交給系統 openssl，不裝任何套件"""
    with open(key_path) as f:
        sa = json.load(f)
    ts = int(time.time())
    header = _b64u(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    claims = _b64u(json.dumps({
        "iss": sa['client_email'],
        "scope": "https://www.googleapis.com/auth/firebase.database "
                 "https://www.googleapis.com/auth/userinfo.email",
        "aud": "https://oauth2.googleapis.com/token",
        "iat": ts, "exp": ts + 3600,
    }).encode())
    signing_input = header + b'.' + claims
    fd, pem = tempfile.mkstemp(suffix='.pem')
    try:
        os.write(fd, sa['private_key'].encode()); os.close(fd); os.chmod(pem, 0o600)
        sig = subprocess.run(['openssl', 'dgst', '-sha256', '-sign', pem],
                             input=signing_input, capture_output=True, check=True).stdout
    finally:
        os.unlink(pem)
    body = urlencode({'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer',
                      'assertion': (signing_input + b'.' + _b64u(sig)).decode()}).encode()
    with urlopen(Request('https://oauth2.googleapis.com/token', data=body, method='POST'), timeout=15) as r:
        return json.loads(r.read())['access_token']


def fetch_clients():
    """工作台上狀態是「進行中」的客戶。讀不到就退回改版前那四家"""
    key, db, room = os.environ.get('WS_SA_KEY'), os.environ.get('WS_DB_URL'), os.environ.get('WS_ROOM')
    global ws_ok
    if not (key and db and room and os.path.exists(os.path.expanduser(key))):
        warnings.append("讀不到工作台，只列改版前那四家")
        ws_ok = False
        return FALLBACK_CLIENTS
    try:
        tok = ws_token(os.path.expanduser(key))
        req = Request(f"{db.rstrip('/')}/{room}/clients.json", headers={'Authorization': f'Bearer {tok}'})
        with urlopen(req, timeout=20) as r:
            data = json.loads(r.read()) or {}
        rows = [c for c in data.values() if isinstance(c, dict) and c.get('active')]
        return rows or FALLBACK_CLIENTS
    except Exception as e:
        print(f"Warning: workspace read failed: {e}", file=sys.stderr)
        warnings.append("讀不到工作台，只列改版前那四家")
        ws_ok = False
        return FALLBACK_CLIENTS


def runs_of(c):
    """走期。舊資料是單一字串 run，新的是 runs 陣列"""
    rs = c.get('runs')
    if isinstance(rs, list):
        return [r for r in rs if isinstance(r, dict) and (r.get('start') or r.get('end'))]
    if isinstance(rs, dict):
        return [r for r in rs.values() if isinstance(r, dict) and (r.get('start') or r.get('end'))]
    return []


def current_run(c, today):
    rs = runs_of(c)
    if not rs:
        return None
    return next((r for r in rs if (not r.get('start') or r['start'] <= today)
                 and (not r.get('end') or r['end'] >= today)), None)


def parse_budget(s):
    """「$28,000 / 月」「$16,563／15天」→ 取整期預算的數字；讀不出來就回 0"""
    m = re.search(r'(\d[\d,]*)', str(s or ''))
    return float(m.group(1).replace(',', '')) if m else 0.0


# ---------- 把活動歸給客戶 ----------
clients = fetch_clients()

match_keys = []      # (比對詞, 客戶)
skipped_short = []
for c in clients:
    pool = [c.get('name'), c.get('short')] + \
           [k.strip() for k in str(c.get('kw') or '').replace('，', ',').split(',')]
    for k in pool:
        k = (k or '').strip()
        if not k:
            continue
        # 單字關鍵字（「沐」「Y」「H」）會誤中別家，一律跳過
        if len(k) < 2:
            skipped_short.append((k, c.get('short') or c.get('name')))
        else:
            match_keys.append((k.lower(), c))


def match_client(campaign_name):
    """活動名稱裡包含客戶的全名／簡稱／關鍵字就算那一家，比對到的字最長的優先"""
    low = (campaign_name or '').lower()
    best, blen = None, 0
    for k, c in match_keys:
        if k in low and len(k) > blen:
            best, blen = c, len(k)
    return best


def kind_of(name, objective, actions):
    """這個活動要看哪一種數字：先看實際跑出什麼，再看目標與命名"""
    def has(*types):
        return any(a.get('action_type') in types and float(a.get('value', 0) or 0) > 0
                   for a in (actions or []))
    if has('purchase', 'offsite_conversion.fb_pixel_purchase', 'omni_purchase'):
        return 'sales'
    if has('onsite_conversion.messaging_conversation_started_7d',
           'messaging_conversation_started_7d'):
        return 'messages'
    if has('lead', 'offsite_conversion.fb_pixel_lead', 'onsite_conversion.lead_grouped'):
        return 'leads'
    # 當天還沒跑出成果時，靠目標與活動名稱判斷
    if objective in ('OUTCOME_SALES',):
        return 'sales'
    if objective in ('OUTCOME_LEADS',):
        return 'leads'
    if objective in ('LINK_CLICKS', 'OUTCOME_TRAFFIC'):
        return 'traffic'
    if '訊息' in (name or '') or 'MSG' in (name or '').upper():
        return 'messages'
    return 'engagement'


def val(actions, *types):
    for a in (actions or []):
        if a.get('action_type') in types:
            try:
                return float(a.get('value', 0))
            except Exception:
                return 0.0
    return 0.0


def f(v):
    try:
        return float(v)
    except Exception:
        return 0.0


# ---------- 抓數字 ----------
# 一次抓「最早走期起日 ～ 回報日」的每日數字，回報日那天的當日成效與整期累計都從這份切出來，
# 不用為了累計再多打一輪 API
today_str = tw_now.strftime("%Y-%m-%d")
starts = [r['start'] for c in clients for r in runs_of(c) if r.get('start') and r['start'] <= report_date]
since = min(starts) if starts else report_date
# 最多回看 120 天，免得有人填了很久以前的走期把資料量拉爆
floor = (datetime.fromisoformat(report_date) - timedelta(days=120)).strftime("%Y-%m-%d")
since = max(since, floor)

try:
    accounts = [(a['account_id'], a.get('name', ''))
                for a in api('me/adaccounts', '&fields=name,account_id&limit=100').get('data', [])]
except Exception as e:
    print(f"Warning: adaccounts error: {e}", file=sys.stderr)
    # 問不到就用改版前那三個帳戶
    accounts = [("1011359997807756", ""), ("1711564422807708", ""), ("1082805773432972", "")]
    warnings.append("問不到廣告帳戶清單，只掃原本那三個")

daily = []          # 每個活動每一天一列
account_errors = []
for acc_id, acc_name in accounts:
    try:
        rows = api_all(
            f"act_{acc_id}/insights",
            f'&level=campaign&time_increment=1'
            f'&fields=campaign_id,campaign_name,objective,spend,actions,action_values,reach,impressions'
            f'&time_range={{"since":"{since}","until":"{report_date}"}}&limit=300'
        )
        daily.extend(rows)
    except Exception as e:
        account_errors.append(f"{acc_id}: {e}")
        print(f"Warning: {acc_id} insights error: {e}", file=sys.stderr)

# 分成兩桶：回報日當天的（要列數字）與整期累計的（算預算花了幾成）
today_rows, spent_by_client = [], {}
orphan_spend, orphan_names = 0.0, {}
for r in daily:
    if f(r.get('spend')) <= 0:
        continue
    c = match_client(r.get('campaign_name', ''))
    if not c:
        if r.get('date_start') == report_date:
            orphan_spend += f(r.get('spend'))
            head = (r.get('campaign_name') or '?').split('_')[0][:10]
            orphan_names[head] = orphan_names.get(head, 0.0) + f(r.get('spend'))
        continue
    cid = c.get('id') or (c.get('short') or c.get('name'))
    if r.get('date_start') == report_date:
        today_rows.append((cid, c, r))
    spent_by_client.setdefault(cid, []).append((r.get('date_start'), f(r.get('spend'))))


# ---------- 組每一家的內容 ----------
# ---------- 卡片的顏色與零件 ----------
# 卡片沿用紫色系。進度條三個狀態的顏色跑過色盲模擬（validate_palette.js，最差 CVD ΔE 15.1 全通過），
# 而且每條旁邊都有百分比文字，不是只靠顏色分辨
INK      = "#2E2740"      # 數值
INK_SOFT = "#6E6784"      # 標籤
CARD_BG  = "#F7F5FB"      # 客戶卡底色
STATE = {                 # (進度條顏色, 未填滿的軌道色)
    'ok':   ("#7C5CBF", "#E5DDF5"),   # 正常
    'slow': ("#1FA08A", "#D3EFE8"),   # 花太慢
    'fast': ("#C0453B", "#F5DEDB"),   # 花太快
}


def compact(v, money=False, dec=0):
    """大數字縮短：25,431 → 2.5萬。手機一眼看得完最重要"""
    pre = NTD if money else ""
    if v >= 10000:
        return f"{pre}{v / 10000:.1f}萬"
    if dec:
        return f"{pre}{v:,.{dec}f}"
    return f"{pre}{v:,.0f}"


def metric_tiles(kind, agg):
    """回傳 [(標籤, 數值)]，之後排成一格一格的方塊"""
    spend = agg['spend']
    t = [("花費", compact(spend, money=True))]
    if kind == 'sales':
        qty, rev = agg['purchase'], agg['revenue']
        t += [("購買", compact(qty)), ("金額", compact(rev, money=True)),
              ("ROAS", f"{rev / spend:.2f}" if spend > 0 else "-"),
              ("CPA", compact(spend / qty, money=True) if qty > 0 else "-")]
    elif kind == 'messages':
        qty = agg['msg']
        t += [("對話", compact(qty)),
              ("每則", compact(spend / qty, money=True) if qty > 0 else "-")]
    elif kind == 'leads':
        qty = agg['lead']
        t += [("名單", compact(qty)),
              ("每筆", compact(spend / qty, money=True) if qty > 0 else "-")]
    elif kind == 'traffic':
        clicks, imp = agg['click'], agg['imp']
        t += [("點擊", compact(clicks)),
              ("CPC", compact(spend / clicks, money=True, dec=1) if clicks > 0 else "-"),
              ("CTR", f"{clicks / imp * 100:.1f}%" if imp > 0 else "-")]
    else:
        eng = agg['eng']
        t += [("互動", compact(eng)),
              ("觸及" + ("(合計)" if agg['n'] > 1 else ""), compact(agg['reach'])),
              ("CPE", compact(spend / eng, money=True, dec=2) if eng > 0 else "-")]
    return t


def tile_rows(tiles):
    """一排最多三格。四格排 2+2、五格排 3+2，比補空格好看"""
    n = len(tiles)
    per = 2 if n == 4 else 3
    rows = []
    for i in range(0, n, per):
        chunk = tiles[i:i + per]
        cells = []
        for label, value in chunk:
            cells.append({"type": "box", "layout": "vertical", "flex": 1, "contents": [
                {"type": "text", "text": label, "size": "xxs", "color": INK_SOFT},
                {"type": "text", "text": value, "size": "sm", "weight": "bold",
                 "color": INK, "margin": "none"},
            ]})
        while len(cells) < per:
            cells.append({"type": "box", "layout": "vertical", "flex": 1, "contents": [{"type": "filler"}]})
        rows.append({"type": "box", "layout": "horizontal", "spacing": "sm",
                     "margin": "md" if rows else "sm", "contents": cells})
    return rows


STATE_LABEL = {'fast': '⚡ 燒太快', 'slow': '🐢 偏慢', 'ok': '✓ 正常'}


def budget_block(pct, time_pct, spent, budget, state):
    """預算執行的區塊：標題列（大百分比＋狀態）→ 進度條 → 細節小字。
    原本標題跟走期都是同一種小灰字，糊在一起看不出哪是哪（2026-08-23 她回報）"""
    fill, track = STATE[state]
    w = max(2, min(100, int(round(pct))))
    head = {"type": "box", "layout": "baseline", "contents": [
        {"type": "text", "text": "預算執行", "size": "xs", "color": INK_SOFT, "flex": 0},
        {"type": "text", "text": f"  {pct:.0f}%", "size": "lg", "weight": "bold",
         "color": INK, "flex": 0},
        {"type": "text", "text": STATE_LABEL[state], "size": "xs", "weight": "bold",
         "color": fill, "align": "end"}]}
    bar = {"type": "box", "layout": "vertical", "height": "10px", "backgroundColor": track,
           "cornerRadius": "5px", "margin": "md", "contents": [
               {"type": "box", "layout": "vertical", "width": f"{w}%", "height": "10px",
                "backgroundColor": fill, "cornerRadius": "5px",
                "contents": [{"type": "filler"}]}]}
    foot = {"type": "text",
            "text": f"時間過 {time_pct:.0f}%　{NTD}{spent:,.0f} / {NTD}{budget:,.0f}",
            "size": "xxs", "color": INK_SOFT, "margin": "sm"}
    # 白色底框：淺紫卡上再疊一層白，預算這區才不會跟上面的走期、下面的數字糊在一起
    return {"type": "box", "layout": "vertical", "margin": "lg", "backgroundColor": "#FFFFFF",
            "cornerRadius": "8px", "paddingAll": "12px", "contents": [head, bar, foot]}


def chip(text):
    """廣告目標的小標籤（流量／互動／訊息…）"""
    return {"type": "box", "layout": "horizontal", "margin": "md", "contents": [
        {"type": "box", "layout": "vertical", "flex": 0, "backgroundColor": "#EDE7F8",
         "cornerRadius": "4px", "paddingAll": "3px", "paddingStart": "10px", "paddingEnd": "10px",
         "contents": [{"type": "text", "text": text, "size": "xxs", "color": "#6B4FA8",
                       "weight": "bold"}]},
        {"type": "filler"}]}


def client_card(label, emoji, spend_total, run_line, budget, groups):
    """一家客戶一張淺紫底的卡"""
    head = {"type": "box", "layout": "horizontal", "contents": [
        {"type": "text", "text": f"{emoji} {label}", "weight": "bold", "size": "md",
         "color": "#3D3357", "wrap": True, "flex": 4},
        {"type": "text", "text": spend_total, "weight": "bold", "size": "md",
         "color": "#6B4FA8", "align": "end", "flex": 3}]}
    contents = [head]
    if run_line:
        contents.append({"type": "text", "text": run_line, "size": "xxs", "color": INK_SOFT,
                         "wrap": True, "margin": "sm"})
    if budget:
        contents.append(budget)
    multi = len(groups) > 1
    for gname, tiles in groups:
        if multi and gname:
            contents.append(chip(gname))
        for row in tile_rows(tiles):
            contents.append(row)
    return {"type": "box", "layout": "vertical", "margin": "md", "backgroundColor": CARD_BG,
            "cornerRadius": "10px", "paddingAll": "14px", "contents": contents}


# 同一家、同一種目標的活動合併加總（屬於花藝一天十個活動，逐個列會爆版）
by_client = {}
for cid, c, r in today_rows:
    kind = kind_of(r.get('campaign_name'), r.get('objective'), r.get('actions'))
    d = by_client.setdefault(cid, {'c': c, 'kinds': {}, 'n': 0})
    d['n'] += 1
    a = d['kinds'].setdefault(kind, {'spend': 0.0, 'purchase': 0.0, 'revenue': 0.0, 'msg': 0.0,
                                     'lead': 0.0, 'click': 0.0, 'eng': 0.0, 'reach': 0.0,
                                     'imp': 0.0, 'n': 0})
    acts, vals = r.get('actions'), r.get('action_values')
    a['n'] += 1
    a['spend']    += f(r.get('spend'))
    a['reach']    += f(r.get('reach'))
    a['imp']      += f(r.get('impressions'))
    a['purchase'] += val(acts, 'purchase', 'offsite_conversion.fb_pixel_purchase', 'omni_purchase')
    a['revenue']  += val(vals, 'purchase', 'offsite_conversion.fb_pixel_purchase', 'omni_purchase')
    a['msg']      += val(acts, 'onsite_conversion.messaging_conversation_started_7d',
                         'messaging_conversation_started_7d')
    a['lead']     += val(acts, 'lead', 'offsite_conversion.fb_pixel_lead', 'onsite_conversion.lead_grouped')
    a['click']    += val(acts, 'link_click')
    a['eng']      += val(acts, 'post_engagement')

boxes = []
listed = set()
for cid, d in sorted(by_client.items(), key=lambda x: -sum(k['spend'] for k in x[1]['kinds'].values())):
    c = d['c']
    label = c.get('short') or c.get('name') or '（未命名客戶）'
    # 走期＝朱兒自己在跑的專案。沒填走期的是別人的案子（屬於花藝、卡威、MISO…），不列
    run = current_run(c, today_str)
    if ws_ok and not run:
        continue
    kinds = d['kinds']
    day_spend = sum(k['spend'] for k in kinds.values())
    main_kind = max(kinds.items(), key=lambda x: x[1]['spend'])[0]
    emoji = BRAND_EMOJI.get(label, KIND_EMOJI.get(main_kind, '📌'))

    # 走期與預算：預算花的速度跟時間過的速度比，才知道燒太快還是太慢
    run_line, budget_ui = "", None
    if run and run.get('start') and run.get('end'):
        st, en = run['start'], run['end']
        try:
            sd, ed = datetime.fromisoformat(st), datetime.fromisoformat(en)
            total_days = (ed - sd).days + 1
            passed = max(1, (datetime.fromisoformat(report_date) - sd).days + 1)
            time_pct = min(100.0, passed / total_days * 100)
            run_line = (f"第{run.get('no', '?')}期 {sd.strftime('%m/%d')}–{ed.strftime('%m/%d')}"
                        f"　第 {passed}/{total_days} 天")
            budget = parse_budget(c.get('budget'))
            if budget > 0:
                spent = sum(v for dt, v in spent_by_client.get(cid, []) if dt and st <= dt <= report_date)
                pct = spent / budget * 100
                gap = pct - time_pct
                state = 'fast' if gap > 10 else ('slow' if gap < -10 else 'ok')
                budget_ui = budget_block(pct, time_pct, spent, budget, state)
        except Exception:
            run_line, budget_ui = "", None

    groups = []
    for kind, agg in sorted(kinds.items(), key=lambda x: -x[1]['spend']):
        groups.append((KIND_NAME.get(kind, kind), metric_tiles(kind, agg)))

    boxes.append(client_card(label, emoji, compact(day_spend, money=True), run_line,
                             budget_ui, groups))
    listed.add(cid)

# 走期內、之前幾天有跑量、但回報日突然沒花費的 → 底部一行小字提醒（可能是預算沒跑或被關掉）
# 北元、高賀那種**在客戶自己帳戶投遞、從頭到尾抓不到**的，這裡就不會出現，不用她每天看
silent = []
for c in clients:
    cid = c.get('id') or (c.get('short') or c.get('name'))
    if cid in listed or not current_run(c, today_str):
        continue
    if spent_by_client.get(cid):      # 這段期間有跑過量，今天卻沒有 → 才值得提醒
        silent.append(c.get('short') or c.get('name'))
if silent:
    warnings.append("走期內但今天沒跑量：" + "、".join(silent[:6]))
if account_errors:
    warnings.append("部分帳戶抓取失敗，數字可能不完整")
if skipped_short:
    ks = "、".join(sorted(set(k for k, _ in skipped_short)))
    warnings.append(f"關鍵字「{ks}」只有一個字，會誤判所以沒採用")

if not boxes:
    if account_errors:
        print(json.dumps({"to": LINE_USER, "messages": [{
            "type": "text",
            "text": f"⚠️ 廣告日報產生失敗（{report_date} {report_label}）\n"
                    f"所有帳戶資料抓取都失敗，請檢查 Meta Token 是否過期或網路狀態。"
        }]}, ensure_ascii=False))
        sys.exit(0)
    print("NO_DATA")
    sys.exit(0)

rd      = datetime.fromisoformat(report_date)
days_zh = {0: "週一", 1: "週二", 2: "週三", 3: "週四", 4: "週五", 5: "週六", 6: "週日"}
rd_str  = f"{rd.strftime('%Y/%m/%d')}（{days_zh[rd.weekday()]}）{report_label}"


def bubble(body_boxes, title_suffix="", footer=True):
    foot = []
    for w in warnings:
        foot.append({"type": "text", "text": f"⚠️ {w}", "size": "xxs", "color": "#CC3333",
                     "wrap": True, "align": "center"})
    foot.append({"type": "text", "text": "廣告穩穩跑，成效天天好！", "size": "xs",
                 "color": "#7C5CBF", "align": "center", "margin": "sm" if warnings else "none"})
    b = {
        "type": "bubble",
        "header": {"type": "box", "layout": "vertical", "backgroundColor": "#9C88CC",
                   "paddingAll": "20px", "contents": [
                       {"type": "text", "text": f"📊 廣告日報{title_suffix}", "weight": "bold",
                        "size": "xl", "color": "#FFFFFF", "align": "center"},
                       {"type": "text", "text": rd_str, "size": "sm", "color": "#EDE7F6",
                        "align": "center", "margin": "sm"}]},
        "body": {"type": "box", "layout": "vertical", "spacing": "none", "paddingAll": "16px",
                 "contents": body_boxes},
    }
    if footer:
        b["footer"] = {"type": "box", "layout": "vertical", "backgroundColor": "#F0EBF8",
                       "paddingAll": "12px", "contents": foot}
    return b


# 客戶多的時候整張卡會超過 LINE 的大小上限，超過就拆成兩則推
def size_of(bs):
    return len(json.dumps(bubble(bs), ensure_ascii=False).encode())


chunks = [boxes]
if size_of(boxes) > 9000 and len(boxes) > 1:
    # 一個 box 就是一家客戶，照張數對半切，不會把同一家切成兩半
    half = max(1, len(boxes) // 2)
    chunks = [boxes[:half], boxes[half:]]

messages = []
for i, ch in enumerate(chunks):
    suffix = f"（{i + 1}/{len(chunks)}）" if len(chunks) > 1 else ""
    messages.append({"type": "flex",
                     "altText": f"廣告日報 {report_date} {report_label}",
                     "contents": bubble(ch, suffix, footer=(i == len(chunks) - 1))})

print(json.dumps({"to": LINE_USER, "messages": messages}, ensure_ascii=False))
PYEOF
)

if [ "$PAYLOAD" = "NO_DATA" ]; then
    echo "No matching campaigns. No notification sent."
    exit 0
fi

if [ -n "$DRY_RUN" ]; then
    echo "$PAYLOAD"
    exit 0
fi

curl -s -X POST 'https://api.line.me/v2/bot/message/push' \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $LINE_PUSH_TOKEN" \
  -d "$PAYLOAD"

echo "LINE sent: ads daily report (Flex)"
