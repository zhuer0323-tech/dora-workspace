# Threads 海巡回文工具

自動搜尋 Threads 關鍵字貼文，用 AI 生成真人語氣回覆並自動發出。

---

## 第一次使用（只需要做一次）

### 1. 建立 .env 檔案

複製 `.env.example` 為 `.env`，填入你的資訊：

```
ANTHROPIC_API_KEY=sk-ant-你的金鑰
```

取得 Claude API Key：https://console.anthropic.com/

### 2. 登入 Threads

```bash
node login.js
```

瀏覽器會打開，在裡面手動登入 Threads 帳號，登入完成後回來按 Enter。
登入狀態會儲存到 `cookies.json`（大約 30 天有效）。

### 3. 設定關鍵字和風格

編輯 `config.json`：
- `keywords`：你要追蹤的關鍵字列表
- `dailyLimit`：每天最多回幾則（建議 20-50）
- `minIntervalMinutes` / `maxIntervalMinutes`：每則回覆之間的等待時間（分鐘）
- `accountPersona`：你的帳號風格描述，AI 會根據這個來寫回覆

---

## 日常使用

### 直接啟動（測試用）

```bash
node index.js
```

### 用 PM2 在背景 24 小時持續運行

```bash
# 啟動
pm2 start index.js --name threads-patrol

# 查看狀態
pm2 status

# 查看即時 log
pm2 logs threads-patrol

# 停止
pm2 stop threads-patrol

# 設定開機自動啟動（設定一次就好）
pm2 startup
pm2 save
```

---

## 檔案說明

| 檔案 | 用途 |
|:--|:--|
| `config.json` | 關鍵字、頻率、帳號風格設定 |
| `cookies.json` | Threads 登入狀態（自動生成，勿刪） |
| `replied.json` | 已回覆記錄（自動生成） |
| `.env` | API 金鑰（勿上傳 git） |

---

## Cookie 過期怎麼辦

大約 30 天需要重新登入一次：
```bash
pm2 stop threads-patrol
node login.js
pm2 start threads-patrol
```
