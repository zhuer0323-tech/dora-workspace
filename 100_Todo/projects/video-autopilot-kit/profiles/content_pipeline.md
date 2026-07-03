# Content Pipeline — 內容類型 + 發布前 checklist

## 內容類型

### 類型 A：IG Reels 短影音（主戰場）
- 平台 / 比例：Instagram Reels，9:16 直式（1080x1920）
- 預設 BGM：`待補`（放進 assets/bgm/ 後填檔名）
- 字幕風格：`[x] 基本`（暫定；要花字再改）
- 開頭 hook 模式：前 3 秒字卡/畫面給出「為什麼要看」（不露臉，用 b-roll + 字卡）
- 結尾：用 `profiles/brand.md` 的字卡 outro

## 發布前 checklist（通用骨架）
- [ ] 素材 fps 對齊 timeline
- [ ] b-roll 已去背景音（只留 BGM/旁白）
- [ ] 畫面跟旁白對得上
- [ ] 通用素材占比 < 主素材
- [ ] 通用素材不重複
- [ ] BGM 短於影片時 → loop 填滿 + 平滑接縫
- [ ] timeline 修剪到旁白真結尾
- [ ] 匯出 player-safe（libx264 + 無 B-frame）
