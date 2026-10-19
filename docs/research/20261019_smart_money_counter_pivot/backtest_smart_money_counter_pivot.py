"""
맥점 반격 (Smart Money Counter-Pivot) 전략 백테스터
--------------------------------------------------
한국 시장(국장) 특화: 대량 거래량 기준봉 포착 후 거래량 급감(Volume Dry-Up) 눌림목 2차 반등 공략
"""

import os
import glob
import pandas as pd
import numpy as np

def run_backtest(df: pd.DataFrame, symbol_name: str, 
                 vol_mult: float = 2.5, 
                 price_pct_threshold: float = 3.5,
                 dry_up_ratio: float = 0.40,
                 tp_pct: float = 5.0, 
                 sl_pct: float = 2.5, 
                 max_hold_days: int = 5,
                 fee_pct: float = 0.25):
    """
    df columns: timestamp, open, high, low, close, volume, trade_amount
    """
    df = df.sort_values("timestamp").reset_index(drop=True).copy()
    
    # 20일 거래량 이동평균 계산
    df["vol_ma20"] = df["volume"].rolling(20).mean()
    df["change_pct"] = (df["close"] / df["close"].shift(1) - 1.0) * 100.0
    
    trades = []
    in_position = False
    entry_idx = 0
    entry_price = 0.0
    entry_date = None
    
    # 기준봉 추적
    active_ref_candle = None # dict(idx, date, open, high, low, close, volume)
    
    for i in range(21, len(df)):
        current_date = df.loc[i, "timestamp"]
        c_open = df.loc[i, "open"]
        c_high = df.loc[i, "high"]
        c_low = df.loc[i, "low"]
        c_close = df.loc[i, "close"]
        c_vol = df.loc[i, "volume"]
        
        # 1. 포지션 보유 중인 경우 청산 체크
        if in_position:
            days_held = i - entry_idx
            # 당일 고가가 익절선 도달
            tp_price = entry_price * (1.0 + tp_pct / 100.0)
            sl_price = entry_price * (1.0 - sl_pct / 100.0)
            
            exit_price = None
            exit_reason = None
            
            # SL 우선 판정 (보수적)
            if c_low <= sl_price:
                exit_price = sl_price
                exit_reason = "손절 (SL)"
            elif c_high >= tp_price:
                exit_price = tp_price
                exit_reason = "익절 (TP)"
            elif days_held >= max_hold_days:
                exit_price = c_close
                exit_reason = f"시간만료 ({max_hold_days}일)"
                
            if exit_price is not None:
                # 수익률 산출 (왕복 수수료 차감)
                raw_ret = (exit_price / entry_price - 1.0) * 100.0
                net_ret = raw_ret - fee_pct
                trades.append({
                    "symbol": symbol_name,
                    "entry_date": entry_date,
                    "exit_date": current_date,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "hold_days": days_held,
                    "net_ret": net_ret,
                    "exit_reason": exit_reason
                })
                in_position = False
                active_ref_candle = None
                continue # 청산 당일 재진입 방지
        
        # 2. 포지션 미보유 시 신규 진입 체크
        # 기준봉 탐지 (거래량 MA20 대비 2.5배 이상 & +3.5% 이상 양봉)
        prev_vol_ma = df.loc[i, "vol_ma20"]
        if prev_vol_ma > 0 and c_vol >= prev_vol_ma * vol_mult and df.loc[i, "change_pct"] >= price_pct_threshold:
            active_ref_candle = {
                "idx": i,
                "date": current_date,
                "open": c_open,
                "high": c_high,
                "low": c_low,
                "close": c_close,
                "volume": c_vol
            }
            continue
            
        # 기준봉 유효성 검사 (5일 이내 경과)
        if active_ref_candle is not None:
            days_since_ref = i - active_ref_candle["idx"]
            if days_since_ref > 5:
                active_ref_candle = None
            else:
                # 눌림목 조건 체크:
                # - 주가가 기준봉 시가 훼손 안함
                # - 당일 거래량이 기준봉 거래량의 dry_up_ratio 이하
                if c_low >= active_ref_candle["open"] and c_vol <= active_ref_candle["volume"] * dry_up_ratio:
                    # 익일 시가 또는 당일 종가 매수 진입 판정 (여기서는 당일 종가 진입 모델)
                    in_position = True
                    entry_idx = i
                    entry_price = c_close
                    entry_date = current_date
                    active_ref_candle = None # 진입했으므로 기준봉 소멸
                    
    trades_df = pd.DataFrame(trades)
    return trades_df

def print_summary(trades_df: pd.DataFrame, title: str):
    print(f"\n=======================================================")
    print(f"📊 [{title}] 백테스트 결과 분석")
    print(f"=======================================================")
    if len(trades_df) == 0:
        print("체결된 거래가 없습니다.")
        return
        
    total_trades = len(trades_df)
    win_trades = trades_df[trades_df["net_ret"] > 0]
    loss_trades = trades_df[trades_df["net_ret"] <= 0]
    
    win_rate = len(win_trades) / total_trades * 100.0
    avg_ret = trades_df["net_ret"].mean()
    cum_ret = ((1.0 + trades_df["net_ret"] / 100.0).cumprod().iloc[-1] - 1.0) * 100.0
    
    total_win_sum = win_trades["net_ret"].sum() if len(win_trades) > 0 else 0
    total_loss_sum = abs(loss_trades["net_ret"].sum()) if len(loss_trades) > 0 else 0.0001
    profit_factor = total_win_sum / total_loss_sum
    
    avg_hold = trades_df["hold_days"].mean()
    
    # MDD 계산
    cum_series = (1.0 + trades_df["net_ret"] / 100.0).cumprod()
    peak = cum_series.cummax()
    drawdown = (cum_series - peak) / peak * 100.0
    mdd = drawdown.min()
    
    print(f"• 총 거래 횟수     : {total_trades}회")
    print(f"• 승률 (Win Rate)  : {win_rate:.2f}% ({len(win_trades)}승 / {len(loss_trades)}패)")
    print(f"• 평균 거래 수익률 : {avg_ret:+.2f}% (수수료 0.25% 차감 후)")
    print(f"• 누적 복리 수익률 : {cum_ret:+.2f}%")
    print(f"• 손익비 (P.F)     : {profit_factor:.2f}")
    print(f"• 최대 낙폭 (MDD)  : {mdd:.2f}%")
    print(f"• 평균 보유 일수   : {avg_hold:.1f}일")
    print(f"\n[청산 사유 분포]")
    for reason, count in trades_df["exit_reason"].value_counts().items():
        print(f"  - {reason}: {count}회 ({count/total_trades*100:.1f}%)")
    print(f"=======================================================")

def run_enhanced_pullback(df: pd.DataFrame, symbol_name: str, 
                          vol_mult: float = 1.8, 
                          ma_period: int = 20,
                          tp_mult: float = 2.0, 
                          sl_mult: float = 1.5, 
                          max_hold_days: int = 10,
                          fee_pct: float = 0.25):
    """
    맥점 반격 v2 (변동성 적응형 ATR + 20일선 추세 눌림목)
    1. 20일 이동평균선 위에 위치 (상승 추세)
    2. 거래량 1.8배 이상 터진 기준봉 이후
    3. 5일~20일선 부근으로 눌림목 발생 시 진입
    4. 청산: ATR(14) 기반 동적 익절/손절
    """
    df = df.sort_values("timestamp").reset_index(drop=True).copy()
    
    # 지표 계산
    df["vol_ma20"] = df["volume"].rolling(ma_period).mean()
    df["close_ma20"] = df["close"].rolling(ma_period).mean()
    
    # ATR(14) 계산
    tr1 = df["high"] - df["low"]
    tr2 = (df["high"] - df["close"].shift(1)).abs()
    tr3 = (df["low"] - df["close"].shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df["atr14"] = tr.rolling(14).mean()
    
    trades = []
    in_position = False
    entry_idx = 0
    entry_price = 0.0
    entry_atr = 0.0
    entry_date = None
    
    active_ref_candle = None
    
    for i in range(ma_period + 15, len(df)):
        current_date = df.loc[i, "timestamp"]
        c_open = df.loc[i, "open"]
        c_high = df.loc[i, "high"]
        c_low = df.loc[i, "low"]
        c_close = df.loc[i, "close"]
        c_vol = df.loc[i, "volume"]
        c_ma20 = df.loc[i, "close_ma20"]
        c_atr = df.loc[i, "atr14"]
        
        if in_position:
            days_held = i - entry_idx
            tp_price = entry_price + (entry_atr * tp_mult)
            sl_price = entry_price - (entry_atr * sl_mult)
            
            exit_price = None
            exit_reason = None
            
            if c_low <= sl_price:
                exit_price = sl_price
                exit_reason = "손절 (SL)"
            elif c_high >= tp_price:
                exit_price = tp_price
                exit_reason = "익절 (TP)"
            elif days_held >= max_hold_days:
                exit_price = c_close
                exit_reason = f"시간만료 ({max_hold_days}일)"
                
            if exit_price is not None:
                raw_ret = (exit_price / entry_price - 1.0) * 100.0
                net_ret = raw_ret - fee_pct
                trades.append({
                    "symbol": symbol_name,
                    "entry_date": entry_date,
                    "exit_date": current_date,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "hold_days": days_held,
                    "net_ret": net_ret,
                    "exit_reason": exit_reason
                })
                in_position = False
                active_ref_candle = None
                continue
                
        # 기준봉 탐지 (20일선 위 + 거래량 1.8배 + 양봉)
        prev_vol_ma = df.loc[i, "vol_ma20"]
        if c_close > c_ma20 and c_vol >= prev_vol_ma * vol_mult and c_close > c_open:
            active_ref_candle = {
                "idx": i,
                "high": c_high,
                "low": c_low,
                "close": c_close,
                "volume": c_vol
            }
            continue
            
        if active_ref_candle is not None:
            days_since = i - active_ref_candle["idx"]
            if days_since > 6:
                active_ref_candle = None
            else:
                # 눌림목: 20일선 근처(이격도 98%~103%) & 거래량이 기준봉 대비 50% 이하 감소
                disparity = c_close / c_ma20 * 100.0
                if 98.0 <= disparity <= 103.5 and c_vol <= active_ref_candle["volume"] * 0.55:
                    in_position = True
                    entry_idx = i
                    entry_price = c_close
                    entry_atr = c_atr
                    entry_date = current_date
                    active_ref_candle = None
                    
    return pd.DataFrame(trades)

def run_oversold_reversal(df: pd.DataFrame, symbol_name: str,
                          rsi_period: int = 14,
                          rsi_buy_thresh: float = 32.0,
                          fee_pct: float = 0.25):
    """
    국장 특화 박스권 역발상 과매도 반등 (Oversold Counter-Strike)
    - RSI(14) <= 32 진입
    - 청산: 20일 이동평균선 터치 시 익절, 또는 -4.0% 손절, 최대 8일 보유
    """
    df = df.sort_values("timestamp").reset_index(drop=True).copy()
    
    # RSI 계산
    delta = df["close"].diff()
    gain = (delta.where(delta > 0, 0)).rolling(rsi_period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(rsi_period).mean()
    rs = gain / (loss + 1e-9)
    df["rsi"] = 100 - (100 / (1 + rs))
    df["close_ma20"] = df["close"].rolling(20).mean()
    
    trades = []
    in_position = False
    entry_idx = 0
    entry_price = 0.0
    entry_date = None
    
    for i in range(30, len(df)):
        current_date = df.loc[i, "timestamp"]
        c_open = df.loc[i, "open"]
        c_high = df.loc[i, "high"]
        c_low = df.loc[i, "low"]
        c_close = df.loc[i, "close"]
        c_ma20 = df.loc[i, "close_ma20"]
        c_rsi = df.loc[i, "rsi"]
        
        if in_position:
            days_held = i - entry_idx
            sl_price = entry_price * 0.96 # -4% 손절
            tp_price = c_ma20 # 20일선 복귀 시 익절
            
            exit_price = None
            exit_reason = None
            
            if c_low <= sl_price:
                exit_price = sl_price
                exit_reason = "손절 (-4%)"
            elif c_high >= tp_price and tp_price > entry_price * 1.01:
                exit_price = tp_price
                exit_reason = "익절 (20일선 회귀)"
            elif days_held >= 8:
                exit_price = c_close
                exit_reason = "시간만료 (8일)"
                
            if exit_price is not None:
                raw_ret = (exit_price / entry_price - 1.0) * 100.0
                net_ret = raw_ret - fee_pct
                trades.append({
                    "symbol": symbol_name,
                    "entry_date": entry_date,
                    "exit_date": current_date,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "hold_days": days_held,
                    "net_ret": net_ret,
                    "exit_reason": exit_reason
                })
                in_position = False
                continue
                
        # RSI 과매도 진입
        if c_rsi <= rsi_buy_thresh and c_close > c_open: # 양봉 발생 시
            in_position = True
            entry_idx = i
            entry_price = c_close
            entry_date = current_date
            
    return pd.DataFrame(trades)

if __name__ == "__main__":
    assets = [
        ("000660", "SK하이닉스"),
        ("005930", "삼성전자"),
        ("122630", "KODEX 레버리지"),
    ]
    
    print("\n" + "#" * 60)
    print("🏆 [테스트 1] 맥점 반격 v2 (변동성 ATR 적응형 눌림목)")
    print("#" * 60)
    all_trades_v2 = []
    for symbol, name in assets:
        fpath = f"DataVault/normalized/candles_1d/{symbol}_1d.parquet"
        if os.path.exists(fpath):
            df = pd.read_parquet(fpath)
            trades = run_enhanced_pullback(df, f"{name}({symbol})")
            print_summary(trades, f"{name} - 맥점 반격 v2")
            if len(trades) > 0:
                all_trades_v2.append(trades)
    if all_trades_v2:
        print_summary(pd.concat(all_trades_v2, ignore_index=True), "맥점 반격 v2 통합 성과")
        
    print("\n" + "#" * 60)
    print("🏆 [테스트 2] 국장 특화 역발상 과매도 회귀 (Oversold Reversal)")
    print("#" * 60)
    all_trades_os = []
    for symbol, name in assets:
        fpath = f"DataVault/normalized/candles_1d/{symbol}_1d.parquet"
        if os.path.exists(fpath):
            df = pd.read_parquet(fpath)
            trades = run_oversold_reversal(df, f"{name}({symbol})")
            print_summary(trades, f"{name} - 과매도 회귀")
            if len(trades) > 0:
                all_trades_os.append(trades)
    if all_trades_os:
        print_summary(pd.concat(all_trades_os, ignore_index=True), "과매도 회귀 통합 성과")

