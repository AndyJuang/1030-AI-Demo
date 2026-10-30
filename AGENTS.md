# 給 AI Agent 的安裝與使用說明

本檔給 Codex、Claude Code 等 AI Agent 讀。使用者請你「安裝並啟動這個專案」時，照下面做。

## 這個專案是什麼

本機執行的網頁工具。用 Ollama 上的 Gemma 模型找出 Word（.docx）與 PDF 裡的個資，換成標註文字或假資料，再輸出新檔。檔案不會離開使用者的電腦。

## 安裝（一行）

在終端機執行：

```bash
curl -fsSL https://raw.githubusercontent.com/AndyJuang/1030-AI-Demo/main/install.sh | bash
```

腳本會自動完成：

1. 檢查 git 與 Python 3.9 以上（缺少時，macOS 且有 Homebrew 會自動裝 Python）。
2. 把專案下載到 `~/1030-AI-Demo`。
3. 建立 `.venv` 並安裝 `requirements.txt`。
4. 安裝並啟動 Ollama（macOS 用 Homebrew，Linux 用官方安裝程式）。
5. 下載模型 `gemma4`（約 10GB，只有第一次）。
6. 執行離線測試。
7. 啟動工具，網址 http://127.0.0.1:8790。

腳本可重複執行，已完成的步驟會略過。

## Agent 執行時請注意

- 下載模型約 10GB，會跑很久。請用背景方式執行，或把逾時設長（10 分鐘以上）。腳本結尾才會印出「安裝完成」。
- Linux 安裝 Ollama 可能要 sudo 密碼，macOS 沒有 Homebrew 時腳本會停下來並說明。這兩種情況請轉告使用者，不要自行繞過。
- 不要把使用者的真實文件上傳到任何網站。測試請用 `samples/` 裡的虛構範例。
- 最後要向使用者回報：網址、安裝位置、`curl http://127.0.0.1:8790/api/health` 的結果。

## 常用環境變數

| 變數 | 用途 |
| --- | --- |
| `INSTALL_DIR=/路徑` | 改安裝位置 |
| `OLLAMA_MODEL=模型名` | 改用其他模型 |
| `SKIP_MODEL=1` | 不下載模型（只裝程式） |
| `NO_LAUNCH=1` | 裝完不啟動 |

範例：`curl -fsSL .../install.sh | NO_LAUNCH=1 bash`

## 日後啟動與停止

```bash
cd ~/1030-AI-Demo && ./run.sh        # 啟動
pkill -f "uvicorn app:app"           # 停止
```

## 驗證安裝成功

```bash
curl -s http://127.0.0.1:8790/api/health
```

回傳的 JSON 要有 `"ollama": true` 與 `"model_ready": true`。

## 開發者：離線測試

改過程式後都要跑（不需要 AI 模型）：

```bash
.venv/bin/python -W ignore tests/test_redact.py && .venv/bin/python -W ignore tests/test_hidden.py
```

## 程式結構

見 `README.md` 最後的「檔案說明」。
