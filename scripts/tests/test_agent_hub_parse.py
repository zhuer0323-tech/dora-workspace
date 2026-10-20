#!/usr/bin/env python3
"""agent-hub 解析函式的離線測試（2026-09-18 四角色改版加的）。

**完全離線**：不碰 Firebase、LINE、Claude、Canva、git、launchd。
只驗證純函式，所以隨時可以跑：

    python3 scripts/tests/test_agent_hub_parse.py

為什麼要這支：小兔改成輸出 <CAPTION> ＋ <CARD_PLAN> 雙區塊之後，
解析錯一個字，規劃表的文案欄就會混進圖卡腳本，或是舊任務整個跑不動。
改 prompt 或改解析的人，改完跑一次這支。
"""
import os
import sys
import importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
RUNNER = os.path.join(HERE, '..', 'dora-agent-hub-runner.py')

spec = importlib.util.spec_from_file_location('ah_runner', RUNNER)
ah = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ah)   # 模組層級只有常數與函式定義，import 不會連線

FAILS = []


def check(name, got, want):
    if got == want:
        print('  ok   %s' % name)
    else:
        print('  FAIL %s\n       得到：%r\n       預期：%r' % (name, got, want))
        FAILS.append(name)


def check_true(name, cond, hint=''):
    check(name, bool(cond), True) if cond else (
        print('  FAIL %s %s' % (name, hint)), FAILS.append(name))
    if cond:
        pass


# ---------------------------------------------------------------- 樣本

def make_plan(pages):
    rows = ['頁數：%d' % pages, '']
    names = ['封面', '破題', '重點一', '重點二', '重點三', '收尾']
    for i in range(pages):
        rows += ['P%d｜%s' % (i + 1, names[i] if i < len(names) else '第%d頁' % (i + 1)),
                 '任務：這頁要做的事', '主標：標題文字', '視覺：數字放大', '']
    return '\n'.join(rows).strip()


NEW_FORMAT = """<CAPTION>
老闆，你的廣告預算花在哪裡？

這是正式貼文的內容。
#禾言數位行銷
</CAPTION>

<CARD_PLAN>
%s
</CARD_PLAN>""" % make_plan(5)

OLD_FORMAT = """老闆，你的廣告預算花在哪裡？

這是 2026-09-18 以前小兔的輸出，整份就是文案，沒有任何標記。
#禾言數位行銷"""


# ---------------------------------------------------------------- 測試

print('\n[1] 新格式：拆出 caption 與 card plan')
cap, plan = ah.parse_maker_output(NEW_FORMAT)
check('caption 取到第一句', cap.splitlines()[0], '老闆，你的廣告預算花在哪裡？')
check('caption 不含 CARD_PLAN 內容', '頁數：' in cap, False)
check('caption 不含標記', '<CAPTION>' in cap or '</CAPTION>' in cap, False)
check('card plan 取到頁數行', plan.splitlines()[0], '頁數：5')
check('card plan 不含 caption', '老闆' in plan, False)

print('\n[2] ig 欄不會混入 CARD_PLAN（寫入分流的核心）')
check('寫進 ig 的就是 caption', cap.strip().endswith('#禾言數位行銷'), True)
check('ig 不含 P1｜', 'P1｜' in cap, False)

print('\n[3] 舊格式向下相容（沒有標記）')
cap2, plan2 = ah.parse_maker_output(OLD_FORMAT)
check('整份當 caption', cap2, OLD_FORMAT.strip())
check('card plan 為空字串不是 None', plan2, '')

print('\n[4] 壞掉的輸入不可以爆掉')
for name, raw in [('空字串', ''), ('None', None), ('只有空白', '   \n  ')]:
    try:
        c, pl = ah.parse_maker_output(raw)
        check('%s 回傳字串' % name, isinstance(c, str) and isinstance(pl, str), True)
    except Exception as e:
        check('%s 不丟例外' % name, 'raised %s' % e, 'no exception')

print('\n[5] 漏掉收尾標記時盡量救回來')
cap3, plan3 = ah.parse_maker_output('<CAPTION>\n文案內容\n<CARD_PLAN>\n%s' % make_plan(4))
check('caption 只拿到文案', cap3.strip(), '文案內容')
check('card plan 仍解析得到', plan3.splitlines()[0], '頁數：4')

print('\n[6] 只有 CARD_PLAN 沒有 CAPTION 時，ig 不會整個空掉')
cap4, plan4 = ah.parse_maker_output('前面這段是文案\n\n<CARD_PLAN>\n%s\n</CARD_PLAN>' % make_plan(4))
check('caption 退回標記外的內容', cap4.strip(), '前面這段是文案')
check('card plan 正常', ah.card_plan_pages(plan4), 4)

print('\n[7] 頁數計算：4、5、6 頁都要能算')
for n in (4, 5, 6):
    check('%d 頁' % n, ah.card_plan_pages(make_plan(n)), n)
check('沒有腳本回 0', ah.card_plan_pages(''), 0)
check('None 回 0', ah.card_plan_pages(None), 0)
check('亂七八糟的內容回 0 不爆掉', ah.card_plan_pages('這裡沒有頁碼'), 0)

print('\n[8] 頁數超出 4～6 時程式不擋、只回數字（擋不擋交給小狐判斷）')
check('3 頁照樣算得出來', ah.card_plan_pages(make_plan(3)), 3)
check('7 頁照樣算得出來', ah.card_plan_pages(make_plan(7)), 7)
check('半形直線也認得', ah.card_plan_pages('P1|封面\nP2|收尾'), 2)
check('同一頁碼只算一次', ah.card_plan_pages('P1｜封面\nP1｜封面重寫'), 1)

print('\n[9] 既有解析函式維持相容（不能被這次改動弄壞）')
check('parse_title', ah.parse_title('標題候選：\n1. A\n2. B\n3. C\n建議標題：廣告預算怎麼分'),
      '廣告預算怎麼分')
check('parse_title 找不到回 None', ah.parse_title('沒有標題行'), None)
check('parse_verdict 通過', ah.parse_verdict('審閱意見...\n決定：通過'), '通過')
check('parse_verdict 需要修改', ah.parse_verdict('審閱意見...\n決定：需要修改'), '需要修改')
check('parse_verdict 看不懂回 None', ah.parse_verdict('決定：再說'), None)
check('parse_need_human', ah.parse_need_human('NEED_HUMAN: 方向不清楚'), '方向不清楚')
check('parse_canva_url', ah.parse_canva_url('做好了\nCanva連結：https://canva.com/d/abc'),
      'https://canva.com/d/abc')

print('\n[10] 小狐的五項評分格式在 prompt 裡（靜態檢查）')
for field in ['三秒理解：', '具體程度：', '禾言人味：', '新手易懂：', '圖卡可讀性：']:
    check('REVIEWER_PROMPT 有「%s」' % field, field in ah.REVIEWER_PROMPT, True)
check('決定行格式保留', '決定：通過' in ah.REVIEWER_PROMPT, True)
check('AI 味是正式條件不是次要', '次要檢查項' in ah.REVIEWER_PROMPT, False)

print('\n[11] 四個角色都指到共用規範檔')
SPEC = '禾言社群語氣與圖卡規範.md'
for role, prompt in [('小梟', ah.PLANNER_PROMPT), ('小兔', ah.MAKER_PROMPT),
                     ('小狐', ah.REVIEWER_PROMPT), ('小蝶', ah.DESIGNER_PROMPT)]:
    check('%s 讀規範檔' % role, SPEC in prompt, True)

print('\n[12] 舊衝突不可以殘留在提示詞裡')
allp = ah.PLANNER_PROMPT + ah.MAKER_PROMPT + ah.REVIEWER_PROMPT + ah.DESIGNER_PROMPT
# 只抓「要求六頁」的肯定句。合法的有兩種，要排除掉：
#   ① 「4～6 頁」的範圍寫法  ② 帶「不」的否定句（不固定六頁／不要硬塞進固定六頁）
# 用整行判斷，不要用固定字數的視窗——否定詞常常在句子更前面（「不要把 Caption 硬塞進固定六頁」）
_six = [ln.strip() for ln in allp.splitlines()
        if ('6 頁' in ln or '六頁' in ln) and '不' not in ln and '～6' not in ln]
check('沒有要求六頁的句子', _six, [])
check('沒有強制三點', '3-4 點' in allp or '三點式' in allp, False)
check('沒有「案例先不寫」', '案例先不寫' in allp, False)
check('沒有「結尾一定要有」禾言觀點', '結尾一定要有' in allp, False)
check('小蝶不會沒腳本就硬塞固定頁數', '硬塞進固定頁數' in ah.DESIGNER_PROMPT, True)
check('小兔要輸出 CAPTION 標記', '<CAPTION>' in ah.MAKER_PROMPT, True)
check('小兔要輸出 CARD_PLAN 標記', '<CARD_PLAN>' in ah.MAKER_PROMPT, True)
check('小蝶收得到 card_plan 參數', '{card_plan}' in ah.DESIGNER_PROMPT, True)
check('小兔收得到 edit_samples 參數', '{edit_samples}' in ah.MAKER_PROMPT, True)
check('小兔收得到 card_type_note 參數', '{card_type_note}' in ah.MAKER_PROMPT, True)
check('小蝶收得到 card_text 參數', '{card_text}' in ah.DESIGNER_PROMPT, True)
check('小狐知道圖卡形式', '{card_spec}' in ah.REVIEWER_PROMPT, True)
check('沒有殘留「可公開案例可引用」', '標示為對外可公開的案例庫' in allp, False)
for kw in ['客戶名稱', '成效數字']:
    check('小兔被告知不能寫%s' % kw, kw in ah.MAKER_PROMPT, True)
    check('小狐會擋%s' % kw, kw in ah.REVIEWER_PROMPT, True)
check('小兔知道字數上限', '250～350 字' in ah.MAKER_PROMPT, True)   # 2026-10-08 從 350～450 收緊
check('小狐會擋超過 350 字', '超過 350 字' in ah.REVIEWER_PROMPT, True)
# 2026-09-18 方案 A：要讀者去後台照做的就寫功能名稱，純觀念的才避開
check('小兔知道功能名稱的新規則', 'Advantage+' in ah.MAKER_PROMPT, True)
check('小狐知道功能名稱的新規則', 'Advantage+' in ah.REVIEWER_PROMPT, True)
check('舊的「一律不寫死」已移除', 'AI 趨勢類主題不要寫死' in allp, False)

print('\n[13] prompt 的 format 參數對得起來（少一個會在正式跑的時候才爆）')
try:
    ah.MAKER_PROMPT.format(type_label='教學/新手', title='T', post_date='2026-09-22', brief='方向',
                           existing_posts='', boss_list='清單', group_notes='',
                           transcript_block='', revision_note='', edit_samples='',
                           card_type_note=ah.card_type_note('carousel'))
    check('MAKER_PROMPT 可格式化', True, True)
except KeyError as e:
    check('MAKER_PROMPT 可格式化', 'KeyError %s' % e, True)
try:
    ah.DESIGNER_PROMPT.format(title='T', type_label='教學/新手', final_copy='文案',
                              card_plan='腳本', card_text='圖上的字', card_spec='輪播（4～6 頁）')
    check('DESIGNER_PROMPT 可格式化', True, True)
except KeyError as e:
    check('DESIGNER_PROMPT 可格式化', 'KeyError %s' % e, True)
try:
    ah.PLANNER_PROMPT.format(type_label='教學/新手', post_date='2026-09-22', brief='',
                             existing_posts='', group_notes='', transcript_block='',
                             title_note=ah.TITLE_NOTE)
    check('PLANNER_PROMPT 可格式化', True, True)
except KeyError as e:
    check('PLANNER_PROMPT 可格式化', 'KeyError %s' % e, True)
try:
    ah.REVIEWER_PROMPT.format(type_label='教學/新手', transcript_block='',
                              card_spec='輪播，Card Plan 應該是 4～6 頁')
    check('REVIEWER_PROMPT 可格式化', True, True)
except KeyError as e:
    check('REVIEWER_PROMPT 可格式化', 'KeyError %s' % e, True)

print('\n[14] 學習樣本 helper：雲端讀不到時要安靜回空字串，不能拖垮整輪')
class _Boom:
    def __getattr__(self, k):
        raise RuntimeError('不該被呼叫')
_orig_db_get = ah.db_get
try:
    def _fail(*a, **k):
        raise RuntimeError('模擬雲端讀取失敗')
    ah.db_get = _fail
    check('讀不到回空字串', ah.recent_human_edits({}, 'tok'), '')
finally:
    ah.db_get = _orig_db_get

print('\n[15] 學習樣本 helper：只挑真的被改過的，沒差異不輸出')
_orig_last = ah.last_message_text
try:
    tasks = {
        't1': {'hySocialId': 'h1', 'title': '她改過的那篇', 'updatedAt': 300},
        't2': {'hySocialId': 'h2', 'title': '她沒動的那篇', 'updatedAt': 200},
        't3': {'title': '沒串規劃表的', 'updatedAt': 100},
    }
    posts = {
        'h1': {'ig': '她改成這樣'},
        'h2': {'ig': '小兔原本的樣子'},
    }
    maker_out = {
        't1': '<CAPTION>\n小兔原本寫這樣\n</CAPTION>',
        't2': '<CAPTION>\n小兔原本的樣子\n</CAPTION>',
    }
    ah.db_get = lambda cfg, tok, room, path: (
        tasks if path == 'tasks' else posts if path == 'posts' else {})
    ah.last_message_text = lambda cfg, tok, tid, role: maker_out.get(tid, '')

    got = ah.recent_human_edits({}, 'tok')
    check('有帶到被改過的那篇', '她改過的那篇' in got, True)
    check('沒帶沒動過的那篇', '她沒動的那篇' in got, False)
    check('有小兔版', '小兔原本寫這樣' in got, True)
    check('有她的版本', '她改成這樣' in got, True)

    got_skip = ah.recent_human_edits({}, 'tok', skip_tid='t1')
    check('skip_tid 會排除自己', got_skip, '')

    # 全部都沒差異時要回空字串，不要吐出空白段落
    posts['h1'] = {'ig': '小兔原本寫這樣'}
    check('沒有任何差異就回空字串', ah.recent_human_edits({}, 'tok'), '')
finally:
    ah.db_get = _orig_db_get
    ah.last_message_text = _orig_last

print('\n[16] 上限常數有設，prompt 不會無限膨脹')
check('最多 3 組', ah.EDIT_SAMPLE_MAX, 3)
check('掃描筆數有上限', ah.EDIT_SAMPLE_SCAN <= 20, True)
check('單篇字數有上限', ah.EDIT_SAMPLE_CHARS <= 1000, True)
check('MAX_ROUNDS 已提高到 3', ah.MAX_ROUNDS, 3)

print('\n[17] 圖卡形式：節慶預設單張，其他預設輪播，她點選的優先')
check('節慶預設單張', ah.card_type_of({'type': '日常/節慶'}), 'single')
check('觀點預設輪播', ah.card_type_of({'type': '觀點/趨勢'}), 'carousel')
check('教學預設輪播', ah.card_type_of({'type': '教學/新手'}), 'carousel')
check('沒有類型也不會爆', ah.card_type_of({}), 'carousel')
check('她點選輪播就蓋過節慶預設',
      ah.card_type_of({'type': '日常/節慶', 'cardType': 'carousel'}), 'carousel')
check('她點選單張就蓋過預設',
      ah.card_type_of({'type': '觀點/趨勢', 'cardType': 'single'}), 'single')
check('亂值退回類型預設', ah.card_type_of({'type': '日常/節慶', 'cardType': 'xx'}), 'single')
check('規劃表沒設時看任務', ah.card_type_of({}, {'cardType': 'single'}), 'single')
check('單張的頁數說明', '1 頁' in ah.expected_pages('single'), True)
check('輪播的頁數說明', '5～6 頁' in ah.expected_pages('carousel'), True)   # 2026-10-09 固定版型最少 5 頁
check('單張的提示詞說不做輪播', '不做輪播' in ah.card_type_note('single'), True)
check('輪播的提示詞說 5～6 頁', '5～6 頁' in ah.card_type_note('carousel'), True)

print('\n[18] 圖卡文字：只留要印上去的字，去掉任務與視覺')
plan_sample = """頁數：2

P1｜封面
任務：讓老闆三秒內知道這篇在講什麼
主標：後台叫你開自動化？
補充：（不放補充句，用視覺證據）
視覺：主標下方畫三顆綠色開關、一顆灰色開關

P2｜為什麼現在要管
任務：交代預設值變了
主標：現在預設先幫你勾好
內容：
以前 ➤ 先問手動還是自動
現在 ➤ 先勾好，要關自己點
視覺：左右對比卡，左卡畫二選一選單
底部句：每個新廣告都要面對這一步"""
txt = ah.card_plan_text(plan_sample)
check('拿掉「任務」行', '任務：' in txt, False)
check('拿掉「視覺」行', '視覺：' in txt, False)
check('拿掉「頁數」行', txt.startswith('頁數'), False)
check('留下主標', '主標：後台叫你開自動化？' in txt, True)
check('留下內容的續行', '以前 ➤ 先問手動還是自動' in txt, True)
check('留下底部句', '底部句：每個新廣告都要面對這一步' in txt, True)
check('頁標題的全形直線換成空格', 'P1 封面' in txt, True)
check('視覺的續行也要拿掉', '左右對比卡' in txt, False)
check('空字串不爆', ah.card_plan_text(''), '')
check('None 不爆', ah.card_plan_text(None), '')

print('\n[20] 老闆關心清單（2026-10-20）：每月題目多一欄「為什麼想看」，舊格式照樣解析')
out_new = """主軸=錢有沒有白花
1. 日期=2026-11-03｜類型=教學/新手｜標題=詢問變多成交變少，先別怪廣告｜方向=分清楚是廣告還是業務的事｜為什麼想看=對應 A2，老闆最常怪錯對象
2. 日期=2026-11-10｜類型=觀點/趨勢｜標題=T2｜方向=舊格式沒有理由"""
its = ah.parse_proposal_items(out_new)
check('新舊格式都解析到', len(its), 2)
check('解析出為什麼想看', its[0].get('why'), '對應 A2，老闆最常怪錯對象')
check('方向不會吃到為什麼想看', its[0]['angle'], '分清楚是廣告還是業務的事')
check('舊格式沒有 why 欄', 'why' in its[1], False)
check('主軸照舊', ah.parse_proposal_theme(out_new), '錢有沒有白花')
check('PROPOSAL_PROMPT 有清單參數', '{boss_list}' in ah.PROPOSAL_PROMPT, True)
check('PROPOSAL_PROMPT 要求為什麼想看', '為什麼想看=' in ah.PROPOSAL_PROMPT, True)
try:
    ah.PROPOSAL_PROMPT.format(target_month='2026 年 11 月', existing_posts='', boss_list='清單', group_notes='')
    check('PROPOSAL_PROMPT 可格式化', True, True)
except KeyError as e:
    check('PROPOSAL_PROMPT 可格式化', 'KeyError %s' % e, True)
try:
    ah.LIST_REFRESH_PROMPT.format(today='2026-11-15', old_list='舊', meeting_notes='會議')
    check('LIST_REFRESH_PROMPT 可格式化', True, True)
except KeyError as e:
    check('LIST_REFRESH_PROMPT 可格式化', 'KeyError %s' % e, True)
check('小兔有清單參數', '{boss_list}' in ah.MAKER_PROMPT, True)
check('小兔主張卡多一欄', '回答老闆的哪個問題：' in ah.MAKER_PROMPT, True)

sample_list = """# 清單
### ⭐ A2. 「詢問變多成交變少」
- 朱兒點過頭
### A3. 「要不要關廣告」
### B9. 「我補的」（朱兒補充）
---

## 朱兒拿掉的（不要再排）

- A1. 「轉換數字騙人」
- D1. 「LINE 漲價」

---

## 來源
- [某網站](https://example.com)
"""
locked = ah._list_locked_lines(sample_list)
check('鎖住 ⭐ 條目', '### ⭐ A2. 「詢問變多成交變少」' in locked, True)
check('鎖住朱兒補充', '### B9. 「我補的」（朱兒補充）' in locked, True)
check('鎖住拿掉的每一行', '- D1. 「LINE 漲價」' in locked, True)
check('一般條目不鎖', '### A3. 「要不要關廣告」' in locked, False)
check('來源連結不算拿掉的', '- [某網站](https://example.com)' in locked, False)
check('讀不到清單不爆', isinstance(ah.boss_list_block(), str), True)

print('\n' + '=' * 46)
if FAILS:
    print('有 %d 項沒過：' % len(FAILS))
    for f in FAILS:
        print('  -', f)
    sys.exit(1)
print('全部通過')
