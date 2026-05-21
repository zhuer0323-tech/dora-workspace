#!/bin/bash
cd "$(dirname "$0")"

echo "======================================"
echo "  Threads 海巡工具 — 初次安裝"
echo "======================================"
echo ""

# 載入 nvm
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"

# 確認 node
if ! command -v node &> /dev/null; then
  echo "❌ 找不到 Node.js，請先依照安裝說明.md 安裝 nvm 和 Node.js"
  echo ""
  echo "按任意鍵關閉..."
  read -n 1
  exit 1
fi

echo "✅ Node.js 版本：$(node -v)"
echo ""

# 安裝 npm 套件
echo "📦 安裝套件中..."
npm install
echo ""

# 安裝 Playwright Chromium
echo "🌐 安裝瀏覽器核心（約 1-2 分鐘）..."
npx playwright install chromium
echo ""

# 確認 .env 存在
if [ ! -f ".env" ]; then
  echo "GROQ_API_KEY=" > .env
  echo "⚠️  請用記事本打開 .env 檔案，填入你的 Groq API Key"
  open .env
else
  echo "✅ .env 已存在"
fi

echo ""
echo "======================================"
echo "  安裝完成！"
echo "  以後雙擊「啟動海巡工具.command」就可以使用"
echo "======================================"
echo ""
echo "按任意鍵關閉..."
read -n 1
