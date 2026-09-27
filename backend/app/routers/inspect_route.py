"""巡查任务路线台账接口。

台账、轨迹、明细三处都走同一个服务实时计算；路由层只负责取参与回包，业务判断全部在
InspectService/InspectRouteService 中。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.inspect_route import InspectRouteService

router = APIRouter(prefix="/api/inspect-routes", tags=["巡查路线台账"])

service = InspectRouteService()


# ---- 台账与明细 ----
@router.get("", response_model=PageResult[dict])
def list_routes(page: int = 1, size: int = 20) -> PageResult[dict]:
    """巡查任务路线台账列表：每个巡查任务对应一条台账行，状态随巡查记录实时一致。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_routes(page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/conflicts", response_model=dict)
def list_conflicts() -> dict:
    """汇总当前留痕的冲突提示，供台账页集中提示排班变化、停用路段、双重认领。"""
    items = service.list_conflicts()
    return {"total": len(items), "items": items}


@router.get("/{inspect_id}/track", response_model=dict)
def get_track(inspect_id: int) -> dict:
    """地图轨迹：与台账、巡查记录明细同源，状态和停靠点顺序严格一致。"""
    route = service.get_track(inspect_id)
    if route is None:
        raise HTTPException(status_code=404, detail=f"巡查记录单 {inspect_id} 不存在或已归档")
    return route


@router.get("/{inspect_id}", response_model=dict)
def get_route(inspect_id: int) -> dict:
    """打开单个任务的路线台账：从巡查记录、巡查路段、巡查人员实时生成；撤回后返回未生成态。"""
    route = service.get_route(inspect_id)
    if route is None:
        raise HTTPException(status_code=404, detail=f"巡查记录单 {inspect_id} 不存在或已归档")
    return route


@router.post("/{inspect_id}/generate", response_model=ActionResult)
def generate_route(inspect_id: int) -> ActionResult:
    """由巡查记录、巡查路段、巡查人员生成路线台账；重复生成会按当前源数据重建并保留调整版本。"""
    route, message = service.generate(inspect_id)
    if route is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=route)


@router.post("/{inspect_id}/adjust", response_model=ActionResult)
def adjust_route(inspect_id: int, payload: EntryPayload) -> ActionResult:
    """调整路线顺序与责任人：提交完整 assignments，冲突会重新计算并提示。"""
    route, message = service.adjust_route(inspect_id, payload.values)
    if route is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=route)


@router.post("/{inspect_id}/claim", response_model=ActionResult)
def claim_section(inspect_id: int, payload: EntryPayload) -> ActionResult:
    """巡查人员现场认领路段；同一路段出现两名认领人时立即留下「两人同时认领」冲突。"""
    route, message = service.claim_section(inspect_id, payload.values)
    if route is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=route)


@router.post("/{inspect_id}/withdraw", response_model=ActionResult)
def withdraw_route(inspect_id: int) -> ActionResult:
    """撤回路线台账：清除覆盖层、认领与冲突留痕，再次打开不会保留旧路线。"""
    result, message = service.withdraw(inspect_id)
    if result is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=result)


# ---- 路段停用 / 启用（驱动冲突提示） ----
@router.get("/sections/all", response_model=dict)
def list_sections() -> dict:
    """巡查路段基础档案：路线台账按路段编码实时引用。"""
    items = service.list_sections()
    return {"total": len(items), "items": items}


@router.post("/sections/{section_id}/actions", response_model=ActionResult)
def toggle_section(section_id: int, payload: EntryPayload) -> ActionResult:
    """启用/停用巡查路段；停用后已生成台账会立刻出现「路段停用」冲突提示。"""
    action = str(payload.values.get("action") or "").strip()
    section, message = service.toggle_section(section_id, action)
    if section is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=section)


# ---- 排班变化（驱动冲突提示） ----
@router.get("/staff/all", response_model=dict)
def list_staff() -> dict:
    """巡查人员排班基础档案：路线台账按人员编码实时引用。"""
    items = service.list_staff()
    return {"total": len(items), "items": items}


@router.post("/staff/{staff_id}/schedule", response_model=ActionResult)
def change_staff_schedule(staff_id: int, payload: EntryPayload) -> ActionResult:
    """调整人员排班（排班/置休）；责任人离岗后相关台账立刻出现「排班变化」冲突提示。"""
    staff, message = service.change_staff_schedule(staff_id, payload.values)
    if staff is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=staff)
