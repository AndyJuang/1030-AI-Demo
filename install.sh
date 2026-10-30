#!/usr/bin/env bash
# 一鍵安裝：下載專案 → 安裝 Python 套件 → 安裝 Ollama → 下載 Gemma 模型 → 跑離線測試 → 啟動
# 支援 macOS 與 Linux。可重複執行，已經裝好的步驟會自動略過。
#
# 可用環境變數：
#   INSTALL_DIR  安裝位置（預設 ~/1030-AI-Demo）
#   OLLAMA_MODEL 使用的模型（預設 gemma4）
#   REPO_URL     專案網址（預設官方 repo）
#   SKIP_MODEL=1 不下載模型（只裝程式，測試用）
#   NO_LAUNCH=1  裝完不啟動
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/AndyJuang/1030-AI-Demo.git}"
INSTALL_DIR="${INSTALL_DIR:-$HOME/1030-AI-Demo}"
MODEL="${OLLAMA_MODEL:-gemma4}"
PORT=8790

say()  { printf '\n==> %s\n' "$*"; }
fail() { printf '\n[失敗] %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

OS="$(uname -s)"
case "$OS" in
  Darwin|Linux) ;;
  *) fail "目前只支援 macOS 與 Linux（偵測到：$OS）。Windows 請使用 WSL。" ;;
esac

# ---------- 1. 基本工具：git、Python 3.9+ ----------
say "檢查 git 與 Python"
if ! have git; then
  if [ "$OS" = Darwin ]; then
    fail "找不到 git。請先在終端機執行 xcode-select --install，裝完再重跑本指令。"
  else
    fail "找不到 git。請先用套件管理器安裝（例如 sudo apt install git），再重跑本指令。"
  fi
fi
PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3.9 python3; do
  if have "$c" && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  if [ "$OS" = Darwin ] && have brew; then
    say "安裝 Python（Homebrew）"
    brew install python@3.12
    PY=python3.12
  else
    fail "需要 Python 3.9 以上。macOS 請先安裝 Homebrew（https://brew.sh）或到 python.org 下載；Linux 請用套件管理器安裝 python3 與 python3-venv。"
  fi
fi
echo "使用 $($PY --version)"

# ---------- 2. 下載專案 ----------
say "下載專案到 $INSTALL_DIR"
if [ -d "$INSTALL_DIR/.git" ]; then
  git -C "$INSTALL_DIR" pull --ff-only || echo "（無法自動更新，沿用現有版本）"
elif [ -e "$INSTALL_DIR" ] && [ -n "$(ls -A "$INSTALL_DIR" 2>/dev/null)" ]; then
  fail "$INSTALL_DIR 已存在而且不是本專案。請換一個位置：INSTALL_DIR=/其他/路徑 再執行。"
else
  git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
fi
cd "$INSTALL_DIR"

# ---------- 3. Python 環境與套件 ----------
say "建立 Python 環境並安裝套件"
[ -d .venv ] || "$PY" -m venv .venv || fail "建立虛擬環境失敗。Linux 請先安裝 python3-venv。"
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt
touch .venv/.installed

# ---------- 4. Ollama ----------
say "檢查 Ollama"
if ! have ollama; then
  if [ "$OS" = Darwin ]; then
    have brew || fail "找不到 Homebrew，無法自動安裝 Ollama。請到 https://ollama.com/download 手動安裝後重跑本指令。"
    brew install ollama
  else
    echo "Linux：使用 Ollama 官方安裝程式（可能會要求 sudo 密碼）"
    curl -fsSL https://ollama.com/install.sh | sh
  fi
fi
if ! curl -s http://127.0.0.1:11434/api/tags >/dev/null; then
  echo "啟動 Ollama 服務…"
  nohup ollama serve >"${TMPDIR:-/tmp}/ollama.log" 2>&1 &
  for _ in $(seq 1 20); do
    sleep 1
    curl -s http://127.0.0.1:11434/api/tags >/dev/null && break
  done
  curl -s http://127.0.0.1:11434/api/tags >/dev/null || fail "Ollama 服務無法啟動，請查看 ${TMPDIR:-/tmp}/ollama.log。"
fi

# ---------- 5. 模型 ----------
if [ "${SKIP_MODEL:-0}" = 1 ]; then
  say "略過模型下載（SKIP_MODEL=1）"
elif ollama list | awk 'NR>1{print $1}' | grep -q "^${MODEL}\(:\|$\)"; then
  say "模型 $MODEL 已經存在"
else
  say "下載模型 $MODEL（約 10GB，第一次需要一段時間，請保持網路連線）"
  ollama pull "$MODEL"
fi

# ---------- 6. 離線測試（不需要 AI，確認程式能正常改寫文件） ----------
say "執行離線測試"
python -W ignore tests/test_redact.py
python -W ignore tests/test_hidden.py

# ---------- 7. 啟動 ----------
if [ "${NO_LAUNCH:-0}" = 1 ]; then
  say "安裝完成（未啟動）。日後啟動：cd \"$INSTALL_DIR\" && ./run.sh"
  exit 0
fi
say "啟動工具"
if curl -s "http://127.0.0.1:$PORT/api/health" | grep -q '"types"'; then
  echo "工具已經在執行。"
else
  nohup ./run.sh >server.log 2>&1 &
  for _ in $(seq 1 30); do
    sleep 1
    curl -s "http://127.0.0.1:$PORT/api/health" | grep -q '"types"' && break
  done
  curl -s "http://127.0.0.1:$PORT/api/health" | grep -q '"types"' || fail "工具沒有成功啟動，請查看 $INSTALL_DIR/server.log。"
fi

cat <<EOF

安裝完成。
  網址：http://127.0.0.1:$PORT   （瀏覽器通常已自動開啟）
  位置：$INSTALL_DIR
  日後啟動：macOS 對「啟動個資去除.command」按兩下，或執行 cd "$INSTALL_DIR" && ./run.sh
  停止：pkill -f "uvicorn app:app"
EOF
