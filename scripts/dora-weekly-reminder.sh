#!/bin/bash
# 廣告回報提醒 - 週三／四／五 9:00 各跑一次，只推「今天」要回報的客戶
# 名單來源：工作台客戶的「廣告回報日」（wk：'3' 週三／'4' 週四／'5' 週五，舊的布林 True 當週三）
# 只讀不寫。腳本必須放在 ~/Library/Scripts/，放 ~/Downloads 會被 macOS TCC 擋

set -euo pipefail

for i in $(seq 1 6); do
    if curl -s --max-time 3 https://api.line.me > /dev/null 2>&1; then break; fi
    sleep 5
done

source /Users/angela/Library/Scripts/dora.env

MSG=$(WS_SA_KEY="${WS_SA_KEY:-}" WS_DB_URL="${WS_DB_URL:-}" WS_ROOM="${WS_ROOM:-}" python3 << 'PYEOF'
import base64, json, os, subprocess, sys, tempfile, time
from datetime import datetime
from urllib.parse import urlencode
from urllib.request import Request, urlopen

sa_key  = os.environ.get('WS_SA_KEY', '')
db_url  = os.environ.get('WS_DB_URL', '').rstrip('/')
ws_room = os.environ.get('WS_ROOM', '')

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

try:
    if not (sa_key and db_url and ws_room and os.path.exists(sa_key)):
        raise RuntimeError('工作台設定不全')
    tok = ws_token(sa_key)
    req = Request(f'{db_url}/{ws_room}/clients.json', headers={'Authorization': f'Bearer {tok}'})
    with urlopen(req, timeout=15) as r:
        clients = json.loads(r.read()) or {}
except Exception:
    # 讀不到就不推，免得每週三丟一則沒用的訊息；失敗原因看 log
    print('', end=''); sys.exit(0)

WKNAME = {'3': '週三', '4': '週四', '5': '週五'}

def wk_of(c):
    v = c.get('wk')
    if v is True:
        return '3'
    return str(v) if str(v) in WKNAME else ''

# launchd 跑在本機時間，直接用今天星期幾（1=一 … 7=日）
today_wd = str(datetime.now().isoweekday())
rows = [c for c in clients.values()
        if isinstance(c, dict) and c.get('active') and wk_of(c) == today_wd]
if not rows:
    # 今天沒有人要回報 → 不推
    print('', end=''); sys.exit(0)

rows.sort(key=lambda c: c.get('order', 0))
lines = [f'🗓 今天{WKNAME[today_wd]}，該做廣告回報了', '', f'這 {len(rows)} 家要回報：']
def cur_run(c):
    """當期走期：含今天的那一期，都不含就用最後一期（走期剛結束還沒排下一期）"""
    rs = [r for r in (c.get('runs') or [])
          if isinstance(r, dict) and (r.get('start') or r.get('end'))]
    if not rs:
        return ''
    today = datetime.now().strftime('%Y-%m-%d')
    hit = next((r for r in rs
                if (not r.get('start') or r['start'] <= today)
                and (not r.get('end') or r['end'] >= today)), rs[-1])
    md = lambda d: f'{int(d[5:7])}/{int(d[8:10])}' if d and len(d) >= 10 else '?'
    no = str(hit.get('no') or '').strip()
    return (f'第{no}期 ' if no else '') + f"{md(hit.get('start'))}-{md(hit.get('end'))}"

for c in rows:
    nm = (c.get('short') or c.get('name') or '').strip()
    run = cur_run(c)
    lines.append(f'・{nm}' + (f'（{run}）' if run else ''))
lines += ['', '到電腦跟 Claude 說「跑今天的廣告回報」就好，', '數字、分析我會一起弄好推回來給你。']
print('\n'.join(lines))
PYEOF
)

if [ -z "$MSG" ]; then
    echo "$(date '+%F %T') 今天沒有要回報的客戶或讀不到工作台，這次不推"
    exit 0
fi

curl -s -X POST https://api.line.me/v2/bot/message/push \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $LINE_PUSH_TOKEN" \
    -d "$(python3 -c 'import json,sys,os
msg = sys.stdin.read()
print(json.dumps({"to": os.environ["LINE_USER_ID"],
                  "messages": [{"type": "text", "text": msg}]}, ensure_ascii=False))' <<< "$MSG")" \
    > /dev/null

echo "$(date '+%F %T') 廣告回報提醒已推播"
