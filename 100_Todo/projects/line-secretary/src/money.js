/* ==========================================================
   記個人帳：把「120 午餐」這種一句話拆成一筆帳

   兩個入口共用這裡：
     1. iPhone 捷徑 → POST /money
     2. LINE 打「記帳 120 午餐」

   記帳跟排任務的差別在**時間方向**：任務講的是未來（8/20 要交），
   記帳講的是過去（昨天花的），所以日期不共用 date.js 的 parseDue，
   這裡自己寫一個往回算的。
   ========================================================== */

import { todayTW } from './date.js';

const DEF_OUT = ['固定支出','預存資金','飲食','交通','日用','娛樂','醫療','人情','治裝','學習','其他'];
const DEF_IN  = ['薪水','獎金','接案','其他'];
const FIXED_CAT = '固定支出';
const PRE_CAT   = '預存資金';      // 保險、稅金這種幾個月才繳一次的（2026-09-14 加）
const SUB_CATS  = [FIXED_CAT, PRE_CAT];
// Firebase 會把陣列存成物件，兩種都要吃
const toList = x => Array.isArray(x) ? x.filter(Boolean) : Object.values(x || {}).filter(Boolean);

/* 理財導航的分配（網頁 money/ 的「分配」頁，2026-09-14 改成四大項）。
   alloc/{YYYY-MM}：cat = [{k:分類, v:預算}]、fix = 固定支出細項與分期、pre = 預存項目、save = 儲蓄投資。
   變動支出 ＝ 固定支出、預存資金以外每一類的預算加總 */
function allocInfo(alloc){
  if (!alloc || typeof alloc !== 'object') return { used: 0, life: 0 };
  let life = 0, fix = 0, pre = 0;
  for (const r of toList(alloc.cat)){
    const v = Number(r.v)||0;
    if (r.k === FIXED_CAT) fix += v; else if (r.k === PRE_CAT) pre += v; else life += v;
  }
  for (const r of toList(alloc.fix)) fix += Number(r.v)||0;       // 固定支出的細項與分期
  for (const r of toList(alloc.pre)) pre += Number(r.v)||0;       // 預存項目（保險、稅金…）
  const save = toList(alloc.save).reduce((s,r) => s + (Number(r.v)||0), 0);
  return { used: life + fix + pre + save, life };
}

/* 分期在某個月還在繳嗎（第 1 期到第 n 期之間） */
function instLive(p, ym){
  if (!p || !/^\d{4}-\d{2}$/.test(p.start || '')) return false;
  const [sy, sm] = p.start.split('-').map(Number), [y, m] = ym.split('-').map(Number);
  const k = (y - sy) * 12 + (m - sm) + 1;
  return k >= 1 && k <= (Number(p.n) || 0);
}
/* 某一類可以套的細項名稱：固定支出＝細項＋那個月還在繳的分期；預存資金＝預存項目 */
function subsOf(settings, c, ym){
  if (c === FIXED_CAT)
    return [...toList(settings?.fixItems), ...toList(settings?.inst).filter(p => instLive(p, ym)).map(p => p.name)];
  if (c === PRE_CAT) return toList(settings?.preItems).map(p => p.name);
  return [];
}

/* LINE 什麼樣的句子算記帳（其他一律當任務）：
   1. 開頭是記帳字眼，或 $ ＋ 符號
   2. 數字開頭（2026-09-14 加，打「120 午餐」就好）。
      但數字後面緊接日期、時間寫法的照舊排任務：8/20、8-20、8月20日、20號、3天後、3點、15:00
   3. 昨天／前天／大前天開頭、後面接數字（「昨天 120 午餐」）
   風險：任務標題本身數字開頭又沒寫日期（「2 支影片剪輯」）會被記成花了 2 塊，前面加日期就不會 */
const MONEY_WORD = /^(記帳|記一筆|花了|支出|花費|消費|收入|入帳|[$＄+＋])/;
const MONEY_NUM  = /^\d[\d,]*(?:\.\d+)?(?![\d,.])(?!\s*(?:[/\-:：月號日點]|天後))/;
const MONEY_PAST = /^(大前天|前天|昨[天日])\s*\d/;
export function isMoneyText(text){
  const t = String(text || '').trim();
  return MONEY_WORD.test(t) || MONEY_NUM.test(t) || MONEY_PAST.test(t);
}

/* 付款方式 → 帳戶（2026-09-14 加）。
   先比她取的帳戶名稱（兩個字以上、最長的優先，例如「國泰」「街口」），再比講法：
     現金 → 現金帳戶
     刷卡、信用卡、Apple Pay 這種綁卡付的 → 信用卡帳戶
     街口、全支付這種儲值的 → 電子支付帳戶（沒開就不套）
     「行動支付」沒講是哪一個 → 有電子支付帳戶就記那裡，沒有就當綁卡算信用卡
   花錢不從存款銀行、股票出（跟網頁一樣）。沒講到就不填，網頁會算進預設帳戶 */
const PAY_WORDS = [
  { words:['現金'], types:['cash'] },
  { words:['信用卡','刷卡','apple pay','applepay','google pay','samsung pay','line pay','linepay'], types:['card'] },
  { words:['街口','全支付','悠遊付','icash pay','全盈支付','pi錢包','pi 錢包'], types:['epay'] },
  { words:['行動支付'], types:['epay','card'] },
];
const escRe = x => x.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
function pickAcc(s, settings, kind){
  const all  = toList(settings?.accs).filter(a => a && a.id);
  const ok   = a => a.type !== 'stock' && !(kind === 'out' && a.type === 'save');
  const accs = all.filter(ok);
  const low = s.toLowerCase();
  // 名字比對要看全部帳戶：講「玉山銀行」而它是存款銀行（花錢不能用）時，
  // 不能退回去套名字藏在裡面的「銀行」，直接當沒講
  let best = null;
  for (const a of all){
    const n = String(a.name || '').trim();
    if (n.length >= 2 && low.includes(n.toLowerCase()) && (!best || n.length > best.word.length))
      best = { id: a.id, name: n, word: n, acc: a };
  }
  if (best) return ok(best.acc) ? { id: best.id, name: best.name, word: best.word } : null;
  for (const p of PAY_WORDS){
    const w = p.words.find(x => low.includes(x));
    if (!w) continue;
    for (const t of p.types){
      const a = accs.find(x => x.type === t);
      if (a) return { id: a.id, name: a.name, word: w };
    }
  }
  return null;
}
/* 沒講付款方式時網頁算進哪個帳戶（跟網頁的 defAccId() 同一套） */
function defAccName(settings){
  const accs = toList(settings?.accs);
  const a = accs.find(x => x.id === settings?.defAcc) || accs.find(x => x.type === 'bank')
         || accs.find(x => x.type === 'cash') || accs[0];
  return a ? a.name : '';
}

/* 講法 → 分類。只有她的分類清單裡真的有那一類，才會套用；
   沒有就退回「其他」，不會自己長出新分類（跟 LINE 排任務同一個原則） */
const ALIAS = {
  // 每個月固定會跑的那些，跟「日用」分開——房租水電不是能決定要不要花的
  固定支出:['房租','房貸','管理費','水費','電費','瓦斯','電話費','手機費','網路費','第四台',
        '保險','保費','訂閱','會員費','netflix','spotify','icloud','youtube premium'],
  // 幾個月才繳一次的稅。「稅」一個字太短不放（會吃到退稅、稅後）
  預存資金:['牌照稅','燃料稅','房屋稅','地價稅','所得稅','稅金'],
  飲食:['早餐','午餐','晚餐','宵夜','下午茶','咖啡','手搖','飲料','便當','吃飯','聚餐','外送',
        '小七','全家','超商','麥當勞','星巴克','便利商店','餐','喝'],
  交通:['加油','停車','停車費','計程車','小黃','uber','捷運','公車','客運','高鐵','台鐵','火車',
        '機票','過路費','etag','車資','油錢','機車','保養廠','驗車'],
  日用:['衛生紙','洗衣精','沐浴乳','牙膏','洗髮精','清潔','日用品','家用','生活用品',
        '超市','全聯','家樂福','大潤發','好市多','costco','寶雅','五金'],
  娛樂:['電影','演唱會','展覽','唱歌','ktv','遊戲','旅遊','住宿','按摩','追劇','門票','桌遊'],
  醫療:['看醫生','醫生','診所','掛號','藥','藥局','牙醫','洗牙','健檢','中醫','復健','保健食品'],
  人情:['禮金','紅包','包禮','送禮','請客','孝親','捐款','伴手禮','喜酒','白包','聚餐分攤'],
  // 「包」「書」這種單字太短會誤判（紅包、臉書），一律用兩個字以上的詞
  治裝:['衣服','外套','褲子','裙子','鞋','包包','美髮','剪頭髮','染髮','燙頭髮','保養品',
        '化妝品','美甲','美睫','美容','指甲','飾品'],
  學習:['課程','上課','線上課','報名費','報名','證照','講座','教材','買書','書店','進修','工作坊'],
};

const CN_NUM = { 一:1, 二:2, 三:3, 四:4, 五:5, 六:6, 七:7, 八:8, 九:9, 十:10 };
const ymd = d => `${d.getUTCFullYear()}-${String(d.getUTCMonth()+1).padStart(2,'0')}-${String(d.getUTCDate()).padStart(2,'0')}`;
const addDays = (d,n) => new Date(d.getTime() + n*86400000);

/* 記帳的日期一律往回找：只寫「8/17」而今天是 8/19 → 就是今年 8/17；
   寫「12/30」而今天是 1/2 → 是去年的 12/30 */
function parseSpentDate(text){
  let s = String(text||'').trim();
  const base = todayTW();
  let date = null;

  const take = (re, fn) => {
    if (date) return;
    const m = s.match(re); if (!m) return;
    const d = fn(m); if (!d) return;
    date = d;
    s = (s.slice(0, m.index) + ' ' + s.slice(m.index + m[0].length)).replace(/\s+/g,' ').trim();
  };

  take(/(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})/, m => {
    const d = new Date(Date.UTC(+m[1], +m[2]-1, +m[3]));
    return isNaN(d) ? null : ymd(d);
  });
  take(/(\d{1,2})\s*月\s*(\d{1,2})\s*[日號]?/, m => rollBack(base, +m[1], +m[2]));
  take(/(?<![\d/-])(\d{1,2})[/-](\d{1,2})(?![\d/-])/, m => rollBack(base, +m[1], +m[2]));
  take(/大前天/,   () => ymd(addDays(base,-3)));
  take(/前天/,     () => ymd(addDays(base,-2)));
  take(/昨[天日]/, () => ymd(addDays(base,-1)));
  take(/今[天日]/, () => ymd(base));
  take(/([0-9一二三四五六七八九十]+)\s*天前/, m => {
    const n = /^\d+$/.test(m[1]) ? +m[1] : (CN_NUM[m[1]]||0);
    return n>0 ? ymd(addDays(base,-n)) : null;
  });

  return { date, rest: s.replace(/^[\s,，.。、:：-]+|[\s,，、:：-]+$/g,'') };
}
function rollBack(base, mm, dd){
  if (mm<1 || mm>12 || dd<1 || dd>31) return null;
  let d = new Date(Date.UTC(base.getUTCFullYear(), mm-1, dd));
  if (isNaN(d)) return null;
  if (d.getTime() > base.getTime() + 86400000){        // 算出來在未來 → 是去年的
    d = new Date(Date.UTC(base.getUTCFullYear()-1, mm-1, dd));
  }
  return ymd(d);
}

/**
 * 把一句話拆成一筆帳
 * @param text     「120 午餐」「昨天 交通 320 高鐵」「+6000 接案」
 * @param settings 雲端 settings（拿她自己設的分類清單，沒有就用預設）
 * @returns {{ok:boolean, err?:string, amt:number, cat:string, kind:string, note:string, date:string}}
 */
export function parseMoney(text, settings){
  let s = String(text||'').trim();
  s = s.replace(/^(記帳|記一筆|花了|支出|花費|消費)\s*/,'').replace(/^[$＄]\s*/,'');

  // 收入：開頭加號，或講了收入類的字眼
  let kind = 'out';
  if (/^[+＋]/.test(s)){ kind='in'; s = s.replace(/^[+＋]\s*/,''); }
  else if (/(收入|入帳|進帳|薪水|獎金|退款|退錢)/.test(s)){
    kind = 'in';
    // 「收入」「入帳」只是在講這是收入，不是備註內容，拿掉；
    // 「薪水」「獎金」留著，它們可能就是分類名
    s = s.replace(/(收入|入帳|進帳)/g,' ').replace(/\s+/g,' ').trim();
  }

  const cats = (kind==='in'
    ? (settings?.catsIn?.length ? settings.catsIn : DEF_IN)
    : (settings?.cats?.length   ? settings.cats   : DEF_OUT));

  const dt = parseSpentDate(s);
  s = dt.rest;

  // 金額：第一組數字（可以有千分位與小數）
  const mAmt = s.match(/(?<![\d.])(\d[\d,]*(?:\.\d+)?)(?![\d.])/);
  if (!mAmt) return { ok:false, err:'沒看到金額' };
  const amt = Math.round(Number(mAmt[1].replace(/,/g,'')));
  if (!(amt > 0)) return { ok:false, err:'金額要大於 0' };
  s = (s.slice(0, mAmt.index) + ' ' + s.slice(mAmt.index + mAmt[0].length))
      .replace(/\s+/g,' ').replace(/^(元|塊錢|塊)\s*/,'').replace(/[$＄]/g,'').trim();

  // 付款方式：講到的那個字從備註拿掉，免得備註變成「午餐 現金」
  const acc = pickAcc(s, settings, kind);
  if (acc) s = s.replace(new RegExp(escRe(acc.word), 'i'), ' ').replace(/\s+/g,' ').trim();

  // 分類：先看她有沒有直接講分類名，再查講法對照表
  let cat = '';
  for (const c of cats){                       // 直接講「飲食」→ 那個字從備註拿掉
    if (c && s.includes(c)){
      cat = c;
      s = s.replace(c,' ').replace(/\s+/g,' ').trim();
      break;
    }
  }
  // 講到她設的細項、分期、預存項目名稱（電話費、iPhone、保險…）就直接記到那一類，最長的優先
  const ym = (dt.date || ymd(todayTW())).slice(0, 7);
  const longestIn = names => names.reduce((b, n) => (n && s.includes(n) && n.length > b.length) ? n : b, '');
  let sub = '';
  if (!cat && kind === 'out'){
    for (const c of SUB_CATS){
      if (!cats.includes(c)) continue;
      const n = longestIn(subsOf(settings, c, ym));
      if (n.length > sub.length){ sub = n; cat = c; }
    }
  }
  if (!cat){
    const low = s.toLowerCase();
    let best = null;
    for (const [c, words] of Object.entries(ALIAS)){
      if (!cats.includes(c)) continue;         // 她把這類刪掉了就不套
      for (const w of words){
        if (low.includes(w) && (!best || w.length > best.w.length)) best = { c, w };
      }
    }
    if (best) cat = best.c;                    // 講法對照到的分類，原字留在備註裡
  }
  if (!cat) cat = cats.includes('其他') ? '其他' : cats[0];

  // 直接講分類名（或講法對照到）固定支出、預存資金時，也看看有沒有講到細項
  if (kind === 'out' && !sub && SUB_CATS.includes(cat)) sub = longestIn(subsOf(settings, cat, ym));

  return { ok:true, amt, cat, sub, kind, note: s.slice(0,40), date: dt.date || ymd(todayTW()),
           acc: acc ? acc.id : '', accName: acc ? acc.name : '' };
}

/** 寫進記帳的雲端節點，回一句確認。dry=true 只試算不寫入（測試用） */
export async function addMoney(text, env, db, dry = false){
  const settings = await db.get('settings').catch(() => null);
  const p = parseMoney(text, settings);
  if (!p.ok) return `⚠️ ${p.err}。試試「120 午餐」或「昨天 320 高鐵」`;

  if (!dry){
    const id = Date.now().toString(36) + Math.random().toString(36).slice(2,7);
    await db.put(`items/${id}`, {
      d: p.date, amt: p.amt, cat: p.cat, note: p.note,
      kind: p.kind, inv: '', ts: Date.now(), src: 'shortcut',
      ...(p.sub ? { sub: p.sub } : {}),
      ...(p.acc ? { acc: p.acc } : {})
    });
  }

  const money = n => 'NT$' + Math.round(n).toLocaleString('en-US');
  const md = `${Number(p.date.slice(5,7))}/${Number(p.date.slice(8,10))}`;
  const head = dry
    ? `🧪 試算（沒有真的存）：${money(p.amt)}　${p.cat}`
    : p.kind==='in'
      ? `✅ 收入 +${money(p.amt)}　${p.cat}`
      : `✅ 記好了 ${money(p.amt)}　${p.cat}${p.sub ? '・' + p.sub : ''}`;
  const lines = [head + (p.note ? `（${p.note}）` : ''), `日期：${md}`];
  const dn = defAccName(settings);
  if (p.accName) lines.push(`帳戶：${p.accName}`);
  else if (dn) lines.push(`帳戶：${dn}（沒寫付款方式，算預設）`);

  // 順便回報這個月還剩多少。算不出來就跳過，帳已經寫進去了不受影響
  // 口徑跟網頁一樣：有排分配 → 看變動支出（固定支出、預存資金另外算）；沒排 → 看舊的每月總預算
  try {
    const ym = p.date.slice(0,7);
    const alloc = allocInfo(await db.get(`alloc/${ym}`).catch(() => null));
    const useAlloc = alloc.used > 0;
    const budget = useAlloc ? alloc.life : (Number(settings?.budget) || 0);
    if (budget){
      const items = await db.get('items') || {};
      let out = 0;
      for (const it of Object.values(items)){
        if (!it || !String(it.d||'').startsWith(ym)) continue;
        if (it.kind === 'in' || it.kind === 'tr' || it.kind === 'adj') continue;   // 只算真的花掉的
        if (useAlloc && SUB_CATS.includes(it.cat)) continue;
        out += Number(it.amt)||0;
      }
      const word = useAlloc ? '變動支出' : '';
      const left = budget - out;
      lines.push(left >= 0 ? `這個月${word}還能花 ${money(left)}` : `這個月${word}已經超支 ${money(-left)}`);
    }
  } catch (e) { console.log('算餘額失敗', e && e.message); }

  return lines.join('\n');
}
