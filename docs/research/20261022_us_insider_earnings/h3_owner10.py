"""H3 10% 대주주(임원·이사 아님) 장내 매수 → 이후 초과수익. 사모펀드·지주사·행동주의 펀드 등 '조직' 매수.

묶음(사전 고정): 전체 / $100만 이상 / 처음 매수(그 회사에서 이 신고인의 첫 장내 매수) / 임원 매수와 같은 30일 안(겹침).
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from h1_insider import S
from event_eval import Evaluator

OUT = Path(__file__).resolve().parent


def main():
    f = pd.read_parquet(S / "form4_ps.parquet")
    f = f[f.document_type.isin(["4", "4/A"]) & (f.trans_code == "P") & (f.trans_shares > 0) & (f.trans_pricepershare > 0) & f.filing_date.notna()]
    f = f.drop_duplicates(["owner_cik", "issuercik", "trans_date", "trans_shares", "trans_pricepershare"])
    rel = f.relationship.fillna("")
    big = f[rel.str.contains("TenPercentOwner") & ~rel.str.contains("Director|Officer")].copy()
    ins = f[rel.str.contains("Director|Officer")]
    big["value"] = big.trans_shares * big.trans_pricepershare
    big = big.sort_values("filing_date")
    big["first"] = ~big.duplicated(["owner_cik", "issuercik"])
    ev = big.groupby(["issuercik", "filing_date"]).agg(value=("value", "sum"), first=("first", "any"), sym=("issuertradingsymbol", "first")).reset_index()
    # 같은 30일 안 임원 매수 겹침
    ins_d = ins.groupby("issuercik").filing_date.apply(lambda s: np.sort(s.values))
    def near(c, d):
        a = ins_d.get(c)
        if a is None: return False
        i = np.searchsorted(a, d - np.timedelta64(30, "D"))
        return i < len(a) and a[i] <= d + np.timedelta64(30, "D")
    ev["with_insider"] = [near(c, d) for c, d in zip(ev.issuercik, ev.filing_date.values)]

    m = pd.read_parquet(S / "insider_buy_events.parquet")[["issuercik", "cik_sym"]].dropna().drop_duplicates("issuercik").set_index("issuercik").cik_sym
    import json
    tk = json.loads((S.parent / "reference/company_tickers_exchange_20261022.json").read_text())
    cik2t = {}
    for cik, _, t, _ in tk["data"]:
        cik2t.setdefault(int(cik), t.replace(".", "-"))
    ev["sym"] = ev.issuercik.map(lambda c: cik2t.get(int(c)) if pd.notna(c) else None)
    ev = ev.dropna(subset=["sym"]).rename(columns={"filing_date": "info_date"})
    ev["전체"] = True
    ev["$100만 이상"] = ev.value >= 1e6
    ev["처음 매수"] = ev["first"]
    ev["임원 매수와 겹침"] = ev.with_insider
    ev["대주주 단독"] = ~ev.with_insider

    E = Evaluator()
    ev = E.prepare(ev)
    groups = ["전체", "$100만 이상", "처음 매수", "임원 매수와 겹침", "대주주 단독"]
    res = E.table(ev, groups)
    res.to_csv(OUT / "h3_owner10.csv", index=False)
    pd.set_option("display.width", 220); pd.set_option("display.max_rows", 200)
    print(res.to_string(index=False))
    print("\n대주주 단독·소형 연도별 타이밍(%):", E.yearly(ev, ev["대주주 단독"] & (ev["size"] == 0)))
    print("전체·소형 연도별 타이밍(%):", E.yearly(ev, ev["size"] == 0))
    print(E.vs_qqq(ev, {"10%대주주·소형": ev["size"] == 0, "10%대주주·전체": ev["전체"]}).to_string(index=False))


if __name__ == "__main__":
    main()
