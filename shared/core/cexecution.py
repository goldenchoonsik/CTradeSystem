"""
CTradeSystem Shared - CExecution (Core Virtual Execution & Parity Engine)

[Single Source of Truth (SSOT)]
연구/학습(CAiModelBaker)과 실전/가상매매(CTrade) 모두에서 사용되는
단일 가상 집행, 체결 시뮬레이션 및 포지션 라이프사이클 관리 코어 엔진입니다.

아키텍처 설계 원칙:
  1. Dual Operating Mode:
     - Mode A (Batch Numba Kernel): Zero-Object C-레벨 배열 기반 초고속 대량 연산 (@njit(fastmath=True))
     - Mode B (Stream Session): 실시간 1틱/1봉 단위 이벤트 상태 머신 (CExecutionStreamSession)
  2. Exact Parity (100% 무괴리):
     - 진입 엣지(min_edge_check), 슬리피지 모델, TBM 청산 우선순위를 단일 수학 공식으로 엄격 통제.
  3. Single Responsibility:
     - 지표 계산(feature_engine) 및 머신러닝(LightGBM)과 독립되어 거래 체결 및 포지션 관리만 전담.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Any, Optional, List, Tuple, Union
import numpy as np
from numba import njit

from shared.core.trade_costs import round_trip_cost, resolve_sell_tax

# ── 청산 사유 코드 (Numba 고속 정수 매핑) ──
EXIT_NONE = 0
EXIT_FORCE = 1          # 당일청산 (KRX 15:18 장마감 청산)
EXIT_EMERGENCY_SL = 2   # 비상손절 (Hard Emergency Stop-Loss, 인트라바 Low 기준)
EXIT_SL = 3             # 일반손절 (Soft Stop-Loss, 종가 기준)
EXIT_TP = 4             # 일반익절 (Soft Take-Profit, 종가 기준)
EXIT_TIME = 5           # 시간만료 (Max Hold Candles 도달)

EXIT_CODE_TO_STR = {
    EXIT_FORCE: "당일청산",
    EXIT_EMERGENCY_SL: "비상손절",
    EXIT_SL: "손절",
    EXIT_TP: "익절",
    EXIT_TIME: "시간만료",
}

STR_TO_EXIT_CODE = {v: k for k, v in EXIT_CODE_TO_STR.items()}
STR_TO_EXIT_CODE["장마감청산"] = EXIT_FORCE


@dataclass
class ExecutionConfig:
    """CExecution 실행 및 비용 파라미터 규격"""
    fee_rate: float = 0.0005              # 단방향 거래 수수료
    slip_rate: float = 0.0005             # 단방향 슬리피지
    sell_tax: float = 0.0                 # 매도 시에만 붙는 세금 (국내 주식 증권거래세 등, 원장 sell_tax)
    take_profit: float = 1.5              # 익절 배수 (ATR 대비)
    stop_loss: float = 1.0                # 손절 배수 (ATR 대비)
    emergency_sl_mult: float = 2.0        # 비상 손절 배수 (stop_loss * mult)
    max_hold_candles: int = 15            # 최대 보유 캔들 수
    cooldown_candles: int = 3             # 청산 후 재진입 쿨다운 캔들 수
    min_edge_ratio: float = 1.0           # 최소 기대수익 / 왕복비용 배수 (1.0 = 동일)
    intraday_square_off: bool = False     # 당일 청산 활성화 여부 (KRX 주식/ETF용)
    interval_minutes: float = 1.0         # 캔들 분 단위 (예: 1.0, 10.0, 30.0)
    seed_money: float = 10_000_000.0      # 백테스트 기준 시드머니

    @property
    def cost(self) -> float:
        """왕복 총 비용 비율 2 × (fee + slip) + sell_tax"""
        return round_trip_cost(self.fee_rate, self.slip_rate, self.sell_tax)


# ══════════════════════════════════════════════════════════════════
# 1. Mode A: Zero-Object Numba JIT Batch Kernel (초고속 C-레벨 연산)
# ══════════════════════════════════════════════════════════════════

@njit(fastmath=True, nogil=True)
def _simulate_batch_numba_core(
    prices: np.ndarray,             # float64[:] 캔들 종가 (Close)
    highs: np.ndarray,              # float64[:] 캔들 고가 (High)
    lows: np.ndarray,               # float64[:] 캔들 저가 (Low)
    atrs: np.ndarray,               # float64[:] 캔들 ATR
    entry_signals: np.ndarray,      # bool_[:] 진입 신호 마스크 (Probability/Macro/Meta 통과 여부)
    krx_force_exit_mask: np.ndarray,# bool_[:] KRX 당일 강제청산 마스크 (15:18 시점)
    krx_entry_allowed_mask: np.ndarray, # bool_[:] KRX 진입 허용 마스크 (09:05~15:10 등)
    fee_rate: float,
    slip_rate: float,
    take_profit: float,
    stop_loss: float,
    emergency_sl_mult: float,
    max_hold_candles: int,
    cooldown_candles: int,
    min_edge_ratio: float,
    intraday_mode: bool,
    max_trades_cap: int = 50000,
    sell_tax: float = 0.0
):
    """
    파이썬 객체/딕셔너리 생성을 0으로 배제하고 1D C-배열만으로 
    체결 및 청산을 0.01~0.05초 만에 연산하는 Numba JIT 커널입니다.
    """
    n = len(prices)
    cost = (fee_rate + slip_rate) * 2.0 + sell_tax  # = trade_costs.round_trip_cost (njit 안이라 인라인)

    # 결과 저장용 고정 크기 배열 사전 할당 (메모리 힙 할당 0)
    out_entry_idx = np.empty(max_trades_cap, dtype=np.int32)
    out_exit_idx = np.empty(max_trades_cap, dtype=np.int32)
    out_entry_price = np.empty(max_trades_cap, dtype=np.float64)
    out_exit_price = np.empty(max_trades_cap, dtype=np.float64)
    out_net_return = np.empty(max_trades_cap, dtype=np.float64)  # 퍼센트 (%)
    out_exit_code = np.empty(max_trades_cap, dtype=np.int32)
    out_mfe = np.empty(max_trades_cap, dtype=np.float64)
    out_mae = np.empty(max_trades_cap, dtype=np.float64)

    trade_count = 0
    holding = False
    entry_idx = 0
    entry_p = 0.0
    entry_atr_val = 0.0
    cooldown_until = 0

    max_high = 0.0
    min_low = 0.0

    cum_ret_mult = 1.0
    peak_ret = 1.0
    max_drawdown = 0.0
    win_trades = 0

    for idx in range(n):
        cur_p = prices[idx]
        cur_high = highs[idx]
        cur_low = lows[idx]

        if holding:
            elapsed = idx - entry_idx

            if cur_high > max_high:
                max_high = cur_high
            if cur_low < min_low:
                min_low = cur_low

            exit_code = EXIT_NONE
            exit_p = cur_p

            # ── [하이브리드 TBM 청산 우선순위 판정] ──
            # 1. 당일청산 (KRX 15:18 장마감) — 최우선
            if intraday_mode and krx_force_exit_mask[idx]:
                exit_code = EXIT_FORCE
                exit_p = cur_p
            else:
                # 2. 비상손절 (Hard Emergency SL, 인트라바 저가 기준)
                emerg_p = entry_p - (entry_atr_val * emergency_sl_mult)
                if cur_low <= emerg_p:
                    exit_code = EXIT_EMERGENCY_SL
                    exit_p = emerg_p
                else:
                    # 3. 일반손절 (Soft SL, 캔들 종가 기준)
                    sl_p = entry_p - (entry_atr_val * stop_loss)
                    if cur_p <= sl_p:
                        exit_code = EXIT_SL
                        exit_p = cur_p
                    else:
                        # 4. 일반익절 (Soft TP, 캔들 종가 기준)
                        tp_p = entry_p + (entry_atr_val * take_profit)
                        if cur_p >= tp_p:
                            exit_code = EXIT_TP
                            exit_p = cur_p
                        else:
                            # 5. 시간만료 (경과 캔들 기준)
                            if elapsed >= max_hold_candles:
                                exit_code = EXIT_TIME
                                exit_p = cur_p

            if exit_code != EXIT_NONE:
                # 청산 체결
                gross = (exit_p - entry_p) / entry_p
                net_ret = (gross - cost) * 100.0

                cum_ret_mult *= (1.0 + (net_ret / 100.0))
                if cum_ret_mult > peak_ret:
                    peak_ret = cum_ret_mult
                dd = (peak_ret - cum_ret_mult) / peak_ret if peak_ret > 0 else 0.0
                if dd > max_drawdown:
                    max_drawdown = dd

                if net_ret > 0:
                    win_trades += 1

                mfe = ((max_high - entry_p) / entry_p) * 100.0 if entry_p > 0 else 0.0
                mae = ((min_low - entry_p) / entry_p) * 100.0 if entry_p > 0 else 0.0

                if trade_count < max_trades_cap:
                    out_entry_idx[trade_count] = entry_idx
                    out_exit_idx[trade_count] = idx
                    out_entry_price[trade_count] = entry_p
                    out_exit_price[trade_count] = exit_p
                    out_net_return[trade_count] = net_ret
                    out_exit_code[trade_count] = exit_code
                    out_mfe[trade_count] = mfe
                    out_mae[trade_count] = mae
                    trade_count += 1

                holding = False
                cooldown_until = idx + cooldown_candles

        else:
            # 미보유 상태: 진입 검사
            if idx >= cooldown_until:
                if krx_entry_allowed_mask[idx]:
                    if entry_signals[idx]:
                        atr_val = atrs[idx]
                        if atr_val <= 0:
                            atr_val = cur_p * 0.015

                        # 최소 기대 엣지 검증: ATR * TP >= (cur_p * cost * min_edge_ratio)
                        expected_gain = atr_val * take_profit
                        round_trip_cost = cur_p * cost
                        if expected_gain >= (round_trip_cost * min_edge_ratio):
                            holding = True
                            entry_idx = idx
                            entry_p = cur_p
                            entry_atr_val = atr_val
                            max_high = cur_high
                            min_low = cur_low

    total_trades = trade_count
    net_return_pct = (cum_ret_mult - 1.0) * 100.0
    win_rate = (win_trades / total_trades * 100.0) if total_trades > 0 else 0.0

    return (
        out_entry_idx[:trade_count],
        out_exit_idx[:trade_count],
        out_entry_price[:trade_count],
        out_exit_price[:trade_count],
        out_net_return[:trade_count],
        out_exit_code[:trade_count],
        out_mfe[:trade_count],
        out_mae[:trade_count],
        total_trades,
        win_trades,
        win_rate,
        net_return_pct,
        max_drawdown * 100.0
    )


# ══════════════════════════════════════════════════════════════════
# 2. Mode B: Stream Step Evaluator (실시간 이벤트 상태 머신)
# ══════════════════════════════════════════════════════════════════

class CExecutionStreamSession:
    """
    실시간 스트리밍(CTrade)을 위한 상태 머신 세션
    
    Mode A(Numba Kernel)와 수학적으로 100% 동일한 규칙을 공유하며,
    실시간 틱 및 완성 캔들 단위로 진입 엣지와 TBM 청산을 판정합니다.
    """

    def __init__(self, config: ExecutionConfig):
        self.config = config
        self.has_position: bool = False
        self.entry_price: float = 0.0
        self.entry_atr: float = 0.0
        self.entry_candle_idx: int = 0
        self.entry_time: Optional[datetime] = None
        self.cooldown_until_idx: int = 0
        self.is_pending_exit: bool = False

        self.max_hold_high: float = 0.0
        self.min_hold_low: float = 0.0

    def check_entry_edge(self, price: float, atr: float, current_candle_idx: int) -> bool:
        """최소 수익성 엣지 검증 (SmartOrderExecutor.min_edge_check 동기화)"""
        if self.has_position or self.is_pending_exit:
            return False
        if current_candle_idx < self.cooldown_until_idx:
            return False
        if price <= 0:
            return False

        effective_atr = atr if atr > 0 else (price * 0.015)
        expected_gain = effective_atr * self.config.take_profit
        round_trip_cost = price * self.config.cost

        return expected_gain >= (round_trip_cost * self.config.min_edge_ratio)

    def check_exit(
        self,
        current_price: float,
        current_low: float,
        current_candle_idx: int,
        is_candle_close: bool = True,
        force_exit: bool = False
    ) -> Optional[Dict[str, Any]]:
        """
        하이브리드 TBM 청산 조건 판정
        - shared.tbm_executor.resolve_exit와 100% 동일한 규격 반환
        """
        if not self.has_position:
            return None

        # MFE / MAE 갱신
        if current_price > self.max_hold_high:
            self.max_hold_high = current_price
        if current_low < self.min_hold_low:
            self.min_hold_low = current_low

        elapsed = current_candle_idx - self.entry_candle_idx
        hold_mins = int(round(elapsed * self.config.interval_minutes))

        def _calc_net_return(exit_price: float) -> float:
            gross = (exit_price - self.entry_price) / self.entry_price
            return (gross - self.config.cost) * 100.0

        # 1. 당일청산 (KRX 15:18 등 강제 청산)
        if force_exit:
            return {
                "exit_type": "당일청산",
                "exit_price": current_price,
                "net_return": _calc_net_return(current_price),
                "hold_candles": elapsed,
                "hold_mins": hold_mins
            }

        # 2. 비상손절 (Hard Emergency SL) — 틱/캔들 상시 최우선 발동
        emerg_p = self.entry_price - (self.entry_atr * self.config.emergency_sl_mult)
        if current_low <= emerg_p:
            return {
                "exit_type": "비상손절",
                "exit_price": emerg_p,
                "net_return": _calc_net_return(emerg_p),
                "hold_candles": elapsed,
                "hold_mins": hold_mins
            }

        # 실시간 틱 판정 시에는 Soft TP/SL/시간만료는 보류 (휩소 방지)
        if not is_candle_close:
            return None

        # 3. 일반손절 (Soft SL)
        sl_p = self.entry_price - (self.entry_atr * self.config.stop_loss)
        if current_price <= sl_p:
            return {
                "exit_type": "손절",
                "exit_price": current_price,
                "net_return": _calc_net_return(current_price),
                "hold_candles": elapsed,
                "hold_mins": hold_mins
            }

        # 4. 일반익절 (Soft TP)
        tp_p = self.entry_price + (self.entry_atr * self.config.take_profit)
        if current_price >= tp_p:
            return {
                "exit_type": "익절",
                "exit_price": current_price,
                "net_return": _calc_net_return(current_price),
                "hold_candles": elapsed,
                "hold_mins": hold_mins
            }

        # 5. 시간만료
        if elapsed >= self.config.max_hold_candles:
            return {
                "exit_type": "시간만료",
                "exit_price": current_price,
                "net_return": _calc_net_return(current_price),
                "hold_candles": elapsed,
                "hold_mins": hold_mins
            }

        return None

    def on_buy_filled(self, real_entry_price: float, entry_atr: float, candle_idx: int, timestamp: Optional[datetime] = None):
        """매수 체결 완료 시 포지션 등록"""
        self.has_position = True
        self.is_pending_exit = False
        self.entry_price = float(real_entry_price)
        self.entry_atr = float(entry_atr) if entry_atr > 0 else (real_entry_price * 0.015)
        self.entry_candle_idx = int(candle_idx)
        self.entry_time = timestamp
        self.max_hold_high = self.entry_price
        self.min_hold_low = self.entry_price

    def on_sell_completed(self, candle_idx: int):
        """매도 청산 완료 시 쿨다운 설정 및 포지션 초기화"""
        self.has_position = False
        self.is_pending_exit = False
        self.entry_price = 0.0
        self.entry_atr = 0.0
        self.cooldown_until_idx = int(candle_idx) + self.config.cooldown_candles


# ══════════════════════════════════════════════════════════════════
# 3. CExecution Façade (단일 진실 공급원 최상위 인터페이스)
# ══════════════════════════════════════════════════════════════════

class CExecution:
    """CExecution 종합 파사드 클래스"""

    @staticmethod
    def create_config_from_params(params: dict, config: dict) -> ExecutionConfig:
        """best_params 및 config 딕셔너리로부터 ExecutionConfig 객체 생성"""
        from shared.core.market_utils import detect_market_type, parse_interval_minutes

        interval = config.get("INTERVAL", "1m")
        interval_min = parse_interval_minutes(interval)

        optuna_cfg = config.get("OPTUNA_CONFIG", {})
        fee_rate = float(config.get("FEE_RATE", config.get("FEE_UPBIT", config.get("fee_rate", optuna_cfg.get("fee_upbit", 0.0005)))))
        slip_rate = float(config.get("SLIPPAGE_RATE", config.get("SLIPPAGE", config.get("slippage_rate", optuna_cfg.get("slippage", 0.0005)))))
        sell_tax = resolve_sell_tax(config)

        take_profit = float(params.get("take_profit", 1.5))
        stop_loss = float(params.get("stop_loss", 1.0))
        emergency_mult = float(params.get("emergency_sl_mult", stop_loss * 2.0))
        max_hold = int(params.get("max_hold_candles", params.get("predict_ahead_candles", 15)))

        cooldown_min = float(params.get("cooldown_minutes", config.get("COOLDOWN_MINUTES", 3)))
        cooldown_candles = max(1, int(cooldown_min / interval_min))

        market_type = detect_market_type(config)
        intraday = (market_type == "KRX" and config.get("INTRADAY_SQUARE_OFF", True))
        seed_money = float(config.get("SEED_MONEY", config.get("SIMULATION_INITIAL_BALANCE", config.get("INVEST_AMOUNT", 10_000_000.0))))

        return ExecutionConfig(
            fee_rate=fee_rate,
            slip_rate=slip_rate,
            sell_tax=sell_tax,
            take_profit=take_profit,
            stop_loss=stop_loss,
            emergency_sl_mult=emergency_mult,
            max_hold_candles=max_hold,
            cooldown_candles=cooldown_candles,
            min_edge_ratio=float(config.get("MIN_EDGE_RATIO", 1.0)),
            intraday_square_off=intraday,
            interval_minutes=interval_min,
            seed_money=seed_money
        )

    @classmethod
    def simulate_batch(
        cls,
        prices: np.ndarray,
        highs: np.ndarray,
        lows: np.ndarray,
        atrs: np.ndarray,
        entry_signals: np.ndarray,
        krx_force_exit_mask: np.ndarray,
        krx_entry_allowed_mask: np.ndarray,
        exec_config: ExecutionConfig,
        timestamps: Optional[np.ndarray] = None,
        preds: Optional[np.ndarray] = None,
        entry_features_dict: Optional[Dict[str, np.ndarray]] = None
    ) -> Dict[str, Any]:
        """
        Numba 배치 커널을 호출하여 초고속으로 백테스트를 수행하고,
        CAiModelBaker 대시보드 및 기존 backtester와 100% 호환되는 딕셔너리를 반환합니다.
        """
        # C-연속 1D 배열 보장
        prices_c = np.ascontiguousarray(prices, dtype=np.float64)
        highs_c = np.ascontiguousarray(highs, dtype=np.float64)
        lows_c = np.ascontiguousarray(lows, dtype=np.float64)
        atrs_c = np.ascontiguousarray(atrs, dtype=np.float64)
        signals_c = np.ascontiguousarray(entry_signals, dtype=np.bool_)
        krx_force_c = np.ascontiguousarray(krx_force_exit_mask, dtype=np.bool_)
        krx_entry_c = np.ascontiguousarray(krx_entry_allowed_mask, dtype=np.bool_)

        # 데이터 크기에 맞춰 트레이드 캡을 동적 설정 (오버플로우 방지)
        max_trades_cap = max(50000, len(prices_c) // 2)

        (
            entry_idxs,
            exit_idxs,
            entry_prices,
            exit_prices,
            net_returns,
            exit_codes,
            mfes,
            maes,
            total_trades,
            win_trades,
            win_rate,
            net_return_pct,
            mdd_pct
        ) = _simulate_batch_numba_core(
            prices=prices_c,
            highs=highs_c,
            lows=lows_c,
            atrs=atrs_c,
            entry_signals=signals_c,
            krx_force_exit_mask=krx_force_c,
            krx_entry_allowed_mask=krx_entry_c,
            fee_rate=exec_config.fee_rate,
            slip_rate=exec_config.slip_rate,
            take_profit=exec_config.take_profit,
            stop_loss=exec_config.stop_loss,
            emergency_sl_mult=exec_config.emergency_sl_mult,
            max_hold_candles=exec_config.max_hold_candles,
            cooldown_candles=exec_config.cooldown_candles,
            min_edge_ratio=exec_config.min_edge_ratio,
            intraday_mode=exec_config.intraday_square_off,
            max_trades_cap=max_trades_cap,
            sell_tax=exec_config.sell_tax
        )

        # 트레이드 상세 로그 리스트 구성 (UI 렌더링용)
        trade_logs = []
        seed = exec_config.seed_money
        fee_rate = exec_config.fee_rate
        slip_rate = exec_config.slip_rate
        sell_tax = exec_config.sell_tax
        inv_min = exec_config.interval_minutes

        for i in range(total_trades):
            e_idx = int(entry_idxs[i])
            x_idx = int(exit_idxs[i])
            e_price = float(entry_prices[i])
            x_price = float(exit_prices[i])
            net_ret = float(net_returns[i])
            code = int(exit_codes[i])
            exit_type_str = EXIT_CODE_TO_STR.get(code, "청산")

            qty = seed / e_price if e_price > 0 else 0.0
            elapsed = x_idx - e_idx
            hold_mins_val = round(elapsed * inv_min, 1) if inv_min < 1.0 else int(round(elapsed * inv_min))

            e_time = timestamps[e_idx] if timestamps is not None and len(timestamps) > e_idx else e_idx
            x_time = timestamps[x_idx] if timestamps is not None and len(timestamps) > x_idx else x_idx
            prob_val = float(preds[e_idx]) if preds is not None and len(preds) > e_idx else 0.0

            # 피처 스냅샷
            feat_snap = {}
            if entry_features_dict:
                for f_name, f_arr in entry_features_dict.items():
                    if len(f_arr) > e_idx:
                        feat_snap[f_name] = float(f_arr[e_idx])

            trade_logs.append({
                "entry_time": e_time,
                "exit_time": x_time,
                "entry_price": e_price,
                "exit_price": x_price,
                "qty": qty,
                "fee_in": seed * fee_rate,
                "fee_out": (qty * x_price) * fee_rate,
                "slip_in": seed * slip_rate,
                "slip_out": (qty * x_price) * slip_rate,
                "tax_out": (qty * x_price) * sell_tax,
                "hold_mins": hold_mins_val,
                "prob": prob_val,
                "exit_type": exit_type_str,
                "net_return": net_ret,
                "mfe": float(mfes[i]),
                "mae": float(maes[i]),
                "entry_features": feat_snap
            })

        return {
            "total_trades": int(total_trades),
            "win_trades": int(win_trades),
            "win_rate": float(win_rate),
            "net_return": float(net_return_pct),
            "mdd": float(mdd_pct),
            "trade_logs": trade_logs
        }

    @classmethod
    def create_stream_session(cls, config: ExecutionConfig) -> CExecutionStreamSession:
        """실시간 스트림 세션 생성"""
        return CExecutionStreamSession(config)


# ── 이전 버전 호환성 별칭 (Backward Compatibility Aliases) ──
CAxiom = CExecution
CAxiomStreamSession = CExecutionStreamSession

