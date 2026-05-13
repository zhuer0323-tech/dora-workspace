/**
 * 禾言數位行銷服務報價單 — Google Apps Script
 *
 * 設定步驟：
 * 1. 開啟 Google 試算表（新建一份，命名「禾言報價記錄」）
 * 2. 點選上方選單：擴充功能 → Apps Script
 * 3. 刪除原有內容，貼上此腳本全文
 * 4. 點 儲存（Ctrl+S）
 * 5. 點 部署 → 新增部署作業
 *    - 類型：Web 應用程式
 *    - 執行身分：我（你的 Google 帳號）
 *    - 存取權限：所有人
 * 6. 點「部署」→ 複製「網路應用程式網址」
 * 7. 把網址貼到 報價單產生器.html 第一行的 GAS_URL = '' 引號裡
 * 8. 重新上傳 HTML 到 Cloudflare Pages
 */

const SHEET_NAME = '報價記錄';

function generateQuoteNo(sheet) {
  const tz = 'Asia/Taipei';
  const now = new Date();
  const dateStr = Utilities.formatDate(now, tz, 'yyyyMMdd');
  const prefix = dateStr;

  const lastRow = sheet.getLastRow();
  let seq = 0;
  if (lastRow >= 2) {
    const col1 = sheet.getRange(2, 1, lastRow - 1, 1).getValues();
    col1.forEach(([v]) => {
      if (v && String(v).startsWith(prefix)) seq++;
    });
  }
  return prefix + String(seq + 1).padStart(4, '0');
}

function doPost(e) {
  try {
    const data = JSON.parse(e.postData.contents);
    const ss    = SpreadsheetApp.getActiveSpreadsheet();
    let   sheet = ss.getSheetByName(SHEET_NAME);

    if (!sheet) {
      sheet = ss.insertSheet(SHEET_NAME);
      const headers = [
        '報價單編號','品牌','合作內容',
        '未稅金額','稅額','含稅金額',
        '支付期數','抬頭','統一編號',
        '發票地址','收件人','聯絡電話','備註'
      ];
      sheet.appendRow(headers);
      sheet.getRange(1, 1, 1, headers.length)
        .setFontWeight('bold')
        .setBackground('#D9EAD3')
        .setHorizontalAlignment('center');
      sheet.setFrozenRows(1);
      sheet.setColumnWidth(1, 140);
      sheet.setColumnWidth(2, 120);
      sheet.setColumnWidth(3, 260);
      sheet.setColumnWidth(8, 120);
      sheet.setColumnWidth(9, 100);
      sheet.setColumnWidth(10, 200);
      sheet.setColumnWidth(13, 200);
    }

    const quoteNo = generateQuoteNo(sheet);

    sheet.appendRow([
      quoteNo,
      data.brand        || '',
      data.content      || '',
      data.subtotal     || 0,
      data.tax          || 0,
      data.grandTotal   || 0,
      data.payPeriods   || '',
      data.invoiceTitle || '',
      data.clientTax    || '',
      data.clientAddr   || '',
      data.clientContact|| '',
      data.clientPhone  || '',
      data.notes        || '',
    ]);

    return ContentService
      .createTextOutput(JSON.stringify({ status: 'ok', quoteNo }))
      .setMimeType(ContentService.MimeType.JSON);

  } catch (err) {
    return ContentService
      .createTextOutput(JSON.stringify({ status: 'error', message: err.message }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

function doGet(e) {
  return ContentService.createTextOutput('禾言報價單記錄系統運作中 ✅');
}
