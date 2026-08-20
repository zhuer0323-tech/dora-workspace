/* ==========================================================
   廣告回報：在 LINE 打「漁三回報」→ 抓數字＋寫分析＋回一則可轉傳的訊息

   流程：
     1. 認出是哪一家（比對工作台客戶的全名／簡稱／認字關鍵字）
     2. 讀這家的走期與回報規格（工作台 clients/{id}.rpt，由
        scripts/sync-report-spec.py 從客戶檔同步上來）
     3. 去 Meta 抓走期內的數字（帳號層 insights，一次撈齊）＋目前在跑的素材與文案
     4. 把「規格 ＋ 數字 ＋ 文案」交給 Claude 排版與寫分析
     5. 回傳純文字，index.js 推回 LINE

   為什麼分析要交給 Claude：每家格式都不一樣（漁三一則素材一段、優逸三排結構、
   工研院分兩塊），而且分析要看素材文案講切角。寫死在程式裡會變成每家一套 if。
   ========================================================== */

import Anthropic from '@anthropic-ai/sdk';

const GRAPH = 'https://graph.facebook.com/v25.0';

/* ---------- 認客戶 ---------- */

/** 從訊息文字找出是哪一家。比對全名、簡稱、認字關鍵字，最長的優先 */
export function findClient(text, clients){
  const t = text.toLowerCase();
  let best = null, bestLen = 0;
  for (const c of Object.values(clients || {})){
    if (!c || typeof c !== 'object' || !c.active) continue;
    const keys = [c.name, c.short, ...String(c.kw || '').replace(/，/g, ',').split(',')]
      .map(s => String(s || '').trim()).filter(Boolean);
    for (const k of keys){
      if (t.includes(k.toLowerCase()) && k.length > bestLen){ best = c; bestLen = k.length; }
    }
  }
  return best;
}

/* ---------- 走期 ---------- */

const todayTW = () => new Date(Date.now() + 8 * 3600 * 1000).toISOString().slice(0, 10);
const ymd = d => d.toISOString().slice(0, 10);

/** 當期走期：含今天的那一期；都不含就用最後一期（走期剛結束還沒排下一期） */
export function currentRun(c){
  const rs = (c?.runs || []).filter(r => r && (r.start || r.end));
  if (!rs.length) return null;
  const t = todayTW();
  return rs.find(r => (!r.start || r.start <= t) && (!r.end || r.end >= t)) || rs[rs.length - 1];
}

/* ---------- Meta ---------- */

async function graph(path, params, token){
  const r = await fetch(`${GRAPH}/${path}?access_token=${token}${params}`);
  const j = await r.json();
  if (j.error){
    const e = new Error(j.error.message || 'Meta API 出錯');
    e.metaCode = j.error.code;
    throw e;
  }
  return j;
}

const INSIGHT_FIELDS = [
  'ad_id', 'ad_name', 'campaign_id', 'campaign_name',
  'impressions', 'reach', 'frequency', 'spend', 'clicks',
  'inline_link_clicks', 'actions', 'action_values',
  'video_thruplay_watched_actions',
].join(',');

/** 這家的活動才算：活動名稱要以 prefix 開頭，且不在 exclude 裡 */
const mine = (name, rpt) => {
  const n = String(name || '');
  if ((rpt.exclude || []).some(x => n.startsWith(x))) return false;
  return (rpt.prefix || []).some(p => n.startsWith(p));
};

/**
 * 抓走期內的數字。
 * - ad 層：一則素材一列（同名不同版位的合併交給 Claude，規格裡有寫怎麼合）
 * - campaign 層：拿**去重後**的觸及（ad 層的 reach 不能相加，同一個人會被算兩次）
 * - ads edge：目前 ACTIVE 的素材與文案（要看文案才寫得出素材面分析）
 */
export async function fetchAds(rpt, since, until, token){
  const ads = [], camps = [], creatives = new Map();

  for (const acc of rpt.accounts || []){
    const tr = `&time_range={"since":"${since}","until":"${until}"}`;

    const adRes = await graph(`act_${acc}/insights`,
      `&level=ad&fields=${INSIGHT_FIELDS}${tr}&limit=500`, token);
    for (const row of adRes.data || []){
      if (mine(row.campaign_name, rpt)) ads.push(row);
    }

    const cRes = await graph(`act_${acc}/insights`,
      `&level=campaign&fields=campaign_id,campaign_name,impressions,reach,frequency,spend,clicks,inline_link_clicks,actions,video_thruplay_watched_actions${tr}&limit=200`,
      token);
    for (const row of cRes.data || []){
      if (mine(row.campaign_name, rpt)) camps.push(row);
    }

    // 目前還在跑的素材＋文案。這裡**不能只看有花錢的**，剛開的新素材會漏掉
    const aRes = await graph(`act_${acc}/ads`,
      '&fields=id,name,effective_status,campaign{name},creative{body,title,object_type}' +
      '&filtering=[{"field":"effective_status","operator":"IN","value":["ACTIVE"]}]&limit=300',
      token);
    for (const a of aRes.data || []){
      if (!mine(a.campaign?.name, rpt)) continue;
      creatives.set(a.id, {
        name: a.name,
        campaign: a.campaign?.name || '',
        body: (a.creative?.body || '').slice(0, 400),   // 文案只要開頭，夠判斷切角就好
        title: a.creative?.title || '',
        kind: a.creative?.object_type || '',
      });
    }
  }
  return { ads, camps, actives: [...creatives.values()] };
}

/** Meta 的 actions 是一個陣列，挑出要的那個動作 */
const act = (row, ...types) => {
  const hit = (row.actions || []).find(a => types.includes(a.action_type));
  return hit ? Number(hit.value) || 0 : 0;
};

/** 把一列 insights 整理成乾淨的數字，順便算出這是哪一種廣告 */
function tidy(row){
  const n = v => Number(v) || 0;
  const msg = act(row, 'onsite_conversion.messaging_conversation_started_7d',
                       'messaging_conversation_started_7d');
  const lead = act(row, 'leadgen.other', 'lead');
  const purchase = act(row, 'purchase', 'omni_purchase', 'offsite_conversion.fb_pixel_purchase');
  const eng = act(row, 'post_engagement');
  const lpv = act(row, 'omni_landing_page_view', 'landing_page_view');
  const thru = n((row.video_thruplay_watched_actions || [])[0]?.value);
  return {
    素材: row.ad_name, 活動: row.campaign_name,
    曝光: n(row.impressions), 觸及: n(row.reach), 頻率: +n(row.frequency).toFixed(2),
    花費: Math.round(n(row.spend)),
    點擊: n(row.clicks), 連結點擊: n(row.inline_link_clicks),
    ...(thru ? { 影片觀看: thru } : {}),
    ...(msg ? { 訊息數: msg } : {}),
    ...(lead ? { 名單數: lead } : {}),
    ...(purchase ? { 購買數: purchase } : {}),
    ...(eng ? { 互動數: eng } : {}),
    ...(lpv ? { 到達頁瀏覽: lpv } : {}),
  };
}

/* ---------- 交給 Claude 排版與分析 ---------- */

const SYSTEM = `你是朱兒（廣告投放經理）的助理，負責把 Meta 廣告數字整理成一則可以直接用 LINE 轉傳給客戶的成效回報。

鐵則（違反任何一條就是失敗）：
1. **一個字都不能提到成本、花費、預算**——每則訊息成本、每次互動成本、CPE、每次點擊成本、名單成本、花了多少錢、預算用了幾成，數據區與分析都不准出現。要比較素材就講倍數或佔比（「訊息量是另一支的五倍以上」「佔整體連結點擊八成以上」）。ROAS 不算成本，銷售型客戶照舊可以報。
2. **數字寫概數**：曝光「約2.7萬次」、點擊「近約1,600次」、訊息「約110則」。名單數這種小數字不要取整（77 就寫約77件）。
3. **完全照客戶規格的格式排**，包含 emoji、欄位順序、素材名稱要不要去掉編號。規格沒寫到的才自己判斷。
4. **不負評表現差的素材**。要講就講「訊息較抽象、轉換較慢」這種中性說法。
5. **只能用我給你的數字**。客戶檔期、素材庫存、客戶內部狀況、上次會議談什麼一律不准猜。
6. 語氣親切口語，句尾可以用「唷!」軟化。**不要 AI 腔**：不要「不是X，是Y」的對仗、不要每段收一句金句、不要用破折號當轉折、不要「真正的」「其實」開頭。

分析（🔺）寫 3–4 行，照這個順序：
  1. 哪幾支跑得好（附佔比或倍數）
  2. **從素材文案解釋為什麼**——切角是講產品實際好處還是品牌理念？訴求具體還是抽象？有沒有點名受眾資格？不能只寫「表現優異」這種空話
  3. 後續素材建議怎麼做（2 個具體方向，要能直接拿去發包）
  4. 收尾：整體進度、平均每日，這期才上線的講「剛上線 N 天」

只輸出那則要傳給客戶的文字，前後不要加任何說明、不要用 markdown 的程式碼框。`;

export async function writeReport({ client, run, rpt, data, since, until, days, env }){
  const anthropic = new Anthropic({ apiKey: env.ANTHROPIC_API_KEY });

  const payload = {
    客戶: client.short || client.name,
    走期: run ? `第${run.no || '?'}期 ${run.start} ～ ${run.end}` : '（沒填走期）',
    這次統計區間: `${since} ～ ${until}（共 ${days} 天）`,
    素材數字: data.ads.map(tidy),
    活動數字_觸及以這裡為準: data.camps.map(tidy),
    目前還在跑的素材與文案: data.actives,
  };

  const res = await anthropic.beta.messages.create({
    model: 'claude-opus-5',
    max_tokens: 8000,
    betas: ['server-side-fallback-2026-07-01'],
    fallbacks: 'default',
    output_config: { effort: 'medium' },     // 有規格有數字，不需要更深的推理；要更好就調 high
    system: SYSTEM,
    messages: [{
      role: 'user',
      content: `這是「${payload.客戶}」的回報規格：\n\n${rpt.spec}\n\n` +
               `以下是抓到的數字（JSON）：\n${JSON.stringify(payload, null, 1)}\n\n` +
               `請照規格排成一則可以直接轉傳給客戶的成效回報。`,
    }],
  });

  return res.content.filter(b => b.type === 'text').map(b => b.text).join('').trim();
}

/* ---------- 對外：index.js 呼叫這一支 ---------- */

export async function makeReport(text, env, db){
  if (!env.META_TOKEN) return '⚠️ 還沒設定廣告帳戶的鑰匙（META_TOKEN）';
  if (!env.ANTHROPIC_API_KEY) return '⚠️ 還沒設定 AI 鑰匙（ANTHROPIC_API_KEY）';

  const clients = await db.get('clients') || {};
  const client = findClient(text, clients);
  if (!client) return '❓ 認不出是哪一家客戶，請把客戶名稱寫清楚一點';

  const rpt = client.rpt;
  if (!rpt?.accounts?.length){
    return `❓「${client.short || client.name}」還沒設定廣告回報\n` +
           '（要我先幫這家做一次，之後才能在 LINE 直接跑）';
  }

  const run = currentRun(client);
  const until = ymd(new Date(Date.now() + 8 * 3600 * 1000 - 86400000));   // 台灣時間的昨天
  const since = run?.start && run.start <= until
    ? run.start
    : ymd(new Date(Date.parse(until) - 29 * 86400000));                   // 沒走期就看最近 30 天
  const days = Math.round((Date.parse(until) - Date.parse(since)) / 86400000) + 1;

  let data;
  try {
    data = await fetchAds(rpt, since, until, env.META_TOKEN);
  } catch (err){
    if (err.metaCode === 190) return '⚠️ 廣告帳戶的鑰匙過期了，要重新產一把（跟每日日報同一把）';
    throw err;
  }
  if (!data.ads.length){
    return `「${client.short || client.name}」在 ${since} ～ ${until} 這段期間沒有跑量的廣告`;
  }

  return await writeReport({ client, run, rpt, data, since, until, days, env });
}
