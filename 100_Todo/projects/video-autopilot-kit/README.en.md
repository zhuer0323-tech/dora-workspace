# 🎬 video-autopilot-kit

> A **framework**, not a hand-me-down config. Reusable CapCut + ffmpeg video-automation
> code, plus a questionnaire that asks about **your** channel and turns the system into yours.
>
> ⚠️ **Ships with zero of the original author's private data** — voice, strategy, and
> community numbers are all **blank templates** you fill in yourself.

*(中文版見 [README.md](README.md))*

## ▶️ See it run in 60 seconds (no CapCut, no real media)

Want to see it actually move first? `examples/` has self-contained, runnable demos —
they synthesize test media with ffmpeg, so you need no real footage and no CapCut:

```bash
python examples/01_vertical_short.py      # synthesized clips → a finished 1080x1920 Short
python examples/02_caption_broll_match.py # zero-config: name b-roll by content, captions auto-align
```

Needs Python 3.9+ and `ffmpeg`/`ffprobe` (only example 01 needs ffmpeg). See [`examples/README.md`](examples/README.md).

## Why this is different

Most "creator systems" either sell you **someone else's setup** (useless to you, sometimes
misleading) or stay too generic to have real methodology. This kit gives you the **skeleton**
(a battle-tested structure); `SETUP.md` **asks you questions** one section at a time, and
your answers fill it in — so it actually becomes **your** system.

## What's inside

| Folder | What |
|---|---|
| ⭐ `src/capcut_helpers/` | **Primary editing path** — CapCut Desktop automation (draft I/O, 4-level mute, captions/effects, post-export ffmpeg, AI-subtitle fixes, b-roll ratio + sync audit). **Driven by an AI assistant + Computer Use operating the CapCut window** (see Requirements) |
| `src/silent_vlog_maker/` | **Secondary path (not the default)** — pure ffmpeg pipeline, **only for silent (no-voiceover) vlogs + post-processing CapCut exports** (content audit, asset normalize, KenBurns, subtitle burn). For normal edits, use CapCut |
| `knowledge/` | **Video-production knowledge base** — M1-M103 pitfall compendium + algorithm + SOP + editing craft |
| ▶️ `examples/` | **Self-contained runnable demos** — ffmpeg-synthesized media; see the pipeline work in 60s (no CapCut/real footage) |
| ⭐ `SETUP.md` | **Start here** — answer questions to make the system yours |
| `templates/` | Blank fill-in templates: voice / brand / algorithm / community / pipeline / context |
| `config.example.py` | Path config (env vars; **no account names** — auto-detects current user) |

## Quick start

1. Read **`SETUP.md`** → fill `templates/*.template.md` into `profiles/*.md`
   (or hand the repo to Claude / ChatGPT: *"ask me the SETUP.md questions and generate my profiles/"*)
2. `cp config.example.py config.py` → set your CapCut / asset / export paths
3. **Install CapCut Desktop + enable your AI assistant's Computer Use** — the primary editing path is the AI operating the CapCut window, so **it won't run without Computer Use** (see Requirements)
4. Use the tools in `src/`

## Requirements

> ⚠️ **The primary tool is CapCut, not ffmpeg.** CapCut has no public API — automation works by an **AI assistant using Computer Use to operate the CapCut window** (click buttons, apply templates, export). ffmpeg only handles post-export processing and silent vlogs.

**Primary path (CapCut — the default)**
- **CapCut Desktop** (Pro is better) — all main editing / captions / templates happen here
- **AI assistant + Computer Use** (Claude Desktop / Claude Code, etc.) — ⚠️ **required**. CapCut automation = the AI driving the GUI via Computer Use; **without Computer Use, `capcut_helpers` cannot drive CapCut**
- Python 3.9+
- `ffmpeg` / `ffprobe` on PATH — for post-export: BGM loop / trim-to-voice-end / player-safe re-encode

**Secondary path (pure ffmpeg — only for silent, no-voiceover vlogs)**
- Use `silent_vlog_maker` only when making a silent (no-voiceover) vlog. **This is not the default** — for normal videos, use CapCut.

*(optional)* an AI assistant can also auto-generate your profiles from your `SETUP.md` answers.

## Philosophy

The most valuable part of a creator system is the **structure and methodology**, not one
person's private numbers. So this repo gives you the bones; you fill them with your own flesh.

## License

MIT — keep the notice and use / modify / sell freely.

## Author

Hao0321 Studio — an open-source framework distilled from a real personal creator system.
