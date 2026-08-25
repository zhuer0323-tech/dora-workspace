#!/usr/bin/env python3
"""廣告回報跑腿：看工作台有沒有從 LINE 排進來的待辦，有就叫 Claude Code 跑完推回 LINE。

為什麼要這一支：LINE 那邊的 Worker 沒有 AI，自己算不出分析。這支跑在朱兒的 Mac 上，
用現成的 Claude Code（她已經在付的訂閱）當引擎，不用另外接付費 API。

launchd 每分鐘叫一次。沒有待辦就直接結束，什麼都不做。
腳本必須放在 ~/Library/Scripts/，放 ~/Downloads 會被 macOS TCC 擋。
"""
import base64, json, os, re, subprocess, sys, tempfile, time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ENV     = os.path.expanduser('~/Library/Scripts/dora.env')
CLAUDE  = '/Users/angela/.local/bin/claude'
WORKDIR = '/Users/angela/Downloads/Dora專屬'
TIMEOUT = 900              # 一筆最多跑 15 分鐘
MAX_AGE = 6 * 3600         # 超過 6 小時就不補跑了（電腦關太久，那天的數字她也不要了）
LOCK    = '/tmp/dora-report-runner.lock'
CLIENTS = os.path.join(WORKDIR, '200_Reference', 'clients')

# 只開跑回報會用到的：讀檔、查廣告數字、跑讀工作台的腳本。
# 不給寫檔與推播的權限——推播由這支自己做，才不會被 AI 亂推
ALLOWED = ','.join([
    'Skill', 'Read', 'Glob', 'Grep',
    'Bash(python3:*)',
    'mcp__claude_ai__ads_get_ad_entities',
    'mcp__claude_ai__ads_get_creatives',
    'mcp__claude_ai__ads_get_ad_accounts',
    'mcp__claude_ai__ads_get_field_context',
])

PROMPT = """跑「{client}」的廣告回報。

- 照 `廣告回報` skill 與 `200_Reference/clients/` 裡這家客戶檔的規格做
- **只輸出那一則要傳給客戶的文字**：前後不要加任何說明、不要 markdown 程式碼框、
  不要「以下是…」這種開場白
- 不要推 LINE、不要寫檔案、不要 git commit（推播由外層腳本處理）
- 抓不到數字或認不出客戶，就只回一句話說明原因
"""


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


def db_get(cfg, tok, path):
    req = Request(f"{cfg['WS_DB_URL'].rstrip('/')}/{cfg['WS_ROOM']}/{path}.json",
                  headers={'Authorization': f'Bearer {tok}'})
    with urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def db_patch(cfg, tok, path, obj):
    req = Request(f"{cfg['WS_DB_URL'].rstrip('/')}/{cfg['WS_ROOM']}/{path}.json",
                  data=json.dumps(obj, ensure_ascii=False).encode(),
                  headers={'Authorization': f'Bearer {tok}', 'Content-Type': 'application/json'},
                  method='PATCH')
    with urlopen(req, timeout=15) as r:
        r.read()


def line_push(cfg, text):
    body = json.dumps({'to': cfg['LINE_USER_ID'],
                       'messages': [{'type': 'text', 'text': text[:4900]}]},
                      ensure_ascii=False).encode()
    req = Request('https://api.line.me/v2/bot/message/push', data=body, method='POST',
                  headers={'Content-Type': 'application/json',
                           'Authorization': f"Bearer {cfg['LINE_PUSH_TOKEN']}"})
    with urlopen(req, timeout=20) as r:
        r.read()


def has_spec(client):
    """這家客戶有沒有回報規格。沒有的話硬叫 AI 只會生出爛東西（2026-08-24 耀聞水果事件）

    檔名比對容錯：去掉空白、不分大小寫（`of AZIKU.md` 這種）。
    客戶檔目錄整個不在（換機、路徑改了）就不擋，維持原本流程。
    """
    if not os.path.isdir(CLIENTS):
        return True
    want = ''.join(client.split()).lower()
    for fn in os.listdir(CLIENTS):
        if not fn.endswith('.md') or fn.startswith('_'):
            continue
        if ''.join(fn[:-3].split()).lower() != want:
            continue
        with open(os.path.join(CLIENTS, fn), encoding='utf-8') as f:
            md = f.read()
        # 檔案在、但只是照模板建的空殼（沒有規格那一段）一樣算沒規格
        return bool(re.search(r'^## [^\n]*(回報規格|週報規格)', md, re.M))
    return False


def run_claude(client):
    """叫 Claude Code 跑回報。工作目錄要在 Dora專屬，才讀得到 CLAUDE.md 與客戶檔"""
    p = subprocess.run(
        [CLAUDE, '-p', PROMPT.format(client=client), '--allowedTools', ALLOWED],
        cwd=WORKDIR, capture_output=True, text=True, timeout=TIMEOUT)
    out = (p.stdout or '').strip()
    if p.returncode != 0 and not out:
        raise RuntimeError((p.stderr or '').strip()[:300] or f'claude 回傳 {p.returncode}')
    return out


def main():
    # 同時只跑一份。上一筆還在跑時，這一分鐘就跳過
    if os.path.exists(LOCK) and time.time() - os.path.getmtime(LOCK) < TIMEOUT:
        return
    open(LOCK, 'w').write(str(os.getpid()))

    try:
        cfg = load_env()
        if not all(cfg.get(k) for k in ('WS_SA_KEY', 'WS_DB_URL', 'WS_ROOM', 'LINE_PUSH_TOKEN')):
            print('設定不全，跳過'); return
        tok = ws_token(cfg['WS_SA_KEY'])
        jobs = db_get(cfg, tok, 'reportJobs') or {}

        # 跑完的待辦留一週就清掉，不然會一直長
        cutoff = int((time.time() - 7 * 86400) * 1000)
        for j in list(jobs.values()):
            if isinstance(j, dict) and j.get('status') in ('done', 'error', 'expired', 'timeout', 'noSpec') \
               and (j.get('doneAt') or 0) < cutoff:
                db_patch(cfg, tok, 'reportJobs', {j['id']: None})

        pending = [j for j in jobs.values()
                   if isinstance(j, dict) and j.get('status') == 'pending']
        if not pending:
            return
        pending.sort(key=lambda j: j.get('createdAt', 0))

        for job in pending:
            jid, client = job['id'], job.get('client', '')
            age = time.time() - job.get('createdAt', 0) / 1000
            if age > MAX_AGE:
                db_patch(cfg, tok, f'reportJobs/{jid}', {'status': 'expired', 'doneAt': int(time.time() * 1000)})
                print(f'{jid} 太舊（{age/3600:.1f} 小時）跳過')
                continue

            # 沒規格就直說，不要叫 AI 硬做（省 1-2 分鐘、也省 Claude 額度）
            if not has_spec(client):
                line_push(cfg, f'「{client}」還沒寫過廣告回報規格，所以做不出回報。\n\n'
                               f'要先定一份格式（回報要放哪些數字、素材怎麼列、期間怎麼算），'
                               f'跟 Claude 說「幫{client}定回報規格」就會帶你走一次。定好之後再叫一次回報就行。')
                db_patch(cfg, tok, f'reportJobs/{jid}',
                         {'status': 'noSpec', 'doneAt': int(time.time() * 1000)})
                print(f"{time.strftime('%F %T')} {client} 沒有回報規格，跳過")
                continue

            db_patch(cfg, tok, f'reportJobs/{jid}', {'status': 'running'})
            print(f"{time.strftime('%F %T')} 開始跑 {client}")
            try:
                out = run_claude(client)
                if not out:
                    raise RuntimeError('沒有輸出')
                line_push(cfg, out)
                db_patch(cfg, tok, f'reportJobs/{jid}',
                         {'status': 'done', 'doneAt': int(time.time() * 1000)})
                print(f"{time.strftime('%F %T')} {client} 完成，{len(out)} 字")
            except subprocess.TimeoutExpired:
                line_push(cfg, f'⚠️「{client}」的廣告回報跑太久被中斷了，到電腦上手動跑一次比較快')
                db_patch(cfg, tok, f'reportJobs/{jid}', {'status': 'timeout', 'doneAt': int(time.time() * 1000)})
            except Exception as e:
                line_push(cfg, f'⚠️「{client}」的廣告回報沒跑成功：{str(e)[:200]}')
                db_patch(cfg, tok, f'reportJobs/{jid}',
                         {'status': 'error', 'error': str(e)[:300], 'doneAt': int(time.time() * 1000)})
                print('出錯：', e)
    finally:
        if os.path.exists(LOCK):
            os.unlink(LOCK)


if __name__ == '__main__':
    main()
