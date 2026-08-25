#!/usr/bin/env python3
"""AI 協作平台的背景引擎：規劃 → 製作 → 審閱 三個角色接力處理文案任務。

為什麼要這一支：跟「廣告回報」同一個道理，用她已經在付的 Claude Code 訂閱當引擎，
不用另外接付費 API。launchd 每分鐘叫一次，每次只推進「最舊那一件還在跑的任務」一步，
不會同時燒好幾個 claude -p 行程搶額度（2026-08-25 定案：一次只跑一件）。

三個角色接力規則：
- 規劃 寫完 → 進「製作」
- 製作 寫完 → 進「審閱」
- 審閱 說「通過」→ 整件完成；說「需要修改」→ 退回「製作」重做，
  最多來回 2 輪，超過就停下來標記「需要你決定」（不會自己一直打轉）
- 任何一個角色覺得資訊不夠、判斷不出來，會在回覆開頭寫 NEED_HUMAN，
  這支腳本看到就整件標記「需要你決定」並推播 LINE（其他時候不推播，不然天天洗版）
"""
import base64, json, os, re, subprocess, sys, tempfile, time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ENV     = os.path.expanduser('~/Library/Scripts/dora.env')
CLAUDE  = '/Users/zhuer/.local/bin/claude'
WORKDIR = '/Users/zhuer/Downloads/Dora專屬'
TIMEOUT = 600              # 一個角色最多跑 10 分鐘
LOCK    = '/tmp/dora-agent-hub-runner.lock'
CLIENTS = os.path.join(WORKDIR, '200_Reference', 'clients')
ROOM    = 'ah_4j2ppkn8rq'   # 改名要同步改 Firebase 規則
WS_ROOM_DEFAULT = 'ws_k7m2q9xr4t'
MAX_ROUNDS = 2               # 製作/審閱最多來回幾輪

SKILL_NAME = {'social': '社群文案撰寫', 'ad': '廣告文案撰寫'}
TYPE_LABEL = {'social': '社群文案', 'ad': '廣告投放文案'}

ROLE_LABEL = {'planner': '規劃', 'maker': '製作', 'reviewer': '審閱', 'human': '你', 'system': '系統'}

ALLOWED = ','.join(['Read', 'Glob', 'Grep'])

PLANNER_PROMPT = """你是「規劃」小幫手，負責幫這篇{type_label}訂出方向，接下來會有另一位「製作」小幫手照你的方向寫初稿。

任務資訊：
- 標題：{title}
- 客戶：{client}
- 需求：{brief}
{client_ctx}
{transcript_block}
請直接寫出：
1. 這次的核心方向與重點
2. 語氣/角度建議
3. 製作小幫手要特別留意的地方

如果需求描述得不夠清楚、你判斷不出方向，不要亂猜——在回覆最開頭寫一行
NEED_HUMAN: <你不確定的地方，一句話說清楚>
然後結束，不要往下硬寫。

只輸出規劃內容本身，不要加「好的」「以下是」這種開場白，不要用 markdown 標題符號。"""

MAKER_PROMPT = """你是「製作」小幫手，負責照規劃的方向寫這篇{type_label}的初稿。

任務資訊：
- 標題：{title}
- 客戶：{client}
{client_ctx}
{transcript_block}
{revision_note}
請先讀 `000_Agent/skills/{skill_name}/SKILL.md` 了解寫作方法與格式要求，
再讀 `200_Reference/writing-samples/` 裡有沒有相關語氣範例可以參考，
然後直接產出這篇的文案（不要在回覆裡問我問題、不要等我確認，直接寫出成品）。

只輸出文案本身，不要加「以下是初稿」這種開場白。

如果規劃給的方向你判斷不出該怎麼下筆（例如完全不知道客戶背景），
在回覆最開頭寫一行：NEED_HUMAN: <原因>，然後結束。"""

REVIEWER_PROMPT = """你是「審閱」小幫手，負責幫這篇{type_label}文案把關。

{transcript_block}
檢查重點：
- 品牌語氣合不合、有沒有明顯的 AI 味（常見毛病：每段都用「不是 X，是 Y」對仗、
  結尾硬收一句金句、三點式清單過度工整、用破折號——當轉折、開頭「其實」「真正的」）
- 內容跟需求有沒有對上、有沒有明顯問題

如果這版可以定稿了，具體說一下這版好在哪裡，最後一行單獨寫：
決定：通過

如果還需要修改，具體寫出要改哪裡、怎麼改，最後一行單獨寫：
決定：需要修改

只輸出審閱意見本身，不要加開場白。"""


def load_env():
    cfg = {}
    if os.path.exists(ENV):
        with open(ENV) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    cfg[k.strip()] = v.strip().strip('"').strip("'")
    return cfg


def _b64u(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b'=')


def ws_token(key_path):
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
    req = Request('https://oauth2.googleapis.com/token', data=body, method='POST')
    with urlopen(req, timeout=10) as r:
        return json.loads(r.read())['access_token']


def db_get(cfg, tok, room, path):
    req = Request(f"{cfg['WS_DB_URL'].rstrip('/')}/{room}/{path}.json",
                  headers={'Authorization': f'Bearer {tok}'})
    with urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def db_patch(cfg, tok, room, path, obj):
    req = Request(f"{cfg['WS_DB_URL'].rstrip('/')}/{room}/{path}.json",
                  data=json.dumps(obj, ensure_ascii=False).encode(),
                  headers={'Authorization': f'Bearer {tok}', 'Content-Type': 'application/json'},
                  method='PATCH')
    with urlopen(req, timeout=15) as r:
        r.read()


def db_post(cfg, tok, room, path, obj):
    req = Request(f"{cfg['WS_DB_URL'].rstrip('/')}/{room}/{path}.json",
                  data=json.dumps(obj, ensure_ascii=False).encode(),
                  headers={'Authorization': f'Bearer {tok}', 'Content-Type': 'application/json'},
                  method='POST')
    with urlopen(req, timeout=15) as r:
        return json.loads(r.read())['name']


def line_push(cfg, text):
    if not cfg.get('LINE_PUSH_TOKEN'):
        return
    body = json.dumps({'to': cfg['LINE_USER_ID'],
                       'messages': [{'type': 'text', 'text': text[:4900]}]},
                      ensure_ascii=False).encode()
    req = Request('https://api.line.me/v2/bot/message/push', data=body, method='POST',
                  headers={'Content-Type': 'application/json',
                           'Authorization': f"Bearer {cfg['LINE_PUSH_TOKEN']}"})
    with urlopen(req, timeout=20) as r:
        r.read()


def client_context(client_name):
    """撈客戶背景給小幫手參考：先看工作台的客戶卡，再看有沒有客戶檔。找不到就空字串。"""
    if not client_name:
        return ''
    parts = []
    md_path = None
    if os.path.isdir(CLIENTS):
        want = ''.join(client_name.split()).lower()
        for fn in os.listdir(CLIENTS):
            if fn.endswith('.md') and not fn.startswith('_') and ''.join(fn[:-3].split()).lower() == want:
                md_path = os.path.join(CLIENTS, fn)
                break
    if md_path:
        parts.append(f'（客戶檔在 `200_Reference/clients/{os.path.basename(md_path)}`，可以讀取參考品牌調性）')
    if not parts:
        return ''
    return '\n客戶背景：\n' + '\n'.join(parts) + '\n'


def build_transcript(messages):
    if not messages:
        return ''
    rows = sorted(messages.values(), key=lambda m: m.get('createdAt', 0))
    lines = ['目前為止的討論：']
    for m in rows:
        label = ROLE_LABEL.get(m.get('role'), m.get('role', '?'))
        lines.append(f"[{label}] {m.get('text', '')}")
    return '\n'.join(lines) + '\n'


def run_claude(prompt):
    p = subprocess.run(
        [CLAUDE, '-p', prompt, '--allowedTools', ALLOWED],
        cwd=WORKDIR, capture_output=True, text=True, timeout=TIMEOUT)
    out = (p.stdout or '').strip()
    if p.returncode != 0 and not out:
        raise RuntimeError((p.stderr or '').strip()[:300] or f'claude 回傳 {p.returncode}')
    return out


def parse_need_human(out):
    m = re.match(r'^NEED_HUMAN:\s*(.+)', out.strip(), re.I)
    return m.group(1).strip() if m else None


def parse_verdict(out):
    m = re.search(r'決定[：:]\s*(通過|需要修改)\s*$', out.strip())
    return m.group(1) if m else None


def process_task(cfg, tok, task):
    tid, stage = task['id'], task.get('stage')
    ttype = task.get('type', 'social')
    type_label = TYPE_LABEL.get(ttype, '文案')
    messages = db_get(cfg, tok, ROOM, f'messages/{tid}') or {}
    transcript_block = build_transcript(messages)
    ctx = client_context(task.get('client', ''))
    now_ms = int(time.time() * 1000)

    if stage == 'planning':
        role = 'planner'
        prompt = PLANNER_PROMPT.format(
            type_label=type_label, title=task.get('title', ''),
            client=task.get('client') or '（沒填，當作沒有特定客戶）',
            brief=task.get('brief', ''), client_ctx=ctx, transcript_block=transcript_block)
    elif stage == 'making':
        role = 'maker'
        round_no = task.get('round', 0)
        revision_note = ''
        if round_no > 0:
            revision_note = '\n審閱小幫手上一輪給了修改意見（看上面討論紀錄裡最新一則「審閱」），請照那個意見修改上一版初稿。\n'
        prompt = MAKER_PROMPT.format(
            type_label=type_label, title=task.get('title', ''),
            client=task.get('client') or '（沒填）', client_ctx=ctx,
            transcript_block=transcript_block, revision_note=revision_note,
            skill_name=SKILL_NAME.get(ttype, '社群文案撰寫'))
    elif stage == 'reviewing':
        role = 'reviewer'
        prompt = REVIEWER_PROMPT.format(type_label=type_label, transcript_block=transcript_block)
    else:
        return False

    print(f"{time.strftime('%F %T')} 開始跑 {tid} / {role}")
    try:
        out = run_claude(prompt)
    except Exception as e:
        db_patch(cfg, tok, ROOM, f'tasks/{tid}', {
            'stage': 'waiting_human', 'waitingKind': 'error',
            'waitingReason': f'{ROLE_LABEL[role]}小幫手這輪跑失敗了：{str(e)[:200]}',
            'resumeStage': stage, 'updatedAt': now_ms})
        line_push(cfg, f'⚠️「{task.get("title","")}」的{ROLE_LABEL[role]}小幫手跑失敗了，麻煩到協作平台看一下')
        return True

    need_human = parse_need_human(out)
    if need_human:
        db_post(cfg, tok, ROOM, f'messages/{tid}', {
            'role': role, 'text': f'我不確定：{need_human}', 'createdAt': now_ms})
        db_patch(cfg, tok, ROOM, f'tasks/{tid}', {
            'stage': 'waiting_human', 'waitingKind': 'needHuman',
            'waitingReason': need_human, 'resumeStage': stage, 'updatedAt': now_ms})
        line_push(cfg, f'🙋「{task.get("title","")}」的{ROLE_LABEL[role]}小幫手卡住了：\n{need_human}\n\n到協作平台回覆一下就能繼續')
        return True

    db_post(cfg, tok, ROOM, f'messages/{tid}', {'role': role, 'text': out, 'createdAt': now_ms})

    if role == 'planner':
        db_patch(cfg, tok, ROOM, f'tasks/{tid}', {'stage': 'making', 'updatedAt': now_ms})
    elif role == 'maker':
        db_patch(cfg, tok, ROOM, f'tasks/{tid}', {'stage': 'reviewing', 'updatedAt': now_ms})
    elif role == 'reviewer':
        verdict = parse_verdict(out)
        if verdict == '通過':
            db_patch(cfg, tok, ROOM, f'tasks/{tid}', {'stage': 'done', 'updatedAt': now_ms})
            line_push(cfg, f'✅「{task.get("title","")}」三階段都跑完了，審閱小幫手說可以定稿，到協作平台看成品')
        else:
            round_no = task.get('round', 0) + 1
            if verdict is None:
                db_patch(cfg, tok, ROOM, f'tasks/{tid}', {
                    'stage': 'waiting_human', 'waitingKind': 'needHuman',
                    'waitingReason': '審閱小幫手的回覆看不出通過還是要改，麻煩你看一下',
                    'resumeStage': 'reviewing', 'updatedAt': now_ms})
                line_push(cfg, f'🙋「{task.get("title","")}」的審閱結果我判斷不出來，到協作平台看一下')
            elif round_no > MAX_ROUNDS:
                db_patch(cfg, tok, ROOM, f'tasks/{tid}', {
                    'stage': 'waiting_human', 'waitingKind': 'maxRound',
                    'waitingReason': f'製作跟審閱已經來回改了 {MAX_ROUNDS} 輪，我先停下來，你要用目前這版定稿，還是再給個方向？',
                    'resumeStage': 'making', 'round': round_no, 'updatedAt': now_ms})
                line_push(cfg, f'🙋「{task.get("title","")}」來回改了 {MAX_ROUNDS} 輪還沒過，到協作平台看要不要直接定稿')
            else:
                db_patch(cfg, tok, ROOM, f'tasks/{tid}', {'stage': 'making', 'round': round_no, 'updatedAt': now_ms})
    return True


def main():
    if os.path.exists(LOCK) and time.time() - os.path.getmtime(LOCK) < TIMEOUT:
        return
    open(LOCK, 'w').write(str(os.getpid()))
    try:
        cfg = load_env()
        if not all(cfg.get(k) for k in ('WS_SA_KEY', 'WS_DB_URL')):
            print('設定不全，跳過'); return
        tok = ws_token(cfg['WS_SA_KEY'])
        tasks = db_get(cfg, tok, ROOM, 'tasks') or {}
        actionable = []
        for tid, t in tasks.items():
            if isinstance(t, dict) and t.get('stage') in ('planning', 'making', 'reviewing'):
                t = dict(t); t['id'] = tid
                actionable.append(t)
        if not actionable:
            return
        actionable.sort(key=lambda t: t.get('updatedAt', t.get('createdAt', 0)))
        process_task(cfg, ws_token(cfg['WS_SA_KEY']), actionable[0])
    finally:
        if os.path.exists(LOCK):
            os.unlink(LOCK)


if __name__ == '__main__':
    main()
