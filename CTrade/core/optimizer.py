import copy
import logging
from typing import Dict, Any, List
from core.simulator import VirtualTrader
from core.rule_engine import RuleEvaluator

class ShadowTrader:
    def __init__(self, variant_id: str, config: dict, fee_rate: float = 0.0005):
        self.variant_id = variant_id
        self.config = config
        self.evaluator = RuleEvaluator(config)
        self.simulator = VirtualTrader(initial_balance=10000000.0, fee_rate=fee_rate, silent=True)

# 업그레이드 판정 상수 (표본/개선폭 기준을 한 곳에서 조정)
MIN_TRADES_FOR_UPGRADE = 3          # 최소 거래 수 (표본)
MIN_WIN_RATE_FOR_UPGRADE = 50.0     # 최소 승률(%)
MIN_IMPROVEMENT_RATIO = 0.10        # base 손익 절대값 대비 최소 개선 비율
MIN_IMPROVEMENT_ABS = 0.0           # 최소 개선폭(원). base<=0 이어도 이 값 + 엄격 초과 필요


class StrategyOptimizer:
    def __init__(self):
        self.shadow_targets: Dict[str, List[ShadowTrader]] = {}
        
    def init_symbol(self, symbol: str, base_config: dict):
        """종목별로 섀도우 트레이더들을 초기화합니다."""
        if not base_config.get("enabled", True):
            return
            
        variants = self._generate_variants(base_config)
        self.shadow_targets[symbol] = [ShadowTrader(v_id, v_cfg) for v_id, v_cfg in variants.items()]
        logging.info(f"[Optimizer] Initialized {len(variants)} shadow strategies for {symbol}")

    def _generate_variants(self, base_config: dict) -> Dict[str, dict]:
        """수치를 미세 조정한 8개의 변종(Variant) 설정을 생성합니다."""
        variants = {}
        
        # 1. Base (원본)
        variants["base"] = copy.deepcopy(base_config)
        
        # 2. Aggressive (공격적: 진입장벽 낮춤)
        agg_cfg = copy.deepcopy(base_config)
        self._modify_thresholds(agg_cfg, "buy_conditions", "local_time_intensity", -10)
        self._modify_thresholds(agg_cfg, "buy_conditions", "binance_futures_intensity", -10)
        self._modify_thresholds(agg_cfg, "buy_conditions", "local_imbalance", -5)
        variants["aggressive"] = agg_cfg
        
        # 3. Hyper-Aggressive (초공격적: 잦은 진입)
        hagg_cfg = copy.deepcopy(base_config)
        self._modify_thresholds(hagg_cfg, "buy_conditions", "local_time_intensity", -20)
        self._modify_thresholds(hagg_cfg, "buy_conditions", "binance_futures_intensity", -20)
        self._modify_thresholds(hagg_cfg, "buy_conditions", "local_imbalance", -10)
        variants["hyper_aggressive"] = hagg_cfg
        
        # 4. Conservative (보수적: 진입장벽 높임)
        cons_cfg = copy.deepcopy(base_config)
        self._modify_thresholds(cons_cfg, "buy_conditions", "local_time_intensity", 10)
        self._modify_thresholds(cons_cfg, "buy_conditions", "binance_futures_intensity", 10)
        self._modify_thresholds(cons_cfg, "buy_conditions", "local_imbalance", 5)
        variants["conservative"] = cons_cfg
        
        # 5. Very Conservative (매우 보수적: 극한의 장벽)
        vcons_cfg = copy.deepcopy(base_config)
        self._modify_thresholds(vcons_cfg, "buy_conditions", "local_time_intensity", 20)
        self._modify_thresholds(vcons_cfg, "buy_conditions", "binance_futures_intensity", 20)
        self._modify_thresholds(vcons_cfg, "buy_conditions", "local_imbalance", 10)
        variants["very_conservative"] = vcons_cfg

        # 6. Imbalance-Heavy (매수벽 지지 중시)
        imb_cfg = copy.deepcopy(base_config)
        self._modify_thresholds(imb_cfg, "buy_conditions", "local_imbalance", 20)
        self._modify_thresholds(imb_cfg, "buy_conditions", "local_time_intensity", -20)
        variants["imbalance_heavy"] = imb_cfg

        # 7. Momentum-Heavy (체결 강도 폭발 중시)
        mom_cfg = copy.deepcopy(base_config)
        self._modify_thresholds(mom_cfg, "buy_conditions", "local_time_intensity", 30)
        self._modify_thresholds(mom_cfg, "buy_conditions", "binance_futures_intensity", 30)
        variants["momentum_heavy"] = mom_cfg

        # 8. Quick-Profit (단기 익절: 회전율 중시)
        qprof_cfg = copy.deepcopy(base_config)
        self._modify_thresholds(qprof_cfg, "sell_conditions", "profit_rate", -0.2)
        variants["quick_profit"] = qprof_cfg
        
        return variants
        
    def _modify_thresholds(self, config: dict, condition_type: str, target: str, offset: float):
        """특정 룰의 수치를 찾아 오프셋만큼 더합니다 (중첩 그룹 재귀 순회).

        offset 은 '진입 장벽' 기준(+: 보수적, -: 공격적)이다. 매수 조건에서 '<'/'<=' 룰은
        값이 낮을수록 장벽이 높으므로 부호를 반전해 적용한다.
        """
        if condition_type in config and isinstance(config[condition_type], dict):
            self._modify_group(config[condition_type], target, offset,
                               invert_lt=(condition_type == "buy_conditions"))

    def _modify_group(self, group: dict, target: str, offset: float, invert_lt: bool):
        for rule in group.get("rules", []) or []:
            if not isinstance(rule, dict):
                continue
            if "rules" in rule:
                self._modify_group(rule, target, offset, invert_lt)
            elif rule.get("target") == target and "value" in rule:
                delta = offset
                if invert_lt and rule.get("operator") in ("<", "<="):
                    delta = -offset
                rule["value"] += delta

    async def evaluate(self, symbol: str, price: float, ctx: dict):
        """메인 엔진 루프에서 호출되어 섀도우 트레이더들을 동작시킵니다.

        ShadowTrader.simulator(VirtualTrader)의 buy/sell은 async 코루틴이므로
        반드시 await 하여 실제 가상 체결이 발생하도록 합니다.
        (await 누락 시 코루틴이 폐기되어 체결 0건 + 'never awaited' 경고 발생)
        """
        if symbol not in self.shadow_targets:
            return

        for shadow in self.shadow_targets[symbol]:
            # 현재 포지션이 있으면 매도 검사
            if symbol in shadow.simulator.positions:
                pos = shadow.simulator.positions[symbol]
                profit_rate = ((price / pos['price']) - 1) * 100

                # Context 객체는 메인 엔진과 공유하므로 복사본 전달
                shadow_ctx = ctx.copy()
                shadow_ctx["profit_rate"] = profit_rate

                if shadow.evaluator.should_sell(shadow_ctx):
                    await shadow.simulator.sell(symbol, price, reason="SIGNAL")
            else:
                # 포지션이 없으면 매수 검사
                if shadow.evaluator.should_buy(ctx):
                    await shadow.simulator.buy(symbol, price, shadow.evaluator.budget)

    def check_for_upgrade(self, symbol: str) -> dict:
        """가장 우수한 성적을 낸 파라미터를 찾아 반환합니다. (없으면 None)"""
        if symbol not in self.shadow_targets:
            return None
            
        best_variant = None
        best_score = float('-inf')
        
        base_shadow = next((s for s in self.shadow_targets[symbol] if s.variant_id == "base"), None)
        base_profit = base_shadow.simulator.symbol_stats.get(symbol, {}).get("total_profit", 0) if base_shadow else 0
        
        for shadow in self.shadow_targets[symbol]:
            stats = shadow.simulator.symbol_stats.get(symbol, {})
            trades = stats.get("trades", 0)
            wins = stats.get("wins", 0)
            profit = stats.get("total_profit", 0)
            
            # 최소 요건: 최소 거래 수 & 최소 승률
            win_rate = (wins / trades * 100) if trades > 0 else 0
            
            if trades >= MIN_TRADES_FOR_UPGRADE and win_rate >= MIN_WIN_RATE_FOR_UPGRADE:
                # 점수 산정: 순수익
                score = profit
                
                # 원본 대비 개선폭 이상일 때만 교체 (base 가 0/음수여도 올바르게 동작)
                required = base_profit + max(abs(base_profit) * MIN_IMPROVEMENT_RATIO, MIN_IMPROVEMENT_ABS)
                if score > best_score and score > required:
                    best_score = score
                    best_variant = shadow
                    
        if best_variant and best_variant.variant_id != "base":
            logging.info(f"🚀 [Optimizer] Upgrade Found for {symbol}: {best_variant.variant_id} (Score: {best_score:,.0f} vs Base: {base_profit:,.0f})")
            
            # 핫스왑된 파라미터를 반환
            return best_variant.config
            
        return None
