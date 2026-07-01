#!/bin/bash
# Meta Token 設定工具
# 用途：把短效 token 換成 60 天長效 token，並儲存到 dora.env
#
# 使用方式：
#   bash ~/Library/Scripts/dora-meta-token-setup.sh <APP_SECRET> <SHORT_TOKEN>
#
# 取得 App Secret：
#   1. 開啟 https://developers.facebook.com/apps/1365710202180406/settings/basic/
#   2. 點「顯示」→ 輸入 Facebook 密碼 → 複製 App Secret
#
# 取得 Short Token：
#   1. 開啟 https://developers.facebook.com/tools/explorer/
#   2. 確認 Meta 應用程式 = Dora廣告監控，用戶或粉絲專頁 = 用戶權杖
#   3. 確認權限有 ads_management 和 ads_read
#   4. 點「Generate Access Token」→ 複製「存取權杖」欄位的值

set -euo pipefail

APP_ID="1365710202180406"
APP_SECRET="${1:-}"
SHORT_TOKEN="${2:-}"
ENV_FILE="$HOME/Library/Scripts/dora.env"

if [ -z "$APP_SECRET" ] || [ -z "$SHORT_TOKEN" ]; then
    echo "用法：bash $0 <APP_SECRET> <SHORT_TOKEN>"
    echo ""
    echo "範例："
    echo "  bash $0 abc123def456... EAATaGy..."
    exit 1
fi

echo "📡 正在換取 60 天長效 token..."

RESULT=$(curl -sf "https://graph.facebook.com/oauth/access_token?\
grant_type=fb_exchange_token\
&client_id=${APP_ID}\
&client_secret=${APP_SECRET}\
&fb_exchange_token=${SHORT_TOKEN}")

if echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if 'access_token' in d else 1)" 2>/dev/null; then
    LONG_TOKEN=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['access_token'])")
    EXPIRES=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('expires_in', '?'))")

    echo "✅ 換取成功！Token 有效期：${EXPIRES} 秒（約 60 天）"
    echo ""

    # 更新 dora.env 中的 META_TOKEN
    if grep -q "^META_TOKEN=" "$ENV_FILE"; then
        # 已存在則更新
        sed -i '' "s|^META_TOKEN=.*|META_TOKEN=${LONG_TOKEN}|" "$ENV_FILE"
        echo "🔄 已更新 dora.env 中的 META_TOKEN"
    else
        # 不存在則新增
        echo "META_TOKEN=${LONG_TOKEN}" >> "$ENV_FILE"
        echo "➕ 已新增 META_TOKEN 到 dora.env"
    fi

    # 更新 Token 到期時間（expires_in 秒後）
    NEW_EXPIRES=$(python3 -c "import time; print(int(time.time()) + ${EXPIRES})")
    if grep -q "META_TOKEN_EXPIRES=" "$ENV_FILE"; then
        sed -i '' "s|META_TOKEN_EXPIRES=.*|META_TOKEN_EXPIRES=${NEW_EXPIRES}|" "$ENV_FILE"
    else
        echo "META_TOKEN_EXPIRES=${NEW_EXPIRES}" >> "$ENV_FILE"
    fi
    EXP_DATE=$(python3 -c "from datetime import datetime; print(datetime.fromtimestamp(${NEW_EXPIRES}).strftime('%Y-%m-%d'))")
    echo "📅 Token 到期日：${EXP_DATE}（提前 3 天將推播 LINE 提醒）"

    # 載入/重新載入 LaunchAgents
    echo ""
    echo "🚀 載入排程..."
    for plist in \
        "$HOME/Library/LaunchAgents/com.dora.campaign-end-checker.plist" \
        "$HOME/Library/LaunchAgents/com.dora.ads-anomaly.plist"; do
        label=$(basename "$plist" .plist)
        launchctl unload "$plist" 2>/dev/null || true
        launchctl load "$plist"
        echo "  ✓ $label 已排程"
    done

    echo ""
    echo "✅ 設定完成！"
    echo "   - 廣告排程提醒：每週一到五 09:00"
    echo "   - 廣告異常警報：每週一到五 18:00"
    echo ""
    echo "💡 token 約 60 天後過期，到時重新執行此腳本即可更新"

else
    ERR=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('error',{}).get('message','Unknown error'))" 2>/dev/null || echo "API call failed")
    echo "❌ 換取失敗：$ERR"
    echo "請確認 App Secret 和 Short Token 正確無誤"
    exit 1
fi
