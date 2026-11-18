import os
import tempfile

# 必须在导入 app.* 之前指定独立临时库
_TMP = tempfile.mkdtemp(prefix="vouchpin-test-")
os.environ["DATA_DIR"] = _TMP

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app import seed  # noqa: E402
from app.db import connect  # noqa: E402

TABLES = [
    "fact_rewrite_audit",
    "chain_breaks",
    "chain_check_runs",
    "redeem_batches",
    "redeem_facts",
    "coupon_events",
]


@pytest.fixture()
def client():
    conn = connect()
    try:
        for t in TABLES:
            conn.execute(f"DELETE FROM {t}")
        conn.execute("DELETE FROM settings WHERE key='chain_integrity_policy'")
        conn.commit()
    finally:
        conn.close()
    seed.seed_if_empty()
    with TestClient(app) as c:
        yield c


def make_redeem(client, code: str, camp: str = "NEW10", amount: float = 10.0, day: str | None = None):
    body = {"event_type": "redeem", "coupon_code": code, "campaign_code": camp, "amount": amount}
    if day:
        body["business_day"] = day
    r = client.post("/api/events", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def set_policy(client, policy: str):
    r = client.put("/api/settings/chain-policy", json={"policy": policy})
    assert r.status_code == 200, r.text
    return r.json()


def get_fact(conn, fact_id: int) -> dict:
    row = conn.execute("SELECT * FROM redeem_facts WHERE id=?", (fact_id,)).fetchone()
    return dict(row)


def error_payload(resp) -> dict:
    """FastAPI 对 dict 型 detail 直接平铺返回，兼容两种形状。"""
    body = resp.json()
    detail = body.get("detail", body)
    return detail if isinstance(detail, dict) else {"message": detail}


def break_reasons(resp) -> list[str]:
    return sorted(b["reason"] for b in error_payload(resp)["breaks"])
