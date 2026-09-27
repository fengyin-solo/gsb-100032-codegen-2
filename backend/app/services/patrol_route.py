"""巡查任务路线台账业务规则。

台账由巡查记录（巡查编号、巡查路段、巡查人员）直接生成；排班变化、路段停用、
两人同时认领时，允许调整路线顺序与责任人，并在台账上留下冲突提示。

状态一致性：台账状态是任务的唯一事实来源——地图轨迹直接读台账，关联的巡查记录
状态由本服务同步；巡查记录侧的状态流转也会回写台账，保证台账、地图轨迹与巡查
记录明细对同一个任务只呈现同一状态。撤回会清空路线与轨迹，撤回后再打开不会保留
旧路线；需要重排时，可对该巡查记录重新生成一份新台账（旧台账留档）。
"""
from __future__ import annotations

import re
import zlib
from typing import Any

from app.store import store

MODULE = "patrol_route"
INSPECT_MODULE = "inspect"
CODE_PREFIX = "XJRT"

STATUSES = ["待认领", "已认领", "巡查中", "已完成", "已撤回"]
DONE_STATUSES = ["已完成", "已撤回"]
# 已完成/已转病害的巡查记录不再补排路线台账
INSPECT_FINISHED = ["已完成", "已转病害"]

# 台账状态 -> 巡查记录状态的同步口径：巡查中/已完成逐字一致；
# 待认领/已认领对应巡查记录仍停留在已排班；撤回把巡查记录退回已排班重新排班。
INSPECT_STATUS_SYNC: dict[str, str] = {
    "待认领": "已排班",
    "已认领": "已排班",
    "巡查中": "巡查中",
    "已完成": "已完成",
    "已撤回": "已排班",
}
# 台账状态视为一致的巡查记录状态集合（已转病害与台账已完成同属任务结束，不回退）
CONSISTENT_INSPECT_STATUS: dict[str, set[str]] = {
    "待认领": {"已排班"},
    "已认领": {"已排班"},
    "巡查中": {"巡查中"},
    "已完成": {"已完成", "已转病害"},
    "已撤回": {"已排班"},
}
# 巡查记录侧前向流转 -> 台账状态
LEDGER_STATUS_SYNC: dict[str, str] = {
    "巡查中": "巡查中",
    "已完成": "已完成",
    "已转病害": "已完成",
}

_SECTION_SPLIT_RE = re.compile(r"[、，,/／;；\s]+")
_CODE_TAIL_RE = re.compile(r"(\d+)$")


def _parse_sections(text: Any) -> list[str]:
    """把巡查路段字段拆成有序、去重的路段名列表。"""
    if not text:
        return []
    seen: set[str] = set()
    sections: list[str] = []
    for part in _SECTION_SPLIT_RE.split(str(text)):
        name = part.strip()
        if name and name not in seen:
            seen.add(name)
            sections.append(name)
    return sections


def _stop_point(name: str) -> tuple[float, float]:
    """按路段名稳定生成一个模拟经纬度，保证重开服务后轨迹不漂移。"""
    digest = zlib.crc32(name.encode("utf-8"))
    lng = round(116.30 + (digest % 1000) / 1000 * 0.25, 4)
    lat = round(39.88 + ((digest // 1000) % 1000) / 1000 * 0.12, 4)
    return lng, lat


def _build_stops(sections: list[str]) -> list[dict[str, Any]]:
    stops: list[dict[str, Any]] = []
    for index, name in enumerate(sections, start=1):
        lng, lat = _stop_point(name)
        stops.append({"seq": index, "路段": name, "坐标": [lng, lat], "停用": False})
    return stops


def _next_code() -> str:
    max_no = 0
    for row in store.rows(MODULE):
        match = _CODE_TAIL_RE.search(str(row.get("台账编号", "")))
        if match:
            max_no = max(max_no, int(match.group(1)))
    return f"{CODE_PREFIX}-{max_no + 1:04d}"


def sync_ledger_from_inspect(inspect_entry: dict[str, Any]) -> None:
    """巡查记录侧状态流转后回写台账，保证两边呈现同一状态。

    已撤回的台账属于历史留档，不参与同步。
    """
    target = LEDGER_STATUS_SYNC.get(str(inspect_entry.get("status")))
    if target is None:
        return
    inspect_id = int(inspect_entry.get("id", 0) or 0)
    for entry in store.rows(MODULE):
        if int(entry.get("巡查记录ID", 0) or 0) != inspect_id:
            continue
        if entry.get("status") == "已撤回":
            continue
        if entry.get("status") == target:
            continue
        entry["status"] = target
        entry["状态"] = target
        entry["pending"] = target not in DONE_STATUSES
        entry.setdefault("调整记录", []).append(
            f"巡查记录状态变更为「{inspect_entry['status']}」，台账同步为「{target}」"
        )


class PatrolRouteService:
    # ------------------------------------------------------------------ 读取
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [
                row
                for row in rows
                if keyword in str(row.get("台账编号", ""))
                or keyword in str(row.get("关联巡查编号", ""))
                or keyword in str(row.get("责任人", ""))
            ]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return [self._view(row) for row in rows[start:start + size]], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        return self._view(entry) if entry is not None else None

    def get_track(self, entry_id: int) -> dict[str, Any] | None:
        """地图轨迹：状态直接取台账，轨迹点取当前路线（撤回后为空）。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        points = [
            {
                "seq": stop.get("seq", index),
                "路段": stop.get("路段", ""),
                "lng": stop.get("坐标", [None, None])[0],
                "lat": stop.get("坐标", [None, None])[1],
                "停用": bool(stop.get("停用")),
            }
            for index, stop in enumerate(entry.get("路线", []), start=1)
        ]
        inspect_entry = store.find(
            INSPECT_MODULE, int(entry.get("巡查记录ID", 0) or 0)
        )
        return {
            "台账编号": entry.get("台账编号"),
            "状态": entry.get("status"),
            "巡查记录状态": inspect_entry.get("status") if inspect_entry else None,
            "points": points,
        }

    # ------------------------------------------------------------ 台账生成
    def generate(self) -> tuple[list[dict[str, Any]], list[tuple[str, str]]]:
        """从巡查记录、巡查路段、巡查人员直接生成台账。

        已存在有效台账的巡查记录跳过；已完成/已转病害的记录不再补排；
        已撤回台账的巡查记录允许重新生成（旧台账留档、路线已清空）。
        """
        created: list[dict[str, Any]] = []
        skipped: list[tuple[str, str]] = []
        ledgers = store.rows(MODULE)
        active_inspect_ids = {
            int(row.get("巡查记录ID", 0) or 0)
            for row in ledgers
            if row.get("status") != "已撤回"
        }
        for inspect_entry in store.rows(INSPECT_MODULE):
            code = str(inspect_entry.get("巡查编号", inspect_entry.get("id")))
            inspect_id = int(inspect_entry.get("id", 0))
            if inspect_id in active_inspect_ids:
                skipped.append((code, "已存在有效路线台账"))
                continue
            if inspect_entry.get("status") in INSPECT_FINISHED:
                skipped.append((code, f"巡查记录状态为「{inspect_entry['status']}」，不再补排路线"))
                continue
            sections = _parse_sections(inspect_entry.get("巡查路段"))
            if not sections:
                skipped.append((code, "巡查路段为空，无法拆分路线"))
                continue
            entry = {
                "id": max(
                    (int(row.get("id", 0)) for row in ledgers), default=0
                ) + 1,
                "台账编号": _next_code(),
                "巡查记录ID": inspect_id,
                "关联巡查编号": code,
                "责任人": str(inspect_entry.get("巡查人员") or "").strip(),
                "路线": _build_stops(sections),
                "status": "待认领",
                "状态": "待认领",
                "pending": True,
                "abnormal": False,
                "冲突提示": [],
                "调整记录": [
                    f"由巡查记录 {code} 生成：责任人取自巡查人员「"
                    f"{inspect_entry.get('巡查人员', '')}」，路线顺序按巡查路段拆分"
                ],
            }
            ledgers.append(entry)
            created.append(entry)
        return created, skipped

    # ------------------------------------------------------------------ 动作
    def run_action(
        self, entry_id: int, action: str, values: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any] | None, str, bool]:
        """返回 (台账, 说明, 动作是否生效)。冲突被驳回时 accepted=False 但提示已留档。"""
        values = values or {}
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"路线台账 {entry_id} 不存在或已删除", False
        if action == "认领":
            return self._claim(entry, values)
        if action == "开始巡查":
            if entry.get("status") != "已认领":
                return None, f"当前状态「{entry.get('status')}」不能开始巡查，请先完成认领", False
            return self._transit(entry, "巡查中", "开始巡查")
        if action == "完成巡查":
            if entry.get("status") != "巡查中":
                return None, f"当前状态「{entry.get('status')}」不能完成巡查", False
            return self._transit(entry, "已完成", "完成巡查")
        if action == "撤回":
            if entry.get("status") not in ("待认领", "已认领"):
                return (
                    None,
                    f"当前状态「{entry.get('status')}」不允许撤回，巡查中/已完成请走结案流程",
                    False,
                )
            return self._withdraw(entry)
        return None, f"动作「{action}」不属于路线台账可执行范围", False

    def _claim(
        self, entry: dict[str, Any], values: dict[str, Any]
    ) -> tuple[dict[str, Any], str, bool]:
        claimer = str(values.get("认领人") or values.get("责任人") or "").strip()
        if not claimer:
            return entry, "认领需要填写认领人", False
        status = str(entry.get("status"))
        if status == "待认领":
            scheduled = str(entry.get("责任人") or "").strip()
            entry["责任人"] = claimer
            self._set_status(entry, "已认领")
            if scheduled and scheduled != claimer:
                note = f"排班责任人「{scheduled}」未认领，「{claimer}」抢先认领并成为责任人"
                conflict = f"认领人与排班责任人不一致：排班为 {scheduled}，实际认领 {claimer}"
                entry["冲突提示"].append(conflict)
                entry["abnormal"] = True
            else:
                note = f"{claimer} 认领任务，责任人确认"
            entry["调整记录"].append(note)
            self._sync_inspect(entry)
            return entry, f"台账已由 {claimer} 认领", True

        owner = str(entry.get("责任人") or "").strip()
        if owner == claimer:
            return entry, f"{claimer} 已是该任务责任人，无需重复认领", False
        # 已认领/巡查中/已完成时他人再认领：两人同时认领，驳回并留冲突提示
        conflict = (
            f"两人同时认领：{owner} 已先认领该任务，{claimer} 的认领被驳回，"
            f"责任人维持 {owner}"
        )
        entry["冲突提示"].append(conflict)
        entry["调整记录"].append(conflict)
        entry["abnormal"] = True
        return entry, conflict, False

    def _transit(
        self, entry: dict[str, Any], target: str, label: str
    ) -> tuple[dict[str, Any], str, bool]:
        self._set_status(entry, target)
        entry["调整记录"].append(f"{label}，台账状态变更为「{target}」")
        self._sync_inspect(entry)
        return (
            entry,
            f"台账已{label}，地图轨迹与巡查记录明细已同步为「{target}」",
            True,
        )

    def _withdraw(self, entry: dict[str, Any]) -> tuple[dict[str, Any], str, bool]:
        old_sections = [str(stop.get("路段", "")) for stop in entry.get("路线", [])]
        entry["路线"] = []  # 清空旧路线，撤回后再打开不会保留
        self._set_status(entry, "已撤回")
        detail = "、".join(old_sections) if old_sections else "（无）"
        entry["调整记录"].append(
            f"任务撤回，原路线（{detail}）与地图轨迹已清空，不再保留旧路线"
        )
        self._sync_inspect(entry)
        return entry, "台账已撤回，旧路线与轨迹已清空；需要时可重新生成台账", True

    # ------------------------------------------------------------------ 调整
    def adjust(
        self, entry_id: int, values: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str, bool]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"路线台账 {entry_id} 不存在或已删除", False
        if entry.get("status") in ("已完成", "已撤回"):
            return (
                None,
                f"当前状态「{entry.get('status')}」不允许调整路线；撤回后的任务请重新生成台账",
                False,
            )

        changed = False
        reason = str(values.get("原因") or "").strip()
        logs: list[str] = []

        # 责任人调整：排班变化
        new_owner = str(values.get("责任人") or "").strip()
        if new_owner:
            old_owner = str(entry.get("责任人") or "").strip()
            if new_owner != old_owner:
                entry["责任人"] = new_owner
                changed = True
                note = f"排班变化：责任人由「{old_owner or '空'}」调整为「{new_owner}」"
                if reason:
                    note += f"（原因：{reason}）"
                logs.append(note)
                if entry.get("status") == "巡查中":
                    conflict = (
                        f"巡查中变更责任人：{old_owner or '空'} → {new_owner}，"
                        "需办理现场交接确认"
                    )
                    entry["冲突提示"].append(conflict)
                    logs.append(conflict)

        # 路段停用
        disabled = str(values.get("停用路段") or "").strip()
        if disabled:
            target_stop = next(
                (stop for stop in entry["路线"] if stop.get("路段") == disabled),
                None,
            )
            if target_stop is None:
                return None, f"路线中不存在路段「{disabled}」，无法停用", False
            if not target_stop.get("停用"):
                target_stop["停用"] = True
                changed = True
                conflict = f"路段「{disabled}」已停用，请调整路线顺序或将其移出路线"
                entry["冲突提示"].append(conflict)
                logs.append(conflict)
            else:
                logs.append(f"路段「{disabled}」已是停用状态")

        # 路线顺序：提交新顺序的路段名列表，省略即移除
        order = values.get("路线顺序")
        if order is not None:
            if not isinstance(order, list) or not order:
                return None, "路线顺序不能为空；如需取消任务请使用撤回", False
            names = [str(name).strip() for name in order if str(name).strip()]
            if not names:
                return None, "路线顺序不能为空；如需取消任务请使用撤回", False
            current = entry["路线"]
            by_name = {str(stop.get("路段")): stop for stop in current}
            unknown = [name for name in names if name not in by_name]
            if unknown:
                return None, "路线顺序中包含不在原路线内的路段：" + "、".join(unknown), False
            removed = [
                str(stop.get("路段")) for stop in current if stop.get("路段") not in names
            ]
            old_names = [str(stop.get("路段")) for stop in current]
            reordered: list[dict[str, Any]] = []
            for index, name in enumerate(names, start=1):
                stop = dict(by_name[name])
                stop["seq"] = index
                reordered.append(stop)
            entry["路线"] = reordered
            if names != old_names or removed:
                changed = True
                if names != old_names:
                    logs.append("路线顺序已调整为：" + " → ".join(names))
                for name in removed:
                    suffix = "（停用路段）" if by_name[name].get("停用") else ""
                    logs.append(f"路段「{name}」已移出路线{suffix}")

        if not changed:
            return None, "没有可调整的内容，请提交新责任人、停用路段或新的路线顺序", False

        entry["调整记录"].extend(logs)
        entry["abnormal"] = bool(entry.get("冲突提示"))
        self._sync_inspect(entry)
        return entry, "路线台账已调整：" + "；".join(logs), True

    # ------------------------------------------------------------------ 内部
    def _set_status(self, entry: dict[str, Any], target: str) -> None:
        entry["status"] = target
        entry["状态"] = target
        entry["pending"] = target not in DONE_STATUSES

    def _sync_inspect(self, entry: dict[str, Any]) -> None:
        """把台账状态同步到关联巡查记录；已结束语义（已转病害）不回退。"""
        inspect_entry = store.find(
            INSPECT_MODULE, int(entry.get("巡查记录ID", 0) or 0)
        )
        if inspect_entry is None:
            return
        consistent = CONSISTENT_INSPECT_STATUS.get(str(entry.get("status")), set())
        if inspect_entry.get("status") in consistent:
            return
        target = INSPECT_STATUS_SYNC[str(entry["status"])]
        inspect_entry["status"] = target
        inspect_entry["pending"] = target not in ("已完成", "已转病害")

    def _view(self, entry: dict[str, Any]) -> dict[str, Any]:
        # 已撤回台账属于历史留档，读时不再回写巡查记录；
        # 有效台账在读时做一次一致性自愈，确保三处状态不会漂移。
        if entry.get("status") != "已撤回":
            self._sync_inspect(entry)
        view = dict(entry)
        view["路线"] = [dict(stop) for stop in entry.get("路线", [])]
        view["路线段数"] = len(view["路线"])
        view["冲突数"] = len(entry.get("冲突提示", []))
        inspect_entry = store.find(
            INSPECT_MODULE, int(entry.get("巡查记录ID", 0) or 0)
        )
        view["巡查记录状态"] = (
            inspect_entry.get("status") if inspect_entry is not None else None
        )
        return view
