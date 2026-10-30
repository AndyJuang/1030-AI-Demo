#!/bin/bash
# 啟動個資去除工具：建立環境 → 確認 Ollama 與模型 → 開啟網頁
cd "$(dirname "$0")" || exit 1
PORT=8790
MODEL="${OLLAMA_MODEL:-gemma4}"

if [ ! -d .venv ]; then
  echo "第一次執行，建立 Python 環境…"
  python3 -m venv .venv || { echo "建立失敗：請確認已安裝 Python 3"; exit 1; }
fi
source .venv/bin/activate
if [ ! -f .venv/.installed ] || [ requirements.txt -nt .venv/.installed ]; then
  echo "安裝所需套件（只需要第一次，約 1 分鐘）…"
  pip install -q -r requirements.txt && touch .venv/.installed || exit 1
fi

if ! curl -s http://127.0.0.1:11434/api/tags >/dev/null; then
  echo "啟動 Ollama…"
  nohup ollama serve >/tmp/ollama.log 2>&1 &
  for i in 1 2 3 4 5 6 7 8 9 10; do sleep 1; curl -s http://127.0.0.1:11434/api/tags >/dev/null && break; done
fi
if ! ollama list | awk 'NR>1{print $1}' | grep -q "^${MODEL}\(:\|$\)"; then
  echo "下載模型 ${MODEL}（約 10GB，只需要第一次）…"
  ollama pull "$MODEL" || exit 1
fi

if curl -s "http://127.0.0.1:$PORT/api/health" | grep -q '"types"'; then
  echo "工具已經在執行，直接開啟網頁。"
  [ -z "$NO_OPEN" ] && open "http://127.0.0.1:$PORT"; exit 0
elif lsof -i :$PORT >/dev/null 2>&1; then
  echo "錯誤：埠號 $PORT 已被其他程式占用，請修改 run.sh 裡的 PORT。"; exit 1
fi
echo "啟動完成：http://127.0.0.1:$PORT （要結束請按 Ctrl+C 或關掉這個視窗）"
[ -z "$NO_OPEN" ] && (sleep 2; open "http://127.0.0.1:$PORT") &
exec uvicorn app:app --host 127.0.0.1 --port $PORT
