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
DESIGNER_ALLOWED = 'Read,Write,Edit,Glob,Grep,Bash,Skill,' \
    'mcp__claude_ai_Canva__import-design-from-url,mcp__claude_ai_Canva__read-design'

PLANNER_PROMPT = """你是「小梟」，禾言數位行銷社群規劃團隊的規劃小幫手，負責幫這個月的一篇貼文訂方向。
接下來會有「小兔」照你的方向寫文案、「小狐」審閱、「小蝶」做圖卡，你的規劃是整條線的起點。

這篇的類型：{type_label}
預計發布日：{post_date}

這個月客戶最想了解的內容／重點方向（朱兒提供）：
{brief}
{transcript_block}
請做兩件事再下判斷：
1. 上網搜尋 Meta／Google／LINE 廣告平台最近的更新消息、新功能或政策變化，
   找出跟「{type_label}」這個類型相關、值得跟客戶分享的重點
2. 讀 `200_Reference/clients/` 底下幾個客戶檔，看有沒有記到廣告投放中實際遇到的問題；
   也可以搜尋一下這個產業常見的廣告投放痛點文章，交叉比對哪個話題最值得寫

綜合以上，直接寫出：
1. 這篇的核心方向與切角（要具體，不要只寫「跟廣告更新有關」這種空泛的話）
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
再讀 `200_Reference/writing-samples/` 找語氣範例，
也讀 `200_Reference/clients/禾言數位行銷.md` 了解禾言的品牌調性與案例素材。

禾言的社群文案規則（一定要照做）：
- 語氣活潑版（emoji、畫面感），受眾是中小企業老闆與行銷窗口
- 結尾一定要有一段【禾言觀點】或【禾言怎麼做】，接一句互動提問或行動呼籲，最後才是署名與 hashtag；
  節慶類主題不用【禾言觀點】，直接用行動呼籲收尾
- 內容要好閱讀、不無聊：先想「用戶會喜歡看什麼寫法」，不要寫成生硬的知識條列
- AI 趨勢類主題不要寫死平台功能名稱（後台改版快，寫死會過期）
- 案例先不寫（禾言案例庫還沒串進來）

直接產出「一則」完整定案的文案（不要給我 A/B 兩個版本選項，你自己選一個最好的直接寫），
你的輸出會直接被存成正式文案，所以只寫文案本身，不要加「以下是初稿」「版本A」這種標籤或說明。

如果判斷不出怎麼下筆，在回覆最開頭寫一行：NEED_HUMAN: <原因>，然後結束。"""

REVIEWER_PROMPT = """你是「小狐」，禾言數位行銷社群規劃團隊的審閱小幫手，
負責幫這篇「{type_label}」貼文把關。你是內容策略角度的審閱者，不是校對機。

{transcript_block}
檢查重點（依重要度排序）：
1. 規劃的方向是不是真的符合現階段大眾想了解的內容——不是「正確但無聊」，是不是有人會真的想點開看
2. 文案開頭夠不夠吸引人注意，會不會讓人滑過去就跳過
3. 整體話題會不會讓人有興趣讀完、有沒有記憶點

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


def write_to_heyen_social(cfg, tok, task):
    """審閱通過的定稿直接寫進「禾言社群規劃」網頁的資料，回傳新建 post 的 id。"""
    final_copy = last_message_text(cfg, tok, task['id'], 'maker')
    post = {
        'date': task.get('postDate') or '',
        'type': task.get('type') or '其他',
        'title': task.get('title') or '',
        'goal': task.get('goal') or '',
        'ig': final_copy,
        'fb': '',
        'done': False,
    }
    return db_post(cfg, tok, HY_ROOM, 'posts', post), final_copy


def process_task(cfg, tok, task):
    tid, stage = task['id'], task.get('stage')
    type_label = task.get('type') or '（沒指定類型）'
    messages = db_get(cfg, tok, ROOM, f'messages/{tid}') or {}
    transcript_block = build_transcript(messages)
    now_ms = int(time.time() * 1000)

    if stage == 'planning':
        role, allowed, timeout = 'planner', PLANNER_ALLOWED, TIMEOUT
        prompt = PLANNER_PROMPT.format(
            type_label=type_label, post_date=task.get('postDate') or '（沒填）',
            brief=task.get('brief', ''), transcript_block=transcript_block,
            title_note='' if task.get('title') else TITLE_NOTE)
    elif stage == 'making':
        role, allowed, timeout = 'maker', BASE_ALLOWED, TIMEOUT
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
        final_copy = last_message_text(cfg, tok, tid, 'maker')
        prompt = DESIGNER_PROMPT.format(
            title=task.get('title') or '', type_label=type_label, final_copy=final_copy)
    else:
        return False

    print(f"{time.strftime('%F %T')} 開始跑 {tid} / {role}")
    try:
        out = run_claude(prompt, allowed=allowed, timeout=timeout)
    except Exception as e:
        db_patch(cfg, tok, ROOM, f'tasks/{tid}', {
            'stage': 'waiting_human', 'waitingKind': 'error',
            'waitingReason': f'{ROLE_LABEL[role]}小幫手這輪跑失敗了：{str(e)[:200]}',
            'resumeStage': stage, 'updatedAt': now_ms})
        line_push(cfg, f'⚠️「{task.get("title","")}」的{ROLE_LABEL[role]}小幫手跑失敗了，麻煩到協作平台看一下')
        return True

    need_human = parse_need_human(out)
    if need_human:
        db_post(cfg, tok, ROOM, f'messages/{tid}', {
            'role': role, 'text': f'我不確定：{need_human}', 'createdAt': now_ms})
        db_patch(cfg, tok, ROOM, f'tasks/{tid}', {
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
        db_patch(cfg, tok, ROOM, f'tasks/{tid}', patch)
    elif role == 'maker':
        db_patch(cfg, tok, ROOM, f'tasks/{tid}', {'stage': 'reviewing', 'updatedAt': now_ms})
    elif role == 'reviewer':
        verdict = parse_verdict(out)
        if verdict == '通過':
            task_for_write = dict(task); task_for_write['id'] = tid
            post_id, _ = write_to_heyen_social(cfg, tok, task_for_write)
            db_patch(cfg, tok, ROOM, f'tasks/{tid}', {
                'stage': 'designing', 'hySocialId': post_id, 'updatedAt': now_ms})
            line_push(cfg, f'✅「{task.get("title","")}」文案定稿了，已經寫進禾言社群規劃，接下來小蝶要開始做圖卡')
        else:
            round_no = task.get('round', 0) + 1
            if verdict is None:
                db_patch(cfg, tok, ROOM, f'tasks/{tid}', {
                    'stage': 'waiting_human', 'waitingKind': 'needHuman',
                    'waitingReason': '審閱小幫手的回覆看不出通過還是要改，麻煩你看一下',
                    'resumeStage': 'reviewing', 'updatedAt': now_ms})
                line_push(cfg, f'🙋「{task.get("title","")}」的審閱結果我判斷不出來，到協作平台看一下')
            elif round_no > MAX_ROUNDS:
                db_patch(cfg, tok, ROOM, f'tasks/{tid}', {
                    'stage': 'waiting_human', 'waitingKind': 'maxRound',
                    'waitingReason': f'製作跟審閱已經來回改了 {MAX_ROUNDS} 輪，我先停下來，你要用目前這版定稿，還是再給個方向？',
                    'resumeStage': 'making', 'round': round_no, 'updatedAt': now_ms})
                line_push(cfg, f'🙋「{task.get("title","")}」來回改了 {MAX_ROUNDS} 輪還沒過，到協作平台看要不要直接定稿')
            else:
                db_patch(cfg, tok, ROOM, f'tasks/{tid}', {'stage': 'making', 'round': round_no, 'updatedAt': now_ms})
    elif role == 'designer':
        canva_url = parse_canva_url(out)
        if not canva_url:
            db_patch(cfg, tok, ROOM, f'tasks/{tid}', {
                'stage': 'waiting_human', 'waitingKind': 'needHuman',
                'waitingReason': '小蝶跑完了但沒抓到 Canva 連結，麻煩到協作平台看一下發生什麼事',
                'resumeStage': 'designing', 'updatedAt': now_ms})
            line_push(cfg, f'🙋「{task.get("title","")}」的圖卡沒拿到 Canva 連結，到協作平台看一下')
            return True
        db_patch(cfg, tok, ROOM, f'tasks/{tid}', {
            'stage': 'done', 'canvaUrl': canva_url, 'updatedAt': now_ms})
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


def main():
    if os.path.exists(LOCK) and time.time() - os.path.getmtime(LOCK) < TIMEOUT:
        return
    open(LOCK, 'w').write(str(os.getpid()))
    try:
        cfg = load_env()
        if not all(cfg.get(k) for k in ('WS_SA_KEY', 'WS_DB_URL')):
            print('設定不全，跳過'); return
        tok = ws_token(cfg['WS_SA_KEY'])

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

        if not jobs:
            return
        jobs.sort(key=lambda j: j[0])
        kind, payload = jobs[0][1], jobs[0][2]
        tok2 = ws_token(cfg['WS_SA_KEY'])
        if kind == 'task':
            process_task(cfg, tok2, payload)
        else:
            role, dm_msgs = payload
            process_dm(cfg, tok2, role, dm_msgs)
    finally:
        if os.path.exists(LOCK):
            os.unlink(LOCK)


if __name__ == '__main__':
    main()
