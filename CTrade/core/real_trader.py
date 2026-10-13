import asyncio
import time
import uuid as _uuid
import logging
import httpx
from core.simulator import VirtualTrader


class PartialSellResult(float):
    """부분 체결 매도 결과 (포지션 일부가 남아 있음).

    float 값 = 체결된 부분의 실현 순손익(원). `partial=True`, `remaining_qty` 속성을 가진다.
    전량 청산이 아니므로 소비측(engine)은 이를 '청산 완료'로 취급해서는 안 된다.
    """
    partial = True
    dust_sync = False

    def __new__(cls, profit: float, remaining_qty: float = 0.0):
        obj = super().__new__(cls, profit)
        obj.remaining_qty = float(remaining_qty)
        return obj


class DustSyncResult(float):
    """실제 잔고가 0/Dust 여서 로컬 허수 포지션만 정리한 결과.

    실제 체결(손익)이 없으므로 float 값은 0.0 이며 `dust_sync=True` 이다.
    소비측은 이를 승리/패배로 기록해서는 안 된다(손익 미확정).
    """
    partial = False
    dust_sync = True

    def __new__(cls):
        return super().__new__(cls, 0.0)

class UpbitRealTrader(VirtualTrader):
    _default_is_real = True

    def __init__(self, upbit_auth, initial_balance: float = 10000000.0, fee_rate: float = 0.0005, silent: bool = False, ref_mapping: dict = None):
        super().__init__(initial_balance, fee_rate, silent)
        self.upbit_auth = upbit_auth
        self.ref_mapping = ref_mapping or {}

    async def get_upbit_balance(self, symbol: str):
        """업비트에서 특정 자산의 보유 수량 조회.
        조회 실패(인증 누락/HTTP 오류/예외)는 None, 계좌에 해당 자산이 없으면 0.0을 반환한다.
        (실패를 0.0으로 돌려주면 호출측이 '잔고 없음(Dust)'으로 오판해 포지션을 지워버린다)"""
        if not self.upbit_auth:
            logging.warning("[RealTrader] UpbitAuth is missing. Skip fetch balance.")
            return None
            
        actual_symbol = self.ref_mapping.get(symbol, symbol)
        url = "https://api.upbit.com/v1/accounts"
        try:
            headers = self.upbit_auth.get_headers()
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    accounts = resp.json()
                    currency = actual_symbol.split("-")[-1]  # KRW-BTC -> BTC
                    for acc in accounts:
                        if acc.get("currency") == currency:
                            return float(acc.get("balance", 0.0))
                    return 0.0
                else:
                    logging.error(f"[RealTrader] Upbit Account HTTP {resp.status_code}: {resp.text}")
        except Exception as e:
            logging.error(f"[RealTrader] Failed to fetch balance for {symbol}: {e}")
        return None

    async def get_upbit_krw_balance(self) -> float:
        """업비트에서 실제 보유 KRW(원화) 잔고 조회"""
        if not self.upbit_auth:
            logging.warning("[RealTrader] UpbitAuth is missing. Skip fetch KRW balance.")
            return 0.0
        url = "https://api.upbit.com/v1/accounts"
        try:
            headers = self.upbit_auth.get_headers()
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    accounts = resp.json()
                    for acc in accounts:
                        if acc.get("currency") == "KRW":
                            return float(acc.get("balance", 0.0))
                else:
                    logging.error(f"[RealTrader] Upbit Account HTTP {resp.status_code}: {resp.text}")
        except Exception as e:
            logging.error(f"[RealTrader] Failed to fetch KRW balance: {e}")
        return 0.0

    async def fetch_order_details(self, order_uuid: str) -> dict:
        """주문 상세 정보를 조회하여 실제 평균 체결가와 수량을 분석합니다 (최대 2초 대기).

        - 체결(trades)이 여러 건이면 전부 합산한다 (가중평균가 / 총 체결수량 / 수수료).
        - state 가 done/cancel 이 되면 확정 결과를 반환한다.
        - 대기 시간 내 확정되지 않으면: 일부 체결분이 보이면 그 값을(state='wait'), 아무 정보도 못 얻으면
          executed_volume=0, state='unknown' 을 반환한다 (전량 체결로 가정하지 않는다).
        """
        url = "https://api.upbit.com/v1/order"
        query = {"uuid": order_uuid}

        partial = None
        for attempt in range(10):
            try:
                # 요청마다 JWT nonce 가 달라야 하므로 헤더를 매번 새로 생성
                headers = self.upbit_auth.get_headers(query)
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.get(url, params=query, headers=headers)
                    if resp.status_code == 200:
                        order_data = resp.json()
                        trades = order_data.get("trades", []) or []
                        state = order_data.get("state")

                        total_volume = 0.0
                        total_funds = 0.0
                        for t in trades:
                            vol = float(t.get("volume", 0.0))
                            price = float(t.get("price", 0.0))
                            total_volume += vol
                            total_funds += (vol * price)

                        avg_price = (total_funds / total_volume) if total_volume > 0.0 else 0.0
                        executed_vol = total_volume
                        if executed_vol <= 0.0 and state in ("done", "cancel"):
                            executed_vol = float(order_data.get("executed_volume") or 0.0)
                            avg_price = float(order_data.get("price") or 0.0)
                        try:
                            paid_fee = float(order_data.get("paid_fee") or 0.0)
                        except Exception:
                            paid_fee = 0.0

                        result = {
                            "avg_price": avg_price,
                            "executed_volume": executed_vol,
                            "state": state,
                            "paid_fee": paid_fee,
                        }
                        if state in ("done", "cancel"):
                            return result
                        if executed_vol > 0.0:
                            # 아직 진행 중인 주문의 일부 체결분: 계속 폴링하되 마지막 값을 보관
                            partial = result
                await asyncio.sleep(0.2)
            except Exception as e:
                logging.error(f"[RealTrader] Exception fetching order details for {order_uuid}: {e}")
                await asyncio.sleep(0.2)

        if partial is not None:
            partial["state"] = partial.get("state") or "wait"
            return partial
        # 타임아웃: 체결 여부 확인 불가
        return {"avg_price": 0.0, "executed_volume": 0.0, "state": "unknown", "paid_fee": 0.0}

    @staticmethod
    def _new_identifier(side: str) -> str:
        """업비트 주문 멱등성 키 (계정 내 유일해야 함)."""
        return f"ctrade-{side}-{_uuid.uuid4().hex}"

    async def _lookup_order_by_identifier(self, identifier: str):
        """POST 타임아웃/예외 후 해당 identifier 로 주문이 실제 접수되었는지 조회 -> uuid 또는 None."""
        url = "https://api.upbit.com/v1/order"
        query = {"identifier": identifier}
        for attempt in range(3):
            try:
                headers = self.upbit_auth.get_headers(query)
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.get(url, params=query, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("uuid"):
                        return data.get("uuid")
                elif resp.status_code == 404:
                    pass  # 아직 조회되지 않음/미접수 -> 재시도
                else:
                    logging.error(f"[RealTrader] Order lookup HTTP {resp.status_code}: {resp.text}")
            except Exception as e:
                logging.error(f"[RealTrader] Order lookup exception ({identifier}): {e}")
            await asyncio.sleep(0.5)
        return None

    async def _post_order(self, query: dict, label: str):
        """주문 POST. 응답 유실(타임아웃/예외/5xx) 시 identifier 로 접수 여부를 확인해 uuid 를 복구한다."""
        url = "https://api.upbit.com/v1/orders"
        identifier = query.get("identifier")
        ambiguous = False
        try:
            headers = self.upbit_auth.get_headers(query)
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(url, json=query, headers=headers)
                if resp.status_code in [200, 201]:
                    order_data = resp.json()
                    logging.info(f"[RealTrader] Upbit {label} Order Placed: {order_data}")
                    return order_data.get("uuid")
                logging.error(f"[RealTrader] Upbit {label} Failed HTTP {resp.status_code}: {resp.text}")
                ambiguous = resp.status_code >= 500
        except Exception as e:
            logging.error(f"[RealTrader] Upbit {label} Exception: {e}")
            ambiguous = True

        if ambiguous and identifier:
            logging.warning(f"[RealTrader] {label} 응답 불확실 -> identifier({identifier})로 실제 접수 여부 확인")
            found = await self._lookup_order_by_identifier(identifier)
            if found:
                logging.warning(f"[RealTrader] {label} 주문이 실제로 접수되었음을 확인 (uuid={found}).")
                return found
        return None

    async def buy_market(self, symbol: str, amount: float) -> str:
        """실제 업비트 시장가 매수 주문 실행 (원화 지정 매수) -> 주문 uuid 반환"""
        if not self.upbit_auth:
            logging.error("[RealTrader] UpbitAuth is missing. Cannot place buy order.")
            return None

        actual_symbol = self.ref_mapping.get(symbol, symbol)
        query = {
            "market": actual_symbol,
            "side": "bid",
            "price": str(int(amount)),
            "ord_type": "price",
            "identifier": self._new_identifier("bid"),
        }
        return await self._post_order(query, "Market Buy")

    async def sell_market(self, symbol: str, volume: float) -> str:
        """실제 업비트 시장가 매도 주문 실행 (수량 지정 매도) -> 주문 uuid 반환"""
        if not self.upbit_auth:
            logging.error("[RealTrader] UpbitAuth is missing. Cannot place sell order.")
            return None

        if volume <= 0.0:
            logging.warning(f"[RealTrader] Cannot sell 0 or negative volume for {symbol}: {volume}")
            return None

        actual_symbol = self.ref_mapping.get(symbol, symbol)
        query = {
            "market": actual_symbol,
            "side": "ask",
            "volume": f"{volume:.8f}",
            "ord_type": "market",
            "identifier": self._new_identifier("ask"),
        }
        return await self._post_order(query, "Market Sell")

    def _merge_position(self, symbol: str, avg_price: float, qty: float, curr_time: float):
        """보유 중 추가 매수 시 평단/수량을 합산해 로컬 포지션을 등록한다 (C++ VirtualTrader::buy 와 동일: 최초 진입시간 유지)."""
        old = self.positions.get(symbol) or {}
        old_qty = float(old.get("qty", 0.0) or 0.0)
        if old_qty > 0.0:
            old_price = float(old.get("price", avg_price))
            total_qty = old_qty + qty
            new_avg = (old_price * old_qty + avg_price * qty) / total_qty if total_qty > 0 else avg_price
            entry_time = float(old.get("time", curr_time) or curr_time)
            max_price = max(float(old.get("max_price", new_avg) or new_avg), new_avg)
            ts_active = bool(old.get("ts_active", False))
            self.force_set_position(symbol, new_avg, total_qty, entry_time, max_price, ts_active)
        else:
            self.force_set_position(symbol, avg_price, qty, curr_time, avg_price, False)

    async def buy(self, symbol: str, price: float, amount: float) -> bool:
        """시장가 매수.

        주문 접수 이후 구간(체결 조회~로컬 포지션 등록~이력 기록)은 호출 태스크가 cancel 되어도
        끝까지 수행된다(고아 포지션 방지). 주문 접수 전에 취소되면 주문 자체를 내지 않는다.
        """
        progress = {"submitted": False}
        inner = asyncio.ensure_future(self._buy_impl(symbol, price, amount, progress))
        try:
            return await asyncio.shield(inner)
        except asyncio.CancelledError:
            if not progress["submitted"]:
                inner.cancel()
            else:
                logging.warning(f"[RealTrader] {symbol} 매수 대기 태스크가 취소되었으나 주문이 이미 접수됨: 포지션 등록까지 완료합니다.")
            try:
                await asyncio.wait({inner})
            except asyncio.CancelledError:
                pass
            raise

    async def _buy_impl(self, symbol: str, price: float, amount: float, progress: dict) -> bool:
        logging.info(f"[RealTrader] Initiating Real Buy for {symbol} | Budget: {amount:,.0f} KRW")

        progress["submitted"] = True  # 이 시점부터(POST 전송 중 포함) 취소로부터 보호
        uuid = await self.buy_market(symbol, amount)
        if not uuid:
            logging.error(f"[RealTrader] Real Buy Failed for {symbol}. Skip local state update.")
            return False

        details = await self.fetch_order_details(uuid)
        avg_price = details["avg_price"]
        executed_volume = details["executed_volume"]

        if avg_price <= 0.0:
            logging.warning(f"[RealTrader] Could not resolve exact execution price. Falling back to trigger price: {price:,.0f}")
            avg_price = price

        if executed_volume <= 0.0 and avg_price > 0.0:
            # 체결 확인 불가: 주문은 접수되었으므로 (고아 포지션 방지) 수수료를 차감한 추정 수량으로 등록한다.
            # (지정 금액 amount 에는 수수료가 포함되어 있다)
            executed_volume = amount * (1.0 - self.fee_rate) / avg_price
            logging.warning(f"[RealTrader] {symbol} 체결 수량 확인 불가(state={details.get('state')}): 수수료 반영 추정 수량 {executed_volume:.8f} 로 등록")

        from core.simulator import _read_current_version
        curr_time = time.time()
        version = _read_current_version()

        # 실제 체결 후 업비트 원화(KRW) 잔고 조회 (가상 1천만 원 배제)
        upbit_krw = await self.get_upbit_krw_balance()

        # 로컬 C++ 포지션 등록 (트레일링/익절/손절 추적용) - 기존 보유분이 있으면 평단/수량 합산
        self._merge_position(symbol, avg_price, executed_volume, curr_time)

        # 실전 매수 체결 이벤트 생성
        fee = details.get("paid_fee") or (avg_price * executed_volume * self.fee_rate)
        evt = {
            "version": version,
            "type": "BUY",
            "symbol": symbol,
            "price": avg_price,
            "qty": executed_volume,
            "fee": fee,
            "amount": amount,
            "time": curr_time,
            "balance": upbit_krw,
            "is_real": True,
            "mode": "REAL",
            "exchange": "UPBIT"
        }
        self.add_history_event(evt)
        self._save_history()

        logging.info(f"[RealTrader] BUY {symbol} | Price: {avg_price:,.0f} | Qty: {executed_volume:.8f} | Cost: {amount:,.0f} | Real KRW: {upbit_krw:,.0f}")

        # 텔레그램 실시간 알림 (실전 모드 명시)
        try:
            from utils.telegram_bot import get_bot
            if bot := get_bot():
                bot.notify_buy(symbol, avg_price, executed_volume, amount, extra=evt, is_real=True)
        except Exception as e:
            logging.warning(f"[RealTrader] Telegram notify_buy exception: {e}")

        return True

    async def sell(self, symbol: str, price: float, reason: str = "SIGNAL"):
        """시장가 매도.

        반환 계약:
          - False               : 매도 미체결/실패/보류 (로컬 포지션 유지)
          - float (일반)        : 전량 청산 완료, 값 = 실현 순손익(원)
          - PartialSellResult   : 일부만 체결(잔여 포지션 유지). float 값 = 체결분 실현 순손익, `.partial` True
          - DustSyncResult      : 실제 잔고가 0/Dust 여서 로컬 포지션만 정리 (손익 미확정), `.dust_sync` True
        """
        logging.info(f"[RealTrader] Initiating Real Sell for {symbol} | Reason: {reason}")

        from core.simulator import _read_current_version
        curr_time = time.time()
        version = _read_current_version()

        local_pos = self.positions.get(symbol, {})
        local_qty = float(local_pos.get("qty", 0.0))
        entry_p = float(local_pos.get("price", price))
        entry_t = float(local_pos.get("time", curr_time))

        if local_qty <= 0.0:
            logging.warning(f"[RealTrader] No local position found for {symbol}. Skip order.")
            return False

        upbit_qty = await self.get_upbit_balance(symbol)
        if upbit_qty is None:
            # 잔고 조회 실패는 '잔고 없음'이 아니다: 포지션을 지우지 않고 다음 손절/매도 시도에서 재시도
            logging.error(f"[RealTrader] 업비트 잔고 조회 실패로 {symbol} 매도를 보류합니다 (로컬 포지션 유지, 재시도 대기).")
            return False
        is_dust_or_zero = (upbit_qty <= 1e-6) or (price > 0 and (upbit_qty * price) < 5000.0)
        if is_dust_or_zero:
            logging.warning(f"[RealTrader] 🛑 실제 업비트 잔고가 0 또는 Dust({upbit_qty:.8f}, 평가액 < 5,000원)입니다 ({symbol}). 로컬 허수 포지션을 자동 청산(Sync)합니다.")
            import ctrade_core
            ctrade_core.VirtualTrader.sell(self, symbol, price, f"SYNC_DUST_{reason}", curr_time, version)
            return DustSyncResult()

        volume = min(local_qty, upbit_qty)
        uuid = await self.sell_market(symbol, volume)
        if not uuid:
            logging.error(f"[RealTrader] Real Sell Failed for {symbol}. Skip local state update.")
            return False

        details = await self.fetch_order_details(uuid)
        avg_price = details["avg_price"]
        executed_vol = details.get("executed_volume", 0.0)

        if executed_vol <= 0.0:
            # 체결 확인 불가(타임아웃 등): 전량 체결로 가정하지 않고 실제 잔고 변화로 판단한다.
            post_qty = await self.get_upbit_balance(symbol)
            if post_qty is None:
                logging.error(f"[RealTrader] {symbol} 매도 체결 여부 확인 불가(주문 조회/잔고 조회 모두 실패). 로컬 포지션을 유지하고 다음 시도에서 잔고 기준으로 재검증합니다.")
                return False
            executed_vol = max(0.0, upbit_qty - post_qty)
            if executed_vol <= 0.0:
                logging.warning(f"[RealTrader] {symbol} 매도 주문 접수 후 잔고 변화 없음(미체결): 로컬 포지션 유지.")
                return False
            logging.warning(f"[RealTrader] {symbol} 주문 상세 조회 실패: 잔고 감소분 {executed_vol:.8f} 을 체결 수량으로 사용")

        if avg_price <= 0.0:
            logging.warning(f"[RealTrader] Could not resolve exact execution sell price. Falling back to trigger price: {price:,.0f}")
            avg_price = price

        # Dust 및 최소 주문금액(5,000원) 미만 잔여분 처리: 잔여가 5,000원 미만이거나 0.1% 이내 오차면 전량 청산 확정
        remaining_qty = max(0.0, volume - executed_vol)
        remaining_val = remaining_qty * avg_price
        is_full_exit = (remaining_qty < 1e-6) or (remaining_val < 5000.0) or (executed_vol >= volume * 0.999)

        if not is_full_exit:
            # 진정한 부분 체결인 경우에만 잔여 포지션 보존
            logging.warning(f"[RealTrader] ⚠️ 유의미한 부분 매도 체결: {symbol} (매도: {executed_vol:.8f}, 잔여: {remaining_qty:.8f}, 잔여액: {remaining_val:,.0f}원)")
            self.force_set_position(symbol, entry_p, remaining_qty, entry_t, entry_p, False)
            part_profit, _, _ = self._calc_profit(entry_p, avg_price, executed_vol)
            return PartialSellResult(part_profit, remaining_qty)

        # 전량 청산 완료 처리 -> C++ 포지션 삭제
        import ctrade_core
        ctrade_core.VirtualTrader.sell(self, symbol, avg_price, reason, curr_time, version)

        # 손익 계산 (순수익 기준)
        profit, profit_rate, fee = self._calc_profit(entry_p, avg_price, executed_vol)
        sell_amt = avg_price * executed_vol

        # 실제 업비트 원화 잔고 조회
        upbit_krw = await self.get_upbit_krw_balance()

        evt = {
            "version": version,
            "type": "SELL",
            "symbol": symbol,
            "price": avg_price,
            "qty": executed_vol,
            "fee": fee,
            "amount": sell_amt,
            "time": curr_time,
            "balance": upbit_krw,
            "buy_price": entry_p,
            "sell_price": avg_price,
            "profit": profit,
            "profit_rate": profit_rate,
            "reason": reason,
            "holding_time": curr_time - entry_t,
            "entry_time": entry_t,
            "exit_time": curr_time,
            "is_real": True,
            "mode": "REAL",
            "exchange": "UPBIT"
        }
        self.add_history_event(evt)
        self._save_history()

        logging.info(f"[RealTrader] SELL {symbol} | Price: {avg_price:,.0f} | Profit: {profit:+,.0f} ({profit_rate:+.2f}%) | Reason: {reason} | Real KRW: {upbit_krw:,.0f}")

        # 텔레그램 실시간 알림 (실전 모드 명시)
        try:
            from utils.telegram_bot import get_bot
            if bot := get_bot():
                bot.notify_sell(symbol, avg_price, profit, profit_rate, reason, extra=evt, is_real=True)
        except Exception as e:
            logging.warning(f"[RealTrader] Telegram notify_sell exception: {e}")

        return profit

    def _calc_profit(self, entry_p: float, sell_p: float, qty: float):
        invest_amt = entry_p * qty
        sell_amt = sell_p * qty
        fee = (invest_amt + sell_amt) * self.fee_rate
        profit = (sell_amt - invest_amt) - fee
        rate = (profit / invest_amt * 100.0) if invest_amt > 0 else 0.0
        return profit, rate, fee
