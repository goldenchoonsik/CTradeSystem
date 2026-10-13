"""
계좌 유형별 매매차익 과세 규칙 (Tax Rules)
=========================================
세금은 종목만으로 정해지지 않는다 — (계좌 유형 × 종목 세금 분류)로 정해진다.
같은 금 ETF 라도 일반계좌에서는 매매차익에 배당소득세 15.4%, ISA·연금계좌에서는 다른 규칙이다.

- 종목의 세금 분류(tax_class)는 종목 마스터 원장(symbols_registry.json)이 정답이다.
- 계좌 유형(account_type)은 계좌 원장(accounts.json, shared/config/account_registry.py)이 정답이다.
- 매도세(증권거래세)는 여기서 다루지 않는다 — 거래 비용이므로 trade_costs.round_trip_cost 가 맡는다.

분류 (2026-10-10 기준 국내 세법, 보수적 근사):
  KR_EQUITY_ETF : 국내주식형 ETF(예: KODEX 200). 매매차익 비과세.
  KR_OTHER_ETF  : 그 밖의 국내 상장 ETF(레버리지·인버스·금·채권·해외지수). 매매차익을 배당소득으로 15.4% 과세.
                  실제 과표는 min(매매차익, 과표기준가 증가분)이지만 과표기준가 이력이 없으므로 매매차익 전액에 과세한다(보수적).
                  손실은 다른 이익과 상계되지 않는다.
  KR_STOCK      : 국내 상장 주식. 매매차익 비과세(대주주 제외).

분배금 과세는 아직 모델링하지 않는다(가격 원천이 분배금 반영 수정주가라 총수익으로만 반영됨).
ISA·연금계좌 규칙은 아직 구현하지 않는다 — 조용히 0 을 돌려주면 세후 비교가 거짓이 되므로 오류를 낸다.
"""

TAX_CLASSES = ("KR_EQUITY_ETF", "KR_OTHER_ETF", "KR_STOCK")
ACCOUNT_TYPES = ("GENERAL", "ISA", "PENSION")

# 일반계좌 매매차익 세율 (배당소득세 14% + 지방소득세 1.4%). 법이 바뀌면 여기 한 곳만 고친다.
_GENERAL_GAIN_TAX = {
    "KR_EQUITY_ETF": 0.0,
    "KR_OTHER_ETF": 0.154,
    "KR_STOCK": 0.0,
}


class TaxRuleNotImplemented(NotImplementedError):
    """아직 규칙이 없는 계좌 유형 — 세후 비교를 조용히 틀리게 만들지 않기 위해 명시적으로 실패한다."""


def gain_tax_rate(account_type: str, tax_class: str) -> float:
    """실현 이익에 붙는 세율. 분류가 없거나 모르는 값이면 ValueError."""
    if tax_class not in TAX_CLASSES:
        raise ValueError(f"tax_class 가 없거나 알 수 없는 값입니다: {tax_class!r} — 종목 원장에 tax_class 를 기입하세요 {TAX_CLASSES}")
    if account_type not in ACCOUNT_TYPES:
        raise ValueError(f"알 수 없는 계좌 유형: {account_type!r} {ACCOUNT_TYPES}")
    if account_type != "GENERAL":
        raise TaxRuleNotImplemented(f"{account_type} 계좌의 과세 규칙은 아직 구현되지 않았습니다")
    return _GENERAL_GAIN_TAX[tax_class]


def realized_gain_tax(account_type: str, tax_class: str, realized_gain: float) -> float:
    """한 번의 매도로 실현된 손익에 대한 세금(원). 손실이면 0 (상계 없음)."""
    rate = gain_tax_rate(account_type, tax_class)
    return max(float(realized_gain), 0.0) * rate
