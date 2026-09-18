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
check('小蝶不會沒腳本就直接做六頁', '硬塞進固定六頁' in ah.DESIGNER_PROMPT, True)
check('小兔要輸出 CAPTION 標記', '<CAPTION>' in ah.MAKER_PROMPT, True)
check('小兔要輸出 CARD_PLAN 標記', '<CARD_PLAN>' in ah.MAKER_PROMPT, True)
check('小蝶收得到 card_plan 參數', '{card_plan}' in ah.DESIGNER_PROMPT, True)
check('小兔收得到 edit_samples 參數', '{edit_samples}' in ah.MAKER_PROMPT, True)

print('\n[13] prompt 的 format 參數對得起來（少一個會在正式跑的時候才爆）')
try:
    ah.MAKER_PROMPT.format(type_label='教學/新手', title='T', post_date='2026-09-22',
                           transcript_block='', revision_note='', edit_samples='')
    check('MAKER_PROMPT 可格式化', True, True)
except KeyError as e:
    check('MAKER_PROMPT 可格式化', 'KeyError %s' % e, True)
try:
    ah.DESIGNER_PROMPT.format(title='T', type_label='教學/新手', final_copy='文案',
                              card_plan='腳本')
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
    ah.REVIEWER_PROMPT.format(type_label='教學/新手', transcript_block='')
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

print('\n' + '=' * 46)
if FAILS:
    print('有 %d 項沒過：' % len(FAILS))
    for f in FAILS:
        print('  -', f)
    sys.exit(1)
print('全部通過')
