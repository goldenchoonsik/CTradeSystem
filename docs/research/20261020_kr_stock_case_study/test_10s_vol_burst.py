"""
10초봉(10s) vs 1분봉(1m) '거래량 폭증 + 변동성 폭발' 포착 속도 및 수익성 정밀 비교 실험
질문: "1분봉은 이미 다 오른 뒤에 끝나서 늦는 것 아닌가? 10초봉으로 폭증 초기(첫 10~20초)를 잡으면 빈틈이 있지 않을까?"

실행: wsl --cd /home/kuzimo/projects/CTradeSystem .venv/bin/python docs/research/20261020_kr_stock_case_study/test_10s_vol_burst.py
"""
import os
import sys
import json
import numpy as np
import pandas as pd

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
OUT_DIR = os.path.dirname(os.path.abspath(__file__))


def analyze_10s_symbol(sym: str, name: str, cost_pct: float):
    p = os.path.join(ROOT_DIR, "DataVault/normalized/candles_10s", f"{sym}_10s.parquet")
    if not os.path.exists(p):
        return None
    df = pd.read_parquet(p)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)

    c = df["close"].values.astype(float)
    o = df["open"].values.astype(float)
    h = df["high"].values.astype(float)
    l = df["low"].values.astype(float)
    v = df["volume"].values.astype(float)
    cvd = df["cvd"].values.astype(float) if "cvd" in df.columns else np.zeros(len(df))
    ofi = df["ofi"].values.astype(float) if "ofi" in df.columns else np.zeros(len(df))
    spread = df["spread"].values.astype(float) if "spread" in df.columns else np.zeros(len(df))
    dates = df["timestamp"].dt.date.values

    # 10s rolling 60-bar (10-min) average volume
    vol_ma60 = pd.Series(v).rolling(60, min_periods=20).mean().values
    d_cvd = np.diff(cvd, prepend=0.0)
    ret_10s = (c / np.roll(c, 1) - 1.0) * 100.0
    ret_10s[0] = 0.0
    ret_20s = (c / np.roll(c, 2) - 1.0) * 100.0
    ret_20s[:2] = 0.0

    # Forward returns at 1c(10s), 3c(30s), 6c(1m), 18c(3m), 36c(6m), 60c(10m)
    horizons = [(1, "10초"), (3, "30초"), (6, "1분"), (18, "3분"), (36, "6분"), (60, "10분")]
    fwd = {}
    for hc, _ in horizons:
        arr = np.full(len(df), np.nan)
        valid = np.arange(len(df) - hc)
        same_d = dates[valid] == dates[valid + hc]
        idx = valid[same_d]
        arr[idx] = (c[idx + hc] - c[idx]) / c[idx] * 100.0
        fwd[hc] = arr

    # Also measure: when a 1-minute (6-candle) big jump (>= +0.8%) happens, how much of it ALREADY happened in the first 10s and 20s?
    big_1m_idx = np.where(fwd[6] >= (0.8 if cost_pct > 0.1 else 0.3))[0]
    first_10s_share = float(np.nanmedian(fwd[1][big_1m_idx] / fwd[6][big_1m_idx]) * 100.0) if len(big_1m_idx) > 0 else 0.0
    first_20s_share = float(np.nanmedian(((c[big_1m_idx + 2] - c[big_1m_idx]) / c[big_1m_idx] * 100.0) / fwd[6][big_1m_idx]) * 100.0) if len(big_1m_idx) > 0 else 0.0

    print("\n" + "=" * 120)
    print(f"▶ {name} ({sym}) 10초봉(10s) 분석 | 총 {len(df):,}캔들 ({dates[0]} ~ {dates[-1]}) | 왕복 마찰비용 = {cost_pct:.2f}%")
    print(f"  [속도 해부] 1분(60초) 동안 큰 상승이 터질 때, 첫 10초 만에 이미 진행되는 비율={first_10s_share:.1f}%, 첫 20초 비율={first_20s_share:.1f}%")
    print("=" * 120)

    # Standardize threshold by each asset's own 99th percentile 10s move and volume spike so sample size is 100~500+
    p98_ret = float(np.nanpercentile(ret_10s[ret_10s > 0], 95))
    p99_ret = float(np.nanpercentile(ret_10s[ret_10s > 0], 98))
    p99_drop = float(np.nanpercentile(ret_10s[ret_10s < 0], 2))

    conds = [
        (
            f"S1. [10초 거래량 3배+ & 상위5% 양봉(+{p98_ret:.2f}%↑) 초기 돌파]",
            (v >= vol_ma60 * 3.0) & (ret_10s >= p98_ret) & (c > o),
        ),
        (
            f"S2. [10초 거래량 5배+ & 상위2% 양봉(+{p99_ret:.2f}%↑) & 순매수(dCVD>40%) 폭발]",
            (v >= vol_ma60 * 5.0) & (ret_10s >= p99_ret) & (v > 0) & ((d_cvd / np.maximum(1.0, v)) >= 0.40),
        ),
        (
            f"S3. [10초 호가불균형(OFI 상위2%) + 거래량 3배+ 초기 점화 (가격 아직 +{p99_ret:.2f}% 미만)]",
            (v >= vol_ma60 * 3.0)
            & (ofi >= np.nanpercentile(ofi, 98))
            & (ret_10s > 0.0)
            & (ret_10s < p99_ret),
        ),
        (
            f"S4. [10초 거래량 5배+ & 하위2% 급락({p99_drop:.2f}%↓) 패닉 직후 역추세 반등]",
            (v >= vol_ma60 * 5.0) & (ret_10s <= p99_drop),
        ),
    ]

    sym_res = {
        "symbol": sym,
        "name": name,
        "rows": int(len(df)),
        "cost_pct": cost_pct,
        "first_10s_share_of_1m_burst_pct": round(first_10s_share, 1),
        "first_20s_share_of_1m_burst_pct": round(first_20s_share, 1),
        "conditions": [],
    }

    for title, mask in conds:
        idx = np.where(mask)[0]
        # De-cluster within 6 candles (1 minute)
        clean_idx = []
        last_i = -999
        for i in idx:
            if i - last_i >= 6:
                clean_idx.append(i)
                last_i = i
        clean_idx = np.array(clean_idx, dtype=int)

        row = {"title": title, "n": int(len(clean_idx))}
        line = f"  • {title:<56} (n={len(clean_idx):4d}) | "
        for hc, hlabel in horizons:
            if len(clean_idx) == 0:
                continue
            vals = fwd[hc][clean_idx]
            vals = vals[~np.isnan(vals)]
            if len(vals) == 0:
                continue
            gross = float(np.mean(vals))
            net = gross - cost_pct
            wr_gross = float(np.mean(vals > 0) * 100.0)
            wr_net = float(np.mean(vals > cost_pct) * 100.0)
            row[f"gross_{hc}c"] = round(gross, 3)
            row[f"net_{hc}c"] = round(net, 3)
            row[f"wr_net_{hc}c"] = round(wr_net, 1)
            line += f"{hlabel}: 세전{gross:+6.3f}%/세후{net:+6.3f}% | "
        print(line)
        sym_res["conditions"].append(row)

    return sym_res


def main():
    targets = [
        ("000660", "SK하이닉스 (변동성 대장 개별주)", 0.23),
        ("005930", "삼성전자 (대형 개별주)", 0.23),
        ("0167A0", "SOL 반도체핵심소재 ETF (무세금 고변동 섹터)", 0.04),
        ("229200", "KODEX 코스닥150 ETF (무세금 코스닥 지수)", 0.03),
    ]
    all_res = []
    for sym, name, cost in targets:
        r = analyze_10s_symbol(sym, name, cost)
        if r:
            all_res.append(r)

    out_path = os.path.join(OUT_DIR, "burst_10s_analysis.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_res, f, ensure_ascii=False, indent=2)
    print(f"\n[DONE] Saved {out_path}")


if __name__ == "__main__":
    main()
