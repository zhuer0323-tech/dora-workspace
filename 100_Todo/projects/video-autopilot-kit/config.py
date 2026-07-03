"""
config.example.py — copy to `config.py` and fill in YOUR paths.

(Or just set these as environment variables — paths.py reads them either way.)
Nothing here hardcodes an account name; defaults auto-detect the current user.
Import this once at startup if you prefer a file over env vars.
"""
import os
from pathlib import Path

# ── Your video project workspace (where your assets/ videos/ live) ──
os.environ.setdefault("VIDEO_KIT_PROJECT_ROOT", str(Path(__file__).resolve().parent))

# ── CapCut Desktop user-data root (leave commented to auto-detect current user) ──
# os.environ["CAPCUT_USER_DATA"] = r"C:\Users\<you>\AppData\Local\CapCut\User Data"

# ── capcut-cli npm shim (only needed for the CapCut JSON automation) ──
# os.environ["CAPCUT_CLI"] = r"C:\Users\<you>\AppData\Roaming\npm\capcut.cmd"

# ── Optional: where your BGM / fonts / b-roll live (else <project>/assets/...) ──
# os.environ["VIDEO_KIT_PROJECT_ROOT"] already covers assets/bgm, assets/fonts
