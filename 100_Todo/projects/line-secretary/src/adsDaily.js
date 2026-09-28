/* ==========================================================
   廣告日報（雲端版）：每天 9:00（昨日）與 17:00（今日截至目前）推一張卡片到 LINE

   2026-09-28 從 Mac 的 ~/Library/Scripts/dora-ads-daily.py 搬過來。
   原因：她 9:00／17:00 的 Mac 都是蓋著的，只有 Power Nap 那種 2~8 秒的短暫喚醒，
   抓數字跑不完，9/25–9/28 早晚都漏送。搬到雲端就跟 Mac 睡不睡無關。

   規則跟 Mac 版一模一樣（列誰、怎麼算、版面），改這裡之前先看 Mac 版的註解。
   差別只有：
     - 沒有 Claude 備援（雲端跑不了 claude -p），抓不到就推失敗通知
     - 「已經送過」的記號寫在工作台雲端 adsDaily/{日期}-{時段}，不是 Mac 上的檔案
     - 讀不到工作台算失敗、等下一次重試（Mac 版會當成沒客戶、安靜地標成已送出）

   排程（wrangler.toml）：台灣 9:00／9:15／9:30、17:00／17:15／17:30，
   前兩次抓不到就安靜等下一次，:30 那次還抓不到才推失敗通知
   ========================================================== */

import { makeDb } from './firebase.js';

const NTD = 'NT$';
const GRAPH = 'https://graph.facebook.com/v25.0';
const LAST_TRY_MINUTE = 30;

// 17:00 那則要不要「沒異常就安靜」，跟 Mac 版一樣預設關著（2026-08-25 她要求每天照推）
const EVENING_QUIET = false;

// 卡片顏色。進度條三色跑過 dataviz 的 validate_palette.js（最差 CVD ΔE 15.1）
const INK = '#2E2740', INK_SOFT = '#6E6784', CARD_BG = '#F7F5FB';
const STATE = { ok: ['#7C5CBF', '#E5DDF5'], slow: ['#1FA08A', '#D3EFE8'], fast: ['#C0453B', '#F5DEDB'] };
const STATE_LABEL = { fast: '⚡ 燒太快', slow: '🐢 偏慢', ok: '✓ 正常' };
const BRAND_EMOJI = { '李老闆': '🛒', '漁三': '🎣', '優逸': '💬', 'TOTO': '🏆' };

const KIND_BY_INDICATOR = [
  ['messaging',  ['messaging_conversation', 'messaging_conversation_started']],
  ['leads',      ['lead']],
  ['sales',      ['purchase', 'omni_purchase']],
  ['traffic',    ['link_click', 'landing_page_view']],
  ['engagement', ['post_engagement', 'page_engagement']],
];
const KIND_NAME  = { sales: '銷售', messaging: '訊息', leads: '名單', traffic: '流量', engagement: '互動', other: '成果' };
const KIND_EMOJI = { sales: '🛒', messaging: '💬', leads: '📋', traffic: '🔗', engagement: '📣', other: '📌' };
const DAYS_ZH = ['週日', '週一', '週二', '週三', '週四', '週五', '週六'];

// ---------- 日期（一律台灣時間）----------
const ymd = d => `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCDate()).padStart(2, '0')}`;
const isYmd = s => /^\d{4}-\d{2}-\d{2}$/.test(String(s || ''));
const dateOf = s => new Date(`${s}T00:00:00Z`);
const dayDiff = (a, b) => Math.round((dateOf(a) - dateOf(b)) / 86400000);
const addDays = (s, n) => ymd(new Date(dateOf(s).getTime() + n * 86400000));

// ---------- 數字格式（照 Python 的 f"{v:,.0f}"）----------
const fmt = (v, dec = 0) => v.toLocaleString('en-US', { minimumFractionDigits: dec, maximumFractionDigits: dec });
function compact(v, money = false, dec = 0){
  const pre = money ? NTD : '';
  if (v >= 10000) return `${pre}${(v / 10000).toFixed(1)}萬`;
  return `${pre}${fmt(v, dec)}`;
}

// ---------- 工作台 ----------
function runsOf(c){
  const rs = c.runs;
  const list = Array.isArray(rs) ? rs : (rs && typeof rs === 'object' ? Object.values(rs) : []);
  return list.filter(r => r && typeof r === 'object' && (r.start || r.end));
}
const currentRun = (c, today) => runsOf(c).find(r =>
  (!r.start || r.start <= today) && (!r.end || r.end >= today)) || null;

function parseBudget(s){
  const m = String(s || '').match(/(\d[\d,]*)/);
  return m ? parseFloat(m[1].replace(/,/g, '')) : 0;
}
const cidOf = c => c.id || c.short || c.name;

// ---------- 把活動歸給客戶 ----------
function buildMatcher(clients){
  const keys = [], skipped = [];
  for (const c of clients){
    const pool = [c.name, c.short, ...String(c.kw || '').replace(/，/g, ',').split(',')];
    for (let k of pool){
      k = String(k || '').trim();
      if (!k) continue;
      // 單字關鍵字（「沐」「Y」「H」）會誤中別家，不拿來比對
      if ([...k].length < 2) skipped.push(k);
      else keys.push([k.toLowerCase(), c]);
    }
  }
  return { keys, skipped };
}
function matchClient(name, keys){
  const low = String(name || '').toLowerCase();
  let best = null, blen = 0;
  for (const [k, c] of keys){
    if (low.includes(k) && k.length > blen){ best = c; blen = k.length; }
  }
  return best;
}
function kindOf(indicator){
  const ind = String(indicator || '').toLowerCase();
  for (const [kind, needles] of KIND_BY_INDICATOR){
    if (needles.some(n => ind.includes(n))) return kind;
  }
  return 'other';
}

// ---------- 抓數字（Graph API，系統使用者 token）----------
async function graphGet(url){
  const r = await fetch(url, { headers: { 'User-Agent': 'DoraMonitor/1.0' }, signal: AbortSignal.timeout(25000) });
  const data = await r.json().catch(() => ({}));
  if (!r.ok || data.error) throw new Error(`Graph ${r.status} ${data.error?.message || ''}`.trim());
  return data;
}
async function graphAll(url, maxPages = 6){
  const out = [];
  let next = url;
  for (let i = 0; i < maxPages && next; i++){
    const data = await graphGet(next);
    out.push(...(data.data || []));
    next = data.paging?.next || null;
  }
  return out;
}
function actVal(actions, ...types){
  for (const a of actions || []){
    if (types.includes(a.action_type)) return parseFloat(a.value) || 0;
  }
  return 0;
}

// 當日要完整欄位；走期累計只要「哪個活動、哪天、花多少」，所以拆成兩次查，
// 回傳的資料小很多（actions 陣列最佔空間），雲端的運算時間才夠用
async function fetchViaGraph(token, since, reportDate){
  const q = p => new URLSearchParams({ access_token: token, ...p }).toString();
  const accounts = (await graphGet(`${GRAPH}/me/adaccounts?${q({ fields: 'account_id', limit: '100' })}`))
    .data.map(a => a.account_id);
  const dayRows = [], rangeRows = [];
  const perAccount = await Promise.all(accounts.map(async acc => {
    const [day, range] = await Promise.all([
      graphAll(`${GRAPH}/act_${acc}/insights?${q({
        level: 'campaign',
        fields: 'campaign_name,objective,spend,actions,action_values,reach,impressions',
        time_range: JSON.stringify({ since: reportDate, until: reportDate }), limit: '300' })}`),
      graphAll(`${GRAPH}/act_${acc}/insights?${q({
        level: 'campaign', time_increment: '1', fields: 'campaign_name,spend',
        time_range: JSON.stringify({ since, until: reportDate }), limit: '500' })}`)
    ]);
    return { acc, day, range };
  }));
  for (const { acc, day, range } of perAccount){
    for (const r of range){
      const spend = parseFloat(r.spend) || 0;
      if (spend > 0) rangeRows.push({ name: r.campaign_name || '', date: r.date_start, spend });
    }
    for (const r of day){
      const spend = parseFloat(r.spend) || 0;
      if (spend <= 0) continue;
      const name = r.campaign_name || '';
      const acts = r.actions, vals = r.action_values;
      const purchase = actVal(acts, 'purchase', 'offsite_conversion.fb_pixel_purchase', 'omni_purchase');
      const msg = actVal(acts, 'onsite_conversion.messaging_conversation_started_7d', 'messaging_conversation_started_7d');
      const lead = actVal(acts, 'lead', 'offsite_conversion.fb_pixel_lead', 'onsite_conversion.lead_grouped');
      const click = actVal(acts, 'link_click');
      const eng = actVal(acts, 'post_engagement');
      const obj = r.objective || '';
      // 成果類型**以活動目標為準**，不是看哪個 action 有數字
      // （訊息廣告偶爾會歸因到一筆購買，purchase 優先的話整家會被誤判成銷售）
      let ind, value;
      if (['OUTCOME_SALES', 'CONVERSIONS', 'PRODUCT_CATALOG_SALES'].includes(obj)) [ind, value] = ['actions:purchase', purchase];
      else if (['OUTCOME_LEADS', 'LEAD_GENERATION'].includes(obj)) [ind, value] = ['actions:lead', lead];
      else if (['LINK_CLICKS', 'OUTCOME_TRAFFIC'].includes(obj)) [ind, value] = ['actions:link_click', click];
      else if (name.includes('訊息') || msg > 0 || obj === 'MESSAGES') [ind, value] = ['actions:messaging_conversation_started', msg];
      else if (purchase) [ind, value] = ['actions:purchase', purchase];
      else if (lead) [ind, value] = ['actions:lead', lead];
      else [ind, value] = ['actions:post_engagement', eng];
      const rev = actVal(vals, 'purchase', 'offsite_conversion.fb_pixel_purchase', 'omni_purchase');
      dayRows.push({
        account: acc, name, objective: obj, spend, result_indicator: ind, result_value: value,
        reach: parseFloat(r.reach) || 0, impressions: parseFloat(r.impressions) || 0,
        link_click: click, post_engagement: eng, roas: spend ? rev / spend : 0
      });
    }
  }
  if (!dayRows.length && !rangeRows.length) throw new Error('Graph 回來是空的');
  return { dayRows, rangeRows };
}

// ---------- 卡片零件（跟 Mac 版一模一樣）----------
function metricTiles(kind, a){
  const spend = a.spend, n = a.result;
  const t = [['花費', compact(spend, true)]];
  if (kind === 'sales'){
    t.push(['購買', compact(n)], ['ROAS', a.roas ? a.roas.toFixed(2) : '-'], ['CPA', n ? compact(spend / n, true) : '-']);
  } else if (kind === 'messaging'){
    t.push(['對話', compact(n)], ['每則', n ? compact(spend / n, true) : '-']);
  } else if (kind === 'leads'){
    t.push(['名單', compact(n)], ['每筆', n ? compact(spend / n, true) : '-']);
  } else if (kind === 'traffic'){
    const clicks = a.click || n;
    t.push(['點擊', compact(clicks)],
      ['CPC', clicks ? compact(spend / clicks, true, 1) : '-'],
      ['CTR', a.imp ? `${(clicks / a.imp * 100).toFixed(1)}%` : '-']);   // CTR 的分母是曝光不是觸及
  } else if (kind === 'engagement'){
    const eng = a.eng || n;
    t.push(['互動', compact(eng)],
      ['觸及' + (a.n > 1 ? '(合計)' : ''), compact(a.reach)],
      ['CPE', eng ? compact(spend / eng, true, 2) : '-']);
  } else {
    t.push(['成果', compact(n)], ['每個', n ? compact(spend / n, true) : '-']);
  }
  return t;
}

function tileRows(tiles){
  const per = tiles.length === 4 ? 2 : 3;
  const rows = [];
  for (let i = 0; i < tiles.length; i += per){
    const cells = tiles.slice(i, i + per).map(([lb, val]) => ({ type: 'box', layout: 'vertical', flex: 1, contents: [
      { type: 'text', text: lb, size: 'xxs', color: INK_SOFT },
      { type: 'text', text: val, size: 'sm', weight: 'bold', color: INK }] }));
    while (cells.length < per) cells.push({ type: 'box', layout: 'vertical', flex: 1, contents: [{ type: 'filler' }] });
    rows.push({ type: 'box', layout: 'horizontal', spacing: 'sm', margin: rows.length ? 'md' : 'sm', contents: cells });
  }
  return rows;
}

function budgetBlock(pct, timePct, spent, budget, state){
  const [fill, track] = STATE[state];
  const w = Math.max(2, Math.min(100, Math.round(pct)));
  const head = { type: 'box', layout: 'baseline', contents: [
    { type: 'text', text: '預算執行', size: 'xs', color: INK_SOFT, flex: 0 },
    { type: 'text', text: `  ${pct.toFixed(0)}%`, size: 'lg', weight: 'bold', color: INK, flex: 0 },
    { type: 'text', text: STATE_LABEL[state], size: 'xs', weight: 'bold', color: fill, align: 'end' }] };
  const bar = { type: 'box', layout: 'vertical', height: '10px', backgroundColor: track, cornerRadius: '5px', margin: 'md', contents: [
    { type: 'box', layout: 'vertical', width: `${w}%`, height: '10px', backgroundColor: fill, cornerRadius: '5px', contents: [{ type: 'filler' }] }] };
  const foot = { type: 'text', text: `時間過 ${timePct.toFixed(0)}%　${NTD}${fmt(spent)} / ${NTD}${fmt(budget)}`,
    size: 'xxs', color: INK_SOFT, margin: 'sm' };
  return { type: 'box', layout: 'vertical', margin: 'lg', backgroundColor: '#FFFFFF', cornerRadius: '8px', paddingAll: '12px', contents: [head, bar, foot] };
}

const chip = text => ({ type: 'box', layout: 'horizontal', margin: 'md', contents: [
  { type: 'box', layout: 'vertical', flex: 0, backgroundColor: '#EDE7F8', cornerRadius: '4px', paddingAll: '3px',
    paddingStart: '10px', paddingEnd: '10px',
    contents: [{ type: 'text', text, size: 'xxs', color: '#6B4FA8', weight: 'bold' }] },
  { type: 'filler' }] });

function clientCard(label, emoji, spendTotal, runLine, budget, groups){
  const contents = [{ type: 'box', layout: 'horizontal', contents: [
    { type: 'text', text: `${emoji} ${label}`, weight: 'bold', size: 'md', color: '#3D3357', wrap: true, flex: 4 },
    { type: 'text', text: spendTotal, weight: 'bold', size: 'md', color: '#6B4FA8', align: 'end', flex: 3 }] }];
  if (runLine) contents.push({ type: 'text', text: runLine, size: 'xxs', color: INK_SOFT, wrap: true, margin: 'sm' });
  if (budget) contents.push(budget);
  for (const [gname, tiles] of groups){
    if (groups.length > 1 && gname) contents.push(chip(gname));
    contents.push(...tileRows(tiles));
  }
  return { type: 'box', layout: 'vertical', margin: 'md', backgroundColor: CARD_BG, cornerRadius: '10px', paddingAll: '14px', contents };
}

function bubble(bodyBoxes, rdStr, warnings, suffix = '', footer = true, alert = false){
  // 異常提醒版換成橘紅底＋不一樣的標題，她才不會把它當成例行日報滑掉
  const b = {
    type: 'bubble',
    header: { type: 'box', layout: 'vertical', backgroundColor: alert ? '#C2603F' : '#9C88CC', paddingAll: '20px', contents: [
      { type: 'text', text: alert ? `⚠️ 廣告要注意${suffix}` : `📊 廣告日報${suffix}`, weight: 'bold', size: 'xl', color: '#FFFFFF', align: 'center' },
      { type: 'text', text: rdStr, size: 'sm', color: alert ? '#F6E7E1' : '#EDE7F6', align: 'center', margin: 'sm' }] },
    body: { type: 'box', layout: 'vertical', spacing: 'none', paddingAll: '16px', contents: bodyBoxes }
  };
  if (footer){
    const foot = warnings.map(w => ({ type: 'text', text: `⚠️ ${w}`, size: 'xxs', color: '#CC3333', wrap: true, align: 'center' }));
    foot.push({ type: 'text', text: '廣告穩穩跑，成效天天好！', size: 'xs', color: '#7C5CBF', align: 'center', margin: warnings.length ? 'sm' : 'none' });
    b.footer = { type: 'box', layout: 'vertical', backgroundColor: '#F0EBF8', paddingAll: '12px', contents: foot };
  }
  return b;
}

// Mac 版用 Python json.dumps（逗號、冒號後面各多一個空格）量大小，照同樣算法才會在同一個點切成兩則
function pyJsonBytes(obj){
  const s = JSON.stringify(obj);
  let extra = 0, inStr = false;
  for (let i = 0; i < s.length; i++){
    const ch = s[i];
    if (inStr){ if (ch === '\\') i++; else if (ch === '"') inStr = false; }
    else if (ch === '"') inStr = true;
    else if (ch === ',' || ch === ':') extra++;
  }
  return new TextEncoder().encode(s).length + extra;
}

async function linePush(env, messages){
  const r = await fetch('https://api.line.me/v2/bot/message/push', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${env.LINE_ACCESS_TOKEN}` },
    body: JSON.stringify({ to: env.LINE_USER_ID, messages }),
    signal: AbortSignal.timeout(25000)
  });
  if (!r.ok) throw new Error(`LINE 推播失敗 ${r.status}: ${await r.text()}`);
}

// ---------- 主流程 ----------
/**
 * @param opts.slot    'morning'（9:00，看昨日）或 'evening'（17:00，看今日截至目前）
 * @param opts.lastTry 這個時段最後一次嘗試：還是抓不到就推失敗通知
 * @param opts.dry     只回卡片內容，不推播、不寫記號
 * @param opts.now     測試用：假裝現在是這個時間（Date）
 */
export async function runAdsDaily(env, { slot, lastTry = false, dry = false, now = new Date() } = {}){
  const tw = new Date(now.getTime() + 8 * 3600 * 1000);
  const todayStr = ymd(tw);
  const [reportDate, reportLabel] = slot === 'morning'
    ? [addDays(todayStr, -1), '昨日'] : [todayStr, '今日截至目前'];
  const db = makeDb(env);
  const markPath = `adsDaily/${todayStr}-${slot}`;
  const markSent = async how => { if (!dry) await db.put(markPath, { how, at: new Date().toISOString() }); };

  if (!dry && await db.get(markPath)) return { skip: `${slot} 這一則今天已經送過了` };

  const warnings = [], alerts = [];
  let clients, dayRows, rangeRows, since;
  try {
    const data = await db.get('clients') || {};
    clients = Object.entries(data)
      .filter(([, c]) => c && typeof c === 'object' && c.active)
      .map(([id, c]) => ({ id, ...c }));
    if (!clients.length) throw new Error('工作台沒有進行中的客戶');

    // 抓數字的起日＝最早的走期起日（最多回看 120 天），走期累計與當日數字都從這裡切
    const starts = clients.flatMap(c => runsOf(c).map(r => r.start)).filter(s => isYmd(s) && s <= reportDate);
    const floor = addDays(reportDate, -120);
    since = starts.length ? [starts.sort()[0], floor].sort()[1] : reportDate;

    if (!env.META_TOKEN) throw new Error('雲端沒有 META_TOKEN');
    ({ dayRows, rangeRows } = await fetchViaGraph(env.META_TOKEN, since, reportDate));
  } catch (e){
    const err = String(e.message || e).slice(0, 200);
    if (!lastTry) return { skip: `這次抓不到（${err}），等下一次重試` };
    const msg = `⚠️ 廣告日報抓不到數字（${reportDate} ${reportLabel}）\n${err}\n（雲端版，Mac 蓋著也會跑；多半是 token 過期或 Meta 暫時連不上）`;
    if (!dry){ await linePush(env, [{ type: 'text', text: msg }]); await markSent('fail'); }
    return { fail: msg };
  }

  const { keys, skipped } = buildMatcher(clients);

  // 走期累計：**每一家照自己的走期起日切**
  // 抓數字是從所有客戶最早的那天開始抓的，不切的話走期晚開始的那家會被多算好幾天
  const spentByClient = {};
  for (const r of rangeRows){
    const c = matchClient(r.name, keys);
    if (!c) continue;
    const st = currentRun(c, todayStr)?.start;
    if (st && r.date && r.date < st) continue;
    const cid = cidOf(c);
    spentByClient[cid] = (spentByClient[cid] || 0) + r.spend;
  }

  // 當日數字，照客戶 → 成果類型分組
  const byClient = {};
  for (const r of dayRows){
    if (!(r.spend > 0)) continue;
    const c = matchClient(r.name, keys);
    if (!c) continue;
    const cid = cidOf(c);
    const d = byClient[cid] ||= { c, kinds: {}, n: 0 };
    d.n++;
    const kind = kindOf(r.result_indicator);
    const a = d.kinds[kind] ||= { spend: 0, result: 0, reach: 0, imp: 0, click: 0, eng: 0, roas: 0, n: 0 };
    a.n++;
    a.spend += r.spend; a.result += r.result_value; a.reach += r.reach;
    a.imp += r.impressions; a.click += r.link_click; a.eng += r.post_engagement;
    a.roas = Math.max(a.roas, r.roas);
  }

  const sumSpend = kinds => Object.values(kinds).reduce((s, k) => s + k.spend, 0);
  const boxes = [], listed = new Set();
  for (const [cid, d] of Object.entries(byClient).sort((x, y) => sumSpend(y[1].kinds) - sumSpend(x[1].kinds))){
    const c = d.c;
    const run = currentRun(c, todayStr);
    // 走期＝她自己在跑的專案。沒填走期的是別人的案子，不列
    if (!run) continue;
    const label = c.short || c.name || '（未命名客戶）';
    const kinds = Object.entries(d.kinds).sort((x, y) => y[1].spend - x[1].spend);
    const mainKind = kinds[0][0];
    const emoji = BRAND_EMOJI[label] || KIND_EMOJI[mainKind] || '📌';

    let runLine = '', budgetUi = null;
    if (isYmd(run.start) && isYmd(run.end)){
      const totalDays = dayDiff(run.end, run.start) + 1;
      const passed = Math.max(1, dayDiff(reportDate, run.start) + 1);
      const timePct = Math.min(100, passed / totalDays * 100);
      const md = s => `${s.slice(5, 7)}/${s.slice(8, 10)}`;
      runLine = `第${run.no ?? '?'}期 ${md(run.start)}–${md(run.end)}　第 ${passed}/${totalDays} 天`;
      const budget = parseBudget(c.budget);
      if (budget > 0){
        const spent = spentByClient[cid] || 0;
        const pct = spent / budget * 100;
        const gap = pct - timePct;
        const state = gap > 10 ? 'fast' : (gap < -10 ? 'slow' : 'ok');
        budgetUi = budgetBlock(pct, timePct, spent, budget, state);
        if (state === 'fast') alerts.push(`${label} 預算燒太快（已花 ${pct.toFixed(0)}%、時間才過 ${timePct.toFixed(0)}%）`);
        else if (timePct >= 90 && pct < 80) alerts.push(`${label} 走期剩 ${totalDays - passed} 天，預算還有 ${(100 - pct).toFixed(0)}% 沒花`);
      }
    }

    const groups = kinds.map(([k, a]) => [KIND_NAME[k] || k, metricTiles(k, a)]);
    boxes.push(clientCard(label, emoji, compact(sumSpend(d.kinds), true), runLine, budgetUi, groups));
    listed.add(cid);
  }

  // 走期內、之前有跑量、今天卻沒花費 → 底部提醒一行（北元、高賀那種永遠抓不到的不會出現）
  const silent = clients.filter(c => !listed.has(cidOf(c)) && currentRun(c, todayStr) && spentByClient[cidOf(c)])
    .map(c => c.short || c.name);
  if (silent.length){
    warnings.push('走期內但今天沒跑量：' + silent.slice(0, 6).join('、'));
    alerts.push('走期內但今天沒跑量：' + silent.slice(0, 6).join('、'));
  }
  if (skipped.length) warnings.push('關鍵字「' + [...new Set(skipped)].sort().join('、') + '」只有一個字，沒採用');

  if (!boxes.length){
    await markSent('nodata');
    return { nodata: true };
  }

  const isEvening = slot === 'evening';
  if (EVENING_QUIET && isEvening && !alerts.length){
    await markSent('quiet');
    return { quiet: true };
  }

  const rdStr = `${reportDate.replace(/-/g, '/')}（${DAYS_ZH[dateOf(reportDate).getUTCDay()]}）${reportLabel}`;
  const alertMode = EVENING_QUIET && isEvening && alerts.length > 0;
  if (alertMode) warnings.unshift(...alerts.filter(a => !warnings.includes(a)));

  let chunks = [boxes];
  if (pyJsonBytes(bubble(boxes, rdStr, warnings)) > 9000 && boxes.length > 1){
    const half = Math.max(1, Math.floor(boxes.length / 2));
    chunks = [boxes.slice(0, half), boxes.slice(half)];
  }
  const alt = (alertMode ? '廣告要注意 ' : '廣告日報 ') + `${reportDate} ${reportLabel}`;
  const messages = chunks.map((ch, i) => ({
    type: 'flex', altText: alt,
    contents: bubble(ch, rdStr, warnings, chunks.length > 1 ? `（${i + 1}/${chunks.length}）` : '', i === chunks.length - 1, alertMode)
  }));

  if (dry) return { dry: true, since, clients: boxes.length, dayRows: dayRows.length, rangeRows: rangeRows.length, messages };
  await linePush(env, messages);
  await markSent('ok');
  return { sent: boxes.length, msgs: chunks.length };
}

// cron 字串 → 哪個時段、是不是最後一次（wrangler.toml 的時間是 UTC）
export function adsSlotOf(cron, now = new Date()){
  const m = String(cron).match(/^([\d,]+) (\d+) /);
  if (!m) return null;
  const hourUtc = +m[2];
  const slot = hourUtc === 1 ? 'morning' : hourUtc === 9 ? 'evening' : null;
  if (!slot) return null;
  // 同一條 cron 可能寫成 "0,15,30 1 * * *"，這時用實際觸發的分鐘判斷
  const minute = m[1].includes(',') ? now.getUTCMinutes() : +m[1];
  return { slot, lastTry: minute >= LAST_TRY_MINUTE };
}
