"""事实-事件链完整性校验。

全局互斥策略由 settings.chain_integrity_policy 决定，只允许两个取值：
- reject     拒钉：任一 open 事实断链则整批拒钉，出断链清单，不写批不锁日
- materialize 以事件为准重物化后钉：金额/业务日漂移在钉事务内按事件重写（审计旧值），
  事件缺失或类型不符仍拒钉；重写失败整笔回滚。
"""
from __future__ import annotations
import sqlite3
from typing import Any

from app.repositories import settings as settings_repo
from app.repositories import facts as fact_repo
from app.repositories import chain as chain_repo

POLICY_REJECT = "reject"
POLICY_MATERIALIZE = "materialize"
POLICY_CHOICES = (POLICY_REJECT, POLICY_MATERIALIZE)
SETTING_KEY = "chain_integrity_policy"

# reason -> 中文原因
REASON_LABELS = {
    "event_missing": "事件缺失（event_id 悬空，事件湖中无该事件）",
    "event_type_mismatch": "事件类型不是 redeem",
    "amount_mismatch": "事实金额与事件金额不一致",
    "business_day_mismatch": "事实业务日与事件业务日不一致",
}

# 物化策略下仍不可修复、必须拒钉的原因
HARD_BLOCK_REASONS = ("event_missing", "event_type_mismatch")


def get_policy(conn: sqlite3.Connection) -> str:
    p = settings_repo.get(conn, SETTING_KEY, POLICY_REJECT)
    return p if p in POLICY_CHOICES else POLICY_REJECT


def set_policy(conn: sqlite3.Connection, policy: str) -> str:
    if policy not in POLICY_CHOICES:
        raise ValueError(f"chain_integrity_policy 须为 {POLICY_CHOICES} 之一")
    settings_repo.set_(conn, SETTING_KEY, policy)
    return policy


def _amounts_equal(a: float, b: float) -> bool:
    return round(float(a), 2) == round(float(b), 2)


def evaluate_facts(
    conn: sqlite3.Connection, facts: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """纯读校验：每条事实对照事件湖，收集全部断链原因。"""
    breaks: list[dict[str, Any]] = []
    for f in facts:
        event = fact_repo.get_event(conn, int(f["event_id"]))
        fact_snap = {
            "id": f["id"],
            "coupon_code": f["coupon_code"],
            "campaign_code": f["campaign_code"],
            "amount": f["amount"],
            "business_day": f["business_day"],
            "event_id": f["event_id"],
        }
        if event is None:
            breaks.append(
                {
                    "fact_id": f["id"],
                    "event_id": f["event_id"],
                    "coupon_code": f["coupon_code"],
                    "reason": "event_missing",
                    "reason_label": REASON_LABELS["event_missing"],
                    "detail": f"事实 #{f['id']} 指向事件 #{f['event_id']}，但事件湖查无此事件",
                    "blocking": True,
                    "fact_snapshot": fact_snap,
                    "event_snapshot": None,
                }
            )
            continue
        event_snap = {
            "id": event["id"],
            "event_type": event["event_type"],
            "coupon_code": event["coupon_code"],
            "campaign_code": event["campaign_code"],
            "amount": event["amount"],
            "business_day": event["business_day"],
        }
        if event["event_type"] != "redeem":
            breaks.append(
                {
                    "fact_id": f["id"],
                    "event_id": event["id"],
                    "coupon_code": f["coupon_code"],
                    "reason": "event_type_mismatch",
                    "reason_label": REASON_LABELS["event_type_mismatch"],
                    "detail": (
                        f"事实 #{f['id']} 关联事件 #{event['id']} 类型为 "
                        f"{event['event_type']}，须为 redeem"
                    ),
                    "blocking": True,
                    "fact_snapshot": fact_snap,
                    "event_snapshot": event_snap,
                }
            )
        if not _amounts_equal(f["amount"], event["amount"]):
            breaks.append(
                {
                    "fact_id": f["id"],
                    "event_id": event["id"],
                    "coupon_code": f["coupon_code"],
                    "reason": "amount_mismatch",
                    "reason_label": REASON_LABELS["amount_mismatch"],
                    "detail": (
                        f"事实金额 {f['amount']} ≠ 事件 #{event['id']} 金额 {event['amount']}"
                    ),
                    "blocking": False,
                    "fact_snapshot": fact_snap,
                    "event_snapshot": event_snap,
                }
            )
        if f["business_day"] != event["business_day"]:
            breaks.append(
                {
                    "fact_id": f["id"],
                    "event_id": event["id"],
                    "coupon_code": f["coupon_code"],
                    "reason": "business_day_mismatch",
                    "reason_label": REASON_LABELS["business_day_mismatch"],
                    "detail": (
                        f"事实业务日 {f['business_day']} ≠ 事件 #{event['id']} "
                        f"业务日 {event['business_day']}"
                    ),
                    "blocking": False,
                    "fact_snapshot": fact_snap,
                    "event_snapshot": event_snap,
                }
            )
    return breaks


def is_blocking(b: dict[str, Any], policy: str) -> bool:
    """该断链在当前策略下是否阻断钉扎。

    reject 策略：任何断链都阻断；
    materialize 策略：仅事件缺失/类型不符阻断，金额、业务日可按事件物化修复。
    """
    if b["reason"] in HARD_BLOCK_REASONS:
        return True
    return policy == POLICY_REJECT


def blocking_breaks(
    breaks: list[dict[str, Any]], policy: str
) -> list[dict[str, Any]]:
    return [b for b in breaks if is_blocking(b, policy)]


def _public_break(b: dict[str, Any], resolved: bool = False) -> dict[str, Any]:
    """对外/落库统一形状。"""
    return {
        "fact_id": b["fact_id"],
        "event_id": b["event_id"],
        "coupon_code": b["coupon_code"],
        "reason": b["reason"],
        "reason_label": b["reason_label"],
        "detail": b["detail"],
        "blocking": bool(b.get("blocking")),
        "resolved": bool(resolved),
        "fact_snapshot": b.get("fact_snapshot"),
        "event_snapshot": b.get("event_snapshot"),
    }


def record_run(
    conn: sqlite3.Connection,
    *,
    business_day: str,
    policy: str,
    outcome: str,
    fact_count: int,
    breaks: list[dict[str, Any]],
    rewrites: list[dict[str, Any]] | None = None,
    message: str = "",
    batch_id: int | None = None,
) -> int:
    """落一次检查运行 + 断链清单 + 重写审计，返回 run_id。"""
    run_id = chain_repo.create_run(
        conn,
        business_day=business_day,
        policy=policy,
        outcome=outcome,
        fact_count=fact_count,
        break_count=len(breaks),
        rewrite_count=len(rewrites or []),
        message=message,
        batch_id=batch_id,
    )
    for b in breaks:
        chain_repo.insert_break(
            conn,
            run_id=run_id,
            fact_id=b["fact_id"],
            event_id=b["event_id"],
            coupon_code=b.get("coupon_code") or "",
            reason=b["reason"],
            detail=b.get("detail") or "",
            fact_snapshot=b.get("fact_snapshot") or {},
            event_snapshot=b.get("event_snapshot") or {},
            resolved=bool(b.get("resolved")),
        )
    for rw in rewrites or []:
        chain_repo.insert_rewrite(conn, run_id=run_id, **rw)
    return run_id


def apply_materialization(
    conn: sqlite3.Connection, breaks: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """在钉事务内按事件重写可修复事实（金额/业务日），逐条写审计。

    返回审计行；事件缺失/类型不符不在此处处理（上层应已硬拒）。
    重写抛错时由上层整体回滚。
    """
    rewrites: list[dict[str, Any]] = []
    # 同一事实可能同时金额+业务日漂移，按 fact_id 合并为一次重写
    per_fact: dict[int, dict[str, Any]] = {}
    for b in breaks:
        if b["reason"] in HARD_BLOCK_REASONS:
            continue
        event = b.get("event_snapshot")
        entry = per_fact.setdefault(
            b["fact_id"],
            {"event_id": b["event_id"], "amount": None, "business_day": None},
        )
        if b["reason"] == "amount_mismatch":
            entry["amount"] = float(event["amount"])
        elif b["reason"] == "business_day_mismatch":
            entry["business_day"] = event["business_day"]

    for fact_id, change in per_fact.items():
        fact = fact_repo.get_fact(conn, fact_id)
        if fact is None:
            continue
        new_amount = change["amount"] if change["amount"] is not None else float(fact["amount"])
        new_day = change["business_day"] or fact["business_day"]
        new_event_id = int(change["event_id"])
        fact_repo.rewrite_fact(
            conn,
            fact_id,
            amount=new_amount,
            business_day=new_day,
            event_id=new_event_id,
        )
        rewrites.append(
            {
                "fact_id": fact_id,
                "event_id": new_event_id,
                "amount_old": float(fact["amount"]),
                "amount_new": round(new_amount, 2),
                "business_day_old": fact["business_day"],
                "business_day_new": new_day,
                "event_id_old": int(fact["event_id"]),
                "event_id_new": new_event_id,
            }
        )
    return rewrites
