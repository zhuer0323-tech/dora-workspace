#!/bin/bash
# 裝一把新的 Meta token 進 dora.env。
# 2026-08-25 建：她的開發人員帳號被停權，developers.facebook.com 進不去，
# 舊的 dora-meta-token-setup.sh（要 App Secret ＋ Graph API Explorer）已經走不通。
# 這支改成吃「企業管理平台的系統使用者 token」——那把在 business.facebook.com 產生，
# 不碰開發者後台，而且**不會過期**。
#
#   bash dora-meta-token-install.sh <NEW_TOKEN>
#   bash dora-meta-token-install.sh <NEW_TOKEN> --force   # 少客戶也照裝
#
# 會先驗證再寫入：token 有沒有效、權限夠不夠、看得到的廣告帳戶有沒有比以前少
# （系統使用者只看得到掛在該 BM 底下的帳戶，直接分享給個人帳號的會漏掉）。
# 寫入前自動備份 dora.env。

set -euo pipefail

NEW_TOKEN="${1:-}"
FORCE="${2:-}"
ENV_FILE="$HOME/Library/Scripts/dora.env"
BASELINE="$HOME/Library/Scripts/dora-meta-accounts.baseline.json"

if [ -z "$NEW_TOKEN" ]; then
    echo "用法：bash $0 <NEW_TOKEN> [--force]"
    echo ""
    echo "怎麼拿系統使用者 token："
    echo "  1. business.facebook.com → 企業設定 → 使用者 → 系統使用者"
    echo "  2. 新增（或選現有的）→ 指派資產 → 把要看的廣告帳戶都勾起來"
    echo "  3. 產生新的權杖 → 選應用程式 → 勾 ads_read（要調預算才加 ads_management）"
    exit 1
fi

NEW_TOKEN="$NEW_TOKEN" FORCE="$FORCE" ENV_FILE="$ENV_FILE" BASELINE="$BASELINE" python3 <<'PY'
import io, json, os, shutil, sys, time
from datetime import datetime
from urllib.request import urlopen, Request

tok      = os.environ['NEW_TOKEN']
force    = os.environ['FORCE'] == '--force'
env_file = os.environ['ENV_FILE']
baseline = os.environ['BASELINE']
UA = {"User-Agent": "DoraMonitor/1.0"}

def get(path, params=""):
    u = f"https://graph.facebook.com/v25.0/{path}?access_token={tok}{params}"
    with urlopen(Request(u, headers=UA), timeout=25) as r:
        return json.loads(r.read())

# --- 1. token 本身 ---
try:
    d = get('debug_token', f'&input_token={tok}').get('data', {})
except Exception as e:
    print(f'❌ 驗證失敗，這把 token 打不通：{e}')
    sys.exit(1)

if not d.get('is_valid'):
    print(f"❌ 這把 token 無效：{(d.get('error') or {}).get('message', '（沒有說明）')}")
    sys.exit(1)

exp    = d.get('expires_at') or 0
scopes = d.get('scopes') or []
ttype  = d.get('type', '?')
print(f"✅ token 有效（類型 {ttype}）")
print(f"   到期：{datetime.fromtimestamp(exp).strftime('%Y-%m-%d %H:%M') if exp else '不會過期 ← 系統使用者就該是這樣'}")
print(f"   權限：{','.join(scopes) or '（空的）'}")

if 'ads_read' not in scopes and 'ads_management' not in scopes:
    print('❌ 少了 ads_read（或 ads_management），抓不到廣告數字。回去把權限勾起來再產生一次。')
    sys.exit(1)

# --- 2. 看得到哪些廣告帳戶，跟基準比 ---
try:
    accs = get('me/adaccounts', '&fields=account_id,name&limit=100').get('data', [])
except Exception as e:
    print(f'❌ 拿不到廣告帳戶清單：{e}')
    sys.exit(1)

now = {a['account_id']: a.get('name') for a in accs}
print(f"\n📋 這把 token 看得到 {len(now)} 個廣告帳戶")

missing = {}
if os.path.exists(baseline):
    base = json.load(io.open(baseline, encoding='utf-8'))
    old = {a['id']: a['name'] for a in base.get('accounts', [])}
    missing = {k: v for k, v in old.items() if k not in now}
    added   = {k: v for k, v in now.items() if k not in old}
    if missing:
        print(f"\n⚠️  比舊 token 少了 {len(missing)} 個（這幾家的數字以後抓不到）：")
        for k, v in missing.items():
            print(f"     - {v}")
        print("\n   系統使用者只看得到掛在這個企業管理平台底下的帳戶。")
        print("   補救：企業設定 → 帳戶 → 廣告帳戶，確認這幾個有加進 BM，")
        print("   再到系統使用者的「指派資產」把它們勾起來，然後重跑這支。")
    if added:
        print(f"\n➕ 多出 {len(added)} 個：" + '、'.join(added.values()))
    if not missing and not added:
        print("   跟舊 token 完全一樣，沒有少任何一家 👍")
else:
    print("   （沒有基準檔可比對）")

if missing and not force:
    print("\n🛑 沒有寫入。確定就這樣也要裝的話，重跑一次加 --force")
    sys.exit(2)

# --- 3. 寫入 ---
shutil.copy2(env_file, env_file + '.bak-' + time.strftime('%Y%m%d-%H%M%S'))
lines = io.open(env_file, encoding='utf-8').read().splitlines()
out, seen_tok, seen_exp = [], False, False
for ln in lines:
    if ln.startswith('META_TOKEN='):
        out.append('META_TOKEN=' + tok); seen_tok = True
    elif ln.startswith('META_TOKEN_EXPIRES='):
        # 0 = 不會過期，dora-campaign-end-checker.sh 看到 0 就不再提醒換 token
        out.append('META_TOKEN_EXPIRES=' + str(exp)); seen_exp = True
    else:
        out.append(ln)
if not seen_tok: out.append('META_TOKEN=' + tok)
if not seen_exp: out.append('META_TOKEN_EXPIRES=' + str(exp))
io.open(env_file, 'w', encoding='utf-8').write('\n'.join(out) + '\n')
os.chmod(env_file, 0o600)

# 換 token 成功＝這批帳戶就是新的基準
json.dump({'saved': time.strftime('%Y-%m-%d'),
           'note': '換 token 後看得到的廣告帳戶',
           'accounts': [{'id': k, 'name': v} for k, v in now.items()]},
          io.open(baseline, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)

print(f"\n✅ 已寫進 {env_file}（舊的備份在同層 .bak-*）")
print("   接著跑這個確認日報正常：bash ~/Library/Scripts/dora-ads-anomaly.sh --dry")
PY
