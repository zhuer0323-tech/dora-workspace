# LINE 小秘書（Dora賺錢小能手）

朱兒在 LINE 打一句話 → 排進工作台 → 回一句確認。2026-08-17 上線。

- **接線生網址**：<https://dora-line-secretary.zhuer0323.workers.dev>（Cloudflare Workers，免費方案）
- **LINE 機器人**：Dora賺錢小能手 @462xyoib（跟 8:30 早報同一隻，早報是推播、這裡是接收）
- **資料**：寫進工作台的 Firebase `busan-trip-2026-201f8` → `ws_k7m2q9xr4t/tasks`

## 怎麼用

在 LINE 打「日期 客戶 事情」，順序隨意，日期漏寫就排今天：

```
8/20 漁三 結案報表        → 8/20 · 漁三 · 結案報告
明天 優逸廣告上線          → 隔天 · 優逸 · 廣告
下週一 禾言 刷卡明細        → 下週一 · 禾言 · 禾言
TOTO 週報回報             → 今天 · TOTO · 週報回報（回覆會註明沒抓到日期）
```

認得的日期寫法：`2026-08-20`、`8/20`、`8月20日`、今天／明天／後天／大後天、
週五／下週一／下下週三、`3天後`。

## 目前只做新增

查詢、打勾完成、修改內容都還沒做。要改就到工作台網頁改。

## 檔案

| 檔案 | 做什麼 |
|:--|:--|
| `src/index.js` | 入口：驗簽 → 認人 → 建任務 → 回覆 |
| `src/classify.js` | 客戶與工作類型的判斷（**從工作台抄過來的，要手動同步**） |
| `src/date.js` | 從句子裡找日期（台灣時間為準，Worker 跑在 UTC） |
| `src/firebase.js` | 服務帳號簽 JWT 換 token、讀寫工作台資料 |

## 兩道門檻

1. **簽章驗證**：確認訊息真的來自 LINE（HMAC-SHA256 比對 `x-line-signature`）
2. **只認朱兒**：`source.userId` 不等於 `LINE_USER_ID` 就完全不理，也不回覆

工作台裡有客戶名稱，這兩道都不能拿掉。

## 機密（存在 Cloudflare，不在這個 repo）

| 名稱 | 內容 |
|:--|:--|
| `LINE_CHANNEL_SECRET` | 驗簽用（LINE Developers → Basic settings） |
| `LINE_ACCESS_TOKEN` | 回訊息用（跟早報同一組 `LINE_PUSH_TOKEN`） |
| `LINE_USER_ID` | 朱兒的使用者編號 |
| `FIREBASE_SA` | Firebase 服務帳號金鑰的完整 json（本機在 `~/Library/Scripts/dora-workspace-sa.json`） |

重設：`npx wrangler secret put <名稱> -c wrangler.toml`

## 維護

```bash
export PATH="/Users/angela/.nvm/versions/node/v24.15.0/bin:$PATH"
npx wrangler deploy -c wrangler.toml   # 改完程式重新上線
npx wrangler tail -c wrangler.toml     # 即時看 log（除錯用）
```

換 webhook 網址（正常不用動）：

```bash
curl -X PUT https://api.line.me/v2/bot/channel/webhook/endpoint \
  -H "Authorization: Bearer $LINE_PUSH_TOKEN" -H "Content-Type: application/json" \
  -d '{"endpoint":"https://dora-line-secretary.zhuer0323.workers.dev"}'
```

## 要注意的地方

- **分類規則要兩邊同步**：`src/classify.js` 是工作台 `index.html` 的複製品。
  網頁改了 `CLIENT_ALIASES`／`DEFAULT_TYPES`／`TYPE_PRIORITY`／`extractHead`，這裡要跟著改
- **不會自動建新客戶**：認不出客戶時只在回覆裡提「看起來像某某」，要不要建由朱兒在工作台決定
  （LINE 隨手打字會長出一堆假客戶）
- **不會學**：`ui.learn` 只讀不寫，學習還是走網頁那邊
- **Worker 跑在 UTC**：日期一律先加 8 小時算台灣時間，改 `date.js` 時別忘了
- **Cloudflare 會擋奇怪的 User-Agent**：本機用 curl／python 測試要帶 `User-Agent`，
  不帶會收到 403（那不是程式的錯）
