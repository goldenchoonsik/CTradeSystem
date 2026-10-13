# ✅ CTradeSystem 수정 결과 리포트 — L-1 ~ L-6 (Low Priority)

| 항목 | 값 |
| :--- | :--- |
| **리포트 종류** | 수정 결과(Verification Result) 및 크로스체크 기록 |
| **기준 커밋(수정 전)** | `c9fba6a` (2026-09-10) |
| **수정 일자** | 2026-09-10 |
| **수정 범위** | 낮음(Low) L-1 ~ L-6 |
| **수정 담당** | Cline |
| **연관 문서** | `CODE_REVIEW_20260910.md`, `CODE_REVIEW_20260910_checklist.csv`, `FIX_RESULT_20260910_M2_M3.md` |

---

## 1. 수정 요약

| ID | 제목 | 상태 |
| :--- | :--- | :---: |
| L-1 | portfolio_tracker 로컬TZ → KST 명시 | ✅ |
| L-2 | `_is_futures_symbol` 부분문자열 과매칭 제거 | ✅ |
| L-3 | `get_stats` 총자산 청산수수료 반영 | ✅ |
| L-4 | KISTrader 실현손익 왕복수수료 반영 | ✅ |
| L-5 | auto_analyser UTC 변환 정확화 | ✅ |
| L-6 | engine 경계 트리거 → 경과시간 기반 | ✅ |

---

## 2. 변경 내역

### L-1 — KST 명시
`CTrade/utils/portfolio_tracker.py`
```python
from datetime import datetime, timezone, timedelta
KST = timezone(timedelta(hours=9))
# datetime.now()  →  datetime.now(KST)  (6곳)
```

### L-2 — 선물 심볼 정확 토큰 매칭
`CTrade/core/tick_router.py`
```python
_FUTURES_EXACT_TOKENS = {'NQ','ES','YM','RTY','VX','NAS','TQQQ','QQQ','SPY','SP500','SPX','S&P','S&P500','KOSPI','KOSPI200','US10Y','WTI','GOLD','USDKRW','NVDA','ZN','TN'}
_FUTURES_SUFFIX_TOKENS = ('=F','FUT')
# 6자리 코드 제외 → 101/105 접두(KOSPI 선물) → 정확 토큰/접미사만 허용
```
- `candle_aggregator.on_futures_tick` 의 KOSPI 조건도 `startswith('101'/'105')` 로 동기화(7자리 `101V000` 지원)

### L-3 — C++ 총자산 수수료 반영
`CTrade/core/cpp/ctrade_core.cpp` (`get_stats`)
```cpp
// L-3: 청산 수수료를 차감한 순 평가액으로 총자산 산출 (unrealized_pnl 과 일관)
pos_market_value += revenue;   // qty*price*(1-fee_rate)
```

### L-4 — KISTrader 순손익
`CTrade/core/kis_trader.py`
```python
@staticmethod
def calc_realized_profit(buy_price, sell_price, qty, fee_rate):
    invest, sell_amt = buy_price*qty, sell_price*qty
    fee = (invest + sell_amt) * fee_rate
    profit = (sell_amt - invest) - fee
    return profit, (profit/invest*100.0 if invest>0 else 0.0), fee
```
- `__init__(..., fee_rate=0.0005)` 추가, buy/sell 이벤트 `fee` 필드 반영

### L-5 — UTC 변환
`CTrade/core/auto_analyser.py`
```python
record_epoch = calendar.timegm(struct_time)   # 로컬 TZ/DST 무관
```

### L-6 — 경과시간 기반 트리거
`CTrade/core/engine.py`
```python
if now - getattr(self, '_last_health_ts', 0.0) >= 5.0: ...
if now - getattr(self, '_last_optimizer_ts', 0.0) >= 10.0: ...
if now - getattr(self, '_last_symbol_log_ts', 0.0) >= 30.0: ...
```
