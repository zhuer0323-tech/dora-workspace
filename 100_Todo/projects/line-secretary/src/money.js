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

const DEF_OUT = ['飲食','交通','日用','娛樂','醫療','人情','其他'];
const DEF_IN  = ['薪水','獎金','接案','其他'];

/* 講法 → 分類。只有她的分類清單裡真的有那一類，才會套用；
   沒有就退回「其他」，不會自己長出新分類（跟 LINE 排任務同一個原則） */
const ALIAS = {
  飲食:['早餐','午餐','晚餐','宵夜','下午茶','咖啡','手搖','飲料','便當','吃飯','聚餐','外送',
        '小七','全家','超商','麥當勞','星巴克','便利商店','餐','喝'],
  交通:['加油','停車','停車費','計程車','小黃','uber','捷運','公車','客運','高鐵','台鐵','火車',
        '機票','過路費','etag','車資','油錢'],
  日用:['房租','水費','電費','瓦斯','電話費','網路費','衛生紙','洗衣精','沐浴乳','牙膏','日用品',
        '超市','全聯','家樂福','大潤發','好市多','costco','家用','生活用品'],
  娛樂:['電影','演唱會','展覽','唱歌','ktv','遊戲','訂閱','netflix','spotify','旅遊','住宿','按摩',
        '書','追劇'],
  醫療:['看醫生','醫生','診所','掛號','藥','藥局','牙醫','洗牙','健檢','中醫','復健','保健食品'],
  人情:['禮金','紅包','包禮','送禮','請客','孝親','捐款','伴手禮','喜酒','白包'],
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

  // 分類：先看她有沒有直接講分類名，再查講法對照表
  let cat = '';
  for (const c of cats){                       // 直接講「飲食」→ 那個字從備註拿掉
    if (c && s.includes(c)){
      cat = c;
      s = s.replace(c,' ').replace(/\s+/g,' ').trim();
      break;
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

  return { ok:true, amt, cat, kind, note: s.slice(0,40), date: dt.date || ymd(todayTW()) };
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
      kind: p.kind, inv: '', ts: Date.now(), src: 'shortcut'
    });
  }

  const money = n => 'NT$' + Math.round(n).toLocaleString('en-US');
  const md = `${Number(p.date.slice(5,7))}/${Number(p.date.slice(8,10))}`;
  const head = dry
    ? `🧪 試算（沒有真的存）：${money(p.amt)}　${p.cat}`
    : p.kind==='in'
      ? `✅ 收入 +${money(p.amt)}　${p.cat}`
      : `✅ 記好了 ${money(p.amt)}　${p.cat}`;
  const lines = [head + (p.note ? `（${p.note}）` : ''), `日期：${md}`];

  // 順便回報這個月還剩多少。算不出來就跳過，帳已經寫進去了不受影響
  try {
    const budget = Number(settings?.budget) || 0;
    if (budget){
      const items = await db.get('items') || {};
      const ym = p.date.slice(0,7);
      let out = 0;
      for (const it of Object.values(items)){
        if (it && it.kind !== 'in' && String(it.d||'').startsWith(ym)) out += Number(it.amt)||0;
      }
      const left = budget - out;
      lines.push(left >= 0 ? `這個月還能花 ${money(left)}` : `這個月已經超支 ${money(-left)}`);
    }
  } catch (e) { console.log('算餘額失敗', e && e.message); }

  return lines.join('\n');
}
