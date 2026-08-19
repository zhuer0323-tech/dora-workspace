#!/usr/bin/env python3
"""讀工作台的客戶資料（唯讀）。

用途：週報要拿客戶的專案背景（走期、廣告格式、素材類型、預算），
      以及「這週三要回報哪幾家」的名單。

用法：
    python3 scripts/dora-ws-clients.py            # 列出週報開關打開的客戶
    python3 scripts/dora-ws-clients.py 漁三        # 查一家（可用全名、簡稱、認字關鍵字）
    python3 scripts/dora-ws-clients.py --all      # 列出全部進行中的客戶
    python3 scripts/dora-ws-clients.py --json 漁三 # output 原始 JSON

設定沿用早報那份 ~/Library/Scripts/dora.env（WS_SA_KEY / WS_DB_URL / WS_ROOM），
簽章一樣交給系統 openssl，不裝任何 python 套件。
"""
import base64, json, os, subprocess, sys, tempfile, time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ENV = os.path.expanduser('~/Library/Scripts/dora.env')


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
        os.write(fd, sa['private_key'].encode())
        os.close(fd)
        os.chmod(pem, 0o600)
        sig = subprocess.run(['openssl', 'dgst', '-sha256', '-sign', pem],
                             input=signing_input, capture_output=True, check=True).stdout
    finally:
        os.unlink(pem)
    body = urlencode({
        'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer',
        'assertion': (signing_input + b'.' + _b64u(sig)).decode(),
    }).encode()
    req = Request('https://oauth2.googleapis.com/token', data=body, method='POST')
    with urlopen(req, timeout=10) as r:
        return json.loads(r.read())['access_token']


def fetch_clients():
    cfg = load_env()
    key, db, room = cfg.get('WS_SA_KEY', ''), cfg.get('WS_DB_URL', '').rstrip('/'), cfg.get('WS_ROOM', '')
    if not (key and db and room and os.path.exists(key)):
        sys.exit('讀不到工作台設定（WS_SA_KEY / WS_DB_URL / WS_ROOM），請檢查 ~/Library/Scripts/dora.env')
    tok = ws_token(key)
    req = Request(f'{db}/{room}/clients.json', headers={'Authorization': f'Bearer {tok}'})
    with urlopen(req, timeout=15) as r:
        return json.loads(r.read()) or {}


def matches(c, q):
    """全名、簡稱、認字關鍵字任一個對得上就算這家"""
    q = q.strip().lower()
    pool = [c.get('name', ''), c.get('short', '')]
    pool += [k.strip() for k in (c.get('kw') or '').replace('，', ',').split(',')]
    return any(p and (q in p.lower() or p.lower() in q) for p in pool)


def show(c):
    out = [f"客戶：{c.get('name','')}" + (f"（{c['short']}）" if c.get('short') else '')]
    fields = [('專案走期', 'run'), ('廣告格式', 'fmt'), ('素材類型', 'mat'),
              ('廣告預算', 'budget'), ('我負責的項目', 'duty'), ('認字關鍵字', 'kw')]
    for label, k in fields:
        v = (c.get(k) or '').strip()
        out.append(f"  {label}：{v if v else '（還沒填）'}")
    out.append(f"  每週三要做週報：{'要' if c.get('wk') else '不用'}")
    out.append(f"  狀態：{'進行中' if c.get('active') else '已結案'}")
    if (c.get('note') or '').strip():
        out.append(f"  備註：{c['note'].strip()}")
    return '\n'.join(out)


def main():
    args = [a for a in sys.argv[1:]]
    as_json = '--json' in args
    args = [a for a in args if a != '--json']
    clients = fetch_clients()
    rows = [c for c in clients.values() if isinstance(c, dict)]

    if args and args[0] == '--all':
        hit = [c for c in rows if c.get('active')]
        title = '進行中的客戶'
    elif args:
        hit = [c for c in rows if matches(c, args[0])]
        title = f'查「{args[0]}」'
    else:
        hit = [c for c in rows if c.get('wk') and c.get('active')]
        title = '每週三要回報的客戶'

    if as_json:
        print(json.dumps(hit, ensure_ascii=False, indent=2))
        return
    if not hit:
        print(f'{title}：沒有符合的。'
              + ('（週報開關要在工作台的客戶卡裡打開）' if not args else ''))
        return
    print(f'== {title}（{len(hit)} 家）==')
    for c in sorted(hit, key=lambda x: x.get('order', 0)):
        print(show(c))
        print()


if __name__ == '__main__':
    main()
