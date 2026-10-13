"""
PortfolioTracker: 원천 데이터 기반 실시간 포트폴리오 평가 및 기준금액(Baseline) 관리 모듈
=====================================================================================
- 증권사(KIS) 및 가상자산 거래소(UPBIT)의 원천 잔고 데이터를 바탕으로 시스템 내부에서
  실시간 평가액, 평가손익, 수익률을 일관되게 직접 계산합니다.
- 계좌 내 보유 전종목(All Holdings)을 빌드하고 BOT 운용 종목과 외부/기존 보유 종목을 명확히 태깅합니다.
- 당일 시작금액(daily_baseline) 및 누적 시작금액(initial_baseline)을 파일(account_baseline.json)에
  영구 보존하여 시스템 재기동 시에도 손익 및 수익률 왜곡을 원천 방지합니다.
"""
import os
import json
import time
import uuid
import logging
from datetime import datetime, timezone, timedelta

# L-1: 모든 기준선 타임스탬프는 KST 기준으로 명시 (서버 TZ 무관)
KST = timezone(timedelta(hours=9))
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger("PortfolioTracker")


def _get_project_root() -> str:
    current_dir = os.path.dirname(os.path.abspath(__file__)) # CTrade/utils
    ctrade_dir = os.path.dirname(current_dir)                # CTrade
    return os.path.dirname(ctrade_dir)                      # CTradeSystem


def _get_baseline_path() -> str:
    root = _get_project_root()
    return os.path.join(root, "account_baseline.json")


def _load_symbols_registry() -> Dict[str, Any]:
    """종목 마스터 원장 로드 (shared.config.config_manager 경유 — 파일 직접 읽기 금지)"""
    from shared.config.config_manager import load_symbols_registry
    return load_symbols_registry()


def load_baseline() -> Dict[str, Any]:
    """account_baseline.json 로드 (없을 경우 기본 템플릿 반환)"""
    path = _get_baseline_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception as e:
            logger.error(f"[PortfolioTracker] account_baseline.json 읽기 오류: {e}")
    return {}


def save_baseline(baseline_data: Dict[str, Any]) -> bool:
    """account_baseline.json 원자적 저장"""
    path = _get_baseline_path()
    tmp_path = path + f".tmp.{os.getpid()}"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(baseline_data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, path)
        return True
    except Exception as e:
        logger.error(f"[PortfolioTracker] account_baseline.json 저장 오류: {e}")
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        return False


def get_symbol_name(symbol: str, registry: Optional[Dict[str, Any]] = None) -> str:
    """종목 코드를 한글 종목명으로 친절하게 변환 (이름 정답은 종목 마스터 원장 하나뿐)"""
    if not symbol:
        return "N/A"
    clean_sym = symbol.strip()
    if registry is not None:
        name = (registry.get(clean_sym) or {}).get("name", "")
        if name:
            return name
    else:
        from shared.config.config_manager import get_symbol_name as ledger_name
        name = ledger_name(clean_sym)
        if name:
            return name
    if clean_sym.startswith("KRW-"):
        return clean_sym.replace("KRW-", "")
    return clean_sym


class PortfolioTracker:
    """원천 데이터 기반 실시간 자산/수익률 계산 및 기준선 관리자"""

    @classmethod
    def update_baseline_rollover(cls, upbit_total: float, kis_total: float) -> Dict[str, Any]:
        """
        일별 롤오버 체크 및 기준선 최신화
        - 당일 날짜(YYYY-MM-DD)와 다를 경우 daily_balance를 현재 총평가액으로 롤오버
        - baseline 파일이 없거나 비어있는 경우 최초 initial_balance로 초기화
        """
        baseline = load_baseline()
        today_str = datetime.now(KST).strftime("%Y-%m-%d")
        now_str = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
        combined_total = upbit_total + kis_total
        changed = False

        for broker, cur_total in [("UPBIT", upbit_total), ("KIS", kis_total), ("TOTAL", combined_total)]:
            b_info = baseline.get(broker, {})
            init_bal = b_info.get("initial_balance")
            daily_bal = b_info.get("daily_balance")
            daily_date = b_info.get("daily_date", "")

            # 1) 최초 가동 시 initial_balance 설정
            if init_bal is None or init_bal <= 0:
                b_info["initial_balance"] = cur_total
                b_info["initial_timestamp"] = now_str
                changed = True

            # 2) 일별 날짜가 바뀌었거나 daily_balance가 없는 경우 롤오버
            if daily_date != today_str or daily_bal is None:
                b_info["daily_balance"] = cur_total
                b_info["daily_date"] = today_str
                b_info["daily_timestamp"] = now_str
                changed = True

            baseline[broker] = b_info

        save_baseline(baseline)
        return baseline

    @classmethod
    def get_baseline_history(cls) -> List[Dict[str, Any]]:
        """기준선 변경 이력 목록 반환 (최신순)"""
        baseline = load_baseline()
        history = baseline.get("history", [])
        return sorted(history, key=lambda x: x.get("timestamp", ""), reverse=True)

    @classmethod
    def reset_baseline(cls, upbit_total: float, kis_total: float, target_broker: str = "ALL", note: str = "수동 재설정") -> Dict[str, Any]:
        """
        사용자 수동 요청에 따라 기준선(initial + daily) 재설정 및 변경 이력 누적
        - target_broker: "ALL" | "UPBIT" | "KIS"
        """
        baseline = load_baseline()
        today_str = datetime.now(KST).strftime("%Y-%m-%d")
        now_str = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
        target_upper = target_broker.upper()

        # 1. 갱신 전 상태를 이력(History)에 스냅샷 저장
        history = baseline.get("history", [])
        prev_u = float(baseline.get("UPBIT", {}).get("daily_balance", upbit_total))
        prev_k = float(baseline.get("KIS", {}).get("daily_balance", kis_total))
        prev_tot = float(baseline.get("TOTAL", {}).get("daily_balance", prev_u + prev_k))

        # 2. 신규 기준선 설정
        if target_upper in ("UPBIT", "ALL", "TOTAL"):
            baseline["UPBIT"] = {
                "initial_balance": upbit_total,
                "initial_timestamp": now_str,
                "daily_balance": upbit_total,
                "daily_date": today_str,
                "daily_timestamp": now_str
            }

        if target_upper in ("KIS", "ALL", "TOTAL"):
            baseline["KIS"] = {
                "initial_balance": kis_total,
                "initial_timestamp": now_str,
                "daily_balance": kis_total,
                "daily_date": today_str,
                "daily_timestamp": now_str
            }

        # TOTAL 기준선 재계산 (갱신된 UPBIT + 갱신/보존된 KIS)
        u_init = float(baseline.get("UPBIT", {}).get("initial_balance", upbit_total))
        u_daily = float(baseline.get("UPBIT", {}).get("daily_balance", upbit_total))
        k_init = float(baseline.get("KIS", {}).get("initial_balance", kis_total))
        k_daily = float(baseline.get("KIS", {}).get("daily_balance", kis_total))

        baseline["TOTAL"] = {
            "initial_balance": u_init + k_init,
            "initial_timestamp": now_str,
            "daily_balance": u_daily + k_daily,
            "daily_date": today_str,
            "daily_timestamp": now_str
        }

        # 이력 레코드 생성
        h_item = {
            "id": f"base_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}",
            "timestamp": now_str,
            "broker": target_upper,
            "action": "RESET",
            "upbit_balance": u_daily,
            "kis_balance": k_daily,
            "total_balance": u_daily + k_daily,
            "prev_upbit": prev_u,
            "prev_kis": prev_k,
            "prev_total": prev_tot,
            "note": note
        }
        history.insert(0, h_item)
        baseline["history"] = history[:50]  # 최대 50건 유지

        save_baseline(baseline)
        logger.info(f"[PortfolioTracker] 기준선 수동 재설정 완료 ({target_upper}): TOTAL=₩{u_daily + k_daily:,.0f} (이력 ID: {h_item['id']})")
        return baseline

    @classmethod
    def restore_baseline(cls, history_id: str) -> Optional[Dict[str, Any]]:
        """
        특정 과거 이력(history_id)의 기준선 스냅샷으로 복구(Undo/Rollback)
        """
        baseline = load_baseline()
        history = baseline.get("history", [])
        target_item = next((h for h in history if h.get("id") == history_id), None)
        if not target_item:
            logger.warning(f"[PortfolioTracker] 복구 대상 이력을 찾을 수 없음: {history_id}")
            return None

        today_str = datetime.now(KST).strftime("%Y-%m-%d")
        now_str = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")

        u_bal = float(target_item.get("upbit_balance", 0.0))
        k_bal = float(target_item.get("kis_balance", 0.0))
        tot_bal = float(target_item.get("total_balance", u_bal + k_bal))
        orig_ts = target_item.get("timestamp", now_str)

        baseline["UPBIT"] = {
            "initial_balance": u_bal,
            "initial_timestamp": orig_ts,
            "daily_balance": u_bal,
            "daily_date": today_str,
            "daily_timestamp": now_str
        }
        baseline["KIS"] = {
            "initial_balance": k_bal,
            "initial_timestamp": orig_ts,
            "daily_balance": k_bal,
            "daily_date": today_str,
            "daily_timestamp": now_str
        }
        baseline["TOTAL"] = {
            "initial_balance": tot_bal,
            "initial_timestamp": orig_ts,
            "daily_balance": tot_bal,
            "daily_date": today_str,
            "daily_timestamp": now_str
        }

        # 복구 이벤트도 이력에 추가
        restore_item = {
            "id": f"base_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}",
            "timestamp": now_str,
            "broker": target_item.get("broker", "ALL"),
            "action": "RESTORE",
            "upbit_balance": u_bal,
            "kis_balance": k_bal,
            "total_balance": tot_bal,
            "note": f"과거 스냅샷({orig_ts})으로 복구"
        }
        history.insert(0, restore_item)
        baseline["history"] = history[:50]

        save_baseline(baseline)
        logger.info(f"[PortfolioTracker] 과거 기준선 복구 완료 ({orig_ts} 기준): TOTAL=₩{tot_bal:,.0f}")
        return baseline

    @classmethod
    def delete_baseline_history(cls, history_id: str) -> bool:
        """특정 기준선 이력 항목 삭제"""
        baseline = load_baseline()
        history = baseline.get("history", [])
        new_hist = [h for h in history if h.get("id") != history_id]
        if len(new_hist) == len(history):
            return False

        baseline["history"] = new_hist
        save_baseline(baseline)
        logger.info(f"[PortfolioTracker] 기준선 이력 항목 삭제 완료: {history_id}")
        return True

    @classmethod
    def calculate_portfolio(
        cls,
        upbit_raw: List[Dict[str, Any]],
        kis_raw: Dict[str, Any],
        live_prices: Dict[str, float],
        active_bot_symbols: Optional[List[str]] = None,
        usd_krw_fx: float = 1380.0
    ) -> Dict[str, Any]:
        """
        원천 잔고 데이터(UPBIT/KIS 국내/해외)와 실시간 시세 및 환율을 결합하여 포트폴리오 산출
        """
        if active_bot_symbols is None:
            active_bot_symbols = []
        bot_sym_set = set(active_bot_symbols)
        registry = _load_symbols_registry()
        fx_rate = float(usd_krw_fx) if float(usd_krw_fx) > 0 else 1380.0

        holdings_all: List[Dict[str, Any]] = []
        upbit_holdings: List[Dict[str, Any]] = []
        kis_holdings: List[Dict[str, Any]] = []
        kis_domestic_holdings: List[Dict[str, Any]] = []
        kis_overseas_holdings: List[Dict[str, Any]] = []

        # ── 1. UPBIT 코인 포트폴리오 산출 ──
        upbit_krw_deposit = 0.0
        upbit_coins_eval = 0.0
        upbit_coins_buy = 0.0

        if isinstance(upbit_raw, list):
            for item in upbit_raw:
                cur = str(item.get("currency", "")).upper()
                bal = float(item.get("balance", 0.0))
                locked = float(item.get("locked", 0.0))
                total_qty = bal + locked
                avg_price = float(item.get("avg_buy_price", 0.0))

                if cur == "KRW":
                    upbit_krw_deposit += total_qty
                    continue

                if total_qty <= 0:
                    continue

                sym = f"KRW-{cur}"
                # 실시간 시세 매칭: live_prices 우선 -> item 내장 단가 -> avg_price
                curr_price = float(live_prices.get(sym, 0.0))
                if curr_price <= 0:
                    curr_price = float(item.get("current_price", 0.0)) or avg_price

                eval_amt = total_qty * curr_price
                buy_amt = total_qty * avg_price
                profit_amt = eval_amt - buy_amt
                profit_rate = (profit_amt / buy_amt * 100.0) if buy_amt > 0 else 0.0

                role = "BOT" if sym in bot_sym_set else "EXTERNAL"
                name = get_symbol_name(sym, registry)

                holding_obj = {
                    "broker": "UPBIT",
                    "market": "CRYPTO",
                    "currency": "KRW",
                    "symbol": sym,
                    "name": name,
                    "role": role,
                    "qty": total_qty,
                    "avg_price": avg_price,
                    "current_price": curr_price,
                    "eval_amount": eval_amt,
                    "buy_amount": buy_amt,
                    "profit_amount": profit_amt,
                    "profit_rate": profit_rate
                }
                upbit_holdings.append(holding_obj)
                holdings_all.append(holding_obj)
                upbit_coins_eval += eval_amt
                upbit_coins_buy += buy_amt

        upbit_total_eval = upbit_krw_deposit + upbit_coins_eval

        # ── 2. KIS 국내 주식/ETF 포트폴리오 산출 ──
        kis_krw_deposit = float(kis_raw.get("krw_deposit", 0.0)) if isinstance(kis_raw, dict) else 0.0
        kis_usd_deposit = float(kis_raw.get("usd_deposit", 0.0)) if isinstance(kis_raw, dict) else 0.0
        kis_dom_eval = 0.0
        kis_dom_buy = 0.0

        stocks_list = kis_raw.get("stocks", []) if isinstance(kis_raw, dict) else []
        if isinstance(stocks_list, list):
            for item in stocks_list:
                sym = str(item.get("pdno", "")).strip()
                if not sym:
                    continue
                qty = float(item.get("hldg_qty", 0.0))
                if qty <= 0:
                    continue

                avg_price = float(item.get("pchs_avg_pric", 0.0))
                raw_prpr = float(item.get("prpr", 0.0))
                curr_price = float(live_prices.get(sym, 0.0)) or raw_prpr or avg_price

                eval_amt = qty * curr_price
                buy_amt = qty * avg_price
                profit_amt = eval_amt - buy_amt
                profit_rate = (profit_amt / buy_amt * 100.0) if buy_amt > 0 else 0.0

                role = "BOT" if sym in bot_sym_set else "EXTERNAL"
                raw_name = item.get("prdt_name", "")
                name = raw_name if raw_name else get_symbol_name(sym, registry)

                holding_obj = {
                    "broker": "KIS",
                    "market": "DOMESTIC",
                    "currency": "KRW",
                    "symbol": sym,
                    "name": name,
                    "role": role,
                    "qty": qty,
                    "avg_price": avg_price,
                    "current_price": curr_price,
                    "eval_amount": eval_amt,
                    "buy_amount": buy_amt,
                    "profit_amount": profit_amt,
                    "profit_rate": profit_rate
                }
                kis_domestic_holdings.append(holding_obj)
                kis_holdings.append(holding_obj)
                holdings_all.append(holding_obj)
                kis_dom_eval += eval_amt
                kis_dom_buy += buy_amt

        # ── 3. KIS 해외 주식/ETF 포트폴리오 산출 (USD -> KRW 환산) ──
        kis_ovrs_eval_usd = 0.0
        kis_ovrs_buy_usd = 0.0

        overseas_list = kis_raw.get("overseas_stocks", []) if isinstance(kis_raw, dict) else []
        if isinstance(overseas_list, list):
            for item in overseas_list:
                sym = str(item.get("ovrs_pdno", "") or item.get("pdno", "")).strip()
                if not sym:
                    continue
                qty = float(item.get("ovrs_cblc_qty", 0.0) or item.get("hldg_qty", 0.0))
                if qty <= 0:
                    continue

                avg_price_usd = float(item.get("pchs_avg_pric", 0.0))
                raw_now_usd = float(item.get("now_pric2", 0.0) or item.get("prpr", 0.0))
                curr_price_usd = float(live_prices.get(sym, 0.0)) or raw_now_usd or avg_price_usd

                eval_amt_usd = qty * curr_price_usd
                buy_amt_usd = qty * avg_price_usd
                profit_amt_usd = eval_amt_usd - buy_amt_usd
                profit_rate = (profit_amt_usd / buy_amt_usd * 100.0) if buy_amt_usd > 0 else 0.0

                eval_amt_krw = eval_amt_usd * fx_rate
                buy_amt_krw = buy_amt_usd * fx_rate
                profit_amt_krw = eval_amt_krw - buy_amt_krw

                role = "BOT" if sym in bot_sym_set else "EXTERNAL"
                raw_name = item.get("ovrs_item_name", "") or item.get("prdt_name", "")
                name = raw_name if raw_name else get_symbol_name(sym, registry)

                holding_obj = {
                    "broker": "KIS",
                    "market": "OVERSEAS",
                    "currency": "USD",
                    "symbol": sym,
                    "name": name,
                    "role": role,
                    "qty": qty,
                    "avg_price": avg_price_usd,
                    "current_price": curr_price_usd,
                    "eval_amount_usd": eval_amt_usd,
                    "buy_amount_usd": buy_amt_usd,
                    "eval_amount": eval_amt_krw,
                    "buy_amount": buy_amt_krw,
                    "profit_amount": profit_amt_krw,
                    "profit_rate": profit_rate
                }
                kis_overseas_holdings.append(holding_obj)
                kis_holdings.append(holding_obj)
                holdings_all.append(holding_obj)
                kis_ovrs_eval_usd += eval_amt_usd
                kis_ovrs_buy_usd += buy_amt_usd

        kis_domestic_total = kis_krw_deposit + kis_dom_eval
        kis_overseas_total_usd = kis_usd_deposit + kis_ovrs_eval_usd
        kis_overseas_total_krw = kis_overseas_total_usd * fx_rate
        kis_total_eval = kis_domestic_total + kis_overseas_total_krw

        combined_total_eval = upbit_total_eval + kis_total_eval

        # ── 3. 기준금액(Baseline) 대조 및 손익/수익률 산출 ──
        # 일별 롤오버 갱신
        baseline = cls.update_baseline_rollover(upbit_total_eval, kis_total_eval)

        # UPBIT 성과
        u_base_daily = float(baseline.get("UPBIT", {}).get("daily_balance", upbit_total_eval))
        u_base_init = float(baseline.get("UPBIT", {}).get("initial_balance", upbit_total_eval))
        u_day_pnl = upbit_total_eval - u_base_daily
        u_day_roi = (u_day_pnl / u_base_daily * 100.0) if u_base_daily > 0 else 0.0
        u_tot_pnl = upbit_total_eval - u_base_init
        u_tot_roi = (u_tot_pnl / u_base_init * 100.0) if u_base_init > 0 else 0.0

        # KIS 성과
        k_base_daily = float(baseline.get("KIS", {}).get("daily_balance", kis_total_eval))
        k_base_init = float(baseline.get("KIS", {}).get("initial_balance", kis_total_eval))
        k_day_pnl = kis_total_eval - k_base_daily
        k_day_roi = (k_day_pnl / k_base_daily * 100.0) if k_base_daily > 0 else 0.0
        k_tot_pnl = kis_total_eval - k_base_init
        k_tot_roi = (k_tot_pnl / k_base_init * 100.0) if k_base_init > 0 else 0.0

        # TOTAL 통합 성과
        tot_base_daily = float(baseline.get("TOTAL", {}).get("daily_balance", combined_total_eval))
        tot_base_init = float(baseline.get("TOTAL", {}).get("initial_balance", combined_total_eval))
        tot_day_pnl = combined_total_eval - tot_base_daily
        tot_day_roi = (tot_day_pnl / tot_base_daily * 100.0) if tot_base_daily > 0 else 0.0
        tot_tot_pnl = combined_total_eval - tot_base_init
        tot_tot_roi = (tot_tot_pnl / tot_base_init * 100.0) if tot_base_init > 0 else 0.0

        # 보유 전종목 정렬: 평가금액 내림차순
        holdings_all.sort(key=lambda x: x.get("eval_amount", 0.0), reverse=True)
        upbit_holdings.sort(key=lambda x: x.get("eval_amount", 0.0), reverse=True)
        kis_holdings.sort(key=lambda x: x.get("eval_amount", 0.0), reverse=True)
        kis_domestic_holdings.sort(key=lambda x: x.get("eval_amount", 0.0), reverse=True)
        kis_overseas_holdings.sort(key=lambda x: x.get("eval_amount", 0.0), reverse=True)

        has_overseas = (len(kis_overseas_holdings) > 0) or (kis_usd_deposit > 0)

        return {
            "summary": {
                "upbit_total": upbit_total_eval,
                "upbit_deposit": upbit_krw_deposit,
                "upbit_coins_eval": upbit_coins_eval,
                "upbit_day_pnl": u_day_pnl,
                "upbit_day_roi": u_day_roi,
                "upbit_total_pnl": u_tot_pnl,
                "upbit_total_roi": u_tot_roi,
                
                "kis_total": kis_total_eval,
                "kis_deposit": kis_krw_deposit,
                "kis_stocks_eval": kis_dom_eval + (kis_ovrs_eval_usd * fx_rate),
                "kis_domestic_total": kis_domestic_total,
                "kis_domestic_stocks_eval": kis_dom_eval,
                "kis_overseas_total_usd": kis_overseas_total_usd,
                "kis_overseas_total_krw": kis_overseas_total_krw,
                "kis_overseas_stocks_eval_usd": kis_ovrs_eval_usd,
                "kis_usd_deposit": kis_usd_deposit,
                "has_overseas_holdings": has_overseas,
                "usd_krw_fx": fx_rate,

                "kis_day_pnl": k_day_pnl,
                "kis_day_roi": k_day_roi,
                "kis_total_pnl": k_tot_pnl,
                "kis_total_roi": k_tot_roi,
                
                "combined_total": combined_total_eval,
                "combined_deposit": upbit_krw_deposit + kis_krw_deposit + (kis_usd_deposit * fx_rate),
                "combined_day_pnl": tot_day_pnl,
                "combined_day_roi": tot_day_roi,
                "combined_total_pnl": tot_tot_pnl,
                "combined_total_roi": tot_tot_roi,
            },
            "holdings_all": holdings_all,
            "upbit_holdings": upbit_holdings,
            "kis_holdings": kis_holdings,
            "kis_domestic_holdings": kis_domestic_holdings,
            "kis_overseas_holdings": kis_overseas_holdings,
            "has_overseas_holdings": has_overseas,
            "baseline": baseline,
            # 하위 호환성 유지 필드
            "upbit": upbit_raw,
            "kis": kis_raw,
            "ts": time.time()
        }
