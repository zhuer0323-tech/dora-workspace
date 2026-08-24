#!/usr/bin/env python3
"""廣告日報：每天 9:00（昨日）與 17:00（今日截至目前）推一張卡片到 LINE。

2026-08-23 改版二：**不再用朱兒自己的 Meta App token**。
  她的 Meta 開發人員帳號被停權（違反開放平台使用條款 7.e.i.2），
  8/30 token 到期後就無法再產生新的。改成叫 Claude Code 用 claude.ai 的
  Meta Ads 連線抓數字——跟「廣告回報」同一條路，不必另接付費 API，也不吃她的 App。

分工：**Claude 只負責搬數字**（呼叫 ads 工具、輸出原始 JSON），
      卡片版面與推播由這支自己做，AI 不碰輸出格式。

用法：
    python3 dora-ads-daily.py           # 正常跑
    python3 dora-ads-daily.py --dry     # 只印卡片內容，不推播
    python3 dora-ads-daily.py --raw     # 連 Claude 抓回來的原始數字也印出來（除錯用）
"""
import base64, json, os, re, subprocess, sys, tempfile, time
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode
from urllib.request import urlopen, Request

ENV     = os.path.expanduser('~/Library/Scripts/dora.env')
CLAUDE  = '/Users/angela/.local/bin/claude'
WORKDIR = '/Users/angela/Downloads/Dora專屬'
FETCH_TIMEOUT = 600          # 抓數字最多跑 10 分鐘
NTD = "NT$"

DRY = '--dry' in sys.argv
RAW = '--raw' in sys.argv

# 卡片顏色。進度條三色跑過 dataviz 的 validate_palette.js（最差 CVD ΔE 15.1）
INK, INK_SOFT, CARD_BG = "#2E2740", "#6E6784", "#F7F5FB"
STATE = {'ok': ("#7C5CBF", "#E5DDF5"), 'slow': ("#1FA08A", "#D3EFE8"), 'fast': ("#C0453B", "#F5DEDB")}
STATE_LABEL = {'fast': '⚡ 燒太快', 'slow': '🐢 偏慢', 'ok': '✓ 正常'}
BRAND_EMOJI = {'李老闆': '🛒', '漁三': '🎣', '優逸': '💬', 'TOTO': '🏆'}

# results 的 indicator → 這家要看哪一種數字
KIND_BY_INDICATOR = [
    ('messaging',  ('messaging_conversation', 'messaging_conversation_started')),
    ('leads',      ('lead',)),
    ('sales',      ('purchase', 'omni_purchase')),
    ('traffic',    ('link_click', 'landing_page_view')),
    ('engagement', ('post_engagement', 'page_engagement')),
]
KIND_NAME  = {'sales': '銷售', 'messaging': '訊息', 'leads': '名單', 'traffic': '流量',
              'engagement': '互動', 'other': '成果'}
KIND_EMOJI = {'sales': '🛒', 'messaging': '💬', 'leads': '📋', 'traffic': '🔗',
              'engagement': '📣', 'other': '📌'}

warnings = []
ws_ok = True


def load_env():
    cfg = {}
    if os.path.exists(ENV):
        for line in open(ENV):
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                cfg[k.strip()] = v.strip().strip('"').strip("'")
    return cfg


CFG = load_env()

tw_now = datetime.now(timezone.utc) + timedelta(hours=8)
if tw_now.hour < 12:
    report_date, report_label = (tw_now - timedelta(days=1)).strftime("%Y-%m-%d"), "昨日"
else:
    report_date, report_label = tw_now.strftime("%Y-%m-%d"), "今日截至目前"
today_str = tw_now.strftime("%Y-%m-%d")


# ---------- 工作台（唯讀）----------
def _b64u(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b'=')


def ws_token(key_path):
    """自己簽 JWT 換 OAuth token，RSA 簽章交給系統 openssl，不裝任何套件"""
    sa = json.load(open(key_path))
    ts = int(time.time())
    header = _b64u(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    claims = _b64u(json.dumps({
        "iss": sa['client_email'],
        "scope": "https://www.googleapis.com/auth/firebase.database "
                 "https://www.googleapis.com/auth/userinfo.email",
        "aud": "https://oauth2.googleapis.com/token",
        "iat": ts, "exp": ts + 3600}).encode())
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
    global ws_ok
    key, db, room = CFG.get('WS_SA_KEY'), CFG.get('WS_DB_URL'), CFG.get('WS_ROOM')
    try:
        tok = ws_token(os.path.expanduser(key))
        req = Request(f"{db.rstrip('/')}/{room}/clients.json", headers={'Authorization': f'Bearer {tok}'})
        with urlopen(req, timeout=20) as r:
            data = json.loads(r.read()) or {}
        rows = [c for c in data.values() if isinstance(c, dict) and c.get('active')]
        if rows:
            return rows
    except Exception as e:
        print(f"Warning: workspace read failed: {e}", file=sys.stderr)
    ws_ok = False
    warnings.append("讀不到工作台，這次沒辦法判斷是誰的專案")
    return []


def runs_of(c):
    rs = c.get('runs')
    if isinstance(rs, list):
        return [r for r in rs if isinstance(r, dict) and (r.get('start') or r.get('end'))]
    if isinstance(rs, dict):
        return [r for r in rs.values() if isinstance(r, dict) and (r.get('start') or r.get('end'))]
    return []


def current_run(c, today):
    return next((r for r in runs_of(c)
                 if (not r.get('start') or r['start'] <= today) and (not r.get('end') or r['end'] >= today)),
                None)


def parse_budget(s):
    m = re.search(r'(\d[\d,]*)', str(s or ''))
    return float(m.group(1).replace(',', '')) if m else 0.0


# ---------- 叫 Claude 抓數字 ----------
ALLOWED = ','.join([
    'mcp__claude_ai__ads_get_ad_accounts',
    'mcp__claude_ai__ads_get_ad_entities',
    'mcp__claude_ai__ads_get_field_context',
    'Bash(python3:*)', 'Read', 'Write',
])

FETCH_PROMPT = """抓廣告日報要的數字，**寫進檔案**，不要印在回覆裡。

1. `ads_get_ad_accounts` 取得所有 `is_queryable` 為 true 的廣告帳戶。
2. 對**每一個**帳戶呼叫 `ads_get_ad_entities`，兩次：
   - 當日：`time_range={{"since":"{day}","until":"{day}"}}`
   - 累計：`time_range={{"since":"{since}","until":"{day}"}}` 且 `time_increment="1"`
     （要一天一列，之後才能照各家走期切）
   兩次都用：
     level="campaign"
     fields=["id","name","objective","amount_spent","results","cost_per_result",
             "reach","impressions","link_click","post_engagement","purchase_roas"]
     filtering=[{{"field":"campaign.amount_spent","operator":"GREATER_THAN","value":["0"]}}]
   工具輸出太大被存成檔案時，用 python3 讀那個檔取值，不要整份讀進對話。
3. 把結果寫成 JSON 存到 `{out}`，格式：
   {{"day": [{{"account":"<帳戶名>","name":"<活動名>","objective":"...","spend":<數字>,
              "result_indicator":"<results.indicator，沒有就空字串>","result_value":<數字>,
              "reach":<數字>,"impressions":<數字>,"link_click":<數字>,
              "post_engagement":<數字>,"roas":<數字或 0>}}],
    "range": [{{"name":"<活動名>","date":"<YYYY-MM-DD>","spend":<數字>}}]}}
   **金額一律轉成純數字**（"NT$1,234 TWD" → 1234）。沒有的欄位填 0。
4. 寫完只回一行：`OK <day 幾筆> <range 幾筆>`。不要解釋、不要貼 JSON。
"""


def fetch_via_graph(since):
    """優先走這條：直接用她自己的 Meta token 打 Graph API。
    快（幾秒）、不吃 Claude 額度。**但 token 2026-08-30 到期，
    開發人員帳號被停權後沒辦法再產生新的**，所以失敗就換下面那條。"""
    token = CFG.get('META_TOKEN')
    if not token:
        raise RuntimeError('沒有 META_TOKEN')

    def api(path, params=""):
        url = f"https://graph.facebook.com/v25.0/{path}?access_token={token}{params}"
        with urlopen(Request(url, headers={"User-Agent": "DoraMonitor/1.0"}), timeout=25) as r:
            return json.loads(r.read())

    def api_all(path, params="", max_pages=6):
        out, nxt = [], None
        for _ in range(max_pages):
            if nxt is None:
                data = api(path, params)
            else:
                with urlopen(Request(nxt, headers={"User-Agent": "DoraMonitor/1.0"}), timeout=25) as r:
                    data = json.loads(r.read())
            out.extend(data.get("data", []))
            nxt = (data.get("paging") or {}).get("next")
            if not nxt:
                break
        return out

    def val(actions, *types):
        for a in (actions or []):
            if a.get('action_type') in types:
                try:
                    return float(a.get('value', 0))
                except Exception:
                    return 0.0
        return 0.0

    accounts = [a['account_id'] for a in api('me/adaccounts', '&fields=account_id&limit=100').get('data', [])]
    day_rows, range_rows = [], []
    for acc in accounts:
        rows = api_all(f"act_{acc}/insights",
                       f'&level=campaign&time_increment=1'
                       f'&fields=campaign_name,objective,spend,actions,action_values,reach,impressions'
                       f'&time_range={{"since":"{since}","until":"{report_date}"}}&limit=300')
        for r in rows:
            spend = float(r.get('spend') or 0)
            if spend <= 0:
                continue
            name = r.get('campaign_name', '')
            range_rows.append({'name': name, 'date': r.get('date_start'), 'spend': spend})
            if r.get('date_start') != report_date:
                continue
            acts, vals = r.get('actions'), r.get('action_values')
            purchase = val(acts, 'purchase', 'offsite_conversion.fb_pixel_purchase', 'omni_purchase')
            msg = val(acts, 'onsite_conversion.messaging_conversation_started_7d',
                      'messaging_conversation_started_7d')
            lead = val(acts, 'lead', 'offsite_conversion.fb_pixel_lead', 'onsite_conversion.lead_grouped')
            click = val(acts, 'link_click')
            eng = val(acts, 'post_engagement')
            obj = r.get('objective') or ''
            # 成果類型**以活動目標為準**，不是看哪個 action 有數字。
            # （訊息廣告偶爾會歸因到一筆購買，purchase 優先的話整家會被誤判成銷售）
            if obj in ('OUTCOME_SALES', 'CONVERSIONS', 'PRODUCT_CATALOG_SALES'):
                ind, value = 'actions:purchase', purchase
            elif obj in ('OUTCOME_LEADS', 'LEAD_GENERATION'):
                ind, value = 'actions:lead', lead
            elif obj in ('LINK_CLICKS', 'OUTCOME_TRAFFIC'):
                ind, value = 'actions:link_click', click
            elif '訊息' in name or msg > 0 or obj == 'MESSAGES':
                ind, value = 'actions:messaging_conversation_started', msg
            elif purchase:
                ind, value = 'actions:purchase', purchase
            elif lead:
                ind, value = 'actions:lead', lead
            else:
                ind, value = 'actions:post_engagement', eng
            rev = val(vals, 'purchase', 'offsite_conversion.fb_pixel_purchase', 'omni_purchase')
            day_rows.append({
                'account': acc, 'name': name, 'objective': obj, 'spend': spend,
                'result_indicator': ind, 'result_value': value,
                'reach': float(r.get('reach') or 0), 'impressions': float(r.get('impressions') or 0),
                'link_click': click, 'post_engagement': eng,
                'roas': (rev / spend if spend else 0),
            })
    if not day_rows and not range_rows:
        raise RuntimeError('Graph 回來是空的')
    return day_rows, range_rows


def fetch_via_claude(since):
    """叫 Claude 用 Meta Ads 連線把數字抓回來，寫成 JSON 檔"""
    out = tempfile.mktemp(prefix='dora-ads-', suffix='.json')
    prompt = FETCH_PROMPT.format(day=report_date, since=since, out=out)
    p = subprocess.run([CLAUDE, '-p', prompt, '--allowedTools', ALLOWED],
                       cwd=WORKDIR, capture_output=True, text=True, timeout=FETCH_TIMEOUT)
    if not os.path.exists(out):
        raise RuntimeError((p.stdout or p.stderr or '')[-400:] or 'Claude 沒有產出檔案')
    try:
        data = json.load(open(out))
    finally:
        if not RAW:
            os.unlink(out)
        else:
            print(f'原始數字留在 {out}', file=sys.stderr)
    return data.get('day', []), data.get('range', [])


# ---------- 把活動歸給客戶 ----------
def build_matcher(clients):
    keys, skipped = [], []
    for c in clients:
        pool = [c.get('name'), c.get('short')] + \
               [k.strip() for k in str(c.get('kw') or '').replace('，', ',').split(',')]
        for k in pool:
            k = (k or '').strip()
            if not k:
                continue
            # 單字關鍵字（「沐」「Y」「H」）會誤中別家，不拿來比對
            if len(k) < 2:
                skipped.append((k, c))
            else:
                keys.append((k.lower(), c))
    return keys, skipped


def match_client(name, keys):
    low = (name or '').lower()
    best, blen = None, 0
    for k, c in keys:
        if k in low and len(k) > blen:
            best, blen = c, len(k)
    return best


def kind_of(indicator):
    ind = (indicator or '').lower()
    for kind, needles in KIND_BY_INDICATOR:
        if any(n in ind for n in needles):
            return kind
    return 'other'


# ---------- 卡片零件 ----------
def compact(v, money=False, dec=0):
    pre = NTD if money else ""
    if v >= 10000:
        return f"{pre}{v / 10000:.1f}萬"
    return f"{pre}{v:,.{dec}f}"


def metric_tiles(kind, a):
    spend = a['spend']
    t = [("花費", compact(spend, money=True))]
    n = a['result']
    if kind == 'sales':
        t += [("購買", compact(n)), ("ROAS", f"{a['roas']:.2f}" if a['roas'] else "-"),
              ("CPA", compact(spend / n, money=True) if n else "-")]
    elif kind == 'messaging':
        t += [("對話", compact(n)), ("每則", compact(spend / n, money=True) if n else "-")]
    elif kind == 'leads':
        t += [("名單", compact(n)), ("每筆", compact(spend / n, money=True) if n else "-")]
    elif kind == 'traffic':
        clicks = a['click'] or n
        t += [("點擊", compact(clicks)),
              ("CPC", compact(spend / clicks, money=True, dec=1) if clicks else "-"),
              ("CTR", f"{clicks / a['imp'] * 100:.1f}%" if a['imp'] else "-")]
    elif kind == 'engagement':
        eng = a['eng'] or n
        t += [("互動", compact(eng)),
              ("觸及" + ("(合計)" if a['n'] > 1 else ""), compact(a['reach'])),
              ("CPE", compact(spend / eng, money=True, dec=2) if eng else "-")]
    else:
        t += [("成果", compact(n)), ("每個", compact(spend / n, money=True) if n else "-")]
    return t


def tile_rows(tiles):
    n = len(tiles)
    per = 2 if n == 4 else 3
    rows = []
    for i in range(0, n, per):
        cells = [{"type": "box", "layout": "vertical", "flex": 1, "contents": [
            {"type": "text", "text": lb, "size": "xxs", "color": INK_SOFT},
            {"type": "text", "text": val, "size": "sm", "weight": "bold", "color": INK}]}
            for lb, val in tiles[i:i + per]]
        while len(cells) < per:
            cells.append({"type": "box", "layout": "vertical", "flex": 1, "contents": [{"type": "filler"}]})
        rows.append({"type": "box", "layout": "horizontal", "spacing": "sm",
                     "margin": "md" if rows else "sm", "contents": cells})
    return rows


def budget_block(pct, time_pct, spent, budget, state):
    """標題列（大百分比＋狀態）→ 進度條 → 細節小字，外面包白框跟其他資訊分開"""
    fill, track = STATE[state]
    w = max(2, min(100, int(round(pct))))
    head = {"type": "box", "layout": "baseline", "contents": [
        {"type": "text", "text": "預算執行", "size": "xs", "color": INK_SOFT, "flex": 0},
        {"type": "text", "text": f"  {pct:.0f}%", "size": "lg", "weight": "bold", "color": INK, "flex": 0},
        {"type": "text", "text": STATE_LABEL[state], "size": "xs", "weight": "bold",
         "color": fill, "align": "end"}]}
    bar = {"type": "box", "layout": "vertical", "height": "10px", "backgroundColor": track,
           "cornerRadius": "5px", "margin": "md", "contents": [
               {"type": "box", "layout": "vertical", "width": f"{w}%", "height": "10px",
                "backgroundColor": fill, "cornerRadius": "5px", "contents": [{"type": "filler"}]}]}
    foot = {"type": "text", "text": f"時間過 {time_pct:.0f}%　{NTD}{spent:,.0f} / {NTD}{budget:,.0f}",
            "size": "xxs", "color": INK_SOFT, "margin": "sm"}
    return {"type": "box", "layout": "vertical", "margin": "lg", "backgroundColor": "#FFFFFF",
            "cornerRadius": "8px", "paddingAll": "12px", "contents": [head, bar, foot]}


def chip(text):
    return {"type": "box", "layout": "horizontal", "margin": "md", "contents": [
        {"type": "box", "layout": "vertical", "flex": 0, "backgroundColor": "#EDE7F8",
         "cornerRadius": "4px", "paddingAll": "3px", "paddingStart": "10px", "paddingEnd": "10px",
         "contents": [{"type": "text", "text": text, "size": "xxs", "color": "#6B4FA8",
                       "weight": "bold"}]},
        {"type": "filler"}]}


def client_card(label, emoji, spend_total, run_line, budget, groups):
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
    for gname, tiles in groups:
        if len(groups) > 1 and gname:
            contents.append(chip(gname))
        contents.extend(tile_rows(tiles))
    return {"type": "box", "layout": "vertical", "margin": "md", "backgroundColor": CARD_BG,
            "cornerRadius": "10px", "paddingAll": "14px", "contents": contents}


def bubble(body_boxes, rd_str, suffix="", footer=True):
    b = {"type": "bubble",
         "header": {"type": "box", "layout": "vertical", "backgroundColor": "#9C88CC",
                    "paddingAll": "20px", "contents": [
                        {"type": "text", "text": f"📊 廣告日報{suffix}", "weight": "bold",
                         "size": "xl", "color": "#FFFFFF", "align": "center"},
                        {"type": "text", "text": rd_str, "size": "sm", "color": "#EDE7F6",
                         "align": "center", "margin": "sm"}]},
         "body": {"type": "box", "layout": "vertical", "spacing": "none", "paddingAll": "16px",
                  "contents": body_boxes}}
    if footer:
        foot = [{"type": "text", "text": f"⚠️ {w}", "size": "xxs", "color": "#CC3333",
                 "wrap": True, "align": "center"} for w in warnings]
        foot.append({"type": "text", "text": "廣告穩穩跑，成效天天好！", "size": "xs",
                     "color": "#7C5CBF", "align": "center", "margin": "sm" if warnings else "none"})
        b["footer"] = {"type": "box", "layout": "vertical", "backgroundColor": "#F0EBF8",
                       "paddingAll": "12px", "contents": foot}
    return b


def line_push(messages):
    body = json.dumps({'to': CFG['LINE_USER_ID'], 'messages': messages}, ensure_ascii=False).encode()
    req = Request('https://api.line.me/v2/bot/message/push', data=body, method='POST',
                  headers={'Content-Type': 'application/json',
                           'Authorization': f"Bearer {CFG['LINE_PUSH_TOKEN']}"})
    with urlopen(req, timeout=25) as r:
        return r.read().decode()


def main():
    clients = fetch_clients()
    keys, skipped = build_matcher(clients)

    # 抓數字的起日＝最早的走期起日（最多回看 120 天），走期累計與當日數字都從這裡切
    starts = [r['start'] for c in clients for r in runs_of(c) if r.get('start') and r['start'] <= report_date]
    floor = (datetime.fromisoformat(report_date) - timedelta(days=120)).strftime("%Y-%m-%d")
    since = max(min(starts), floor) if starts else report_date

    # 先用她自己的 token（快、不吃 Claude 額度），失敗才叫 Claude 抓（慢、吃額度）
    day_rows = range_rows = None
    errs = []
    for how, fn in (('token', fetch_via_graph), ('Claude', fetch_via_claude)):
        try:
            day_rows, range_rows = fn(since)
            if how == 'Claude':
                warnings.append("token 失效，這次是用 Claude 抓的")
            break
        except Exception as e:
            errs.append(f"{how}：{str(e)[:150]}")
            print(f"Warning: fetch via {how} failed: {e}", file=sys.stderr)
    if day_rows is None:
        msg = f"⚠️ 廣告日報抓不到數字（{report_date} {report_label}）\n" + "\n".join(errs)
        print(msg)
        if not DRY:
            line_push([{"type": "text", "text": msg}])
        return

    if RAW:
        print(json.dumps({'day': day_rows[:5], 'range': range_rows[:5]}, ensure_ascii=False, indent=2))

    # 走期累計：**每一家照自己的走期起日切**。
    # 抓數字是從所有客戶最早的那天開始抓的，不切的話走期晚開始的那家會被多算好幾天
    spent_by_client = {}
    for r in range_rows:
        c = match_client(r.get('name'), keys)
        if not c:
            continue
        run = current_run(c, today_str)
        st = (run or {}).get('start')
        d = r.get('date')
        if st and d and d < st:
            continue
        cid = c.get('id') or (c.get('short') or c.get('name'))
        spent_by_client[cid] = spent_by_client.get(cid, 0.0) + float(r.get('spend') or 0)

    # 當日數字，照客戶 → 成果類型分組
    by_client = {}
    for r in day_rows:
        if float(r.get('spend') or 0) <= 0:
            continue
        c = match_client(r.get('name'), keys)
        if not c:
            continue
        cid = c.get('id') or (c.get('short') or c.get('name'))
        d = by_client.setdefault(cid, {'c': c, 'kinds': {}, 'n': 0})
        d['n'] += 1
        kind = kind_of(r.get('result_indicator'))
        a = d['kinds'].setdefault(kind, {'spend': 0.0, 'result': 0.0, 'reach': 0.0, 'imp': 0.0,
                                         'click': 0.0, 'eng': 0.0, 'roas': 0.0, 'n': 0})
        a['n'] += 1
        for k, f in (('spend', 'spend'), ('result', 'result_value'), ('reach', 'reach'),
                     ('imp', 'impressions'), ('click', 'link_click'), ('eng', 'post_engagement')):
            a[k] += float(r.get(f) or 0)
        a['roas'] = max(a['roas'], float(r.get('roas') or 0))

    boxes, listed = [], set()
    for cid, d in sorted(by_client.items(), key=lambda x: -sum(k['spend'] for k in x[1]['kinds'].values())):
        c = d['c']
        run = current_run(c, today_str)
        # 走期＝她自己在跑的專案。沒填走期的是別人的案子（屬於花藝、卡威、MISO…），不列
        if not run:
            continue
        label = c.get('short') or c.get('name') or '（未命名客戶）'
        kinds = d['kinds']
        day_spend = sum(k['spend'] for k in kinds.values())
        main_kind = max(kinds.items(), key=lambda x: x[1]['spend'])[0]
        emoji = BRAND_EMOJI.get(label, KIND_EMOJI.get(main_kind, '📌'))

        run_line, budget_ui = "", None
        if run.get('start') and run.get('end'):
            try:
                sd, ed = datetime.fromisoformat(run['start']), datetime.fromisoformat(run['end'])
                total_days = (ed - sd).days + 1
                passed = max(1, (datetime.fromisoformat(report_date) - sd).days + 1)
                time_pct = min(100.0, passed / total_days * 100)
                run_line = (f"第{run.get('no', '?')}期 {sd.strftime('%m/%d')}–{ed.strftime('%m/%d')}"
                            f"　第 {passed}/{total_days} 天")
                budget = parse_budget(c.get('budget'))
                if budget > 0:
                    spent = spent_by_client.get(cid, 0.0)
                    pct = spent / budget * 100
                    gap = pct - time_pct
                    state = 'fast' if gap > 10 else ('slow' if gap < -10 else 'ok')
                    budget_ui = budget_block(pct, time_pct, spent, budget, state)
            except Exception:
                run_line, budget_ui = "", None

        groups = [(KIND_NAME.get(k, k), metric_tiles(k, a))
                  for k, a in sorted(kinds.items(), key=lambda x: -x[1]['spend'])]
        boxes.append(client_card(label, emoji, compact(day_spend, money=True), run_line, budget_ui, groups))
        listed.add(cid)

    # 走期內、之前有跑量、今天卻沒花費 → 底部提醒一行（北元、高賀那種永遠抓不到的不會出現）
    silent = [c.get('short') or c.get('name') for c in clients
              if (c.get('id') or c.get('short') or c.get('name')) not in listed
              and current_run(c, today_str)
              and spent_by_client.get(c.get('id') or c.get('short') or c.get('name'))]
    if silent:
        warnings.append("走期內但今天沒跑量：" + "、".join(silent[:6]))
    if skipped:
        warnings.append("關鍵字「" + "、".join(sorted(set(k for k, _ in skipped))) + "」只有一個字，沒採用")

    if not boxes:
        print("NO_DATA")
        return

    rd = datetime.fromisoformat(report_date)
    days_zh = {0: "週一", 1: "週二", 2: "週三", 3: "週四", 4: "週五", 5: "週六", 6: "週日"}
    rd_str = f"{rd.strftime('%Y/%m/%d')}（{days_zh[rd.weekday()]}）{report_label}"

    chunks = [boxes]
    if len(json.dumps(bubble(boxes, rd_str), ensure_ascii=False).encode()) > 9000 and len(boxes) > 1:
        half = max(1, len(boxes) // 2)
        chunks = [boxes[:half], boxes[half:]]

    messages = [{"type": "flex", "altText": f"廣告日報 {report_date} {report_label}",
                 "contents": bubble(ch, rd_str, f"（{i + 1}/{len(chunks)}）" if len(chunks) > 1 else "",
                                    footer=(i == len(chunks) - 1))}
                for i, ch in enumerate(chunks)]

    if DRY:
        print(json.dumps({'messages': messages}, ensure_ascii=False))
    else:
        print(line_push(messages))
        print(f"LINE sent: ads daily report ({len(boxes)} clients, {len(chunks)} msg)")


if __name__ == '__main__':
    main()
