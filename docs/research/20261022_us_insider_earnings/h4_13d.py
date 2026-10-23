"""H4 5% 이상 지분 공시: 13D(경영 개입 가능 — 행동주의·전략적 투자자) vs 13G(단순 투자 — 대조군) → 제출 다음 날부터 초과수익.

사건 목록: EDGAR 분기 색인(master.gz)의 최초 공시(SC 13D / SCHEDULE 13D, 13G 동일; 정정 /A 제외).
한 접수번호는 대상 회사와 신고인 각각의 CIK 로 여러 줄 → 접수번호로 묶고, 현재 SEC 티커가 있는 CIK 중
13D/13G 전체에 덜 자주 등장하는 쪽(신고인은 여러 회사에 반복 등장)을 대상 회사로 본다.
공시 전 10일 상승·공시일~진입일 반응도 함께 잼(우리가 사기 전에 이미 반영된 몫).
"""
import gzip, json, sys
from collections import Counter
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from h1_insider import S
from event_eval import Evaluator

OUT = Path(__file__).resolve().parent
IDX = S.parent / "raw/edgar_index"


def read_index():
    rows = []
    for p in sorted(IDX.glob("*_master.gz")):
        with gzip.open(p, "rt", encoding="latin-1") as f:
            for line in f:
                parts = line.rstrip("\n").split("|")
                if len(parts) == 5 and ("13D" in parts[2] or "13G" in parts[2]):
                    rows.append(parts)
    df = pd.DataFrame(rows, columns=["cik", "name", "form", "date", "file"])
    df = df[df.cik != "CIK"]
    df["cik"] = df.cik.astype(int)
    df["acc"] = df.file.str.rsplit("/", n=1).str[-1]
    return df


def main():
    idx = read_index()
    freq = Counter(idx.cik)
    orig = idx[idx.form.isin(["SC 13D", "SCHEDULE 13D", "SC 13G", "SCHEDULE 13G"])].copy()
    orig["kind"] = np.where(orig.form.str.contains("13D"), "13D", "13G")
    tk = json.loads((S.parent / "reference/company_tickers_exchange_20261022.json").read_text())
    cik2t = {}
    for cik, _, t, _ in tk["data"]:
        cik2t.setdefault(int(cik), t.replace(".", "-"))

    E = Evaluator()
    ev = []
    for acc, g in orig.groupby("acc"):
        cands = [c for c in g.cik.unique() if cik2t.get(c) in E.cols]
        if not cands:
            continue
        subj = min(cands, key=lambda c: freq[c])
        filers = [c for c in g.cik.unique() if c != subj]
        ev.append((subj, cik2t[subj], pd.Timestamp(g.date.iloc[0]), g.kind.iloc[0], filers[0] if filers else None))
    ev = pd.DataFrame(ev, columns=["cik", "sym", "info_date", "kind", "filer"])
    ev = ev[ev.info_date.between("2006-01-01", "2026-09-30")].sort_values("info_date")
    # 같은 대상에 대한 13D 가 1년 안에 없던 '새' 13D
    ev["new13d"] = False
    last = {}
    for i, c, d, k in zip(ev.index, ev.cik, ev.info_date, ev.kind):
        if k == "13D":
            ev.at[i, "new13d"] = c not in last or (d - last[c]).days > 365
            last[c] = d
    fcount = ev[ev.kind == "13D"].filer.value_counts()
    ev["13D"] = ev.kind == "13D"
    ev["13D 새 대상"] = ev["13D"] & ev.new13d
    ev["13D 반복 신고인(≥10건)"] = ev["13D"] & ev.filer.map(fcount).fillna(0).ge(10).values
    ev["13G(대조)"] = ev.kind == "13G"
    print(f"사건 13D {ev['13D'].sum():,} · 13G {ev['13G(대조)'].sum():,} (현재 티커 있는 대상만)")

    ev = E.prepare(ev)
    # 공시 전 10일 상승, 공시일~진입일 반응
    Xv, n = E.X.values, len(E.dates)
    e0 = E.dates.searchsorted(ev.info_date.values, side="left")
    ev["pre10"] = [np.nansum(Xv[max(0, a - 10):a, c]) for a, c in zip(e0, ev.ci.values)]
    ev["jump"] = [np.nansum(Xv[a:e + 1, c]) for a, e, c in zip(e0, ev.e.values, ev.ci.values)]

    groups = ["13D", "13D 새 대상", "13D 반복 신고인(≥10건)", "13G(대조)"]
    res = E.table(ev, groups)
    res.to_csv(OUT / "h4_13d.csv", index=False)
    pd.set_option("display.width", 220); pd.set_option("display.max_rows", 200)
    print(res.to_string(index=False))
    print("\n공시 전 10일·공시~진입 반응(평균 %):")
    print((ev.groupby(["kind", "size"])[["pre10", "jump"]].mean() * 100).round(2).to_string())
    for g in ["13D 새 대상", "13D"]:
        print(f"{g}·소형 연도별 타이밍(%):", E.yearly(ev, ev[g] & (ev["size"] == 0)))
    print(E.vs_qqq(ev, {"13D 새 대상·소형": ev["13D 새 대상"] & (ev["size"] == 0), "13D 새 대상·전체": ev["13D 새 대상"]}).to_string(index=False))
    ev.to_parquet(S / "h4_events.parquet", index=False)


if __name__ == "__main__":
    main()
