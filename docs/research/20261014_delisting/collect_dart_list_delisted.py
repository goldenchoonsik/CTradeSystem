"""상폐 회사 DART 공시목록 보완 수집 — 기존 dart_list(corp_cls Y/K)는 현재 상장사만 돌려줘 상폐 회사가 0건.

corp_code 별로 2011-01-01 ~ 폐지일 전체(회사 지정 시 기간 제한 없음) 수집 → raw/dart/list_delisted/{corp_code}_{page:03d}.json
이어받기(있는 파일은 건너뜀), 하루 한도(020)면 멈춤.
  .venv/bin/python docs/research/20261014_delisting/collect_dart_list_delisted.py
"""
import os, sys
import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, ROOT)
from shared.data_library.kr_equity import store
from shared.data_library.kr_equity.store import Dart, DartQuota


def main():
    u = pd.read_csv(store.path("universe.csv"), dtype=str)
    d = u[(u.status == "delisted") & (u.is_spac == "0") & (u.is_preferred == "0")]
    f = pd.read_parquet(store.path("derived", "fundamentals_q.parquet"), columns=["stock_code", "corp_code"]).drop_duplicates()
    m = d.assign(code6=d.code.str[1:]).merge(f, left_on="code6", right_on="stock_code")
    print(f"비스팩 상폐 {len(d)} 중 corp_code 있음 {m.corp_code.nunique()}", flush=True)
    dart, calls = Dart(), 0
    try:
        for r in m.drop_duplicates("corp_code").itertuples():
            page, total = 1, None
            end = r.delist_date.replace("-", "")
            while total is None or page <= total:
                fp = store.path("raw", "dart", "list_delisted", f"{r.corp_code}_{page:03d}.json")
                if os.path.exists(fp):
                    total = (store.read_json(fp) or {}).get("response", {}).get("total_page", 0)
                    page += 1
                    continue
                q = {"corp_code": r.corp_code, "bgn_de": "20110101", "end_de": end, "page_no": page, "page_count": 100}
                st, j = dart.call("list", q)
                calls += 1
                if st not in ("000", "013"):
                    print("실패", r.name, st, flush=True)
                    break
                os.makedirs(os.path.dirname(fp), exist_ok=True)
                dart.save(fp, "list", q, j)
                total = j.get("total_page", 0)
                page += 1
    except DartQuota as e:
        print("하루 한도 — 내일 같은 명령으로 이어받기:", e, flush=True)
    print("호출", calls, flush=True)


if __name__ == "__main__":
    main()
