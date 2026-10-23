"""SEC Form 3/4/5 분기 zip → 장내 매수(P)·매도(S) 거래 한 표.

원본 zip 은 건드리지 않고 읽기만 한다. 공동 신고(한 접수번호에 신고인 여럿)는 접수번호당 한 줄로 합쳐
첫 신고인 + 신고인 수 + 관계 합집합을 남긴다(같은 거래가 신고인 수만큼 불어나는 것 방지).
산출: DataVault/staging/us_sec_bulk/derived/form4_ps.parquet
"""
import csv, io, zipfile
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[3] / "DataVault/staging/us_sec_bulk"
COLS = {
    "SUBMISSION": ["ACCESSION_NUMBER", "FILING_DATE", "DOCUMENT_TYPE", "ISSUERCIK", "ISSUERNAME", "ISSUERTRADINGSYMBOL", "AFF10B5ONE"],
    "REPORTINGOWNER": ["ACCESSION_NUMBER", "RPTOWNERCIK", "RPTOWNERNAME", "RPTOWNER_RELATIONSHIP", "RPTOWNER_TITLE"],
    "NONDERIV_TRANS": ["ACCESSION_NUMBER", "SECURITY_TITLE", "TRANS_DATE", "TRANS_CODE", "TRANS_SHARES", "TRANS_PRICEPERSHARE",
                       "TRANS_ACQUIRED_DISP_CD", "SHRS_OWND_FOLWNG_TRANS", "DIRECT_INDIRECT_OWNERSHIP"],
}


def read(z, name):
    with z.open(f"{name}.tsv") as f:
        df = pd.read_csv(io.TextIOWrapper(f, encoding="utf-8", errors="replace"), sep="\t", dtype=str,
                         quoting=csv.QUOTE_NONE, keep_default_na=False, on_bad_lines="warn")
    return df[[c for c in COLS[name] if c in df.columns]]


def one(path):
    with zipfile.ZipFile(path) as z:
        tr = read(z, "NONDERIV_TRANS")
        tr = tr[tr.TRANS_CODE.isin(["P", "S"])]
        sub = read(z, "SUBMISSION")
        own = read(z, "REPORTINGOWNER")
    own = own.groupby("ACCESSION_NUMBER").agg(
        owner_cik=("RPTOWNERCIK", "first"), owner_name=("RPTOWNERNAME", "first"),
        relationship=("RPTOWNER_RELATIONSHIP", lambda s: ",".join(sorted({x for v in s for x in v.split(",") if x}))),
        title=("RPTOWNER_TITLE", "first"), n_owners=("RPTOWNERCIK", "size")).reset_index()
    df = tr.merge(sub, on="ACCESSION_NUMBER", how="left").merge(own, on="ACCESSION_NUMBER", how="left")
    df["quarter"] = path.stem.split("_")[0]
    return df


def main():
    files = sorted((ROOT / "raw/form345").glob("*_form345.zip"))
    df = pd.concat([one(p) for p in files], ignore_index=True)
    for c in ["FILING_DATE", "TRANS_DATE"]:
        df[c] = pd.to_datetime(df[c], format="%d-%b-%Y", errors="coerce")
    for c in ["TRANS_SHARES", "TRANS_PRICEPERSHARE", "SHRS_OWND_FOLWNG_TRANS"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df.columns = [c.lower() for c in df.columns]
    df["issuercik"] = pd.to_numeric(df["issuercik"], errors="coerce").astype("Int64")
    (ROOT / "derived").mkdir(exist_ok=True)
    df.to_parquet(ROOT / "derived/form4_ps.parquet", index=False)
    print(f"{len(files)}개 분기 → {len(df):,}행 (P {int((df.trans_code == 'P').sum()):,} / S {int((df.trans_code == 'S').sum()):,})")
    print(df.groupby(df.filing_date.dt.year).trans_code.value_counts().unstack().to_string())


if __name__ == "__main__":
    main()
