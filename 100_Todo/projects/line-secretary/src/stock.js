/* ==========================================================
   台股股價與定期定額（理財導航第二批，2026-09-15）

   兩個入口：
     1. GET /stock?code=XXXX → 記帳網頁輸入代號時，查名稱與最近價格（只回公開股價，不碰帳務）
     2. 每天定時執行（wrangler.toml 的 crons）→ 更新持股價格、定期定額自動記帳

   價格從哪裡來（2026-09-15 從 Cloudflare 主機實測過）：
     - 證交所「即時行情」mis.twse.com.tw：上市、上櫃都有，收盤後就是當天收盤價，但只有當天
     - 證交所「個股日成交」www.twse.com.tw：只有上市，可以查某個月的每一天
     - ⚠️ 櫃買中心整個網站擋 Cloudflare（一律 302 導去 /errors，開放資料也一樣），
       所以上櫃查不到過去的價格：改成每天把收盤價存一份到 priceHist，補記時用存下來的
   ========================================================== */

import { makeDb } from './firebase.js';
import { todayStr } from './date.js';

const UA = {
  'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126 Safari/537.36',
  'Accept': 'application/json'
};
const CODE_RE = /^[0-9A-Z]{4,6}$/;
const ALLOW_ORIGIN = 'https://zhuer0323-tech.github.io';
const MAX_CODES = 15;       // 免費方案一次執行最多 50 個對外連線，代號太多會超過
const MAX_BACKFILL = 3;     // 每組定期定額一次最多補 3 期，開始月份設錯也不會一口氣長出一堆帳
const CLOSED = '13:30';     // 成交時間到 13:30 才算收盤價

// Firebase 會把陣列存成物件，兩種都要吃
const toList = x => Array.isArray(x) ? x.filter(Boolean) : Object.values(x || {}).filter(Boolean);
const num = s => { const n = Number(String(s ?? '').replace(/,/g, '')); return Number.isFinite(n) && n > 0 ? n : 0; };
const pad2 = n => String(n).padStart(2, '0');
const lastDay = ym => new Date(Date.UTC(+ym.slice(0, 4), +ym.slice(5, 7), 0)).getUTCDate();
const ymShift = (ym, n) => {
  const d = new Date(Date.UTC(+ym.slice(0, 4), +ym.slice(5, 7) - 1 + n, 1));
  return `${d.getUTCFullYear()}-${pad2(d.getUTCMonth() + 1)}`;
};
/* 定期定額在某個月的預定日：設 31 號遇到小月就用那個月最後一天 */
const targetDay = (p, ym) => `${ym}-${pad2(Math.min(Math.max(1, Math.round(Number(p.day) || 1)), lastDay(ym)))}`;

/* 即時行情：一次問多檔。不知道是上市還是上櫃，所以兩種都問，有回名字的那個就是 */
export async function misQuotes(codes, fetchImpl = fetch){
  const out = {};
  const list = [...new Set(codes)].filter(c => CODE_RE.test(c || ''));
  for (let i = 0; i < list.length; i += 10){
    const chunk = list.slice(i, i + 10);
    const ex = chunk.flatMap(c => [`tse_${c}.tw`, `otc_${c}.tw`]).join('|');
    const r = await fetchImpl(`https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=${ex}&json=1&delay=0`, { headers: UA });
    if (!r.ok) throw new Error(`即時行情 ${r.status}`);
    const data = JSON.parse((await r.text()).trim());
    for (const m of data.msgArray || []){
      if (!m.c || !chunk.includes(m.c)) continue;
      // z＝最近成交價；還沒成交時是 "-"，退回 pz（上一筆）或 y（昨收）
      const close = num(m.z) || num(m.pz) || num(m.y);
      if (!close || !/^\d{8}$/.test(m.d || '')) continue;
      out[m.c] = {
        name: String(m.n || '').trim(), mkt: m.ex === 'otc' ? 'otc' : 'tse', close,
        d: `${m.d.slice(0, 4)}-${m.d.slice(4, 6)}-${m.d.slice(6, 8)}`, t: String(m.t || '')
      };
    }
  }
  return out;
}

/* 證交所個股日成交：某個月每一天的收盤價（只有上市；查不到回空陣列） */
export async function twseMonth(code, ym, fetchImpl = fetch){
  const r = await fetchImpl(
    `https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY?date=${ym.replace('-', '')}01&stockNo=${code}&response=json`,
    { headers: UA });
  if (!r.ok) throw new Error(`證交所 ${r.status}`);
  const j = await r.json();
  if (j.stat !== 'OK') return [];
  return (j.data || []).map(row => {
    const m = String(row[0] || '').match(/^(\d{2,3})\/(\d{2})\/(\d{2})$/);   // 民國 115/09/01
    return m ? { d: `${Number(m[1]) + 1911}-${m[2]}-${m[3]}`, close: num(row[6]) } : null;
  }).filter(x => x && x.close);
}

const json = (body, status, headers) => new Response(JSON.stringify(body), { status, headers });

export async function handleStock(request, env, url, fetchImpl = fetch){
  const headers = {
    'Access-Control-Allow-Origin': ALLOW_ORIGIN, 'Vary': 'Origin',
    'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store'
  };
  if (request.method === 'OPTIONS')
    return new Response(null, { status: 204, headers: { ...headers, 'Access-Control-Allow-Methods': 'GET' } });
  if (request.method !== 'GET') return json({ error: '只收 GET' }, 405, headers);
  const code = String(url.searchParams.get('code') || '').trim().toUpperCase();
  if (!CODE_RE.test(code)) return json({ error: '代號格式不對' }, 400, headers);
  try {
    const q = (await misQuotes([code], fetchImpl))[code];
    return q ? json({ code, ...q }, 200, headers) : json({ error: '查不到這個代號' }, 404, headers);
  } catch (e) {
    return json({ error: '股價來源暫時連不上' }, 502, headers);
  }
}

/* 每天定時執行：先更新價格，再補記定期定額。
   opt 給測試用：fetch、db、today、sleep 都可以換掉 */
export async function runStockJob(env, opt = {}){
  const f = opt.fetch || fetch;
  const db = opt.db || makeDb(env, env.MN_ROOM);
  const today = opt.today || todayStr();
  const ym = today.slice(0, 7);
  const sleep = opt.sleep || (ms => new Promise(r => setTimeout(r, ms)));
  const log = { today, prices: 0, hist: 0, dca: [], wait: [], errors: [] };

  const [settings, lots, prices, skip] = await Promise.all(['settings', 'lots', 'prices', 'dcaSkip'].map(k => db.get(k)));
  const plans = toList(settings?.dca).filter(p => p.id && CODE_RE.test(p.code || '') && p.acc && p.from &&
    Number(p.amt) > 0 && /^\d{4}-\d{2}$/.test(p.start || '') && !p.off);
  const codes = [...new Set([...Object.values(lots || {}).filter(Boolean).map(l => l.code), ...plans.map(p => p.code)])]
    .filter(c => CODE_RE.test(c || '')).slice(0, MAX_CODES);
  const px = { ...(prices || {}) };

  // 1. 價格：今天的收盤價已經存過的就不問
  const need = codes.filter(c => !(px[c]?.d === today && String(px[c]?.t || '') >= CLOSED));
  if (need.length){
    try {
      const q = await misQuotes(need, f);
      for (const [c, v] of Object.entries(q)){
        const old = px[c];
        const wasFinal = old && old.d === v.d && String(old.t || '') >= CLOSED;
        if (!old || old.d !== v.d || old.close !== v.close || old.t !== v.t || old.name !== v.name){
          px[c] = { ...v, ts: Date.now() };
          await db.put(`prices/${c}`, px[c]); log.prices++;
        }
        // 收盤價每天存一份，上櫃補記定期定額時用（櫃買中心查不到過去的價格）
        if (v.t >= CLOSED && !wasFinal){ await db.put(`priceHist/${c}/${v.d}`, v.close); log.hist++; }
      }
    } catch (e) { log.errors.push(`價格：${e.message}`); }
  }

  // 2. 定期定額：成交日到了、還沒記、也沒被她刪掉的那幾期
  const monthCache = {}, histCache = {};
  let twseCalls = 0;
  const twse = async (code, m) => {
    const k = `${code}|${m}`;
    if (!(k in monthCache)){
      if (twseCalls++) await sleep(1200);          // 證交所有頻率限制（大約每 5 秒 3 次）
      monthCache[k] = await twseMonth(code, m, f);
    }
    return monthCache[k];
  };
  const histOf = async code => {
    if (!(code in histCache)) histCache[code] = (await db.get(`priceHist/${code}`)) || {};
    return histCache[code];
  };

  for (const p of plans){
    let made = 0;
    for (let m = p.start; m <= ym && made < MAX_BACKFILL; m = ymShift(m, 1)){
      const id = `dca_${p.id}_${m}`;
      if (skip?.[id] || lots?.[id]) continue;
      const target = targetDay(p, m);
      if (target > today) break;

      // 成交日＝預定日當天或之後第一個有開盤的日子；拿到那天的收盤價才記
      let hit = null;
      try {
        if (px[p.code]?.mkt !== 'otc'){
          try {
            const days = [...await twse(p.code, m), ...(m < ym ? await twse(p.code, ymShift(m, 1)) : [])];
            hit = days.find(x => x.d >= target && x.d <= today) || null;
          } catch (e) { log.errors.push(`${id} 證交所：${e.message}`); }
        }
        if (!hit){
          const h = await histOf(p.code);
          const d = Object.keys(h).filter(x => x >= target && x <= today).sort()[0];
          if (d && num(h[d])) hit = { d, close: num(h[d]) };
        }
      } catch (e) { log.errors.push(`${id}：${e.message}`); break; }
      if (!hit){ log.wait.push(id); break; }

      const amt = Math.round(Number(p.amt));
      const name = px[p.code]?.name || p.code;
      const ts = Date.now();
      // 轉帳先寫、買進紀錄後寫：買進紀錄是「這期記好了」的記號，中途失敗下次會整期重寫
      await db.put(`items/${id}`, { kind: 'tr', from: p.from, to: p.acc, amt, d: hit.d,
        note: `定期定額・${name}`, dca: p.id, ts });
      await db.put(`lots/${id}`, { acc: p.acc, code: p.code, sh: Math.floor(amt / hit.close), amt, d: hit.d,
        px: hit.close, src: 'dca', est: true, plan: p.id, ts });
      made++; log.dca.push(id);
    }
  }
  return log;
}
