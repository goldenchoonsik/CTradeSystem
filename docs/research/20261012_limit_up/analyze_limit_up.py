"""상한가 종목의 공통점 — 서술 분석 (탐색, 전략 판정 아님).

기간: 2024-08-14 ~ 2026-10-08, 유니버스 2,739 (우선주·스팩 포함, 현재 상장 종목만 → 생존 편향)
- 정규장 가격: 2026-09-14 부터 Creon 일봉은 20:00 시간외 종가라 그 날들은 1분봉 09:00~15:30 으로 다시 만든다
- 상한가 마감 = 수정 종가 / 전일 수정 종가 − 1 ≥ 29.5% 이고 종가 = 고가. 터치 = 고가 기준 ≥ 29.5%
- 제외: 상장 후 5거래일(신규상장 가격제한이 다름)
- 공통점은 두 갈래: (1) 전날까지 알 수 있는 특징별 '상한가 확률 배수'(lift) (2) 당일·이후 모습

  .venv/bin/python docs/research/20261012_limit_up/analyze_limit_up.py
"""
import glob
import json
import os
import re
from collections import Counter

import numpy as np
import pandas as pd

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
STAGE = os.path.join(REPO, "DataVault", "staging", "vol_spike_universe")
HERE = os.path.dirname(__file__)
NXT_FROM = pd.Timestamp("2026-09-14")
LU = 0.295


def read_daily(code):
    cols = {"timestamp": "date", "upbit_open": "o", "upbit_high": "h", "upbit_low": "l", "upbit_close": "c", "upbit_volume": "v", "upbit_trade_price": "val"}
    a = pd.read_csv(os.path.join(STAGE, "raw/creon/candle_1d", f"{code}_1d.csv"), parse_dates=["timestamp"]).drop_duplicates("timestamp").rename(columns=cols)
    r = pd.read_csv(os.path.join(STAGE, "raw/creon/candle_1d_unadj", f"{code}_1d.csv"), parse_dates=["timestamp"]).drop_duplicates("timestamp").rename(columns=cols)
    d = r.merge(a[["date", "c"]].rename(columns={"c": "c_adj"}), on="date", how="left")
    d["f"] = d.c_adj / d.c                                     # 수정 계수 (시간외 종가끼리도 비율은 같다)
    # 2026-09-14~ 정규장 OHLCV 를 1분봉으로 교체
    for i in d.index[d.date >= NXT_FROM]:
        fp = os.path.join(STAGE, "raw/creon/candle_1m", code, f"{d.at[i, 'date']:%Y-%m-%d}.csv")
        if not os.path.exists(fp):
            d.loc[i, ["o", "h", "l", "c", "v", "val"]] = np.nan
            continue
        m = pd.read_csv(fp, parse_dates=["timestamp"])
        m = m[(m.timestamp.dt.hour * 100 + m.timestamp.dt.minute).between(900, 1530)]
        if m.empty or m.upbit_volume.sum() <= 0:
            d.loc[i, ["v", "val"]] = 0
            continue
        d.loc[i, ["o", "h", "l", "c", "v", "val"]] = [m.upbit_open.iloc[0], m.upbit_high.max(), m.upbit_low.min(),
                                                      m.upbit_close.iloc[-1], m.upbit_volume.sum(), m.upbit_trade_price.sum()]
    d["code"] = code
    return d


def build_panel():
    cache = os.path.join(STAGE, "derived", "limit_up_panel.parquet")   # 23MB — 저장소 밖
    if os.path.exists(cache):
        return pd.read_parquet(cache)
    uni = pd.read_csv(os.path.join(STAGE, "universe.csv"))
    d = pd.concat([read_daily(c) for c in uni.code], ignore_index=True)
    d.to_parquet(cache)
    return d


def first_touch(code, day, limit_px):
    """1분봉에서 상한가 첫 도달 시각·시가 상한가(점상) 여부·마감까지 유지 여부."""
    fp = os.path.join(STAGE, "raw/creon/candle_1m", code, f"{day:%Y-%m-%d}.csv")
    if not os.path.exists(fp):
        return None
    m = pd.read_csv(fp, parse_dates=["timestamp"])
    m = m[(m.timestamp.dt.hour * 100 + m.timestamp.dt.minute).between(900, 1530)]
    if m.empty:
        return None
    hit = m[m.upbit_high >= limit_px * 0.999]
    if hit.empty:
        return None
    t = hit.timestamp.iloc[0]
    after = m[m.timestamp >= t]
    return dict(touch_min=(t.hour - 9) * 60 + t.minute, gap_limit=bool(m.upbit_open.iloc[0] >= limit_px * 0.999),
                min_low_after=float(after.upbit_low.min() / limit_px - 1),
                val_before_touch=float(m[m.timestamp < t].upbit_trade_price.sum()))


def bucket_lift(df, col, bins, labels, flag="lu"):
    x = df.dropna(subset=[col])
    cut = pd.cut(x[col], bins=bins, labels=labels, include_lowest=True)
    g = x.groupby(cut, observed=False)[flag].agg(["sum", "size"])
    base = x[flag].mean()
    g["rate"] = g["sum"] / g["size"]
    g["lift"] = g.rate / base
    g["share"] = g["sum"] / g["sum"].sum()
    return g


def fmt_lift(title, g):
    out = [f"### {title}", "", "| 구간 | 상한가 수 | 비중 | 종목-일 | 확률 | 배수 |", "|---|---|---|---|---|---|"]
    for k, r in g.iterrows():
        out.append(f"| {k} | {int(r['sum'])} | {r.share:.0%} | {int(r['size']):,} | {r.rate*100:.3f}% | {r.lift:.1f}× |")
    return out + [""]


def features():
    """종목-일 패널 + 전날(D-1)까지의 특징. 다른 분석(after5 등)이 같은 정의를 쓰도록 분리."""
    uni = pd.read_csv(os.path.join(STAGE, "universe.csv")).set_index("code")
    sh = pd.read_csv(os.path.join(STAGE, "shares_now.csv")).set_index("code").shares
    d = build_panel().sort_values(["code", "date"]).reset_index(drop=True)
    d = d[d.v.notna()]
    g = d.groupby("code", sort=False)
    d["ca"] = d.c * d.f
    d["ha"] = d.h * d.f
    pc = g.ca.shift(1)
    d["ret"] = d.ca / pc - 1
    d["hret"] = d.ha / pc - 1
    d["prev_raw"] = g.c.shift(1)
    d["n"] = g.cumcount()
    listed = pd.to_datetime(d.code.map(uni.listed_date).astype(str), format="%Y%m%d", errors="coerce")
    d["age_days"] = (d.date - listed).dt.days
    d = d[(d.v > 0) & pc.notna()]
    d = d[~((d.age_days >= 0) & (d.age_days < 8))]                  # 신규상장 직후
    d["lu"] = ((d.ret >= LU) & (d.c >= d.h * 0.999)).astype(int)
    d["touch"] = (d.hret >= LU).astype(int)

    # ---- 전날까지의 특징 (D-1 기준) ----
    g = d.groupby("code", sort=False)
    sh_d = d.code.map(sh)
    d["p_raw"] = g.c.shift(1)
    d["mcap"] = (d.p_raw * sh_d) / 1e8                                 # 억원 (현재 주식수 근사)
    d["val20"] = g.val.transform(lambda s: s.shift(1).rolling(20, min_periods=10).mean()) / 1e8
    d["vratio"] = g.val.shift(1) / g.val.transform(lambda s: s.shift(2).rolling(20, min_periods=10).median())
    d["r1"] = g.ret.shift(1)
    for k in (5, 20, 60):
        d[f"r{k}"] = g.ca.shift(1) / g.ca.shift(1 + k) - 1
    d["vol20"] = g.ret.transform(lambda s: s.shift(1).rolling(20, min_periods=10).std())
    d["hi120"] = g.ca.shift(1) / g.ha.transform(lambda s: s.shift(1).rolling(120, min_periods=40).max()) - 1
    d["lu_prev20"] = g.lu.transform(lambda s: s.shift(1).rolling(20, min_periods=1).sum())
    d["touch_prev20"] = g.touch.transform(lambda s: s.shift(1).rolling(20, min_periods=1).sum())
    d["market"] = d.code.map(uni.market)
    d["pref"] = d.code.map(uni.is_preferred)
    d["spac"] = d.code.map(uni.is_spac)
    d["mkt_r"] = d.groupby(["date", "market"]).ret.transform("median")
    d["n_lu_day"] = d.groupby("date").lu.transform("sum")

    return d, uni


def main():
    d, uni = features()
    ev = d[d.lu == 1].copy()
    base = d.lu.mean()
    L = [f"# 상한가 종목 공통점 (서술 분석, {d.date.min():%Y-%m-%d} ~ {d.date.max():%Y-%m-%d})", "",
         f"- 종목-일 {len(d):,}건 중 **상한가 마감 {len(ev):,}건** (확률 {base*100:.3f}%, 하루 평균 {len(ev)/d.date.nunique():.2f}건), 종목 {ev.code.nunique():,}개",
         f"- 상한가 터치 {d.touch.sum():,}건 → 마감까지 유지 {len(ev)/d.touch.sum():.0%}",
         f"- 상한가가 1건 이상 나온 날 {(ev.groupby('date').size() > 0).sum()}일 / {d.date.nunique()}일",
         "- 배수 = 그 구간의 상한가 확률 ÷ 전체 확률. 모든 특징은 **전날 장마감까지** 알 수 있는 값(시가총액은 현재 주식수 근사)", ""]

    L += fmt_lift("시장·종류", pd.concat([
        d.groupby("market").lu.agg(["sum", "size"]),
        d.assign(k=np.where(d.pref == 1, "우선주", np.where(d.spac == 1, "스팩", "보통주"))).groupby("k").lu.agg(["sum", "size"])]).assign(
        rate=lambda x: x["sum"] / x["size"], lift=lambda x: x.rate / base, share=lambda x: x["sum"] / len(ev)))
    L += fmt_lift("시가총액(전일, 억원)", bucket_lift(d, "mcap", [0, 300, 500, 1000, 2000, 5000, 1e9], ["<300", "300~500", "500~1천", "1천~2천", "2천~5천", "5천+"]))
    L += fmt_lift("주가(전일 실제가, 원)", bucket_lift(d, "p_raw", [0, 1000, 2000, 5000, 10000, 50000, 1e9], ["<1천", "1~2천", "2~5천", "5천~1만", "1~5만", "5만+"]))
    L += fmt_lift("20일 평균 거래대금(억원)", bucket_lift(d, "val20", [0, 1, 5, 20, 100, 1e9], ["<1억", "1~5억", "5~20억", "20~100억", "100억+"]))
    L += fmt_lift("전일 거래대금 배수(전일 ÷ 그 전 20일 중앙값)", bucket_lift(d, "vratio", [0, 0.5, 1, 2, 5, 10, 1e9], ["<0.5", "0.5~1", "1~2", "2~5", "5~10", "10+"]))
    L += fmt_lift("전일 수익률", bucket_lift(d, "r1", [-1, -0.05, -0.01, 0.01, 0.05, 0.15, 0.295, 2], ["≤−5%", "−5~−1%", "±1%", "1~5%", "5~15%", "15~29.5%", "상한가"]))
    L += fmt_lift("직전 20일 수익률", bucket_lift(d, "r20", [-1, -0.2, -0.05, 0.05, 0.2, 0.5, 100], ["≤−20%", "−20~−5%", "±5%", "5~20%", "20~50%", "50%+"]))
    L += fmt_lift("직전 120일 고점 대비", bucket_lift(d, "hi120", [-1, -0.6, -0.4, -0.2, -0.05, 0.01], ["−60% 이하", "−60~−40%", "−40~−20%", "−20~−5%", "고점 근처"]))
    L += fmt_lift("20일 변동성(일 수익률 표준편차)", bucket_lift(d, "vol20", [0, 0.015, 0.03, 0.05, 0.08, 10], ["<1.5%", "1.5~3%", "3~5%", "5~8%", "8%+"]))
    L += fmt_lift("직전 20일 상한가 횟수", bucket_lift(d, "lu_prev20", [-0.5, 0.5, 1.5, 100], ["0", "1", "2+"]))
    L += fmt_lift("상장 후 경과", bucket_lift(d, "age_days", [0, 90, 365, 365 * 3, 365 * 10, 1e6], ["<3개월", "3~12개월", "1~3년", "3~10년", "10년+"]))
    L += fmt_lift("그날 시장(같은 시장 중앙 수익률)", bucket_lift(d, "mkt_r", [-1, -0.01, 0, 0.01, 1], ["≤−1%", "−1~0%", "0~1%", "≥1%"]))

    # 현재 상태(스냅샷, 2026-10-11) — 과거 날짜엔 미래 정보라 참고만
    st = ev.code.map(uni.supervision_kind).value_counts(normalize=True)
    L += ["### 현재(10-11) 투자경고·관리 지정 여부 — 참고(사후 지정 포함)", "",
          f"상한가 종목 중 현재 감리/경고 구분 ≠ 0: {(ev.code.map(uni.supervision_kind) != 0).mean():.0%} (전체 유니버스 {(uni.supervision_kind != 0).mean():.0%}), "
          f"관리 구분 ≠ 0: {(ev.code.map(uni.control_kind) != 0).mean():.0%} (전체 {(uni.control_kind != 0).mean():.0%})", ""]

    # ---- 당일 모습 (1분봉) ----
    rows = []
    for r in ev.itertuples():
        lp = r.c                                                      # 마감가 = 상한가
        x = first_touch(r.code, r.date, lp)
        if x:
            x.update(code=r.code, date=r.date)
            rows.append(x)
    tt = pd.DataFrame(rows)
    ev = ev.merge(tt, on=["code", "date"], how="left")
    ev["gap"] = ev.o * ev.f / (ev.ca / (1 + ev.ret)) - 1
    tm = ev.touch_min.dropna()
    L += ["## 당일 모습", "",
          f"- 1분봉 있는 {tm.size}건 기준: 시가부터 상한가(점상) {ev.gap_limit.mean():.0%}, 첫 도달 시각 중앙값 {int(tm.median())//60+9}:{int(tm.median())%60:02d}",
          f"- 첫 도달 시각 분포: 09:00~09:10 {(tm<=10).mean():.0%} · ~10:00 {(tm.between(11,60)).mean():.0%} · ~12:00 {(tm.between(61,180)).mean():.0%} · ~14:00 {(tm.between(181,300)).mean():.0%} · 14시 이후 {(tm>300).mean():.0%}",
          f"- 시가 갭: 중앙값 {ev.gap.median()*100:+.1f}%, 갭 ≥ +10% {(ev.gap>=0.10).mean():.0%}, 갭 ≤ 0% {(ev.gap<=0).mean():.0%}",
          f"- 도달 후 최저가(상한가 대비) 중앙값 {ev.min_low_after.median()*100:+.1f}% — 풀렸다 다시 잠긴 경우 {(ev.min_low_after < -0.02).mean():.0%}",
          f"- 같은 날 상한가 종목 수: 혼자 {(ev.n_lu_day==1).mean():.0%}, 2~4개 날 {(ev.n_lu_day.between(2,4)).mean():.0%}, 5개 이상 날 {(ev.n_lu_day>=5).mean():.0%}", ""]

    # ---- 이후 ----
    full = build_panel().sort_values(["code", "date"])
    full = full[full.v.notna()].copy()
    full["ca"], full["oa"] = full.c * full.f, full.o * full.f
    gg = full.groupby("code", sort=False)
    for k in (1, 5, 20):
        full[f"c{k}"] = gg.ca.shift(-k) / full.ca - 1
    full["o1"] = gg.oa.shift(-1) / full.ca - 1
    after = ev.merge(full[["code", "date", "o1", "c1", "c5", "c20"]], on=["code", "date"], how="left")
    L += ["## 이후 수익 (상한가 종가 기준, 비용 전) — H5 와 같은 문제: 상한가 매수 잔량 때문에 대부분 체결 불가", "",
          "| 구분 | 건 | 익일 시가 | 익일 종가 | 5일 | 20일 | 익일 연속 상한가 |", "|---|---|---|---|---|---|---|"]
    nxt_lu = ev.merge(d[["code", "date", "lu"]].assign(date=lambda x: x.groupby("code").date.shift(1)).dropna(subset=["date"]).rename(columns={"lu": "lu_next"}),
                      on=["code", "date"], how="left").lu_next
    after["lu_next"] = nxt_lu.values
    for lab, m in (("전체", after.index == after.index), ("점상(시가 상한가)", after.gap_limit == True), ("장중 도달", after.gap_limit == False),
                   ("첫 상한가(직전 20일 없음)", after.lu_prev20 == 0), ("연속(직전 20일 있음)", after.lu_prev20 > 0)):
        x = after[m]
        L.append(f"| {lab} | {len(x)} | {x.o1.median()*100:+.1f}% | {x.c1.median()*100:+.1f}% | {x.c5.median()*100:+.1f}% | {x.c20.median()*100:+.1f}% | {x.lu_next.mean():.0%} |")
    L += ["", "(중앙값. 평균은 극단값에 끌려 다름)", ""]

    top = ev.groupby("code").size().sort_values(ascending=False).head(15)
    L += ["## 상한가가 가장 많았던 종목", "", "| 종목 | 시장 | 횟수 |", "|---|---|---|"]
    for c, n in top.items():
        L.append(f"| {uni.name.get(c)} ({c[1:]}) | {uni.market.get(c)} | {n} |")
    L += predictability(d)
    ev[["code", "date", "ret", "gap", "touch_min", "gap_limit", "mcap", "p_raw", "val20", "vratio", "r1", "r20", "lu_prev20", "n_lu_day", "market", "pref", "spac"]].to_csv(
        os.path.join(HERE, "limit_up_events.csv"), index=False)
    out = "\n".join(L)
    print(out)
    with open(os.path.join(HERE, "results.md"), "w", encoding="utf-8") as f:
        f.write(out + "\n")


FEATS = ["mcap", "p_raw", "val20", "vratio", "r1", "r5", "r20", "r60", "vol20", "hi120", "lu_prev20", "touch_prev20", "age_days", "kosdaq", "pref"]


def predictability(d):
    """전날 특징만으로 내일 상한가를 미리 고를 수 있나 — 시간 분할(학습 ~2025-12, 시험 2026-01~)."""
    import lightgbm as lgb
    x = d.assign(kosdaq=(d.market == "KOSDAQ").astype(int)).dropna(subset=["r1", "vol20", "val20"])
    tr, te = x[x.date < "2026-01-01"], x[x.date >= "2026-01-01"].copy()
    params = {"objective": "binary", "learning_rate": 0.03, "num_leaves": 31, "min_data_in_leaf": 500,
              "feature_fraction": 0.8, "bagging_fraction": 0.8, "bagging_freq": 1, "verbose": -1, "seed": 7, "num_threads": 4}
    m = lgb.train(params, lgb.Dataset(tr[FEATS], tr.lu), num_boost_round=400)
    te["p"] = m.predict(te[FEATS])
    r = te.p.rank()
    npos = te.lu.sum()
    auc = (r[te.lu == 1].sum() - npos * (npos + 1) / 2) / (npos * (len(te) - npos))   # 맨-휘트니 AUC
    te["rk"] = te.groupby("date").p.rank(ascending=False, method="first")
    out = ["## 전날 특징으로 내일 상한가를 미리 고를 수 있나 (학습 2024-09~2025-12 → 시험 2026-01~10)", "",
           f"- 시험 구간 상한가 확률 {te.lu.mean()*100:.2f}%, 모델 AUC {auc:.3f}", "",
           "| 매일 점수 상위 | 그중 상한가 비율 | 기준 대비 | 상한가 놓친 비율(전체 상한가 중 못 잡음) | 당일 +10% 이상 | 당일 −10% 이하 | 당일 시가→종가 평균 | 당일 시가→종가 중앙값 |", "|---|---|---|---|---|---|---|---|"]
    te["oc"] = te.c / te.o - 1
    for k in (5, 20, 100, 10**6):
        top = te[te.rk <= k]
        out.append(f"| {k if k < 10**6 else '전체'} | {top.lu.mean()*100:.1f}% | {top.lu.mean()/te.lu.mean():.0f}× | {1 - top.lu.sum()/te.lu.sum():.0%} | {(top.ret >= 0.10).mean():.0%} | {(top.ret <= -0.10).mean():.0%} | {top.oc.mean()*100:+.2f}% | {top.oc.median()*100:+.2f}% |")
    imp = pd.Series(m.feature_importance("gain"), index=FEATS).sort_values(ascending=False)
    out += ["", "특징 중요도(gain 비중): " + ", ".join(f"{k} {v/imp.sum():.0%}" for k, v in imp.head(6).items()),
            "", "시가→종가는 전날 점수로 당일 시가에 샀을 때(비용 전, 왕복 비용 약 0.3~0.5%). 상한가는 대부분 시가 이후에 나오지만 시가 매수가 그 수익을 다 잡는 것은 아니다.", ""]
    return out


if __name__ == "__main__":
    main()
