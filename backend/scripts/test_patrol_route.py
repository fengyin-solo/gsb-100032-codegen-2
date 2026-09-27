"""巡查路线台账业务规则的内存集成测试：不依赖 fastapi，直接跑 service 层。

运行：PYTHONPATH=. python3 scripts/test_patrol_route.py
"""
from __future__ import annotations

from app.services.inspect import InspectService
from app.services.patrol_route import MODULE, PatrolRouteService
from app.store import store


def assert_true(cond: object, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def find_ledger(code: str) -> dict:
    return next(row for row in store.rows(MODULE) if row["台账编号"] == code)


def main() -> None:
    svc = PatrolRouteService()
    inspect_svc = InspectService()

    # 1) 生成：INSP-0001 可生成；INSP-0002 已有有效台账跳过；INSP-0003 已完成跳过
    created, skipped = svc.generate()
    assert_true(len(created) == 1, f"应只生成 1 份台账，实际 {len(created)}")
    ledger = created[0]
    assert_true(ledger["台账编号"] == "XJRT-0002", f"新台账编号异常：{ledger['台账编号']}")
    assert_true(ledger["关联巡查编号"] == "INSP-0001", "应由 INSP-0001 生成")
    assert_true(ledger["责任人"] == "张建国", "责任人应取自巡查人员")
    assert_true([s["路段"] for s in ledger["路线"]]
                == ["和平路雨水管段", "解放北路污水管段", "滨江中路合流管段"],
                "路线应按巡查路段顺序拆分")
    assert_true(ledger["status"] == "待认领", "新台账应为待认领")
    skip_codes = {code for code, _ in skipped}
    assert_true({"INSP-0002", "INSP-0003"} <= skip_codes,
                f"INSP-0002/0003 应跳过，实际跳过 {skip_codes}")

    # 2) 两人同时认领：先认领生效，第二人驳回且留下冲突提示，责任人不变
    entry, msg, ok = svc.run_action(ledger["id"], "认领", {"认领人": "赵敏"})
    assert_true(ok and entry["责任人"] == "赵敏" and entry["status"] == "已认领",
                "首次认领应生效")
    insp = store.find("inspect", ledger["巡查记录ID"])
    assert_true(insp["status"] == "已排班", "认领后巡查记录应保持已排班")
    # 排班责任人（张建国）与实际认领人（赵敏）不一致，应已留冲突
    assert_true(any("不一致" in c for c in entry["冲突提示"]),
                "认领人与排班不一致应留冲突提示")
    entry, msg, ok = svc.run_action(ledger["id"], "认领", {"认领人": "钱坤"})
    assert_true(not ok, "第二人认领应被驳回")
    assert_true(entry["责任人"] == "赵敏", "驳回后责任人不应改变")
    assert_true(any("两人同时认领" in c for c in entry["冲突提示"]),
                "两人同时认领应留下冲突提示")

    # 3) 开始巡查：台账/轨迹/巡查记录三处同一状态
    entry, _, ok = svc.run_action(ledger["id"], "开始巡查", {})
    assert_true(ok and entry["status"] == "巡查中", "开始巡查后台账应为巡查中")
    assert_true(insp["status"] == "巡查中",
                f"巡查记录应同步为巡查中，实际 {insp['status']}")
    track = svc.get_track(ledger["id"])
    assert_true(track["状态"] == "巡查中" == track["巡查记录状态"],
                "地图轨迹状态应与台账、巡查记录一致")

    # 4) 排班变化：巡查中换责任人，可调且留下冲突提示
    entry, msg, ok = svc.adjust(ledger["id"], {"责任人": "孙磊", "原因": "赵敏调休"})
    assert_true(ok and entry["责任人"] == "孙磊", "责任人应可调整")
    assert_true(any("巡查中变更责任人" in c for c in entry["冲突提示"]),
                "巡查中变更责任人应留冲突提示")

    # 5) 路段停用 + 路线顺序调整：停用留冲突，重排/移除后冲突记录留痕
    entry, _, ok = svc.adjust(ledger["id"], {"停用路段": "解放北路污水管段"})
    assert_true(ok, "路段停用应成功")
    stop = next(s for s in entry["路线"] if s["路段"] == "解放北路污水管段")
    assert_true(stop["停用"], "该路段应标记停用")
    assert_true(any("已停用" in c for c in entry["冲突提示"]),
                "路段停用应留下冲突提示")
    # 停用一个不存在的路段要报错
    _, msg, ok = svc.adjust(ledger["id"], {"停用路段": "不存在的路段"})
    assert_true(not ok, "停用不存在的路段应被拒绝")
    new_order = ["和平路雨水管段", "滨江中路合流管段"]  # 把停用段移出并换序
    entry, _, ok = svc.adjust(ledger["id"], {"路线顺序": new_order})
    assert_true(ok, "路线顺序调整应成功")
    assert_true([s["路段"] for s in entry["路线"]] == new_order,
                "路线顺序应已更新且停用段被移除")
    assert_true([s["seq"] for s in entry["路线"]] == [1, 2], "顺序号应重排")
    assert_true(any("移出路线" in log for log in entry["调整记录"]),
                "移除路段应留调整记录")
    # 调整轨迹仍然可读且状态一致
    track = svc.get_track(ledger["id"])
    assert_true(len(track["points"]) == 2 and track["状态"] == "巡查中",
                "轨迹应跟随调整后的路线")

    # 6) 完成巡查：三处状态全部已完成
    entry, _, ok = svc.run_action(ledger["id"], "完成巡查", {})
    assert_true(ok and entry["status"] == "已完成" and insp["status"] == "已完成",
                "完成后台账与巡查记录都应是已完成")
    _, _, ok = svc.adjust(ledger["id"], {"责任人": "谁都不行"})
    assert_true(not ok, "已完成台账不允许再调整")

    # 7) 巡查记录侧流转回写台账：新建一份台账，用巡查记录的动作推进
    created, _ = svc.generate()  # 没有新的可生成记录
    assert_true(not created, "无可生成记录时应返回空")
    # INSP-0001 已完成不能生成；直接对 INSP-0002 的预置台账走“巡查记录侧”不适用，
    # 改为造一条新巡查记录验证反向同步
    new_insp, missing = inspect_svc.create_entry(
        {"巡查编号": "INSP-0099", "巡查路段": "A路段、B路段", "巡查人员": "周武"}
    )
    assert_true(not missing, "新巡查记录应能登记")
    created, _ = svc.generate()
    assert_true(len(created) == 1 and created[0]["关联巡查编号"] == "INSP-0099",
                "新巡查记录应能生成台账")
    route99 = created[0]
    # 台账待认领时，巡查记录侧直接“开始巡查”，台账应跟随到巡查中
    _, m = inspect_svc.run_action(new_insp["id"], "开始巡查")
    assert_true("开始巡查" in m, "巡查记录动作应成功")
    assert_true(find_ledger("XJRT-0003")["status"] == "巡查中",
                "巡查记录侧推进时台账应同步为巡查中")

    # 8) 撤回：清空路线与轨迹，巡查记录退回已排班，再生成是新台账且旧台账留空档
    #    先用另一份待认领台账验证撤回
    new_insp2, _ = inspect_svc.create_entry(
        {"巡查编号": "INSP-0098", "巡查路段": "C路段、D路段", "巡查人员": "冯远"}
    )
    created, _ = svc.generate()
    route98 = created[0]
    assert_true(route98["status"] == "待认领", "新台账应为待认领")
    entry, _, ok = svc.run_action(route98["id"], "撤回", {})
    assert_true(ok and entry["status"] == "已撤回", "撤回应生效")
    assert_true(entry["路线"] == [], "撤回后路线必须清空，不得保留旧路线")
    track = svc.get_track(route98["id"])
    assert_true(track["points"] == [] and track["状态"] == "已撤回",
                "撤回后地图轨迹必须为空且状态为已撤回")
    insp98 = store.find("inspect", new_insp2["id"])
    assert_true(insp98["status"] == "已排班",
                "撤回后巡查记录应退回已排班以便重新排班")
    # 撤回后再打开（重新读），仍然拿不到旧路线
    again = svc.get_entry(route98["id"])
    assert_true(again["路线"] == [] and again["路线段数"] == 0,
                "撤回后重新打开不能保留旧路线")
    _, _, ok = svc.run_action(route98["id"], "开始巡查", {})
    assert_true(not ok, "已撤回台账不能再开始巡查")
    # 重新生成：旧台账留档，新台账拿到新编号、重新生成路线
    created, skipped = svc.generate()
    assert_true(len(created) == 1 and created[0]["关联巡查编号"] == "INSP-0098",
                "撤回后应允许重新生成台账")
    assert_true(created[0]["台账编号"] != route98["台账编号"],
                "重新生成应产生新台账编号")
    assert_true(len(created[0]["路线"]) == 2, "新台账应重新生成路线")
    old = find_ledger(route98["台账编号"])
    assert_true(old["status"] == "已撤回" and old["路线"] == [],
                "旧撤回台账必须保持空路线留档")

    # 9) 列表视图自带的巡查记录状态与台账一致
    items, total = svc.list_entries(page=1, size=100)
    for item in items:
        if item["状态"] == "已撤回":
            continue
        if item["状态"] == "已完成":
            assert_true(item["巡查记录状态"] in ("已完成", "已转病害"),
                        f"{item['台账编号']} 状态不一致：{item}")
        else:
            assert_true(item["巡查记录状态"] == item["状态"]
                        or (item["状态"] in ("待认领", "已认领")
                            and item["巡查记录状态"] == "已排班"),
                        f"{item['台账编号']} 三处状态不一致：{item}")

    print("全部断言通过 ✓")
    print("台账清单：", [(r["台账编号"], r["关联巡查编号"], r["责任人"], r["status"])
                    for r in store.rows(MODULE)])


if __name__ == "__main__":
    main()
