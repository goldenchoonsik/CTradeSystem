"""
CCommander - Trade & Portfolio Manager
======================================
trade_history.json 및 심볼 레지스트리를 분석하여
잔고, 누적 손익, 최근 체결 내역, 승률 통계를 텔레그램 리포트 형식으로 제공합니다.
"""
import os
import json
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger("CCommander")


def _fmt_krw(value: float) -> str:
    """원화 포맷 (예: 142,500,000원)"""
    sign = "-" if value < 0 else ""
    return f"{sign}{abs(value):,.0f}원"


def _fmt_pct(value: float) -> str:
    """수익률 백분율 포맷 (예: +0.56%)"""
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.2f}%"


def _fmt_qty(qty: float) -> str:
    """수량 스마트 포맷 (주식은 정수주, 코인은 소수점)"""
    if qty <= 0:
        return "0"
    if abs(qty - round(qty)) < 1e-5:
        return f"{int(round(qty)):,}주"
    return f"{qty:.6f}".rstrip("0").rstrip(".")


def _fmt_duration(seconds: float) -> str:
    """초 단위 시간을 '1시간 20분 30초' 또는 '29분 59초' 형태로 포맷"""
    if seconds <= 0:
        return "즉시"
    sec = int(round(seconds))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}시간 {m}분 {s}초" if s > 0 else f"{h}시간 {m}분"
    if m > 0:
        return f"{m}분 {s}초" if s > 0 else f"{m}분"
    return f"{s}초"


class TradeManager:
    """CTrade 매매 이력 및 포트폴리오 분석 관리자"""

    @staticmethod
    def _get_project_root() -> str:
        current_dir = os.path.dirname(os.path.abspath(__file__)) # CCommander/core
        commander_dir = os.path.dirname(current_dir)             # CCommander
        return os.path.dirname(commander_dir)                   # CTradeSystem

    @classmethod
    def get_symbol_display_name(cls, symbol: str) -> str:
        """종목 코드를 친절한 종목명(예: KODEX 200 (069500))으로 변환 (종목 마스터 원장 경유)"""
        from shared.config.config_manager import get_symbol_display_name
        return get_symbol_display_name(symbol)

    @classmethod
    def load_trade_history(cls) -> List[Dict[str, Any]]:
        """trade_history.json 파일 로드"""
        root = cls._get_project_root()
        possible_paths = [
            os.path.join(root, "trade_history.json"),
            os.path.join(root, "CTrade", "trade_history.json"),
        ]
        for p in possible_paths:
            if os.path.exists(p):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, list):
                            return data
                except Exception as e:
                    logger.warning(f"trade_history.json({p}) 파싱 오류: {e}")
        return []

    @classmethod
    def load_account_baseline(cls) -> Dict[str, Any]:
        """account_baseline.json 로드"""
        root = cls._get_project_root()
        path = os.path.join(root, "account_baseline.json")
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"account_baseline.json 로드 실패: {e}")
        return {}

    @classmethod
    def is_simulation_mode(cls) -> bool:
        """trading_config.json 기준 현재 시뮬레이션 모드 여부 확인"""
        root = cls._get_project_root()
        for p in [
            os.path.join(root, "CTrade", "config", "trading_config.json"),
            os.path.join(root, "config", "trading_config.json"),
        ]:
            if os.path.exists(p):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        cfg = json.load(f)
                        return bool(cfg.get("GLOBAL_STRATEGY", {}).get("SIMULATION_MODE", True))
                except Exception:
                    pass
        return False

    @classmethod
    def calculate_trade_metrics(
        cls, 
        history: List[Dict[str, Any]], 
        initial_balance: float = 10000000.0, 
        baseline: Optional[Dict[str, Any]] = None,
        filter_real: Optional[bool] = None
    ) -> Dict[str, Any]:
        """매매 이력 기반 통산 성과 지표 계산 (시행횟수, 승률, 손익비, 누적/당일 손익 및 ROI)"""
        # 실전 / 가상 모드에 맞게 history 필터링
        filtered_history = history
        if filter_real is not None:
            if filter_real:
                filtered_history = [h for h in history if h.get("is_real") is True or h.get("mode") == "REAL"]
            else:
                filtered_history = [h for h in history if not (h.get("is_real") is True or h.get("mode") == "REAL")]

        sells = [h for h in filtered_history if h.get("type") == "SELL"]
        buys = [h for h in filtered_history if h.get("type") == "BUY"]
        
        total_trades = len(sells)
        wins = [s for s in sells if float(s.get("profit", 0.0)) > 0]
        losses = [s for s in sells if float(s.get("profit", 0.0)) < 0]
        ties = [s for s in sells if float(s.get("profit", 0.0)) == 0]
        
        win_count = len(wins)
        lose_count = len(losses)
        win_rate = (win_count / total_trades * 100.0) if total_trades > 0 else 0.0
        
        total_profit = sum(float(s.get("profit", 0.0)) for s in sells)
        total_gain = sum(float(s.get("profit", 0.0)) for s in wins)
        total_loss = abs(sum(float(s.get("profit", 0.0)) for s in losses))
        
        if total_loss > 0:
            profit_factor = total_gain / total_loss
        elif total_gain > 0:
            profit_factor = 99.9  # 무패 전승
        else:
            profit_factor = 0.0
            
        avg_profit = (total_profit / total_trades) if total_trades > 0 else 0.0
        
        # 건별 순수익률 합산 (회전 누적 ROI %)
        summed_trade_roi = sum(float(s.get("profit_rate", 0.0)) for s in sells)
        avg_trade_roi = (summed_trade_roi / total_trades) if total_trades > 0 else 0.0
        
        # 총 매수 누적 금액 (회전 투자금)
        total_invested = sum(float(b.get("amount", 0.0)) + float(b.get("fee", 0.0)) for b in buys)
        
        # 계좌 초기자본금 대비 ROI %
        account_roi = (total_profit / initial_balance * 100.0) if initial_balance > 0 else 0.0
        
        # 운용 잔고
        if filter_real:
            # 실전 모드: baseline에 등록된 실제 자산 기준 잔고 우선
            if baseline and float(baseline.get("TOTAL", {}).get("daily_balance", 0.0)) > 0:
                last_balance = float(baseline["TOTAL"]["daily_balance"])
            elif sells and "balance" in sells[-1] and float(sells[-1]["balance"]) > 0 and float(sells[-1]["balance"]) < 5000000.0:
                last_balance = float(sells[-1]["balance"])
            else:
                last_balance = initial_balance
        else:
            # 가상 모드
            last_balance = float(filtered_history[-1].get("balance", initial_balance)) if filtered_history else initial_balance
        
        # 당일(Today) 통계 분리 (오늘 00:00:00 KST 이후)
        today_str = datetime.now().strftime("%Y-%m-%d")
        today_sells = []
        for s in sells:
            ts = s.get("time", 0.0) or s.get("exit_time", 0.0) or s.get("entry_time", 0.0)
            if ts > 0:
                try:
                    dt_s = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
                    if dt_s == today_str:
                        today_sells.append(s)
                except Exception:
                    pass
                    
        today_trades_count = len(today_sells)
        today_wins = len([s for s in today_sells if float(s.get("profit", 0.0)) > 0])
        today_losses = len([s for s in today_sells if float(s.get("profit", 0.0)) < 0])
        today_profit = sum(float(s.get("profit", 0.0)) for s in today_sells)
        today_trade_roi = sum(float(s.get("profit_rate", 0.0)) for s in today_sells)
        
        # 기준선 일별 시작금액 대비 당일 ROI
        daily_base_val = initial_balance
        if baseline:
            tot_b = baseline.get("TOTAL", {})
            if tot_b and float(tot_b.get("daily_balance", 0.0)) > 0:
                daily_base_val = float(tot_b["daily_balance"])
        today_account_roi = (today_profit / daily_base_val * 100.0) if daily_base_val > 0 else 0.0

        return {
            "total_trades": total_trades,
            "buy_count": len(buys),
            "win_count": win_count,
            "lose_count": lose_count,
            "tie_count": len(ties),
            "win_rate": win_rate,
            "total_profit": total_profit,
            "total_gain": total_gain,
            "total_loss": total_loss,
            "profit_factor": profit_factor,
            "avg_profit": avg_profit,
            "summed_trade_roi": summed_trade_roi,
            "avg_trade_roi": avg_trade_roi,
            "total_invested": total_invested,
            "account_roi": account_roi,
            "last_balance": last_balance,
            "today_trades_count": today_trades_count,
            "today_wins": today_wins,
            "today_losses": today_losses,
            "today_profit": today_profit,
            "today_trade_roi": today_trade_roi,
            "today_account_roi": today_account_roi,
            "daily_base_val": daily_base_val,
        }

    @classmethod
    def get_balance_report(cls) -> str:
        """/balance 명령어 응답 텍스트 생성 (누적/당일 손익 및 ROI, 시행통계)"""
        history = cls.load_trade_history()
        baseline = cls.load_account_baseline()
        is_sim = cls.is_simulation_mode()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        mode_badge = "🔵 [가상 매매]" if is_sim else "🔴 [실전 매매]"

        # 초기 자본금 산출
        init_bal = 10000000.0
        if not is_sim:
            # 실전 모드인 경우 baseline의 initial_balance 또는 daily_balance 사용
            if baseline:
                tot_b = baseline.get("TOTAL", {})
                if tot_b and float(tot_b.get("initial_balance", 0.0)) > 0:
                    init_bal = float(tot_b["initial_balance"])
                elif tot_b and float(tot_b.get("daily_balance", 0.0)) > 0:
                    init_bal = float(tot_b["daily_balance"])
        else:
            if baseline:
                tot_b = baseline.get("TOTAL", {})
                if tot_b and float(tot_b.get("initial_balance", 0.0)) > 0:
                    init_bal = float(tot_b["initial_balance"])

        # 기준선 정보 포맷팅
        baseline_lines = []
        if baseline:
            tot_b = baseline.get("TOTAL", {})
            u_b = baseline.get("UPBIT", {})
            k_b = baseline.get("KIS", {})
            if tot_b:
                tot_day = float(tot_b.get("daily_balance", 0.0))
                u_day = float(u_b.get("daily_balance", 0.0))
                k_day = float(k_b.get("daily_balance", 0.0))
                baseline_lines.append(f"📌 <b>당일 기준선</b>: <code>{_fmt_krw(tot_day)}</code>")
                baseline_lines.append(f"   (UPBIT: {_fmt_krw(u_day)} | KIS: {_fmt_krw(k_day)})")

        base_str = "\n".join(baseline_lines) if baseline_lines else ""

        if not history:
            tot_val = float(baseline.get("TOTAL", {}).get("daily_balance", init_bal)) if baseline else init_bal
            base_section = f"{base_str}\n\n" if base_str else ""
            return (
                f"💰 <b>[CTrade] {mode_badge} 잔고 및 누적 성과</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"{base_section}"
                f"🏦 <b>운용 잔고</b>: <code>{_fmt_krw(tot_val)}</code> (기준: {_fmt_krw(init_bal)})\n"
                f"⚠️ <code>trade_history.json</code> 내역이 없습니다.\n"
                f"• 아직 체결된 거래가 없거나 초기화된 상태입니다.\n\n"
                f"⏰ <code>{now_str}</code>"
            )

        # 실전/가상 모드에 맞추어 메트릭 계산
        m = cls.calculate_trade_metrics(history, initial_balance=init_bal, baseline=baseline, filter_real=(not is_sim))
        
        # 현재 미청산(보유 중인) 포지션 계산 (모드에 맞는 포지션만 필터링)
        filtered_hist = [h for h in history if (h.get("is_real") is True or h.get("mode") == "REAL")] if not is_sim else [h for h in history if not (h.get("is_real") is True or h.get("mode") == "REAL")]
        unclosed = {}
        for evt in filtered_hist:
            evt_type = evt.get("type")
            sym = evt.get("symbol")
            if evt_type == "BUY":
                unclosed[sym] = evt
            elif evt_type == "SELL":
                unclosed.pop(sym, None)

        unclosed_lines = []
        for sym, pos in unclosed.items():
            sym_name = cls.get_symbol_display_name(sym)
            b_p = float(pos.get("price", 0.0))
            qty = float(pos.get("qty", 0.0))
            cost = float(pos.get("amount", b_p * qty)) + float(pos.get("fee", 0.0))
            unclosed_lines.append(f"• 🤖 <b>{sym_name}</b>: {_fmt_qty(qty)} (@{_fmt_krw(b_p)})\n    매입총액: <code>{_fmt_krw(cost)}</code>")
        
        pos_str = "\n".join(unclosed_lines) if unclosed_lines else "• 포지션 없음 (100% 현금 대기 중)"

        profit_sign = "+" if m["total_profit"] >= 0 else ""
        profit_emoji = "📈" if m["total_profit"] >= 0 else "📉"
        today_sign = "+" if m["today_profit"] >= 0 else ""
        today_emoji = "📈" if m["today_profit"] >= 0 else "📉"
        avg_sign = "+" if m["avg_profit"] >= 0 else ""
        base_section = f"{base_str}\n" if base_str else ""

        text = (
            f"💰 <b>[CTrade] {mode_badge} 잔고 및 누적 성과</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🏦 <b>운용 잔고</b>: <code>{_fmt_krw(m['last_balance'])}</code> (기준: {_fmt_krw(init_bal)})\n"
            f"{base_section}"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📈 <b>수익률 및 손익 증감</b>:\n"
            f"  • {profit_emoji} <b>통산 실현손익</b>: <b>{profit_sign}{_fmt_krw(m['total_profit'])}</b> (계좌 ROI: <b>{_fmt_pct(m['account_roi'])}</b>)\n"
            f"  • 🔄 <b>회전 누적수익</b>: <b>{_fmt_pct(m['summed_trade_roi'])}</b> (총 매수누적: {_fmt_krw(m['total_invested'])})\n"
            f"  • {today_emoji} <b>당일 실현손익</b>: <b>{today_sign}{_fmt_krw(m['today_profit'])} ({_fmt_pct(m['today_account_roi'])})</b> (오늘 {m['today_trades_count']}건 청산)\n\n"
            f"📊 <b>시행 통계</b>:\n"
            f"  • 시행 횟수: <code>{m['total_trades']}건 청산</code> (매수 {m['buy_count']}건)\n"
            f"  • 승/패 현황: <code>{m['win_count']}승 {m['lose_count']}패</code> (승률 <b>{m['win_rate']:.1f}%</b>)\n"
            f"  • 손익비(PF): <b>{m['profit_factor']:.2f}</b> | 건당 평균: <b>{avg_sign}{_fmt_krw(m['avg_profit'])}</b>\n\n"
            f"🤖 <b>현재 보유 포지션 ({len(unclosed)}건)</b>:\n{pos_str}\n\n"
            f"⏰ <code>{now_str}</code>"
        )
        return text

    @classmethod
    def get_trades_report(cls, limit: int = 5) -> str:
        """/trades 명령어 응답 텍스트 생성 (최근 limit건 체결 상세 + 통산 통계)"""
        history = cls.load_trade_history()
        baseline = cls.load_account_baseline()
        is_sim = cls.is_simulation_mode()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        mode_badge = "🔵 [가상 매매]" if is_sim else "🔴 [실전 매매]"

        if not history:
            return (
                f"📋 <b>[CTrade] {mode_badge} 최근 체결 내역</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"⚠️ 체결 내역이 존재하지 않습니다.\n\n"
                f"⏰ <code>{now_str}</code>"
            )

        # 최신 순으로 정렬
        recent = list(reversed(history[-limit:]))
        lines = [
            f"📋 <b>[CTrade] {mode_badge} 최근 체결 내역 ({len(recent)}건)</b>",
            "━━━━━━━━━━━━━━━━━━━━"
        ]

        for i, h in enumerate(recent, 1):
            t = h.get("type", "?")
            sym = h.get("symbol", "?")
            sym_name = cls.get_symbol_display_name(sym)
            qty = float(h.get("qty", 0.0))
            fee = float(h.get("fee", 0.0))
            is_real = (h.get("is_real") is True or h.get("mode") == "REAL")
            trade_badge = "🔴실전" if is_real else "🔵가상"
            
            ts = h.get("time", 0.0)
            if ts <= 0:
                ts = h.get("exit_time", 0.0) or h.get("entry_time", 0.0)
            ts_str = datetime.fromtimestamp(ts).strftime("%m/%d %H:%M") if ts else "N/A"

            if t == "BUY":
                price = float(h.get("price", 0.0))
                total_cost = float(h.get("amount", price * qty)) + fee
                lines.append(
                    f"<b>{i}. [{trade_badge} BUY] {sym_name}</b>\n"
                    f"   • 매입단가: <code>{_fmt_krw(price)}</code> | 수량: <code>{_fmt_qty(qty)}</code>\n"
                    f"   • 실제구입액: <code>{_fmt_krw(total_cost)}</code> (수수료: <code>{fee:,.0f}원</code>)\n"
                    f"   • 체결시간: <code>{ts_str}</code>\n"
                )
            elif t == "SELL":
                buy_p = float(h.get("buy_price", 0.0))
                sell_p = float(h.get("sell_price", h.get("price", 0.0)))
                profit = float(h.get("profit", 0.0))
                rate_val = float(h.get("profit_rate", 0.0))
                invest_amt = float(h.get("amount", buy_p * qty))
                holding_sec = float(h.get("holding_time", 0.0))
                holding_str = _fmt_duration(holding_sec) if holding_sec > 0 else "즉시"
                
                emoji = "📈" if profit >= 0 else "📉"
                reason = h.get("reason", "SIGNAL")
                
                lines.append(
                    f"<b>{i}. [{trade_badge} SELL] {sym_name}</b>\n"
                    f"   • 손익: {emoji} <b>{_fmt_krw(profit)} ({_fmt_pct(rate_val)})</b> | 투자: <code>{_fmt_krw(invest_amt)}</code>\n"
                    f"   • 단가: <code>{_fmt_krw(buy_p)}</code> → <code>{_fmt_krw(sell_p)}</code>\n"
                    f"   • 수량: <code>{_fmt_qty(qty)}</code> | 수수료: <code>{fee:,.0f}원</code>\n"
                    f"   • 보유: <code>{holding_str}</code> | 사유: <code>{reason}</code> | <code>{ts_str}</code>\n"
                )
            else:
                lines.append(f"<b>{i}. [{trade_badge}] {t} {sym_name}</b> (시간: <code>{ts_str}</code>)\n")

        # 통산 성과 지표 계산 및 하단 요약 추가 (실전/가상 모드 분리 집계)
        init_bal = 10000000.0
        if not is_sim and baseline:
            tot_b = baseline.get("TOTAL", {})
            if tot_b and float(tot_b.get("daily_balance", 0.0)) > 0:
                init_bal = float(tot_b["daily_balance"])
        m = cls.calculate_trade_metrics(history, initial_balance=init_bal, baseline=baseline, filter_real=(not is_sim))
        p_sign = "+" if m["total_profit"] >= 0 else ""
        avg_sign = "+" if m["avg_profit"] >= 0 else ""
        lines.append(
            f"📊 <b>통산 매매 통계 요약 ({mode_badge})</b>:\n"
            f"  • 총 시행: <code>{m['total_trades']}전 {m['win_count']}승 {m['lose_count']}패</code> (승률 <b>{m['win_rate']:.1f}%</b> | 손익비 <b>{m['profit_factor']:.2f}</b>)\n"
            f"  • 평균 손익: <b>{avg_sign}{_fmt_krw(m['avg_profit'])}</b> (평균 순수익률 <b>{_fmt_pct(m['avg_trade_roi'])}</b>)\n"
            f"  • 통산 손익: <b>{p_sign}{_fmt_krw(m['total_profit'])}</b> (회전 누적 <b>{_fmt_pct(m['summed_trade_roi'])}</b>)\n"
        )

        lines.append(f"⏰ <code>{now_str}</code>")
        return "\n".join(lines)

    @classmethod
    def get_quick_summary(cls) -> str:
        """/status 종합 현황용 트레이딩 요약 블록 반환"""
        history = cls.load_trade_history()
        if not history:
            return (
                "  • 누적 손익: <b>+0원</b>\n"
                "  • 청산/승률: <code>내역 없음</code>"
            )
        
        m = cls.calculate_trade_metrics(history)
        profit_sign = "+" if m["total_profit"] >= 0 else ""
        
        return (
            f"  • 통산 손익: <b>{profit_sign}{_fmt_krw(m['total_profit'])}</b> (회전: <b>{_fmt_pct(m['summed_trade_roi'])}</b>)\n"
            f"  • 청산 통계: <code>{m['total_trades']}전 {m['win_count']}승 {m['lose_count']}패</code> (승률 <b>{m['win_rate']:.1f}%</b> | PF <b>{m['profit_factor']:.2f}</b>)"
        )

    @classmethod
    def get_pnl_report(cls) -> str:
        """/pnl 명령어 응답: 특정 기준 시점(마지막 /resetbase 또는 /setbase 시점) 이후 실시간 실현 성과 조회"""
        history = cls.load_trade_history()
        baseline = cls.load_account_baseline()
        is_sim = cls.is_simulation_mode()
        now = datetime.now()
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")

        mode_badge = "🔴 실전매매" if not is_sim else "🔵 가상매매"

        # 1. 기준선 정보 추출
        base_bal = 10000000.0
        base_ts = 0.0
        base_time_str = "미설정"
        if baseline:
            tot_b = baseline.get("TOTAL", {})
            base_bal = float(tot_b.get("daily_balance", 0.0)) or float(tot_b.get("initial_balance", 10000000.0))
            base_time_str = tot_b.get("daily_timestamp") or tot_b.get("initial_timestamp") or ""
            if not base_time_str and baseline.get("history"):
                base_time_str = baseline["history"][0].get("timestamp", "")
            if base_time_str:
                try:
                    base_ts = datetime.strptime(base_time_str.strip(), "%Y-%m-%d %H:%M:%S").timestamp()
                except Exception:
                    pass

        # 기준 시점 경과 시간 계산
        elapsed_str = ""
        if base_ts > 0:
            elapsed_sec = max(0, int(now.timestamp() - base_ts))
            elapsed_str = f" ({_fmt_duration(elapsed_sec)} 경과)"

        # 2. 기준 시점 이후의 체결 내역만 필터링
        filtered = []
        for h in history:
            # 실전/가상 모드 필터
            if not is_sim:
                if not (h.get("is_real") is True or h.get("mode") == "REAL"):
                    continue
            else:
                if (h.get("is_real") is True or h.get("mode") == "REAL"):
                    continue
            
            # 기준 시점 이후 체결 필터
            t = float(h.get("time", 0.0) or h.get("exit_time", 0.0) or h.get("entry_time", 0.0))
            if base_ts > 0 and t < (base_ts - 5.0):  # 5초 버퍼 허용
                continue
            filtered.append(h)

        sells = [h for h in filtered if h.get("type") == "SELL"]
        buys = [h for h in filtered if h.get("type") == "BUY"]

        tot_trades = len(sells)
        wins = len([s for s in sells if float(s.get("profit", 0.0)) > 0])
        losses = len([s for s in sells if float(s.get("profit", 0.0)) < 0])
        win_rate = (wins / tot_trades * 100.0) if tot_trades > 0 else 0.0

        tot_profit = sum(float(s.get("profit", 0.0)) for s in sells)
        tot_gain = sum(float(s.get("profit", 0.0)) for s in sells if float(s.get("profit", 0.0)) > 0)
        tot_loss = abs(sum(float(s.get("profit", 0.0)) for s in sells if float(s.get("profit", 0.0)) < 0))
        pf = (tot_gain / tot_loss) if tot_loss > 0 else (99.9 if tot_gain > 0 else 0.0)
        summed_trade_roi = sum(float(s.get("profit_rate", 0.0)) for s in sells)

        # 수수료 및 매수/매도 총액
        buy_amt = sum(float(b.get("amount", 0.0)) for b in buys)
        sell_amt = sum(float(s.get("amount", 0.0)) for s in sells)
        buy_fee = sum(float(b.get("fee", 0.0)) for b in buys)
        sell_fee = sum(float(s.get("fee", 0.0)) for s in sells)
        tot_fee = buy_fee + sell_fee

        # 계좌 기준 수익률
        account_roi = (tot_profit / base_bal * 100.0) if base_bal > 0 else 0.0

        p_emoji = "📈" if tot_profit >= 0 else "📉"
        p_sign = "+" if tot_profit >= 0 else ""

        # 현재 잔고 (기준선 자산 + 실현손익)
        est_curr_balance = base_bal + tot_profit

        text = (
            f"📊 <b>[실시간 실현손익 / 기준점 이후]</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 <b>운용 모드</b>: <b>{mode_badge}</b>\n"
            f"⏰ <b>기준 시점</b>: <code>{base_time_str}</code>{elapsed_str}\n"
            f"🏦 <b>기준 자산</b>: <code>{_fmt_krw(base_bal)}</code> ➔ <code>{_fmt_krw(est_curr_balance)}</code> ({p_sign}{_fmt_krw(tot_profit)})\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📈 <b>기준점 이후 실현 성과</b>:\n"
            f"  • 시행 횟수: <code>{tot_trades}전 {wins}승 {losses}패</code> (승률 <b>{win_rate:.1f}%</b> | PF <b>{pf:.2f}</b>)\n"
            f"  • 총 매수액: <code>{_fmt_krw(buy_amt)}</code> ({len(buys)}건)\n"
            f"  • 총 매도액: <code>{_fmt_krw(sell_amt)}</code> ({len(sells)}건)\n"
            f"  • 총 수수료: <code>{_fmt_krw(tot_fee)}</code>\n"
            f"  • {p_emoji} <b>순 실현손익</b>: <b>{p_sign}{_fmt_krw(tot_profit)}</b> (회전: <b>{_fmt_pct(summed_trade_roi)}</b>)\n"
            f"  • 🏦 <b>계좌 수익률</b>: <b>{p_sign}{account_roi:.3f}%</b> (기준자산 대비)\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💡 새 기준점 설정: <code>/setbase</code> (현재 시점으로 0 리셋)\n"
            f"⏰ <code>{now_str}</code>"
        )
        return text

    @classmethod
    def reset_account_baseline(cls, broker: str = "ALL", note: str = "CCommander 텔레그램 수동 재설정") -> Tuple[bool, str]:
        """기준선(account_baseline.json)을 현재 잔고 기준으로 재설정"""
        import requests
        clean_broker = broker.strip().upper()
        if clean_broker not in ("ALL", "UPBIT", "KIS"):
            clean_broker = "ALL"

        # 1. CTrade HTTP API 호출 시도
        api_url = f"http://127.0.0.1:56789/api/portfolio/reset_baseline?broker={clean_broker}&note={note}"
        try:
            res = requests.post(api_url, timeout=4.0)
            if res.ok:
                data = res.json()
                return True, data.get("message", f"기준선이 성공적으로 갱신되었습니다. ({clean_broker})")
        except Exception as e:
            logger.warning(f"reset_baseline API 호출 실패 (fallback 진행): {e}")

        # 2. Fallback: account_baseline.json 직접 갱신
        try:
            root = cls._get_project_root()
            path = os.path.join(root, "account_baseline.json")
            data = {}
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)

            now_dt = datetime.now()
            today_str = now_dt.strftime("%Y-%m-%d")
            now_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")

            # 최신 trade_history 잔고 또는 기존 값 활용
            hist = cls.load_trade_history()
            last_bal = hist[-1].get("balance", 10000000.0) if hist else 10000000.0

            if clean_broker in ("ALL", "TOTAL"):
                data.setdefault("TOTAL", {})["daily_balance"] = last_bal
                data["TOTAL"]["daily_date"] = today_str
                data["TOTAL"]["daily_timestamp"] = now_str
            if clean_broker in ("ALL", "UPBIT"):
                data.setdefault("UPBIT", {})["daily_date"] = today_str
                data["UPBIT"]["daily_timestamp"] = now_str
            if clean_broker in ("ALL", "KIS"):
                data.setdefault("KIS", {})["daily_date"] = today_str
                data["KIS"]["daily_timestamp"] = now_str

            data.setdefault("history", []).append({
                "id": f"base_{int(now_dt.timestamp())}",
                "timestamp": now_str,
                "broker": clean_broker,
                "action": "RESET_TELEGRAM",
                "note": note
            })

            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

            return True, f"기준선이 성공적으로 갱신되었습니다. ({clean_broker}, {today_str})"
        except Exception as ex:
            return False, f"기준선 갱신 실패: {ex}"

    @classmethod
    def set_trading_active(cls, active: bool) -> Tuple[bool, str]:
        """CTrade 매매 활성화/비활성화 (trading_active 토글 및 영속 저장)"""
        import requests
        api_url = "http://127.0.0.1:56789/api/engine/trade-control"
        
        # 1. CTrade HTTP API 호출 시도
        try:
            res = requests.post(api_url, json={"active": active}, timeout=3.0)
            if res.ok:
                data = res.json()
                return True, data.get("message", "성공")
        except Exception as e:
            logger.warning(f"CTrade trade-control API 연결 불가 (fallback 파일 수정 진행): {e}")

        # 2. Fallback: trading_config.json 직접 수정
        try:
            root = cls._get_project_root()
            cfg_path = os.path.join(root, "CTrade", "config", "trading_config.json")
            if os.path.exists(cfg_path):
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                if "GLOBAL_STRATEGY" in cfg:
                    cfg["GLOBAL_STRATEGY"]["TRADING_ACTIVE"] = active
                with open(cfg_path, "w", encoding="utf-8") as f:
                    json.dump(cfg, f, indent=4, ensure_ascii=False)
                state_str = "ACTIVE (매매 시작)" if active else "PAUSED (매매 일시정지)"
                return True, f"설정 파일 저장 완료: {state_str}"
        except Exception as ex:
            return False, f"설정 변경 실패: {ex}"
            
        return False, "설정 파일을 찾을 수 없습니다."

    @classmethod
    def close_position(cls, symbol: str) -> Tuple[bool, str]:
        """CTrade에 특정 종목 즉시 시장가 청산 명령 전송"""
        import requests
        api_url = "http://127.0.0.1:56789/api/engine/close"
        try:
            res = requests.post(api_url, json={"symbol": symbol.strip().upper()}, timeout=4.0)
            if res.ok:
                return True, f"{symbol} 시장가 수동 청산 주문이 접수되었습니다."
            else:
                return False, f"청산 요청 실패 (HTTP {res.status_code}): {res.text}"
        except Exception as e:
            return False, f"CTrade 엔진 API 통신 실패: {e}"

    @classmethod
    def close_all_positions(cls) -> Tuple[bool, str]:
        """CTrade에 전 종목 긴급 청산 명령 전송"""
        import requests
        api_url = "http://127.0.0.1:56789/api/engine/close-all"
        try:
            res = requests.post(api_url, timeout=5.0)
            if res.ok:
                return True, "보유 중인 전 종목 일괄 긴급 청산 주문이 접수되었습니다."
            else:
                return False, f"전량 청산 요청 실패 (HTTP {res.status_code}): {res.text}"
        except Exception as e:
            return False, f"CTrade 엔진 API 통신 실패: {e}"

    @classmethod
    def get_targets_auto_trade_status(cls) -> List[Dict[str, Any]]:
        """전체 대상 종목의 자동매매(auto_trade) 활성화 현황 조회"""
        import requests
        api_url = "http://127.0.0.1:56789/api/engine/symbol-auto-trade"
        try:
            res = requests.get(api_url, timeout=3.0)
            if res.ok:
                data = res.json()
                if data.get("status") == "success":
                    return data.get("targets", [])
        except Exception:
            pass

        # Fallback: trading_config.json 직접 조회
        try:
            root = cls._get_project_root()
            cfg_path = os.path.join(root, "CTrade", "config", "trading_config.json")
            if os.path.exists(cfg_path):
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                return cfg.get("SCALPING_TARGETS", [])
        except Exception as ex:
            logger.error(f"trading_config.json 읽기 실패: {ex}")
        return []

    @classmethod
    def set_symbol_auto_trade(cls, symbol: str, auto_trade: Optional[bool] = None) -> Tuple[bool, str, Optional[bool]]:
        """특정 종목의 자동매매(auto_trade) 활성화/비활성화 제어 (auto_trade가 None이면 토글)"""
        import requests
        clean_sym = symbol.strip().upper()
        api_url = "http://127.0.0.1:56789/api/engine/symbol-auto-trade"
        payload = {"symbol": clean_sym}
        if auto_trade is not None:
            payload["auto_trade"] = bool(auto_trade)

        # 1. HTTP API 호출 시도
        try:
            res = requests.post(api_url, json=payload, timeout=4.0)
            if res.ok:
                data = res.json()
                final_val = data.get("auto_trade")
                state_str = "ON (자동매매 활성)" if final_val else "OFF (자동매매 비활성)"
                return True, f"<b>{clean_sym}</b> 자동매매가 <b>{state_str}</b> 상태로 변경되었습니다.", final_val
        except Exception as e:
            logger.warning(f"symbol-auto-trade API 연결 불가 (fallback 진행): {e}")

        # 2. Fallback: trading_config.json 직접 수정
        try:
            root = cls._get_project_root()
            cfg_path = os.path.join(root, "CTrade", "config", "trading_config.json")
            if os.path.exists(cfg_path):
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                found = False
                final_val = auto_trade
                for t in cfg.get("SCALPING_TARGETS", []):
                    if t.get("symbol") == clean_sym:
                        if auto_trade is not None:
                            t["auto_trade"] = bool(auto_trade)
                        else:
                            t["auto_trade"] = not t.get("auto_trade", True)
                        final_val = t["auto_trade"]
                        found = True
                        break
                if not found:
                    new_target = {
                        "symbol": clean_sym,
                        "exchange": "UPBIT",
                        "active": True,
                        "auto_trade": bool(auto_trade) if auto_trade is not None else False
                    }
                    cfg.setdefault("SCALPING_TARGETS", []).append(new_target)
                    final_val = new_target["auto_trade"]

                with open(cfg_path, "w", encoding="utf-8") as f:
                    json.dump(cfg, f, indent=4, ensure_ascii=False)
                state_str = "ON (자동매매 활성)" if final_val else "OFF (자동매매 비활성)"
                return True, f"<b>{clean_sym}</b> 자동매매 설정 파일 저장 완료: <b>{state_str}</b>", final_val
        except Exception as ex:
            return False, f"설정 변경 실패: {ex}", None

        return False, "종목 설정을 변경할 수 없습니다.", None


