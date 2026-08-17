/* ==========================================================
   從一句話裡找出日期，並把日期那段字從標題中拿掉

   支援寫法：
     2026-08-20 / 2026/8/20
     8/20 / 8-20 / 8月20日 / 8月20號
     今天 今日 明天 明日 後天 大後天
     週五 禮拜五 星期五（本週，已經過了就算下週）
     下週一 下禮拜三 / 下下週五
     3天後 / 三天後
   台灣時間（UTC+8）為準——Worker 跑在 UTC，一定要自己加 8 小時，
   不然半夜寫的「今天」會變成前一天
   ========================================================== */

const TZ_OFFSET_MS = 8 * 60 * 60 * 1000;

export function todayTW(){
  return new Date(Date.now() + TZ_OFFSET_MS);
}
const ymd = d => `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCDate()).padStart(2, '0')}`;
const addDays = (d, n) => new Date(d.getTime() + n * 86400000);

const CN_NUM = { '一':1,'二':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10,'日':0,'天':0 };

/**
 * @returns {{ due: string|null, rest: string }}
 *   due  = YYYY-MM-DD；找不到就是 null（呼叫端自己決定預設）
 *   rest = 把日期那段字拿掉之後的標題
 */
export function parseDue(text){
  let s = String(text || '').trim();
  const base = todayTW();
  let due = null;

  const take = (re, fn) => {
    if (due) return;
    const m = s.match(re);
    if (!m) return;
    const d = fn(m);
    if (!d) return;
    due = d;
    s = (s.slice(0, m.index) + ' ' + s.slice(m.index + m[0].length)).replace(/\s+/g, ' ').trim();
  };

  // 2026-08-20 / 2026/8/20
  take(/(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})/, m => {
    const d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3]));
    return isNaN(d) ? null : ymd(d);
  });

  // 8月20日 / 8月20號
  take(/(\d{1,2})\s*月\s*(\d{1,2})\s*[日號]?/, m => rollForward(base, +m[1], +m[2]));

  // 8/20 或 8-20（前後不能再接數字，免得吃到 2026-08 這種）
  take(/(?<![\d/-])(\d{1,2})[/-](\d{1,2})(?![\d/-])/, m => rollForward(base, +m[1], +m[2]));

  // 今天 / 明天 / 後天 / 大後天
  take(/大後天/, () => ymd(addDays(base, 3)));
  take(/後天/,   () => ymd(addDays(base, 2)));
  take(/明[天日]/, () => ymd(addDays(base, 1)));
  take(/今[天日]/, () => ymd(base));

  // 下週三 / 下下禮拜五 / 這週五 / 週五
  take(/(下下|下|這|本)?\s*(?:週|周|禮拜|星期)\s*([一二三四五六日天])/, m => {
    const want = CN_NUM[m[2]];
    if (want === undefined) return null;
    const cur = base.getUTCDay();          // 0=日
    let diff = (want - cur + 7) % 7;
    const pre = m[1] || '';
    if (pre === '下') diff += 7;
    else if (pre === '下下') diff += 14;
    else if (diff === 0) diff = 7;         // 沒寫「這」而剛好是今天 → 當成下一個
    return ymd(addDays(base, diff));
  });

  // 3天後 / 三天後
  take(/([0-9一二三四五六七八九十]+)\s*天後/, m => {
    const n = /^\d+$/.test(m[1]) ? +m[1] : (CN_NUM[m[1]] || 0);
    return n > 0 ? ymd(addDays(base, n)) : null;
  });

  return { due, rest: s.replace(/^[\s,，.。、:：/-]+|[\s,，、:：/-]+$/g, '') };
}

/* 只寫月日的情況：一律用今年；如果算出來比今天早超過半年，
   多半是在講明年（例如 12 月底打 1/5），才把年份加一 */
function rollForward(base, mm, dd){
  if (mm < 1 || mm > 12 || dd < 1 || dd > 31) return null;
  let d = new Date(Date.UTC(base.getUTCFullYear(), mm - 1, dd));
  if (isNaN(d)) return null;
  if (d.getTime() < base.getTime() - 180 * 86400000){
    d = new Date(Date.UTC(base.getUTCFullYear() + 1, mm - 1, dd));
  }
  return ymd(d);
}

export const todayStr = () => ymd(todayTW());
