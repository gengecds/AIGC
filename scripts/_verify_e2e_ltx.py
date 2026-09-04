#!/usr/bin/env python3
"""LTX-2.3 端到端验证驱动（一次性脚本）：POST /run → 自动批准每个审核断点 → 等 done/failed

目的：验证「视频引擎切换为 LTX-2.3 MLX 后整条真实管线仍能出成品」。
所有审核断点（research/script/storyboard/character/image/video）自动 approve。
用法：
    .venv/bin/python scripts/_verify_e2e_ltx.py [故事] [resume]
第一个可选参数为故事文本；第二个可选参数传 resume 表示从最近断点续跑
输出实时打印到 stdout（调用方重定向到文件）。
"""
import sys
import time
import httpx

BASE = "http://127.0.0.1:8888"

RESUME = len(sys.argv) > 2 and sys.argv[2] == "resume"

STORY = (
    sys.argv[1]
    if len(sys.argv) > 1
    else "深夜便利店，加班的女孩买完关东煮准备离开，发现外面下起大雨。"
         "店员小哥追出来把自己的伞递给她，两人在雨棚下相视一笑。"
)

# 允许超时：4 分镜 × 视频 13min + 出图等 ≈ 3 小时，给足余量
TIMEOUT = 3 * 3600


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main() -> None:
    with httpx.Client(timeout=30) as c:
        # 1) 启动管线
        r = c.post(f"{BASE}/api/v1/pipeline/run",
                   json={"input": STORY, "style": "写实风格",
                         "resume": RESUME})
        r.raise_for_status()
        pid = r.json()["pipeline_id"]
        log(f"管线已启动 pid={pid} resume={RESUME}")
        log(f"故事: {STORY[:60]}...")

        # 2) 轮询状态：review 自动批准，直到 done/failed/超时
        start = time.time()
        last_status = ""
        review_count = 0
        while time.time() - start < TIMEOUT:
            try:
                j = c.get(f"{BASE}/api/v1/pipeline/status/{pid}").json()
            except Exception as e:
                log(f"  状态查询失败(重试): {e}")
                time.sleep(5)
                continue

            st, cur, prog = j.get("status"), j.get("current_agent"), j.get("progress", 0)
            if (st, cur, prog) != last_status:
                log(f"  agent={cur} status={st} progress={prog}%")
                last_status = (st, cur, prog)

            if st == "failed":
                log(f"❌ 管线失败: {j.get('error', '')}")
                return
            if st == "done":
                log("✅ 管线完成!")
                summary = j.get("summary", {})
                for name, meta in summary.items():
                    log(f"   - {name}: {str(meta)[:100]}")
                return
            if st == "review":
                review_count += 1
                log(f"⏸️  审核断点({review_count}): {cur} — 自动批准")
                c.post(f"{BASE}/api/v1/pipeline/approve/{pid}")

            time.sleep(5)

        log("❌ 超时未完成")


if __name__ == "__main__":
    main()
