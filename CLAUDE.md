# Dora 專屬工作區

## 使用者基本資訊
- **姓名**：朱加瑜（朱兒）
- **職業**：廣告行銷經理
- **居住地**：台中市

## 專業領域
- 數位行銷、Meta 與 Google 廣告投放
- ROAS 分析、受眾定位、轉換追蹤
- 素材分析、品牌市場調查

## 語言偏好
- 所有回應使用**繁體中文**
- 程式碼中的變數名稱、註解視需求使用英文

## 回應風格
- 視情況而定：簡單問題給直接簡短的答案，複雜任務附上脈絡與說明
- 不要在回應末尾加贅述性的總結（如「以上是我的回答」）
- 使用條列式或分段排版讓內容易讀

## 主要用途
此工作區用於：
- 寫作與內容創作（文章、創作、文案）
- 個人助理與規劃（任務管理、行程、備忘）
- 學習與研究（整理知識、分析資料）

## 一般原則
- 優先完成任務，必要時才詢問確認
- 不主動添加超出需求的功能或內容
- 除非特別要求，不加 emoji

## 執行權限設定

### 預設模式
- 編輯檔案：自動允許
- 其他工具：逐次確認

### 永久封鎖指令
- 刪除檔案：`rm -rf`、`rm -r`、`rm -f`、`rm -fr`、`rm -R`
- 危險 Git：`git push --force`、`git push -f`、`git reset --hard`、`git clean -f`、`git branch -D`
- 系統操作：`sudo`、`reboot`、`shutdown`、`diskutil erase`、`mkfs`、`dd`
- 危險權限：`chmod 777`、`chmod -R 777`
- 覆寫檔案：`truncate`、`: >*`

### 本專案自動允許指令
- `export PATH=...`（設定環境變數）
- `npm --version`

---

## MCP 工具

### 本地安裝
Node.js 透過 nvm 安裝，路徑：`/Users/angela/.nvm/versions/node/v24.15.0/`

**Firecrawl**
- 用途：輸入網址取得乾淨的 Markdown 文字，適合爬網頁文章、社群貼文
- API Key 存於 `~/.claude.json` 的 env 設定中
- 帳號：firecrawl.dev（zhuer0323@gmail.com）

**Playwright**
- 用途：操控瀏覽器（點擊、截圖、填表單），適合需要登入或動態渲染的網頁
- 不需要 API Key，全域安裝於 nvm node 路徑下

### claude.ai 整合工具
- **Gmail**：搜尋信件、讀取內容、建立草稿、管理標籤
- **Notion**：搜尋/讀取/建立/編輯頁面與資料庫、管理留言
- **Canva**：搜尋/生成/編輯/匯出設計、管理資產與資料夾、協作留言
- **Google Drive**：目前僅支援授權連線驗證

### 注意事項
- 若 MCP 連線失敗，確認 nvm node 路徑是否存在
- 設定存於 `~/.claude.json` 的 Dora專屬 專案區塊
