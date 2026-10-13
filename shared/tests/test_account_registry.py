"""계좌 원장 (2026-10-10, 일 단위 배분 엔진의 운영 단위)"""
import json
import os

import pytest

from shared.config import account_registry as ar

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REAL_ACCOUNTS_PATH = os.path.join(REPO_ROOT, "shared", "config", "accounts.json")


def _good():
    return {"KIS_MAIN": {"name": "x", "broker": "KIS", "account_type": "GENERAL", "execution": "auto",
                         "credentials_env": {"acc_no": "KIS_ACC_NO", "acc_code": "KIS_ACC_CODE"}}}


def test_real_ledger_is_valid():
    with open(REAL_ACCOUNTS_PATH, "r", encoding="utf-8") as f:
        assert ar.validate_accounts(json.load(f)) == []


def test_tests_read_an_isolated_copy():
    assert os.path.realpath(ar.ACCOUNTS_PATH) != os.path.realpath(REAL_ACCOUNTS_PATH)


def test_get_account():
    a = ar.get_account("kis_main")
    assert a["account_id"] == "KIS_MAIN"
    assert a["broker"] == "KIS" and a["account_type"] == "GENERAL" and a["execution"] == "auto"


def test_unknown_account():
    with pytest.raises(ar.UnknownAccountError):
        ar.get_account("NOPE")


@pytest.mark.parametrize("field, bad", [
    ("broker", "MERITZ"),
    ("account_type", "IRP"),
    ("execution", "semi"),
])
def test_rejects_bad_enum_fields(field, bad):
    acc = _good()
    acc["KIS_MAIN"][field] = bad
    assert any(field in p for p in ar.validate_accounts(acc))


def test_rejects_secret_values_written_in_place_of_env_names():
    """계좌번호를 원장에 직접 쓰면 저장소에 비밀값이 커밋된다."""
    acc = _good()
    acc["KIS_MAIN"]["credentials_env"]["acc_no"] = "12345678"
    assert any("환경변수" in p for p in ar.validate_accounts(acc))


def test_auto_account_requires_credentials():
    acc = _good()
    del acc["KIS_MAIN"]["credentials_env"]
    assert ar.validate_accounts(acc)
    acc["KIS_MAIN"]["execution"] = "manual"
    assert ar.validate_accounts(acc) == []
