#!/bin/bash
# 週報提醒 - 每週三 9:00 推 LINE，列出這週要回報哪幾家
# 名單來源：工作台客戶的「每週三要做週報」開關（wk = true 且進行中）
# 只讀不寫。腳本必須放在 ~/Library/Scripts/，放 ~/Downloads 會被 macOS TCC 擋

set -euo pipefail

for i in $(seq 1 6); do
    if curl -s --max-time 3 https://api.line.me > /dev/null 2>&1; then break; fi
    sleep 5
done

source /Users/angela/Library/Scripts/dora.env

MSG=$(WS_SA_KEY="${WS_SA_KEY:-}" WS_DB_URL="${WS_DB_URL:-}" WS_ROOM="${WS_ROOM:-}" python3 << 'PYEOF'
import base64, json, os, subprocess, sys, tempfile, time
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

rows = [c for c in clients.values()
        if isinstance(c, dict) and c.get('wk') and c.get('active')]
if not rows:
    # 一家都沒開開關 → 不推（她還沒設定，推了也沒意義）
    print('', end=''); sys.exit(0)

rows.sort(key=lambda c: c.get('order', 0))
lines = ['🗓 今天週三，該做週報了', '', f'這 {len(rows)} 家要回報：']
for c in rows:
    nm = (c.get('short') or c.get('name') or '').strip()
    run = (c.get('run') or '').strip()
    lines.append(f'・{nm}' + (f'（走期 {run}）' if run else ''))
lines += ['', '到電腦跟 Claude 說「跑這週週報」就好，', '數字、分析我會一起弄好推回來給你。']
print('\n'.join(lines))
PYEOF
)

if [ -z "$MSG" ]; then
    echo "$(date '+%F %T') 沒有要回報的客戶或讀不到工作台，這次不推"
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

echo "$(date '+%F %T') 週報提醒已推播"
