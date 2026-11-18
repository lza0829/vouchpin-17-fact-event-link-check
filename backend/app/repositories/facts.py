from __future__ import annotations
import sqlite3
from typing import Any


def list_facts(conn: sqlite3.Connection, business_day: str | None = None) -> list[dict[str, Any]]:
    if business_day:
        rows = conn.execute(
            "SELECT * FROM redeem_facts WHERE business_day=? ORDER BY id",
            (business_day,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM redeem_facts ORDER BY id DESC LIMIT 200").fetchall()
    return [dict(r) for r in rows]


def insert_fact(conn: sqlite3.Connection, **fields: Any) -> int:
    cur = conn.execute(
        "INSERT INTO redeem_facts(coupon_code, campaign_code, amount, business_day, event_id, status) VALUES(?,?,?,?,?,?)",
        (
            fields["coupon_code"],
            fields["campaign_code"],
            float(fields["amount"]),
            fields["business_day"],
            int(fields["event_id"]),
            fields.get("status") or "open",
        ),
    )
    return int(cur.lastrowid)


def open_facts_for_day(conn: sqlite3.Connection, business_day: str) -> list[dict[str, Any]]:
    """钉扎/预览扫描集。

    除事实本身 business_day 命中的 open 事实外，还纳入链接事件属于本日、
    但事实业务日已漂移的 open 事实，使「业务日不一致」能在钉前被检出
    （事件缺失的事实仍可经由事实自身业务日入选）。
    """
    rows = conn.execute(
        """
        SELECT f.*
        FROM redeem_facts f
        LEFT JOIN coupon_events e ON e.id = f.event_id
        WHERE f.status = 'open'
          AND (f.business_day = ? OR e.business_day = ?)
        ORDER BY f.id
        """,
        (business_day, business_day),
    ).fetchall()
    return [dict(r) for r in rows]


def mark_batched(conn: sqlite3.Connection, fact_ids: list[int], batch_id: int) -> None:
    for fid in fact_ids:
        conn.execute(
            "UPDATE redeem_facts SET status='sealed', batch_id=? WHERE id=?",
            (batch_id, fid),
        )


def day_is_sealed(conn: sqlite3.Connection, business_day: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM redeem_batches WHERE business_day=? AND status='sealed' LIMIT 1",
        (business_day,),
    ).fetchone()
    return row is not None


def get_event(conn: sqlite3.Connection, event_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM coupon_events WHERE id=?",
        (int(event_id),),
    ).fetchone()
    return dict(row) if row else None


def get_fact(conn: sqlite3.Connection, fact_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM redeem_facts WHERE id=?", (int(fact_id),)).fetchone()
    return dict(row) if row else None


def rewrite_fact(
    conn: sqlite3.Connection,
    fact_id: int,
    *,
    amount: float | None = None,
    business_day: str | None = None,
    event_id: int | None = None,
) -> None:
    """按事件重写事实金额/业务日/链接；仅更新给出的列。"""
    sets: list[str] = []
    params: list[Any] = []
    if amount is not None:
        sets.append("amount=?")
        params.append(float(amount))
    if business_day is not None:
        sets.append("business_day=?")
        params.append(business_day)
    if event_id is not None:
        sets.append("event_id=?")
        params.append(int(event_id))
    if not sets:
        return
    params.append(int(fact_id))
    conn.execute(f"UPDATE redeem_facts SET {', '.join(sets)} WHERE id=?", params)


def facts_for_batch(conn: sqlite3.Connection, batch_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM redeem_facts WHERE batch_id=? ORDER BY id",
        (int(batch_id),),
    ).fetchall()
    return [dict(r) for r in rows]
