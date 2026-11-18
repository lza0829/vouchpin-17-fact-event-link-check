from __future__ import annotations
from typing import Any
from fastapi import HTTPException
from app.db import connect
from app.repositories import settings as settings_repo
from app.services import chain_service


def get_day() -> dict[str, Any]:
    conn = connect()
    try:
        return {"business_day": settings_repo.get(conn, "business_day")}
    finally:
        conn.close()


def set_day(body: dict[str, Any]) -> dict[str, Any]:
    day = (body.get("business_day") or "").strip()
    if len(day) != 10:
        raise HTTPException(400, "business_day 格式 YYYY-MM-DD")
    conn = connect(immediate=True)
    try:
        settings_repo.set_(conn, "business_day", day)
        conn.commit()
        return {"business_day": day}
    finally:
        conn.close()


def get_chain_policy() -> dict[str, Any]:
    conn = connect()
    try:
        p = chain_service.get_policy(conn)
        return {
            "key": chain_service.SETTING_KEY,
            "policy": p,
            "choices": list(chain_service.POLICY_CHOICES),
            "labels": {
                chain_service.POLICY_REJECT: "拒钉——钉前校验，断链即拒并出清单",
                chain_service.POLICY_MATERIALIZE: "以事件为准重物化后钉——按事件重写并审计，事件缺失仍拒",
            },
        }
    finally:
        conn.close()


def set_chain_policy(body: dict[str, Any]) -> dict[str, Any]:
    policy = (body.get("policy") or "").strip()
    if policy not in chain_service.POLICY_CHOICES:
        raise HTTPException(
            400,
            f"policy 须为 {list(chain_service.POLICY_CHOICES)} 之一，全局只贯彻一种策略",
        )
    conn = connect(immediate=True)
    try:
        saved = chain_service.set_policy(conn, policy)
        conn.commit()
        return get_chain_policy() | {"policy": saved}
    finally:
        conn.close()
