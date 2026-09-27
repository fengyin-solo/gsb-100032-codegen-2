"""巡查任务路线台账业务规则。

路线台账不单独存一份“路线快照”，而是每次从巡查记录、巡查路段、巡查人员三张源表实时生成：
台账、地图轨迹、巡查记录明细因此对同一任务永远呈现同一状态。

- 顺序与责任人的人工调整放在覆盖层（inspect_route_assign）；
- 现场认领记录放在 inspect_route_claim；
- 冲突提示落在 inspect_route_conflict，调整/认领/重新生成时按当前数据重算并留痕；
- 撤回会清掉覆盖层、认领与冲突记录，再次打开只能按当时的源数据重建，不会保留旧路线。
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any

from app.store import store

INSPECT_MODULE = "inspect"
SECTION_MODULE = "inspect_section"
STAFF_MODULE = "inspect_staff"
META_MODULE = "inspect_route_meta"
ASSIGN_MODULE = "inspect_route_assign"
CLAIM_MODULE = "inspect_route_claim"
CONFLICT_MODULE = "inspect_route_conflict"

SECTION_SPLIT_RE = re.compile(r"[、,，;；\s]+")

CONFLICT_SECTION_MISSING = "路段不存在"
CONFLICT_SECTION_DISABLED = "路段停用"
CONFLICT_SCHEDULE_CHANGED = "排班变化"
CONFLICT_DOUBLE_CLAIM = "两人同时认领"
CONFLICT_DUPLICATE = "路段重复编排"


def _next_id(rows: list[dict[str, Any]]) -> int:
    return max((int(row.get("id", 0)) for row in rows), default=0) + 1


def _split_codes(text: Any) -> list[str]:
    """巡查记录的“巡查路段”字段按中文标点拆成路段编码序列，保留原始顺序。"""
    return [part for part in SECTION_SPLIT_RE.split(str(text or "").strip()) if part]


def _today() -> str:
    return date.today().isoformat()


class InspectRouteService:
    # ---------- 基础数据：路段 ----------
    def list_sections(self) -> list[dict[str, Any]]:
        return store.rows(SECTION_MODULE)

    def toggle_section(self, section_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        section = store.find(SECTION_MODULE, section_id)
        if section is None:
            return None, f"巡查路段 {section_id} 不存在"
        if action not in ("启用", "停用"):
            return None, "路段只支持「启用」「停用」两种操作"
        section["status"] = action
        section["abnormal"] = action == "停用"
        self._refresh_open_routes()
        return section, f"巡查路段{section.get('路段编码')}已{action}，相关台账冲突提示已重算"

    # ---------- 基础数据：人员排班 ----------
    def list_staff(self) -> list[dict[str, Any]]:
        return store.rows(STAFF_MODULE)

    def change_staff_schedule(
        self, staff_id: int, values: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str]:
        staff = store.find(STAFF_MODULE, staff_id)
        if staff is None:
            return None, f"巡查人员 {staff_id} 不存在"
        action = str(values.get("action") or "").strip()
        if action == "排班":
            shift = str(values.get("班次") or "").strip()
            if not shift:
                return None, "排班必须指定班次（如白班、夜班）"
            staff["班次"] = shift
            staff["排班日期"] = str(values.get("排班日期") or _today()).strip() or _today()
            staff["status"] = "在岗"
            staff["abnormal"] = False
        elif action == "置休":
            staff["班次"] = "休息"
            staff["status"] = "休息"
            staff["abnormal"] = True
        else:
            return None, "排班调整只支持「排班」「置休」两种操作"
        self._refresh_open_routes()
        return staff, f"巡查人员{staff.get('姓名')}排班已调整，相关台账冲突提示已重算"

    # ---------- 台账列表/明细 ----------
    def list_routes(self, *, page: int = 1, size: int = 20) -> tuple[list[dict[str, Any]], int]:
        summaries = [self._route_summary(record) for record in store.rows(INSPECT_MODULE)]
        total = len(summaries)
        start = max(page - 1, 0) * size
        return summaries[start:start + size], total

    def get_route(self, inspect_id: int) -> dict[str, Any] | None:
        record = store.find(INSPECT_MODULE, inspect_id)
        if record is None:
            return None
        return self._route_detail(record)

    # ---------- 生成 / 撤回 ----------
    def generate(self, inspect_id: int) -> tuple[dict[str, Any] | None, str]:
        record = store.find(INSPECT_MODULE, inspect_id)
        if record is None:
            return None, f"巡查记录单 {inspect_id} 不存在或已归档，无法生成路线台账"
        meta = self._ensure_meta(inspect_id)
        if not meta.get("generated"):
            meta["generated"] = True
        meta["version"] = int(meta.get("version", 0)) + 1
        route = self._route_detail(record)
        self._snapshot_conflicts(route)
        return route, f"巡查任务{record.get('巡查编号')}路线台账已按巡查记录、路段、人员排班生成"

    def withdraw(self, inspect_id: int) -> tuple[dict[str, Any] | None, str]:
        record = store.find(INSPECT_MODULE, inspect_id)
        if record is None:
            return None, f"巡查记录单 {inspect_id} 不存在或已归档"
        meta = self._ensure_meta(inspect_id)
        if not meta.get("generated"):
            return None, "该任务尚未生成路线台账，无需撤回"
        # 撤回即清空覆盖层与一切衍生数据，旧路线无法在重新打开时残留
        meta["generated"] = False
        meta["version"] = int(meta.get("version", 0)) + 1
        for table in (ASSIGN_MODULE, CLAIM_MODULE, CONFLICT_MODULE):
            rows = store.rows(table)
            rows[:] = [row for row in rows if int(row.get("inspect_id", 0)) != inspect_id]
        return {"inspect_id": inspect_id, "generated": False, "version": meta["version"]}, (
            f"巡查任务{record.get('巡查编号')}路线台账已撤回，旧路线与调整记录已清除"
        )

    # ---------- 调整顺序 / 责任人 ----------
    def adjust_route(
        self, inspect_id: int, values: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str]:
        record = store.find(INSPECT_MODULE, inspect_id)
        if record is None:
            return None, f"巡查记录单 {inspect_id} 不存在或已归档"
        meta = self._ensure_meta(inspect_id)
        if not meta.get("generated"):
            return None, "请先生成路线台账，再调整路线顺序或责任人"

        raw_assignments = values.get("assignments")
        if not isinstance(raw_assignments, list) or not raw_assignments:
            return None, "调整必须提交完整的 assignments（路段编码 + 责任人编码，按新顺序排列）"

        source_codes = _split_codes(record.get("巡查路段"))
        staff_codes = {str(row.get("人员编码")) for row in store.rows(STAFF_MODULE)}
        assignments: list[dict[str, str]] = []
        for item in raw_assignments:
            if not isinstance(item, dict):
                return None, "assignments 每一项都应包含 section_code 与 staff_code"
            code = str(item.get("section_code") or "").strip()
            staff = str(item.get("staff_code") or "").strip()
            if not code or not staff:
                return None, "每个路线停靠点都要指定路段编码和责任人编码"
            assignments.append({"section_code": code, "staff_code": staff})

        ordered_codes = [item["section_code"] for item in assignments]
        missing = [code for code in source_codes if code not in ordered_codes]
        extra = [code for code in ordered_codes if code not in source_codes]
        if missing or extra:
            parts = []
            if missing:
                parts.append(f"缺少路段：{'、'.join(missing)}")
            if extra:
                parts.append(f"原路线没有的路段：{'、'.join(extra)}")
            return None, "调整后的路段集合必须与巡查记录完全一致（" + "；".join(parts) + "）"
        unknown_staff = sorted({item["staff_code"] for item in assignments} - staff_codes)
        if unknown_staff:
            return None, f"责任人编码不存在：{'、'.join(unknown_staff)}"

        rows = store.rows(ASSIGN_MODULE)
        rows[:] = [row for row in rows if int(row.get("inspect_id", 0)) != inspect_id]
        for index, item in enumerate(assignments, start=1):
            rows.append({
                "id": _next_id(rows),
                "inspect_id": inspect_id,
                "seq": index,
                "section_code": item["section_code"],
                "staff_code": item["staff_code"],
            })
        meta["version"] = int(meta.get("version", 0)) + 1

        route = self._route_detail(record)
        self._snapshot_conflicts(route)
        message = "路线顺序与责任人已调整"
        if route["conflicts"]:
            message += f"，当前仍有 {len(route['conflicts'])} 条冲突提示"
        return route, message

    # ---------- 现场认领 ----------
    def claim_section(
        self, inspect_id: int, values: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str]:
        record = store.find(INSPECT_MODULE, inspect_id)
        if record is None:
            return None, f"巡查记录单 {inspect_id} 不存在或已归档"
        meta = self._ensure_meta(inspect_id)
        if not meta.get("generated"):
            return None, "请先生成路线台账，再认领路段"
        code = str(values.get("section_code") or "").strip()
        staff_code = str(values.get("staff_code") or "").strip()
        if not code or not staff_code:
            return None, "认领必须提交 section_code 与 staff_code"
        if code not in _split_codes(record.get("巡查路段")):
            return None, f"路段 {code} 不在任务 {record.get('巡查编号')} 的路线上，不能认领"
        staff = self._staff_by_code(staff_code)
        if staff is None:
            return None, f"巡查人员 {staff_code} 不存在"

        rows = store.rows(CLAIM_MODULE)
        existing = next(
            (row for row in rows
             if int(row.get("inspect_id", 0)) == inspect_id
             and row.get("section_code") == code
             and row.get("staff_code") == staff_code),
            None,
        )
        if existing is None:
            rows.append({
                "id": _next_id(rows),
                "inspect_id": inspect_id,
                "section_code": code,
                "staff_code": staff_code,
            })
        meta["version"] = int(meta.get("version", 0)) + 1

        route = self._route_detail(record)
        self._snapshot_conflicts(route)
        message = f"路段 {code} 已由 {staff.get('姓名')} 认领"
        if any(item["kind"] == CONFLICT_DOUBLE_CLAIM and item["section_code"] == code
               for item in route["conflicts"]):
            message += "，与同站责任人构成两人同时认领，系统已留下冲突提示"
        elif route["conflicts"]:
            message += f"，系统留下 {len(route['conflicts'])} 条冲突提示"
        return route, message

    # ---------- 地图轨迹 ----------
    def get_track(self, inspect_id: int) -> dict[str, Any] | None:
        record = store.find(INSPECT_MODULE, inspect_id)
        if record is None:
            return None
        route = self._route_detail(record)
        points: list[dict[str, Any]] = []
        for stop in route["stops"]:
            points.append({
                "order": stop["order"],
                "section_code": stop["section_code"],
                "section_name": stop["section_name"],
                "staff_code": stop["staff_code"],
                "staff_name": stop["staff_name"],
                "start": stop["start"],
                "end": stop["end"],
            })
        route["points"] = points
        return route

    def list_conflicts(self) -> list[dict[str, Any]]:
        return store.rows(CONFLICT_MODULE)

    # ---------- 内部：元数据与覆盖层 ----------
    def _ensure_meta(self, inspect_id: int) -> dict[str, Any]:
        rows = store.rows(META_MODULE)
        meta = next((row for row in rows if int(row.get("inspect_id", 0)) == inspect_id), None)
        if meta is None:
            meta = {"id": _next_id(rows), "inspect_id": inspect_id, "generated": False, "version": 0}
            rows.append(meta)
        return meta

    def _section_map(self) -> dict[str, dict[str, Any]]:
        return {str(row.get("路段编码")): row for row in store.rows(SECTION_MODULE)}

    def _staff_map(self) -> dict[str, dict[str, Any]]:
        return {str(row.get("人员编码")): row for row in store.rows(STAFF_MODULE)}

    def _staff_by_code(self, code: str) -> dict[str, Any] | None:
        return self._staff_map().get(code)

    def _overrides(self, inspect_id: int) -> tuple[dict[str, str], dict[str, int]]:
        """返回 (路段编码 -> 调整后责任人, 路段编码 -> 调整后的顺序号)。"""
        assign_rows = sorted(
            (row for row in store.rows(ASSIGN_MODULE) if int(row.get("inspect_id", 0)) == inspect_id),
            key=lambda row: int(row.get("seq", 0)),
        )
        owners = {str(row["section_code"]): str(row["staff_code"]) for row in assign_rows}
        order_map = {str(row["section_code"]): int(row["seq"]) for row in assign_rows}
        return owners, order_map

    def _claims(self, inspect_id: int) -> dict[str, list[str]]:
        claims: dict[str, list[str]] = {}
        for row in store.rows(CLAIM_MODULE):
            if int(row.get("inspect_id", 0)) != inspect_id:
                continue
            claims.setdefault(str(row["section_code"]), []).append(str(row["staff_code"]))
        return claims

    # ---------- 内部：实时计算路线（唯一事实来源） ----------
    def _route_detail(self, record: dict[str, Any]) -> dict[str, Any]:
        inspect_id = int(record["id"])
        meta = self._ensure_meta(inspect_id)
        generated = bool(meta.get("generated"))
        sections = self._section_map()
        staffs = self._staff_map()
        owners, order_map = self._overrides(inspect_id)
        claims = self._claims(inspect_id)

        raw_codes = _split_codes(record.get("巡查路段"))
        seen: set[str] = set()
        duplicates: set[str] = set()
        for code in raw_codes:
            if code in seen:
                duplicates.add(code)
            seen.add(code)

        stops: list[dict[str, Any]] = []
        for index, code in enumerate(raw_codes, start=1):
            section = sections.get(code)
            staff_code = owners.get(code) or str(record.get("巡查人员") or "")
            staff = staffs.get(staff_code)
            claim_codes = claims.get(code, [])
            claim_names = [
                str(staffs[claim_code].get("姓名"))
                for claim_code in claim_codes
                if claim_code in staffs
            ]
            stop = {
                "order": order_map.get(code, index),
                "section_code": code,
                "section_name": str(section.get("路段名称")) if section else "",
                "start": str(section.get("起点坐标")) if section else "",
                "end": str(section.get("终点坐标")) if section else "",
                "staff_code": staff_code,
                "staff_name": str(staff.get("姓名")) if staff else "",
                "owner_source": "调整" if code in owners else "排班",
                "claim_staff_codes": claim_codes,
                "claim_staff_names": claim_names,
                "conflicts": [],
            }
            if section is None:
                stop["conflicts"].append(CONFLICT_SECTION_MISSING)
            elif str(section.get("status")) == "停用":
                stop["conflicts"].append(CONFLICT_SECTION_DISABLED)
            if staff is None:
                stop["conflicts"].append(CONFLICT_SCHEDULE_CHANGED)
            elif str(staff.get("status")) != "在岗":
                stop["conflicts"].append(CONFLICT_SCHEDULE_CHANGED)
            if code in duplicates:
                stop["conflicts"].append(CONFLICT_DUPLICATE)
            stops.append(stop)

        # 调整后的顺序排序；未调整的停靠点保持相对位置
        stops.sort(key=lambda stop: stop["order"])
        for index, stop in enumerate(stops, start=1):
            stop["order"] = index

        if generated:
            self._mark_double_claims(record, stops, sections, staffs)

        conflicts: list[dict[str, Any]] = []
        for stop in stops:
            for kind in stop["conflicts"]:
                conflicts.append({
                    "inspect_id": inspect_id,
                    "巡查编号": str(record.get("巡查编号") or ""),
                    "stop_order": stop["order"],
                    "section_code": stop["section_code"],
                    "section_name": stop["section_name"],
                    "kind": kind,
                    "message": self._conflict_message(kind, stop),
                })

        return {
            "inspect_id": inspect_id,
            "巡查编号": str(record.get("巡查编号") or ""),
            "巡查日期": str(record.get("巡查日期") or ""),
            "管线类型": str(record.get("管线类型") or ""),
            "generated": generated,
            "adjusted": bool(owners),
            "version": int(meta.get("version", 0)),
            # 状态只取自巡查记录源表，台账/轨迹/明细因此严格一致
            "status": str(record.get("status") or ""),
            "stops": stops,
            "conflicts": conflicts,
            "stop_count": len(stops),
            "claimed_count": len(claims),
        }

    def _route_summary(self, record: dict[str, Any]) -> dict[str, Any]:
        route = self._route_detail(record)
        return {
            "inspect_id": route["inspect_id"],
            "巡查编号": route["巡查编号"],
            "巡查日期": route["巡查日期"],
            "管线类型": route["管线类型"],
            "generated": route["generated"],
            "status": route["status"],
            "version": route["version"],
            "adjusted": route["adjusted"],
            "stop_count": route["stop_count"],
            "claimed_count": route["claimed_count"],
            "conflict_count": len(route["conflicts"]),
            "conflict_kinds": sorted({item["kind"] for item in route["conflicts"]}),
        }

    def _mark_double_claims(
        self,
        record: dict[str, Any],
        stops: list[dict[str, Any]],
        sections: dict[str, dict[str, Any]],
        staffs: dict[str, dict[str, Any]],
    ) -> None:
        """两人同时认领：同一天、同一路段上，责任人与各任务的认领人出现两个及以上不同人员即判冲突。"""
        target_date = str(record.get("巡查日期") or "")

        def people_on(code: str) -> set[str]:
            people: set[str] = set()
            for other in store.rows(INSPECT_MODULE):
                if str(other.get("巡查日期") or "") != target_date:
                    continue
                other_id = int(other["id"])
                other_meta = self._ensure_meta(other_id)
                if not other_meta.get("generated"):
                    continue
                if code not in _split_codes(other.get("巡查路段")):
                    continue
                owners, _ = self._overrides(other_id)
                owner_code = owners.get(code) or str(other.get("巡查人员") or "")
                # 置休/不存在的人员已由「排班变化」覆盖，不参与两人同时认领判定
                if owner_code and str(staffs.get(owner_code, {}).get("status")) == "在岗":
                    people.add(owner_code)
                people.update(
                    claim_code
                    for claim_section, claim_codes in self._claims(other_id).items()
                    if claim_section == code
                    for claim_code in claim_codes
                    if str(staffs.get(claim_code, {}).get("status")) == "在岗"
                )
            return people

        for stop in stops:
            code = stop["section_code"]
            if code not in sections:
                continue
            people = people_on(code)
            if len(people) >= 2:
                names = "、".join(
                    sorted(str(staffs[code_name].get("姓名")) for code_name in people if code_name in staffs)
                ) or "、".join(sorted(people))
                stop["conflicts"].append(CONFLICT_DOUBLE_CLAIM)
                stop["double_claim_people"] = names  # type: ignore[assignment]

    def _conflict_message(self, kind: str, stop: dict[str, Any]) -> str:
        code = stop["section_code"]
        if kind == CONFLICT_SECTION_MISSING:
            return f"第 {stop['order']} 站路段编码 {code} 在巡查路段档案中不存在，请核对巡查记录"
        if kind == CONFLICT_SECTION_DISABLED:
            return f"第 {stop['order']} 站路段 {code}（{stop['section_name']}）已停用，排班需改道或重新启用"
        if kind == CONFLICT_SCHEDULE_CHANGED:
            who = stop["staff_name"] or stop["staff_code"]
            return f"第 {stop['order']} 站责任人 {who}（{stop['staff_code']}）今日不在岗，排班已发生变化，请重新指定责任人"
        if kind == CONFLICT_DOUBLE_CLAIM:
            return (
                f"第 {stop['order']} 站路段 {code} 同时被多人认领/负责："
                f"{stop.get('double_claim_people', '')}，需明确唯一责任人"
            )
        if kind == CONFLICT_DUPLICATE:
            return f"路段 {code} 在本任务路线中重复编排，请调整路线顺序"
        return kind

    # ---------- 内部：冲突留痕 ----------
    def _snapshot_conflicts(self, route: dict[str, Any]) -> None:
        """按当前数据重算并覆盖该任务的冲突提示记录；冲突解除后旧提示不再残留。"""
        inspect_id = int(route["inspect_id"])
        rows = store.rows(CONFLICT_MODULE)
        rows[:] = [row for row in rows if int(row.get("inspect_id", 0)) != inspect_id]
        for item in route["conflicts"]:
            rows.append({
                "id": _next_id(rows),
                "inspect_id": inspect_id,
                "巡查编号": item["巡查编号"],
                "section_code": item["section_code"],
                "kind": item["kind"],
                "message": item["message"],
                "detected_at": _today(),
            })

    def _refresh_open_routes(self) -> None:
        """路段停用/启用、排班变化后，已生成台账的冲突提示同步重算。"""
        for record in store.rows(INSPECT_MODULE):
            meta = self._ensure_meta(int(record["id"]))
            if meta.get("generated"):
                self._snapshot_conflicts(self._route_detail(record))
