import httpx
import logging
from utils.upbit_auth import UpbitAuth
from utils.retry import retry_async

class PortfolioManager:
    def __init__(self, kis_auth, upbit_auth, shared_state=None):
        self.kis_auth = kis_auth
        self.upbit_auth = upbit_auth
        self.shared_state = shared_state

    async def fetch_upbit_balance(self):
        """업비트 잔고 조회 (KRW 및 보유 코인) — ConnectTimeout 시 최대 3회 재시도.
        조회 실패(HTTP 오류/타임아웃/인증 누락)는 None, 성공 시 계좌 리스트(보유 없음이면 [])를 반환한다."""
        if not self.upbit_auth:
            logging.warning("[PortfolioManager] UpbitAuth is missing. Skip fetch_upbit_balance.")
            return None
        
        async def _do_fetch():
            url = "https://api.upbit.com/v1/accounts"
            headers = self.upbit_auth.get_headers()
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    if self.shared_state and self.shared_state.control.get("upbit_ip_error"):
                        self.shared_state.update_control({"upbit_ip_error": False})
                    return resp.json()
                else:
                    logging.error(f"[PortfolioManager] Upbit balance response HTTP {resp.status_code}: {resp.text}")
                    if resp.status_code == 401 and "no_authorization_ip" in resp.text:
                        if self.shared_state and not self.shared_state.control.get("upbit_ip_error"):
                            self.shared_state.update_control({"upbit_ip_error": True})
                            import time
                            logs = self.shared_state.data.get('__logs__', [])
                            timestamp = time.strftime('%H:%M:%S')
                            logs.append({
                                "time": timestamp,
                                "type": "SYSTEM",
                                "version": "v1.3.0",
                                "message": "🚨 [API 에러] 업비트 IP 미인증 오류 (401)! API Key에 현재 호스트의 공인 IP를 등록하셔야 잔고 조회가 가능합니다."
                            })
                            self.shared_state.data['__logs__'] = logs[-100:]
                    return None
        
        result = await retry_async(
            _do_fetch,
            max_retries=3,
            base_delay=1.0,
            exceptions=(httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError, OSError),
            logger_name="PortfolioManager/fetch_upbit_balance",
        )
        return result

    async def fetch_upbit_tickers(self, markets: list) -> dict:
        """업비트 마켓 리스트의 현재가 일괄 조회 (REST)"""
        if not markets:
            return {}
        try:
            markets_str = ",".join(markets)
            url = f"https://api.upbit.com/v1/ticker?markets={markets_str}"
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    prices = {}
                    for item in data:
                        m = item.get("market")
                        p = float(item.get("trade_price", 0.0))
                        if m and p > 0:
                            prices[m] = p
                    return prices
        except Exception as e:
            logging.debug(f"[PortfolioManager] fetch_upbit_tickers error: {e}")
        return {}

    async def fetch_kis_balance(self):
        """KIS 국내/해외 주식 잔고 및 예수금 안전 조회"""
        if not self.kis_auth or not getattr(self.kis_auth, 'url_base', '') or not self.kis_auth.acc_no:
            return {"krw_deposit": 0.0, "usd_deposit": 0.0, "tot_asset": 0.0, "stocks": [], "overseas_stocks": []}
            
        res = {
            "krw_deposit": 0.0,
            "usd_deposit": 0.0,
            "tot_asset": 0.0,
            "stocks": [],
            "overseas_stocks": [],
            # 조회 성공 여부 (실패와 '보유 없음'을 구분해 잔고 대조 오청산을 방지)
            "stocks_ok": False,
            "overseas_ok": False
        }

        # 1. 국내주식 잔고 및 원화 예수금 조회
        try:
            await self.kis_auth.get_access_token()
            url_dom = f"{self.kis_auth.url_base}/uapi/domestic-stock/v1/trading/inquire-balance"
            tr_id_dom = "TTTC8434R" if not "openapivts" in self.kis_auth.url_base else "VTTC8434R"
            headers_dom = self.kis_auth.get_base_headers()
            headers_dom.update({"tr_id": tr_id_dom})
            params_dom = {
                "CANO": self.kis_auth.acc_no[:8],
                "ACNT_PRDT_CD": self.kis_auth.acc_code,
                "AFHR_FLPR_YN": "N",
                "ODR_MTHD": "00",
                "CASH_TP": "1",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": ""
            }
            
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url_dom, headers=headers_dom, params=params_dom)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("rt_cd") == "0":
                        res["stocks"] = data.get('output1', [])
                        res["stocks_ok"] = True
                        output2 = data.get('output2', [])
                        if output2 and len(output2) > 0:
                            summary = output2[0]
                            res["krw_deposit"] = float(summary.get("dnca_tot_amt", 0.0) or summary.get("prvs_rcdl_excc_amt", 0.0))
                            res["tot_asset"] = float(summary.get("tot_evlu_amt", 0.0) or res["krw_deposit"])
        except Exception as e:
            logging.debug(f"[PortfolioManager] KIS domestic balance fetch exception: {e}")

        # 2. 해외주식 잔고(TTTS3012R) 및 외화 예수금(TTTS3007R) 안전 조회 (격리 try-except)
        try:
            url_ovrs = f"{self.kis_auth.url_base}/uapi/overseas-stock/v1/trading/inquire-balance"
            tr_id_ovrs = "TTTS3012R" if not "openapivts" in self.kis_auth.url_base else "VTTS3012R"
            headers_ovrs = self.kis_auth.get_base_headers()
            headers_ovrs.update({"tr_id": tr_id_ovrs})
            params_ovrs = {
                "CANO": self.kis_auth.acc_no[:8],
                "ACNT_PRDT_CD": self.kis_auth.acc_code,
                "OVRS_EXCG_CD": "NASD",
                "TR_CRCY_CD": "USD",
                "CTX_AREA_FK200": "",
                "CTX_AREA_NK200": ""
            }
            async with httpx.AsyncClient(timeout=8.0) as client:
                # 2-1) 보유 해외주식 목록 조회
                resp_o = await client.get(url_ovrs, headers=headers_ovrs, params=params_ovrs)
                if resp_o.status_code == 200:
                    data_o = resp_o.json()
                    if data_o.get("rt_cd") == "0":
                        res["overseas_stocks"] = data_o.get("output1", [])
                        res["overseas_ok"] = True

                # 2-2) 외화 예수금 및 실시간 환율 조회 (inquire-psamount)
                url_ps = f"{self.kis_auth.url_base}/uapi/overseas-stock/v1/trading/inquire-psamount"
                tr_id_ps = "TTTS3007R" if not "openapivts" in self.kis_auth.url_base else "VTTS3007R"
                headers_ps = self.kis_auth.get_base_headers()
                headers_ps.update({"tr_id": tr_id_ps})
                params_ps = {
                    "CANO": self.kis_auth.acc_no[:8],
                    "ACNT_PRDT_CD": self.kis_auth.acc_code,
                    "OVRS_EXCG_CD": "NASD",
                    "OVRS_ORD_UNPR": "10.0",
                    "ITEM_CD": "AAPL"
                }
                resp_ps = await client.get(url_ps, headers=headers_ps, params=params_ps)
                if resp_ps.status_code == 200:
                    data_ps = resp_ps.json()
                    if data_ps.get("rt_cd") == "0":
                        out_ps = data_ps.get("output", {})
                        res["usd_deposit"] = float(out_ps.get("ord_psbl_frcr_amt", 0.0) or out_ps.get("ovrs_ord_psbl_amt", 0.0) or 0.0)
                        if out_ps.get("exrt"):
                            res["usd_krw_fx"] = float(out_ps.get("exrt", 0.0))
        except Exception as oe:
            logging.debug(f"[PortfolioManager] KIS overseas balance/deposit fetch exception (optional): {oe}")

        return res