#!/usr/bin/env python3
"""代購許願池：幫朋友重設密碼。

朋友登入用「暱稱＋6 位數字密碼」，背後是 Firebase 帳號，email 由暱稱算出來
（跟網頁 nickEmail() 同一套：NFKC → 去頭尾空白 → 小寫 → SHA-256 前 24 碼）。
網頁端沒辦法幫別人改密碼，所以用服務帳號金鑰從這裡改。

用法：python3 scripts/dora-daigou-reset.py <暱稱> <新的 6 位數字密碼>
金鑰：~/Library/Scripts/dora-workspace-sa.json（不進版控）
"""
import base64, hashlib, json, os, re, subprocess, sys, tempfile, time, unicodedata
from urllib.parse import urlencode
from urllib.request import Request, urlopen

KEY = os.path.expanduser('~/Library/Scripts/dora-workspace-sa.json')
PROJECT = 'busan-trip-2026-201f8'
DOMAIN = '@dg.invalid'


def nick_email(nick):
    n = re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', nick).strip())
    return 'u' + hashlib.sha256(n.strip().lower().encode()).hexdigest()[:24] + DOMAIN


def _b64u(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b'=')


def token():
    sa = json.load(open(KEY))
    ts = int(time.time())
    h = _b64u(json.dumps({'alg': 'RS256', 'typ': 'JWT'}).encode())
    c = _b64u(json.dumps({'iss': sa['client_email'], 'scope': 'https://www.googleapis.com/auth/cloud-platform',
                          'aud': 'https://oauth2.googleapis.com/token', 'iat': ts, 'exp': ts + 3600}).encode())
    si = h + b'.' + c
    fd, pem = tempfile.mkstemp(suffix='.pem')
    try:
        os.write(fd, sa['private_key'].encode()); os.close(fd); os.chmod(pem, 0o600)
        sig = subprocess.run(['openssl', 'dgst', '-sha256', '-sign', pem], input=si, capture_output=True, check=True).stdout
    finally:
        os.unlink(pem)
    body = urlencode({'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer',
                      'assertion': (si + b'.' + _b64u(sig)).decode()}).encode()
    return json.loads(urlopen(Request('https://oauth2.googleapis.com/token', data=body, method='POST')).read())['access_token']


def api(path, data, tok):
    req = Request(f'https://identitytoolkit.googleapis.com/v1/projects/{PROJECT}/{path}', data=json.dumps(data).encode(),
                  headers={'Authorization': 'Bearer ' + tok, 'Content-Type': 'application/json'}, method='POST')
    return json.loads(urlopen(req).read())


def main():
    if len(sys.argv) != 3 or not re.fullmatch(r'\d{6}', sys.argv[2]):
        sys.exit('用法：python3 scripts/dora-daigou-reset.py <暱稱> <新的 6 位數字密碼>')
    nick, pw = sys.argv[1], sys.argv[2]
    tok = token()
    users = api('accounts:lookup', {'email': [nick_email(nick)]}, tok).get('users', [])
    if not users:
        sys.exit(f'找不到暱稱「{nick}」，確認大小寫與空白是不是跟報名時一樣')
    api('accounts:update', {'localId': users[0]['localId'], 'password': pw}, tok)
    print(f'「{nick}」的密碼改好了')


if __name__ == '__main__':
    main()
