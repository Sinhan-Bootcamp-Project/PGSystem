"""결제·금융 코어 시나리오: FEP 경유 충전, 장애 주입, 보상, 일 마감 대사.

test_scenarios.py의 인증·호출 도우미를 재사용한다. 기관과 FEP는 시험 컨테이너가 붙은 fep/mock 네트워크로 직접 호출한다.
"""

import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from test_scenarios import (  # noqa: F401  (auth는 pytest fixture)
    BASE,
    CLIENT,
    USER,
    auth,
    call,
    db_connection,
    fault,
    finish,
    headers,
    payment,
    proposal,
)

FEP = "http://fep-service:8080"
SEOUL = timezone(timedelta(hours=9))


def business_date():
    now = datetime.now(SEOUL)
    day = now.date()
    if (now.hour, now.minute) >= (23, 30):
        day += timedelta(days=1)
    return day.isoformat()


def fep_fault(auth, institution, mode, message_type=None, count=1, delay_ms=0):
    r = CLIENT.post(
        FEP + "/fep/v1/test/faults",
        headers=headers(auth["admin"]),
        json={
            "institution": institution,
            "messageType": message_type,
            "mode": mode,
            "delayMs": delay_ms,
            "count": count,
        },
    )
    assert r.status_code == 200, r.text


def clear_fep_faults(auth):
    CLIENT.delete(FEP + "/fep/v1/test/faults", headers=headers(auth["admin"]))


def fep_messages(auth, transaction_id):
    r = CLIENT.get(
        FEP + "/fep/v1/messages",
        params={"transactionId": transaction_id},
        headers=headers(auth["admin"]),
    )
    assert r.status_code == 200, r.text
    return r.json()


def topup(auth, amount, key=None):
    return CLIENT.post(
        BASE + "/v1/paymoney/charges",
        headers={**headers(auth["user"]), "Idempotency-Key": key or str(uuid.uuid4())},
        json={"amountKrw": amount},
    )


def balance(auth):
    r = CLIENT.get(BASE + "/v1/paymoney/balance", headers=headers(auth["user"]))
    assert r.status_code == 200, r.text
    return r.json()["balanceKrw"]


def bank_balance(account):
    with db_connection(
        "host=postgres dbname=food user=inst_bank_app password=inst_bank_dev_only options=-csearch_path=inst_bank"
    ) as db:
        return db.execute(
            "SELECT balance_krw FROM accounts WHERE account_no=%s", (account,)
        ).fetchone()[0]


USER_ACCOUNT = "088-0001-000001"
TRUST_ACCOUNT = "088-9000-000001"


@pytest.fixture(autouse=True)
def reset_faults(auth):
    clear_fep_faults(auth)
    yield
    clear_fep_faults(auth)


def test_topup_moves_money_through_kftc_and_is_idempotent(auth):
    before, user_before, trust_before = balance(auth), bank_balance(USER_ACCOUNT), bank_balance(TRUST_ACCOUNT)
    key = str(uuid.uuid4())
    first = topup(auth, 10000, key)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "COMPLETED"
    again = topup(auth, 10000, key)
    assert again.json()["id"] == first.json()["id"]
    assert topup(auth, 20000, key).status_code == 409
    assert balance(auth) == before + 10000
    assert bank_balance(USER_ACCOUNT) == user_before - 10000
    assert bank_balance(TRUST_ACCOUNT) == trust_before + 10000
    log = fep_messages(auth, first.json()["id"])
    assert [m["direction"] for m in log] == ["REQUEST", "RESPONSE"]
    assert log[1]["response_code"] == "0000"


def test_topup_response_loss_is_resolved_by_inquiry_without_resend(auth):
    before = balance(auth)
    fep_fault(auth, "KFT", "RESPONSE_LOST", "TRANSFER")
    r = topup(auth, 5000)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "COMPLETED"
    assert balance(auth) == before + 5000
    types = [m["message_type"] for m in fep_messages(auth, r.json()["id"]) if m["direction"] == "REQUEST"]
    assert types == ["TRANSFER", "TRANSACTION_INQUIRY"]


def test_credit_leg_failure_is_compensated(auth):
    before, user_before = balance(auth), bank_balance(USER_ACCOUNT)
    r = CLIENT.post(
        "http://institution-simulator:8080/institutions/v1/test/faults/bank_deposit_fail",
        headers=headers(auth["admin"]),
        json={"count": 1},
    )
    assert r.status_code == 200, r.text
    r = topup(auth, 7000)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "FAILED"
    assert balance(auth) == before
    assert bank_balance(USER_ACCOUNT) == user_before
    with db_connection(
        "host=postgres dbname=food user=inst_kftc_app password=inst_kftc_dev_only options=-csearch_path=inst_kftc"
    ) as db:
        row = db.execute(
            "SELECT status,debit_status,credit_status,compensation_status FROM transfers WHERE transaction_id=%s",
            (r.json()["id"],),
        ).fetchone()
    assert row == ("COMPENSATED", "OK", "FAILED", "OK")


def test_fep_rejects_callers_without_fep_permission(auth):
    r = CLIENT.post(
        FEP + "/fep/v1/messages",
        headers=headers(auth["worker"]),
        json={"header": {"messageType": "NET_SETTLEMENT", "transactionId": "x", "institutionCode": "BOK"}, "body": {}},
    )
    assert r.status_code == 403  # order-worker에는 fep:send 권한이 없다


def test_card_response_loss_is_confirmed_by_inquiry(auth):
    state = proposal(auth)
    method = state["proposal"]["quote"]["paymentMethod"]
    if not method.startswith("CARD"):
        pytest.skip("선택된 주문안이 카드 결제가 아니다: " + method)
    fep_fault(auth, "CRD", "RESPONSE_LOST", "CARD_APPROVAL")
    assert finish(auth, state)["status"] == "COMPLETED"
    p = state["proposal"]
    assert payment(auth, p)["status"] == "PAID"
    types = [m["message_type"] for m in fep_messages(auth, p["executionId"]) if m["direction"] == "REQUEST"]
    assert types.count("CARD_APPROVAL") == 1 and "TRANSACTION_INQUIRY" in types


def test_zz_no_response_becomes_unknown_and_day_close_settles_it(auth):
    """마지막에 실행: 당일 영업일을 마감한다(시험 환경은 ALLOW_EARLY_CLOSE=true)."""
    fep_fault(auth, "KFT", "NO_RESPONSE", count=2)  # 이체 + 상태 조회 모두 무응답
    r = topup(auth, 3000)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "UNKNOWN"
    clear_fep_faults(auth)
    report = call(
        auth, "payment-service", f"/internal/v1/settlements/{business_date()}/run", {}, actor="admin"
    )
    assert report.status_code == 200, report.text
    body = report.json()
    assert body["run"]["status"] == "COMPLETED", body
    steps = [s["step"] for s in body["steps"]]
    assert steps[:3] == ["CARD_CLOSE", "KFTC_CLOSE", "BOK_CHECK"]
    resolved = [i for i in body["items"] if i["transaction_id"] == r.json()["id"]]
    assert resolved and resolved[0]["kind"] == "RESOLVE_UNKNOWN"
    status = CLIENT.get(BASE + "/v1/paymoney/charges/" + r.json()["id"], headers=headers(auth["user"])).json()
    assert status["status"] == "FAILED"
    mismatches = [i for i in body["items"] if i["kind"] != "RESOLVE_UNKNOWN"]
    assert not [i for i in mismatches if i["step"] in ("TRUST_CHECK", "LEDGER_CHECK")], mismatches
