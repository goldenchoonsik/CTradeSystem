"""A3: 날짜별·시장별 후보 풀 — 날 D 의 후보는 D-1 까지의 일봉 정보만으로 정한다 (사후 선택 금지).

규칙(사전 고정):
  - 대상: 보통주(STOCK/DR/FOREIGN), 우선주·스팩 제외
  - 전일 종가 >= 1,000원
  - 전일 거래대금 시장별 상위 TOP
  - 직전 20일 평균 일중 범위((고-저)/전일종가) 가 그날 시장 중앙값 이상
출력: pool_daily.csv(date, code, market, rank), targets.txt(합집합), 요약 출력.

  .venv/bin/python docs/research/20261011_vol_spike/candidate_pool.py [--top 100]
"""
import argparse
import os

import pandas as pd

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
STAGE = os.path.join(REPO, "DataVault", "staging", "vol_spike_universe")
DAILY_DIR = os.path.join(STAGE, "raw", "creon", "candle_1d")
UNADJ_DIR = os.path.join(STAGE, "raw", "creon", "candle_1d_unadj")


def load_panel(universe):
    frames = []
    for code in universe.index:
        p = os.path.join(DAILY_DIR, f"{code}_1d.csv")
        if not os.path.exists(p):
            continue
        d = pd.read_csv(p, parse_dates=["timestamp"])
        d = d.rename(columns={"timestamp": "date", "upbit_open": "open", "upbit_high": "high", "upbit_low": "low",
                              "upbit_close": "close", "upbit_volume": "volume", "upbit_trade_price": "value"})
        d["code"] = code
        up = os.path.join(UNADJ_DIR, f"{code}_1d.csv")
        if os.path.exists(up):            # 가격 필터는 그날 실제 가격(무수정)으로
            u = pd.read_csv(up, parse_dates=["timestamp"]).drop_duplicates("timestamp").set_index("timestamp")["upbit_close"]
            d["raw_close"] = d["date"].map(u)
        else:
            d["raw_close"] = d["close"]
        frames.append(d)
    panel = pd.concat(frames, ignore_index=True).sort_values(["code", "date"])
    panel["market"] = panel["code"].map(universe["market"])
    return derive(panel)


def derive(panel):
    """원 일봉 패널 → D 시점에 아는 값(prev_*). 날 D 의 값은 D-1 까지에서만 나온다(테스트로 고정)."""
    panel = panel.sort_values(["code", "date"]).reset_index(drop=True)
    g = panel.groupby("code", sort=False)
    prev_close = g["close"].shift(1)
    panel["range"] = (panel["high"] - panel["low"]) / prev_close
    # D 시점에 아는 값 = D-1 까지
    panel["prev_close"] = prev_close
    panel["prev_raw_close"] = g["raw_close"].shift(1) if "raw_close" in panel else prev_close
    panel["prev_value"] = g["value"].shift(1)
    panel["prev_range20"] = g["range"].transform(lambda s: s.shift(1).rolling(20, min_periods=15).mean())
    return panel


def build_pool(panel, top):
    x = panel.dropna(subset=["prev_close", "prev_value", "prev_range20"])
    x = x[(x["prev_raw_close"] >= 1000) & (x["volume"] > 0)]   # 실제 가격 기준 1,000원   # 당일 거래정지 종목은 그날 매매 불가
    med = x.groupby(["date", "market"])["prev_range20"].transform("median")
    x = x[x["prev_range20"] >= med].copy()
    x["rank"] = x.groupby(["date", "market"])["prev_value"].rank(ascending=False, method="first")
    return x[x["rank"] <= top][["date", "code", "market", "rank", "prev_value", "prev_range20"]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=100)
    a = ap.parse_args()
    uni = pd.read_csv(os.path.join(STAGE, "universe.csv")).set_index("code")
    uni = uni[(uni["is_preferred"] == 0) & (uni["is_spac"] == 0)]
    panel = load_panel(uni)
    print(f"일봉 로드 {panel['code'].nunique()} 종목, {panel['date'].min():%Y-%m-%d}~{panel['date'].max():%Y-%m-%d}")
    for top in sorted({50, 100, 200, a.top}):
        pool = build_pool(panel, top)
        union = pool.groupby("market")["code"].nunique()
        stay = pool.groupby("code")["date"].nunique()
        print(f"TOP{top}: 일수 {pool['date'].nunique()}, 합집합 {pool['code'].nunique()} "
              f"(KOSPI {union.get('KOSPI', 0)}, KOSDAQ {union.get('KOSDAQ', 0)}), "
              f"종목당 후보일 중앙값 {stay.median():.0f}, 1일만 {int((stay == 1).sum())}")
        if top == a.top:
            pool.to_csv(os.path.join(STAGE, f"pool_daily_top{top}.csv"), index=False)
            with open(os.path.join(STAGE, f"targets_top{top}.txt"), "w") as f:
                f.write("\n".join(sorted(pool["code"].unique())) + "\n")


if __name__ == "__main__":
    main()
