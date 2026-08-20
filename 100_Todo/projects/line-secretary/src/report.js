/* ==========================================================
   廣告回報：在 LINE 打「漁三回報」→ 排一筆待辦 → 電腦跑完推回來

   為什麼不在這裡直接算：
     回報要抓數字、讀素材文案、照各家規格排版、寫分析。分析那段要 AI，
     Worker 自己做就得另外接一個付費 API。朱兒不想多一個服務，所以改成
     **她的 Mac 當引擎**——那邊已經有 Claude Code（現成的訂閱）與廣告帳戶權限。

   這支只做三件事：
     1. 認出是哪一家（比對工作台客戶的全名／簡稱／認字關鍵字）
     2. 在工作台記一筆待辦 reportJobs/{id}
     3. 回一句「收到」

   接下來由 ~/Library/Scripts/dora-report-runner.sh（每分鐘跑一次）接手：
   看到待辦 → 叫 Claude Code 跑回報 → 推回 LINE → 把待辦標記完成。
   ========================================================== */

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

const todayTW = () => new Date(Date.now() + 8 * 3600 * 1000).toISOString().slice(0, 10);

/** 當期走期：含今天的那一期；都不含就用最後一期 */
export function currentRun(c){
  const rs = (c?.runs || []).filter(r => r && (r.start || r.end));
  if (!rs.length) return null;
  const t = todayTW();
  return rs.find(r => (!r.start || r.start <= t) && (!r.end || r.end >= t)) || rs[rs.length - 1];
}

/** 排一筆廣告回報的待辦，回覆給朱兒的確認訊息 */
export async function queueReport(text, env, db){
  const clients = await db.get('clients') || {};
  const client = findClient(text, clients);
  if (!client) return '❓ 認不出是哪一家客戶，請把客戶名稱寫清楚一點';

  const label = client.short || client.name;
  const run = currentRun(client);

  const id = Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
  await db.put(`reportJobs/${id}`, {
    id,
    client: label,          // 電腦那邊拿這個去跑
    clientId: client.id || '',
    raw: text,
    status: 'pending',
    createdAt: Date.now(),
    doneAt: null,
  });

  const lines = [`📊 收到，正在跑「${label}」的廣告回報`];
  if (run) lines.push(`走期：第${run.no || '?'}期 ${run.start} ～ ${run.end}`);
  lines.push('大約 1-2 分鐘推給你（電腦要開著）');
  return lines.join('\n');
}
