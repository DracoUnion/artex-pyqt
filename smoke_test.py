"""快速冒烟测试：起后端并打一组 API。"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

from app.backend.server import create_app

DATA_DIR = os.path.join(tempfile.mkdtemp(prefix="artex-test-"), "data")
SKILLS_DIR = os.path.join(tempfile.mkdtemp(prefix="artex-test-"), "skills")

app = create_app(DATA_DIR, SKILLS_DIR)

with TestClient(app) as client:
    r = client.get("/api/health")
    print("health:", r.status_code, r.json())

    r = client.get("/api/auth/status")
    print("auth/status:", r.status_code, r.json())

    r = client.post("/api/auth/init", json={"password": "testpass123"})
    print("auth/init:", r.status_code, "token?" , "token" in r.json())
    TOKEN = r.json()["token"]
    H = {"Authorization": f"Bearer {TOKEN}"}

    r = client.post("/api/auth/login", json={"username": "ARTEX", "password": "testpass123"})
    print("auth/login:", r.status_code)
    TOKEN = r.json().get("token") or TOKEN
    H = {"Authorization": f"Bearer {TOKEN}"}

    r = client.get("/api/agents", headers=H)
    print("agents:", r.status_code, [a["key"] for a in r.json()])

    r = client.get("/api/tools", headers=H)
    print("tools:", r.status_code, len(r.json()))

    r = client.get("/api/llm/profiles", headers=H)
    print("llm/profiles:", r.status_code, r.json())

    # 创建任务
    r = client.post("/api/tasks", headers=H,
                    json={"name": "演示任务", "description": "扫描 demo.test.example 的暴露面",
                          "goal": "对 demo.test.example 做一次基础渗透测试"})
    print("create task:", r.status_code)
    if r.status_code == 200:
        task = r.json()
        print("  task id:", task["id"], "status:", task["status"])
        tid = task["id"]

        import time
        time.sleep(6)
        r = client.get(f"/api/tasks/{tid}", headers=H)
        t = r.json()
        print("  after run:", t["status"], "in_flight:", t["in_flight"],
              "findings:", t["findings"], "goals_met:", t["goals_met"])

        r = client.get(f"/api/exploration/graph?task={tid}", headers=H)
        g = r.json()
        print("  graph nodes:", len(g["nodes"]), "edges:", len(g["edges"]))
        kinds = {}
        for n in g["nodes"]:
            kinds[n["type"]] = kinds.get(n["type"], 0) + 1
        print("  kinds:", kinds)

        r = client.get(f"/api/exploration/activity?task={tid}", headers=H)
        print("  activity items:", len(r.json().get("items", [])))
        r = client.get(f"/api/exploration/findings?task={tid}", headers=H)
        print("  findings:", len(r.json()) if isinstance(r.json(), list) else r.json())

        r = client.get(f"/api/report?task={tid}", headers=H)
        print("  report len:", len(r.text))

    # 会话
    r = client.post("/api/conversations", headers=H, json={"agent_key": "assistant", "title": "测试会话"})
    print("create conv:", r.status_code)
    if r.status_code == 200:
        cid = r.json()["id"]
        r = client.post(f"/api/conversations/{cid}/messages", headers=H, json={"message": "你好"})
        print("  send msg:", r.status_code)
        time.sleep(2)
        r = client.get(f"/api/conversations/{cid}/messages", headers=H)
        print("  messages:", len(r.json().get("items", [])))

    r = client.get("/api/assets", headers=H)
    print("assets:", r.status_code, len(r.json()))
    r = client.get("/api/traffic", headers=H)
    print("traffic:", r.status_code)

print("SMOKE OK")