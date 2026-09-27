"""巡查任务路线台账接口：生成台账、认领/调整/撤回，以及地图轨迹读取。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.patrol_route import STATUSES, PatrolRouteService

router = APIRouter(prefix="/api/patrol_route", tags=["巡查路线台账"])

service = PatrolRouteService()


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按台账编号、巡查编号或责任人检索"),
    status: str | None = Query(default=None, description="待认领、已认领、巡查中、已完成、已撤回"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按条件分页列出路线台账；每条都带上实时同步后的巡查记录状态。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    if status and status not in STATUSES:
        raise HTTPException(status_code=400, detail=f"状态只支持：{'、'.join(STATUSES)}")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.post("/generate", response_model=ActionResult)
def generate_entries() -> ActionResult:
    """直接从巡查记录、巡查路段、巡查人员生成路线台账；跳过原因一并返回。"""
    created, skipped = service.generate()
    if not created:
        reason = "；".join(f"{code}：{why}" for code, why in skipped) or "没有可生成的巡查记录"
        return ActionResult(ok=False, message=f"没有新增台账（{reason}）")
    message = f"已生成 {len(created)} 份路线台账"
    if skipped:
        message += "；跳过 " + "；".join(f"{code}（{why}）" for code, why in skipped)
    return ActionResult(
        ok=True,
        message=message,
        entry={"created": [dict(item) for item in created], "skipped": skipped},
    )


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出台账清单：返回全量台账数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "patrol_route", "total": total, "items": items}


@router.get("/{entry_id}/track")
def get_track(entry_id: int) -> dict[str, Any]:
    """读取地图轨迹：状态取自台账本身，撤回后的任务轨迹为空。"""
    track = service.get_track(entry_id)
    if track is None:
        raise HTTPException(status_code=404, detail=f"路线台账 {entry_id} 不存在或已删除")
    return track


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单份台账明细，含路线顺序、冲突提示、调整记录与巡查记录实时状态。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"路线台账 {entry_id} 不存在或已删除")
    return entry


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """认领、开始巡查、完成巡查、撤回；冲突驳回时 ok=False 且冲突已在台账留痕。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message, accepted = service.run_action(entry_id, action, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=accepted, message=message, entry=entry)


@router.put("/{entry_id}/adjust", response_model=ActionResult)
def adjust_entry(entry_id: int, payload: EntryPayload) -> ActionResult:
    """调整责任人（排班变化）、停用路段、重排路线顺序；冲突会写入冲突提示。"""
    entry, message, accepted = service.adjust(entry_id, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=accepted, message=message, entry=entry)
