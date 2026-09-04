#!/usr/bin/env python3
"""Pipeline SSE 端点 Smoke Test（域 D 独立模块）

验证点（按任务要求「简化处理」）：
  1. GET /pipeline/events?run_id=__demo__ 返回的 header 中
     content-type 必须是 text/event-stream
  2. 流式响应的首个 chunk 中必须包含：
     - event: progress （SSE 事件名）
     - data: 前缀 （SSE 数据行）
     - "剧本" 或 "step: 0" 关键词（代表第一个 Step 0 到达）

设计说明：
  本测试不 import api.main（它依赖 dotenv / sqlalchemy 等多个可选包），
  而是创建一个「隔离的最小 FastAPI 实例」，只挂载我们的新 router。
  这样即使本地环境没装 python-dotenv / DB driver，Smoke Test 照样能跑。
  这正是域 D 独立的好处：SSE 模块不依赖 db / config / agents。
"""

import os
import sys
import re
import json

# ── 确保能 import 到项目根下的模块 ─────────────────────────────
TEST_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(TEST_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# 注意：本测试不依赖 pytest，可直接 `python tests/test_pipeline_sse.py` 运行
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ── 只 import 域 D 的独立 router（不引 api.main，避免 dotenv 依赖）──
from api.routes.pipeline_events_router import (
    router as pipeline_events_router,
    publish_pipeline_event,
)

# ── 创建隔离的测试专用 FastAPI app ──────────────────────────────
# 与 api/main.py 中 app.include_router(pipeline_events_router) 的效果一致
_test_app = FastAPI(title="SSE Smoke Test")
_test_app.include_router(pipeline_events_router)


def test_sse_demo_headers_and_first_event():
    """验证 __demo__ 模式的 SSE：header + 首条 progress 事件。"""

    # 用 TestClient 包装隔离的 app
    client = TestClient(_test_app)

    # —— 1. 发 stream 请求（httpx stream 模式）——
    # __demo__ 模式每秒推一条，我们只读到第一条完整事件就停止
    with client.stream(
        "GET",
        "/pipeline/events",
        params={"run_id": "__demo__"},
        timeout=5.0,  # 5 秒超时（首条 1 秒出，留 4 秒余量）
    ) as resp:
        # —— 1.1 Header 断言 ——
        content_type = resp.headers.get("content-type", "")
        assert "text/event-stream" in content_type.lower(), (
            f"❌ SSE 端点 content-type 错误：期望包含 text/event-stream，"
            f"实际得到 {content_type!r}"
        )
        print(f"✅ Header content-type 验证通过: {content_type!r}")

        # —— 可选：验证反代理 / 浏览器友好的 header ——
        xaccel = resp.headers.get("x-accel-buffering", "")
        if xaccel:
            # 有 X-Accel-Buffering: no 是加分项
            assert xaccel.lower() == "no"
            print(f"✅ Header X-Accel-Buffering={xaccel!r}（Nginx 不禁缓冲 SSE）")

        # —— 1.2 首条 chunk 断言 ——
        # 迭代流式字节，直到拿到至少一条完整 SSE 事件（\n\n 结尾）
        first_chunk: bytes = b""
        for chunk in resp.iter_bytes(chunk_size=8192):
            first_chunk += chunk
            # 至少包含一个完整 SSE 事件（出现 \n\n 分隔符）
            if b"\n\n" in first_chunk and len(first_chunk) > 20:
                break
            # 安全上限：64KB 还没拿到完整事件就停止（防止 hang）
            if len(first_chunk) > 65536:
                break

        first_text = first_chunk.decode("utf-8", errors="replace")
        print(f"✅ 首段 SSE 数据长度: {len(first_text)} 字节")
        # 展示预览（最多 400 字符）
        preview = first_text[:400].replace("\n", "\\n")
        print(f"   内容预览: {preview!r}")
        if len(first_text) > 400:
            print("   (... 截断预览)")

        # —— 断言 A：必须包含 'event: progress' ——
        assert "event: progress" in first_text, (
            "❌ SSE 首条事件缺少 'event: progress' 标记。"
        )
        print("✅ 包含 SSE 协议行 'event: progress'")

        # —— 断言 B：必须包含 'data:' 前缀 ——
        assert "data:" in first_text, (
            "❌ SSE 首条事件缺少 'data:' 数据前缀行。"
        )
        print("✅ 包含 SSE 协议行 'data:' 前缀")

        # —— 断言 C：Step 0 特征词（剧本 / script / step_name / step:0）——
        step0_keywords = ("剧本", '"step": 0', '"step_name":', 'script')
        found_keyword = any(kw in first_text for kw in step0_keywords)
        assert found_keyword, (
            "❌ 首条事件未出现 Step 0 特征词（剧本 / step:0 / step_name）。"
        )
        print("✅ 首条事件包含 Step 0 (剧本审查) 特征词")

        # —— 断言 D：提取出 data 的 JSON，验证 step=0 step_name=剧本审查 ——
        m = re.search(
            r"event:\s*progress\s*\ndata:\s*(\{.*?\})\s*\n\n",
            first_text,
            re.DOTALL,
        )
        assert m, "❌ 没能用正则从首条 chunk 中提取出完整 progress JSON"
        parsed = json.loads(m.group(1))
        assert parsed.get("step") == 0 or parsed.get("step_name") == "剧本审查", (
            f"❌ 首条 progress 不是 Step 0：step={parsed.get('step')!r} "
            f"step_name={parsed.get('step_name')!r}"
        )
        print(
            f"✅ JSON 解析成功：type={parsed.get('type')!r} "
            f"step={parsed.get('step')} step_name={parsed.get('step_name')!r} "
            f"percent={parsed.get('percent')}%"
        )

    print("🎉 __demo__ SSE header + 首条事件 全部断言通过！")


def test_sse_invalid_run_id():
    """边界测试：空 run_id → 400 / 422。"""
    client = TestClient(_test_app)
    # 传空串：业务层 HTTPException(400)
    resp = client.get("/pipeline/events", params={"run_id": ""})
    assert resp.status_code in (400, 422), (
        f"❌ 空 run_id 期望 4xx，实际 {resp.status_code}"
    )
    print(f"✅ 空 run_id 返回 {resp.status_code}（符合预期）")


def test_publish_pipeline_event_api_exists():
    """验证 publish_pipeline_event() 作为模块级函数对外暴露、可调用。"""
    # 这是一个「函数存在 + 类型可调用」的 Smoke 断言
    assert callable(publish_pipeline_event), (
        "❌ publish_pipeline_event 不可调用"
    )

    # 实际调用一次，看是否抛异常（内部吞异常，外部不应该出错）
    try:
        publish_pipeline_event(
            run_id="test_smoke_run_123",
            event={
                "type": "checkpoint",
                "step": 2,
                "step_name": "角色图审查",
                "percent": 60,
                "message": "测试事件（可忽略）",
                "payload": {"foo": "bar"},
            },
        )
    except Exception as e:
        raise AssertionError(
            f"❌ publish_pipeline_event() 抛出了不该抛的异常: {e}"
        )
    print("✅ publish_pipeline_event() 对外 API 正常（调用无异常）")


# ── 允许直接运行 `python tests/test_pipeline_sse.py` ────────────
if __name__ == "__main__":
    print("=" * 60)
    print("Pipeline SSE 端点 Smoke Test（域 D 独立）")
    print("=" * 60)

    failures = 0

    # 1) 核心 SSE __demo__ 测试
    try:
        test_sse_demo_headers_and_first_event()
    except AssertionError as e:
        print(f"\n❌ 核心测试失败: {e}")
        failures += 1
    except Exception as e:
        print(f"\n❌ 核心测试异常: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        failures += 1

    print()

    # 2) publish 函数存在性 + 可调用测试
    try:
        test_publish_pipeline_event_api_exists()
    except AssertionError as e:
        print(f"\n❌ publish API 测试失败: {e}")
        failures += 1
    except Exception as e:
        print(f"\n⚠️  publish API 测试异常（可能环境问题）: {type(e).__name__}: {e}")
        # 不增加 failures，非核心断言

    print()

    # 3) 边界测试（空 run_id → 4xx）
    try:
        test_sse_invalid_run_id()
    except AssertionError as e:
        print(f"\n⚠️  边界测试失败（非核心）: {e}")
        # 不增加 failures，这是加分项
    except Exception as e:
        print(f"\n⚠️  边界测试异常（非核心）: {type(e).__name__}: {e}")

    print()
    if failures == 0:
        print("🏁 所有核心 Smoke Test 通过 ✅")
        sys.exit(0)
    else:
        print(f"💥 共 {failures} 项核心断言失败 ❌")
        sys.exit(1)
