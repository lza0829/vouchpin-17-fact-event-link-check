"""真实 HTTP 端到端冒烟：四种断链 × 两策略。"""
import json
import sqlite3
import sys

import httpx

BASE = "http://127.0.0.1:10900/api"
DB = "/tmp/vp-live/clipbond.db"
c = httpx.Client(base_url=BASE, timeout=10)


def sql(fn):
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    try:
        return fn(conn)
    finally:
        conn.commit()
        conn.close()


def add_redeem(code, day, amount=10.0):
    r = c.post("/events", json={
        "event_type": "redeem", "coupon_code": code,
        "campaign_code": "NEW10", "amount": amount, "business_day": day,
    })
    assert r.status_code == 200, r.text
    return r.json()


def set_day(day):
    assert c.put("/settings/day", json={"business_day": day}).status_code == 200


def set_policy(p):
    r = c.put("/settings/chain-policy", json={"policy": p})
    assert r.status_code == 200, r.text
    print(f"  policy -> {r.json()['policy']}")


def seal(expect):
    r = c.post("/redeem/seal", json={})
    print(f"  seal -> {r.status_code}")
    assert r.status_code == expect, r.text
    return r.json() if r.status_code == 200 else r.json()["detail"]


def corrupt(kind, fact_id):
    def op(conn):
        f = conn.execute("SELECT * FROM redeem_facts WHERE id=?", (fact_id,)).fetchone()
        if kind == "dangling":
            ghost = conn.execute("SELECT MAX(id) m FROM coupon_events").fetchone()["m"] + 900
            conn.execute("UPDATE redeem_facts SET event_id=? WHERE id=?", (ghost, fact_id))
        elif kind == "missing":
            conn.execute("DELETE FROM coupon_events WHERE id=?", (f["event_id"],))
        elif kind == "amount":
            conn.execute("UPDATE redeem_facts SET amount=55.5 WHERE id=?", (fact_id,))
        elif kind == "day":
            conn.execute("UPDATE redeem_facts SET business_day='1988-08-08' WHERE id=?", (fact_id,))
    sql(op)


results = []

# ---------- 拒钉策略：四种断链全部拒钉、无 sealed、有清单 ----------
print("== 策略 reject ==")
set_policy("reject")
for i, kind in enumerate(["dangling", "missing", "amount", "day"], start=1):
    day = f"2026-10-0{i}"
    set_day(day)
    f = add_redeem(f"C-RJ-{i}", day)
    corrupt(kind, f["fact_id"])
    payload = seal(422)
    reasons = sorted(b["reason"] for b in payload["breaks"])
    results.append(("reject", kind, reasons, payload["run_id"]))
    print(f"    {kind:9s} -> {reasons} run={payload['run_id']}")

# 拒钉后绝无 sealed 批次，且本次 4 条测试事实仍 open
n_batch = sql(lambda x: x.execute("SELECT COUNT(*) c FROM redeem_batches").fetchone()["c"])
n_open_test = sql(lambda x: x.execute(
    "SELECT COUNT(*) c FROM redeem_facts WHERE status='open' AND coupon_code LIKE 'C-RJ-%'"
).fetchone()["c"])
print(f"  reject 后批次数={n_batch}（应为0） 测试open事实={n_open_test}（应为4）")
assert n_batch == 0 and n_open_test == 4

# 清单接口复查
br = c.get("/chain/breaks").json()
runs = c.get("/chain/runs").json()
print(f"  断链清单 {br['break_count']} 条；检查运行 {len(runs)} 次（均 rejected_*）")
assert br["break_count"] == 4
assert all(r["outcome"].startswith("rejected") for r in runs)
# 每次 run 都能单独复查
for r in runs:
    d = c.get(f"/chain/runs/{r['id']}").json()
    assert len(d["breaks"]) >= 1

# ---------- 物化策略：金额/业务日修复，事件缺失仍拒 ----------
print("== 策略 materialize ==")
set_policy("materialize")

# 金额漂移 -> 重写
day = "2026-11-01"
set_day(day)
f = add_redeem("C-MT-AMT", day)
corrupt("amount", f["fact_id"])
ok = seal(200)
print(f"    amount 修复: 重写 {ok['materialized_fact_count']} 条, 合计 {ok['total_amount']}")
assert ok["total_amount"] == 10.0 and ok["materialized_fact_count"] == 1
det = c.get(f"/batches/{ok['batch_id']}").json()
assert det["rewrites"][0]["amount_old"] == 55.5 and det["rewrites"][0]["amount_new"] == 10.0

# 业务日漂移 -> 重写回事件业务日
day = "2026-11-02"
set_day(day)
f = add_redeem("C-MT-DAY", day)
corrupt("day", f["fact_id"])
ok = seal(200)
print(f"    day 修复: 事实数 {ok['fact_count']}, 合计 {ok['total_amount']}")
row = sql(lambda x: x.execute("SELECT business_day,status FROM redeem_facts WHERE id=?", (f["fact_id"],)).fetchone())
assert row["business_day"] == day and row["status"] == "sealed"

# 悬空 event_id -> 物化也救不了，仍拒钉、无新批
day = "2026-11-03"
set_day(day)
f = add_redeem("C-MT-DAN", day)
corrupt("dangling", f["fact_id"])
payload = seal(422)
print(f"    dangling 仍拒: {[b['reason'] for b in payload['breaks']]}")
assert [b["reason"] for b in payload["breaks"]] == ["event_missing"]

# 事件缺失（删除）-> 仍拒钉
day = "2026-11-04"
set_day(day)
f = add_redeem("C-MT-MIS", day)
corrupt("missing", f["fact_id"])
payload = seal(422)
print(f"    missing 仍拒: {[b['reason'] for b in payload['breaks']]} run={payload['run_id']}")
assert payload["run_id"] in [r["id"] for r in c.get("/chain/runs").json()]

# 最终：sealed 批次只有 2 个（金额、业务日），拒钉的两天无批
batches = c.get("/batches").json()
print(f"  最终 sealed 批次数={len(batches)}（应为2）")
assert len(batches) == 2 and all(b["status"] == "sealed" for b in batches)

# 预览接口形状（只读不落库）
set_day("2026-11-05")
f = add_redeem("C-PV", "2026-11-05")
corrupt("amount", f["fact_id"])
pv = c.get("/redeem/preview").json()
print(f"  预览: break_count={pv['break_count']} blocking={pv['blocking_count']} policy={pv['policy']}")
assert pv["break_count"] == 1 and pv["blocking_count"] == 0  # 物化策略下不阻断

# 非法策略被拒
r = c.put("/settings/chain-policy", json={"policy": "skip_silently"})
print(f"  非法策略 -> {r.status_code}")
assert r.status_code == 400

print("\n全部冒烟断言通过 ✓")
print(json.dumps(results, ensure_ascii=False, indent=2))
