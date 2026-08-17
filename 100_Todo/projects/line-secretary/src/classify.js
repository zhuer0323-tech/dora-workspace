/* ==========================================================
   客戶與工作類型的自動判斷

   ⚠️ 這一份是從工作台 `100_Todo/projects/index.html` 抄過來的，
   兩邊必須維持同一套規則。網頁那邊改了 CLIENT_ALIASES／DEFAULT_TYPES／
   TYPE_PRIORITY／extractHead 的邏輯，這裡要跟著改，不會自動同步。
   （工作台是唯一真相，這裡只讀不寫，也不寫入 ui.learn）
   ========================================================== */

export const DEFAULT_TYPES = [
  { n:'廣告',     kw:'廣告上線,名單下載,廣告調整,走期規劃,社群內容,限動發布,操作方向,方向建議,優化建議,投放排程,廣告文案,社群文案,素材分析,走期,名單,限動,社群,貼文,發文,投放,排程,上檔,續投,素材,文案,廣告' },
  { n:'會議',     kw:'會議記錄,會議,開會,面談,拜訪,訪談' },
  { n:'提案',     kw:'提案簡報,提案,企劃,簡報' },
  { n:'禾言',     kw:'禾言,刷卡明細,雲端更新,雲端調整,雲端整理' },
  { n:'內部',     kw:'廣告檢查,結案檢查,檢查' },
  { n:'結案報告', kw:'結案報告,結案報表,結案,月報' },
  { n:'報價單',   kw:'報價單,報價' },
  { n:'KOL',      kw:'kol,網紅,部落客' },
  { n:'週報回報', kw:'廣告回報,成效回報,週報回報,週報,回報' },
  { n:'其他',     kw:'' }
];

export const TYPE_PRIORITY = ['禾言', '內部'];

export const CLIENT_ALIASES = {
  '李享家直播集團':['李享家','李老闆','李老板'],
  '漁三':['渔三'], '優逸':['优逸'], 'TOTO':['toto'],
  '十八子肉':['十八子'], '北元當舖':['北元','當舖','當鋪'],
  'OF AZIKU':['aziku'], '花徑花藝':['花徑'], '華信航空':['華信'],
  '焦糖風':['焦糖楓'], '惠盈生技':['惠盈'], '柚山':['沪山','滬山'], 'LF':['lf資訊'],
  '內部':['禾言','廣告成效填寫','刷卡明細','報價單歸檔','成效表單','專案成效',
         'fb專員','fb會議','meta廣告驗證','名片','勞報單','身分證','職代交接','離職交接',
         '課程簡報','廣告教材','廣告顧問','雲端整理','結案報告歸檔','官方line','會議記錄統整','短影音拍攝']
};

const EXTRA_ACTIONS = ['報告','報表','表單','確認','處理','追蹤','上稿','修改','調整','回覆',
  '整理','名單','影片','拍攝','剪輯','設計','製作','分析','檢視','歸檔','結算','請款','對帳','匯款'];

export const normKey = s => String(s ?? '').toLowerCase()
  .replace(/[\s　·・、,，。.／/\\|\-—–－_()（）\[\]【】{}:：;；!！?？'"“”‘’@#*+~%$&]+/g, '');

function typeDefs(ui){
  const v = ui && ui.types;
  if (!Array.isArray(v) || !v.length) return DEFAULT_TYPES.map(t => ({ ...t }));
  return v.filter(t => t && t.n);
}
export const typeNames = ui => typeDefs(ui).map(t => t.n);
export const fallbackType = ui =>
  typeNames(ui).includes('其他') ? '其他' : (typeNames(ui)[0] || '其他');

/* 客戶：全名 + 簡稱 + 別名表 + 客戶自己填的關鍵字，長的排前面，「內部」永遠最後
   類型：TYPE_PRIORITY 那幾類整組排最前面，之後才比關鍵字長度 */
function kwTables(clients, ui){
  const cs = [];
  Object.values(clients || {}).forEach(c => {
    if (!c || !c.name) return;
    const words = [c.name, c.short, ...(CLIENT_ALIASES[c.name] || []),
                   ...String(c.kw || '').split(/[,，、\s]+/)];
    const seen = new Set();
    words.map(normKey).filter(w => w && !seen.has(w) && seen.add(w))
      .forEach(k => cs.push({ id: c.id, k, last: c.name === '內部' }));
  });
  cs.sort((a, b) => (a.last ? 1 : 0) - (b.last ? 1 : 0) || b.k.length - a.k.length);

  const ts = [];
  typeDefs(ui).forEach(t => String(t.kw || '').split(/[,，、\s]+/)
    .map(normKey).filter(Boolean).forEach(k => ts.push({ n: t.n, k })));
  const pri = n => { const i = TYPE_PRIORITY.indexOf(n); return i < 0 ? 99 : i; };
  ts.sort((a, b) => pri(a.n) - pri(b.n) || b.k.length - a.k.length);

  const acts = [...new Set([...ts.map(x => x.k), ...EXTRA_ACTIONS.map(normKey)])]
    .filter(w => w.length >= 2);
  return { cs, ts, acts };
}

/* 標題開頭到第一個動作詞之間，當作客戶名（太長就寧可留空，不亂猜） */
function extractHead(title, acts){
  const s = String(title || '').trim();
  if (!s) return '';
  const low = s.toLowerCase();
  let idx = -1;
  for (const w of acts){
    const i = low.indexOf(w);
    if (i > 0 && (idx < 0 || i < idx)) idx = i;
  }
  if (idx < 0) return '';
  const head = s.slice(0, idx)
    .replace(/^[\s　@#\-—–－·、,，:：/／|(（\[【]+/, '')
    .replace(/[\s　\-—–－·、,，:：/／|)）\]】的]+$/, '').trim();
  if (!head || head.length > 8 || /^[\d\s\-/.]+$/.test(head)) return '';
  return head;
}

/**
 * 判斷一句話屬於哪個客戶、哪種工作類型。
 * 回傳 { client, clientIsGuessNew, type }
 * - client 是客戶 id；認不出來就是空字串
 * - clientIsGuessNew 是「開頭那段字看起來像新客戶」的候選名稱，
 *   小秘書**不會**自動建客戶（避免 LINE 隨手打字在客戶頁長出一堆假客戶），
 *   只會在回覆裡提一句，讓朱兒自己到工作台決定
 */
export function classify(title, clients, ui){
  const raw = String(title || '').trim();
  const out = { client: '', clientIsGuessNew: '', type: '' };
  if (!raw) return out;

  const { cs, ts, acts } = kwTables(clients, ui);
  const head = extractHead(raw, acts);
  const headN = normKey(head);
  const nRaw = normKey(raw);
  // 開頭那段字本身就是工作內容就不算客戶名
  const headIsWork = !!headN && ts.some(t => headN.includes(t.k));

  const learnedC = !headIsWork && headN && ui?.learn?.client?.[headN];
  let hitKw = '';
  if (learnedC && clients[learnedC]) out.client = learnedC;
  else {
    const hit = cs.find(c => nRaw.includes(c.k));
    if (hit){ out.client = hit.id; hitKw = hit.k; }
    else if (head && !headIsWork) out.clientIsGuessNew = head;
  }

  // 類型一律用整串標題比對（「禾言」「刷卡明細」同時是客戶別名與類型關鍵字，切掉就比不到）；
  // 但「她手動改過所以要記住」的那個 key 是去掉客戶名的那段，同一種工作換客戶也認得
  const learnKey = (() => {
    let rest = raw;
    if (head && !headIsWork && rest.startsWith(head)) rest = rest.slice(head.length);
    let r = normKey(rest);
    if (hitKw) r = r.split(hitKw).join('');
    return r;
  })();
  const learnedT = learnKey && ui?.learn?.type?.[learnKey];
  if (learnedT && typeNames(ui).includes(learnedT)) out.type = learnedT;
  else {
    const hitT = ts.find(t => nRaw.includes(t.k));
    out.type = hitT ? hitT.n : fallbackType(ui);
  }
  return out;
}
