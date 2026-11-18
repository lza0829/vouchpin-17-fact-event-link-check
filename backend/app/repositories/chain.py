from __future__ import annotations
import json
import sqlite3
from typing import Any


def create_run(conn: sqlite3.Connection, **fields: Any) -> int:
    cur = conn.execute(
        """
        INSERT INTO chain_check_runs(
            business_day, policy, outcome, fact_count, break_count,
            rewrite_count, message, batch_id
        ) VALUES(?,?,?,?,?,?,?,?)
        """,
        (
            fields["business_day"],
            fields["policy"],
            fields["outcome"],
            int(fields.get("fact_count") or 0),
            int(fields.get("break_count") or 0),
            int(fields.get("rewrite_count") or 0),
            fields.get("message") or "",
            fields.get("batch_id"),
        ),
    )
    return int(cur.lastrowid)


def attach_batch(conn: sqlite3.Connection, run_id: int, batch_id: int) -> None:
    conn.execute(
        "UPDATE chain_check_runs SET batch_id=? WHERE id=?",
        (batch_id, run_id),
    )


def insert_break(conn: sqlite3.Connection, **fields: Any) -> int:
    cur = conn.execute(
        """
        INSERT INTO chain_breaks(
            run_id, fact_id, event_id, coupon_code, reason, detail,
            fact_snapshot, event_snapshot, resolved
        ) VALUES(?,?,?,?,?,?,?,?,?)
        """,
        (
            int(fields["run_id"]),
            int(fields["fact_id"]),
            fields.get("event_id"),
            fields.get("coupon_code") or "",
            fields["reason"],
            fields.get("detail") or "",
            json.dumps(fields.get("fact_snapshot") or {}, ensure_ascii=False),
            json.dumps(fields.get("event_snapshot") or {}, ensure_ascii=False),
            1 if fields.get("resolved") else 0,
        ),
    )
    return int(cur.lastrowid)


def insert_rewrite(conn: sqlite3.Connection, **fields: Any) -> int:
    cur = conn.execute(
        """
        INSERT INTO fact_rewrite_audit(
            run_id, fact_id, event_id,
            amount_old, amount_new, business_day_old, business_day_new,
            event_id_old, event_id_new
        ) VALUES(?,?,?,?,?,?,?,?,?)
        """,
        (
            int(fields["run_id"]),
            int(fields["fact_id"]),
            int(fields["event_id"]),
            fields.get("amount_old"),
            fields.get("amount_new"),
            fields.get("business_day_old"),
            fields.get("business_day_new"),
            fields.get("event_id_old"),
            fields.get("event_id_new"),
        ),
    )
    return int(cur.lastrowid)


def latest_run(conn: sqlite3.Connection) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM chain_check_runs ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def list_runs(conn: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM chain_check_runs ORDER BY id DESC LIMIT ?",
        (int(limit),),
    ).fetchall()
    return [dict(r) for r in rows]


def breaks_for_run(conn: sqlite3.Connection, run_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM chain_breaks WHERE run_id=? ORDER BY id",
        (int(run_id),),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        d["fact_snapshot"] = json.loads(d["fact_snapshot"] or "{}")
        d["event_snapshot"] = json.loads(d["event_snapshot"] or "{}")
        out.append(d)
    return out


def list_breaks(conn: sqlite3.Connection, limit: int = 200) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT b.*, r.policy AS run_policy, r.outcome AS run_outcome,
               r.business_day AS run_business_day
        FROM chain_breaks b
        JOIN chain_check_runs r ON r.id = b.run_id
        ORDER BY b.id DESC LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        d["fact_snapshot"] = json.loads(d["fact_snapshot"] or "{}")
        d["event_snapshot"] = json.loads(d["event_snapshot"] or "{}")
        out.append(d)
    return out


def rewrites_for_run(conn: sqlite3.Connection, run_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM fact_rewrite_audit WHERE run_id=? ORDER BY id",
        (int(run_id),),
    ).fetchall()
    return [dict(r) for r in rows]
