"""
[EXP-KRX-018] MSCI 신규 편입주 스윙 매매 내역의 편입일(D-Day) 전후 정밀 분해 분석
====================================================================================
- 목적: 앞선 799회 매매 내역을 각 종목의 실제 편입일(D-Day) 기준으로 분해하여
  1) 편입 후 6개월 이내에 실제로 매매가 일어났는지 (실체 확인)
  2) 편입 후 6개월 이내 매매들의 승률, 손익비, 순손익은 어땠는지
  3) 만약 "편입 후 6개월간 매수 금지(Blackout)" 룰을 적용했다면 성과가 어떻게 개선되는지
"""
import os
import sys
import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = "/home/kuzimo/projects/CTradeSystem"
RESEARCH_DIR = f"{ROOT}/docs/research/20261021_krx_super_gainers_postmortem"

# 종목별 실제 편입일 매핑
INCLUSION_MAP = {
    "A019170": pd.Timestamp("2020-08-31"), # 신풍제약
    "A086520": pd.Timestamp("2023-08-31"), # 에코프로
    "A247540": pd.Timestamp("2021-08-31"), # 에코프로비엠
    "A022100": pd.Timestamp("2023-11-30"), # 포스코DX
    "A012450": pd.Timestamp("2023-05-31"), # 한화에어로스페이스
    "A042700": pd.Timestamp("2023-08-31"), # 한미반도체
    "A196170": pd.Timestamp("2020-05-29"), # 알테오젠
    "A267260": pd.Timestamp("2024-05-31"), # HD현대일렉트릭
    "A011200": pd.Timestamp("2021-05-27"), # HMM
    "A352820": pd.Timestamp("2021-02-26"), # 하이브
    "A008930": pd.Timestamp("2015-08-31"), # 한미사이언스
    "A128940": pd.Timestamp("2015-05-29"), # 한미약품
    "A034020": pd.Timestamp("2020-11-30"), # 두산에너빌리티
    "A036570": pd.Timestamp("2017-05-31"), # 엔씨소프트
    "A251270": pd.Timestamp("2017-05-31"), # 넷마블
    "A091990": pd.Timestamp("2017-08-31"), # 셀트리온헬스케어
    "A068760": pd.Timestamp("2018-05-31"), # 셀트리온제약
    "A263750": pd.Timestamp("2017-11-30"), # 펄어비스
    "A028300": pd.Timestamp("2018-11-30"), # HLB
    "A010140": pd.Timestamp("2018-05-31"), # 삼성중공업
    "A003670": pd.Timestamp("2018-08-31"), # 포스코퓨처엠
    "A005380": pd.Timestamp("2013-01-01"), # 현대차 (기존)
    "A326030": pd.Timestamp("2020-11-30"), # SK바이오팜
    "A302440": pd.Timestamp("2021-08-31"), # SK바이오사이언스
    "A066970": pd.Timestamp("2021-11-30"), # 엘앤에프
    "A293490": pd.Timestamp("2021-08-31"), # 카카오게임즈
    "A377300": pd.Timestamp("2023-02-28"), # 카카오페이
    "A005490": pd.Timestamp("2013-01-01"), # POSCO홀딩스
    "A010130": pd.Timestamp("2013-01-01"), # 고려아연
    "A010950": pd.Timestamp("2013-01-01"), # S-Oil
    "A047050": pd.Timestamp("2023-05-31"), # 포스코인터내셔널
    "A005070": pd.Timestamp("2023-05-31"), # 코스모신소재
    "A035900": pd.Timestamp("2023-08-31"), # JYP Ent.
    "A010120": pd.Timestamp("2024-08-30"), # LS ELECTRIC
    "A064350": pd.Timestamp("2024-11-25"), # 현대로템
    "A079550": pd.Timestamp("2024-11-25"), # LIG넥스원
    "A450080": pd.Timestamp("2024-02-29"), # 에코프로머티
    "A096770": pd.Timestamp("2013-01-01"), # SK이노베이션
    "A028260": pd.Timestamp("2015-09-01"), # 삼성물산
    "A035720": pd.Timestamp("2014-11-25"), # 카카오
}

def main():
    print("=" * 125)
    print("🔬 [정밀 해부] MSCI 신규 편입주 스윙 매매 내역: 편입일(D-Day) 전후 구간별 성과 분해")
    print("=" * 125)

    uni_path = f"{ROOT}/DataVault/staging/kr_equity/universe.csv"
    ext_path = f"{ROOT}/DataVault/staging/kr_equity/derived/daily_ext.parquet"
    idx_path = f"{ROOT}/DataVault/staging/kr_equity/derived/index_1d.parquet"
    px_path = f"{ROOT}/DataVault/staging/kr_equity/derived/prices_1d.parquet"

    uni = pd.read_csv(uni_path)
    name_map = uni.set_index('code')['name'].to_dict()

    df_idx = pd.read_parquet(idx_path)
    u180 = df_idx[df_idx['code'] == 'U180'].sort_values('date').set_index('date')['close']
    cal = u180.loc[(u180.index >= '2013-01-01') & (u180.index <= '2024-12-31')].index

    target_codes = list(INCLUSION_MAP.keys())

    df_p = pd.read_parquet(
        px_path,
        columns=['code', 'date', 'close', 'value'],
        filters=[('code', 'in', target_codes), ('date', '>=', pd.Timestamp('2012-01-01')), ('date', '<=', pd.Timestamp('2024-12-31'))]
    )
    df_ext = pd.read_parquet(
        ext_path,
        columns=['code', 'date', 'market_cap', 'foreign_hold_pct', 'inst_net_qty'],
        filters=[('code', 'in', target_codes), ('date', '>=', pd.Timestamp('2012-01-01')), ('date', '<=', pd.Timestamp('2024-12-31'))]
    )

    adj_mat = df_p.pivot(index='date', columns='code', values='close').sort_index()
    fgn_mat = df_ext.pivot(index='date', columns='code', values='foreign_hold_pct').sort_index()
    inst_mat = df_ext.pivot(index='date', columns='code', values='inst_net_qty').sort_index()

    ret_mat = adj_mat.pct_change().fillna(0.0)
    ma20_mat = adj_mat.rolling(20, min_periods=10).mean()
    ma200_mat = adj_mat.rolling(200, min_periods=100).mean()

    delta = adj_mat.diff()
    up = delta.clip(lower=0)
    down = -1 * delta.clip(upper=0)
    ema_up = up.ewm(com=13, adjust=False).mean()
    ema_down = down.ewm(com=13, adjust=False).mean()
    rs = ema_up / ema_down
    rsi_mat = 100 - (100 / (1 + rs))

    fgn_diff5 = fgn_mat.diff(5)
    inst_sum5 = inst_mat.rolling(5, min_periods=3).sum()

    cal_sub = cal[(cal >= '2013-03-01') & (cal <= '2024-12-31')]
    num_slots = 5
    slot_weight = 1.0 / num_slots
    cost_rate = 0.0027
    daily_cash_ret = 0.025 / 252.0

    tp1, tp2, sl, max_days = 0.10, 0.20, -0.06, 40

    slots = [{"ticker": None, "entry_price": 0.0, "entry_date": None, "peak_price": 0.0, "holding_days": 0, "remaining_ratio": 1.0, "is_cash": True} for _ in range(num_slots)]
    trades = []

    for i in range(1, len(cal_sub)):
        d = cal_sub[i]
        prev_d = cal_sub[i-1]

        sig_list = []
        for code in target_codes:
            p = adj_mat.loc[prev_d, code] if code in adj_mat.columns else np.nan
            ma20 = ma20_mat.loc[prev_d, code] if code in ma20_mat.columns else np.nan
            ma200 = ma200_mat.loc[prev_d, code] if code in ma200_mat.columns else np.nan
            rsi = rsi_mat.loc[prev_d, code] if code in rsi_mat.columns else np.nan

            if np.isnan(p) or np.isnan(ma20) or np.isnan(ma200) or p <= 0:
                continue

            cond_loc = (p / ma200 >= 0.85) and ((0.95 <= p / ma20 <= 1.03) or (rsi <= 42.0))
            f_flow = fgn_diff5.loc[prev_d, code] if code in fgn_diff5.columns else 0.0
            i_flow = inst_sum5.loc[prev_d, code] if code in inst_sum5.columns else 0.0
            if cond_loc and (f_flow > 0 or i_flow > 0):
                sig_list.append(code)

        empty_slots = [idx for idx, s in enumerate(slots) if s["is_cash"] or s["ticker"] is None]
        if empty_slots and sig_list:
            currently_held = [s["ticker"] for s in slots if not s["is_cash"] and s["ticker"] is not None]
            new_picks = [c for c in sig_list if c not in currently_held]

            for slot_idx, ticker in zip(empty_slots, new_picks):
                p_entry = adj_mat.loc[d, ticker] if ticker in adj_mat.columns else np.nan
                if not np.isnan(p_entry) and p_entry > 0:
                    slots[slot_idx] = {
                        "ticker": ticker,
                        "entry_price": p_entry,
                        "entry_date": d,
                        "peak_price": p_entry,
                        "holding_days": 0,
                        "remaining_ratio": 1.0,
                        "is_cash": False
                    }

        for s in slots:
            if s["is_cash"] or s["ticker"] is None:
                continue

            t = s["ticker"]
            curr_px = adj_mat.loc[d, t] if t in adj_mat.columns else np.nan
            if np.isnan(curr_px) or curr_px <= 0:
                continue

            s["holding_days"] += 1
            if curr_px > s["peak_price"]:
                s["peak_price"] = curr_px

            gain_from_entry = (curr_px / s["entry_price"]) - 1.0
            drop_from_peak = (curr_px / s["peak_price"]) - 1.0

            action = None
            if gain_from_entry >= tp2:
                action = "TP2"
            elif gain_from_entry >= tp1 and drop_from_peak <= -0.05:
                action = "TRAIL"
            elif gain_from_entry <= sl:
                action = "SL"
            elif s["holding_days"] >= max_days:
                action = "TIME"

            if action is not None:
                inc_date = INCLUSION_MAP.get(t, pd.Timestamp("2099-01-01"))
                # 진입일 기준 편입일과의 차이 (일수)
                days_diff = (s["entry_date"] - inc_date).days

                # 구간 분류
                if days_diff < 0:
                    phase = "1. 편입 전 매매"
                elif 0 <= days_diff <= 180:
                    phase = "2. 편입 후 6개월 이내 (위험구간)"
                else:
                    phase = "3. 편입 후 6개월 이후 (정상화구간)"

                trades.append({
                    "ticker": t,
                    "name": name_map.get(t, t),
                    "entry_date": s["entry_date"],
                    "exit_date": d,
                    "inc_date": inc_date,
                    "days_from_inc": days_diff,
                    "phase": phase,
                    "gain_pct": gain_from_entry * 100.0,
                    "action": action,
                    "hold_days": s["holding_days"]
                })
                s["is_cash"] = True
                s["ticker"] = None
            else:
                if s["remaining_ratio"] == 1.0 and gain_from_entry >= tp1:
                    s["remaining_ratio"] = 0.5

    df_tr = pd.DataFrame(trades)
    print(f"✅ 총 매매 추출 완료: {len(df_tr)}건")

    # 구간별 매매 분석
    print("\n" + "=" * 115)
    print("📊 [매매 진입 시점별 실측 성과 분해 (편입 전 vs 편입 후 6개월 이내 vs 6개월 이후)]")
    print("=" * 115)

    summary = []
    for ph, g in df_tr.groupby('phase'):
        n = len(g)
        wins = g[g['gain_pct'] > 0]
        losses = g[g['gain_pct'] <= 0]
        win_rate = len(wins) / n * 100.0
        sum_w = wins['gain_pct'].sum()
        sum_l = abs(losses['gain_pct'].sum())
        pf = sum_w / sum_l if sum_l > 0 else 0.0
        net_s = sum_w - sum_l
        avg_ret = g['gain_pct'].mean()
        sl_cnt = (g['action'] == 'SL').sum()
        sl_pct = sl_cnt / n * 100.0

        summary.append({
            "진입 구간": ph,
            "매매건수": f"{n}회 ({n/len(df_tr)*100:.1f}%)",
            "승률": f"{win_rate:.1f}%",
            "손익비(PF)": f"{pf:.2f}",
            "순합산손익": f"{net_s:+.1f}%",
            "건당 평균손익": f"{avg_ret:+.2f}%",
            "손절(SL) 비중": f"{sl_pct:.1f}% ({sl_cnt}회)"
        })

    df_ph = pd.DataFrame(summary)
    print(df_ph.to_string(index=False))
    print("=" * 115)

    # 특히 '편입 후 6개월 이내'에 들어온 종목들의 실제 사례 (에코프로 등)
    post_6m_trades = df_tr[df_tr['phase'] == '2. 편입 후 6개월 이내 (위험구간)']
    print("\n" + "=" * 115)
    print("⚠️ [편입 후 6개월 이내 위험구간에 실제로 슬롯에 들어왔던 매매들 샘플]")
    print("=" * 115)
    sample_cols = ['name', 'inc_date', 'entry_date', 'exit_date', 'action', 'gain_pct', 'days_from_inc']
    print(post_6m_trades[sample_cols].head(15).to_string(index=False))
    print("=" * 115)

if __name__ == "__main__":
    main()
