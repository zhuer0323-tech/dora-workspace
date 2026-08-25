#!/usr/bin/env python3
"""把客戶檔的「回報規格」推上工作台，給 LINE 小秘書的廣告回報用。

為什麼要這一步：規格寫在 `200_Reference/clients/*.md`（我維護的地方），
但 Cloudflare Worker 讀不到本機檔案。這支把規格文字＋抓數字要用的帳戶設定
寫進 Firebase 的 `clients/{id}/rpt`，Worker 就讀得到。

**單一來源仍然是客戶檔**：改規格 → 改客戶檔 → 跑一次這支。

用法：
    python3 scripts/sync-report-spec.py            # 全部同步
    python3 scripts/sync-report-spec.py 漁三        # 只同步一家
    python3 scripts/sync-report-spec.py --dry      # 只印出來，不寫雲端
"""
import io, json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from urllib.request import Request, urlopen

# 讀 Firebase 的那套（簽 JWT、換 token）直接沿用唯讀腳本的
import importlib.util
_spec = importlib.util.spec_from_file_location(
    "wsc", os.path.join(os.path.dirname(os.path.abspath(__file__)), "dora-ws-clients.py"))
wsc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wsc)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 抓數字要用的設定。**這裡是手動維護的**，新客戶要做回報就在這裡加一筆。
# accounts：去哪幾個廣告帳戶找（漁三跨兩個）
# prefix  ：活動名稱開頭是這幾個字才算這家
# exclude ：同帳戶裡要濾掉的別家活動
FEEDS = {
    '漁三': {
        'file': '漁三.md',
        'accounts': ['1711564422807708', '1082805773432972'],
        'prefix': ['漁三_'],
        'exclude': ['MISO_', 'Y_'],
    },
    '優逸': {
        'file': '優逸.md',
        'accounts': ['1011359997807756'],
        'prefix': ['優逸工藝_'],
        'exclude': ['一沐日_', 'H_', '卡威', '十八'],
    },
    'OF AZIKU': {
        'file': 'of AZIKU.md',
        'accounts': ['1082805773432972'],
        'prefix': ['of AZIKU_'],
        'exclude': [],
    },
    '工研院': {
        'file': '工研院.md',
        'accounts': ['1082805773432972'],
        'prefix': ['工研院_'],
        'exclude': [],
    },
    # ⚠️ 2026-08-24 建：prefix 還沒用真實活動名驗證過（當天 Meta 連線授權過期）。
    # 用「耀聞」兩個字當開頭是保守猜法（不管後台寫 耀聞_ 還是 耀聞水果_ 都吃得到）。
    # 第一次抓到真實活動名之後，把它改成完整開頭字串，並同步更新客戶檔。
    '耀聞水果': {
        'file': '耀聞水果.md',
        'accounts': ['1082805773432972'],
        'prefix': ['耀聞'],
        'exclude': ['工研院_', 'of AZIKU_', '沐拾_', '漁三_'],
    },
}


def spec_text(filename):
    """抓客戶檔裡「回報規格 / 週報規格」到下一個 ## 標題之間那段，
    再把「素材觀察」那段也一起帶上（分析要用）"""
    path = os.path.join(ROOT, '200_Reference', 'clients', filename)
    if not os.path.exists(path):
        return None
    md = io.open(path, encoding='utf-8').read()
    out = []
    for m in re.finditer(r'^## ([^\n]*(?:回報規格|週報規格|素材觀察)[^\n]*)\n(.*?)(?=\n## |\Z)',
                         md, re.S | re.M):
        out.append(f"## {m.group(1)}\n{m.group(2).strip()}")
    return '\n\n'.join(out) if out else None


def main():
    args = [a for a in sys.argv[1:]]
    dry = '--dry' in args
    args = [a for a in args if not a.startswith('--')]

    clients = wsc.fetch_clients()
    cfg = wsc.load_env()
    db, room = cfg.get('WS_DB_URL', '').rstrip('/'), cfg.get('WS_ROOM', '')
    tok = None if dry else wsc.ws_token(cfg['WS_SA_KEY'])

    done, missing = [], []
    for key, feed in FEEDS.items():
        if args and not any(a in key or key in a for a in args):
            continue
        # 在工作台找出這家客戶（用全名或簡稱比對）
        hit = next((c for c in clients.values()
                    if isinstance(c, dict) and key in (c.get('name', ''), c.get('short', ''))), None)
        if not hit:
            missing.append(f'{key}：工作台找不到這個客戶')
            continue
        spec = spec_text(feed['file'])
        if not spec:
            missing.append(f"{key}：客戶檔 {feed['file']} 裡沒有「回報規格」段落")
            continue

        rpt = {
            'accounts': feed['accounts'],
            'prefix': feed['prefix'],
            'exclude': feed['exclude'],
            'spec': spec,
        }
        if dry:
            print(f"== {key}（{hit['id']}）==")
            print(f"  帳戶 {rpt['accounts']}／開頭 {rpt['prefix']}／排除 {rpt['exclude']}")
            print(f"  規格 {len(spec)} 字\n")
        else:
            req = Request(f"{db}/{room}/clients/{hit['id']}/rpt.json",
                          data=json.dumps(rpt, ensure_ascii=False).encode(),
                          headers={'Authorization': f'Bearer {tok}',
                                   'Content-Type': 'application/json'},
                          method='PUT')
            with urlopen(req, timeout=15) as r:
                r.read()
        done.append(f"{key}（規格 {len(spec)} 字）")

    print('已同步：' + ('、'.join(done) if done else '無'))
    for m in missing:
        print('⚠️ ' + m)


if __name__ == '__main__':
    main()
