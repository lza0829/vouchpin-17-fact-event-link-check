from __future__ import annotations
from typing import Any
from fastapi import APIRouter
from app.services import settings_service as svc

router = APIRouter(tags=["settings"])


@router.get("/settings/day")
def get_day():
    return svc.get_day()


@router.put("/settings/day")
def put_day(body: dict[str, Any]):
    return svc.set_day(body)


@router.get("/settings/chain-policy")
def get_chain_policy():
    return svc.get_chain_policy()


@router.put("/settings/chain-policy")
def put_chain_policy(body: dict[str, Any]):
    return svc.set_chain_policy(body)
