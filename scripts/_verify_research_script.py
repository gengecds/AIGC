"""临时验证：只跑 方案(research) + 剧本(script)，检查量化与风格是否落地。"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.style_resolver import set_active_style
from pipeline.scheduler import Pipeline
from agents.research_agent import ResearchAgent
from agents.script_agent import ScriptAgent


async def main():
    style = sys.argv[1] if len(sys.argv) > 1 else "写实风格"
    user_input = sys.argv[2] if len(sys.argv) > 2 else (
        "一支手机品牌宣传短剧：讲述一位年轻摄影师在都市雨夜用手机拍下动人瞬间的故事，"
        "传递'记录每一个瞬间'的品牌理念，风格偏电影级写实。"
    )
    set_active_style(style)

    pipe = Pipeline(pipeline_id=f"verify_{style}")
    agents = [
        ResearchAgent(llm_provider="ollama"),
        ScriptAgent(llm_provider="ollama"),
    ]
    result = await pipe.run(agents, user_input, resume=False, enable_review=False)

    def get(key):
        d = result.get("results", {}).get(key, {})
        return d.get("data", {}) if isinstance(d, dict) else {}

    print("\n================ 方案 research ================")
    print(json.dumps(get("research_agent"), ensure_ascii=False, indent=2))
    print("\n================ 剧本 script ================")
    print(json.dumps(get("script_agent"), ensure_ascii=False, indent=2))
    print("\nSUCCESS:", result.get("success"), "| FAILED_AT:", result.get("failed_at"), "| ERR:", result.get("error"))


asyncio.run(main())
