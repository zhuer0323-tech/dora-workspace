require('dotenv').config();
const { chromium } = require('playwright');
const Groq = require('groq-sdk');
const fs = require('fs');
const path = require('path');

const CONFIG_FILE   = path.join(__dirname, 'config.json');
const REPLIED_FILE  = path.join(__dirname, 'replied.json');
const STATUS_FILE   = path.join(__dirname, 'patrol-status.json');
const PROFILE_DIR   = path.join(__dirname, 'browser-profile');

const config = JSON.parse(fs.readFileSync(CONFIG_FILE, 'utf-8'));
const groq   = new Groq({ apiKey: process.env.GROQ_API_KEY });

const keywords = process.argv.slice(2).length
  ? process.argv.slice(2)
  : config.keywords;

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
function randomBetween(a, b) { return Math.floor(Math.random() * (b - a + 1)) + a; }
function getTodayKey() { return new Date().toISOString().slice(0, 10); }

function loadReplied() {
  if (!fs.existsSync(REPLIED_FILE)) return {};
  return JSON.parse(fs.readFileSync(REPLIED_FILE, 'utf-8'));
}

function saveReplied(data) {
  fs.writeFileSync(REPLIED_FILE, JSON.stringify(data, null, 2));
}

function writeStatus(update) {
  const current = fs.existsSync(STATUS_FILE)
    ? JSON.parse(fs.readFileSync(STATUS_FILE, 'utf-8'))
    : { running: false, logs: [], results: [] };
  const merged = { ...current, ...update, updatedAt: new Date().toISOString() };
  if (update.log) {
    merged.logs = [{ time: new Date().toISOString(), msg: update.log }, ...(current.logs || [])].slice(0, 100);
    delete merged.log;
  }
  if (update.result) {
    merged.results = [update.result, ...(current.results || [])];
    delete merged.result;
  }
  fs.writeFileSync(STATUS_FILE, JSON.stringify(merged, null, 2));
}

async function generateReply(postText) {
  const res = await groq.chat.completions.create({
    model: 'llama-3.3-70b-versatile',
    max_tokens: 150,
    messages: [
      {
        role: 'system',
        content: `你是一個幫忙回覆 Threads 貼文的助手。

帳號個性：
${config.accountPersona}

回覆規則：
- 長度 15-60 字，簡短自然，不要太長
- 像真人說話，不要像官方公告
- 可以提問、分享觀點、補充見解
- 不要說「你說得很對」「這很有道理」這類空洞的話
- 不要打廣告
- 不要用 emoji
- 只輸出回覆內容本身`
      },
      { role: 'user', content: `請回覆這篇 Threads 貼文：\n\n${postText}` }
    ]
  });
  return res.choices[0].message.content.trim();
}

async function searchPosts(page, keyword) {
  await page.goto(
    `https://www.threads.com/search?q=${encodeURIComponent(keyword)}&serp_type=default`,
    { waitUntil: 'domcontentloaded', timeout: 30000 }
  );
  await sleep(5000);

  // 捲動幾次讓更多貼文載入
  for (let i = 0; i < 3; i++) {
    await page.evaluate(() => window.scrollBy(0, 500));
    await sleep(1500);
  }

  return page.evaluate(() => {
    const results = [];
    const seen = new Set();
    document.querySelectorAll('a[href*="/post/"]').forEach(link => {
      const url = link.href;
      if (!url || seen.has(url)) return;
      seen.add(url);

      // 往上找有貼文內容的父層
      let el = link;
      let text = '';
      for (let i = 0; i < 12; i++) {
        el = el.parentElement;
        if (!el) break;
        const t = (el.innerText || '').trim();
        if (t.length > 30) { text = t; break; }
      }

      // 清理文字：去掉帳號行、時間行、翻譯按鈕、純數字
      if (text) {
        const lines = text.split('\n').map(l => l.trim()).filter(Boolean);
        const content = lines
          .slice(2)
          .filter(l => l !== 'Translate' && !/^\d+$/.test(l) && l.length > 4)
          .join(' ')
          .slice(0, 300);
        text = content;
      }

      if (text.length > 15) results.push({ url, text });
    });
    return results.slice(0, 15);
  });
}

async function replyToPost(page, url, replyText) {
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await sleep(4000);
  await page.evaluate(() => window.scrollBy(0, 300));
  await sleep(1000);

  // 找回覆輸入框（嘗試多個 selector）
  const inputSelectors = [
    'div[contenteditable="true"]',
    '[role="textbox"]',
    '[data-lexical-editor="true"]',
  ];

  let input = null;
  for (const sel of inputSelectors) {
    const el = page.locator(sel).first();
    if (await el.count() > 0) { input = el; break; }
  }
  if (!input) throw new Error('找不到回覆輸入框');

  await input.click();
  await sleep(800);

  // 用 insertText 直接貼入（支援中文，不走 IME）
  await page.keyboard.insertText(replyText);
  await sleep(randomBetween(800, 1500));

  // 找發送按鈕（多種可能的文字）
  const btnCandidates = [
    'button:has-text("Post")',
    'button:has-text("發佈")',
    'button:has-text("Reply")',
    'button:has-text("回覆")',
    'button[type="submit"]',
  ];

  let clicked = false;
  for (const sel of btnCandidates) {
    const btn = page.locator(sel).last(); // 用 last() 避免抓到頁首的按鈕
    if (await btn.count() > 0) {
      try {
        await btn.waitFor({ state: 'visible', timeout: 3000 });
        await btn.click();
        clicked = true;
        break;
      } catch {}
    }
  }
  if (!clicked) throw new Error('找不到發送按鈕');
  await sleep(2500);
}

(async () => {
  // 清除可能殘留的瀏覽器鎖定檔
  const lockFile = path.join(PROFILE_DIR, 'SingletonLock');
  try { fs.unlinkSync(lockFile); } catch {}

  writeStatus({ running: true, keywords, totalFound: 0, totalReplied: 0, logs: [], results: [], log: `開始海巡，關鍵字：${keywords.join('、')}` });

  let context;
  try {
    context = await chromium.launchPersistentContext(PROFILE_DIR, {
      headless: false,
      viewport: { width: 1440, height: 900 },
      args: ['--disable-blink-features=AutomationControlled'],
      userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
    });

    await context.addInitScript(() => {
      Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    });

    const replied = loadReplied();
    const todayKey = getTodayKey();
    if (!replied[todayKey]) replied[todayKey] = {};

    const allRepliedUrls = new Set(
      Object.values(replied).flatMap(d => Object.keys(d))
    );

    let totalFound = 0;
    let totalReplied = 0;

    for (const keyword of keywords) {
      writeStatus({ log: `搜尋關鍵字「${keyword}」...` });
      const page = await context.newPage();

      try {
        const posts = await searchPosts(page, keyword);
        const newPosts = posts.filter(p => !allRepliedUrls.has(p.url));

        totalFound += newPosts.length;
        writeStatus({ totalFound, log: `「${keyword}」找到 ${newPosts.length} 篇新貼文` });

        for (const post of newPosts) {
          // 生成回覆
          writeStatus({ log: `生成回覆中...` });
          let reply;
          try {
            reply = await generateReply(post.text);
          } catch (e) {
            writeStatus({ log: `AI 生成失敗：${e.message}` });
            continue;
          }

          // 發送回覆
          writeStatus({ log: `發送回覆中...「${reply.slice(0, 30)}...」` });
          try {
            const replyPage = await context.newPage();
            await replyToPost(replyPage, post.url, reply);
            await replyPage.close();

            replied[todayKey][post.url] = {
              repliedAt: new Date().toISOString(),
              keyword,
              reply,
              postText: post.text.slice(0, 120)
            };
            allRepliedUrls.add(post.url);
            saveReplied(replied);
            totalReplied++;

            writeStatus({
              totalReplied,
              log: `✅ 回覆成功（今天第 ${totalReplied} 則）`,
              result: {
                url: post.url,
                keyword,
                postText: post.text.slice(0, 120),
                reply,
                repliedAt: new Date().toISOString(),
                status: 'success'
              }
            });
          } catch (e) {
            writeStatus({
              log: `❌ 發送失敗：${e.message}`,
              result: {
                url: post.url,
                keyword,
                postText: post.text.slice(0, 120),
                reply,
                repliedAt: new Date().toISOString(),
                status: 'error',
                error: e.message
              }
            });
          }

          await sleep(randomBetween(10000, 20000));
        }

        await page.close();
      } catch (e) {
        writeStatus({ log: `搜尋「${keyword}」失敗：${e.message}` });
        try { await page.close(); } catch {}
      }

      await sleep(randomBetween(4000, 8000));
    }

    writeStatus({ running: false, log: `✅ 海巡完成！找到 ${totalFound} 篇，回覆 ${totalReplied} 則` });
  } catch (e) {
    writeStatus({ running: false, log: `💥 程式錯誤：${e.message}` });
  } finally {
    if (context) await context.close();
  }
})();
