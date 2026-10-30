"""農業文件個資去除工具：本機網頁伺服器（只綁定 127.0.0.1，檔案不會離開這台電腦）。"""
import logging
import re
import shutil
import zipfile
import threading
import time
import uuid
from pathlib import Path

import requests
import pymupdf
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

import docs_io
import pii

BASE = Path(__file__).parent
JOBS_DIR = BASE / "jobs"
JOBS_DIR.mkdir(exist_ok=True)
MAX_BYTES = 50 * 1024 * 1024
KEEP_SECONDS = 3600

log = logging.getLogger("uvicorn.error")
for _old in JOBS_DIR.iterdir():  # 啟動時清掉上次殘留的原檔與輸出檔
    if _old.is_dir():
        shutil.rmtree(_old, ignore_errors=True)

app = FastAPI(title="農業文件個資去除工具")
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}


@app.middleware("http")
async def local_only(request: Request, call_next):
    """只接受以本機位址開啟的頁面：擋掉 DNS rebinding，以及其他網站對本機服務送出的上傳請求。"""
    host = request.headers.get("host", "")
    if host.rsplit(":", 1)[0] not in LOCAL_HOSTS and host not in LOCAL_HOSTS:
        return JSONResponse({"detail": "只允許從本機開啟。"}, status_code=403)
    origin = request.headers.get("origin")
    if request.method not in ("GET", "HEAD") and origin and origin.split("://", 1)[-1] != host:
        return JSONResponse({"detail": "不允許跨網站請求。"}, status_code=403)
    return await call_next(request)


def check_file(path, ext):
    """上傳時就檢查檔案能不能處理，用白話回報，不洩漏伺服器路徑。"""
    head = Path(path).read_bytes()[:8]
    if not head:
        return "檔案是空的。"
    if ext == ".docx" and not zipfile.is_zipfile(path):
        return "這不是有效的 Word（.docx）檔，可能已損毀，或副檔名與內容不符。"
    if ext == ".pdf":
        if not head.startswith(b"%PDF"):
            return "這不是有效的 PDF 檔，可能已損毀，或副檔名與內容不符。"
        try:
            with pymupdf.open(path) as doc:
                if doc.needs_pass or doc.is_encrypted:
                    return "這份 PDF 有密碼保護，請先解除密碼再上傳。"
        except Exception:  # noqa: BLE001
            return "這份 PDF 無法開啟，可能已損毀。"
    return None
JOBS = {}
LOCK = threading.Lock()


def purge_old():
    now = time.time()
    for d in JOBS_DIR.iterdir():
        if d.is_dir() and now - d.stat().st_mtime > KEEP_SECONDS:
            shutil.rmtree(d, ignore_errors=True)
            JOBS.pop(d.name, None)


def get_job(job_id):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "找不到這個工作（可能已過期或已清除），請重新上傳。")
    return job


def run_analyze(job):
    try:
        job["text"] = docs_io.extract(job["input"], job["ext"])

        def progress(done, total):
            job["progress"] = {"done": done, "total": total}

        job["entities"], job["warning"] = pii.analyze(job["text"], progress)
        job["status"] = "ready"
    except docs_io.ScannedPdfError as e:
        job["status"], job["error"] = "error", str(e)
    except Exception:  # noqa: BLE001
        log.exception("analyze failed")
        job["status"], job["error"] = "error", "讀取文件失敗，檔案可能損毀或格式特殊。"


@app.get("/api/health")
def health():
    info = {"model": pii.MODEL, "types": pii.TYPES, "ollama": False, "model_ready": False}
    try:
        r = requests.get(f"{pii.OLLAMA_URL}/api/tags", timeout=2)
        names = [m["name"] for m in r.json().get("models", [])]
        info["ollama"] = True
        info["model_ready"] = any(n == pii.MODEL or n.split(":")[0] == pii.MODEL for n in names)
    except requests.RequestException:
        pass
    return info


@app.post("/api/analyze")
async def analyze(file: UploadFile = File(...)):
    purge_old()
    ext = Path(file.filename or "").suffix.lower()
    if ext == ".doc":
        raise HTTPException(400, "不支援舊版 .doc，請在 Word 另存成 .docx 再上傳。")
    if ext not in (".docx", ".pdf"):
        raise HTTPException(400, "只支援 .docx 與 .pdf 檔案。")
    data = await file.read()
    if len(data) > MAX_BYTES:
        raise HTTPException(400, "檔案超過 50MB。")
    job_id = uuid.uuid4().hex[:12]
    folder = JOBS_DIR / job_id
    folder.mkdir()
    src = folder / f"input{ext}"
    src.write_bytes(data)
    problem = check_file(src, ext)
    if problem:
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(400, problem)
    job = {"id": job_id, "ext": ext, "filename": file.filename, "input": str(src),
           "status": "analyzing", "progress": {"done": 0, "total": 0}, "entities": [], "text": ""}
    with LOCK:
        JOBS[job_id] = job
    threading.Thread(target=run_analyze, args=(job,), daemon=True).start()
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    job = get_job(job_id)
    out = {k: job.get(k) for k in ("id", "filename", "status", "progress", "error", "warning")}
    if job["status"] == "ready":
        out["entities"] = job["entities"]
        out["text"] = job["text"][:40000]
        out["truncated"] = len(job["text"]) > 40000
    return out


class Item(BaseModel):
    text: str
    type: str = "OTHER"
    replacement: str = ""


class RedactRequest(BaseModel):
    mode: str = "label_typed"
    items: list[Item]


@app.post("/api/jobs/{job_id}/redact")
def redact(job_id: str, req: RedactRequest):
    job = get_job(job_id)
    if job["status"] != "ready":
        raise HTTPException(409, "偵測尚未完成。")
    if req.mode not in ("label_typed", "label_generic", "fake"):
        raise HTTPException(400, "不明的取代模式。")
    items = [i.model_dump() for i in req.items if i.text.strip()]
    if not items:
        raise HTTPException(400, "沒有選取任何要去除的項目。")
    mapping = pii.build_mapping(items, req.mode)
    ext = job["ext"]
    dst = Path(job["input"]).with_name(f"output{ext}")
    try:
        warnings = []
        counts = docs_io.redact(job["input"], str(dst), ext, mapping, warnings)
        leftovers = docs_io.residual(str(dst), ext, mapping)
        if not docs_io.integrity_ok(job["input"], str(dst), ext, mapping):
            warnings.append("輸出檔的文字與預期不完全一致，可能有誤刪或位移，請把成品和原檔逐頁對照。")
    except Exception as e:  # noqa: BLE001
        log.exception("redact failed")
        raise HTTPException(500, "產生文件時發生錯誤，請確認原檔能正常開啟後再試。") from e
    job["output"] = str(dst)
    stem = Path(job["filename"]).stem
    job["download_name"] = f"{stem}_去識別化{ext}"
    stats = [{"text": k, "replacement": v, "count": counts.get(k, 0)} for k, v in mapping.items()]
    return {
        "download": f"/api/jobs/{job_id}/download",
        "name": job["download_name"],
        "total": sum(counts.values()),
        "stats": stats,
        "not_found": [s["text"] for s in stats if s["count"] == 0],
        "residual": leftovers,
        "warnings": sorted(set(warnings)),
    }


@app.get("/api/jobs/{job_id}/download")
def download(job_id: str):
    job = get_job(job_id)
    if not job.get("output"):
        raise HTTPException(409, "尚未產生輸出檔。")
    return FileResponse(job["output"], filename=job["download_name"])


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str):
    if not re.fullmatch(r"[0-9a-f]{12}", job_id):
        raise HTTPException(400, "不正確的工作代碼。")
    JOBS.pop(job_id, None)
    shutil.rmtree(JOBS_DIR / job_id, ignore_errors=True)
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse(BASE / "static" / "index.html", headers={"Cache-Control": "no-store"})
