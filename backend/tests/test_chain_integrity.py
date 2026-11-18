"""事实-事件链完整性：全局互斥两策略的端到端行为。"""
from __future__ import annotations

from app.db import connect
from app.repositories import facts as fact_repo
from app.repositories import chain as chain_repo
from app.services import chain_service

from conftest import make_redeem, set_policy, get_fact, break_reasons, error_payload


# ---------- 工具：制造四种断链 ----------

def inject_dangling_event_id(conn, fact_id: int) -> None:
    """悬空 event_id：事实指向不存在的事件（事件湖从未有过该 id）。"""
    row = conn.execute("SELECT MAX(id) m FROM coupon_events").fetchone()
    ghost = (row["m"] or 0) + 999
    conn.execute("UPDATE redeem_facts SET event_id=? WHERE id=?", (ghost, fact_id))
    conn.commit()


def inject_event_deleted(conn, fact_id: int) -> int:
    """事件缺失：事件从事件湖被删除，事实 event_id 悬空。"""
    eid = get_fact(conn, fact_id)["event_id"]
    conn.execute("DELETE FROM coupon_events WHERE id=?", (eid,))
    conn.commit()
    return eid


def inject_amount_drift(conn, fact_id: int, amount: float = 999.99) -> None:
    conn.execute("UPDATE redeem_facts SET amount=? WHERE id=?", (amount, fact_id))
    conn.commit()


def inject_business_day_drift(conn, fact_id: int, day: str = "2000-01-01") -> None:
    conn.execute("UPDATE redeem_facts SET business_day=? WHERE id=?", (day, fact_id))
    conn.commit()


# ---------- 基线：干净链两策略都能钉 ----------

def test_clean_chain_seals_under_both_policies(client):
    for policy in ("reject", "materialize"):
        # 每天重建：用不同业务日与券码避免唯一键冲突
        day = f"2026-11-{(10 if policy == 'reject' else 11):02d}"
        client.put("/api/settings/day", json={"business_day": day})
        make_redeem(client, f"C-BASE-{policy}", amount=10, day=day)
        set_policy(client, policy)
        r = client.post("/api/redeem/seal", json={})
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["status"] == "sealed"
        assert data["policy"] == policy
        # 批确实落 sealed
        batches = client.get("/api/batches").json()
        assert any(b["id"] == data["batch_id"] and b["status"] == "sealed" for b in batches)


# ---------- 拒钉策略 ----------

def test_reject_dangling_event_id(client):
    set_policy(client, "reject")
    day = "2026-12-01"
    client.put("/api/settings/day", json={"business_day": day})
    f = make_redeem(client, "C-DAN-1", day=day)
    conn = connect()
    try:
        inject_dangling_event_id(conn, f["fact_id"])
    finally:
        conn.close()

    r = client.post("/api/redeem/seal", json={})
    assert r.status_code == 422
    payload = error_payload(r)
    assert break_reasons(r) == ["event_missing"]
    assert payload["rejected"] is True
    assert payload["break_count"] == 1
    b = payload["breaks"][0]
    assert b["fact_id"] == f["fact_id"]
    assert b["event_snapshot"] is None
    # 不写批、不锁日
    assert client.get("/api/batches").json() == []
    assert client.get("/api/redeem/preview").json()["already_sealed"] is False
    # 清单可在接口复查
    review = client.get("/api/chain/breaks").json()
    assert review["break_count"] >= 1
    assert any(x["reason"] == "event_missing" and x["fact_id"] == f["fact_id"] for x in review["breaks"])
    run = client.get(f"/api/chain/runs/{payload['run_id']}").json()
    assert run["run"]["outcome"] == "rejected_hard_block"
    # 事实保持 open 未被改动
    conn = connect()
    try:
        assert get_fact(conn, f["fact_id"])["status"] == "open"
    finally:
        conn.close()


def test_reject_event_deleted(client):
    set_policy(client, "reject")
    day = "2026-12-02"
    client.put("/api/settings/day", json={"business_day": day})
    f = make_redeem(client, "C-DEL-1", day=day)
    conn = connect()
    try:
        eid = inject_event_deleted(conn, f["fact_id"])
    finally:
        conn.close()

    r = client.post("/api/redeem/seal", json={})
    assert r.status_code == 422
    assert break_reasons(r) == ["event_missing"]
    assert error_payload(r)["breaks"][0]["event_id"] == eid


def test_reject_amount_drift(client):
    set_policy(client, "reject")
    day = "2026-12-03"
    client.put("/api/settings/day", json={"business_day": day})
    f = make_redeem(client, "C-AMT-1", amount=10, day=day)
    conn = connect()
    try:
        inject_amount_drift(conn, f["fact_id"], 88.88)
    finally:
        conn.close()

    r = client.post("/api/redeem/seal", json={})
    assert r.status_code == 422
    payload = error_payload(r)
    assert break_reasons(r) == ["amount_mismatch"]
    assert payload["run_id"] > 0
    # 无批次、无锁日、事实金额未被偷偷改写
    assert client.get("/api/batches").json() == []
    conn = connect()
    try:
        row = get_fact(conn, f["fact_id"])
        assert row["amount"] == 88.88 and row["status"] == "open"
    finally:
        conn.close()


def test_reject_business_day_drift(client):
    set_policy(client, "reject")
    day = "2026-12-04"
    client.put("/api/settings/day", json={"business_day": day})
    f = make_redeem(client, "C-DAY-1", day=day)
    conn = connect()
    try:
        inject_business_day_drift(conn, f["fact_id"], "1999-09-09")
    finally:
        conn.close()

    r = client.post("/api/redeem/seal", json={})
    assert r.status_code == 422
    assert break_reasons(r) == ["business_day_mismatch"]


def test_reject_multiple_breaks_listed_all(client):
    """一批多条断链，清单必须列全，且不落 sealed。"""
    set_policy(client, "reject")
    day = "2026-12-05"
    client.put("/api/settings/day", json={"business_day": day})
    f1 = make_redeem(client, "C-MUL-1", day=day)
    f2 = make_redeem(client, "C-MUL-2", day=day)
    f3 = make_redeem(client, "C-MUL-3", day=day)
    conn = connect()
    try:
        inject_amount_drift(conn, f1["fact_id"], 50)
        inject_business_day_drift(conn, f2["fact_id"], "1999-01-01")
        inject_dangling_event_id(conn, f3["fact_id"])
    finally:
        conn.close()

    r = client.post("/api/redeem/seal", json={})
    assert r.status_code == 422
    reasons = break_reasons(r)
    assert reasons == ["amount_mismatch", "business_day_mismatch", "event_missing"]
    assert client.get("/api/batches").json() == []


# ---------- 物化策略 ----------

def test_materialize_missing_event_still_rejected(client):
    set_policy(client, "materialize")
    day = "2026-12-10"
    client.put("/api/settings/day", json={"business_day": day})
    f = make_redeem(client, "C-MAT-MISS", day=day)
    conn = connect()
    try:
        inject_dangling_event_id(conn, f["fact_id"])
    finally:
        conn.close()

    r = client.post("/api/redeem/seal", json={})
    assert r.status_code == 422
    payload = error_payload(r)
    assert break_reasons(r) == ["event_missing"]
    assert client.get("/api/batches").json() == []
    # 清单留痕
    review = client.get("/api/chain/breaks").json()
    assert any(x["fact_id"] == f["fact_id"] and x["reason"] == "event_missing" for x in review["breaks"])


def test_materialize_amount_drift_rewrites_and_audits(client):
    set_policy(client, "materialize")
    day = "2026-12-11"
    client.put("/api/settings/day", json={"business_day": day})
    f = make_redeem(client, "C-MAT-AMT", amount=10, day=day)
    conn = connect()
    try:
        inject_amount_drift(conn, f["fact_id"], 77.0)
    finally:
        conn.close()

    r = client.post("/api/redeem/seal", json={})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["materialized_fact_count"] == 1
    assert data["total_amount"] == 10.0  # 按事件金额重算
    rw = data["rewrites"][0]
    assert rw["amount_old"] == 77.0 and rw["amount_new"] == 10.0
    assert data["breaks"][0]["reason"] == "amount_mismatch"
    assert data["breaks"][0]["resolved"] is True

    # 事实已按事件重写并进 sealed 批
    conn = connect()
    try:
        row = get_fact(conn, f["fact_id"])
        assert row["amount"] == 10.0 and row["status"] == "sealed"
        assert row["batch_id"] == data["batch_id"]
        audits = chain_repo.rewrites_for_run(conn, data["run_id"])
        assert len(audits) == 1 and audits[0]["amount_old"] == 77.0
        breaks = chain_repo.breaks_for_run(conn, data["run_id"])
        assert all(b["resolved"] == 1 for b in breaks)
    finally:
        conn.close()

    # 批明细可复查到事实与审计
    detail = client.get(f"/api/batches/{data['batch_id']}").json()
    assert detail["batch"]["check_run_id"] == data["run_id"]
    assert {x["id"] for x in detail["facts"]} == {f["fact_id"]}
    assert len(detail["rewrites"]) == 1


def test_materialize_business_day_drift_moves_fact(client):
    set_policy(client, "materialize")
    day = "2026-12-12"
    client.put("/api/settings/day", json={"business_day": day})
    # 一条干净事实保证本日仍有可钉内容
    clean = make_redeem(client, "C-MAT-DAY-OK", amount=10, day=day)
    drift = make_redeem(client, "C-MAT-DAY-BAD", amount=10, day=day)
    conn = connect()
    try:
        inject_business_day_drift(conn, drift["fact_id"], "2000-01-01")
        event_day = fact_repo.get_event(conn, get_fact(conn, drift["fact_id"])["event_id"])["business_day"]
        assert event_day == day
    finally:
        conn.close()

    r = client.post("/api/redeem/seal", json={})
    assert r.status_code == 200, r.text
    data = r.json()
    # 漂移事实被改回事件业务日（=本日），故仍在批内，共 2 笔
    assert data["fact_count"] == 2
    conn = connect()
    try:
        row = get_fact(conn, drift["fact_id"])
        assert row["business_day"] == day and row["status"] == "sealed"
        rw = chain_repo.rewrites_for_run(conn, data["run_id"])[0]
        assert rw["business_day_old"] == "2000-01-01"
        assert rw["business_day_new"] == day
    finally:
        conn.close()


def test_materialize_rewrite_failure_rolls_back_everything(client, monkeypatch):
    """重写失败：整笔回滚——无批、无 sealed、无审计、事实复原、清单留档。"""
    set_policy(client, "materialize")
    day = "2026-12-13"
    client.put("/api/settings/day", json={"business_day": day})
    f = make_redeem(client, "C-MAT-FAIL", amount=10, day=day)
    conn = connect()
    try:
        inject_amount_drift(conn, f["fact_id"], 42.0)
    finally:
        conn.close()

    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(fact_repo, "rewrite_fact", boom)

    r = client.post("/api/redeem/seal", json={})
    assert r.status_code == 500
    assert "整笔回滚" in error_payload(r)["message"]
    conn = connect()
    try:
        row = get_fact(conn, f["fact_id"])
        assert row["amount"] == 42.0 and row["status"] == "open"
        assert conn.execute("SELECT COUNT(*) c FROM redeem_batches").fetchone()["c"] == 0
        assert conn.execute("SELECT COUNT(*) c FROM fact_rewrite_audit").fetchone()["c"] == 0
    finally:
        conn.close()


def test_materialize_recheck_failure_rolls_back_rewrites(client, monkeypatch):
    """重写后重检仍断链：回滚重写，不写批，清单记录 recheck 失败。"""
    set_policy(client, "materialize")
    day = "2026-12-14"
    client.put("/api/settings/day", json={"business_day": day})
    f = make_redeem(client, "C-MAT-RECHECK", amount=10, day=day)
    conn = connect()
    try:
        inject_amount_drift(conn, f["fact_id"], 42.0)
    finally:
        conn.close()

    real_eval = chain_service.evaluate_facts
    calls = {"n": 0}

    def flaky(conn_, facts):
        calls["n"] += 1
        res = real_eval(conn_, facts)
        if calls["n"] == 2:  # 重写后的重检
            res.append({
                "fact_id": f["fact_id"], "event_id": None,
                "coupon_code": "C-MAT-RECHECK", "reason": "amount_mismatch",
                "reason_label": "x", "detail": "forced recheck failure",
                "blocking": False, "fact_snapshot": {}, "event_snapshot": {"amount": 10},
            })
        return res

    monkeypatch.setattr(chain_service, "evaluate_facts", flaky)
    r = client.post("/api/redeem/seal", json={})
    assert r.status_code == 422
    assert "重检" in error_payload(r)["message"]
    conn = connect()
    try:
        # 重写已回滚
        assert get_fact(conn, f["fact_id"])["amount"] == 42.0
        assert conn.execute("SELECT COUNT(*) c FROM redeem_batches").fetchone()["c"] == 0
        run = conn.execute(
            "SELECT * FROM chain_check_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert run["outcome"] == "rejected_recheck_failed"
    finally:
        conn.close()


# ---------- 互斥与预览 ----------

def test_policy_is_global_mutex(client):
    r = client.put("/api/settings/chain-policy", json={"policy": "nonsense"})
    assert r.status_code == 400
    r = client.put("/api/settings/chain-policy", json={"policy": "materialize"})
    assert r.status_code == 200
    assert r.json()["policy"] == "materialize"
    # 全局生效：预览读到的就是该策略
    p = client.get("/api/redeem/preview").json()
    assert p["policy"] == "materialize"
    r = client.put("/api/settings/chain-policy", json={"policy": "reject"})
    assert r.json()["policy"] == "reject"
    assert client.get("/api/settings/chain-policy").json()["choices"] == [
        "reject", "materialize"
    ]


def test_preview_reports_breaks_without_persisting(client):
    set_policy(client, "reject")
    day = "2026-12-20"
    client.put("/api/settings/day", json={"business_day": day})
    f = make_redeem(client, "C-PREV-1", day=day)
    conn = connect()
    try:
        inject_amount_drift(conn, f["fact_id"], 66.0)
    finally:
        conn.close()

    p = client.get("/api/redeem/preview").json()
    assert p["chain_ok"] is False
    assert p["break_count"] == 1
    assert p["blocking_count"] == 1  # reject 下金额漂移阻断
    assert p["breaks"][0]["reason"] == "amount_mismatch"
    # 只读预览不产生检查运行/清单
    assert client.get("/api/chain/runs").json() == []

    set_policy(client, "materialize")
    p2 = client.get("/api/redeem/preview").json()
    assert p2["blocking_count"] == 0  # 同一条漂移在物化策略下不阻断
    assert p2["break_count"] == 1


def test_event_type_mismatch_blocks_both_policies(client):
    for policy in ("reject", "materialize"):
        day = f"2026-12-2{1 if policy == 'reject' else 2}"
        client.put("/api/settings/day", json={"business_day": day})
        f = make_redeem(client, f"C-TYPE-{policy}", day=day)
        conn = connect()
        try:
            conn.execute(
                "UPDATE coupon_events SET event_type='void' WHERE id=?",
                (get_fact(conn, f["fact_id"])["event_id"],),
            )
            conn.commit()
        finally:
            conn.close()
        set_policy(client, policy)
        r = client.post("/api/redeem/seal", json={})
        assert r.status_code == 422
        assert break_reasons(r) == ["event_type_mismatch"]
        assert client.get("/api/batches").json() == []


def test_sealed_day_blocks_new_redeem_events(client):
    """锁日后事件湖禁止再写核销（既有防线不回退）。"""
    set_policy(client, "reject")
    day = "2026-12-23"
    client.put("/api/settings/day", json={"business_day": day})
    make_redeem(client, "C-LOCK-1", day=day)
    assert client.post("/api/redeem/seal", json={}).status_code == 200
    r = client.post(
        "/api/events",
        json={"event_type": "redeem", "coupon_code": "C-LOCK-2",
              "campaign_code": "NEW10", "amount": 10, "business_day": day},
    )
    assert r.status_code == 409
