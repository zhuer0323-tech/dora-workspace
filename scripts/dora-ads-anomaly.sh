#!/bin/bash
# 廣告日報的進入點（launchd 指向這支，所以檔名不動）。
# 真正的程式在 dora-ads-daily.py —— 2026-08-23 改寫成 Python，因為要處理
# 「先用 token、失敗才叫 Claude」兩條抓數字的路。
#
#   bash dora-ads-anomaly.sh          正常跑
#   bash dora-ads-anomaly.sh --dry    只印卡片內容不推播
#   bash dora-ads-anomaly.sh --raw    連抓回來的原始數字也印出來
#
# 舊版備份：.bak-20260823（只認四家的原版）、.bak-graph-20260823（改版後純 Graph 版）

set -euo pipefail

# 開機後 launchd 可能比網路早醒，等一下再跑
for i in $(seq 1 6); do
    curl -s --max-time 3 https://api.line.me > /dev/null 2>&1 && break
    sleep 5
done

exec /usr/bin/python3 "$HOME/Library/Scripts/dora-ads-daily.py" "$@"
