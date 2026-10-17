"""Loughran-McDonald 정식 사전(pysentiment2 패키지에 동봉된 LM.csv, 어간 처리 없이 원형 일치)으로 MD&A·위험요인 지표 재계산. 출력 metrics_lm.csv"""
import re, json, os, sys, csv
from multiprocessing import Pool
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import metrics_all as M
D = os.path.dirname(os.path.abspath(__file__))
CATS = ["Negative", "Positive", "Uncertainty", "Litigious", "Constraining"]
def load():
    p = os.environ["LM_CSV"]; d = pd.read_csv(p)
    return {c: set(d[d[c] > 0]["Word"].str.lower()) for c in CATS}
LM = load()
def work(rec):
    t = M.text_of(f"{M.ROOT}/raw/{rec['ticker']}/{rec['file']}")[1]; s = M.sections(t)
    out = {k: rec[k] for k in ("ticker", "form", "filed", "accession")}
    for nm, txt in (("mda", s.get("7", "") if rec["form"] == "10-K" else s.get("2", "")), ("rf", s.get("1A", ""))):
        w = re.findall(r"[a-z]+", txt.lower()); n = max(len(w), 1); out[f"{nm}_n"] = len(w)
        for c in CATS: out[f"{nm}_{c[:3].lower()}"] = sum(x in LM[c] for x in w) / n * 1000
        out[f"{nm}_tone"] = (out[f"{nm}_pos"] - out[f"{nm}_neg"]) / max(out[f"{nm}_pos"] + out[f"{nm}_neg"], 1e-9) if len(w) > 500 else float("nan")
    return out
if __name__ == "__main__":
    import json as J
    idx = J.load(open(M.ROOT + "/reference/index.json"))
    with Pool(6) as p: rows = p.map(work, idx, chunksize=4)
    pd.DataFrame(rows).sort_values(["ticker", "form", "filed"]).to_csv(f"{D}/metrics_lm.csv", index=False); print(len(rows))
