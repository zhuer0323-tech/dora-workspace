/* ==========================================================
   Dora 賺錢小能手 · LINE 小秘書

   朱兒在 LINE 打一句話 → 排進工作台 → 回一句確認。
   這一版只做「新增任務」，查詢與打勾完成之後再加。

   兩道門檻缺一不可：
     1. 簽章驗證：確定訊息真的來自 LINE
     2. 使用者比對：只聽朱兒本人的（工作台裡有客戶名稱）
   ========================================================== */

import { makeDb } from './firebase.js';
import { classify } from './classify.js';
import { parseDue, todayStr } from './date.js';

export default {
  async fetch(request, env, ctx){
    if (request.method === 'GET') return new Response('OK');           // 給自己確認有活著用
    if (request.method !== 'POST') return new Response('Not found', { status: 404 });

    const raw = await request.text();
    const sig = request.headers.get('x-line-signature') || '';
    if (!(await validSignature(raw, sig, env.LINE_CHANNEL_SECRET))){
      return new Response('bad signature', { status: 401 });
    }

    let body;
    try { body = JSON.parse(raw); } catch { return new Response('bad json', { status: 400 }); }

    // LINE 規定 10 秒內要回 200，實際處理丟到背景做
    for (const ev of body.events || []) ctx.waitUntil(handleEvent(ev, env));
    return new Response('OK');
  }
};

async function validSignature(raw, sig, secret){
  if (!sig || !secret) return false;
  const key = await crypto.subtle.importKey(
    'raw', new TextEncoder().encode(secret),
    { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']
  );
  const mac = await crypto.subtle.sign('HMAC', key, new TextEncoder().encode(raw));
  const expected = btoa(String.fromCharCode(...new Uint8Array(mac)));
  // 長度不同直接判否，長度相同才逐字元比對（避免用時間長短反推簽章）
  if (expected.length !== sig.length) return false;
  let diff = 0;
  for (let i = 0; i < expected.length; i++) diff |= expected.charCodeAt(i) ^ sig.charCodeAt(i);
  return diff === 0;
}

async function handleEvent(ev, env){
  if (ev.type !== 'message' || ev.message?.type !== 'text') return;
  if (ev.source?.userId !== env.LINE_USER_ID) return;   // 不是朱兒本人，完全不理

  const text = String(ev.message.text || '').trim();
  if (!text) return;

  try {
    const reply = await addTask(text, env);
    await lineReply(ev.replyToken, reply, env);
  } catch (err) {
    console.log('出錯：', err && err.message);
    await lineReply(ev.replyToken, '⚠️ 沒存成功，請稍後再試一次', env);
  }
}

async function addTask(text, env){
  const db = makeDb(env);
  const [clients, ui] = await Promise.all([db.get('clients'), db.get('ui')]);

  const { due: parsedDue, rest } = parseDue(text);
  const name = rest || text;                       // 整句都是日期時就退回原文
  const due = parsedDue || todayStr();
  const guess = classify(name, clients || {}, ui || {});

  const id = Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
  // 欄位與工作台「快速記一筆」建出來的一致（index.html 的 quickAdd）
  await db.put(`tasks/${id}`, {
    id, name, area: 'work',
    client: guess.client || '',
    type: guess.type || '其他',
    prio: 'today', status: 'todo',
    repeat: 'none', wdays: [], doneDates: {},
    start: '', due, time: '', mins: 0,
    progress: 0, note: '', star: false, notionId: '',
    source: 'line',                                // 標記從 LINE 進來的，之後要查得出來
    createdAt: Date.now(), doneAt: null
  });

  const c = guess.client ? (clients?.[guess.client] || null) : null;
  const clientLabel = c ? (c.short || c.name)
    : guess.clientIsGuessNew ? `未指定（看起來像「${guess.clientIsGuessNew}」，要建客戶請到工作台）`
    : '未指定（之後在工作台補）';

  const lines = [
    `✅ 已排入 ${due.slice(5).replace('-', '/')}`,
    name,
    `客戶：${clientLabel}`,
    `類型：${guess.type}`
  ];
  if (!parsedDue) lines.push('（沒抓到日期，先排今天）');
  return lines.join('\n');
}

async function lineReply(replyToken, text, env){
  if (!replyToken) return;
  const r = await fetch('https://api.line.me/v2/bot/message/reply', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${env.LINE_ACCESS_TOKEN}`
    },
    body: JSON.stringify({ replyToken, messages: [{ type: 'text', text }] })
  });
  if (!r.ok) console.log('回覆失敗', r.status, await r.text());
}
