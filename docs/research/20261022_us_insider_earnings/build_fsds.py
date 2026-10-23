"""SEC 재무제표 일괄 자료(FSDS) → 10-Q 분기 매출·순이익(이번 분기와 작년 같은 분기, 같은 공시 안의 비교값)·총자산.

같은 10-Q 안에 작년 같은 분기 값이 비교 표시되므로 공시 하나로 전년 대비 변화를 만든다(분기 이어 붙이기·정정 섞임 없음).
연결 기준만(segments·coreg 빈 칸), USD. 10-K 는 4분기 단독값이 없어 제외. 정정 공시(10-Q/A)도 제외.
산출: DataVault/staging/us_sec_bulk/derived/fsds_10q.parquet (공시당 한 줄)
"""
import csv, io, zipfile
from multiprocessing import Pool
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3] / "DataVault/staging/us_sec_bulk"
REV = ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
       "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueGoodsNet", "SalesRevenueServicesNet"]
NI = ["NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"]
TAGS = set(REV + NI + ["Assets"])


def pick(g, tags):
    for t in tags:
        v = g.get(t)
        if v is not None and np.isfinite(v):
            return v
    return np.nan


def one(path):
    with zipfile.ZipFile(path) as z:
        sub = pd.read_csv(z.open("sub.txt"), sep="\t", dtype=str, usecols=["adsh", "cik", "name", "sic", "form", "period", "fp", "filed", "accepted"],
                          quoting=csv.QUOTE_NONE, keep_default_na=False)
        sub = sub[sub.form == "10-Q"]
        keep = set(sub.adsh)
        parts = []
        for ch in pd.read_csv(io.TextIOWrapper(z.open("num.txt"), encoding="utf-8", errors="replace"), sep="\t", dtype=str,
                              usecols=["adsh", "tag", "ddate", "qtrs", "uom", "segments", "coreg", "value"],
                              quoting=csv.QUOTE_NONE, keep_default_na=False, chunksize=2_000_000):
            ch = ch[ch.tag.isin(TAGS) & (ch.uom == "USD") & (ch.segments == "") & (ch.coreg == "") & ch.adsh.isin(keep)]
            parts.append(ch[["adsh", "tag", "ddate", "qtrs", "value"]])
    num = pd.concat(parts)
    num["value"] = pd.to_numeric(num.value, errors="coerce")
    num = num.merge(sub[["adsh", "period"]], on="adsh")
    per = pd.to_datetime(num.period, format="%Y%m%d", errors="coerce")
    dd = pd.to_datetime(num.ddate, format="%Y%m%d", errors="coerce")
    lag = (per - dd).dt.days
    cur = num[(dd == per)]
    prev = num[lag.between(350, 380)]                       # 작년 같은 분기 말
    rows = []
    for adsh, g in cur.groupby("adsh"):
        q1 = g[g.qtrs == "1"].groupby("tag").value.first()
        a0 = g[g.qtrs == "0"].groupby("tag").value.first()
        rows.append((adsh, pick(q1, REV), pick(q1, NI), a0.get("Assets", np.nan)))
    out = pd.DataFrame(rows, columns=["adsh", "rev", "ni", "assets"])
    pr = []
    for adsh, g in prev[prev.qtrs == "1"].groupby("adsh"):
        q1 = g.groupby("tag").value.first()
        pr.append((adsh, pick(q1, REV), pick(q1, NI)))
    out = out.merge(pd.DataFrame(pr, columns=["adsh", "rev_py", "ni_py"]), on="adsh", how="left")
    out = out.merge(sub, on="adsh")
    out["zip"] = path.stem
    return out


def main():
    files = sorted((ROOT / "raw/fsds").glob("*.zip"))
    with Pool(3) as p:
        df = pd.concat(p.map(one, files), ignore_index=True)
    df["filed"] = pd.to_datetime(df.filed, format="%Y%m%d", errors="coerce")
    df["period"] = pd.to_datetime(df.period, format="%Y%m%d", errors="coerce")
    df["cik"] = df.cik.astype(int)
    df = df.sort_values("filed").drop_duplicates(["cik", "period"], keep="first")   # 같은 분기 최초 공시만
    df.to_parquet(ROOT / "derived/fsds_10q.parquet", index=False)
    ok = df.ni.notna() & df.ni_py.notna() & df.assets.notna()
    print(f"10-Q {len(df):,}건 · 순이익 전년비+총자산 있음 {ok.mean():.1%} · 매출 전년비 있음 {(df.rev.notna() & df.rev_py.notna()).mean():.1%}")
    print(df.groupby(df.filed.dt.year).apply(lambda g: pd.Series({"건": len(g), "순이익비교": (g.ni.notna() & g.ni_py.notna()).mean()}), include_groups=False).round(2).T.to_string())


if __name__ == "__main__":
    main()
