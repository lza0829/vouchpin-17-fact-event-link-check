from __future__ import annotations
from datetime import datetime
from typing import Any
from fastapi import HTTPException
from app.db import connect
from app.repositories import settings as settings_repo
from app.repositories import facts as fact_repo
from app.repositories import batches as batch_repo
from app.repositories import chain as chain_repo
from app.services import chain_service


def list_facts(business_day: str | None = None) -> list[dict[str, Any]]:
    conn = connect()
    try:
        day = business_day or settings_repo.get(conn, "business_day")
        return fact_repo.list_facts(conn, day)
    finally:
        conn.close()


def preview(business_day: str | None = None) -> dict[str, Any]:
    """只读预览：不写检查运行、不写断链清单，仅展示当前链状态。"""
    conn = connect()
    try:
        day = business_day or settings_repo.get(conn, "business_day")
        opens = fact_repo.open_facts_for_day(conn, day)
        total = round(sum(float(f["amount"]) for f in opens), 2)
        breaks = chain_service.evaluate_facts(conn, opens)
        policy = chain_service.get_policy(conn)
        blocking = chain_service.blocking_breaks(breaks, policy)
        return {
            "business_day": day,
            "policy": policy,
            "open_count": len(opens),
            "open_total": total,
            "already_sealed": fact_repo.day_is_sealed(conn, day),
            "chain_ok": not breaks,
            "break_count": len(breaks),
            "blocking_count": len(blocking),
            "breaks": [chain_service._public_break(b) for b in breaks],
            "facts": opens,
        }
    finally:
        conn.close()


def _reject_payload(
    *, message: str, breaks: list[dict[str, Any]], run_id: int, policy: str
) -> dict[str, Any]:
    return {
        "message": message,
        "run_id": run_id,
        "policy": policy,
        "rejected": True,
        "break_count": len(breaks),
        "breaks": [chain_service._public_break(b) for b in breaks],
    }


def seal(body: dict[str, Any]) -> dict[str, Any]:
    conn = connect(immediate=True)
    try:
        day = body.get("business_day") or settings_repo.get(conn, "business_day")
        policy = chain_service.get_policy(conn)
        if fact_repo.day_is_sealed(conn, day):
            raise HTTPException(409, f"业务日 {day} 已有 sealed 钉扎批")
        opens = fact_repo.open_facts_for_day(conn, day)
        if not opens:
            raise HTTPException(400, "无可钉扎的 open 核销事实")

        breaks = chain_service.evaluate_facts(conn, opens)
        hard = [b for b in breaks if b["reason"] in chain_service.HARD_BLOCK_REASONS]

        # 两种策略下，事件缺失 / 类型不符都必须拒钉（事件缺失无法物化）
        if hard:
            run_id = chain_service.record_run(
                conn,
                business_day=day,
                policy=policy,
                outcome="rejected_hard_block",
                fact_count=len(opens),
                breaks=breaks,
                message="存在事件缺失或类型不符的断链事实，拒钉",
            )
            conn.commit()
            raise HTTPException(
                422,
                _reject_payload(
                    message=(
                        f"钉扎被拒：{len(hard)} 条事实事件缺失或类型不符"
                        "（事件缺失无法物化），见断链清单"
                    ),
                    breaks=breaks,
                    run_id=run_id,
                    policy=policy,
                ),
            )

        if policy == chain_service.POLICY_REJECT and breaks:
            # 拒钉策略：任一断链都不钉，不写批、不锁日，清单落库可复查
            run_id = chain_service.record_run(
                conn,
                business_day=day,
                policy=policy,
                outcome="rejected_breaks",
                fact_count=len(opens),
                breaks=breaks,
                message="拒钉策略：存在断链事实，整批拒钉",
            )
            conn.commit()
            raise HTTPException(
                422,
                _reject_payload(
                    message=(
                        f"钉扎被拒：{len(breaks)} 条事实-事件链不完整，"
                        f"已生成断链清单（run_id={run_id}），未写批、未锁日"
                    ),
                    breaks=breaks,
                    run_id=run_id,
                    policy=policy,
                ),
            )

        rewrites: list[dict[str, Any]] = []
        materialized_breaks: list[dict[str, Any]] = []
        if policy == chain_service.POLICY_MATERIALIZE and breaks:
            # 以事件为准重物化：重写在同一钉事务的 savepoint 内。
            # 重写/重检失败 -> 回滚重写，保留断链清单后拒钉，绝不静默跳过。
            sp = conn.execute("SAVEPOINT materialize")
            try:
                rewrites = chain_service.apply_materialization(conn, breaks)
                # 重检必须读库拿重写后的新值；业务日被改写走的事实不再属于本日
                fresh = fact_repo.open_facts_for_day(conn, day)
                recheck = chain_service.evaluate_facts(conn, fresh)
                if recheck:
                    conn.execute("ROLLBACK TO SAVEPOINT materialize")
                    run_id = chain_service.record_run(
                        conn,
                        business_day=day,
                        policy=policy,
                        outcome="rejected_recheck_failed",
                        fact_count=len(opens),
                        breaks=recheck,
                        rewrites=rewrites,
                        message="物化后重检仍有断链，重写已回滚，整笔拒钉",
                    )
                    conn.execute("RELEASE SAVEPOINT materialize")
                    conn.commit()
                    raise HTTPException(
                        422,
                        _reject_payload(
                            message="物化后重检仍有断链，重写已整笔回滚，未写批、未锁日",
                            breaks=recheck,
                            run_id=run_id,
                            policy=policy,
                        ),
                    )
            except HTTPException:
                raise
            except Exception as e:
                conn.execute("ROLLBACK TO SAVEPOINT materialize")
                conn.execute("RELEASE SAVEPOINT materialize")
                conn.commit()
                raise HTTPException(500, f"物化重写失败，整笔回滚：{e}") from e
            conn.execute("RELEASE SAVEPOINT materialize")
            for b in breaks:
                b["resolved"] = True
            materialized_breaks = breaks

        # 干净 / 物化通过后，以当前事实值重算（物化可能改写业务日/金额）
        current = fact_repo.open_facts_for_day(conn, day)
        total = round(sum(float(f["amount"]) for f in current), 2)
        run_id = chain_service.record_run(
            conn,
            business_day=day,
            policy=policy,
            outcome="sealed" if not materialized_breaks else "sealed_materialized",
            fact_count=len(opens),
            breaks=materialized_breaks,
            rewrites=rewrites,
            message="钉扎成功" + ("（含按事件物化重写）" if rewrites else ""),
        )
        bid = batch_repo.create_batch(
            conn,
            business_day=day,
            status="sealed",
            fact_count=len(current),
            total_amount=total,
            sealed_at=datetime.now().replace(microsecond=0).isoformat(),
            note=body.get("note") or "",
        )
        conn.execute(
            "UPDATE redeem_batches SET check_run_id=? WHERE id=?", (run_id, bid)
        )
        chain_repo.attach_batch(conn, run_id, bid)
        fact_repo.mark_batched(conn, [int(f["id"]) for f in current], bid)
        conn.commit()
        return {
            "batch_id": bid,
            "run_id": run_id,
            "business_day": day,
            "policy": policy,
            "fact_count": len(current),
            "total_amount": total,
            "status": "sealed",
            "materialized_fact_count": len({r["fact_id"] for r in rewrites}),
            "rewrites": rewrites,
            "breaks": [
                chain_service._public_break(b, resolved=True)
                for b in materialized_breaks
            ],
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_batches() -> list[dict[str, Any]]:
    conn = connect()
    try:
        return batch_repo.list_batches(conn)
    finally:
        conn.close()


def list_chain_breaks() -> dict[str, Any]:
    """断链清单复查（全量留痕，含已被物化修复 resolved=1 的行）。"""
    conn = connect()
    try:
        breaks = chain_repo.list_breaks(conn)
        run = chain_repo.latest_run(conn)
        return {
            "latest_run": run,
            "break_count": len(breaks),
            "breaks": breaks,
        }
    finally:
        conn.close()


def list_chain_runs() -> list[dict[str, Any]]:
    conn = connect()
    try:
        return chain_repo.list_runs(conn)
    finally:
        conn.close()


def chain_run_detail(run_id: int) -> dict[str, Any]:
    conn = connect()
    try:
        row = conn.execute(
            "SELECT * FROM chain_check_runs WHERE id=?", (int(run_id),)
        ).fetchone()
        if row is None:
            raise HTTPException(404, f"检查运行 #{run_id} 不存在")
        return {
            "run": dict(row),
            "breaks": chain_repo.breaks_for_run(conn, run_id),
            "rewrites": chain_repo.rewrites_for_run(conn, run_id),
        }
    finally:
        conn.close()


def batch_detail(batch_id: int) -> dict[str, Any]:
    conn = connect()
    try:
        batch = batch_repo.get_batch(conn, batch_id)
        if batch is None:
            raise HTTPException(404, f"批次 #{batch_id} 不存在")
        facts = fact_repo.facts_for_batch(conn, batch_id)
        run = None
        run_id = batch.get("check_run_id")
        if run_id:
            row = conn.execute(
                "SELECT * FROM chain_check_runs WHERE id=?", (run_id,)
            ).fetchone()
            run = dict(row) if row else None
        return {
            "batch": batch,
            "facts": facts,
            "check_run": run,
            "rewrites": chain_repo.rewrites_for_run(conn, run_id) if run_id else [],
        }
    finally:
        conn.close()
