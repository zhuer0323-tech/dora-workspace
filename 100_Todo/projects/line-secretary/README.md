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

---

## 這隻 Worker 現在有兩條路

| 路徑 | 誰在用 | 認什麼身分 | 做什麼 |
|:--|:--|:--|:--|
| `/`（根目錄） | LINE 的 webhook | LINE 簽章 ＋ 朱兒的 userId | 排任務進工作台（`ws_k7m2q9xr4t`） |
| `/money` | iPhone 捷徑 | `x-dora-token` 標頭 | 記個人帳（`mn_x3f9b6qz`） |

兩條路各自認身分、各寫各的節點，改一邊不會影響另一邊。

### 記帳（2026-08-19 加）

- 程式：`src/money.js`（拆句子 ＋ 寫入），路由在 `src/index.js` 的 `handleShortcut`
- 機密：`MONEY_TOKEN`（`wrangler secret put`）。**倉庫是公開的，這串不能寫進任何檔案**；
  本機備份在 `~/Library/Scripts/dora-money-token.txt`
- LINE 也能記帳：訊息開頭有 `記帳／花了／支出／收入／$／+` 才走記帳，其餘一律照舊當任務。
  **不用猜的**，免得「8/20 漁三 結案報表」被當成花了 8 塊
- 日期跟排任務相反：任務往未來找（8/20 是下週要交），記帳往回找（8/17 是前天花的），
  所以 `money.js` 自己寫了一個 `parseSpentDate`，沒有共用 `date.js` 的 `parseDue`
- 分類清單**讀雲端的 `settings`**（她在 App 設定頁改什麼就是什麼），
  對不到就丟「其他」，**不自動長出新分類**
- 講法對照表在 `money.js` 的 `ALIAS`，加詞改那裡
- 測試不想弄髒帳本：網址加 `?dry=1`，只回結果不寫入
- 手把手的捷徑設定寫在 `100_Todo/plans/2026-08-19-記帳-手機捷徑.md`

## 廣告回報（2026-08-20 加）

在 LINE 打「漁三回報」「回報 優逸」→ 抓走期內的數字、看素材文案、寫分析 →
回一則可以直接轉傳給客戶的成效回報。

- **怎麼認得要跑回報**：句子**以「回報」開頭或結尾**才算。
  **帶日期的一律當任務**（「8/20 漁三 廣告回報」是要排一件事，不是現在跑數字）
- **會分兩則回**：先回一句「收到，正在跑」（LINE 的 replyToken 只能用一次，
  而且規定 10 秒內要回），跑完再用 push 推第二則
- **規格哪裡來**：工作台 `clients/{id}.rpt`，由 `scripts/sync-report-spec.py`
  從 `200_Reference/clients/*.md` 的「回報規格」段落同步上來。
  **改規格要改客戶檔再跑一次同步**，不要直接改雲端
- **新客戶要能跑回報**：在 `sync-report-spec.py` 的 `FEEDS` 加一筆
  （廣告帳戶、活動名稱開頭、要排除哪些別家的活動），再跑一次同步
- **數字怎麼抓**：`act_X/insights`（ad 層一次撈齊、campaign 層拿去重觸及）
  ＋ `act_X/ads` 拿目前 ACTIVE 的素材與文案。
  查進行中的素材**不能篩有花錢的**，剛開的新素材會漏掉
- **分析交給 Claude 寫**（`claude-opus-5`）：每家格式都不一樣，寫死在程式裡會變成
  每家一套 if；而且素材面分析要讀文案才寫得出來
- **成本一律不進客戶版**——這條寫在 `report.js` 的 SYSTEM 提示裡

### 這條路要的兩把鑰匙
```bash
npx wrangler secret put META_TOKEN          # 廣告帳戶（跟每日日報同一把，會過期）
npx wrangler secret put ANTHROPIC_API_KEY   # 分析用，console.anthropic.com 申請
```
鑰匙過期或沒設，LINE 會回一句講清楚是哪一把，不會默默失敗。
