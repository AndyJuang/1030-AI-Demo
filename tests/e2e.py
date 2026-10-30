"""端對端測試：對執行中的伺服器上傳範例檔、偵測、去識別化、讀回檢查。"""
import sys, time, requests, json
sys.path.insert(0, ".")
import docs_io

B = "http://127.0.0.1:8790"

def run(path, mode):
    ext = path[path.rindex("."):]
    with open(path, "rb") as f:
        jid = requests.post(f"{B}/api/analyze", files={"file": (path.split('/')[-1], f)}).json()["job_id"]
    while True:
        j = requests.get(f"{B}/api/jobs/{jid}").json()
        if j["status"] != "analyzing": break
        time.sleep(1)
    assert j["status"] == "ready", j
    print(f"\n=== {path} / {mode} ===  warning={j.get('warning')}")
    for e in j["entities"]:
        print(f"  {e['type']:8} {e['source']:3} x{e['count']}  {e['text']!r}")
    items = [{"text": e["text"], "type": e["type"]} for e in j["entities"]]
    r = requests.post(f"{B}/api/jobs/{jid}/redact", json={"mode": mode, "items": items}).json()
    print("  total", r["total"], "| not_found", r["not_found"], "| residual", r["residual"])
    out = f"/tmp/out_{mode}{ext}"
    open(out, "wb").write(requests.get(B + r["download"]).content)
    print("  --- output text ---")
    print("  " + docs_io.extract_raw(out, ext).replace("\n", "\n  "))
    requests.delete(f"{B}/api/jobs/{jid}")
    return out

if __name__ == "__main__":
    for p in ("samples/範例_申請書.docx", "samples/範例_申請書.pdf"):
        for m in ("label_typed", "fake"):
            run(p, m)
