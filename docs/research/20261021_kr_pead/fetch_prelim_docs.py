"""DART 영업(잠정)실적(공정공시) 원문 수집 — 회사·분기별 첫 공시만 (한국판 PEAD 연구용).

  .venv/bin/python -I docs/research/20261021_kr_pead/fetch_prelim_docs.py            # 개발 구간(접수 ≤ 2024-02-29) 먼저, 이어서 나머지
  .venv/bin/python -I docs/research/20261021_kr_pead/fetch_prelim_docs.py --plan     # 받을 목록만 만들고 건수 출력
  .venv/bin/python -I docs/research/20261021_kr_pead/fetch_prelim_docs.py --followup # 첫 문서에 영업이익이 없던 (회사,분기)는 같은 분기 다음 잠정 공시를 받는다(parse 후, 최대 3회)

원천: staging/kr_equity/raw/dart/list(상장사)·list_delisted(상폐사) 공시 목록. 원본 zip 은 무수정으로
staging/kr_equity/raw/dart/prelim_docs/<연도>/<접수번호>.zip 에 한 번만 쓴다(재실행 시 건너뜀, 재개 가능).
선정: 보고서명에 '잠정' 포함·정정/자회사/월별 제외, 접수월로 분기 배정(1~3월=전년 4분기, 4~6월=1분기, 7~9월=2분기,
10~12월=3분기), (회사, 분기)별 가장 이른 접수 1건. 하루 한도(status 020) 만나면 멈춘다 — 다음 날 같은 명령.
"""
import argparse
import glob
import io
import json
import os
import re
import sys
import time
import zipfile

import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, ROOT)
from shared.data_library.kr_equity.store import dart_key, get, log, write_once  # noqa: E402

KR = os.path.join(ROOT, "DataVault", "staging", "kr_equity")
DOCS = os.path.join(KR, "raw", "dart", "prelim_docs")
OUT = os.path.dirname(os.path.abspath(__file__))
PLAN = os.path.join(OUT, "prelim_plan.csv")
CAND = os.path.join(OUT, "prelim_candidates.csv")                          # (회사,분기)별 모든 후보(접수순 rank)
FIRST, DEV_END, LAST = "20170101", "20240229", "20260930"
GAP = 0.3


def list_rows():
    rows = []
    for sub in ("list", "list_delisted"):
        for f in glob.glob(os.path.join(KR, "raw", "dart", sub, "*.json")):
            try:
                rr = json.load(open(f, encoding="utf-8"))["response"]
            except Exception:
                continue
            for x in rr.get("list", []) or []:
                nm = re.sub(r"\s+", "", x.get("report_nm", ""))
                if "잠정" in nm and "실적" in nm:
                    rows.append(dict(corp=x["corp_code"], stock=(x.get("stock_code") or "").strip(), name=x["corp_name"],
                                     nm=nm, dt=x["rcept_dt"], no=x["rcept_no"], src=sub))
    return pd.DataFrame(rows).drop_duplicates("no")


def plan():
    d = list_rows()
    n0 = len(d)
    d = d[~d.nm.str.startswith("[") & ~d.nm.str.contains("자회사") & ~d.nm.str.contains("월별") & (d.stock != "")]
    d = d[(d.dt >= FIRST) & (d.dt <= LAST)].copy()
    m, y = d.dt.str[4:6].astype(int), d.dt.str[:4].astype(int)
    d["fy"] = (y - (m <= 3)).astype(int)
    d["fq"] = ((m - 1) // 3).replace(0, 4).astype(int)
    d = d.sort_values(["dt", "no"])
    d["rank"] = d.groupby(["corp", "fy", "fq"]).cumcount()
    d.assign(dev=d.dt <= DEV_END).to_csv(CAND, index=False)
    d = d[d["rank"] == 0]
    d["dev"] = d.dt <= DEV_END
    d = d.sort_values(["dev", "dt"], ascending=[False, True]).reset_index(drop=True)
    d.to_csv(PLAN, index=False)
    print(f"잠정 공시 {n0} → 선정 {len(d)} (개발 {int(d.dev.sum())}, 이후 {int((~d.dev).sum())}), 회사 {d.corp.nunique()}")
    return d


def fp_of(no):
    return os.path.join(DOCS, no[:4], f"{no}.zip")


def fetch(d):
    key = dart_key()
    todo = [no for no in d.no if not os.path.exists(fp_of(no))]
    print(f"받을 것 {len(todo)} / {len(d)}")
    fails, last, t0 = [], 0.0, time.time()
    for k, no in enumerate(todo, 1):
        wait = GAP - (time.time() - last)
        if wait > 0:
            time.sleep(wait)
        last = time.time()
        r = get("https://opendart.fss.or.kr/api/document.xml", params={"crtfc_key": key, "rcept_no": no})
        if r is None:
            fails.append((no, "network"))
            continue
        try:
            zipfile.ZipFile(io.BytesIO(r.content)).namelist()
        except zipfile.BadZipFile:
            txt = r.content[:400].decode("utf-8", "replace")
            st = re.search(r"<status>(\d+)</status>", txt)
            if st and st.group(1) == "020":
                log(f"[prelim_docs] 하루 한도 — {k - 1}건 받고 멈춤", "pead_fetch_log.txt")
                print(f"하루 한도(020) — {k - 1}건 받고 멈춤. 다음 날 같은 명령으로 이어받기")
                break
            fails.append((no, st.group(1) if st else txt[:80]))
            continue
        os.makedirs(os.path.dirname(fp_of(no)), exist_ok=True)
        write_once(fp_of(no), r.content)
        if k % 500 == 0:
            print(f"  {k}/{len(todo)} ({(time.time() - t0) / k:.2f}s/건, 실패 {len(fails)})", flush=True)
    pd.DataFrame(fails, columns=["no", "reason"]).to_csv(os.path.join(OUT, "prelim_fetch_fail.csv"), index=False)
    have = sum(os.path.exists(fp_of(no)) for no in d.no)
    print(f"완료: 보유 {have}/{len(d)}, 이번 실패 {len(fails)}, {time.time() - t0:.0f}s")


def followup():
    """parse_prelim.py 결과에서 영업이익 값이 없는 (회사,분기) → 같은 분기 다음 후보 1건씩 받기."""
    c = pd.read_csv(CAND, dtype={"no": str, "corp": str, "stock": str, "dt": str})
    p = pd.read_parquet(os.path.join(OUT, "prelim_parsed.parquet"))
    have = c.merge(p[["no", "op_raw"]], on="no", how="left")
    have["fetched"] = [os.path.exists(fp_of(n)) for n in have.no]
    g = have.groupby(["corp", "fy", "fq"])
    done = g.apply(lambda x: x.op_raw.notna().any(), include_groups=False)
    need = done[~done].index
    nxt = have.set_index(["corp", "fy", "fq"]).loc[need].reset_index()
    nxt = nxt[~nxt.fetched].sort_values("rank").drop_duplicates(["corp", "fy", "fq"])
    nxt = nxt[nxt["rank"] <= 3]
    print(f"영업이익 없는 (회사,분기) {len(need)} → 다음 후보 받기 {len(nxt)}")
    fetch(nxt.sort_values(["dev", "dt"], ascending=[False, True]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--followup", action="store_true")
    a = ap.parse_args()
    if a.followup:
        return followup()
    d = plan()
    if not a.plan:
        fetch(d)


if __name__ == "__main__":
    main()
