"""미국(나스닥100) ↔ 한국(K200) 시차 영향 + 외국인 수급 (읽기 전용).

Q1 미국 밤사이 → 한국 갭, 갭 이후 장중 지속/되돌림
Q2 밤사이 미국 → 당일 외국인 순매수, 미국 몫을 뺀 외국인 잔차의 설명력
Q3 한국 장중 → 한국 마감 이후 나스닥 (한국 시간대 나스닥 통제, 니케이 대조군)
Q4 한국 장중 1분·5분 시차 상관 (K200 선물 vs 나스닥 CFD)

시각 규약(실측 확인):
- cTrader 원본 = UTC, 봉 시작 라벨 → 구간 끝 KST = 라벨 + 9h + 1m
  (2025-07 거래량 정점 13:30, 2025-12 14:30 = 뉴욕 09:30 개장의 UTC)
- Creon 1분봉 = KST, 봉 끝 라벨 (08:46 = 08:45~08:46)
- 한국 지수 일봉 U180(K200)·U001(KOSPI) 시가/종가 사용(선물 교체 점프 없음)

  .venv/bin/python docs/research/20261013_flow_kospi/us_kr_leadlag.py
"""
import glob
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import flow_kospi as F  # noqa: E402

REPO = F.REPO
CT = os.path.join(REPO, "DataVault", "raw", "ctrader")
FUT = os.path.join(REPO, "DataVault", "raw", "creon", "candle_1m", "A10100")
START = "2020-01-02"
STALE = pd.Timedelta("45min")       # 기준 시각 이전 이 시간 안에 봉이 없으면 결측
OPEN_STALE = pd.Timedelta("5h")     # 09:00 기준가: 2020~23 CFD 는 UTC 20~02시(KST 05~11시)가 비어 미국 마감 무렵 가격으로 대체


def load_ct(name):
    d = pd.read_csv(os.path.join(CT, f"{name}_1m.csv"), usecols=["timestamp", "close"], parse_dates=["timestamp"])
    d["t"] = d["timestamp"] + pd.Timedelta(hours=9, minutes=1)   # 구간 끝, KST
    return d.set_index("t")["close"].sort_index()


def price_at(s, times, stale=STALE):
    """각 시각 T 의 마지막 가격(T 이전 stale 안)."""
    times = pd.DatetimeIndex(times)
    nat = times.isna()
    idx = s.index.searchsorted(times.fillna(s.index[0]), side="right") - 1
    ok = (idx >= 0) & ~nat
    out = np.full(len(times), np.nan)
    ti = s.index.values
    for k in np.where(ok)[0]:
        if times[k] - ti[idx[k]] <= stale:
            out[k] = s.values[idx[k]]
    return pd.Series(out, index=times)


def ols(y, X, names):
    """OLS + HC1 표준오차. 반환: 계수/t 문자열, R²."""
    m = y.notna() & X.notna().all(axis=1)
    y, X = y[m].values, X[m].values
    X = np.column_stack([np.ones(len(y)), X])
    b, *_ = np.linalg.lstsq(X, y, rcond=None)
    e = y - X @ b
    n, k = X.shape
    XtXi = np.linalg.inv(X.T @ X)
    V = XtXi @ (X.T * e ** 2) @ X @ XtXi * n / (n - k)
    t = b / np.sqrt(np.diag(V))
    r2 = 1 - e.var() / y.var()
    parts = [f"{nm} β{b[i+1]:+.3f}(t{t[i+1]:+.1f})" for i, nm in enumerate(names)]
    return ", ".join(parts) + f" | R² {r2:.3f} n{n}"


def corr(a, b):
    m = a.notna() & b.notna()
    r = np.corrcoef(a[m], b[m])[0, 1]
    n = int(m.sum())
    return f"{r:+.3f}(t{r*np.sqrt((n-2)/(1-r*r)):+.1f})"


def by_year(fn, df):
    return " · ".join(f"{y}:{fn(s)}" for y, s in df.groupby(df.index.year))


def daily_frame():
    idx = pd.read_parquet(os.path.join(F.KR, "derived", "index_1d.parquet"))
    k2 = idx[idx.code == "U180"].set_index("date").sort_index()
    k2 = k2[k2.index >= START]
    days = k2.index
    d = pd.DataFrame(index=days)
    d["kr_gap"] = np.log(k2.open / k2.close.shift()) * 100
    d["kr_intra"] = np.log(k2.close / k2.open) * 100
    d["kr_am"] = np.nan                                       # 09:00→10:00 (선물로 채움)
    nq, nk = load_ct("US_TECH_100"), load_ct("JAPAN_225")
    t0900 = days + pd.Timedelta("9h")
    t1530 = days + pd.Timedelta("15h30min")
    prev1530 = pd.DatetimeIndex([pd.NaT] + list(t1530[:-1]))
    nxt0900 = pd.DatetimeIndex(list(t0900[1:]) + [pd.NaT])
    for nm, s in (("nq", nq), ("nk", nk)):
        p_prev = price_at(s, prev1530).values
        p0900 = price_at(s, t0900, OPEN_STALE).values
        p1530 = price_at(s, t1530).values
        p2130 = price_at(s, days + pd.Timedelta("21h30min")).values
        p_nxt = price_at(s, nxt0900.fillna(t0900[-1]), OPEN_STALE).values
        d[f"{nm}_overnight"] = np.log(p0900 / p_prev) * 100     # 전일 한국 마감 → 오늘 한국 개장
        d[f"{nm}_krsess"] = np.log(p1530 / p0900) * 100         # 한국 장중과 같은 시간
        d[f"{nm}_after"] = np.log(p_nxt / p1530) * 100          # 한국 마감 → 다음 한국 개장
        d[f"{nm}_after_eu"] = np.log(p2130 / p1530) * 100       # 15:30 → 21:30 (유럽, 미국 개장 전)
        d[f"{nm}_after_us"] = np.log(p_nxt / p2130) * 100       # 21:30 → 다음 09:00 (미국 정규장 포함)
    d.iloc[-1, d.columns.get_loc("nq_after")] = np.nan
    return d


def load_fut():
    rows = []
    for fp in sorted(glob.glob(os.path.join(FUT, "*.csv"))):
        if os.path.basename(fp)[:10] < START:
            continue
        x = pd.read_csv(fp, usecols=["timestamp", "upbit_close"], parse_dates=["timestamp"])
        rows.append(x)
    f = pd.concat(rows).rename(columns={"upbit_close": "close"})
    return f.set_index("timestamp")["close"].sort_index()       # 라벨 = 구간 끝 KST


def q4(fut, nq):
    """한국 장중(09:05~15:15) K200 선물 vs 나스닥 CFD 시차 상관. k>0 이면 K200 이 k분 먼저."""
    out = []
    for step in ("1min", "5min"):
        kf = fut.resample(step, label="right", closed="right").last()
        nf = nq.resample(step, label="right", closed="right").last()
        df = pd.concat([np.log(kf).diff().rename("k"), np.log(nf).diff().rename("n")], axis=1)
        tm = df.index.time
        df = df[(tm >= pd.Timestamp("09:05").time()) & (tm <= pd.Timestamp("15:15").time())]
        # 같은 날 안에서만 시차 (날짜 넘김 차단)
        day = df.index.normalize()
        lags = [-30, -15, -10, -5, -3, -2, -1, 0, 1, 2, 3, 5, 10, 15, 30] if step == "1min" else [-6, -3, -2, -1, 0, 1, 2, 3, 6]
        unit = 1 if step == "1min" else 5
        row = {"봉": step}
        for L in lags:
            n_sh = df.groupby(day)["n"].shift(-L)    # 나스닥 L 봉 뒤
            row[f"{L*unit:+d}분"] = round(df["k"].corr(n_sh), 3)
        out.append(row)
        if step == "5min":
            yr = {}
            for y, s in df.groupby(df.index.year):
                dd = s.index.normalize()
                yr[y] = (round(s.k.corr(s.n), 3), round(s.k.corr(s.groupby(dd).n.shift(-1)), 3),
                         round(s.n.corr(s.groupby(dd).k.shift(-1)), 3))
    return out, yr


def main():
    d = daily_frame()
    L = ["# 미국 ↔ 한국 시차 영향 (2026-10-13)", "",
         f"기간 {d.index.min():%Y-%m-%d} ~ {d.index.max():%Y-%m-%d} · 수익률 % 로그 · β/t 는 HC1 · "
         "nq=나스닥100 CFD, nk=니케이225 CFD(아시아 대조군), K200=U180 지수 일봉", ""]

    # Q1
    L += ["## Q1. 미국 밤사이 → 한국 갭 → 장중", "",
          f"- 갭 ~ 나스닥 밤사이: {ols(d.kr_gap, d[['nq_overnight']], ['nq_overnight'])}",
          f"- 갭 ~ 나스닥 밤사이 + 니케이 밤사이: {ols(d.kr_gap, d[['nq_overnight','nk_overnight']], ['nq','nk'])}",
          f"- 갭 연도별 상관: {by_year(lambda s: f'{s.kr_gap.corr(s.nq_overnight):+.2f}', d)}",
          f"- 장중 ~ 갭: {ols(d.kr_intra, d[['kr_gap']], ['gap'])}",
          f"- 장중 ~ 나스닥 밤사이(이미 갭에 반영된 뒤 추가 움직임?): {ols(d.kr_intra, d[['nq_overnight','kr_gap']], ['nq_overnight','gap'])}",
          f"- 장중 ~ 같은 시간 나스닥(동시 움직임): {corr(d.kr_intra, d.nq_krsess)}",
          ""]

    # Q2 외국인
    codes = F.kospi_codes()
    cr = F.load_creon(codes)
    kis = F.load_kis(codes)
    fl = pd.DataFrame({"frgn_creon": cr["외국인"] / cr.tv * 100, "inst_creon": cr["기관계"] / cr.tv * 100})
    fk = pd.DataFrame({"frgn_kis": kis["외국인"] / kis.tv * 100, "prsn_kis": kis["개인"] / kis.tv * 100})
    x = d.join(fl).join(fk)
    x["nq_overnight_next"] = x.nq_overnight.shift(-1)
    L += ["## Q2. 같은 외국인인가 — 밤사이 미국과 당일 외국인 순매수", "",
          f"- 외국인(Creon, 2020~) ~ 나스닥 밤사이: {ols(x.frgn_creon, x[['nq_overnight']], ['nq_overnight'])}",
          f"- 외국인(Creon) ~ 나스닥 밤사이 + 한국 갭 + 한국 장중: {ols(x.frgn_creon, x[['nq_overnight','kr_gap','kr_intra']], ['nq_on','gap','intra'])}",
          f"- 외국인(KIS 2024-07~) ~ 나스닥 밤사이 + 갭 + 장중: {ols(x.frgn_kis, x[['nq_overnight','kr_gap','kr_intra']], ['nq_on','gap','intra'])}",
          f"- 기관(Creon) ~ 나스닥 밤사이 + 갭 + 장중: {ols(x.inst_creon, x[['nq_overnight','kr_gap','kr_intra']], ['nq_on','gap','intra'])}",
          f"- 개인(KIS) ~ 나스닥 밤사이 + 갭 + 장중: {ols(x.prsn_kis, x[['nq_overnight','kr_gap','kr_intra']], ['nq_on','gap','intra'])}",
          ]
    m = x[["frgn_creon", "nq_overnight", "kr_gap"]].dropna()
    b = np.linalg.lstsq(np.column_stack([np.ones(len(m)), m.nq_overnight, m.kr_gap]), m.frgn_creon, rcond=None)[0]
    x["frgn_resid"] = x.frgn_creon - (b[0] + b[1] * x.nq_overnight + b[2] * x.kr_gap)
    L += [f"- 외국인 잔차(미국·갭 몫 제거) vs 당일 장중: {corr(x.frgn_resid, x.kr_intra)} · 원 외국인 vs 장중: {corr(x.frgn_creon, x.kr_intra)}",
          f"- 외국인 잔차 → 다음 날 갭: {corr(x.frgn_resid, x.kr_gap.shift(-1))} · → 다음 날 장중: {corr(x.frgn_resid, x.kr_intra.shift(-1))}",
          f"- 외국인(오늘) → 오늘 밤 나스닥(한국 마감 후): {corr(x.frgn_creon, x.nq_after)}",
          ""]

    # Q3 한국 → 미국
    L += ["## Q3. 한국 장중 → 한국 마감 이후 나스닥", "",
          f"- 나스닥 마감후 ~ 한국 장중: {ols(d.nq_after, d[['kr_intra']], ['kr_intra'])}",
          f"- 나스닥 마감후 ~ 한국 장중 + 같은 시간 나스닥: {ols(d.nq_after, d[['kr_intra','nq_krsess']], ['kr_intra','nq_krsess'])}",
          f"- 나스닥 마감후 ~ 한국 장중 + 같은 시간 나스닥 + 니케이 같은 시간: {ols(d.nq_after, d[['kr_intra','nq_krsess','nk_krsess']], ['kr_intra','nq_krsess','nk_krsess'])}",
          f"- 분해 15:30→21:30(유럽): {ols(d.nq_after_eu, d[['kr_intra','nq_krsess']], ['kr_intra','nq_krsess'])}",
          f"- 분해 21:30→익일 09:00(미국): {ols(d.nq_after_us, d[['kr_intra','nq_krsess']], ['kr_intra','nq_krsess'])}",
          f"- 대조: 나스닥 마감후 ~ 니케이 한국시간 움직임 + 나스닥 같은 시간: {ols(d.nq_after, d[['nk_krsess','nq_krsess']], ['nk_krsess','nq_krsess'])}",
          f"- 한국 장중 연도별 단순상관(→ 나스닥 마감후): {by_year(lambda s: f'{s.kr_intra.corr(s.nq_after):+.2f}', d)}",
          f"- 외국인 순매수 오늘 → 한국 마감 후 나스닥(위와 동일 지표 재확인): {corr(x.frgn_creon, x.nq_after)}",
          ""]

    # Q4
    fut = load_fut()
    nq = load_ct("US_TECH_100")
    rows, yr = q4(fut, nq)
    hdr = list(rows[0].keys())
    L += ["## Q4. 한국 장중 시차 상관 (K200 선물 수익률 vs 나스닥 CFD 수익률)", "",
          "열 +k분 = 나스닥이 k분 **뒤** (양수 열이 크면 K200 이 먼저), −k분 = 나스닥이 먼저. 09:05~15:15, 같은 날 안에서만.", ""]
    for r in rows:
        L += ["| " + " | ".join(f"{k}={v}" for k, v in r.items()) + " |"]
    L += ["", "5분봉 연도별 (동시, K200→나스닥 5분뒤, 나스닥→K200 5분뒤): "
          + " · ".join(f"{y}:{v}" for y, v in yr.items()), ""]

    with open(os.path.join(os.path.dirname(__file__), "results_us_kr.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print("\n".join(L))


if __name__ == "__main__":
    main()
