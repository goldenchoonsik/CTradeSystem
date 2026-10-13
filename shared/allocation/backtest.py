"""
일 단위 배분 백테스터
====================
목표 비중(정책 결과) → 일별 종가 체결 → 비용·세금·자산 곡선.

규약:
  - 시점: t 종가 정보로 정한 목표를 lag 거래일 뒤 종가에 체결한다(기본 lag=1, 보수적).
    실전은 '15:15 정보 → 15:30 종가 동시호가'라 lag=0 에 가깝다. 그 차이는 검증 도구가 1m 로 따로 측정한다.
  - 비용: 매수·매도 금액 × (원장 수수료 + 슬리피지), 매도에는 원장 매도세를 더한다.
    왕복 비용을 직접 합산하지 않는다(trade_costs.round_trip_cost 가 왕복 계산의 유일한 지점).
  - 세금: 매도할 때 평균단가 기준 실현이익 × tax_rules(계좌 유형, 원장 tax_class). 매도 시 원천징수로 본다.
    account_type=None 이면 세전.
  - 현금 이자는 0 (보수적).
  - 가격은 분배금 반영 수정주가를 쓴다 → 분배금은 총수익으로만 반영되고 분배금 과세는 모델링하지 않는다.
"""

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from shared.allocation.portfolio import needs_rebalance


@dataclass(frozen=True)
class BacktestConfig:
    initial_capital: float = 10_000_000.0
    lag: int = 1
    band: float = 0.0
    integer_shares: bool = False
    account_type: Optional[str] = "GENERAL"   # None = 세전

    def __post_init__(self):
        if self.lag < 0:
            raise ValueError("lag 는 0 이상")
        if self.initial_capital <= 0:
            raise ValueError("initial_capital 은 양수")


@dataclass
class BacktestResult:
    equity: pd.Series
    weights: pd.DataFrame          # 체결 후 실제 비중
    daily: pd.DataFrame            # turnover, cost, tax, rebalanced
    trades: pd.DataFrame
    config: BacktestConfig = field(repr=False, default=None)


def _symbol_costs(symbols, account_type):
    from shared.config.config_manager import get_symbol_info
    from shared.core.tax_rules import gain_tax_rate
    out = {}
    for s in symbols:
        info = get_symbol_info(s)
        out[s] = {
            "one_way": float(info.get("default_fee") or 0.0) + float(info.get("default_slippage") or 0.0),
            "sell_tax": float(info.get("sell_tax") or 0.0),
            "gain_tax": 0.0 if account_type is None else gain_tax_rate(account_type, info.get("tax_class")),
        }
    return out


def run_backtest(target_weights: pd.DataFrame, prices: pd.DataFrame,
                 cfg: BacktestConfig = BacktestConfig()) -> BacktestResult:
    symbols = list(target_weights.columns)
    missing = [s for s in symbols if s not in prices.columns]
    if missing:
        raise ValueError(f"가격이 없는 종목: {missing}")
    px = prices[symbols].astype(float).sort_index()
    if px.index.has_duplicates:
        raise ValueError("가격 인덱스에 중복 날짜")
    # 목표는 결정일 기준. 거래일 달력(가격 인덱스)에 맞춘 뒤 lag 만큼 미룬다.
    tw = target_weights.sort_index().reindex(px.index.union(target_weights.index)).ffill().reindex(px.index)
    tw = tw.shift(cfg.lag).fillna(0.0)
    if (tw.sum(axis=1) > 1.0 + 1e-9).any():
        raise ValueError("목표 비중 합이 1 을 넘는 날이 있습니다 (현금 계좌)")
    bad = (tw > 1e-12) & px.isna()
    if bad.any().any():
        d = bad.any(axis=1).idxmax()
        raise ValueError(f"{d.date()}: 가격이 없는 종목에 목표 비중이 있습니다 {list(bad.columns[bad.loc[d]])}")

    costs = _symbol_costs(symbols, cfg.account_type)
    cash = float(cfg.initial_capital)
    shares = dict.fromkeys(symbols, 0.0)
    avg = dict.fromkeys(symbols, 0.0)
    eq_rows, w_rows, d_rows, trades = [], [], [], []

    for t, prow, trow in zip(px.index, px.itertuples(index=False), tw.itertuples(index=False)):
        p = {s: float(v) for s, v in zip(symbols, prow)}
        target = {s: float(v) for s, v in zip(symbols, trow)}
        held_value = {s: shares[s] * p[s] for s in symbols if shares[s] > 0}
        if any(np.isnan(v) for v in held_value.values()):
            raise ValueError(f"{t.date()}: 보유 종목의 가격이 없습니다")
        equity = cash + sum(held_value.values())
        cur_w = {s: v / equity for s, v in held_value.items()} if equity > 0 else {}
        day_cost = day_tax = traded = 0.0
        rebalanced = needs_rebalance(cur_w, target, cfg.band)
        if rebalanced:
            delta = {s: target[s] * equity - held_value.get(s, 0.0) for s in symbols}
            # 1) 매도 먼저 (현금 확보)
            for s in symbols:
                if delta[s] >= 0 or shares[s] <= 0:
                    continue
                q = min(-delta[s] / p[s], shares[s])
                if target[s] <= 1e-12:
                    q = shares[s]                       # 목표 0 이면 전량
                elif cfg.integer_shares:
                    q = float(np.ceil(q - 1e-9))
                    q = min(q, shares[s])
                if q <= 0:
                    continue
                value = q * p[s]
                cost = value * (costs[s]["one_way"] + costs[s]["sell_tax"])
                tax = max(q * (p[s] - avg[s]), 0.0) * costs[s]["gain_tax"]
                cash += value - cost - tax
                shares[s] -= q
                if shares[s] <= 1e-12:
                    shares[s], avg[s] = 0.0, 0.0
                day_cost += cost; day_tax += tax; traded += value
                trades.append((t, s, -q, p[s], cost, tax))
            # 2) 매수 (현금이 모자라면 비율로 줄인다)
            buys = {s: d for s, d in delta.items() if d > 0}
            need = sum(d * (1 + costs[s]["one_way"]) for s, d in buys.items())
            scale = min(1.0, cash / need) if need > 0 else 0.0
            for s, d in buys.items():
                q = d * scale / p[s]                    # 비용은 need/scale 에 이미 반영됨
                if cfg.integer_shares:
                    q = float(np.floor(q + 1e-9))
                if q <= 0:
                    continue
                value = q * p[s]
                cost = value * costs[s]["one_way"]
                avg[s] = (avg[s] * shares[s] + value) / (shares[s] + q)
                shares[s] += q
                cash -= value + cost
                day_cost += cost; traded += value
                trades.append((t, s, q, p[s], cost, 0.0))
        if cash < -1e-6 * cfg.initial_capital:
            raise AssertionError(f"{t.date()}: 현금이 음수가 되었습니다 ({cash:.2f})")
        end_eq = cash + sum(shares[s] * p[s] for s in symbols if shares[s] > 0)
        eq_rows.append(end_eq)
        w_rows.append({s: (shares[s] * p[s] / end_eq if shares[s] > 0 else 0.0) for s in symbols})
        d_rows.append({"turnover": traded / equity if equity > 0 else 0.0, "cost": day_cost,
                       "tax": day_tax, "rebalanced": bool(rebalanced and traded > 0)})

    idx = px.index
    return BacktestResult(
        equity=pd.Series(eq_rows, index=idx, name="equity"),
        weights=pd.DataFrame(w_rows, index=idx, columns=symbols),
        daily=pd.DataFrame(d_rows, index=idx),
        trades=pd.DataFrame(trades, columns=["date", "symbol", "qty", "price", "cost", "tax"]),
        config=cfg,
    )


def summarize(res: BacktestResult, start=None, end=None, annualization: int = 252) -> dict:
    """성과 요약. start/end 로 구간을 자르면 그 구간 시작 자산을 1 로 다시 맞춘다."""
    eq = res.equity.loc[start:end]
    daily = res.daily.loc[start:end]
    r = eq.pct_change().dropna()
    if len(r) < 2:
        raise ValueError("구간이 너무 짧습니다")
    years = len(r) / annualization
    total = eq.iloc[-1] / eq.iloc[0]
    vol = r.std() * np.sqrt(annualization)
    dd = 1 - eq / eq.cummax()
    yearly = (1 + r).groupby(r.index.year).prod() - 1
    avg_eq = eq.mean()
    return {
        "start": str(eq.index[0].date()), "end": str(eq.index[-1].date()), "years": round(years, 2),
        "cagr": total ** (1 / years) - 1,
        "vol": vol,
        "sharpe": (r.mean() / r.std() * np.sqrt(annualization)) if r.std() > 0 else float("nan"),
        "mdd": dd.max(),
        "worst_year": yearly.min(), "best_year": yearly.max(),
        "turnover_per_year": daily["turnover"].sum() / years,
        "rebalances_per_year": daily["rebalanced"].sum() / years,
        "cost_pct_per_year": daily["cost"].sum() / avg_eq / years,
        "tax_pct_per_year": daily["tax"].sum() / avg_eq / years,
        "avg_exposure_weight": res.weights.loc[start:end].sum(axis=1).mean(),
    }
