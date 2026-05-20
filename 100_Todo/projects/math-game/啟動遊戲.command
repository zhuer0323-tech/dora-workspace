#!/bin/bash
# 數學冒險遊戲啟動腳本 — 雙擊此檔案即可開始

cd "$(dirname "$0")"

# 確認 port 沒有被佔用
if lsof -i :8765 &>/dev/null; then
  echo "⚠️  Port 8765 已被使用，請先關閉其他遊戲視窗"
  echo "直接開啟瀏覽器到 http://localhost:8765"
  open "http://localhost:8765"
  exit 0
fi

echo "🎮 啟動數學冒險遊戲..."
python3 game-server.py &
SERVER_PID=$!

# 等伺服器準備好
sleep 1

echo "🌐 開啟瀏覽器..."
open "http://localhost:8765"

echo "✅ 遊戲已啟動！關閉此視窗會停止伺服器。"
echo "（按 Ctrl+C 或關閉視窗停止）"

wait $SERVER_PID
