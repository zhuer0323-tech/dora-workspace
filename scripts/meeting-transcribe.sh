#!/bin/bash
# 會議錄音 → 逐字稿（本機處理，不上傳網路）
# 用法：scripts/meeting-transcribe.sh <錄音或錄影檔> <輸出資料夾>
# 產出：<輸出資料夾>/transcript.txt（純文字）與 transcript.srt（含時間軸）
# 依賴：ffmpeg（~/.local/bin）、mlx_whisper（uv tool 安裝）

set -euo pipefail

export PATH="$HOME/.local/bin:$PATH"

INPUT="${1:?請提供錄音或錄影檔路徑}"
OUTDIR="${2:?請提供輸出資料夾}"

# 模型：large-v3-turbo，中文辨識品質好、速度快；第一次執行會自動下載（約 1.6GB）
MODEL="mlx-community/whisper-large-v3-turbo"

if [ ! -f "$INPUT" ]; then
  echo "找不到檔案：$INPUT" >&2
  exit 1
fi

mkdir -p "$OUTDIR"
TMPWAV="$(mktemp -t meeting_audio).wav"
trap 'rm "$TMPWAV" 2>/dev/null || true' EXIT

# 不管來源是錄影還是錄音，統一抽出 16kHz 單聲道音訊（Whisper 的標準輸入格式）
echo "[1/2] 轉換音訊格式..."
ffmpeg -y -i "$INPUT" -vn -ac 1 -ar 16000 -c:a pcm_s16le "$TMPWAV" -loglevel error

echo "[2/2] 語音轉文字中（依會議長度，可能需要幾分鐘）..."
mlx_whisper "$TMPWAV" \
  --model "$MODEL" \
  --language zh \
  --initial-prompt "以下是繁體中文的會議逐字稿，內容關於廣告行銷。" \
  --condition-on-previous-text False \
  --hallucination-silence-threshold 2 \
  --output-dir "$OUTDIR" \
  --output-name transcript \
  --output-format all \
  --verbose False

# --output-format 一次只能選一種，改用 all 再清掉用不到的格式
rm "$OUTDIR/transcript.vtt" "$OUTDIR/transcript.tsv" "$OUTDIR/transcript.json" 2>/dev/null || true

echo "完成：$OUTDIR/transcript.txt"
