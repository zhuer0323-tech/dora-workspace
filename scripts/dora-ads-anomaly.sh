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

# 2026-08-31 改：不再靠這裡單次長時間等待（原本最多等 3 分鐘）。
# Mac 常常是靠 Power Nap 那種 2~13 秒的短暫喚醒在撐，卡在這裡等 3 分鐘
# 反而更容易還沒等完就被系統打斷、整支腳本連 python 都沒機會執行到。
# 現在改成 plist 排 9:00-9:35／17:00-17:35 每 5 分鐘一次的多次輕量嘗試
# （見 dora-ads-daily.py 的 STATE_DIR／IS_LAST_TRY），這裡只做很短的網路檢查，
# 抓不到就直接進 python，讓它自己判斷要不要安靜跳過等下一次。
for i in 1 2; do
    curl -s --max-time 5 https://graph.facebook.com/v25.0/ > /dev/null 2>&1 && break
    sleep 5
done

exec /usr/bin/python3 "$HOME/Library/Scripts/dora-ads-daily.py" "$@"
