require('dotenv').config();
const { chromium } = require('playwright');
const Groq = require('groq-sdk');
const fs = require('fs');
const path = require('path');

const CONFIG_FILE = path.join(__dirname, 'config.json');
const COOKIES_FILE = path.join(__dirname, 'cookies.json');
const REPLIED_FILE = path.join(__dirname, 'replied.json');
const LOG_FILE = path.join(__dirname, 'activity.json');

const config = JSON.parse(fs.readFileSync(CONFIG_FILE, 'utf-8'));
const groq = new Groq({ apiKey: process.env.GROQ_API_KEY });

function writeLog(type, message, extra = {}) {
  const logs = fs.existsSync(LOG_FILE)
    ? JSON.parse(fs.readFileSync(LOG_FILE, 'utf-8'))
    : [];
  logs.unshift({ time: new Date().toISOString(), type, message, ...extra });
  if (logs.length > 200) logs.splice(200);
  fs.writeFileSync(LOG_FILE, JSON.stringify(logs, null, 2));
}

function loadReplied() {
  if (!fs.existsSync(REPLIED_FILE)) return {};
  return JSON.parse(fs.readFileSync(REPLIED_FILE, 'utf-8'));
}

function saveReplied(data) {
  fs.writeFileSync(REPLIED_FILE, JSON.stringify(data, null, 2));
}

function getTodayKey() {
  return new Date().toISOString().slice(0, 10);
}

function sleep(ms) {
  return new Promise(r => setTimeout(r, ms));
}

function randomBetween(min, max) {
  return Math.floor(Math.random() * (max - min + 1)) + min;
}

async function generateReply(postContent) {
  const response = await groq.chat.completions.create({
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
- 像真人在說話，不要像官方公告
- 可以提問、分享觀點、表示認同並補充
- 不要一直說「你說得很對」「這很有道理」這類空洞的話
- 不要打廣告或推銷任何東西
- 不要用 emoji
- 只輸出回覆內容本身，不要加任何解釋`
      },
      {
        role: 'user',
        content: `請幫我針對這篇 Threads 貼文寫一則回覆：\n\n${postContent}`
      }
    ]
  });
  return response.choices[0].message.content.trim();
}

async function searchAndReply(browser, keyword, replied, todayKey) {
  const page = await browser.newPage();

  try {
    console.log(`\n🔍 搜尋關鍵字：「${keyword}」`);
    await page.goto(`https://www.threads.net/search?q=${encodeURIComponent(keyword)}&serp_type=default`, {
      waitUntil: 'networkidle',
      timeout: 30000
    });
    await sleep(3000);

    // 捲動一下讓更多貼文載入
    await page.evaluate(() => window.scrollBy(0, 500));
    await sleep(2000);

    // 抓取貼文元素
    const posts = await page.evaluate(() => {
      const results = [];
      // Threads 的貼文連結通常長這樣：/@username/post/XXXX
      const links = document.querySelectorAll('a[href*="/post/"]');
      links.forEach(link => {
        const href = link.href;
        if (!href || results.some(r => r.url === href)) return;

        // 找最近的文字內容
        const container = link.closest('article') || link.closest('[role="article"]') || link.parentElement;
        if (!container) return;

        const textEl = container.querySelector('span, p, div[dir="auto"]');
        const text = textEl ? textEl.innerText.trim() : '';

        if (text.length > 10) {
          results.push({ url: href, text });
        }
      });
      return results.slice(0, 10); // 每個關鍵字最多取 10 篇
    });

    writeLog('search', `搜尋「${keyword}」，找到 ${posts.length} 篇貼文`, { keyword, count: posts.length });
    console.log(`  找到 ${posts.length} 篇貼文`);

    for (const post of posts) {
      // 確認今天的上限
      const todayCount = replied[todayKey] ? Object.keys(replied[todayKey]).length : 0;
      if (todayCount >= config.dailyLimit) {
        writeLog('limit', `今天已達上限 ${config.dailyLimit} 則`);
        console.log(`📊 今天已達上限 ${config.dailyLimit} 則，休息到明天。`);
        return true;
      }

      // 跳過已回覆的
      if (replied[todayKey] && replied[todayKey][post.url]) {
        continue;
      }

      // 跳過之前回覆過的（跨日記錄）
      const allReplied = Object.values(replied).flatMap(d => Object.keys(d));
      if (allReplied.includes(post.url)) {
        continue;
      }

      console.log(`\n  📝 準備回覆：${post.url.slice(0, 60)}...`);
      console.log(`  貼文內容：「${post.text.slice(0, 80)}${post.text.length > 80 ? '...' : ''}」`);

      // 生成 AI 回覆
      let replyText;
      try {
        replyText = await generateReply(post.text);
        console.log(`  💬 生成回覆：「${replyText}」`);
      } catch (err) {
        writeLog('error', `AI 生成失敗：${err.message}`);
        console.error(`  ❌ AI 生成失敗：${err.message}`);
        continue;
      }

      // 打開貼文頁面並回覆
      const replyPage = await browser.newPage();
      try {
        await replyPage.goto(post.url, { waitUntil: 'networkidle', timeout: 30000 });
        await sleep(2000);

        // 找回覆框（Threads 的回覆輸入框）
        const replyInput = await replyPage.locator('div[contenteditable="true"]').first();
        if (!replyInput) {
          writeLog('warn', '找不到回覆輸入框，跳過', { url: post.url });
          console.log('  ⚠️ 找不到回覆輸入框，跳過');
          continue;
        }

        await replyInput.click();
        await sleep(500);

        // 用隨機打字速度輸入，模擬真人
        for (const char of replyText) {
          await replyPage.keyboard.type(char);
          await sleep(randomBetween(30, 120));
        }
        await sleep(randomBetween(500, 1500));

        // 找並點擊發送按鈕
        const sendBtn = await replyPage.locator('button[type="submit"], button:has-text("發佈"), button:has-text("Post")').first();
        if (sendBtn) {
          await sendBtn.click();
          await sleep(2000);

          // 記錄已回覆
          if (!replied[todayKey]) replied[todayKey] = {};
          replied[todayKey][post.url] = {
            repliedAt: new Date().toISOString(),
            keyword,
            reply: replyText,
            postText: post.text.slice(0, 100)
          };
          saveReplied(replied);

          const count = Object.keys(replied[todayKey]).length;
          writeLog('reply', `回覆成功（今天第 ${count} 則）`, {
            keyword, url: post.url,
            postText: post.text.slice(0, 100),
            reply: replyText
          });
          console.log(`  ✅ 回覆成功！今天第 ${count} 則`);
        } else {
          writeLog('warn', '找不到發送按鈕，跳過', { url: post.url });
          console.log('  ⚠️ 找不到發送按鈕，跳過');
        }
      } catch (err) {
        writeLog('error', `回覆失敗：${err.message}`, { url: post.url });
        console.error(`  ❌ 回覆失敗：${err.message}`);
      } finally {
        await replyPage.close();
      }

      // 每次回覆後等待一段時間（模擬真人節奏）
      const waitMin = randomBetween(config.minIntervalMinutes, config.maxIntervalMinutes);
      console.log(`  ⏳ 等待 ${waitMin} 分鐘後繼續...`);
      await sleep(waitMin * 60 * 1000);
    }
  } catch (err) {
    console.error(`  ❌ 搜尋「${keyword}」時發生錯誤：${err.message}`);
  } finally {
    await page.close();
  }
  return false;
}

async function main() {
  if (!fs.existsSync(COOKIES_FILE)) {
    console.error('❌ 找不到登入狀態（cookies.json）');
    console.error('請先執行：node login.js');
    process.exit(1);
  }

  if (!process.env.GROQ_API_KEY) {
    console.error('❌ 請在 .env 檔案中設定 GROQ_API_KEY');
    process.exit(1);
  }

  const cookies = JSON.parse(fs.readFileSync(COOKIES_FILE, 'utf-8'));

  console.log('🚀 Threads 海巡回文工具啟動');
  console.log(`📋 關鍵字：${config.keywords.join('、')}`);
  console.log(`📊 每日上限：${config.dailyLimit} 則`);
  console.log(`⏰ 回覆間隔：${config.minIntervalMinutes}-${config.maxIntervalMinutes} 分鐘\n`);

  const browser = await chromium.launch({
    headless: true,
    args: ['--no-sandbox']
  });

  const context = await browser.newContext({
    userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    viewport: { width: 1280, height: 800 }
  });

  await context.addCookies(cookies);

  while (true) {
    const replied = loadReplied();
    const todayKey = getTodayKey();
    const todayCount = replied[todayKey] ? Object.keys(replied[todayKey]).length : 0;

    console.log(`\n📅 ${new Date().toLocaleString('zh-TW')} — 今天已回覆 ${todayCount}/${config.dailyLimit} 則`);

    if (todayCount >= config.dailyLimit) {
      // 計算到明天 0 點還要等多久
      const now = new Date();
      const tomorrow = new Date(now);
      tomorrow.setDate(tomorrow.getDate() + 1);
      tomorrow.setHours(0, 5, 0, 0); // 明天 00:05
      const waitMs = tomorrow - now;
      console.log(`💤 今天額度已滿，等到明天 ${tomorrow.toLocaleString('zh-TW')} 再繼續`);
      await sleep(waitMs);
      continue;
    }

    let dailyDone = false;
    for (const keyword of config.keywords) {
      dailyDone = await searchAndReply(browser, keyword, loadReplied(), todayKey);
      if (dailyDone) break;
      await sleep(randomBetween(60000, 180000)); // 每個關鍵字之間等 1-3 分鐘
    }

    if (!dailyDone) {
      // 所有關鍵字跑完一輪，等 2-3 小時再跑下一輪
      const waitMin = randomBetween(120, 180);
      console.log(`\n🔄 本輪搜尋完成，等 ${waitMin} 分鐘後下一輪...`);
      await sleep(waitMin * 60 * 1000);
    }
  }
}

main().catch(err => {
  console.error('💥 程式異常中斷：', err);
  process.exit(1);
});
