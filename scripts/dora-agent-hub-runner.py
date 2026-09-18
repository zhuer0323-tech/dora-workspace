#!/usr/bin/env python3
"""禾言社群規劃團隊的背景引擎：規劃 → 製作 → 審閱 → 製圖 四個角色接力做每篇貼文。

為什麼要這一支：跟「廣告回報」同一個道理，用她已經在付的 Claude Code 訂閱當引擎，
不用另外接付費 API。launchd 每分鐘叫一次，每次只推進「最舊那一件還在跑的任務」一步，
不會同時燒好幾個 claude -p 行程搶額度（2026-08-25 定案：一次只跑一件）。

2026-08-25 從「任何客戶都能丟的社群/廣告文案工具」改版成專門服務禾言自己的
月度社群規劃團隊（她的原話：「這個我把它定義為社群規劃團隊」），四個角色：
- 小梟（規劃）：抓 Meta/Google/LINE 廣告平台更新消息＋客戶常遇到的問題話題，
  結合她每月給的重點，交出「唯一主張＋證據＋圖卡視覺方向」（會用 WebSearch，也會讀 200_Reference/clients/）
- 小兔（製作）：照主張寫成正式貼文 Caption，**外加一份 4～6 頁的圖卡腳本 Card Plan**
- 小狐（審閱）：不是校對機，同時審 Caption 與 Card Plan，五項評分（三秒理解／具體程度／
  禾言人味／新手易懂／圖卡可讀性）都要 4 分才過。**AI 味是正式的通過條件，不是次要項**
- 小蝶（製圖）：照小狐通過的 Card Plan 做成 Canva 可編輯檔，**頁數依內容 4～6 頁，不固定六頁**
  （這一階段會真的 git push＋呼叫 Canva MCP，是四個角色裡唯一會動到共用檔案的）

2026-09-18 四角色改版（計劃書 100_Todo/plans/2026-09-18-agent-hub-四角色改版.md）：
起因是產出的文案模板化、AI 感重，圖卡層級太多又重複。四個角色共用的內容標準抽成
`200_Reference/writing-samples/禾言社群語氣與圖卡規範.md`，提示詞不再各寫各的。

接力規則：
- 規劃 → 製作 → 審閱；審閱「需要修改」退回製作，最多來回 2 輪，超過就標記「需要你決定」
- 審閱「通過」→ 定稿文案直接寫進「禾言社群規劃」網頁的資料（hy_social_r7n3k8），
  接著進「製圖」；製圖做完（或需要她確認）才算整件結束
- 任何角色判斷不出來會在回覆開頭寫 NEED_HUMAN，這支腳本看到就整件標記「需要你決定」並推播 LINE
  （其他時候不推播，不然天天洗版）

網頁上還有「私聊」——朱兒可以不透過任務、直接跟某個角色聊天。
每輪一樣只挑「最舊的一件待處理事」動手，任務推進跟私聊回覆一起排隊，不會搶額度。
"""
import base64, json, os, re, socket, subprocess, sys, tempfile, time
from datetime import date, timedelta
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ENV     = os.path.expanduser('~/Library/Scripts/dora.env')
CLAUDE  = '/Users/zhuer/.local/bin/claude'
WORKDIR = '/Users/zhuer/Downloads/Dora專屬'
TIMEOUT = 600                 # 一般角色最多跑 10 分鐘
DESIGN_TIMEOUT = 900          # 製圖要 git push＋等 Pages 更新＋Canva，給多一點
LOCK    = '/tmp/dora-agent-hub-runner.lock'
LOCK_STALE = DESIGN_TIMEOUT + 120   # 鎖過期門檻要蓋過最長的角色逾時（製圖900s），
                                     # 不然做圖跑到10~15分鐘那段會被誤判成死鎖，讓下一輪同時搶同一個任務
CLIENTS = os.path.join(WORKDIR, '200_Reference', 'clients')
ROOM    = 'ah_4j2ppkn8rq'     # 改名要同步改 Firebase 規則
HY_ROOM = 'hy_social_r7n3k8'  # 禾言社群規劃網頁的節點，定稿後直接寫進這裡
MAX_ROUNDS = 3                 # 製作/審閱最多來回幾輪（2026-09-18 從 2 加到 3：
                               # 小狐改成五項都要 4 分才過，退件率上升，2 輪常態不夠用）
# 圖卡形式（2026-09-18 她要求）：節慶那種不用做整套輪播，一張圖就夠了。
# 預設值只是預設，她可以在禾言規劃表上自己點選改掉。
CARD_TYPE_DEFAULT = {'日常/節慶': 'single'}   # 沒列到的類型一律 carousel
CARD_TYPE_LABEL = {'single': '單張', 'carousel': '輪播'}
CAROUSEL_MIN, CAROUSEL_MAX = 4, 6

DESIGN_WINDOW_DAYS = 3          # 審閱通過後不馬上做圖，等到離發布日剩這幾天才交給小蝶（2026-08-26 她要求）
PROPOSAL_DAY_START, PROPOSAL_DAY_END = 15, 21   # 每月第三週（大致），小梟排下個月建議
AUDIT_INTERVAL_SEC = 7 * 86400  # 小梟定期掃描已排程內容，一週一次就好，不用每天掃

# 2026-09-09 加：規劃表上排好的貼文自動開工＋防漏發提醒
STATE_DIR = os.path.expanduser('~/Library/Scripts/.dora-ah-state')
REMIND_HOUR_START, REMIND_HOUR_END = 9, 11   # 提醒只在早上這段推，不要半夜洗版
OVERDUE_REMIND_DAYS = 3         # 過了發布日還沒打勾，最多追這幾天就安靜
NET_WAIT_SEC = 10               # Mac 剛醒時最多等網路這麼久（每分鐘還會再跑，不要卡太久）
TOKEN_RETRY, TOKEN_RETRY_GAP = 3, 20   # 換 token 失敗重試次數與間隔

# 2026-09-09 加：聊天回覆加速
CHAT_MODEL = 'claude-sonnet-5'  # 私聊／工作群用快一點的腦袋（實測 16 秒 vs 預設 27 秒，答得一樣好；
                                # Haiku 一樣 16 秒但會回「我幫你查一下」這種空話，不能用）
SCAN_MIN_GAP = 55               # launchd 改成 20 秒一次是為了讓聊天快點被接到；
                                # 但讀整份禾言規劃表比較重，維持約一分鐘掃一次就好，省雲端讀取量
AUDIT_MIN_GAP = 600             # 兩次定期稽核至少隔 10 分鐘。2026-09-09 踩到：七篇同時到期時
                                # 連續一小時都在稽核，她在工作群講話要排隊等 1.5 分鐘才有人理
QUOTA_FALLBACK_SEC = 1800       # 額度用完又看不懂恢復時間時，先退避半小時

ROLE_LABEL = {'planner': '規劃', 'maker': '製作', 'reviewer': '審閱', 'designer': '製圖',
              'human': '你', 'system': '系統'}

# 私聊跟活動流用的人設。跟 100_Todo/projects/agent-hub/index.html 的 PERSONA 要對齊，改一邊要記得改另一邊
PERSONA = {
    'planner':  {'name': '小梟', 'desc': '規劃方向'},
    'maker':    {'name': '小兔', 'desc': '製作初稿'},
    'reviewer': {'name': '小狐', 'desc': '審閱把關'},
    'designer': {'name': '小蝶', 'desc': '製作圖卡'},
}

BASE_ALLOWED = 'Read,Glob,Grep'
PLANNER_ALLOWED = BASE_ALLOWED + ',WebSearch'
MAKER_ALLOWED = BASE_ALLOWED + ',Skill'   # 要真的呼叫 speak-human-tw，不是只憑印象模仿
DESIGNER_ALLOWED = 'Read,Write,Edit,Glob,Grep,Bash,Skill,' \
    'mcp__claude_ai_Canva__import-design-from-url,mcp__claude_ai_Canva__read-design'

PLANNER_PROMPT = """你是「小梟」，禾言數位行銷社群規劃團隊的規劃小幫手，負責幫這個月的一篇貼文訂方向。
接下來「小兔」會照你的方向寫文案＋圖卡腳本、「小狐」審閱、「小蝶」做圖卡，你的規劃是整條線的起點。
你交出去的不是「大方向」，是**一個主張＋支撐它的證據＋圖卡該長什麼樣**。

這篇的類型：{type_label}
預計發布日：{post_date}

這個月客戶最想了解的內容／重點方向（朱兒提供）：
{brief}

禾言規劃表這個月＋上個月已經排的貼文（**先看這個，不要跟這些話題或切角撞在一起**）：
{existing_posts}
{group_notes}{transcript_block}
下判斷前先做四件事：
1. 讀 `200_Reference/writing-samples/禾言社群語氣與圖卡規範.md`
   ——四個角色共用的標準，你要照它的「可以用的真實素材」挑依據
2. 看上面「已經排的貼文」，確認話題與切角沒有跟其中任何一篇重複
   （發現重疊就換角度或換更具體的子題目，不要硬寫一樣的）
3. 上網搜尋 Meta／Google／LINE 廣告平台最近的更新、新功能或政策變化，
   找出跟「{type_label}」相關、值得跟客戶分享的重點
4. 讀 `200_Reference/clients/` 底下的客戶檔找廣告投放實際遇到的問題。
   ⚠️ **社群貼文不可以出現客戶名稱，也不可以引用客戶的成效數字**（2026-09-18 朱兒定）。
   案例只能當背景，寫進「依據／真實情境」時**只留做法、拿掉身分與數字**
   （例如「素材上直接寫出價格，可以先擋掉只是好奇的人」，不是「某某公司寫了價格帶進 230 筆訊息」）。
   也不要用「我們有一個客戶」這種化名寫法，那等於換個方式點名

然後照下面的欄位輸出，**欄位名稱一字不改、順序不變**（小兔與小狐都靠這些欄位工作）：

受眾此刻的問題：
這篇唯一主張：
禾言的判斷：
為什麼現在值得談：
依據／真實情境：
讀者看完能做什麼：
這篇不要寫什麼：
適合的圖卡視覺：
{title_note}
規則（違反任何一條，小狐會把整篇退回來）：
- **「這篇唯一主張」18 個中文字左右，一篇只能有一個**。想講兩件事就挑一件，
  另一件寫進「這篇不要寫什麼」，留給下一篇
- 「禾言的判斷」要是明確立場。寫「因人而異」「各有優缺點」「看情況」等於沒講，不算數
- 「依據／真實情境」必須來自可用資料、平台更新、公開報導或客戶常見問題，**要寫得出出處**。
  找不到就在這一欄寫「缺少具體依據」——**不要自己編數字、趨勢、故事或客戶對話**。
  **不可以寫客戶名稱或客戶的成效數字**，投放的實際做法與判斷原則本身就夠有價值，不需要案例背書
- 「適合的圖卡視覺」從這七種挑**一種**，並說明為什麼這篇適合它：
  前後對比／流程／試算／錯誤示範／檢查清單／放大數字／單一觀點
- 「這篇不要寫什麼」要具體寫出容易失焦的支線，不是寫「不要寫太複雜」

如果需求描述得不夠清楚、判斷不出方向，不要亂猜——在回覆最開頭寫一行
NEED_HUMAN: <你不確定的地方，一句話說清楚>
然後結束，不要往下硬寫。

只輸出規劃內容本身，不要加「好的」「以下是」這種開場白，不要用 markdown 標題符號。"""

TITLE_NOTE = """標題候選：
1.
2.
3.
建議標題：

（標題不能只寫大主題，要讓人看見問題、結果、衝突或具體利益。
三個候選要是不同切角，不是同一句話換字。最後一行「建議標題：」的格式不要改。）
"""

MAKER_PROMPT = """你是「小兔」，禾言數位行銷社群規劃團隊的製作小幫手。
這次要交**兩份東西**：一份正式貼文 Caption，一份給小蝶用的圖卡腳本 Card Plan。
兩份都會被小狐審，缺一份就是退回。

這篇的類型：{type_label}
標題：{title}
預計發布日：{post_date}
{transcript_block}
{revision_note}
動筆前先讀這五份（不能跳）：
1. `200_Reference/writing-samples/禾言社群語氣與圖卡規範.md`
   ——四個角色共用的標準，**這次的主要依據**，寫作規則與圖卡層級都在裡面
2. `000_Agent/skills/社群文案撰寫/SKILL.md` 的「禾言 agent-hub 模式」那一章
   （前面的一般客戶模式是給其他客戶用的，不要套到禾言身上）
3. `200_Reference/writing-samples/禾言社群文案-朱兒改寫範例.md`
   ——朱兒親手把你的稿子改過一輪的真實對照，**照那份的手法寫，不是只看規則條文**
4. `200_Reference/clients/禾言數位行銷.md` ——品牌調性
   （⚠️ 裡面的案例庫**只能當背景理解禾言怎麼做事，不可以寫進貼文**，見下面的案例規則）
5. `200_Reference/writing-samples/廣告文案/語氣風格分析.md` ——朱兒本人的語言習慣統計。
   ⚠️ 那是她幫客戶寫的**廣告文案**習慣，**只帶結構與節奏（直接敘述開頭、【】標題式、✅✦ 條列），
   不要帶情緒強度**（一律驚嘆號、每則 4.6 個 emoji、❗❓ 符號標點、限時急迫感都不要）。
   那份最後一節「帶到禾言社群時的取捨」有逐項對照，照那個走
{edit_samples}
## Caption 的規則

- **這篇只服務小梟定義的「這篇唯一主張」**。小梟寫進「這篇不要寫什麼」的支線一律不碰
- 第一段直接進入讀者的問題、具體情境或結論，**不暖場、不鋪陳**
- 讀者是完全沒有廣告投放背景的老闆與行銷窗口：白話、好懂，
  術語第一次出現要用一句話解釋（例如「頻率」要順便講白話是什麼意思）
- **至少要有一個具體元素**：後台會看到的畫面、操作步驟、實際遇到的問題或明確判斷
- 要有禾言自己的判斷。把「禾言」換成別家行銷公司之後還完全成立，代表這篇太通用，重寫
- ⚠️ **不可以寫客戶名稱，也不可以引用客戶的成效數字**（曝光、CTR、CPC、ROAS、訊息數、名單數）。
  品牌檔的案例庫是提案用的，社群是公開發布的場合，標準不一樣。
  案例只留做法、拿掉身分與數字（「素材上直接寫出價格可以擋掉只是好奇的人」✓／
  「某某公司寫了價格帶進 230 筆訊息」✗）。
  **也不要用「我們有一個客戶」「曾經有老闆跟我們說」這種化名寫法**，那等於換個方式點名，
  而且容易滑成編故事。沒有出處的成效一律不提
- **全篇 350～450 字**。超過就是太繁瑣、重點被稀釋。壓字數要**整段刪掉在講同一件事的段落**，
  不是把每句話都縮短：收尾清單只留正文沒講的「接下來怎麼做」、解釋性的補充句丟給圖卡講、
  同類的例子只留最有力的一個
- CTA 依這篇的目的決定，**不是每篇都要叫讀者留言**
- 【禾言觀點】／【禾言怎麼做】／【禾言建議】**只有真的有禾言判斷時才用**，依內容挑一種；
  行動型內容可以直接給下一步，節慶類直接用行動呼籲收尾。**不強制每篇都有這一段**
- **不強制三點、不強制問句開場、不強制金句結尾**。只有兩個項目就寫兩個
- 標點跟著語氣走，不要一律套驚嘆號
- **平台功能名稱看這篇要不要讀者去操作**（2026-09-18 改）：
  小梟寫的「讀者看完能做什麼」是一串後台操作 → **寫出功能全名一次**
  （例如「Advantage+ 素材優化」），老闆在後台看到什麼字就寫什麼字，不然對不起來；
  純觀念、趨勢判斷、不需要動後台的 → 避開名稱只講原則，寫死會過期。
  這跟貼文掛在哪個類型無關，觀點/趨勢類也可能是要人去後台照做的
- 不用加「— 禾言數位行銷」署名行，hashtag 裡已經有

寫完第一版之後，**用 Skill 工具實際執行一次 `speak-human-tw`**，把 Caption 交給它去 AI 味
（這是非互動環境，那個 skill 會自動跳過確認清單直接套用，不會卡住等回覆）。
⚠️ **它只能清掉 AI 痕跡，不能替禾言編出個性、案例或經驗**——
它跑完你還是要自己對照規範檔第二段再檢查一次，不要把「跑過 skill」當成品質保證。
它給你的修改摘要不要留在輸出裡。

## Card Plan 的規則

{card_type_note}
- **一頁只講一件事**，每頁不得重複上一頁的結論
- 每頁都要寫「任務」與「視覺」，不能只列要放的文字
- 圖卡文字要比 Caption **更短**，不是把 Caption 分段貼上
- **封面最多三層**：類型小標／核心主標（兩行內、三秒看懂）／一句補充**或**一個視覺證據（二選一）。
  不要同時放三顆以上膠囊、引言、副標、「閱讀全文」、底部重複摘要
- 視覺要跟內容直接相關，從這幾種挑：數字放大／算式／前後對比／流程箭頭／
  錯誤與正確示範／簡單資料圖／內容專屬 SVG。優先呼應小梟寫的「適合的圖卡視覺」
- 不固定第 5 頁是禾言觀點，不固定最後一頁是互動 CTA，沒必要時停在最有力的結論
- 字數上限（超過會爆版）：封面主標一行 12 字內、各頁主標 8–12 字、底部句 16 字內

## 輸出格式（兩個區塊都要，標記一字不改）

<CAPTION>
（這裡只放正式貼文本身，含 hashtag。不要加標籤、說明或初稿字樣）
</CAPTION>

<CARD_PLAN>
頁數：5

P1｜封面
任務：這頁要達成什麼
主標：
補充：
視覺：

P2｜（頁名）
任務：
主標：
內容：
視覺：

（依此類推，頁數照上面「這篇要做幾頁」那段）
</CARD_PLAN>

⚠️ 「任務」與「視覺」是寫給小蝶看的工作說明，**不會印在圖上**；
「主標」「補充」「內容」「底部句」才是真的會印上去的字，朱兒會在規劃表上直接改這幾行，
所以那幾行要寫成**可以直接印的最終文字**，不要寫成描述（「這裡放一句結論」✗）。

兩個區塊以外不要寫任何字。如果判斷不出怎麼下筆，在回覆最開頭寫一行：
NEED_HUMAN: <原因>，然後結束。"""

REVIEWER_PROMPT = """你是「小狐」，禾言數位行銷社群規劃團隊的審閱小幫手，
負責幫這篇「{type_label}」貼文把關。你是內容策略角度的審閱者，不是校對機。
**這次要同時審 Caption 與 Card Plan 兩份**，只審其中一份不算數。

這篇的圖卡形式：{card_spec}
（形式是朱兒在規劃表上指定的，不要質疑該不該做輪播，只檢查頁數對不對）

{transcript_block}
審之前先讀 `200_Reference/writing-samples/禾言社群語氣與圖卡規範.md`，
你的評分與退件理由都要對得上那份的標準。

## 固定輸出這個格式（欄位名稱一字不改）

三秒理解：X/5
具體程度：X/5
禾言人味：X/5
新手易懂：X/5
圖卡可讀性：X/5

硬性問題：
- 無
（或逐條列出，一條一行）

具體修改指示：
（要改哪一句、哪一頁，為什麼，小兔應該補什麼）

最後一行單獨寫：
決定：通過
或
決定：需要修改

## 五項評分在看什麼

- **三秒理解**：封面主標三秒內看得出這篇在講什麼；看得出問題、結果、衝突或具體利益，
  不是只有一個大主題
- **具體程度**：有沒有後台會看到的畫面、操作步驟、實際遇到的問題或明確判斷。
  全是形容詞就是低分。⚠️ **不要因為「沒有客戶案例或成效數字」而扣分**——
  那些不准寫進社群貼文，具體程度要看做法講得夠不夠清楚
- **禾言人味**：有沒有禾言自己的判斷。**把「禾言」換成別家行銷公司還完全成立 → 過度通用，最多 2 分**
- **新手易懂**：完全沒有廣告背景的老闆看不看得懂，術語有沒有解釋。
  ⚠️ **要讀者去後台照做的貼文，反而要寫出後台看得到的功能名稱**（例如「Advantage+ 素材優化」），
  只寫通稱會讓人在後台找不到，這種要扣分；純觀念、不用動後台的才避開名稱
- **圖卡可讀性**：一頁一件事、頁與頁不重複、封面沒有超過三層資訊、頁數符合指定的圖卡形式。
  單張圖的話看它能不能自己成立（不能寫成「往下滑看更多」）

## 通過條件（全部達成才能寫「通過」）

1. 五項**都至少 4 分**
2. 沒有硬性問題
3. Card Plan 存在，而且頁數符合這篇指定的圖卡形式（單張＝1 頁／輪播＝4～6 頁）
4. 至少有一個具體的做法、場景或可執行方法
5. **Caption 全篇 350～450 字**（超過就是太繁瑣，退回）

## 一定要退回的情況（這些是硬性問題，不是小瑕疵）

- 有明顯 AI 套路：「不是 A 而是 B」對仗、「其實」「真正的問題是」「說到底」開頭、
  金句收尾、硬湊三點、罐頭鉤子（「九成人不知道」）、假坦白（「老實說」）、
  制式 CTA（每篇都叫人留言）
- 同一個意思換句話說兩三次、底部摘要跟正文講同一件事
- 圖卡各頁重複同一個意思
- **有無來源的數字、編造的案例，或把推測寫成事實**
- ⚠️ **出現客戶名稱，或引用客戶的成效數字**（曝光、CTR、CPC、ROAS、訊息數、名單數）——
  這是硬性問題，一定退回。「我們有一個客戶」這種化名寫法同樣不行
- 內容過度通用，換掉品牌名還完全成立
- **Caption 超過 450 字**，或明顯有段落在講正文已經講過的事
- Card Plan 缺漏、頁數跟指定的圖卡形式對不上，或每頁沒寫「任務」與「視覺」

⚠️ **AI 味在這一版是正式的通過條件，不是次要參考項。**

## 退件時的要求

不可以只寫「再自然一點」「可以更吸引人」。每一條都要寫出：
① 哪一句或哪一頁 ② 為什麼不吸引人／不像禾言 ③ 小兔應該補什麼。

如果這版可以定稿，在「具體修改指示」那欄改寫這版好在哪裡（尤其是為什麼會有人想看）。

只輸出審閱意見本身，不要加開場白。"""

DESIGNER_PROMPT = """你是「小蝶」，禾言數位行銷社群規劃團隊的製圖小幫手，
負責把這篇已經審閱通過的貼文做成 IG 圖卡的 Canva 可編輯檔。

標題：{title}
類型：{type_label}
**圖卡形式：{card_spec}**（朱兒在規劃表上指定的，照做，不要自己改頁數）

定稿文案（Caption，只是背景參考，不要整段搬到圖上）：
{final_copy}

**要印在圖上的文字（以這份為準）**：
{card_text}

圖卡腳本（Card Plan，含每頁的任務與視覺指示）：
{card_plan}

請先讀這兩份：
- `200_Reference/writing-samples/禾言社群語氣與圖卡規範.md` 的第四段「圖卡資訊層級」
- `000_Agent/skills/禾言圖文/SKILL.md`，走「agent-hub 自動模式」

不用做 skill 的 Step 1（取文案，上面已經給你）跟 Step 2（等朱兒確認——
這篇小狐已經審過，等同確認，不用再問一次）。

## 怎麼用這兩份

- **圖上的字一律以「要印在圖上的文字」那份為準**——朱兒會在規劃表上直接改那一欄，
  它跟 Card Plan 不一致時，**以它為準**（她改過的才是最新的）
- **Card Plan 看「任務」與「視覺」就好**，那是每頁要達成什麼、要畫什麼
- 一頁一個任務，不要自己重新拆頁
- **兩份都缺漏或看不懂時**：不要把 Caption 硬塞進固定頁數。
  先自己依 Caption 重整一份符合指定形式的腳本，照規範檔的圖卡規則自我檢查一遍，再開始做圖。
  單張圖的話只做一張；輪播真的整理不出四頁（內容太少）才在最開頭寫 NEED_HUMAN

## 圖卡規則

- **單張圖**：只做一張，它要能自己成立，不要寫成「往下滑看更多」。
  節慶類就是祝賀本身，不用硬塞廣告知識或行動呼籲
- 一頁只講一件事，每頁不得重複上一頁的結論
- **封面最多三層**：類型小標／核心主標（兩行內）／一句補充**或**一個視覺證據（二選一）。
  不要同時放三顆以上膠囊、引言、副標、「閱讀全文」、底部重複摘要
- **移除純裝飾性的重複「閱讀全文」**；同一句話不得同時出現在正文、頁尾結論與底部膠囊
- 不固定第 5 頁是禾言觀點，不固定最後一頁是互動 CTA，沒必要時停在最有力的結論
- **每篇至少一種跟內容直接相關的視覺表達**：數字放大／算式／前後對比／流程箭頭／
  錯誤與正確示範／簡單資料圖／內容專屬 SVG（用 inline SVG 畫，不要去 Canva 找素材）
- 不為了吸睛增加裝飾、貼紙或額外 emoji
- 保留品牌辨識：禾言黃、米白、格線背景、大圓角底紙、黃色星角、頂部 logo，右下角不放頁碼
- **Canva 匯入鐵則照舊**：圖片一定要絕對網址、強調用 linear-gradient 不用純色背景、
  每張卡要有 `data-document-role="page"`

## 流程

套柔和版模板 → 覆蓋 `100_Todo/projects/heyen-cards/index.html` → commit push →
等 GitHub Pages 更新（用 curl 確認，不要憑感覺等）→ Canva MCP 匯入 → 讀縮圖檢查。

**檢查至少要看封面、中間一頁、最後一頁**，看的是：
文字有沒有爆版、層級是不是太多、主標一眼讀不讀得懂、有沒有跟別頁重複。
不是只確認「Canva 有成功匯入」就算完成。

完成後最後一行單獨寫：
Canva連結：<edit_url 的完整網址>

如果中途卡住（例如 Canva MCP 連不上、GitHub Pages 一直沒更新），
在回覆最開頭寫一行：NEED_HUMAN: <發生什麼事>，然後結束，不要一直重試耗時間。"""

DM_PROMPT = """你是「{name}」，禾言社群規劃團隊裡負責{desc}，這次不是特定任務裡的討論，
是朱兒直接傳私訊給你——她可能在問狀況、問想法，或只是聊聊。

目前為止的對話：
{transcript}

用你這個角色的口吻自然回覆就好，不用太正式，也不用一直強調自己是規劃/製作/審閱/製圖小幫手。
只輸出你要回的話本身，不要加開場白。"""

GROUP_CHAT_PROMPT = """你是「小梟」，禾言數位行銷社群規劃團隊的規劃小幫手，也是團隊工作群裡
朱兒找得到的窗口——小兔、小狐、小蝶也在同一個群裡，但由你代表團隊回覆她。

朱兒在這裡說的話你要記住，之後規劃方向、排下個月內容、定期稽核時都要納入判斷，
不是回覆完就忘了。

目前為止的對話：
{transcript}

用你的角色口吻自然回覆，讓她知道你聽到了、記住了；如果她是在催進度，簡短說明目前狀況，
不確定的事不要不懂裝懂掰數字。只輸出你要回的話本身，不要加開場白。

【真的要派工的時候】
如果她這次講的話是**明確要團隊產出一篇貼文**（要你們新寫一篇、重寫某篇、換方向改寫），
在你的回覆最後另起一行，照這個格式輸出一行派工指令（前面不要加任何符號或說明）：

PARTY_TASK: {{"title": "這篇的標題", "type": "四類其中之一", "brief": "要寫什麼、方向是什麼、要避開什麼，寫清楚一點讓小兔看得懂"}}

規則：
- type 只能是：觀點/趨勢、教學/新手、日常/節慶、廣告顧問陪跑
- **只有她明確要求產出貼文才輸出這一行**。她只是在交代原則、講方向、問進度、聊天、
  或說「以後排到這類再照這樣走」，都不要輸出——寧可不派，也不要冒出她沒要的任務。
- 一次最多派一篇。
- 這一行不會出現在她看到的訊息裡，所以你回覆的正文要自己把「我來安排」講清楚。"""


def load_env():
    cfg = {}
    if os.path.exists(ENV):
        with open(ENV) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    cfg[k.strip()] = v.strip().strip('"').strip("'")
    return cfg


def _b64u(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b'=')


def ws_token(key_path):
    with open(key_path) as f:
        sa = json.load(f)
    ts = int(time.time())
    header = _b64u(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    claims = _b64u(json.dumps({
        "iss": sa['client_email'],
        "scope": "https://www.googleapis.com/auth/firebase.database "
                 "https://www.googleapis.com/auth/userinfo.email",
        "aud": "https://oauth2.googleapis.com/token",
        "iat": ts, "exp": ts + 3600,
    }).encode())
    signing_input = header + b'.' + claims
    fd, pem = tempfile.mkstemp(suffix='.pem')
    try:
        os.write(fd, sa['private_key'].encode()); os.close(fd); os.chmod(pem, 0o600)
        sig = subprocess.run(['openssl', 'dgst', '-sha256', '-sign', pem],
                             input=signing_input, capture_output=True, check=True).stdout
    finally:
        os.unlink(pem)
    body = urlencode({'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer',
                      'assertion': (signing_input + b'.' + _b64u(sig)).decode()}).encode()
    req = Request('https://oauth2.googleapis.com/token', data=body, method='POST')
    with urlopen(req, timeout=10) as r:
        return json.loads(r.read())['access_token']


def db_get(cfg, tok, room, path):
    req = Request(f"{cfg['WS_DB_URL'].rstrip('/')}/{room}/{path}.json",
                  headers={'Authorization': f'Bearer {tok}'})
    with urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def db_patch(cfg, tok, room, path, obj):
    req = Request(f"{cfg['WS_DB_URL'].rstrip('/')}/{room}/{path}.json",
                  data=json.dumps(obj, ensure_ascii=False).encode(),
                  headers={'Authorization': f'Bearer {tok}', 'Content-Type': 'application/json'},
                  method='PATCH')
    with urlopen(req, timeout=15) as r:
        r.read()


def db_post(cfg, tok, room, path, obj):
    req = Request(f"{cfg['WS_DB_URL'].rstrip('/')}/{room}/{path}.json",
                  data=json.dumps(obj, ensure_ascii=False).encode(),
                  headers={'Authorization': f'Bearer {tok}', 'Content-Type': 'application/json'},
                  method='POST')
    with urlopen(req, timeout=15) as r:
        return json.loads(r.read())['name']


def line_push(cfg, text):
    if not cfg.get('LINE_PUSH_TOKEN'):
        return
    body = json.dumps({'to': cfg['LINE_USER_ID'],
                       'messages': [{'type': 'text', 'text': text[:4900]}]},
                      ensure_ascii=False).encode()
    req = Request('https://api.line.me/v2/bot/message/push', data=body, method='POST',
                  headers={'Content-Type': 'application/json',
                           'Authorization': f"Bearer {cfg['LINE_PUSH_TOKEN']}"})
    with urlopen(req, timeout=20) as r:
        r.read()


def build_transcript(messages):
    if not messages:
        return ''
    rows = sorted(messages.values(), key=lambda m: m.get('createdAt', 0))
    lines = ['目前為止的討論：']
    for m in rows:
        label = ROLE_LABEL.get(m.get('role'), m.get('role', '?'))
        lines.append(f"[{label}] {m.get('text', '')}")
    return '\n'.join(lines) + '\n'


class QuotaExhausted(RuntimeError):
    """Claude 額度用完。跟一般失敗要分開處理：整套系統暫停到恢復時間，
    不要把任務標成錯誤、也不要每分鐘再叫一次 claude 空轉。"""


def quota_block(msg):
    """把「暫停到什麼時候」寫進標記檔。訊息長這樣：
    You've hit your session limit · resets 1:30pm (Asia/Taipei)"""
    until = time.time() + QUOTA_FALLBACK_SEC
    m = re.search(r'resets\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)', msg, re.I)
    if m:
        h = int(m.group(1)) % 12
        if m.group(3).lower() == 'pm':
            h += 12
        lt = time.localtime()
        cand = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, h, int(m.group(2) or 0), 0, 0, 0, -1))
        if cand < time.time():
            cand += 86400
        until = cand + 60          # 多等一分鐘，免得剛好卡在恢復的那一秒
    with open(state_path('quota-blocked'), 'w') as f:
        f.write(str(int(until)))
    return until


def quota_blocked_until():
    """還在暫停中就回傳恢復時間，已經可以動了回 0。"""
    try:
        with open(state_path('quota-blocked')) as f:
            until = int(f.read().strip())
    except (OSError, ValueError):
        return 0
    return until if until > time.time() else 0


def audit_gap_ok():
    """兩次稽核之間留 AUDIT_MIN_GAP 秒，別讓背景維護連續霸佔，害聊天排隊。"""
    fp = state_path('last-audit')
    try:
        return time.time() - os.path.getmtime(fp) >= AUDIT_MIN_GAP
    except OSError:
        return True


def audit_mark():
    with open(state_path('last-audit'), 'w') as f:
        f.write(str(int(time.time())))


def run_claude(prompt, allowed=BASE_ALLOWED, timeout=TIMEOUT, model=None):
    cmd = [CLAUDE, '-p', prompt, '--allowedTools', allowed]
    if model:
        cmd += ['--model', model]
    p = subprocess.run(cmd, cwd=WORKDIR, capture_output=True, text=True, timeout=timeout)
    out = (p.stdout or '').strip()
    if p.returncode != 0 and not out:
        raise RuntimeError((p.stderr or '').strip()[:300] or f'claude 回傳 {p.returncode}')
    # 2026-08-27 踩過的坑：額度用完時 claude -p 仍會回傳 0 且印出這句提示，不是丟例外，
    # 結果被當成正常回覆寫進任務訊息裡，規劃/製作/審閱三個角色被污染了一輪都沒人發現。
    # 這句訊息很固定，直接抓字串當成失敗處理。
    if "hit your session limit" in out or "hit your weekly limit" in out or "usage limit" in out.lower():
        # 2026-09-09 改成專屬例外：額度用完是「整套系統都不能動」，不是「這件事失敗了」。
        # 之前用一般的 RuntimeError，結果任務被標成錯誤、稽核每分鐘重試（那天空轉了 50 次）。
        raise QuotaExhausted(out[:200])
    return out


def parse_need_human(out):
    m = re.match(r'^NEED_HUMAN:\s*(.+)', out.strip(), re.I)
    return m.group(1).strip() if m else None


def parse_verdict(out):
    m = re.search(r'決定[：:]\s*(通過|需要修改)\s*$', out.strip())
    return m.group(1) if m else None


def parse_title(out):
    m = re.search(r'建議標題[：:]\s*(.+)', out)
    return m.group(1).strip() if m else None


def parse_canva_url(out):
    m = re.search(r'Canva\s*連結[：:]\s*(\S+)', out)
    return m.group(1).strip() if m else None


# ---- 小兔的雙區塊輸出：Caption（正式貼文）＋ Card Plan（圖卡腳本）----
# 2026-09-18 加。在這之前小兔只交一份文案，整份直接寫進禾言規劃表的 ig 欄，
# 小蝶拿到的也只有文案、要自己硬拆六頁。現在拆成兩份：ig 欄只放乾淨的 caption，
# 腳本另外存 cardPlan 給小蝶用。

def _tagged_block(text, tag):
    """取 <TAG>…</TAG> 之間的內容。沒有收尾標記時，取到下一個標記或結尾為止
    （小兔偶爾會漏掉結尾標記，不該因此整份解析失敗）。找不到開始標記回 None。"""
    m = re.search(r'<%s>(.*?)</%s>' % (tag, tag), text, re.S | re.I)
    if m:
        return m.group(1).strip()
    m = re.search(r'<%s>(.*)' % tag, text, re.S | re.I)
    if not m:
        return None
    rest = re.split(r'</?(?:CAPTION|CARD_PLAN)>', m.group(1), flags=re.I)[0]
    return rest.strip()


def parse_maker_output(out):
    """把小兔的輸出拆成 (caption, card_plan)。

    向下相容：舊任務（2026-09-18 以前）沒有標記，整份視為 caption、card_plan 為空字串，
    絕對不能報錯——那些任務還可能被重跑或被小蝶拿去做圖。
    """
    text = (out or '').strip()
    caption = _tagged_block(text, 'CAPTION')
    card_plan = _tagged_block(text, 'CARD_PLAN')
    if caption is None and card_plan is None:
        return text, ''
    if not caption:
        # 只有 CARD_PLAN 沒有 CAPTION：把標記以外的內容當 caption，
        # 不然規劃表的文案欄會整個空掉
        caption = re.sub(r'<CARD_PLAN>.*?(?:</CARD_PLAN>|$)', '', text,
                         flags=re.S | re.I).strip()
        caption = re.sub(r'</?CAPTION>', '', caption, flags=re.I).strip()
    return caption, (card_plan or '')


PAGE_LINE_RE = re.compile(r'^\s*P\s*(\d+)\s*[｜|]', re.M)

# 「任務」「視覺」是給小蝶看的工作說明，不是要印在圖上的字。
# 朱兒要在規劃表上直接改圖卡文字，所以另外抽一份乾淨的出來給她看（2026-09-18）。
PLAN_WORK_FIELDS = ('任務', '視覺')


def card_plan_text(card_plan):
    """把 Card Plan 抽成「只有要印上去的字」，給規劃表顯示與編輯用。

    去掉「任務：」「視覺：」這兩種工作說明行（含它們的續行），
    頁標題的全形直線換成空格，讀起來就是一頁一頁的圖卡文字。
    """
    if not card_plan:
        return ''
    out, skipping = [], False
    for raw in card_plan.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if PAGE_LINE_RE.match(line):
            skipping = False
            out.append(re.sub(r'^\s*(P\s*\d+)\s*[｜|]\s*', r'\1 ', stripped))
            continue
        if re.match(r'^頁數[：:]', stripped):
            continue
        m = re.match(r'^([^：:]{1,6})[：:]', stripped)
        if m:
            skipping = m.group(1).strip() in PLAN_WORK_FIELDS
            if skipping:
                continue
        elif skipping and stripped:
            continue          # 工作說明的續行
        if not stripped:
            skipping = False
        out.append(line)
    text = '\n'.join(out)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


CARD_NOTE_CAROUSEL = """- **這篇做輪播，依內容決定 {lo}～{hi} 頁**，不固定六頁。內容只夠 {lo} 頁就寫 {lo} 頁，不要為了補滿硬湊"""

CARD_NOTE_SINGLE = """- **這篇只做一張圖，不做輪播**（朱兒指定）。Card Plan 只寫 P1 一頁，`頁數：1`
- 一張圖要自己成立，沒有下一頁可以接：把主張講完，不要寫成「往下滑看更多」
- 一樣最多三層：類型小標／主標（兩行內）／一句補充**或**一個視覺證據（二選一）
- 節慶類就是祝賀本身，不用硬塞廣告知識或行動呼籲，也不要為了湊內容加清單"""


def card_type_note(card_type):
    if card_type == 'single':
        return CARD_NOTE_SINGLE
    return CARD_NOTE_CAROUSEL.format(lo=CAROUSEL_MIN, hi=CAROUSEL_MAX)


def expected_pages(card_type):
    """這種圖卡形式該有幾頁，用來組提示詞與檢查。"""
    if card_type == 'single':
        return '1 頁（單張圖，不做輪播）'
    return '%d～%d 頁' % (CAROUSEL_MIN, CAROUSEL_MAX)


def card_type_of(post, task=None):
    """這篇要做單張還是輪播：規劃表她點選的優先，沒點過就照類型給預設。"""
    for src in (post or {}), (task or {}):
        v = (src or {}).get('cardType')
        if v in CARD_TYPE_LABEL:
            return v
    t = ((post or {}).get('type') or (task or {}).get('type') or '').strip()
    return CARD_TYPE_DEFAULT.get(t, 'carousel')



def card_plan_pages(card_plan):
    """Card Plan 有幾頁（「P1｜封面」這種行算一頁，同一頁碼只算一次）。
    只用來記錄與提示，頁數不對是交給小狐判斷退不退，程式本身不擋、也不能崩。"""
    if not card_plan:
        return 0
    try:
        return len({int(n) for n in PAGE_LINE_RE.findall(card_plan)})
    except Exception:
        return 0


# ---- 朱兒手改過的版本，當成小兔下次寫作的參考 ----
# 2026-09-18 加。她常常直接在禾言規劃表網頁上改文案，那些改動以前完全沒有回流，
# 小兔每次都從同一套規則重新寫。這裡只做一件事：把「小兔交的版本」跟「規劃表現在的內容」
# 有差異的幾組挑出來當範例，**不歸納規則、不推測理由、不改寫任何檔案**。
EDIT_SAMPLE_MAX = 3       # 最多帶幾組 before/after
EDIT_SAMPLE_SCAN = 12     # 最多回頭掃幾筆任務（控制雲端讀取量）
EDIT_SAMPLE_CHARS = 700   # 單一版本最多帶幾個字，避免 prompt 無限膨脹


def _norm_text(t):
    return re.sub(r'\s+', '', t or '')


def _clip(t, n=EDIT_SAMPLE_CHARS):
    t = (t or '').strip()
    return t if len(t) <= n else t[:n] + '\n（後略）'


def recent_human_edits(cfg, tok, skip_tid=None):
    """回傳可以直接塞進 prompt 的「小兔版／朱兒最後版」對照，沒有就回空字串。"""
    try:
        tasks = db_get(cfg, tok, ROOM, 'tasks') or {}
        posts = db_get(cfg, tok, HY_ROOM, 'posts') or {}
    except Exception:
        return ''
    rows = [dict(t, id=tid) for tid, t in tasks.items()
            if isinstance(t, dict) and t.get('hySocialId') and tid != skip_tid]
    rows.sort(key=lambda t: t.get('updatedAt', 0), reverse=True)

    pairs = []
    for t in rows[:EDIT_SAMPLE_SCAN]:
        if len(pairs) >= EDIT_SAMPLE_MAX:
            break
        final = (posts.get(t['hySocialId']) or {}).get('ig', '') or ''
        if not final.strip():
            continue
        try:
            mine, _ = parse_maker_output(last_message_text(cfg, tok, t['id'], 'maker'))
        except Exception:
            continue
        if not mine.strip():
            continue
        if _norm_text(mine) == _norm_text(final):
            continue   # 她沒動過，沒有參考價值
        pairs.append((t.get('title', ''), mine, final))

    if not pairs:
        return ''
    out = ['\n朱兒實際改過的版本（左邊是小兔交出去的，右邊是她最後真的用的）：',
           '照她的改法寫，但**不要自己歸納成規則**，也不要照抄她那幾篇的內容。\n']
    for i, (title, mine, final) in enumerate(pairs, 1):
        out.append('【第 %d 組｜%s】' % (i, title or '（沒標題）'))
        out.append('小兔當時寫的：\n%s\n' % _clip(mine))
        out.append('她最後用的：\n%s\n' % _clip(final))
    return '\n'.join(out) + '\n'


def last_message_text(cfg, tok, tid, role):
    msgs = db_get(cfg, tok, ROOM, f'messages/{tid}') or {}
    rows = sorted(msgs.values(), key=lambda m: m.get('createdAt', 0))
    for m in reversed(rows):
        if m.get('role') == role:
            return m.get('text', '')
    return ''


def existing_hy_posts_summary(cfg, tok, post_date):
    """小梟規劃前先看禾言規劃表當月＋上個月已經排了什麼，避免撞題。
    2026-08-25 加的：出過一次事故，小梟在完全不知道規劃表內容的情況下，
    生出一篇當天已經做過的重複主題，一路跑到真的 git push＋建 Canva 檔才被發現。"""
    try:
        posts = db_get(cfg, tok, HY_ROOM, 'posts') or {}
    except Exception:
        return '（讀不到禾言規劃表，跳過比對，下筆時自己留意別跟明顯常見的主題撞題）'

    prefixes = set()
    if post_date and len(post_date) >= 7:
        y, m = int(post_date[:4]), int(post_date[5:7])
        prefixes.add(f'{y:04d}-{m:02d}')
        pm, py = (m - 1, y) if m > 1 else (12, y - 1)
        prefixes.add(f'{py:04d}-{pm:02d}')

    rows = []
    for p in posts.values():
        if not isinstance(p, dict):
            continue
        d = p.get('date') or ''
        if prefixes and d[:7] not in prefixes:
            continue
        rows.append((d, p.get('type') or '（沒類型）', p.get('title') or '（未命名）'))
    if not rows:
        return '（這個月跟上個月規劃表裡還沒有其他貼文，不用擔心撞題）'
    rows.sort()
    return '\n'.join(f'- {d}｜{t}｜{ti}' for d, t, ti in rows)


def recent_group_chat_summary(cfg, tok, limit=10):
    """朱兒在「工作群」交代過的事，小梟規劃/排月建議時要記得納入判斷，不是回覆完就忘了。"""
    try:
        msgs = db_get(cfg, tok, ROOM, 'groupChat') or {}
    except Exception:
        return ''
    human_msgs = [m for m in msgs.values() if isinstance(m, dict) and m.get('from') == 'human']
    if not human_msgs:
        return ''
    human_msgs.sort(key=lambda m: m.get('createdAt', 0))
    recent = human_msgs[-limit:]
    lines = [f"- {m.get('text', '')}" for m in recent]
    return '朱兒在工作群交代過的事（要記住，納入判斷）：\n' + '\n'.join(lines) + '\n\n'


def ensure_hy_social(cfg, tok, task):
    """任務一進「規劃」就在禾言社群規劃那邊開一張對應的卡（內容先空著），
    這樣她從一開始就能在熟悉的規劃表上看到這篇、看到進度徽章，不用等審閱通過才看得到。
    已經開過的話就只補一次目前階段（防呆用，正常都是靠 set_stage 保持最新）。
    回傳（可能更新過的）task dict。
    """
    tid = task['id']
    if task.get('hySocialId'):
        db_patch(cfg, tok, HY_ROOM, f'posts/{task["hySocialId"]}', {'agentStage': task.get('stage')})
        return task
    post = {
        'date': task.get('postDate') or '', 'type': task.get('type') or '其他',
        'title': task.get('title') or '', 'goal': task.get('goal') or '',
        'ig': '', 'fb': '', 'done': False,
        'agentTaskId': tid, 'agentStage': task.get('stage'),
    }
    hy_id = db_post(cfg, tok, HY_ROOM, 'posts', post)
    db_patch(cfg, tok, ROOM, f'tasks/{tid}', {'hySocialId': hy_id})
    task = dict(task); task['hySocialId'] = hy_id
    return task


def set_stage(cfg, tok, task, fields):
    """更新 ah 任務的階段／欄位，同時把狀態同步到禾言社群規劃那張卡（如果已經串接）——
    這樣禾言規劃表上的徽章才會跟著換，不用等她自己回 agent-hub 看。
    每一條退出 process_task() 的路徑都會經過這裡，所以順便把 processingRole/
    processingStartedAt 清掉（2026-08-26 她反饋「看不出現在到底在做什麼」，
    這兩個欄位是給前端顯示「正在做 X，已經進行 N 分鐘」用的，做完就要清乾淨）。"""
    tid = task['id']
    fields = dict(fields)
    fields.setdefault('processingRole', None)
    fields.setdefault('processingStartedAt', None)
    db_patch(cfg, tok, ROOM, f'tasks/{tid}', fields)
    hy_id = task.get('hySocialId')
    if hy_id and 'stage' in fields:
        hy_fields = {'agentStage': fields['stage']}
        if 'title' in fields:
            hy_fields['title'] = fields['title']
        db_patch(cfg, tok, HY_ROOM, f'posts/{hy_id}', hy_fields)


def process_task(cfg, tok, task):
    tid, stage = task['id'], task.get('stage')
    type_label = task.get('type') or '（沒指定類型）'
    messages = db_get(cfg, tok, ROOM, f'messages/{tid}') or {}
    transcript_block = build_transcript(messages)
    now_ms = int(time.time() * 1000)

    task = ensure_hy_social(cfg, tok, task)  # 一進來就確保禾言那邊有對應的卡、狀態是最新的

    # 規劃表那張卡讀一次就好，小兔、小狐、小蝶都要用（圖卡形式、她手改過的文案與圖卡文字）
    hy_post = {}
    if task.get('hySocialId'):
        try:
            hy_post = db_get(cfg, tok, HY_ROOM, f'posts/{task["hySocialId"]}') or {}
        except Exception:
            hy_post = {}
    card_type = card_type_of(hy_post, task)

    if stage == 'planning':
        role, allowed, timeout = 'planner', PLANNER_ALLOWED, TIMEOUT
        prompt = PLANNER_PROMPT.format(
            type_label=type_label, post_date=task.get('postDate') or '（沒填）',
            brief=task.get('brief', ''), transcript_block=transcript_block,
            existing_posts=existing_hy_posts_summary(cfg, tok, task.get('postDate')),
            group_notes=recent_group_chat_summary(cfg, tok),
            title_note='' if task.get('title') else TITLE_NOTE)
    elif stage == 'making':
        role, allowed, timeout = 'maker', MAKER_ALLOWED, TIMEOUT
        round_no = task.get('round', 0)
        revision_note = ''
        if round_no > 0:
            revision_note = '\n審閱小幫手上一輪給了修改意見（看上面討論紀錄裡最新一則「審閱」），請照那個意見修改上一版初稿。\n'
        prompt = MAKER_PROMPT.format(
            type_label=type_label, title=task.get('title') or '（還沒定，你可以自己下一個貼合內容的標題）',
            post_date=task.get('postDate') or '（沒填）',
            transcript_block=transcript_block, revision_note=revision_note,
            card_type_note=card_type_note(card_type),
            edit_samples=recent_human_edits(cfg, tok, skip_tid=tid))
    elif stage == 'reviewing':
        role, allowed, timeout = 'reviewer', BASE_ALLOWED, TIMEOUT
        prompt = REVIEWER_PROMPT.format(
            type_label=type_label, transcript_block=transcript_block,
            card_spec='%s，Card Plan 應該是 %s' % (
                CARD_TYPE_LABEL[card_type], expected_pages(card_type)))
    elif stage == 'designing':
        role, allowed, timeout = 'designer', DESIGNER_ALLOWED, DESIGN_TIMEOUT
        # 文案以禾言規劃表「現在」的內容為準，不要用 ah 任務裡小兔那則舊訊息——
        # 她可能直接在禾言規劃表網頁上手改過文案，那個才是最新版（2026-08-26 踩過這個坑）
        final_copy = hy_post.get('ig', '') or ''
        card_plan = hy_post.get('cardPlan', '') or task.get('cardPlan', '') or ''
        card_text = hy_post.get('cardText', '') or ''
        if not final_copy or not card_plan:
            # 舊任務（沒有雙區塊格式）或雲端讀不到時，退回任務訊息裡小兔最後那則
            fb_caption, fb_plan = parse_maker_output(last_message_text(cfg, tok, tid, 'maker'))
            final_copy = final_copy or fb_caption
            card_plan = card_plan or fb_plan
        if not card_text:
            card_text = card_plan_text(card_plan)
        prompt = DESIGNER_PROMPT.format(
            title=task.get('title') or '', type_label=type_label,
            card_spec='%s（%s）' % (CARD_TYPE_LABEL[card_type], expected_pages(card_type)),
            final_copy=final_copy,
            card_text=card_text or '（沒有圖卡文字，照下面「Card Plan 缺漏」那段處理）',
            card_plan=card_plan or '（這篇沒有圖卡腳本，照下面「Card Plan 缺漏」那段自己重整）')
    else:
        return False

    print(f"{time.strftime('%F %T')} 開始跑 {tid} / {role}")
    db_patch(cfg, tok, ROOM, f'tasks/{tid}', {'processingRole': role, 'processingStartedAt': now_ms})
    try:
        out = run_claude(prompt, allowed=allowed, timeout=timeout)
    except QuotaExhausted:
        # 額度用完不是這件任務的錯，把處理中標記清掉、階段留著，等額度恢復自然會再跑
        db_patch(cfg, tok, ROOM, f'tasks/{tid}', {'processingRole': None, 'processingStartedAt': None})
        raise
    except Exception as e:
        set_stage(cfg, tok, task, {
            'stage': 'waiting_human', 'waitingKind': 'error',
            'waitingReason': f'{ROLE_LABEL[role]}小幫手這輪跑失敗了：{str(e)[:200]}',
            'resumeStage': stage, 'updatedAt': now_ms})
        line_push(cfg, f'⚠️「{task.get("title","")}」的{ROLE_LABEL[role]}小幫手跑失敗了，麻煩到協作平台看一下')
        return True

    need_human = parse_need_human(out)
    if need_human:
        db_post(cfg, tok, ROOM, f'messages/{tid}', {
            'role': role, 'text': f'我不確定：{need_human}', 'createdAt': now_ms})
        set_stage(cfg, tok, task, {
            'stage': 'waiting_human', 'waitingKind': 'needHuman',
            'waitingReason': need_human, 'resumeStage': stage, 'updatedAt': now_ms})
        line_push(cfg, f'🙋「{task.get("title","")}」的{ROLE_LABEL[role]}小幫手卡住了：\n{need_human}\n\n到協作平台回覆一下就能繼續')
        return True

    db_post(cfg, tok, ROOM, f'messages/{tid}', {'role': role, 'text': out, 'createdAt': now_ms})

    if role == 'planner':
        patch = {'stage': 'making', 'updatedAt': now_ms}
        title = parse_title(out)
        if title and not task.get('title'):
            patch['title'] = title
        set_stage(cfg, tok, task, patch)
    elif role == 'maker':
        # 2026-09-18 起小兔交兩份：Caption 進規劃表的 ig 欄，Card Plan 另存給小蝶。
        # 舊格式（沒有標記）整份會被當成 caption，行為跟以前一樣。
        caption, card_plan = parse_maker_output(out)
        patch = {'stage': 'reviewing', 'updatedAt': now_ms}
        if card_plan:
            patch['cardPlan'] = card_plan
            patch['cardPlanPages'] = card_plan_pages(card_plan)
        set_stage(cfg, tok, task, patch)
        if task.get('hySocialId'):
            # cardText 是「只有要印上去的字」，規劃表上她可以直接改，小蝶以她改過的為準
            hy_patch = {'ig': caption, 'cardType': card_type}
            if card_plan:
                hy_patch['cardPlan'] = card_plan
                hy_patch['cardText'] = card_plan_text(card_plan)
            db_patch(cfg, tok, HY_ROOM, f'posts/{task["hySocialId"]}', hy_patch)
    elif role == 'reviewer':
        verdict = parse_verdict(out)
        if verdict == '通過':
            # 2026-08-26 改：文案定稿不馬上做圖，等離發布日剩 DESIGN_WINDOW_DAYS 天才交給小蝶
            # （她的原話：不要文案一過就馬上做圖，拖到接近發布日才做，圖不用整月一次做完）
            set_stage(cfg, tok, task, {'stage': 'awaiting_window', 'updatedAt': now_ms})
            line_push(cfg, f'✅「{task.get("title","")}」文案定稿了，已經寫進禾言社群規劃。離發布日還有一段時間，小蝶會在接近發布日前 {DESIGN_WINDOW_DAYS} 天開始做圖')
        else:
            round_no = task.get('round', 0) + 1
            if verdict is None:
                set_stage(cfg, tok, task, {
                    'stage': 'waiting_human', 'waitingKind': 'needHuman',
                    'waitingReason': '審閱小幫手的回覆看不出通過還是要改，麻煩你看一下',
                    'resumeStage': 'reviewing', 'updatedAt': now_ms})
                line_push(cfg, f'🙋「{task.get("title","")}」的審閱結果我判斷不出來，到協作平台看一下')
            elif round_no > MAX_ROUNDS:
                set_stage(cfg, tok, task, {
                    'stage': 'waiting_human', 'waitingKind': 'maxRound',
                    'waitingReason': f'製作跟審閱已經來回改了 {MAX_ROUNDS} 輪，我先停下來，你要用目前這版定稿，還是再給個方向？',
                    'resumeStage': 'making', 'round': round_no, 'updatedAt': now_ms})
                line_push(cfg, f'🙋「{task.get("title","")}」來回改了 {MAX_ROUNDS} 輪還沒過，到協作平台看要不要直接定稿')
            else:
                set_stage(cfg, tok, task, {'stage': 'making', 'round': round_no, 'updatedAt': now_ms})
    elif role == 'designer':
        canva_url = parse_canva_url(out)
        if not canva_url:
            set_stage(cfg, tok, task, {
                'stage': 'waiting_human', 'waitingKind': 'needHuman',
                'waitingReason': '小蝶跑完了但沒抓到 Canva 連結，麻煩到協作平台看一下發生什麼事',
                'resumeStage': 'designing', 'updatedAt': now_ms})
            line_push(cfg, f'🙋「{task.get("title","")}」的圖卡沒拿到 Canva 連結，到協作平台看一下')
            return True
        set_stage(cfg, tok, task, {'stage': 'done', 'canvaUrl': canva_url, 'updatedAt': now_ms})
        if task.get('hySocialId'):
            db_patch(cfg, tok, HY_ROOM, f'posts/{task["hySocialId"]}', {'link': canva_url})
        line_push(cfg, f'🎨「{task.get("title","")}」的圖卡做好了\n\nCanva 編輯：{canva_url}')
    return True


def process_dm(cfg, tok, role, dm_msgs):
    p = PERSONA[role]
    rows = sorted(dm_msgs.values(), key=lambda m: m.get('createdAt', 0))
    lines = []
    for m in rows:
        who = '你' if m.get('from') == 'human' else p['name']
        lines.append(f"[{who}] {m.get('text', '')}")
    transcript = '\n'.join(lines)
    prompt = DM_PROMPT.format(name=p['name'], desc=p['desc'], transcript=transcript)

    print(f"{time.strftime('%F %T')} 開始跑私聊 / {role}")
    now_ms = int(time.time() * 1000)
    try:
        out = run_claude(prompt, model=CHAT_MODEL)  # 聊天求快
    except QuotaExhausted:
        raise          # 額度用完就整體暫停，不要在對話裡留一句「跑失敗了」
    except Exception as e:
        db_post(cfg, tok, ROOM, f'dms/{role}', {
            'from': role, 'text': f'（這輪跑失敗了：{str(e)[:150]}，再傳一次看看）', 'createdAt': now_ms})
        return
    db_post(cfg, tok, ROOM, f'dms/{role}', {'from': role, 'text': out, 'createdAt': now_ms})


def parse_group_task(out):
    """從小梟的回覆裡把派工指令那行挑出來，回傳 (指令 dict 或 None, 清掉指令後的正文)。
    2026-09-09 加：在這之前工作群只會回話不會派工，她 8/26 交代「讓小蝶重新製圖」、
    9/9 交代「重寫一篇」，小梟都答應了但實際上一件事都沒發生。"""
    m = re.search(r'^\s*PARTY_TASK:\s*(\{.*\})\s*$', out, re.M)
    if not m:
        return None, out
    cleaned = (out[:m.start()] + out[m.end():]).strip()
    try:
        spec = json.loads(m.group(1))
    except ValueError:
        return None, cleaned
    if not isinstance(spec, dict) or not (spec.get('title') or '').strip():
        return None, cleaned
    if spec.get('type') not in ('觀點/趨勢', '教學/新手', '日常/節慶', '廣告顧問陪跑'):
        spec['type'] = '其他'
    return spec, cleaned


def next_free_tuesday(cfg, tok):
    """找下一個還沒排貼文的週二（禾言固定一週一篇、週二發）。
    日期交給程式算比較準——小梟在工作群裡看不到目前的排程表。"""
    posts = db_get(cfg, tok, HY_ROOM, 'posts') or {}
    taken = {p.get('date') for p in posts.values() if isinstance(p, dict)}
    d = date.today()
    d += timedelta(days=(1 - d.weekday()) % 7 or 7)   # 下一個週二（今天是週二就跳下週）
    for _ in range(52):
        if d.isoformat() not in taken:
            return d.isoformat()
        d += timedelta(days=7)
    return d.isoformat()


def process_group_chat(cfg, tok, msgs):
    """工作群：小梟代表團隊回覆，跟私聊不同的是這裡的話要記住、影響之後的規劃判斷
    （靠 recent_group_chat_summary() 在下次規劃/月建議時撈回來用，不是這支自己記）。"""
    rows = sorted(msgs.values(), key=lambda m: m.get('createdAt', 0))
    lines = []
    for m in rows:
        who = '你' if m.get('from') == 'human' else '小梟'
        lines.append(f"[{who}] {m.get('text', '')}")
    transcript = '\n'.join(lines)
    prompt = GROUP_CHAT_PROMPT.format(transcript=transcript)

    print(f"{time.strftime('%F %T')} 開始跑工作群回覆")
    now_ms = int(time.time() * 1000)
    try:
        out = run_claude(prompt, model=CHAT_MODEL)  # 聊天求快
    except QuotaExhausted:
        raise          # 同上，額度用完不要污染工作群
    except Exception as e:
        db_post(cfg, tok, ROOM, 'groupChat', {
            'from': 'planner', 'text': f'（這輪跑失敗了：{str(e)[:150]}，再說一次看看）', 'createdAt': now_ms})
        return
    spec, out = parse_group_task(out)
    db_post(cfg, tok, ROOM, 'groupChat', {'from': 'planner', 'text': out, 'createdAt': now_ms})
    if not spec:
        return

    # 她在工作群交代要產出一篇 → 真的把任務建起來，直接進「製作」讓小兔開寫
    # （方向已經在對話裡講清楚了，不用再繞回「規劃」那一關）
    post_date = (spec.get('postDate') or '').strip() or next_free_tuesday(cfg, tok)
    task_id = db_post(cfg, tok, ROOM, 'tasks', {
        'title': spec['title'], 'type': spec.get('type') or '其他',
        'postDate': post_date, 'goal': spec.get('goal') or '',
        'brief': f"（工作群交代）{spec.get('brief') or ''}",
        'stage': 'making', 'round': 0,
        'createdAt': now_ms, 'updatedAt': now_ms,
    })
    db_post(cfg, tok, ROOM, f'messages/{task_id}', {
        'role': 'planner', 'text': f"從工作群接到的方向：\n{spec.get('brief') or ''}",
        'createdAt': now_ms})
    print(f"{time.strftime('%F %T')} 工作群派工 → 建任務 / {spec['title']} / {post_date}")
    line_push(cfg, f'📋 小梟把你在工作群交代的「{spec["title"]}」建成任務了，'
                   f'小兔開始寫，排在 {post_date}')


def check_design_window(cfg, tok):
    """awaiting_window 的任務，離發布日剩不到 DESIGN_WINDOW_DAYS 天（或已經過期）就推進到「製圖」。
    純資料庫操作、沒有 claude -p 呼叫，每輪都可以做，不佔「一次只跑一件」的名額。"""
    tasks = db_get(cfg, tok, ROOM, 'tasks') or {}
    today = date.today()
    for tid, t in tasks.items():
        if not isinstance(t, dict) or t.get('stage') != 'awaiting_window':
            continue
        try:
            pd = date.fromisoformat(t.get('postDate') or '')
        except ValueError:
            continue  # 沒填發布日就先不推，留給她自己在網頁上處理
        if (pd - today).days <= DESIGN_WINDOW_DAYS:
            t = dict(t); t['id'] = tid
            set_stage(cfg, tok, t, {'stage': 'designing', 'updatedAt': int(time.time() * 1000)})
            print(f"{time.strftime('%F %T')} {tid} 進入製圖窗口，推進到 designing")


# ---------------------------------------------------------------------------
# 2026-09-09 加的三組東西：網路等待/重試、到期自動開工製圖、發布提醒
# 起因：她問「本週為什麼沒有跑流程」，查出來排程是活的、只是沒東西可跑——
# 規劃表上「有文案、沒圖卡」的貼文沒有任何機制會自動被接手，9/8 那篇因此漏掉。
# ---------------------------------------------------------------------------

def wait_network(host='oauth2.googleapis.com', max_sec=NET_WAIT_SEC):
    """Mac 剛睡醒時網路還沒接上，DNS 會整批解析不到。等它通，最多等 max_sec 秒。
    跟 dora-report-runner.py / dora-ads-anomaly.sh 同一個毛病、同一種修法：
    探測的要是這支真正要連的主機（Google 換 token），不是隨便一個網站。"""
    deadline = time.time() + max_sec
    while time.time() < deadline:
        try:
            socket.getaddrinfo(host, 443)
            return True
        except OSError:
            time.sleep(2)
    try:
        socket.getaddrinfo(host, 443)
        return True
    except OSError:
        return False


def ws_token_retry(cfg):
    """換 token 重試幾次才放棄。網路抖一下不該讓整輪直接崩掉噴堆疊——
    2026-09-09 之前沒有這層，err log 因此累積到 194KB 全是 URLError。"""
    for i in range(TOKEN_RETRY):
        try:
            return ws_token(cfg['WS_SA_KEY'])
        except Exception as e:
            if i == TOKEN_RETRY - 1:
                print(f"{time.strftime('%F %T')} 連不上網路，這輪跳過（{str(e)[:80]}）")
                return None
            time.sleep(TOKEN_RETRY_GAP)
    return None


def state_path(name):
    os.makedirs(STATE_DIR, exist_ok=True)
    return os.path.join(STATE_DIR, name)


def state_seen(name):
    return os.path.exists(state_path(name))


def state_mark(name):
    with open(state_path(name), 'w') as f:
        f.write(str(int(time.time())))


def state_cleanup(days=7):
    """清掉舊的標記檔，不然這個資料夾會一直長大。"""
    if not os.path.isdir(STATE_DIR):
        return
    cutoff = time.time() - days * 86400
    for fn in os.listdir(STATE_DIR):
        fp = os.path.join(STATE_DIR, fn)
        try:
            if os.path.getmtime(fp) < cutoff:
                os.unlink(fp)
        except OSError:
            pass


def should_scan():
    """重的掃描（讀整份規劃表）至少隔 SCAN_MIN_GAP 秒才做一次。
    用單一檔案記上次時間，不是一分鐘留一個標記檔（那樣一週會堆一萬個檔）。"""
    os.makedirs(STATE_DIR, exist_ok=True)
    fp = os.path.join(STATE_DIR, 'last-scan')
    try:
        if time.time() - os.path.getmtime(fp) < SCAN_MIN_GAP:
            return False
    except OSError:
        pass
    with open(fp, 'w') as f:
        f.write(str(int(time.time())))
    return True


def check_design_due(cfg, tok):
    """禾言規劃表上「有文案、還沒圖卡」的貼文，離發布日剩 DESIGN_WINDOW_DAYS 天
    （含已經過期的）就自動建一筆製圖任務交給小蝶，並推 LINE 說一聲開工了。

    為什麼要這一段（2026-09-09）：原本只有「任務從審閱通過走到 awaiting_window」
    這一條路會進製圖，她自己直接在規劃表排的貼文沒有任何人接手。8/26 之後她沒建過
    新任務，系統就閒置兩週，9/8 該發那篇文案有 607 字、圖卡沒做、也沒發。

    一輪最多建一篇（挑最早該發的那篇），避免七篇同時開工把 Claude 額度燒光。
    純資料庫操作，不吃額度，每輪都可以做。"""
    posts = db_get(cfg, tok, HY_ROOM, 'posts') or {}
    today = date.today()
    cands = []
    for pid, p in posts.items():
        if not isinstance(p, dict) or p.get('done'):
            continue
        if not (p.get('ig') or '').strip():
            continue                      # 沒文案不能做圖
        if (p.get('link') or '').strip():
            continue                      # 已經有圖卡了
        stage = p.get('agentStage')
        if stage and stage != 'done':
            continue                      # 正在被處理中，別重複建（少了這條會每分鐘建一筆）
        try:
            pd = date.fromisoformat(p.get('date') or '')
        except ValueError:
            continue                      # 沒填發布日就不自動動它
        left = (pd - today).days
        if left > DESIGN_WINDOW_DAYS:
            continue
        cands.append((p.get('date'), pid, p, left))
    if not cands:
        return
    cands.sort()
    _, pid, p, left = cands[0]            # 最早該發的先做
    now_ms = int(time.time() * 1000)
    when = f'還有 {left} 天要發' if left > 0 else ('今天就要發' if left == 0 else f'已經過期 {-left} 天')
    task_id = db_post(cfg, tok, ROOM, 'tasks', {
        'title': p.get('title', ''), 'type': p.get('type', ''), 'postDate': p.get('date', ''),
        'goal': p.get('goal', ''),
        'brief': f'文案已經在規劃表上定稿，{when}，自動開工做圖卡。',
        'stage': 'designing', 'round': 0, 'hySocialId': pid,
        'createdAt': now_ms, 'updatedAt': now_ms,
    })
    db_post(cfg, tok, ROOM, f'messages/{task_id}', {
        'role': 'system', 'text': f'{when}，自動開工做圖卡（文案以規劃表上的版本為準）。',
        'createdAt': now_ms})
    db_patch(cfg, tok, HY_ROOM, f'posts/{pid}', {'agentStage': 'designing', 'agentTaskId': task_id})
    print(f"{time.strftime('%F %T')} 自動開工製圖 / {p.get('title','')} / {p.get('date','')}")
    line_push(cfg, f'🦋 小蝶開工做「{p.get("title","")}」的圖卡了（{p.get("date","")} 要發，{when}）')


def check_publish_reminder(cfg, tok):
    """發布當天早上提醒她去發，過了發布日還沒打勾再追幾天。
    只在早上 REMIND_HOUR_START–REMIND_HOUR_END 之間推，靠標記檔確保同一件事一天只推一次。"""
    hour = time.localtime().tm_hour
    if not (REMIND_HOUR_START <= hour < REMIND_HOUR_END):
        return
    posts = db_get(cfg, tok, HY_ROOM, 'posts') or {}
    today = date.today()
    for pid, p in posts.items():
        if not isinstance(p, dict) or p.get('done'):
            continue
        try:
            pd = date.fromisoformat(p.get('date') or '')
        except ValueError:
            continue
        late = (today - pd).days
        if late == 0:
            kind = 'today'
        elif 0 < late <= OVERDUE_REMIND_DAYS:
            kind = 'overdue'
        else:
            continue
        mark = f'{today.isoformat()}-{pid}-{kind}'
        if state_seen(mark):
            continue
        link = (p.get('link') or '').strip()
        card = f'\n圖卡：{link}' if link else '\n（圖卡還沒做好）'
        if kind == 'today':
            line_push(cfg, f'📌 今天要發「{p.get("title","")}」{card}\n\n發完記得到規劃表打勾')
        else:
            line_push(cfg, f'⚠️「{p.get("title","")}」原本 {p.get("date","")} 要發，還沒標成已發布{card}')
        state_mark(mark)
        print(f"{time.strftime('%F %T')} 推發布提醒 / {kind} / {p.get('title','')}")
        return   # 一輪只推一則，避免一次七則洗版


PROPOSAL_PROMPT = """你是「小梟」，禾言數位行銷社群規劃團隊的規劃小幫手。現在要規劃 {target_month} \
整個月的社群貼文主題建議（不是單篇，是一整個月的清單），先給朱兒確認過，她點頭才會正式建立任務。

禾言的社群節奏規則：
- 一週一篇，固定週二發布；當月如果有重要節慶，可以在節慶當天加開一篇
- 每個月至少一篇「廣告顧問陪跑」類型（這條線最直接帶詢價）
- 「教學/新手」類型不要超過整月篇數的一半
- 不同類型要交錯排，不要同一類型連續兩篇；有時效性的節慶/檔期主題要排在對的日期附近，
  不要為了交錯硬搬
- 上網查一下 {target_month} 有沒有重要的行銷相關節慶或檔期，有的話排進去

這個月＋上個月已經在禾言規劃表裡的內容（**這個月要規劃的內容不要跟這些話題重複**）：
{existing_posts}

{group_notes}請規劃 {target_month} 這個月的貼文，抓 4-6 篇，{target_month}的週二日期你自己算出來。
每篇輸出一行，格式固定（用全形｜分隔，不要換別的符號）：
N. 日期=YYYY-MM-DD｜類型=XXX｜標題=XXX｜方向=一句話說明這篇要寫什麼

「方向」那欄要寫得出**這篇的單一主張**（一篇只講一件事），不是列一個大主題。
標題也不能只寫大主題，要讓人看見問題、結果、衝突或具體利益。

只輸出這個清單，不要加開場白或其他說明文字，不要用 markdown。"""

AUDIT_PROMPT = """你是「小梟」，禾言數位行銷社群規劃團隊的規劃小幫手。定期回頭檢查已經排定、
但還沒發布的貼文，看內容是不是還適合現在發、需不需要調整。

這篇的資訊：
- 發布日：{post_date}
- 類型：{post_type}
- 標題：{title}
- 目前文案：
{ig}

請判斷這篇還適不適合照原樣發布——有沒有事實過期（提到的平台功能／數字／時事已經變了）、
方向是不是還符合現在的狀況。沒有明顯問題就不用雞蛋裡挑骨頭。

如果沒問題，最後一行單獨寫：
決定：不用調整

如果需要調整，具體寫出要改哪裡、怎麼改，最後一行單獨寫：
決定：需要調整

只輸出判斷內容本身，不要加開場白。"""


def parse_proposal_items(out):
    items = []
    for line in out.splitlines():
        m = re.match(r'^\d+\.\s*日期=([\d-]+)｜類型=([^｜]+)｜標題=([^｜]+)｜方向=(.+)$', line.strip())
        if m:
            items.append({'date': m.group(1), 'type': m.group(2).strip(),
                          'title': m.group(3).strip(), 'angle': m.group(4).strip()})
    return items


def check_monthly_proposal(cfg, tok):
    """每月第三週左右，小梟該主動排下個月的內容建議了嗎？回傳目標月份（YYYY-MM）或 None。
    用 proposals/{yyyy-mm} 存不存在判斷這個月做過沒有，一個月只會生一次。"""
    today = date.today()
    if not (PROPOSAL_DAY_START <= today.day <= PROPOSAL_DAY_END):
        return None
    y, m = today.year, today.month
    ty, tm = (y + 1, 1) if m == 12 else (y, m + 1)
    target_key = f'{ty:04d}-{tm:02d}'
    if db_get(cfg, tok, ROOM, f'proposals/{target_key}'):
        return None
    return target_key


def run_monthly_proposal(cfg, tok, target_key):
    ty, tm = (int(x) for x in target_key.split('-'))
    target_month_label = f'{ty} 年 {tm} 月'
    existing = existing_hy_posts_summary(cfg, tok, f'{target_key}-01')
    prompt = PROPOSAL_PROMPT.format(target_month=target_month_label, existing_posts=existing,
                                     group_notes=recent_group_chat_summary(cfg, tok))
    print(f"{time.strftime('%F %T')} 開始跑月規劃建議 / {target_key}")
    now_ms = int(time.time() * 1000)
    try:
        out = run_claude(prompt, allowed=PLANNER_ALLOWED, timeout=TIMEOUT)
    except QuotaExhausted:
        raise
    except Exception as e:
        print('月規劃建議失敗：', e)
        return
    items = parse_proposal_items(out)
    db_patch(cfg, tok, ROOM, f'proposals/{target_key}', {
        'status': 'pending', 'items': items, 'rawText': out, 'createdAt': now_ms})
    if items:
        line_push(cfg, f'📅 小梟排好 {target_month_label} 的內容建議了（{len(items)} 篇），到 agent-hub 看要不要用')
    else:
        line_push(cfg, f'⚠️ 小梟想排 {target_month_label} 的內容建議，但輸出格式解析不出來，到 agent-hub 看一下原始內容')


def parse_audit_verdict(out):
    m = re.search(r'決定[：:]\s*(不用調整|需要調整)\s*$', out.strip())
    return m.group(1) if m else None


def check_audit_scan(cfg, tok):
    """挑一篇該定期稽核的已排程貼文（還沒發布、沒有正在被 agent-hub 處理、
    上次稽核是一週以前）。一次只挑最早發布日那篇，回傳 (post_id, post) 或 None。"""
    if not audit_gap_ok():
        return None          # 剛稽核過，先讓聊天與任務有機會插隊
    posts = db_get(cfg, tok, HY_ROOM, 'posts') or {}
    today_str = date.today().isoformat()
    now_ms = int(time.time() * 1000)
    candidates = []
    for pid, p in posts.items():
        if not isinstance(p, dict) or p.get('done'):
            continue
        d = p.get('date') or ''
        if d < today_str:
            continue  # 已經過期沒發的不在稽核範圍內，那是另一個問題
        if p.get('agentStage') and p.get('agentStage') != 'done':
            continue  # 正在被處理中的不要打斷
        if now_ms - (p.get('lastAuditAt') or 0) < AUDIT_INTERVAL_SEC * 1000:
            continue  # 這週已經稽核過了
        candidates.append((pid, p))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[1].get('date', ''))
    return candidates[0]


def run_audit_one(cfg, tok, post_id, p):
    prompt = AUDIT_PROMPT.format(
        post_date=p.get('date', ''), post_type=p.get('type', ''),
        title=p.get('title', ''), ig=p.get('ig', ''))
    print(f"{time.strftime('%F %T')} 開始跑內容稽核 / {post_id}")
    now_ms = int(time.time() * 1000)
    audit_mark()   # 不管結果如何都算「剛稽核過」，下一篇要隔 AUDIT_MIN_GAP 才輪到
    try:
        out = run_claude(prompt, allowed=PLANNER_ALLOWED, timeout=TIMEOUT)
    except QuotaExhausted:
        raise
    except Exception as e:
        print('稽核失敗：', e)
        # 失敗也要記時間，往後推一小時再試。2026-09-09 之前沒記，額度用完那段
        # 同一篇被重試了 50 次，整個系統一小時都在空轉。
        db_patch(cfg, tok, HY_ROOM, f'posts/{post_id}',
                 {'lastAuditAt': now_ms - AUDIT_INTERVAL_SEC * 1000 + 3600 * 1000})
        return
    db_patch(cfg, tok, HY_ROOM, f'posts/{post_id}', {'lastAuditAt': now_ms})
    if parse_audit_verdict(out) != '需要調整':
        return  # 沒問題，不用打擾她
    task_id = db_post(cfg, tok, ROOM, 'tasks', {
        'title': p.get('title', ''), 'type': p.get('type', ''), 'postDate': p.get('date', ''),
        'goal': p.get('goal', ''), 'brief': f'小梟定期稽核發現這篇需要調整：{out}',
        'stage': 'making', 'round': 0, 'hySocialId': post_id,
        'createdAt': now_ms, 'updatedAt': now_ms,
    })
    db_post(cfg, tok, ROOM, f'messages/{task_id}', {
        'role': 'planner', 'text': f'定期稽核發現這篇需要調整：\n{out}', 'createdAt': now_ms})
    db_patch(cfg, tok, HY_ROOM, f'posts/{post_id}', {'agentStage': 'making', 'agentTaskId': task_id})
    line_push(cfg, f'🦉 小梟稽核發現「{p.get("title","")}」需要調整，已經交給小兔改文案')


def run_round(cfg, tok):
    """一輪要做的事。網路出狀況由 main() 統一接住印一行就好，不要噴堆疊。"""
    # 這幾項要讀整份禾言規劃表，比較重。launchd 改成 20 秒一次是為了讓聊天快點被接到，
    # 掃描沒必要跟著變三倍，維持約一分鐘一次（2026-09-09）。
    if should_scan():
        check_design_window(cfg, tok)     # 純資料庫操作，不吃額度
        check_design_due(cfg, tok)        # 規劃表上到期的貼文自動開工做圖卡
        check_publish_reminder(cfg, tok)  # 發布當天／漏發提醒
        state_cleanup()

    jobs = []  # (時間戳, 種類, 資料)

    tasks = db_get(cfg, tok, ROOM, 'tasks') or {}
    for tid, t in tasks.items():
        if isinstance(t, dict) and t.get('stage') in ('planning', 'making', 'reviewing', 'designing'):
            t = dict(t); t['id'] = tid
            jobs.append((t.get('updatedAt', t.get('createdAt', 0)), 'task', t))

    all_dms = db_get(cfg, tok, ROOM, 'dms') or {}
    for role in PERSONA:
        dm_msgs = all_dms.get(role) or {}
        if not dm_msgs:
            continue
        rows = sorted(dm_msgs.values(), key=lambda m: m.get('createdAt', 0))
        last = rows[-1]
        if last.get('from') == 'human':
            jobs.append((last.get('createdAt', 0), 'dm', (role, dm_msgs)))

    group_msgs = db_get(cfg, tok, ROOM, 'groupChat') or {}
    if group_msgs:
        rows = sorted(group_msgs.values(), key=lambda m: m.get('createdAt', 0))
        if rows[-1].get('from') == 'human':
            jobs.append((rows[-1].get('createdAt', 0), 'group', group_msgs))

    tok2 = ws_token_retry(cfg)
    if not tok2:
        return

    if jobs:
        # 私聊／工作群優先於任務推進：她在等聊天回覆比任務多花一輪才推進更有感，
        # 同一層級才照時間排序（2026-08-26 她實測發現私聊被任務卡住太久）
        jobs.sort(key=lambda j: (0 if j[1] in ('dm', 'group') else 1, j[0]))
        kind, payload = jobs[0][1], jobs[0][2]
        if kind == 'task':
            process_task(cfg, tok2, payload)
        elif kind == 'dm':
            role, dm_msgs = payload
            process_dm(cfg, tok2, role, dm_msgs)
        else:
            process_group_chat(cfg, tok2, payload)
        return

    # 沒有任務/私聊要處理，這輪換去做背景維護：月規劃建議或定期稽核，一樣一次只做一件
    target = check_monthly_proposal(cfg, tok)
    if target:
        run_monthly_proposal(cfg, tok2, target)
        return
    audit_candidate = check_audit_scan(cfg, tok)
    if audit_candidate:
        run_audit_one(cfg, tok2, *audit_candidate)


def lock_is_alive():
    """鎖檔裡存的是 pid，程序還在才算真的有人在跑。
    2026-09-09 加：重啟排程（launchctl unload）會直接殺掉正在跑的程序，
    finally 來不及刪鎖，整套系統就被一個沒有主人的鎖卡住 LOCK_STALE（17 分鐘）。"""
    try:
        pid = int(open(LOCK).read().strip())
    except (OSError, ValueError):
        return False
    try:
        os.kill(pid, 0)      # 只是探測，不會真的送訊號
        return True
    except OSError:
        return False


def main():
    if (os.path.exists(LOCK) and time.time() - os.path.getmtime(LOCK) < LOCK_STALE
            and lock_is_alive()):
        return
    open(LOCK, 'w').write(str(os.getpid()))
    try:
        cfg = load_env()
        if not all(cfg.get(k) for k in ('WS_SA_KEY', 'WS_DB_URL')):
            print('設定不全，跳過'); return
        # Mac 剛睡醒網路還沒通時，以前這裡會整輪崩掉噴堆疊（err log 累積到 194KB）。
        # 2026-09-09 改成先等網路、換 token 重試，真的不通就安靜結束等下一分鐘。
        if not wait_network():
            print(f"{time.strftime('%F %T')} 網路還沒通，這輪跳過")
            return
        blocked = quota_blocked_until()
        if blocked:
            return          # Claude 額度還沒恢復，安靜等，不要每 20 秒叫一次空轉
        tok = ws_token_retry(cfg)
        if not tok:
            return
        try:
            run_round(cfg, tok)
        except QuotaExhausted as e:
            until = quota_block(str(e))
            print(f"{time.strftime('%F %T')} Claude 額度用完，暫停到 "
                  f"{time.strftime('%H:%M', time.localtime(until))} 再繼續")
        except OSError as e:
            # URLError / ConnectionResetError / 逾時都是 OSError 的子類，
            # 網路中途斷掉就印一行，不要噴整串堆疊把 err log 灌爆
            print(f"{time.strftime('%F %T')} 這輪中途連線出狀況，跳過（{type(e).__name__}: {str(e)[:100]}）")
    finally:
        if os.path.exists(LOCK):
            os.unlink(LOCK)


if __name__ == '__main__':
    main()
