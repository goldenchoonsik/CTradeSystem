"""임원ㆍ주요주주 소유상황보고서 원문 — 맛보기 표본 수집 (2016~2021 무작위, 하루 한도 남은 만큼).

  .venv/bin/python -I docs/research/20261021_insider_buying/fetch_sample.py [--n 3500]

원천: staging/kr_equity/raw/dart/list·list_delisted. 원본 zip 은 staging/kr_equity/raw/dart/insider_docs/<연도>/<접수번호>.zip
에 한 번만 쓴다(재실행 시 건너뜀). 하루 한도(status 020) 만나면 멈춘다.
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
from shared.data_library.kr_equity.store import dart_key, get, write_once  # noqa: E402

KR = os.path.join(ROOT, "DataVault", "staging", "kr_equity")
DOCS = os.path.join(KR, "raw", "dart", "insider_docs")
OUT = os.path.dirname(os.path.abspath(__file__))
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
                if nm == "임원ㆍ주요주주특정증권등소유상황보고서":
                    rows.append(dict(corp=x["corp_code"], stock=(x.get("stock_code") or "").strip(), name=x["corp_name"],
                                     flr=x.get("flr_nm", ""), dt=x["rcept_dt"], no=x["rcept_no"], src=sub))
    return pd.DataFrame(rows).drop_duplicates("no")


def fp_of(no):
    return os.path.join(DOCS, no[:4], f"{no}.zip")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=3500)
    a = ap.parse_args()
    d = list_rows()
    d = d[(d.stock != "") & (d.dt >= "20160101") & (d.dt <= "20211231")]
    print(f"2016~2021 보고서 {len(d)}건(상장 {int((d.src == 'list').sum())}·상폐 {int((d.src == 'list_delisted').sum())})")
    s = d.sample(n=min(a.n, len(d)), random_state=42).sort_values("dt")
    s.to_csv(os.path.join(OUT, "sample_plan.csv"), index=False)
    key = dart_key()
    todo = [no for no in s.no if not os.path.exists(fp_of(no))]
    print(f"표본 {len(s)}, 받을 것 {len(todo)}", flush=True)
    last, t0, fails = 0.0, time.time(), 0
    for k, no in enumerate(todo, 1):
        wait = GAP - (time.time() - last)
        if wait > 0:
            time.sleep(wait)
        last = time.time()
        r = get("https://opendart.fss.or.kr/api/document.xml", params={"crtfc_key": key, "rcept_no": no})
        if r is None:
            fails += 1
            continue
        try:
            zipfile.ZipFile(io.BytesIO(r.content)).namelist()
        except zipfile.BadZipFile:
            txt = r.content[:400].decode("utf-8", "replace")
            st = re.search(r"<status>(\d+)</status>", txt)
            if st and st.group(1) == "020":
                print(f"하루 한도(020) — {k - 1}건 받고 멈춤", flush=True)
                break
            fails += 1
            continue
        write_once(fp_of(no), r.content)
        if k % 250 == 0:
            print(f"  {k}/{len(todo)} ({(time.time() - t0) / k:.2f}s/건, 실패 {fails})", flush=True)
    have = sum(os.path.exists(fp_of(no)) for no in s.no)
    print(f"완료: 보유 {have}/{len(s)}, 실패 {fails}, {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
