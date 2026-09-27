"""路线台账接口层测试：通过 FastAPI TestClient 走真实 HTTP 路由。

运行：PYTHONPATH=. python3 scripts/test_patrol_route_api.py
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def check(cond: object, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def main() -> None:
    # 健康检查与列表（种子里有 1 份 XJRT-0001）
    r = client.get("/api/health")
    check(r.status_code == 200, f"health 失败：{r.status_code}")
    r = client.get("/api/patrol_route")
    check(r.status_code == 200, f"列表失败：{r.status_code} {r.text}")
    data = r.json()
    check(data["total"] == 1 and data["items"][0]["台账编号"] == "XJRT-0001",
          f"种子台账异常：{data}")
    check(data["items"][0]["巡查记录状态"] == "巡查中"
          == data["items"][0]["状态"], "种子台账与巡查记录状态应一致")

    # 非法状态过滤应 400
    r = client.get("/api/patrol_route", params={"status": "乱填"})
    check(r.status_code == 400, "非法状态应返回 400")

    # 生成
    r = client.post("/api/patrol_route/generate")
    check(r.status_code == 200 and r.json()["ok"], f"生成失败：{r.text}")
    created = r.json()["entry"]["created"]
    check(len(created) == 1 and created[0]["台账编号"] == "XJRT-0002",
          f"应只生成 XJRT-0002：{r.json()}")
    ledger_id = created[0]["id"]

    # /{id}/track 不能被 /{id} 抢匹配；静态路径优先
    r = client.get(f"/api/patrol_route/{ledger_id}/track")
    check(r.status_code == 200 and len(r.json()["points"]) == 3,
          f"轨迹接口异常：{r.status_code} {r.text}")
    r = client.get("/api/patrol_route/export")
    check(r.status_code == 200 and r.json()["total"] >= 2,
          f"导出接口异常（说明静态路由被动态路由抢占）：{r.status_code} {r.text}")

    # 详情
    r = client.get(f"/api/patrol_route/{ledger_id}")
    check(r.status_code == 200 and r.json()["状态"] == "待认领", f"详情异常：{r.text}")

    # 认领（正确的 payload 形态：{values: {...}}）
    r = client.post(f"/api/patrol_route/{ledger_id}/actions",
                    json={"values": {"action": "认领", "认领人": "赵敏"}})
    body = r.json()
    check(body["ok"] and body["entry"]["状态"] == "已认领", f"认领失败：{body}")
    # 同一人重复认领：ok=False
    r = client.post(f"/api/patrol_route/{ledger_id}/actions",
                    json={"values": {"action": "认领", "认领人": "赵敏"}})
    check(not r.json()["ok"], "重复认领应返回 ok=false")
    # 两人同时认领：驳回 + 冲突留痕
    r = client.post(f"/api/patrol_route/{ledger_id}/actions",
                    json={"values": {"action": "认领", "认领人": "钱坤"}})
    body = r.json()
    check(not body["ok"] and "两人同时认领" in body["message"]
          and any("两人同时认领" in c for c in body["entry"]["冲突提示"]),
          f"双认领应留冲突：{body}")

    # 开始巡查后，台账、轨迹、巡查记录明细同状态
    r = client.post(f"/api/patrol_route/{ledger_id}/actions",
                    json={"values": {"action": "开始巡查"}})
    check(r.json()["ok"], f"开始巡查失败：{r.text}")
    ledger = client.get(f"/api/patrol_route/{ledger_id}").json()
    track = client.get(f"/api/patrol_route/{ledger_id}/track").json()
    inspect_id = ledger["巡查记录ID"]
    inspect_row = client.get(f"/api/inspect/{inspect_id}").json()
    check(ledger["状态"] == track["状态"] == inspect_row["status"] == "巡查中",
          f"三处状态不一致：{ledger['状态']}/{track['状态']}/{inspect_row['status']}")

    # 排班变化 + 路段停用 + 重排
    r = client.put(f"/api/patrol_route/{ledger_id}/adjust",
                   json={"values": {"责任人": "孙磊", "原因": "赵敏调休"}})
    check(r.json()["ok"] and "排班变化" in r.json()["message"], f"换责任人失败：{r.text}")
    r = client.put(f"/api/patrol_route/{ledger_id}/adjust",
                   json={"values": {"停用路段": "解放北路污水管段"}})
    check(r.json()["ok"], f"停用失败：{r.text}")
    r = client.put(f"/api/patrol_route/{ledger_id}/adjust",
                   json={"values": {"路线顺序": ["和平路雨水管段", "滨江中路合流管段"]}})
    body = r.json()
    check(body["ok"] and len(body["entry"]["路线"]) == 2, f"重排失败：{body}")

    # 巡查记录侧动作反向同步：完成巡查记录 → 台账完成
    r = client.post(f"/api/inspect/{inspect_id}/actions",
                    json={"values": {"action": "完成巡查"}})
    check(r.json()["ok"], f"巡查记录完成失败：{r.text}")
    ledger = client.get(f"/api/patrol_route/{ledger_id}").json()
    check(ledger["状态"] == "已完成" == ledger["巡查记录状态"],
          f"巡查记录侧完成后台账应同步：{ledger['状态']}/{ledger['巡查记录状态']}")
    # 已完成不允许调整
    r = client.put(f"/api/patrol_route/{ledger_id}/adjust",
                   json={"values": {"责任人": "X"}})
    check(not r.json()["ok"], "已完成台账不允许调整")

    # 撤回流程：新建巡查记录→生成→撤回→路线清空→重新生成
    r = client.post("/api/inspect",
                    json={"values": {"巡查编号": "INSP-0100",
                                     "巡查路段": "E路段、F路段",
                                     "巡查人员": "冯远"}})
    check(r.json()["ok"], f"登记巡查记录失败：{r.text}")
    r = client.post("/api/patrol_route/generate")
    rid = r.json()["entry"]["created"][0]["id"]
    r = client.post(f"/api/patrol_route/{rid}/actions",
                    json={"values": {"action": "撤回"}})
    check(r.json()["ok"], f"撤回失败：{r.text}")
    detail = client.get(f"/api/patrol_route/{rid}").json()
    track = client.get(f"/api/patrol_route/{rid}/track").json()
    check(detail["路线"] == [] and detail["状态"] == "已撤回"
          and track["points"] == [] and track["状态"] == "已撤回"
          and detail["巡查记录状态"] == "已排班",
          "撤回后路线/轨迹必须清空，巡查记录退回已排班")
    # 重新生成 → 新台账，旧的保持空路线留档
    r = client.post("/api/patrol_route/generate")
    created = r.json()["entry"]["created"]
    check(len(created) == 1 and len(created[0]["路线"]) == 2
          and created[0]["台账编号"] != detail["台账编号"],
          f"撤回后应能重新生成新台账：{r.json()}")

    print("接口层全部断言通过 ✓")


if __name__ == "__main__":
    main()
