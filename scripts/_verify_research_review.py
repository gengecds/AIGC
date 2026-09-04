"""临时验证：方案(research)断点 编辑→提交→合并覆盖→继续到剧本 的完整链路。

用 Mock LLM 快速跑通，不需等 Ollama。
- 在 research_approval 断点处调用 submit_edit 修改标题
- 确认：①结果 data 含修改字段 ②checkpoint 更新 ③管线继续到 script
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.style_resolver import set_active_style
from pipeline.scheduler import Pipeline
from agents.research_agent import ResearchAgent
from agents.script_agent import ScriptAgent
from providers.base import LLMProvider

RESEARCH_MOCK = {
    "title": "雨夜瞬影：手机摄影的都市诗篇",
    "target_audience": "18-35 都市年轻人",
    "style_direction": "写实风格 · 电影级张雨夜",
    "narrative_structure": "温情悬疑：雨夜偶遇 → 手机留痕 → 温情反转",
    "key_words": ["写实", "电影级光影", "35mm"],
    "render_engine_words": ["虚幻引擎5.3 Lumen 全局光照", "Nanite 虚拟几何体"],
    "lighting_words": ["光线追踪反射", "路径追踪"],
    "camera_words": ["35mm, f/2.8, 浅景深"],
    "copy_points": ["记录每一个瞬间", "雨夜也清晰"],
    "bgm_mood": "温馨 · 慢节奏钢琴",
    "scene_sounds": ["雨滴声", "车流声"],
    "candidate_words": ["二次曝光"],
}

SCRIPT_MOCK = {
    "title": "雨夜瞬影",
    "characters": [
        {"name": "林深", "appearance": "黑色短发微卷，穿深灰冲锋衣", "personality": "专注", "role": "主角"}
    ],
    "episodes": [
        {"episode_number": 1, "title": "雨夜初遇", "plot": "摄影师的雨夜记录",
         "dialogues": [
             {"scene": "23:47 雨夜街道", "character": "林深", "line": "这雨下得真邪乎。"}
         ]}
    ],
}


class VerifyLLM(LLMProvider):
    async def generate(self, prompt, system_prompt=None, model=None, **kwargs) -> str:
        if system_prompt and "方案" in system_prompt:
            return json.dumps(RESEARCH_MOCK, ensure_ascii=False, indent=2)
        return json.dumps(SCRIPT_MOCK, ensure_ascii=False, indent=2)

    async def chat(self, messages, model=None, **kwargs) -> str:
        sys_msg = next((m.get("content", "") for m in messages if m.get("role") == "system"), "")
        if "方案" in sys_msg:
            return json.dumps(RESEARCH_MOCK, ensure_ascii=False, indent=2)
        return json.dumps(SCRIPT_MOCK, ensure_ascii=False, indent=2)


async def main():
    set_active_style("写实风格")
    pipe = Pipeline(pipeline_id="verify_review")
    agents = [ResearchAgent(llm_provider=VerifyLLM()), ScriptAgent(llm_provider=VerifyLLM())]

    done = asyncio.Event()
    submitted = False

    async def operator():
        nonlocal submitted
        # 轮询等待断点触发，直到管线完成
        while not done.is_set():
            if pipe._paused:
                reason = pipe._review_data.get("reason", "")
                if reason == "research_approval" and not submitted:
                    submitted = True
                    print("  [operator] 检测到 research 断点，正在提交修改稿…")
                    edited = dict(RESEARCH_MOCK)
                    edited["title"] = "【用户修改】雨夜瞬影 v2"
                    edited["style_direction"] = "写实 · 用户自定义方向"
                    pipe.submit_edit({"research": edited})
                else:
                    print(f"  [operator] 断点 {reason}，直接放行")
                    pipe.approve_review()
            await asyncio.sleep(0.2)

    task = asyncio.create_task(pipe.run(agents, "测试需求", resume=False, enable_review=True))
    op = asyncio.create_task(operator())
    result = await task
    done.set()
    await op

    research = result["results"]["research_agent"]["data"]
    script = result["results"]["script_agent"]["data"]

    print("\n===== 校验结果 =====")
    ok = True
    if research.get("title") == "【用户修改】雨夜瞬影 v2":
        print("  ✅ 方案标题已被用户修改覆盖")
    else:
        ok = False
        print(f"  ❌ 方案标题未覆盖，当前={research.get('title')}")
    if research.get("style_direction") == "写实 · 用户自定义方向":
        print("  ✅ 方案风格方向已被用户修改覆盖")
    else:
        ok = False
        print(f"  ❌ 风格方向未覆盖，当前={research.get('style_direction')}")
    if research.get("camera_words") == ["35mm, f/2.8, 浅景深"]:
        print("  ✅ 方案未改动字段保留（合并而非整体替换）")
    else:
        ok = False
        print(f"  ❌ 合并丢失未改动字段：{research.get('camera_words')}")
    if script.get("title"):
        print("  ✅ 断点放行后管线继续执行到剧本")
    else:
        ok = False
        print("  ❌ 管线未继续到剧本")
    if pipe.state.load_checkpoint("research_agent").get("data", {}).get("title") == "【用户修改】雨夜瞬影 v2":
        print("  ✅ 方案 checkpoint 已同步用户修改")
    else:
        ok = False
        print("  ❌ 方案 checkpoint 未同步用户修改")

    print("\nSUCCESS:", ok, "| pipeline_done:", result.get("success"))
    sys.exit(0 if ok else 1)


asyncio.run(main())
