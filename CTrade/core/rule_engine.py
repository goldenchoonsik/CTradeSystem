import operator
import logging
from typing import Dict, Any, List, Callable
import ctrade_core

class ConditionRegistry:
    """문자열 연산자를 파이썬 내장 연산자 함수로 매핑"""
    OPERATORS = {
        ">=": operator.ge,
        ">": operator.gt,
        "<=": operator.le,
        "<": operator.lt,
        "==": operator.eq,
        "!=": operator.ne
    }
    
    @classmethod
    def get_op(cls, op_str: str) -> Callable:
        if op_str not in cls.OPERATORS:
            raise ValueError(f"지원하지 않는 연산자입니다: {op_str}")
        return cls.OPERATORS[op_str]


class RuleCompiler:
    """JSON 룰을 고속 실행 가능한 람다 함수로 컴파일"""
    
    @staticmethod
    def compile_rule(rule: dict) -> Callable[[Dict[str, Any]], bool]:
        target = rule.get("target")
        op_str = rule.get("operator")
        value = rule.get("value")
        
        # 검증
        if not target or not op_str or value is None:
            raise ValueError(f"잘못된 룰 형식입니다: {rule}")
            
        op_func = ConditionRegistry.get_op(op_str)
        
        # 클로저를 활용하여 고속 람다 함수 반환 (레이턴시 최소화)
        # Context(ctx)는 엔진이 매 틱마다 던져주는 상태값 딕셔너리
        # ctx 에 키가 없으면 0.0 으로 취급하지 않고 False (C++ SimpleRule 과 동일)
        def _eval(ctx):
            if target not in ctx:
                return False
            return op_func(ctx[target], value)
        return _eval
        
    @staticmethod
    def compile_conditions(conditions: dict) -> Callable[[Dict[str, Any]], bool]:
        if not conditions:
            return lambda ctx: False
            
        logic = conditions.get("logic", "AND").upper()
        rules = conditions.get("rules", [])
        
        if logic not in ["AND", "OR"]:
            raise ValueError(f"지원하지 않는 논리 연산자입니다: {logic}")
            
        compiled_rules = []
        for r in rules:
            if "rules" in r:  # Nested condition group
                compiled_rules.append(RuleCompiler.compile_conditions(r))
            else:             # Simple condition rule
                compiled_rules.append(RuleCompiler.compile_rule(r))
        
        if not compiled_rules:
            return lambda ctx: False
            
        if logic == "AND":
            # 모든 룰이 True여야 True (하나라도 False면 단축 평가로 중단)
            def and_evaluator(ctx):
                for f in compiled_rules:
                    if not f(ctx):
                        return False
                return True
            return and_evaluator
            
        elif logic == "OR":
            def or_evaluator(ctx):
                for f in compiled_rules:
                    if f(ctx):
                        return True
                return False
            return or_evaluator


RuleEvaluator = ctrade_core.RuleEvaluator


def merge_strategy(target_conf: dict, config: dict) -> dict:
    """종목별 설정과 전략 템플릿을 안전하게 결합하는 헬퍼 (하이브리드 매핑 및 자동 롤백 지원)"""
    merged = target_conf.copy()
    templates = config.get("STRATEGY_TEMPLATES", {})
    
    # 1. strategy_name 파악 및 자동 롤백 처리
    strat_name = merged.get("strategy_name", "").strip()
    if not strat_name or strat_name not in templates:
        strat_name = "default_scalping"
        merged["strategy_name"] = strat_name
        
    template = templates.get(strat_name, {})
    
    # 2. 템플릿의 기본 사양들을 결합하되, 종목 설정에 이미 하드코딩된 필드가 있다면 하위 호환을 위해 유지 (우선순위: 종목 개별 설정 > 템플릿)
    # ⚠️ deploy_session/deploy_base/type/reference_symbols 는 AI_BAKER 모델 배포 정보의 핵심 필드다.
    #    과거 이 목록에서 빠져 있어 merge_strategy() 결과(t)를 쓰는 실시간 참조종목 자동구독 로직이
    #    deploy_session 을 항상 None 으로 인식, 배포된 모델이 요구하는 참조종목(예: 대장주 스프레드 피처)
    #    구독이 통째로 누락되어 전량 매매 불능 상태가 된 적이 있다 (2026-09-22 장애). 반드시 유지할 것.
    for key in ["budget_per_trade", "orderbook_level", "reference_market", "buy_conditions", "sell_conditions",
                "deploy_session", "deploy_base", "type", "reference_symbols"]:
        if key not in merged or merged[key] is None:
            # 템플릿에서 값을 안전하게 복사
            val = template.get(key)
            if isinstance(val, dict):
                merged[key] = val.copy()
            elif isinstance(val, list):
                merged[key] = val.copy()
            else:
                merged[key] = val
                
    return merged


class StrategyManager:
    """모든 종목의 룰 인스턴스를 중앙 관리"""
    def __init__(self, config: dict):
        self.config = config  # config 캐시
        self.targets = {}
        targets_conf = config.get("SCALPING_TARGETS", [])
        templates = config.get("STRATEGY_TEMPLATES", {})
        
        for t_conf in targets_conf:
            symbol = t_conf["symbol"]
            # AI_BAKER 타입 전략은 RuleEvaluator가 아닌 AIStrategyModule이 담당하므로 스킵
            strat_name = t_conf.get("strategy_name", "")
            tmpl = templates.get(strat_name, {})
            if tmpl.get("type") == "AI_BAKER":
                logging.info(f"[StrategyManager] {symbol}: AI_BAKER 전략 — RuleEvaluator 생략 (AIStrategyModule 관할)")
                continue
            # 템플릿 결합 수행
            merged_conf = merge_strategy(t_conf, config)
            self.targets[symbol] = RuleEvaluator(merged_conf)
            
    def get_evaluator(self, symbol: str) -> RuleEvaluator:
        return self.targets.get(symbol)
        
    def update_evaluator(self, symbol: str, new_config: dict):
        """특정 종목의 룰 인스턴스를 새로운 설정으로 교체합니다 (Hot-Swap)."""
        try:
            # update_evaluator가 개별 target 설정을 직접 전달받는 경우, config 컨텍스트를 활용해 병합
            merged_conf = merge_strategy(new_config, self.config)
            new_evaluator = RuleEvaluator(merged_conf)
            self.targets[symbol] = new_evaluator
            status_str = "ACTIVE" if new_evaluator.active else "INACTIVE"
            logging.info(f"[StrategyManager] Hot-swap successful: {symbol} is now {status_str} (AutoTrade: {new_evaluator.auto_trade})")
            return True
        except Exception as e:
            logging.error(f"[StrategyManager] Failed to hot-swap rules for {symbol}: {e}")
            return False
