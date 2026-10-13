import time
import json
import os
import logging
from typing import Dict, Any, Optional
import ctrade_core

_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config', 'trading_config.json')

def _read_current_version():
    """trading_config.json 에서 현재 최적화 세션 VERSION 태그를 읽어옵니다. 실패 시 기본값 반환."""
    try:
        with open(_CONFIG_PATH, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        return cfg.get('GLOBAL_STRATEGY', {}).get('VERSION', 'v1.3.0_opt001')
    except Exception:
        return 'v1.3.0_opt001'

_META_FIELDS = ("is_real", "mode", "exchange", "entry_atr")


def _meta_key(evt: dict):
    try:
        t = round(float(evt.get("time", 0.0) or 0.0), 3)
    except Exception:
        t = 0.0
    return (str(evt.get("type", "")), str(evt.get("symbol", "")), t)


class VirtualTrader(ctrade_core.VirtualTrader):
    # 이 트레이더가 C++ 로 직접 생성한(메타데이터 없는) 이벤트에 붙일 기본 라벨
    _default_is_real = False

    def __init__(self, initial_balance: float = 10000000.0, fee_rate: float = 0.0005, silent: bool = False):
        # C++ 부모 클래스 생성자 호출
        super().__init__(initial_balance, fee_rate, silent)
        self.fee_rate = fee_rate
        self.initial_balance = initial_balance
        self.silent = silent
        # C++ TradeEvent 는 is_real/mode/exchange/entry_atr 를 보존하지 않으므로 Python 측에 별도 보관한다
        # (미보관 시 저장 때마다 모든 이벤트가 SIMULATION 으로 덮어써져 실전/모의 구분이 소실된다)
        self._event_meta = {}
        # 프로젝트 루트(CTradeSystem/) 기준으로 trade_history.json 경로 설정
        root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        project_root = os.path.dirname(root_dir)
        self.history_file = os.path.join(project_root, "trade_history.json")
        
        if not self.silent:
            self._load_history()

    def add_history_event(self, ev):
        """C++ 이력 추가 + C++ 가 버리는 부가 필드(is_real/mode/exchange/entry_atr)를 Python 측에 보관."""
        try:
            meta = {k: ev[k] for k in _META_FIELDS if k in ev}
            if meta:
                self._event_meta[_meta_key(ev)] = meta
        except Exception:
            pass
        return super().add_history_event(ev)

    def _annotated_event(self, h) -> dict:
        d = dict(h)
        meta = self._event_meta.get(_meta_key(d))
        if meta:
            d.update(meta)
        if "is_real" not in d:
            d["is_real"] = bool(self._default_is_real)
        if "mode" not in d:
            d["mode"] = "REAL" if d.get("is_real") else "SIMULATION"
        return d

    def history_annotated(self):
        """C++ history 에 보관된 부가 메타(is_real/mode/exchange/entry_atr)를 복원한 이벤트 리스트."""
        return [self._annotated_event(h) for h in self.history]

    def _load_history(self):
        """파일에서 기존 매매 이력을 불러와 C++ 내부 구조와 동기화합니다."""
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, "r", encoding="utf-8") as f:
                    history_data = json.load(f)
                    
                    self.clear_history()
                    for h in history_data:
                        if isinstance(h, dict):
                            # 라벨이 없는 과거 이벤트는 가상(SIMULATION)으로 간주 (기존 저장 규칙과 동일)
                            h = dict(h)
                            h.setdefault("is_real", False)
                            h.setdefault("mode", "REAL" if h.get("is_real") else "SIMULATION")
                        self.add_history_event(h)
            except Exception as e:
                logging.error(f"[Simulator] History Load Error: {e}")

    def _save_history_sync(self):
        """[내부 전용] 동기 저장 로직 - run_in_executor 에서 호출됨

        M-1: 다중 writer(UPBIT/KIS) 파일 충돌 방지를 위해 합집합 병합 저장을 사용한다.
        """
        if self.silent:
            return
        try:
            # 보관해 둔 is_real/mode/exchange 메타를 복원하고, 없으면 트레이더 기본 라벨(모의=SIMULATION)을 부여
            history_data = self.history_annotated()
            from utils.history_store import save_history_merged
            save_history_merged(self.history_file, history_data)
        except Exception as e:
            logging.error(f"[Simulator] History Save Error: {e}")

    def _save_history(self):
        """이벤트 루프 블로킹 방지: asyncio 루프가 실행 중이면 스레드풀에서 비동기 저장"""
        if self.silent:
            return
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.run_in_executor(None, self._save_history_sync)
            else:
                self._save_history_sync()
        except RuntimeError:
            self._save_history_sync()

    async def buy(self, symbol: str, price: float, amount: float,
                  curr_time: float = None, version: str = None) -> bool:
        """가상 매수 실행"""
        if curr_time is None:
            curr_time = time.time()
        if version is None:
            version = _read_current_version()
        
        success = super().buy(symbol, price, amount, curr_time, version)
        if success:
            if not self.silent:
                # C++ history 벡터의 마지막 아이템을 참조하여 수량과 금액 로깅
                last_evt = dict(self.history[-1]) if self.history else {}
                last_evt["is_real"] = False
                last_evt["mode"] = "SIMULATION"
                qty = last_evt.get("qty", 0.0)
                total_cost = last_evt.get("amount", 0.0)
                qty_str = f"{int(qty)}주" if (qty >= 1 and qty == int(qty) and ("-" not in symbol and "_" not in symbol)) else f"{qty:.8f}"
                logging.info(f"[Simulator] BUY {symbol} | Price: {price:,.0f} | Qty: {qty_str} | Total Cost: {total_cost:,.0f}")
                # 텔레그램 알림 (가상 모드 명시)
                try:
                    from utils.telegram_bot import get_bot
                    if bot := get_bot():
                        bot.notify_buy(symbol, price, qty, total_cost, extra=last_evt, is_real=False)
                except Exception:
                    pass
            self._save_history()
            return True
        return False

    def _purge_simulation_history_file(self):
        """history_file 에서 가상(SIMULATION, is_real=False/미지정) 이벤트를 제거하고 실전 이벤트만 남긴다."""
        try:
            from utils.history_store import load_history_raw
            try:
                import fcntl
            except Exception:
                fcntl = None
            lock_fd = None
            try:
                if fcntl is not None:
                    try:
                        lock_fd = open(self.history_file + ".lock", "w")
                        fcntl.flock(lock_fd, fcntl.LOCK_EX)
                    except Exception:
                        lock_fd = None
                existing = load_history_raw(self.history_file)
                kept = [e for e in existing
                        if isinstance(e, dict) and e.get("is_real") is True and e.get("mode") != "SIMULATION"]
                tmp_path = f"{self.history_file}.tmp.{os.getpid()}"
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(kept, f, indent=4, ensure_ascii=False)
                os.replace(tmp_path, self.history_file)
            finally:
                if lock_fd is not None:
                    try:
                        fcntl.flock(lock_fd, fcntl.LOCK_UN)
                        lock_fd.close()
                    except Exception:
                        pass
        except Exception as e:
            logging.error(f"[Simulator] 리셋 이력 파일 정리 실패: {e}")

    def reset(self, new_initial_balance: float = None):
        """가상 매매 상태(포지션, 히스토리, 통계)를 완전 리셋하고 잔고를 초기 자본금으로 복원합니다."""
        if new_initial_balance is not None and new_initial_balance > 0:
            self.initial_balance = new_initial_balance
            super().reset(new_initial_balance)
        else:
            super().reset(self.initial_balance)
        
        if not self.silent:
            # 합집합 병합 저장(save_history_merged)은 리셋된 빈 이력을 반영하지 못해, 재기동 시 이전 미청산 BUY 가
            # 부활한다. 파일에서 가상(SIMULATION) 이벤트를 명시적으로 제거한다 (실전 REAL 이력은 보존).
            self._purge_simulation_history_file()
            logging.info(f"[Simulator] VirtualTrader 완전 리셋 완료 (초기 자본금: {self.initial_balance:,.0f}원)")

    async def sell(self, symbol: str, price: float, reason: str = "SIGNAL",
                   curr_time: float = None, version: str = None):
        """가상 매도 실행"""
        if curr_time is None:
            curr_time = time.time()
        if version is None:
            version = _read_current_version()
        
        profit = super().sell(symbol, price, reason, curr_time, version)
        if profit != -999.0:
            if not self.silent:
                last_evt = dict(self.history[-1]) if self.history else {}
                last_evt["is_real"] = False
                last_evt["mode"] = "SIMULATION"
                profit_rate = last_evt.get("profit_rate", 0.0)
                logging.info(f"[Simulator] SELL {symbol} | Price: {price:,.0f} | Profit: {profit:,.0f} ({profit_rate:.2f}%)")
                # 텔레그램 알림 (가상 모드 명시)
                try:
                    from utils.telegram_bot import get_bot
                    if bot := get_bot():
                        bot.notify_sell(symbol, price, profit, profit_rate, reason, extra=last_evt, is_real=False)
                except Exception:
                    pass
            self._save_history()
            return profit
        return False

