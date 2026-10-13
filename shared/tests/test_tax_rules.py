"""계좌 유형 × 종목 세금 분류 과세 규칙 + 원장의 tax_class/leverage 필드 (2026-10-10, 일 단위 배분 엔진)"""
import pytest

from shared.config import config_manager as cm
from shared.core import tax_rules as tr


def test_general_account_rates():
    assert tr.gain_tax_rate("GENERAL", "KR_EQUITY_ETF") == 0.0
    assert tr.gain_tax_rate("GENERAL", "KR_OTHER_ETF") == pytest.approx(0.154)
    assert tr.gain_tax_rate("GENERAL", "KR_STOCK") == 0.0


def test_losses_are_not_taxed_or_credited():
    assert tr.realized_gain_tax("GENERAL", "KR_OTHER_ETF", 1_000_000) == pytest.approx(154_000)
    assert tr.realized_gain_tax("GENERAL", "KR_OTHER_ETF", -500_000) == 0.0


def test_missing_tax_class_fails_loudly():
    """분류가 없는 종목을 조용히 비과세로 취급하면 세후 비교가 거짓이 된다."""
    with pytest.raises(ValueError):
        tr.gain_tax_rate("GENERAL", None)
    with pytest.raises(ValueError):
        tr.gain_tax_rate("GENERAL", "KR_ETF")


@pytest.mark.parametrize("account_type", ["ISA", "PENSION"])
def test_unimplemented_account_types_fail_loudly(account_type):
    with pytest.raises(tr.TaxRuleNotImplemented):
        tr.gain_tax_rate(account_type, "KR_OTHER_ETF")


def test_ledger_tax_fields_for_k200_instruments():
    assert cm.get_symbol_info("069500")["tax_class"] == "KR_EQUITY_ETF"
    assert cm.get_symbol_info("069500")["leverage"] == 1
    assert cm.get_symbol_info("122630")["tax_class"] == "KR_OTHER_ETF"
    assert cm.get_symbol_info("122630")["leverage"] == 2


@pytest.mark.parametrize("field, bad", [
    ("tax_class", "TAX_FREE"),
    ("leverage", 0),
    ("leverage", "2"),
    ("leverage", True),
])
def test_ledger_rejects_bad_tax_fields(field, bad):
    reg = {"069500": {"symbol": "069500", "name": "KODEX 200", "market_type": "KRX_ETF", field: bad}}
    assert any(field in p for p in cm.validate_symbols_registry(reg))


def test_ledger_accepts_inverse_leverage():
    reg = {"114800": {"symbol": "114800", "name": "KODEX 인버스", "market_type": "KRX_ETF",
                      "tax_class": "KR_OTHER_ETF", "leverage": -1}}
    assert cm.validate_symbols_registry(reg) == []
