#!/usr/bin/env python3
"""禾言社群規劃團隊的背景引擎：規劃 → 製作 → 審閱 → 製圖 四個角色接力做每篇貼文。

為什麼要這一支：跟「廣告回報」同一個道理，用她已經在付的 Claude Code 訂閱當引擎，
不用另外接付費 API。launchd 每分鐘叫一次，每次只推進「最舊那一件還在跑的任務」一步，
不會同時燒好幾個 claude -p 行程搶額度（2026-08-25 定案：一次只跑一件）。

2026-08-25 從「任何客戶都能丟的社群/廣告文案工具」改版成專門服務禾言自己的
月度社群規劃團隊（她的原話：「這個我把它定義為社群規劃團隊」），四個角色：
- 小梟（規劃）：抓 Meta/Google/LINE 廣告平台更新消息＋客戶常遇到的問題話題，
  結合她每月給的重點，訂出這篇的方向（會用 WebSearch，也會讀 200_Reference/clients/）
- 小兔（製作）：照方向寫成正式文案，好閱讀不無聊
- 小狐（審閱）：不是校對機，審的是「這方向大眾想不想看」「文案吸不吸引人」「話題有沒有趣」，
  AI 味/半形標點是次要檢查項
- 小蝶（製圖）：文案定稿後，照 `禾言圖文` skill 的柔和版模板流程做成 Canva 可編輯的 6 頁圖卡
  （這一階段會真的 git push＋呼叫 Canva MCP，是四個角色裡唯一會動到共用檔案的）

接力規則：
- 規劃 → 製作 → 審閱；審閱「需要修改」退回製作，最多來回 2 輪，超過就標記「需要你決定」
- 審閱「通過」→ 定稿文案直接寫進「禾言社群規劃」網頁的資料（hy_social_r7n3k8），
  接著進「製圖」；製圖做完（或需要她確認）才算整件結束
- 任何角色判斷不出來會在回覆開頭寫 NEED_HUMAN，這支腳本看到就整件標記「需要你決定」並推播 LINE
  （其他時候不推播，不然天天洗版）

網頁上還有「私聊」——朱兒可以不透過任務、直接跟某個角色聊天。
每輪一樣只挑「最舊的一件待處理事」動手，任務推進跟私聊回覆一起排隊，不會搶額度。
"""
import base64, json, os, re, subprocess, sys, tempfile, time
from datetime import date
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ENV     = os.path.expanduser('~/Library/Scripts/dora.env')
CLAUDE  = '/Users/zhuer/.local/bin/claude'
WORKDIR = '/Users/zhuer/Downloads/Dora專屬'
TIMEOUT = 600                 # 一般角色最多跑 10 分鐘
DESIGN_TIMEOUT = 900          # 製圖要 git push＋等 Pages 更新＋Canva，給多一點
LOCK    = '/tmp/dora-agent-hub-runner.lock'
CLIENTS = os.path.join(WORKDIR, '200_Reference', 'clients')
ROOM    = 'ah_4j2ppkn8rq'     # 改名要同步改 Firebase 規則
HY_ROOM = 'hy_social_r7n3k8'  # 禾言社群規劃網頁的節點，定稿後直接寫進這裡
MAX_ROUNDS = 2                 # 製作/審閱最多來回幾輪
DESIGN_WINDOW_DAYS = 3          # 審閱通過後不馬上做圖，等到離發布日剩這幾天才交給小蝶（2026-08-26 她要求）
PROPOSAL_DAY_START, PROPOSAL_DAY_END = 15, 21   # 每月第三週（大致），小梟排下個月建議
AUDIT_INTERVAL_SEC = 7 * 86400  # 小梟定期掃描已排程內容，一週一次就好，不用每天掃

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
接下來會有「小兔」照你的方向寫文案、「小狐」審閱、「小蝶」做圖卡，你的規劃是整條線的起點。

這篇的類型：{type_label}
預計發布日：{post_date}

這個月客戶最想了解的內容／重點方向（朱兒提供）：
{brief}

禾言規劃表這個月＋上個月已經排的貼文（**先看這個，不要跟這些話題或切角撞在一起**）：
{existing_posts}
{group_notes}{transcript_block}
請做三件事再下判斷：
1. 先看上面「已經排的貼文」，確認這次要寫的方向沒有跟其中任何一篇的話題或切角重複
2. 上網搜尋 Meta／Google／LINE 廣告平台最近的更新消息、新功能或政策變化，
   找出跟「{type_label}」這個類型相關、值得跟客戶分享的重點
3. 讀 `200_Reference/clients/` 底下幾個客戶檔，看有沒有記到廣告投放中實際遇到的問題；
   也可以搜尋一下這個產業常見的廣告投放痛點文章，交叉比對哪個話題最值得寫

綜合以上，直接寫出：
1. 這篇的核心方向與切角（要具體，不要只寫「跟廣告更新有關」這種空泛的話；
   如果發現跟既有貼文重疊，換一個角度或换一個更具體的子題目，不要硬寫一樣的）
2. 為什麼這個時間點適合寫這個話題（呼應了什麼平台更新／客戶痛點）
3. 給小兔的重點提醒（語氣、要不要提到具體案例情境等）
{title_note}
如果需求描述得不夠清楚、判斷不出方向，不要亂猜——在回覆最開頭寫一行
NEED_HUMAN: <你不確定的地方，一句話說清楚>
然後結束，不要往下硬寫。

只輸出規劃內容本身，不要加「好的」「以下是」這種開場白，不要用 markdown 標題符號。"""

TITLE_NOTE = """4. 幫這篇取一個吸引人的標題，最後一行單獨寫：
建議標題：<標題>
"""

MAKER_PROMPT = """你是「小兔」，禾言數位行銷社群規劃團隊的製作小幫手，
負責照小梟規劃的方向，把這篇「{type_label}」貼文寫成禾言官方 IG／FB 要發的正式文案。

標題：{title}
預計發布日：{post_date}
{transcript_block}
{revision_note}
請先讀 `000_Agent/skills/社群文案撰寫/SKILL.md` 了解寫作方法，
再讀 `200_Reference/writing-samples/禾言社群文案-朱兒改寫範例.md`
——這是朱兒親手把你寫的稿子改過一輪的真實對照，**照那份的手法寫，不是只看規則條文**，
也讀 `200_Reference/clients/禾言數位行銷.md` 了解禾言的品牌調性與案例素材。

禾言的社群文案規則（一定要照做）：
- **讀者是完全沒有廣告投放背景的新手小白、客戶，不是同業或行銷人**：
  一目瞭然、白話、清楚好懂，不能寫得複雜。專有名詞第一次出現要用白話解釋一句
  （例如「頻率」要順便講白話是什麼意思，不能預設讀者懂），寧可寫得淺白一點，
  也不要為了顯得專業把內容講複雜
- 語氣活潑版（emoji、畫面感），受眾是中小企業老闆與行銷窗口
- 結尾一定要有一段【禾言觀點】／【禾言怎麼做】／【禾言建議】其中一種（依內容性質挑，
  觀點類用「觀點」、給具體下一步的用「建議」或「怎麼做」），接一句互動提問或行動呼籲，
  最後才是 hashtag；節慶類主題不用這段，直接用行動呼籲收尾
- 內容要好閱讀、不無聊：先想「用戶會喜歡看什麼寫法」，不要寫成生硬的知識條列
- AI 趨勢類主題不要寫死平台功能名稱（後台改版快，寫死會過期）
- 案例先不寫（禾言案例庫還沒串進來）
- 不用加「— 禾言數位行銷」署名行，hashtag 裡已經有 #禾言數位行銷

寫完第一版之後，**用 Skill 工具實際執行一次 `speak-human-tw`**，把剛寫好的初稿交給它去 AI 味
（這是非互動環境，沒有人能回答確認清單，那個 skill 自己的規則會偵測到、自動跳過確認直接套用，
不會卡住等回覆；跑完會給你一份修改摘要）。用它處理完的最終版本當作你的正式定稿，
不要用你自己的印象模仿它的邏輯，要真的呼叫這個 skill。
重點對照 `200_Reference/writing-samples/禾言社群文案-朱兒改寫範例.md` 那份的原則
（整段重複的意思整段刪、拿掉鋪陳開場白、長句拆短行、少用破折號、對比改用→做視覺節奏、
模糊描述換具體詞），這些跟 speak-human-tw 抓的問題本來就高度重疊，兩邊會互相加強。

直接產出「一則」完整定案、已經跑過 speak-human-tw 的文案（不要給我 A/B 兩個版本選項，
你自己選一個最好的直接寫），你的輸出會直接被存成正式文案，**只留最終文案本身**——
speak-human-tw 跑完給你的清單/摘要文字不要留在輸出裡，不要加「以下是初稿」這種標籤或說明。

如果判斷不出怎麼下筆，在回覆最開頭寫一行：NEED_HUMAN: <原因>，然後結束。"""

REVIEWER_PROMPT = """你是「小狐」，禾言數位行銷社群規劃團隊的審閱小幫手，
負責幫這篇「{type_label}」貼文把關。你是內容策略角度的審閱者，不是校對機。

{transcript_block}
檢查重點（依重要度排序）：
1. 規劃的方向是不是真的符合現階段大眾想了解的內容——不是「正確但無聊」，是不是有人會真的想點開看
2. **讀者是完全沒有廣告投放背景的新手小白、客戶**：文案是不是一目瞭然、白話好懂，
   有沒有哪裡寫得太複雜、術語沒解釋就直接用——這種要退回去要求改得更淺白
3. 文案開頭夠不夠吸引人注意，會不會讓人滑過去就跳過
4. 整體話題會不會讓人有興趣讀完、有沒有記憶點

其次也留意（比較次要，不是決定通過與否的主因）：
- 有沒有明顯 AI 味（「不是X是Y」對仗、金句收尾、三點式過工整、破折號轉折、「其實」「真正的」開頭）、半形標點

如果這版可以定稿了，具體說一下這版好在哪裡（尤其是「為什麼會有人想看」），最後一行單獨寫：
決定：通過

如果還需要修改，具體寫出要改哪裡、怎麼改，最後一行單獨寫：
決定：需要修改

只輸出審閱意見本身，不要加開場白。"""

DESIGNER_PROMPT = """你是「小蝶」，禾言數位行銷社群規劃團隊的製圖小幫手，
負責把這篇已經審閱通過的貼文做成 6 頁 IG 圖卡的 Canva 可編輯檔。

標題：{title}
類型：{type_label}
定稿文案：
{final_copy}

請先完整讀過 `000_Agent/skills/禾言圖文/SKILL.md`，照它「Step 4（現行做法）：
套柔和版模板 → 匯入 Canva」往下做（拆頁規則在同一份文件的 Step 3）。

不用做它的 Step 1（取文案，我已經直接給你了）跟 Step 2（給朱兒確認文案——
這篇是小狐已經審閱通過的定稿，等同確認過了，不用再問一次）。

從 Step 3（拆成 6 頁）開始：套用柔和版模板、覆蓋 `100_Todo/projects/heyen-cards/index.html`、
commit push、等 GitHub Pages 更新、用 Canva MCP 匯入、讀縮圖檢查有沒有走樣。

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
不確定的事不要不懂裝懂掰數字。只輸出你要回的話本身，不要加開場白。"""


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


def run_claude(prompt, allowed=BASE_ALLOWED, timeout=TIMEOUT):
    p = subprocess.run(
        [CLAUDE, '-p', prompt, '--allowedTools', allowed],
        cwd=WORKDIR, capture_output=True, text=True, timeout=timeout)
    out = (p.stdout or '').strip()
    if p.returncode != 0 and not out:
        raise RuntimeError((p.stderr or '').strip()[:300] or f'claude 回傳 {p.returncode}')
    # 2026-08-27 踩過的坑：額度用完時 claude -p 仍會回傳 0 且印出這句提示，不是丟例外，
    # 結果被當成正常回覆寫進任務訊息裡，規劃/製作/審閱三個角色被污染了一輪都沒人發現。
    # 這句訊息很固定，直接抓字串當成失敗處理。
    if "hit your session limit" in out or "hit your weekly limit" in out or "usage limit" in out.lower():
        raise RuntimeError(f'Claude 額度用完了：{out[:200]}')
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
            transcript_block=transcript_block, revision_note=revision_note)
    elif stage == 'reviewing':
        role, allowed, timeout = 'reviewer', BASE_ALLOWED, TIMEOUT
        prompt = REVIEWER_PROMPT.format(type_label=type_label, transcript_block=transcript_block)
    elif stage == 'designing':
        role, allowed, timeout = 'designer', DESIGNER_ALLOWED, DESIGN_TIMEOUT
        # 文案以禾言規劃表「現在」的內容為準，不要用 ah 任務裡小兔那則舊訊息——
        # 她可能直接在禾言規劃表網頁上手改過文案，那個才是最新版（2026-08-26 踩過這個坑）
        final_copy = ''
        if task.get('hySocialId'):
            try:
                final_copy = (db_get(cfg, tok, HY_ROOM, f'posts/{task["hySocialId"]}') or {}).get('ig', '') or ''
            except Exception:
                final_copy = ''
        if not final_copy:
            final_copy = last_message_text(cfg, tok, tid, 'maker')
        prompt = DESIGNER_PROMPT.format(
            title=task.get('title') or '', type_label=type_label, final_copy=final_copy)
    else:
        return False

    print(f"{time.strftime('%F %T')} 開始跑 {tid} / {role}")
    db_patch(cfg, tok, ROOM, f'tasks/{tid}', {'processingRole': role, 'processingStartedAt': now_ms})
    try:
        out = run_claude(prompt, allowed=allowed, timeout=timeout)
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
        set_stage(cfg, tok, task, {'stage': 'reviewing', 'updatedAt': now_ms})
        if task.get('hySocialId'):
            db_patch(cfg, tok, HY_ROOM, f'posts/{task["hySocialId"]}', {'ig': out})
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
        line_push(cfg, f'🎨「{task.get("title","")}」的圖卡也做完了，全部四關都跑完啦\n\nCanva 編輯：{canva_url}')
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
        out = run_claude(prompt)
    except Exception as e:
        db_post(cfg, tok, ROOM, f'dms/{role}', {
            'from': role, 'text': f'（這輪跑失敗了：{str(e)[:150]}，再傳一次看看）', 'createdAt': now_ms})
        return
    db_post(cfg, tok, ROOM, f'dms/{role}', {'from': role, 'text': out, 'createdAt': now_ms})


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
        out = run_claude(prompt)
    except Exception as e:
        db_post(cfg, tok, ROOM, 'groupChat', {
            'from': 'planner', 'text': f'（這輪跑失敗了：{str(e)[:150]}，再說一次看看）', 'createdAt': now_ms})
        return
    db_post(cfg, tok, ROOM, 'groupChat', {'from': 'planner', 'text': out, 'createdAt': now_ms})


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
    try:
        out = run_claude(prompt, allowed=PLANNER_ALLOWED, timeout=TIMEOUT)
    except Exception as e:
        print('稽核失敗：', e)
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


def main():
    if os.path.exists(LOCK) and time.time() - os.path.getmtime(LOCK) < TIMEOUT:
        return
    open(LOCK, 'w').write(str(os.getpid()))
    try:
        cfg = load_env()
        if not all(cfg.get(k) for k in ('WS_SA_KEY', 'WS_DB_URL')):
            print('設定不全，跳過'); return
        tok = ws_token(cfg['WS_SA_KEY'])

        check_design_window(cfg, tok)  # 純資料庫操作，不吃額度，每輪都做

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

        tok2 = ws_token(cfg['WS_SA_KEY'])

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
    finally:
        if os.path.exists(LOCK):
            os.unlink(LOCK)


if __name__ == '__main__':
    main()
