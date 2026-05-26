require('dotenv').config();
const express = require('express');
const { chromium } = require('playwright');
const Groq = require('groq-sdk');
const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

const app = express();
const PORT = 3939;
const PROFILE_DIR  = path.join(__dirname, 'browser-profile');
const CONFIG_FILE  = path.join(__dirname, 'config.json');
const REPLIED_FILE = path.join(__dirname, 'replied.json');

const groq = new Groq({ apiKey: process.env.GROQ_API_KEY });
let config = JSON.parse(fs.readFileSync(CONFIG_FILE, 'utf-8'));

app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

// 保持一個全域 browser context，避免每次都重新開瀏覽器
let browserContext = null;

function killOrphanBrowsers() {
  try {
    const psOutput = execSync('ps aux', { encoding: 'utf-8' });
    const pids = psOutput.split('\n')
      .filter(line => line.includes('threads-patrol/browser-profile') && !line.includes(` ${process.pid} `))
      .map(line => line.trim().split(/\s+/)[1])
      .filter(Boolean);
    if (pids.length > 0) {
      console.log(`[Browser] 清除殘留進程: PID ${pids.join(', ')}`);
      execSync(`kill ${pids.join(' ')} 2>/dev/null || true`, { shell: true });
      return pids.length;
    }
  } catch {}
  return 0;
}

async function getContext() {
  if (browserContext) return browserContext;
  // 清除殘留的舊瀏覽器進程，避免 profile 被佔用
  const killed = killOrphanBrowsers();
  if (killed > 0) await sleep(2000);
  try { fs.unlinkSync(path.join(PROFILE_DIR, 'SingletonLock')); } catch {}
  browserContext = await chromium.launchPersistentContext(PROFILE_DIR, {
    headless: false,
    viewport: { width: 1440, height: 900 },
    args: ['--disable-blink-features=AutomationControlled'],
    userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
  });
  await browserContext.addInitScript(() => {
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
  });
  return browserContext;
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

// ── 搜尋貼文 ──
app.post('/api/search', async (req, res) => {
  const { keyword } = req.body;
  if (!keyword) return res.status(400).json({ error: '請輸入關鍵字' });

  try {
    const context = await getContext();
    const page = await context.newPage();
    await page.goto(
      `https://www.threads.com/search?q=${encodeURIComponent(keyword)}&serp_type=default`,
      { waitUntil: 'domcontentloaded', timeout: 30000 }
    );
    await sleep(5000);
    for (let i = 0; i < 4; i++) {
      await page.evaluate(() => window.scrollBy(0, 500));
      await sleep(1200);
    }

    const posts = await page.evaluate(() => {
      const results = [];
      const seen = new Set();
      document.querySelectorAll('a[href*="/post/"]').forEach(link => {
        const url = link.href;
        if (!url || seen.has(url)) return;
        seen.add(url);

        let el = link, text = '';
        for (let i = 0; i < 12; i++) {
          el = el.parentElement;
          if (!el) break;
          const t = (el.innerText || '').trim();
          if (t.length > 30) { text = t; break; }
        }

        if (text) {
          const lines = text.split('\n').map(l => l.trim()).filter(Boolean);
          // 嘗試抓帳號名稱（第一行）
          const author = lines[0] || '';
          // 正文從第2行開始，過濾掉時間、翻譯、純數字
          const content = lines.slice(1)
            .filter(l => !/^\d+[smhd]$/.test(l) && l !== 'Translate' && !/^\d+$/.test(l) && l.length > 3)
            .join(' ')
            .slice(0, 300);
          if (content.length > 15) results.push({ url, author, text: content });
        }
      });
      return results.slice(0, 20);
    });

    await page.close();
    res.json({ posts });
  } catch (err) {
    browserContext = null; // 重置，下次重開瀏覽器
    res.status(500).json({ error: `搜尋失敗：${err.message}` });
  }
});

// ── 生成 AI 回覆草稿 ──
app.post('/api/generate', async (req, res) => {
  const { postText, extraInfo } = req.body;
  if (!postText) return res.status(400).json({ error: '缺少貼文內容' });

  try {
    const response = await groq.chat.completions.create({
      model: 'llama-3.3-70b-versatile',
      max_tokens: 200,
      messages: [
        {
          role: 'system',
          content: `你是一個幫忙撰寫 Threads 回覆草稿的助手。

帳號個性：
${config.accountPersona}

回覆規則：
- 長度 20-80 字，自然口語
- 像真人說話，不像官方稿
- 可以提問、補充觀點、表達認同或疑惑
- 不要打廣告
- 不要用 emoji
- 只輸出回覆內容本身`
        },
        {
          role: 'user',
          content: `請幫我針對這篇 Threads 貼文寫一則回覆草稿：

【貼文內容】
${postText}

${extraInfo ? `【我想補充的方向或資訊】\n${extraInfo}` : ''}

請直接輸出回覆文字。`
        }
      ]
    });
    res.json({ reply: response.choices[0].message.content.trim() });
  } catch (err) {
    res.status(500).json({ error: `AI 生成失敗：${err.message}` });
  }
});

// ── 發送回覆 ──
app.post('/api/reply', async (req, res) => {
  const { url, reply } = req.body;
  if (!url || !reply) return res.status(400).json({ error: '缺少必要資訊' });

  const LOG = '/tmp/reply-debug.log';
  const log = (msg) => {
    const line = `[${new Date().toISOString()}] ${msg}\n`;
    process.stdout.write(line);
    fs.appendFileSync(LOG, line);
  };

  // 每次清空 log
  fs.writeFileSync(LOG, `=== Reply 開始 ===\nURL: ${url}\n回覆: ${reply}\n\n`);

  try {
    log('① getContext...');
    const context = await getContext();
    log('② newPage...');
    const page = await context.newPage();

    log(`③ 前往: ${url}`);
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
    log('④ 頁面載入完成，等待 4 秒...');
    await sleep(4000);
    await page.evaluate(() => window.scrollBy(0, 300));
    await sleep(1000);

    // 記錄所有按鈕
    const dumpButtons = async (label) => {
      const btns = await page.evaluate(() =>
        Array.from(document.querySelectorAll('button, [role="button"]')).map(b => ({
          tag: b.tagName,
          text: (b.innerText || '').trim().slice(0, 30),
          disabled: b.disabled || b.getAttribute('aria-disabled') === 'true',
          aria: b.getAttribute('aria-label') || '',
        })).filter(b => b.text || b.aria)
      );
      log(`${label}: ${JSON.stringify(btns)}`);
    };
    await dumpButtons('⑤ 初始按鈕');

    // ── Step 1：點擊回覆觸發按鈕 ──
    // 先試 aria-label
    let triggered = false;
    const ariaLabels = ['Reply', '回覆', 'reply'];
    for (const label of ariaLabels) {
      const el = page.locator(`[aria-label="${label}"]`).first();
      if (await el.count() > 0) {
        try { await el.click(); triggered = true; console.log(`[Reply] ✓ 點擊觸發 aria-label="${label}"`); break; } catch {}
      }
    }

    // 再試包含 "Reply to" 或 placeholder 文字的元素
    if (!triggered) {
      const textTriggers = [
        'span:has-text("Reply to")', 'span:has-text("新增回覆")', 'span:has-text("新增串文")',
        '[placeholder*="Reply"]', '[placeholder*="回覆"]',
      ];
      for (const sel of textTriggers) {
        const el = page.locator(sel).first();
        if (await el.count() > 0) {
          try { await el.click(); triggered = true; console.log(`[Reply] ✓ 點擊觸發 "${sel}"`); break; } catch {}
        }
      }
    }

    // 最後備援：點第一個有 SVG 的 role="button"
    if (!triggered) {
      const btns = page.locator('div[role="button"]');
      const count = await btns.count();
      for (let i = 0; i < Math.min(count, 8); i++) {
        const b = btns.nth(i);
        const hasSvg = await b.locator('svg').count() > 0;
        if (hasSvg) {
          try { await b.click(); triggered = true; console.log(`[Reply] ✓ 點擊 div[role="button"] #${i}`); break; } catch {}
        }
      }
    }

    log(`⑥ 觸發狀態: ${triggered ? '成功' : '未觸發，繼續等輸入框'}`);
    await sleep(1500);
    await dumpButtons('⑦ 觸發後按鈕');

    // ── Step 2：等待輸入框出現（最多 10 秒）──
    let input = null;
    const inputSels = ['div[contenteditable="true"]', '[role="textbox"]', '[data-lexical-editor="true"]'];
    for (let attempt = 0; attempt < 10; attempt++) {
      for (const sel of inputSels) {
        const el = page.locator(sel).first();
        if (await el.count() > 0) { input = el; console.log(`[Reply] ✓ 找到輸入框: ${sel}`); break; }
      }
      if (input) break;
      await sleep(1000);
    }
    if (!input) {
      await page.screenshot({ path: '/tmp/threads-before-post.png' }).catch(() => {});
      throw new Error('找不到回覆輸入框（截圖存至 /tmp/threads-before-post.png）');
    }
    log('⑧ 找到輸入框，開始輸入...');

    await input.click();
    await sleep(600);
    await page.keyboard.insertText(reply);
    log(`⑨ 文字已輸入: "${reply.slice(0, 30)}"`);
    await sleep(2000);

    // 截圖（輸入後，送出前）
    await page.screenshot({ path: '/tmp/threads-before-post.png' }).catch(() => {});
    log('⑩ 截圖存至 /tmp/threads-before-post.png');
    await dumpButtons('⑪ 輸入後按鈕');

    // ── Step 3：送出（四策略） ──
    let clicked = false;

    // 策略 A：從 input 父層往上找，搜尋 button + role="button"，不限定文字（最後一個非 disabled 的）
    clicked = await page.evaluate(() => {
      const keywords = ['Post', '發布', '發佈', 'Reply', '回覆'];
      const selectors = ['div[contenteditable="true"]', '[role="textbox"]', '[data-lexical-editor="true"]'];
      let inputEl = null;
      for (const s of selectors) { inputEl = document.querySelector(s); if (inputEl) break; }
      if (!inputEl) return false;

      let el = inputEl;
      for (let i = 0; i < 25; i++) {
        el = el.parentElement;
        if (!el) break;
        // 找 button 和 role="button"，包含 div / span
        const candidates = Array.from(el.querySelectorAll('button, [role="button"]')).reverse();
        for (const btn of candidates) {
          const txt = (btn.innerText || btn.textContent || '').trim();
          const aria = btn.getAttribute('aria-label') || '';
          const isDisabled = btn.disabled || btn.getAttribute('aria-disabled') === 'true';
          if (!isDisabled && keywords.some(k => txt.includes(k) || aria.includes(k))) {
            btn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
            return 'A:' + txt.slice(0, 20);
          }
        }
      }
      return false;
    });
    if (clicked) log('✓ 策略 A: ' + clicked);

    // 策略 B：全頁掃所有可見且非 disabled 的 Post/發布 元素
    if (!clicked) {
      clicked = await page.evaluate(() => {
        const keywords = ['Post', '發布', '發佈'];
        const all = Array.from(document.querySelectorAll('button, [role="button"]'));
        for (let i = all.length - 1; i >= 0; i--) {
          const el = all[i];
          const txt = (el.innerText || el.textContent || '').trim();
          const aria = el.getAttribute('aria-label') || '';
          const isDisabled = el.disabled || el.getAttribute('aria-disabled') === 'true';
          const rect = el.getBoundingClientRect();
          if (!isDisabled && rect.width > 0 && keywords.some(k => txt.includes(k) || aria.includes(k))) {
            el.dispatchEvent(new MouseEvent('click', { bubbles: true }));
            return 'B:' + txt.slice(0, 20);
          }
        }
        return false;
      });
      if (clicked) log('✓ 策略 B: ' + clicked);
    }

    // 策略 C：Playwright locator + force click
    if (!clicked) {
      const kwPatterns = [/^Post$/i, /^發布$/, /^發佈$/, /^Reply$/i, /^回覆$/];
      const allBtns = page.locator('button');
      const cnt = await allBtns.count();
      for (let i = cnt - 1; i >= 0; i--) {
        const btn = allBtns.nth(i);
        try {
          const txt = (await btn.textContent() || '').trim();
          if (kwPatterns.some(p => p.test(txt)) && await btn.isVisible() && await btn.isEnabled()) {
            await btn.click({ force: true });
            clicked = 'C:' + txt;
            log('✓ 策略 C: ' + clicked);
            break;
          }
        } catch {}
      }
    }

    // 策略 D：鍵盤快捷鍵
    if (!clicked) {
      try { await input.press('Meta+Return'); await sleep(1500); clicked = 'D:Meta+Return'; log('✓ 策略 D: Meta+Return'); } catch {}
    }
    if (!clicked) {
      try { await page.keyboard.press('Control+Return'); await sleep(1500); clicked = 'D:Ctrl+Return'; log('✓ 策略 D: Ctrl+Return'); } catch {}
    }

    if (!clicked) {
      log('✗ 四策略全失敗');
      throw new Error('找不到送出按鈕（截圖已存至 /tmp/threads-before-post.png）');
    }

    await sleep(2500);
    log('✓ 發送完成');
    await page.close();

    // 記錄
    const replied = fs.existsSync(REPLIED_FILE) ? JSON.parse(fs.readFileSync(REPLIED_FILE)) : {};
    const today = new Date().toISOString().slice(0, 10);
    if (!replied[today]) replied[today] = {};
    replied[today][url] = { repliedAt: new Date().toISOString(), reply };
    fs.writeFileSync(REPLIED_FILE, JSON.stringify(replied, null, 2));

    res.json({ ok: true });
  } catch (err) {
    res.status(500).json({ error: `發送失敗：${err.message}` });
  }
});

// ── 今日統計 ──
app.get('/api/stats', (req, res) => {
  const replied = fs.existsSync(REPLIED_FILE) ? JSON.parse(fs.readFileSync(REPLIED_FILE)) : {};
  const today = new Date().toISOString().slice(0, 10);
  res.json({ todayCount: Object.keys(replied[today] || {}).length });
});

// ── 回覆歷史 ──
app.get('/api/history', (req, res) => {
  const replied = fs.existsSync(REPLIED_FILE) ? JSON.parse(fs.readFileSync(REPLIED_FILE)) : {};
  const all = [];
  for (const [, entries] of Object.entries(replied)) {
    for (const [url, data] of Object.entries(entries)) {
      all.push({ url, ...data });
    }
  }
  all.sort((a, b) => new Date(b.repliedAt || 0) - new Date(a.repliedAt || 0));
  res.json({ history: all.slice(0, 300) });
});

// ── 讀取設定 ──
app.get('/api/config', (req, res) => {
  res.json(config);
});

// ── 更新設定 ──
app.put('/api/config', (req, res) => {
  const { keywords, accountPersona, dailyLimit } = req.body;
  if (Array.isArray(keywords)) config.keywords = keywords;
  if (typeof accountPersona === 'string') config.accountPersona = accountPersona;
  if (typeof dailyLimit === 'number') config.dailyLimit = dailyLimit;
  fs.writeFileSync(CONFIG_FILE, JSON.stringify(config, null, 2));
  res.json({ ok: true });
});

const server = app.listen(PORT, () => {
  console.log(`\n✅ 海巡工具已啟動`);
  console.log(`👉 打開瀏覽器：http://localhost:${PORT}\n`);
});

// 關閉時清除瀏覽器
process.on('SIGINT', async () => {
  if (browserContext) await browserContext.close().catch(() => {});
  server.close();
  process.exit(0);
});
