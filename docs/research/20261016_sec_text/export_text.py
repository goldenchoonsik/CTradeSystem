"""301건 변환 텍스트를 파일로 저장(staging/us_filings_sec/text/{티커}/{원본이름}.txt, 섹션 경계 표시) + 확인 페이지용 샘플 JSON"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import metrics_all as M
from multiprocessing import Pool
OUT = M.ROOT + "/text"
def work(rec):
    rl, t = M.text_of(f"{M.ROOT}/raw/{rec['ticker']}/{rec['file']}"); s = M.sections(t)
    os.makedirs(f"{OUT}/{rec['ticker']}", exist_ok=True)
    keep = ["1", "1A", "7", "7A", "8"] if rec["form"] == "10-K" else ["1A", "2", "3"]
    with open(f"{OUT}/{rec['ticker']}/{rec['file']}.txt", "w") as f:
        f.write(f"# {rec['ticker']} {rec['form']} 접수 {rec['filed']} (원본 {rl//1000}KB → 글 {len(t)//1000}KB)\n\n")
        for k in sorted(s, key=lambda k: t.find(s[k][:200])): f.write(f"\n===== Item {k} ({len(s[k])//1000}KB) =====\n{s[k]}\n")
    return rec["ticker"], rec["file"], rl, len(t), {k: len(v) for k, v in s.items()}
if __name__ == "__main__":
    idx = json.load(open(M.ROOT + "/reference/index.json"))
    with Pool(6) as p: r = p.map(work, idx, chunksize=4)
    print(len(r), "files", sum(x[3] for x in r) // 10**6, "MB text")
