import os
import asyncio
import logging
import json
import aiohttp
import time
from typing import Dict, Any, Optional, Union
from utils.auth import KISAuthManager
from shared.core.market_utils import round_to_krx_tick
from shared.core.trade_costs import sell_tax_for_symbol

def is_domestic_krx_code(symbol: str) -> bool:
    """국내(KRX) 종목코드 여부: 6자리 영숫자이며 숫자를 1개 이상 포함 (신규 영숫자 단축코드 예: 0015N0 지원).

    해외 티커는 최대 5자 영문(+거래소 접두사 시 6자 초과)이므로 숫자 없는 6자 영문은 해외로 취급한다.
    """
    clean = (symbol or "").strip().upper()
    return len(clean) == 6 and clean.isalnum() and any(ch.isdigit() for ch in clean)


class KISTrader:
    """한국투자증권(KIS) 국내 및 해외주식 REST API 주문 실행기 (UpbitRealTrader와 호환 규격)"""
    def __init__(self, auth_manager: KISAuthManager, is_simulation: bool = False, initial_balance: float = 10000000.0, fee_rate: float = 0.0005):
        self.auth = auth_manager
        self.is_simulation = is_simulation
        self.url_base = auth_manager.url_base
        self.balance = initial_balance
        self.initial_balance = initial_balance
        self.positions: Dict[str, Dict[str, Any]] = {}
        self.history = []
        self.fee_rate = float(fee_rate)  # L-4: 편도 수수료율 (순손익 산출용)
        self._fx_rate = 0.0  # engine이 주입하는 USD/KRW 환율

        # 프로젝트 루트 trade_history.json 경로
        root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        project_root = os.path.dirname(root_dir)
        self.history_file = os.path.join(project_root, "trade_history.json")
        self._load_history()

    def _load_history(self):
        """기존 trade_history.json 로드"""
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, "r", encoding="utf-8") as f:
                    self.history = json.load(f)
            except Exception as e:
                logging.error(f"[KISTrader] History Load Error: {e}")

    def _save_history_sync(self):
        """동기 파일 저장 (M-1: 합집합 병합으로 UPBIT/KIS 상호 이벤트 보존)"""
        try:
            from utils.history_store import save_history_merged
            save_history_merged(self.history_file, self.history)
        except Exception as e:
            logging.error(f"[KISTrader] History Save Error: {e}")

    def _save_history(self):
        """비동기 또는 동기 저장"""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.run_in_executor(None, self._save_history_sync)
            else:
                self._save_history_sync()
        except RuntimeError:
            self._save_history_sync()

    @staticmethod
    def calc_realized_profit(buy_price: float, sell_price: float, qty: float, fee_rate: float, sell_tax: float = 0.0):
        """왕복 수수료와 매도세를 반영한 실현 순손익/수익률/비용 반환 (L-4).

        Returns: (profit_amt, profit_rate_pct, fee)  — fee 는 수수료 + 매도세 합계
        """
        invest = buy_price * qty
        sell_amt = sell_price * qty
        fee = (invest + sell_amt) * fee_rate + sell_amt * sell_tax
        profit = (sell_amt - invest) - fee
        rate = (profit / invest * 100.0) if invest > 0 else 0.0
        return profit, rate, fee


    def _is_overseas(self, symbol: str) -> bool:
        """종목 코드로 국내/해외 판별 (국내는 숫자 6자리)"""
        return not is_domestic_krx_code(symbol)

    def _get_overseas_exchange(self, symbol: str) -> str:
        """
        해외주식 거래소 코드 자동 판별 (KIS 심볼 접두사 기준)
          - DNAS: 나스닥, DNYS: 뉴욕증권거래소, DAMS: 아멕스
        접두사가 없는 경우 나스닥(NASD) 기본
        """
        clean_sym = symbol.strip().upper()
        if clean_sym.startswith("DNYS"):
            return "NYSE"
        if clean_sym.startswith("DAMS"):
            return "AMEX"
        return "NASD"

    def _convert_budget_to_usd(self, budget_krw: float) -> float:
        """
        해외주식 예산 원화(KRW) → 달러(USD) 변환.
        예산이 이미 USD 단위라고 판단되는 규모(1000 이하)면 그대로 사용합니다.
        환율은 set_fx_rate()로 주입된 값 또는 기본 1380 사용.
        """
        if budget_krw <= 1000.0:
            return budget_krw
        fx_rate = self._fx_rate if self._fx_rate > 0 else 1380.0
        return budget_krw / fx_rate

    def set_fx_rate(self, fx_rate: float):
        """환율 주입 (engine/data_manager에서 호출)"""
        self._fx_rate = float(fx_rate) if fx_rate > 0 else 0.0

    def _get_headers(self, tr_id: str) -> Dict[str, str]:
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self.auth.access_token}",
            "appkey": self.auth.app_key,
            "appsecret": self.auth.app_secret,
            "tr_id": tr_id,
            "custtype": "P"
        }

    async def buy(self, symbol: str, price: float, budget: float, order_type: str = "00") -> bool:
        """
        주식/ETF 매수 주문 실행 (엔진 호환 인터페이스)
        - budget과 price로부터 주문 수량(qty) 자동 계산
        - 해외주식은 예산을 USD로 변환하여 수량 계산
        """
        if price <= 0 or budget <= 0:
            logging.error(f"[KISTrader] 매수 실패: 잘못된 가격({price}) 또는 예산({budget})")
            return False

        is_ovrs = self._is_overseas(symbol)
        if is_ovrs:
            # 해외주식: 예산을 USD로 변환하여 수량 계산
            usd_budget = self._convert_budget_to_usd(budget)
            qty = int(usd_budget // price)
        else:
            qty = int(budget // price)
        if qty <= 0:
            currency = "USD" if is_ovrs else "KRW"
            logging.warning(f"[KISTrader] ⚠️ 매수 수량 부족: 예산 {budget:,.0f}({currency})으로 {symbol}(@{price:,.2f}) 최소 1주를 매수할 수 없습니다.")
            return False

        await self.auth.get_access_token()
        is_ovrs = self._is_overseas(symbol)

        if is_ovrs:
            res = await self._buy_overseas(symbol, price, qty, order_type)
        else:
            res = await self._buy_domestic(symbol, price, qty, order_type)

        if res.get("success", False):
            trade_time = time.time()
            prev = self.positions.get(symbol) or {}
            prev_qty = float(prev.get("qty", 0.0) or 0.0)
            if prev_qty > 0.0:
                # 보유 중 추가 매수: 평단/수량 합산 (최초 진입시간 유지, C++ VirtualTrader::buy 와 동일)
                total_qty = prev_qty + float(qty)
                avg_price = (float(prev.get("price", price)) * prev_qty + price * qty) / total_qty
                self.positions[symbol] = {
                    "price": avg_price,
                    "qty": total_qty,
                    "time": prev.get("time", trade_time),
                    "highest_price": max(float(prev.get("highest_price", avg_price) or avg_price), price),
                    "trailing_active": bool(prev.get("trailing_active", False))
                }
            else:
                self.positions[symbol] = {
                    "price": price,
                    "qty": float(qty),
                    "time": trade_time,
                    "highest_price": price,
                    "trailing_active": False
                }
            _fee = price * qty * self.fee_rate
            evt = {
                "type": "BUY",
                "symbol": symbol,
                "price": price,
                "qty": float(qty),
                "fee": _fee,
                "amount": price * qty,
                "time": trade_time,
                "balance": self.balance,
                "exchange": "KIS",
                "is_real": not self.is_simulation,
                "mode": "REAL" if not self.is_simulation else "SIMULATION"
            }
            self.history.append(evt)
            self._save_history()
            logging.info(f"🚀 [KISTrader 체결 완료] 매수: {symbol} | 단가: {price:,.2f} | 수량: {qty}주 | 총액: {price * qty:,.2f} | 모드: {'실전' if not self.is_simulation else '가상'}")
            
            # 텔레그램 실시간 매수 알림
            try:
                from utils.telegram_bot import get_bot
                if bot := get_bot():
                    bot.notify_buy(symbol, price, float(qty), price * qty, extra=evt, is_real=not self.is_simulation)
            except Exception as e:
                logging.warning(f"[KISTrader] 텔레그램 매수 알림 실패: {e}")

            return True
        return False

    async def sell(self, symbol: str, price: float, reason: str = "SIGNAL", order_type: str = "00") -> Union[float, bool]:
        """
        주식/ETF 매도 주문 실행 (엔진 호환 인터페이스)
        - 보유 포지션 전량 매도 및 실현 손익(profit_amt) 반환
        - 포지션 기록이 없는 경우 잔고 조회 후 실제 평단가/수량 조회 시도
        - order_type: '00'(지정가), '01'(시장가)
        """
        pos = self.positions.get(symbol, {})
        qty = int(pos.get("qty", 0))
        buy_price = float(pos.get("price", 0.0))

        # 내부 포지션이 없으면 실계좌 잔고 조회 시도
        if qty <= 0:
            qty_f, avg_p = await self.get_balance_with_avg_price(symbol)
            qty = int(qty_f)
            buy_price = avg_p if avg_p > 0 else price

        if qty <= 0:
            logging.warning(f"[KISTrader] 매도 실패: 보유 잔고 없음 ({symbol})")
            return False

        await self.auth.get_access_token()
        is_ovrs = self._is_overseas(symbol)

        if is_ovrs:
            res = await self._sell_overseas(symbol, price, qty, order_type)
        else:
            res = await self._sell_domestic(symbol, price, qty, order_type)

        if res.get("success", False):
            trade_time = time.time()
            # L-4: 왕복 수수료를 반영한 순손익 계산
            try:
                sell_tax = sell_tax_for_symbol(symbol)
            except Exception as e:  # 매도는 이미 체결됐다 — 손익 기록이 원장 조회 실패로 유실되면 안 된다
                logging.error(f"[KISTrader] 매도세율 조회 실패 — 이번 손익은 매도세 없이 기록됨 ({symbol}): {e}")
                sell_tax = 0.0
            profit_amt, profit_rate, _fee = self.calc_realized_profit(buy_price, price, qty, self.fee_rate, sell_tax)

            # 포지션 제거
            self.positions.pop(symbol, None)

            evt = {
                "type": "SELL",
                "symbol": symbol,
                "price": price,
                "buy_price": buy_price,
                "sell_price": price,
                "qty": float(qty),
                "fee": _fee,
                "profit": profit_amt,
                "profit_rate": profit_rate,
                "reason": reason,
                "time": trade_time,
                "balance": self.balance,
                "exchange": "KIS",
                "is_real": not self.is_simulation,
                "mode": "REAL" if not self.is_simulation else "SIMULATION"
            }
            self.history.append(evt)
            self._save_history()
            logging.info(f"🔄 [KISTrader 체결 완료] 매도: {symbol} | 단가: {price:,.2f} | 수량: {qty}주 | 손익: {profit_amt:+,.2f} ({reason}) | 모드: {'실전' if not self.is_simulation else '가상'}")
            
            # 텔레그램 실시간 매도 알림
            try:
                from utils.telegram_bot import get_bot
                if bot := get_bot():
                    bot.notify_sell(symbol, price, profit_amt, profit_rate, reason, extra=evt, is_real=not self.is_simulation)
            except Exception as e:
                logging.warning(f"[KISTrader] 텔레그램 매도 알림 실패: {e}")

            return profit_amt
        return False

    async def _buy_domestic(self, symbol: str, price: float, qty: int, order_type: str) -> Dict[str, Any]:
        """국내주식 현금매수"""
        tr_id = "VTTC0802U" if self.is_simulation else "TTTC0802U"
        url = f"{self.url_base}/uapi/domestic-stock/v1/trading/order-cash"
        
        # order_type: '00'(지정가), '01'(시장가)
        order_price_str = str(round_to_krx_tick(price)) if order_type == "00" else "0"
        body = {
            "CANO": self.auth.acc_no[:8],
            "ACNT_PRDT_CD": self.auth.acc_code,
            "PDNO": symbol,
            "ORD_DVSN": order_type,
            "ORD_QTY": str(int(qty)),
            "ORD_UNPR": order_price_str
        }
        return await self._send_order(url, tr_id, body, "국내매수", symbol)

    async def _sell_domestic(self, symbol: str, price: float, qty: int, order_type: str) -> Dict[str, Any]:
        """국내주식 현금매도"""
        tr_id = "VTTC0801U" if self.is_simulation else "TTTC0801U"
        url = f"{self.url_base}/uapi/domestic-stock/v1/trading/order-cash"
        
        order_price_str = str(round_to_krx_tick(price)) if order_type == "00" else "0"
        body = {
            "CANO": self.auth.acc_no[:8],
            "ACNT_PRDT_CD": self.auth.acc_code,
            "PDNO": symbol,
            "ORD_DVSN": order_type,
            "ORD_QTY": str(int(qty)),
            "ORD_UNPR": order_price_str
        }
        return await self._send_order(url, tr_id, body, "국내매도", symbol)

    async def _buy_overseas(self, symbol: str, price: float, qty: int, order_type: str) -> Dict[str, Any]:
        """해외(미국)주식 매수"""
        tr_id = "VTTT1002U" if self.is_simulation else "TTTT1002U"
        url = f"{self.url_base}/uapi/overseas-stock/v1/trading/order"
        
        # 시장가 주문('01') 시 단가 0 처리 (KIS API 요구사항)
        unpr = f"{price:.4f}" if order_type == "00" else "0"
        body = {
            "CANO": self.auth.acc_no[:8],
            "ACNT_PRDT_CD": self.auth.acc_code,
            "OVRS_EXCG_CD": self._get_overseas_exchange(symbol),
            "PDNO": symbol,
            "ORD_QTY": str(int(qty)),
            "OVRS_ORD_UNPR": unpr,
            "ORD_SVR_DVSN_CD": "0",
            "ORD_DVSN": order_type
        }
        return await self._send_order(url, tr_id, body, "해외매수", symbol)

    async def _sell_overseas(self, symbol: str, price: float, qty: int, order_type: str) -> Dict[str, Any]:
        """해외(미국)주식 매도"""
        tr_id = "VTTT1006U" if self.is_simulation else "TTTT1006U"
        url = f"{self.url_base}/uapi/overseas-stock/v1/trading/order"
        
        # 시장가 주문('01') 시 단가 0 처리 (KIS API 요구사항)
        unpr = f"{price:.4f}" if order_type == "00" else "0"
        body = {
            "CANO": self.auth.acc_no[:8],
            "ACNT_PRDT_CD": self.auth.acc_code,
            "OVRS_EXCG_CD": self._get_overseas_exchange(symbol),
            "PDNO": symbol,
            "ORD_QTY": str(int(qty)),
            "OVRS_ORD_UNPR": unpr,
            "ORD_SVR_DVSN_CD": "0",
            "ORD_DVSN": order_type
        }
        return await self._send_order(url, tr_id, body, "해외매도", symbol)

    async def _send_order(self, url: str, tr_id: str, body: Dict[str, Any], action_name: str, symbol: str) -> Dict[str, Any]:
        headers = self._get_headers(tr_id)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=body, timeout=aiohttp.ClientTimeout(total=5.0)) as resp:
                    resp_json = await resp.json()
                    rt_cd = resp_json.get("rt_cd")
                    msg1 = resp_json.get("msg1", "")
                    if rt_cd == "0":
                        odno = resp_json.get("output", {}).get("ODNO", "")
                        logging.info(f"🚀 [KIS {action_name} 성공] {symbol} | 주문번호: {odno} | 메시지: {msg1}")
                        return {"success": True, "odno": odno, "msg": msg1, "data": resp_json.get("output")}
                    else:
                        logging.error(f"❌ [KIS {action_name} 실패] {symbol} | 코드: {rt_cd} | 메시지: {msg1}")
                        return {"success": False, "code": rt_cd, "msg": msg1}
        except Exception as e:
            logging.error(f"❌ [KIS {action_name} 통신 에러] {symbol}: {e}")
            return {"success": False, "msg": str(e)}

    async def get_balance(self, symbol: str) -> float:
        """특정 종목의 보유 수량 조회"""
        try:
            qty, _ = await self.get_balance_with_avg_price(symbol)
            return qty
        except Exception as e:
            logging.error(f"[KIS] 잔고 조회 실패 ({symbol}): {e}")
            return 0.0

    async def get_balance_with_avg_price(self, symbol: str) -> tuple:
        """
        특정 종목의 보유 수량 및 평균 단가 조회 (국내/해외 공용)
        Returns: (qty, avg_price)
        """
        await self.auth.get_access_token()
        is_ovrs = self._is_overseas(symbol)
        try:
            if is_ovrs:
                # 해외 잔고 조회
                tr_id = "VTTS3012R" if self.is_simulation else "TTTS3012R"
                url = f"{self.url_base}/uapi/overseas-stock/v1/trading/inquire-balance"
                params = {
                    "CANO": self.auth.acc_no[:8],
                    "ACNT_PRDT_CD": self.auth.acc_code,
                    "OVRS_EXCG_CD": self._get_overseas_exchange(symbol),
                    "TR_CRCY_CD": "USD",
                    "CTX_AREA_FK200": "",
                    "CTX_AREA_NK200": ""
                }
            else:
                # 국내 잔고 조회
                tr_id = "VTTC8434R" if self.is_simulation else "TTTC8434R"
                url = f"{self.url_base}/uapi/domestic-stock/v1/trading/inquire-balance"
                params = {
                    "CANO": self.auth.acc_no[:8],
                    "ACNT_PRDT_CD": self.auth.acc_code,
                    "AFHR_FLPR_YN": "N",
                    "OFL_YN": "",
                    "INQR_DVSN": "02",
                    "UNPR_DVSN": "01",
                    "FUND_STTL_ICLD_YN": "N",
                    "FNCG_AMT_AUTO_RDPT_YN": "N",
                    "PRCS_DVSN": "01",
                    "CTX_AREA_FK100": "",
                    "CTX_AREA_NK100": ""
                }

            headers = self._get_headers(tr_id)
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, params=params, timeout=aiohttp.ClientTimeout(total=5.0)) as resp:
                    data = await resp.json()
                    if data.get("rt_cd") == "0":
                        output1 = data.get("output1", [])
                        for item in output1:
                            item_sym = item.get("ovrs_pdno") or item.get("pdno", "")
                            if item_sym == symbol:
                                qty = float(item.get("ovrs_cblc_qty") or item.get("hldg_qty", 0.0))
                                # 평균 단가 필드 (해외: ovrs_pchs_avg_pchs, 국내: pchs_avg_pric)
                                avg_price = float(item.get("ovrs_pchs_avg_pchs") or item.get("pchs_avg_pric", 0.0))
                                return qty, avg_price
            return 0.0, 0.0
        except Exception as e:
            logging.error(f"[KIS] 잔고/평단가 조회 실패 ({symbol}): {e}")
            return 0.0, 0.0

    async def get_current_price(self, symbol: str) -> float:
        """국내 주식/ETF 현재가 조회 (FHKST01010100)"""
        await self.auth.get_access_token()
        clean_sym = symbol.strip()
        tr_id = "FHKST01010100"
        url = f"{self.url_base}/uapi/domestic-stock/v1/quotations/inquire-price"
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": clean_sym
        }
        headers = self._get_headers(tr_id)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, params=params, timeout=aiohttp.ClientTimeout(total=3.0)) as resp:
                    data = await resp.json()
                    if data.get("rt_cd") == "0":
                        output = data.get("output", {})
                        stck_prpr = output.get("stck_prpr", "0")
                        return float(stck_prpr)
            return 0.0
        except Exception as e:
            logging.error(f"[KIS] 현재가 조회 에러 ({symbol}): {e}")
            return 0.0

    async def get_index_price(self, index_code: str = "2001") -> float:
        """국내 업종/지수 현재가 조회 (FHKUP03500100, 2001=코스피 200)"""
        await self.auth.get_access_token()
        tr_id = "FHKUP03500100"
        url = f"{self.url_base}/uapi/domestic-stock/v1/quotations/inquire-index-price"
        params = {
            "FID_COND_MRKT_DIV_CODE": "U",
            "FID_INPUT_ISCD": index_code
        }
        headers = self._get_headers(tr_id)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, params=params, timeout=aiohttp.ClientTimeout(total=3.0)) as resp:
                    data = await resp.json()
                    if data.get("rt_cd") == "0":
                        output = data.get("output", {})
                        bstp_nmix_prpr = output.get("bstp_nmix_prpr", "0")
                        return float(bstp_nmix_prpr)
        except Exception as e:
            logging.error(f"[KIS] 지수 현재가 조회 에러 ({index_code}): {e}")
            return 0.0