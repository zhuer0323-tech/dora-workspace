/* ==========================================================
   讀寫工作台的雲端資料（Firebase Realtime Database）

   權限走 Firebase 服務帳號：自己簽一個 JWT 去換 access token。
   Worker 這邊用內建的 WebCrypto 簽 RS256（8:30 早報的腳本是叫系統 openssl，
   兩邊做的事一樣，只是環境不同）
   ========================================================== */

const b64u = buf => btoa(String.fromCharCode(...new Uint8Array(buf)))
  .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
const b64uStr = str => b64u(new TextEncoder().encode(str));

// token 有效一小時，同一個 Worker 執行個體重複使用，不用每則訊息都換一次
let cachedToken = null;   // { token, exp }

async function accessToken(saJson){
  const now = Math.floor(Date.now() / 1000);
  if (cachedToken && cachedToken.exp - 60 > now) return cachedToken.token;

  const sa = JSON.parse(saJson);
  const header = b64uStr(JSON.stringify({ alg: 'RS256', typ: 'JWT' }));
  const claims = b64uStr(JSON.stringify({
    iss: sa.client_email,
    scope: 'https://www.googleapis.com/auth/firebase.database https://www.googleapis.com/auth/userinfo.email',
    aud: 'https://oauth2.googleapis.com/token',
    iat: now,
    exp: now + 3600
  }));
  const signingInput = `${header}.${claims}`;

  const pem = sa.private_key.replace(/-----[^-]+-----/g, '').replace(/\s+/g, '');
  const der = Uint8Array.from(atob(pem), c => c.charCodeAt(0));
  const key = await crypto.subtle.importKey(
    'pkcs8', der.buffer,
    { name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-256' },
    false, ['sign']
  );
  const sig = await crypto.subtle.sign('RSASSA-PKCS1-v1_5', key,
    new TextEncoder().encode(signingInput));

  const resp = await fetch('https://oauth2.googleapis.com/token', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({
      grant_type: 'urn:ietf:params:oauth:grant-type:jwt-bearer',
      assertion: `${signingInput}.${b64u(sig)}`
    })
  });
  if (!resp.ok) throw new Error(`拿 token 失敗 ${resp.status}: ${await resp.text()}`);
  const data = await resp.json();
  cachedToken = { token: data.access_token, exp: now + (data.expires_in || 3600) };
  return cachedToken.token;
}

export function makeDb(env){
  const base = `${env.WS_DB_URL.replace(/\/$/, '')}/${env.WS_ROOM}`;
  const auth = async () => ({ Authorization: `Bearer ${await accessToken(env.FIREBASE_SA)}` });
  return {
    async get(path){
      const r = await fetch(`${base}/${path}.json`, { headers: await auth() });
      if (!r.ok) throw new Error(`讀取 ${path} 失敗 ${r.status}`);
      return await r.json();
    },
    async put(path, value){
      const r = await fetch(`${base}/${path}.json`, {
        method: 'PUT',
        headers: { ...(await auth()), 'Content-Type': 'application/json' },
        body: JSON.stringify(value)
      });
      if (!r.ok) throw new Error(`寫入 ${path} 失敗 ${r.status}`);
      return await r.json();
    }
  };
}
