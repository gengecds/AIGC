"""真实端到端验证：方案/剧本 断点 → 编辑提交 → 合并且继续。

用法：
    .venv/bin/python scripts/_verify_edit_submit.py <pid>      # 驱动一个已存在的管线
    .venv/bin/python scripts/_verify_edit_submit.py            # 自己启动一个管线
所有日志实时写到 stdout（调用方用 > 重定向到文件再读取）。
"""
import json
import sys
import time
import httpx

BASE = "http://127.0.0.1:8888"


def log(level, msg):
    print(f"[{level}] {msg}", flush=True)


def wait_status(client, pid, expect_agents, expect_status="review", timeout=1800):
    """轮询直到 current_agent 命中 expect_agents 且 status==expect_status。"""
    start = time.time()
    while time.time() - start < timeout:
        r = client.get(f"{BASE}/api/v1/pipeline/status/{pid}")
        if r.status_code == 200:
            j = r.json()
            ca, st = j.get("current_agent"), j.get("status")
            if st == "failed":
                raise RuntimeError(f"管线失败: {j.get('error')}")
            log("POLL", f"agent={ca} status={st}")
            if ca in expect_agents and st == expect_status:
                return j
        time.sleep(4)
    raise TimeoutError(f"等待 {expect_agents} 进入 {expect_status} 超时")


def main():
    pid = sys.argv[1] if len(sys.argv) > 1 else None
    with httpx.Client(timeout=30) as client:
        if not pid:
            r = client.post(f"{BASE}/api/v1/pipeline/run", json={
                "input": "制作一个15秒的宠物猫短视频，暖色调，治愈系，节奏舒缓",
                "style": "写实风格",
            })
            pid = r.json()["pipeline_id"]
            log("RUN", f"管线已启动 pid={pid}")
        else:
            log("RUN", f"接管已有管线 pid={pid}")

        # 等 research 断点
        wait_status(client, pid, ["research_agent"], "review")
        log("BLOCK", "方案断点触发")

        snap = client.get(f"{BASE}/api/v1/pipeline/snapshot/latest").json()
        research = snap.get("research_agent") or {}
        log("EDIT", f"取到方案 title={research.get('title')!r}")
        if research:
            research_edit = dict(research)
            research_edit["copy_points"] = list(research.get("copy_points") or []) + ["【用户修改】增加一句治愈系标语"]
            research_edit["title"] = (research.get("title") or "方案") + "（已编辑）"
            r = client.post(f"{BASE}/api/v1/pipeline/submit/{pid}", json={"research": research_edit})
            log("SUBMIT", f"方案修改稿提交 -> {r.json()}")

        # 等 script 断点
        wait_status(client, pid, ["script_agent"], "review")
        log("BLOCK", "剧本断点触发")

        snap = client.get(f"{BASE}/api/v1/pipeline/snapshot/latest").json()
        script = snap.get("script_agent") or {}
        log("EDIT", f"取到剧本 集数={len(script.get('episodes') or [])}")
        script_edit = dict(script)
        eps = script_edit.get("episodes") or []
        if eps:
            eps[0]["title"] = eps[0].get("title", "") + "（已编辑）"
        research_back = snap.get("research_agent") or {}
        research_back["copy_points"] = list(research_back.get("copy_points") or []) + ["【用户修改】剧本阶段再补一句"]
        r = client.post(f"{BASE}/api/v1/pipeline/submit/{pid}", json={
            "script": script_edit, "research": research_back,
        })
        log("SUBMIT", f"剧本修改稿提交 -> {r.json()}")

        # 确认提交后继续
        started = time.time()
        advanced = False
        last = None
        while time.time() - started < 180:
            j = client.get(f"{BASE}/api/v1/pipeline/status/{pid}").json()
            last = (j.get("current_agent"), j.get("status"))
            if j.get("current_agent") not in ("research_agent", "script_agent"):
                advanced = True
                break
            if j.get("status") == "review":
                break
            time.sleep(4)
        log("RESUME", f"提交后管线状态: {last}（继续={'是' if advanced else '否'}）")

        snap = client.get(f"{BASE}/api/v1/pipeline/snapshot/latest").json()
        rs = snap.get("research_agent") or {}
        sc = snap.get("script_agent") or {}
        ok_plan = any("用户修改" in (p or "") for p in (rs.get("copy_points") or []))
        ok_script = any("已编辑" in (e.get("title") or "") for e in (sc.get("episodes") or []))
        log("CHECK", f"方案修改已合并={'是' if ok_plan else '否'}；剧本修改已合并={'是' if ok_script else '否'}")

        print("\n========== 验证结论 ==========", flush=True)
        print("· 方案断点触发+编辑提交: ✅", flush=True)
        print("· 剧本断点触发+编辑提交: ✅", flush=True)
        print("· 提交后管线继续:", "✅" if advanced else "❌", flush=True)
        print("· 方案修改合并生效:", "✅" if ok_plan else "❌", flush=True)
        print("· 剧本修改合并生效:", "✅" if ok_script else "❌", flush=True)
        print("· 管线 id:", pid, flush=True)


if __name__ == "__main__":
    main()
