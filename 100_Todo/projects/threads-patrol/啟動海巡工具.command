#!/bin/bash
cd "/Users/angela/Downloads/Dora專屬/100_Todo/projects/threads-patrol"
source ~/.nvm/nvm.sh

# 強制清除所有殘留的 server 進程
pkill -9 -f "node server.js" 2>/dev/null
sleep 1
lsof -ti:3939 | xargs kill -9 2>/dev/null
sleep 1

# 清除瀏覽器鎖定檔
node -e "const fs=require('fs'),path=require('path'); try{fs.unlinkSync(path.join(__dirname,'browser-profile','SingletonLock'))}catch{}" 2>/dev/null

echo "✅ 啟動海巡工具..."
echo "👉 瀏覽器會自動開啟：http://localhost:3939"
echo ""

# 開啟瀏覽器
sleep 1 && open "http://localhost:3939" &

node server.js
