"""快速冒烟测试：直接装配后端 services（进程内）并打一组调用。"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.gateway import Gateway, GatewayError

DATA_DIR = os.path.join(tempfile.mkdtemp(prefix="artex-test-"), "data")
SKILLS_DIR = os.path.join(tempfile.mkdtemp(prefix="artex-test-"), "skills")

g = Gateway(DATA_DIR, SKILLS_DIR)


def call(method, path, params=None, json=None):
    try:
        return g.dispatch(method, path, params=params, body=json)
    except GatewayError as e:
        print(f"  !! {method} {path} -> {e.status} {e.message}")
        return None


print("health:", call("GET", "/api/health"))
print("auth/status:", call("GET", "/api/auth/status"))

tok = call("POST", "/api/auth/init", json={"password": "testpass123"})
print("auth/init token?", "token" in (tok or {}))
tok2 = call("POST", "/api/auth/login", json={"username": "ARTEX", "password": "testpass123"})
print("auth/login token?", "token" in (tok2 or {}))

agents = call("GET", "/api/agents")
print("agents:", len(agents) if agents else None, [a["key"] for a in (agents or [])])

tools = call("GET", "/api/tools")
print("tools:", len(tools) if tools else None)

profs = call("GET", "/api/llm/profiles")
print("llm/profiles:", profs)

task = call("POST", "/api/tasks", json={
    "name": "演示任务", "description": "扫描 demo.test.example 的暴露面",
    "goal": "对 demo.test.example 做一次基础渗透测试",
})
print("create task:", task and (task.get("id"), task.get("status")))
if task:
    tid = task["id"]
    time.sleep(6)
    t = call("GET", f"/api/tasks/{tid}")
    print("  after run:", t and (t["status"], t["in_flight"], t["findings"], t["goals_met"]))

    gd = call("GET", "/api/exploration/graph", params={"task": tid})
    print("  graph nodes/edges:", gd and (len(gd["nodes"]), len(gd["edges"])))

    act = call("GET", "/api/exploration/activity", params={"task": tid})
    print("  activity items:", len((act or {}).get("items", [])))

    fds = call("GET", "/api/exploration/findings", params={"task": tid})
    print("  findings:", len(fds) if isinstance(fds, list) else fds)

    rep = call("GET", "/api/report", params={"task": tid})
    print("  report len:", len(rep or ""))

conv = call("POST", "/api/conversations", json={"agent_key": "assistant", "title": "测试会话"})
print("create conv:", conv and conv.get("id"))
if conv:
    cid = conv["id"]
    call("POST", f"/api/conversations/{cid}/messages", json={"message": "你好"})
    time.sleep(2)
    msgs = call("GET", f"/api/conversations/{cid}/messages")
    print("  messages:", len((msgs or {}).get("items", [])))

print("assets:", len(call("GET", "/api/assets") or []))
print("traffic:", call("GET", "/api/traffic"))

g.close()
print("SMOKE OK")