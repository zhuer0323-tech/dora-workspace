"""
capcut_helpers.draft_io — Load / save / sync CapCut draft JSON (M18 7-file sync).

關鍵：CapCut Desktop 寫 draft_info.json，讀 draft_content.json，散落 backup 在
.bak / .tmp + Timelines/<UUID>/。任一不同步 → CapCut「載入」時用舊版覆蓋新版改動。
"""
import json
import shutil
from pathlib import Path
from typing import Optional

from .paths import draft_path, discover_all_draft_jsons


def load_draft(project_name: str) -> dict:
    """Load draft_content.json (the canonical UI-visible state)."""
    p = draft_path(project_name) / "draft_content.json"
    if not p.exists():
        raise FileNotFoundError(f"draft_content.json not found at {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def save_draft_with_sync(project_name: str, draft: dict, backup: bool = True) -> list[Path]:
    """Save draft to root + sync to all 7 locations (M18 fix).

    Args:
        project_name: CapCut project folder name
        draft: dict to save as JSON
        backup: If True, save current root → .bak before overwriting (safety net)

    Returns:
        list of paths written
    """
    d = draft_path(project_name)
    root_content = d / "draft_content.json"
    root_info = d / "draft_info.json"

    # Optional backup before write
    if backup and root_content.exists():
        backup_dir = d.parent / f"_backup_{project_name}"
        backup_dir.mkdir(exist_ok=True)
        import time
        ts = time.strftime("%Y%m%d_%H%M%S")
        shutil.copy(root_content, backup_dir / f"draft_content_{ts}.json")

    # Serialize once (separators=(",",":") matches CapCut's compact format)
    blob = json.dumps(draft, ensure_ascii=False, separators=(",", ":"))

    written = []
    # Write root_content (the file CapCut reads)
    root_content.write_text(blob, encoding="utf-8")
    written.append(root_content)

    # Sync to root_info (the file capcut-cli writes — keep matching)
    root_info.write_text(blob, encoding="utf-8")
    written.append(root_info)

    # Sync to Timelines/<UUID>/draft_content.json (per-timeline subfolder)
    timelines = d / "Timelines"
    if timelines.exists():
        for sub in timelines.iterdir():
            if sub.is_dir():
                tl_dc = sub / "draft_content.json"
                if tl_dc.exists():
                    tl_dc.write_text(blob, encoding="utf-8")
                    written.append(tl_dc)

    return written


def set_canvas_portrait(draft: dict, width: int = 1080, height: int = 1920) -> dict:
    """M46 fix — capcut-cli init 預設 canvas 1920×1080 landscape，
    portrait source (rotation=-90) export 出來會 letterboxed 在 landscape frame。
    必須 explicit 改 canvas_config 為 portrait。

    Args:
        draft: full draft dict
        width / height: 預設 1080×1920 (Shorts portrait)

    Returns: the modified canvas_config dict
    """
    cfg = draft.setdefault("canvas_config", {})
    cfg["width"] = width
    cfg["height"] = height
    cfg["ratio"] = "9:16" if width < height else "16:9"
    return cfg


def set_canvas_landscape(draft: dict, width: int = 1920, height: int = 1080) -> dict:
    """Set canvas to landscape (長片 / YT 長片 / IG square 等)."""
    cfg = draft.setdefault("canvas_config", {})
    cfg["width"] = width
    cfg["height"] = height
    cfg["ratio"] = "16:9" if width > height else "1:1" if width == height else "9:16"
    return cfg


def auto_set_canvas(draft: dict, layout: str) -> dict:
    """One-shot canvas setter based on routing decision.

    Args:
        layout: 'portrait' / 'landscape' / 'mixed' (defaults to portrait for safety)
    """
    if layout == "landscape":
        return set_canvas_landscape(draft)
    # portrait default (also for 'mixed' — safer to render portrait)
    return set_canvas_portrait(draft)


def verify_sync(project_name: str) -> dict:
    """Verify all draft JSON copies are identical (M18 health check).

    Returns:
        {
            'all_synced': bool,
            'files_checked': int,
            'mismatched': list[Path],
            'reference_size': int,
        }
    """
    files = discover_all_draft_jsons(project_name)
    json_files = [f for f in files if f.suffix == ".json" and not f.suffix.endswith(".bak")]

    if not json_files:
        return {"all_synced": False, "files_checked": 0, "mismatched": [], "reference_size": 0}

    ref = json_files[0].read_bytes()
    ref_size = len(ref)
    mismatched = []
    for f in json_files[1:]:
        if f.read_bytes() != ref:
            mismatched.append(f)

    return {
        "all_synced": len(mismatched) == 0,
        "files_checked": len(json_files),
        "mismatched": mismatched,
        "reference_size": ref_size,
    }
