#!/usr/bin/env python3
"""把 Notion「計畫資料庫」的任務匯出成工作台可以匯入的 JSON。

用法：
    python3 scripts/notion-to-workspace.py            # 產出到 ~/Downloads/工作台匯入.json
    python3 scripts/notion-to-workspace.py --open     # 產出後直接開啟所在資料夾

產出的檔案拿到工作台（已登入狀態）的「設定 → 匯入 Notion 任務」選進去即可。
之所以繞這一圈而不是直接寫進資料庫：資料庫規則綁定 Google 帳號，
本機腳本沒有登入身分，寫不進去。走「產檔案 → 在已登入的網頁匯入」比申請服務帳號金鑰簡單也安全。

Notion 欄位 → 工作台欄位的對應：
    Name          → 任務名稱
    日期           → 截止日
    完成狀態        → 待開始 todo / 進行中 doing / 已完成 done
    緊急程度        → 優先級（四個選項名稱完全一致）
    分類           → 會議 → 工作類型「會議」；其餘（日/週/月任務）→「其他」
    （客戶）        → Notion 沒有這個欄位，改從任務名稱關鍵字推斷
"""

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import datetime

SETTINGS = os.path.expanduser("~/.claude/settings.json")
OUT = os.path.expanduser("~/Downloads/工作台匯入.json")

# 任務名稱裡出現這些關鍵字就歸給該客戶。key 要跟工作台客戶清單的「客戶全名」一致。
# 由上往下比，先中的先算，所以具體的要放在籠統的前面。
CLIENT_KEYWORDS = {
    # --- 現役客戶 ---
    "李享家直播集團": ["李享家", "李老闆", "李老板"],
    "漁三": ["漁三", "渔三"],
    "優逸": ["優逸", "优逸"],
    "TOTO": ["toto"],
    "一沐日": ["一沐日"],
    "沐拾": ["沐拾"],
    "十八子肉": ["十八子"],
    "北元當舖": ["北元", "當舖", "當鋪"],
    "工研院": ["工研院"],
    # --- 從 Notion 舊資料判讀出來的客戶（2026-08-09 補建）---
    "MISO": ["miso"],
    "OF AZIKU": ["aziku"],
    "翠芙思": ["翠芙思"],
    "屬於花藝": ["屬於花藝"],          # 要排在「花徑花藝」前面，兩者是不同客戶
    "花徑花藝": ["花徑"],
    "華信航空": ["華信"],
    "焦糖風": ["焦糖風", "焦糖楓"],     # 焦糖楓是打錯字，同一個客戶
    "民視": ["民視"],
    "卡威": ["卡威"],
    "惠盈生技": ["惠盈"],
    "新穎木地板": ["新穎木地板"],
    "歐客佬": ["歐客佬"],
    "柚山": ["柚山", "沪山", "滬山"],   # 常一起出現，視為同一組
    "耀聞水果": ["耀聞水果"],
    "皇后": ["皇后"],
    "露比": ["露比"],
    "長典": ["長典"],
    "阿奇哭": ["阿奇哭"],
    "名絢": ["名絢"],
    "LF": ["lf資訊"],
    # --- 內部行政：放最後，前面都沒中才算內部 ---
    "內部": [
        "禾言", "內部", "公司",
        "廣告成效填寫", "刷卡明細", "報價單歸檔", "成效表單", "專案成效",
        "fb專員", "fb會議", "meta廣告驗證", "名片", "勞報單", "身分證",
        "職代交接", "離職交接", "課程簡報", "廣告教材", "廣告顧問",
        "雲端整理", "結案報告歸檔", "官方line", "會議記錄統整",
        "開會、拜拜", "短影音拍攝",
    ],
}

PRIORITY_MAP = {
    "今日須完成": "today",
    "優先完成": "priority",
    "需在指定時間完成": "scheduled",
    "日常任務": "daily",
}

STATUS_MAP = {"待開始": "todo", "進行中": "doing", "已完成": "done"}


def load_credentials():
    try:
        env = json.load(open(SETTINGS))["env"]
        return env["NOTION_TOKEN"], env["NOTION_TASKS_DB"]
    except (OSError, KeyError) as exc:
        sys.exit(f"讀不到 Notion 憑證（{SETTINGS} 的 env 區塊）：{exc}")


def notion_query(token, db_id):
    """把整個資料庫分頁抓完。"""
    rows, cursor = [], None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        req = urllib.request.Request(
            f"https://api.notion.com/v1/databases/{db_id}/query",
            data=json.dumps(body).encode(),
            headers={
                "Authorization": f"Bearer {token}",
                "Notion-Version": "2022-06-28",
                "Content-Type": "application/json",
            },
        )
        page = json.load(urllib.request.urlopen(req))
        rows += page["results"]
        if not page.get("has_more"):
            return rows
        cursor = page["next_cursor"]


def guess_client(title):
    low = title.lower()
    for name, keywords in CLIENT_KEYWORDS.items():
        if any(k.lower() in low for k in keywords):
            return name
    return ""


# 這些是朱兒目前主力配合的客戶，不管最後一筆任務多久以前都算進行中
ALWAYS_ACTIVE = {"李享家直播集團", "漁三", "優逸", "TOTO", "一沐日", "沐拾",
                 "十八子肉", "北元當舖", "工研院", "內部"}
# 這個日期之後還有任務，就當成還在合作
ACTIVE_SINCE = "2026-07-01"


def collect_clients(tasks):
    """整理出這批資料用到哪些客戶，並依最後活動日期判斷還在不在合作。"""
    last = {}
    for t in tasks:
        name = t["clientName"]
        if not name:
            continue
        due = t["due"] or ""
        if due > last.get(name, ""):
            last[name] = due
    return [
        {
            "name": name,
            "lastSeen": due,
            "active": name in ALWAYS_ACTIVE or due >= ACTIVE_SINCE,
        }
        for name, due in sorted(last.items())
    ]


def guess_missing_clients(tasks, top=15):
    """從沒對到客戶的任務名稱裡，猜出可能是客戶名的詞並統計次數。

    做法很土：取名稱開頭的中文／英文詞，濾掉明顯是動作的字眼。
    目的只是給一份「要不要把這些補進客戶名單」的參考清單，不求精準。
    """
    # 出現在名稱裡就代表這是「動作」不是客戶，整筆跳過
    ACTION_WORDS = (
        "成效填寫 刷卡明細 報價單歸檔 雲端整理 專案成效 短影音拍攝 名片 會議記錄 兩場會議"
    ).split()
    # 客戶名後面常接的業務詞，切掉才會露出客戶名
    TAIL_WORDS = (
        "廣告 報價單 結案報告 會議 貼文 影片 素材 文案 受眾 預算 匯款 名單 粉絲數 權限 訂單 數據 回報 檢查 整理"
    ).split()
    # 純功能性的前綴詞，不是客戶
    NOT_CLIENT = set("廣告 報價 結案 會議 專案 雲端 名片 刷卡 短影 資訊 內容 資料 初稿 登陳".split())

    counter = {}
    for t in tasks:
        if t["clientName"]:
            continue
        name = re.sub(r"^\d+[/\-.]\d+\s*", "", t["name"]).strip()  # 去掉開頭的 8/7 這種日期
        if any(w in name for w in ACTION_WORDS):
            continue
        m = re.match(r"(?:of\s+)?([A-Za-z][A-Za-z0-9]{1,11}|[一-鿿]{2,6})", name, re.I)
        if not m:
            continue
        key = m.group(1).strip()
        # 中文的話從長到短切，看哪個切點後面剛好接業務詞（花徑花藝廣告上線 → 花徑花藝）
        if not key.isascii():
            for n in range(len(key), 1, -1):
                if any(name[n:].startswith(w) for w in TAIL_WORDS):
                    key = key[:n]
                    break
            else:
                key = key[:3]                       # 都對不上就取前三個字當代表
        if len(key) < 2 or key in NOT_CLIENT:
            continue
        counter[key.upper() if key.isascii() else key] = (
            counter.get(key.upper() if key.isascii() else key, 0) + 1
        )

    return [
        {"name": k, "count": v}
        for k, v in sorted(counter.items(), key=lambda kv: -kv[1])[:top]
        if v >= 2
    ]


def convert(page):
    props = page["properties"]
    title = "".join(t["plain_text"] for t in props["Name"]["title"]).strip()
    if not title:
        return None

    status_obj = props.get("完成狀態", {}).get("status") or {}
    urgency_obj = props.get("緊急程度", {}).get("select") or {}
    date_obj = props.get("日期", {}).get("date") or {}
    categories = [m["name"] for m in props.get("分類", {}).get("multi_select", [])]

    status = STATUS_MAP.get(status_obj.get("name"), "todo")
    due = (date_obj.get("start") or "")[:10]

    # Notion 的日期可以帶時間（2026-08-07T14:00:00+08:00），有的話當成預計開始時間
    start_raw = date_obj.get("start") or ""
    plan_time = ""
    if "T" in start_raw:
        try:
            plan_time = datetime.fromisoformat(start_raw).strftime("%H:%M")
        except ValueError:
            plan_time = ""

    return {
        "notionId": page["id"],
        "name": title,
        "area": "work",
        "clientName": guess_client(title),
        "type": "會議" if "會議" in categories else "其他",
        "prio": PRIORITY_MAP.get(urgency_obj.get("name"), "daily"),
        "status": status,
        "due": due,
        "time": plan_time,
        "progress": 100 if status == "done" else 0,
        "note": "／".join(categories),
        "createdAt": page["created_time"],
        "doneAt": page["last_edited_time"] if status == "done" else None,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--open", action="store_true", help="產出後開啟所在資料夾")
    args = ap.parse_args()

    token, db_id = load_credentials()
    print("正在讀取 Notion 計畫資料庫…")
    rows = notion_query(token, db_id)

    tasks = [t for t in (convert(p) for p in rows) if t]
    payload = {
        "source": "notion",
        "exportedAt": datetime.now().isoformat(timespec="seconds"),
        "count": len(tasks),
        # 這批資料用到的客戶，工作台匯入時可以照這份自動建立缺少的
        "clients": collect_clients(tasks),
        # 沒對到客戶的任務，猜一下它們的客戶可能叫什麼，方便決定要不要補進客戶名單
        "unmatchedHints": guess_missing_clients(tasks),
        "tasks": tasks,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)

    # 印一份摘要，方便匯入前先確認有沒有對錯
    from collections import Counter

    by_status = Counter(t["status"] for t in tasks)
    by_prio = Counter(t["prio"] for t in tasks)
    by_client = Counter(t["clientName"] or "（沒對到客戶）" for t in tasks)

    print(f"\n共 {len(tasks)} 筆 → {args.out}\n")
    print("狀態：", dict(by_status))
    print("優先級：", dict(by_prio))
    print("客戶對應：")
    for name, n in by_client.most_common():
        print(f"    {name:>8}  {n}")

    if args.open:
        subprocess.run(["open", "-R", args.out], check=False)


if __name__ == "__main__":
    main()
