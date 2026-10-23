"""임원 장내 매수 사건 중 Yahoo 가격(현재 상장 종목만)이 있는 비율 = 생존 편향 크기 측정.

사건 = (발행사 CIK, 거래일) 단위, 임원·이사 신고(10% 대주주 단독 제외), Form 4·4/A, 금액 > 0.
매칭은 두 길:
  CIK  — 발행사 CIK 가 현재 SEC 티커 목록에 있고 그 티커의 Yahoo 일봉이 사건일을 덮음
  기호 — 신고 당시 기호로 Yahoo 일봉을 찾음(티커 재사용 위험 → 거래일 종가와 신고 단가가 0.8~1.25배인지 검증)
Yahoo raw 일봉은 무수정가라 신고 단가와 바로 비교 가능.
"""
import json
from pathlib import Path
import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parents[3] / "DataVault"
S = BASE / "staging/us_sec_bulk"
YH = BASE / "raw/yahoo/candle_1d"


def span(path):
    with open(path, "rb") as f:
        f.readline()
        first = f.readline()[:10].decode()
        f.seek(0, 2)
        f.seek(max(0, f.tell() - 200))
        last = f.read().splitlines()[-1][:10].decode()
    return first, last


def close_on(sym, dates, cache={}):
    if sym not in cache:
        p = YH / f"{sym}_1d.csv"
        cache[sym] = pd.read_csv(p, usecols=["timestamp", "close"], parse_dates=["timestamp"]).dropna().set_index("timestamp").close if p.exists() else None
    s = cache[sym]
    if s is None or s.empty:
        return np.full(len(dates), np.nan)
    i = s.index.searchsorted(dates, side="right") - 1
    out = np.where(i >= 0, s.values[np.clip(i, 0, None)], np.nan)
    return out


def main():
    df = pd.read_parquet(S / "derived/form4_ps.parquet")
    p = df[(df.trans_code == "P") & df.document_type.isin(["4", "4/A"])
           & df.relationship.str.contains("Director|Officer", na=False)
           & (df.trans_shares > 0) & (df.trans_pricepershare > 0)].copy()
    p["value"] = p.trans_shares * p.trans_pricepershare
    ev = p.groupby(["issuercik", "trans_date"]).agg(
        filing_date=("filing_date", "min"), symbol=("issuertradingsymbol", "first"), value=("value", "sum"),
        price=("trans_pricepershare", "median"), n_insiders=("owner_cik", "nunique")).reset_index()
    ev = ev[ev.trans_date.between("2006-01-01", "2026-09-30")]

    tk = json.loads((S / "reference/company_tickers_exchange_20261022.json").read_text())
    cik2t = {}
    for cik, _, t, _ in tk["data"]:
        cik2t.setdefault(int(cik), t.replace(".", "-"))
    spans = {f.name[:-7]: span(f) for f in YH.glob("*_1d.csv")}

    def covered(sym, d):
        s = spans.get(sym)
        return s is not None and s[0] <= d.strftime("%Y-%m-%d") <= s[1]

    ev["cik_sym"] = ev.issuercik.map(lambda c: cik2t.get(int(c)) if pd.notna(c) else None)
    ev["sym_hist"] = ev.symbol.str.upper().str.strip().str.replace(".", "-", regex=False)
    ev["by_cik"] = [covered(s, d) if s else False for s, d in zip(ev.cik_sym, ev.trans_date)]
    ev["by_sym"] = [covered(s, d) if isinstance(s, str) and s else False for s, d in zip(ev.sym_hist, ev.trans_date)]

    # 기호 매칭 검증: 거래일(또는 직전) 종가 대비 신고 단가
    ev["ratio"] = np.nan
    m = ev.by_sym & ~ev.by_cik
    for sym, g in ev[m].groupby("sym_hist"):
        ev.loc[g.index, "ratio"] = g.price.values / close_on(sym, g.trans_date.values)
    ev["sym_ok"] = m & ev.ratio.between(0.8, 1.25)
    ev["priced"] = ev.by_cik | ev.sym_ok

    ev["year"] = ev.trans_date.dt.year
    ev["big"] = ev.value >= 100_000
    t = ev.groupby("year").agg(events=("priced", "size"), cik=("by_cik", "mean"), sym_ok=("sym_ok", "mean"),
                               priced=("priced", "mean"), big_events=("big", "sum"),
                               big_priced=("priced", lambda s: s[ev.loc[s.index, "big"]].mean()))
    pd.set_option("display.width", 200)
    print(t.round(3).to_string())
    print(f"\n전체 사건 {len(ev):,} · 가격 있음 {ev.priced.mean():.1%} (CIK {ev.by_cik.mean():.1%} + 옛 기호 검증 {ev.sym_ok.mean():.1%})"
          f" · 기호 매칭 중 단가 불일치(재사용 의심) {(m & ~ev.sym_ok).sum():,}")
    print(f"$10만 이상 사건 {ev.big.sum():,} · 가격 있음 {ev[ev.big].priced.mean():.1%}")
    ev.to_parquet(S / "derived/insider_buy_events.parquet", index=False)


if __name__ == "__main__":
    main()
