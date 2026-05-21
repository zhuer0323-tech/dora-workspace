require('dotenv').config();
const { chromium } = require('playwright');
const path = require('path');

const PROFILE_DIR = path.join(__dirname, 'browser-profile');

(async () => {
  console.log('🔐 開啟瀏覽器，請登入你的 Threads 帳號...\n');

  const context = await chromium.launchPersistentContext(PROFILE_DIR, {
    headless: false,
    viewport: { width: 1440, height: 900 },
    args: ['--disable-blink-features=AutomationControlled'],
    userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
  });

  await context.addInitScript(() => {
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
  });

  const page = await context.newPage();
  await page.goto('https://www.threads.com/login');

  console.log('每 10 秒偵測一次是否登入成功（最長等 5 分鐘）...\n');

  let waited = 0;
  while (waited < 300) {
    await page.waitForTimeout(10000);
    waited += 10;

    const cookies = await context.cookies();
    const loggedIn = cookies.some(c => c.name === 'sessionid');

    if (loggedIn) {
      console.log('✅ 登入成功！登入狀態已永久儲存到 browser-profile/');
      console.log('之後不需要重新登入，直接執行 node server.js 就好。');
      await context.close();
      process.exit(0);
    }

    console.log(`  ${waited}s — 等待登入中...`);
  }

  console.error('❌ 等待逾時，請重新執行 node login.js');
  await context.close();
  process.exit(1);
})();
