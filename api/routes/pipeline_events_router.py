"""Pipeline SSE 事件流路由（域 D 独立模块）

提供 GET /pipeline/events?run_id=xxx 端点，前端通过 EventSource 订阅实时进度。
设计目标：
  1. 纯内存 asyncio 队列 + Event 通知，不依赖 Redis / MQ（单进程部署够用）
  2. 暴露 publish_pipeline_event() 全局函数，任意 pipeline step 完成后可直接调用
  3. 支持 __demo__ 模式：订阅后自动按 1 秒间隔推送 5 个假进度（便于前端联调）
  4. 每 10 秒发送 :keepalive 注释，防止浏览器 / 反向代理断线
  5. 客户端断开后自动清理订阅引用，避免内存泄漏
"""

import asyncio
import json
import time
import logging
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)

# ── 路由器定义 ────────────────────────────────────────────────
# 统一前缀 /pipeline，和其他 pipeline 域接口对齐
router = APIRouter(prefix="/pipeline", tags=["pipeline"])


# ── 内存事件总线（模块级单例） ────────────────────────────────
# 结构：
#   _EVENTS[run_id] = {
#       "history": list[dict],     # 事件历史，新订阅者可一次性回放
#       "event": asyncio.Event,    # 有新消息时 set，订阅者 await 后 clear
#   }
# 注意：单进程部署安全；多 workers / 多进程部署需换 Redis Pub/Sub。
_EVENTS: dict[str, dict] = {}


def _ensure_run(run_id: str) -> dict:
    """确保某个 run_id 的事件槽存在，不存在则新建并返回。"""
    if run_id not in _EVENTS:
        _EVENTS[run_id] = {
            "history": [],
            "event": asyncio.Event(),
        }
    return _EVENTS[run_id]


# ── 对外发布函数（其他模块 import 后直接调用） ─────────────────
def publish_pipeline_event(run_id: str, event: dict) -> None:
    """向所有订阅 run_id 的 SSE 客户端推送一条事件。

    Args:
        run_id: 管线运行唯一标识（pipeline_id / job_id 均可）
        event: 事件字典，固定字段约定：
            {
              "type": "progress" | "checkpoint" | "done" | "error",
              "step": int,                 # 0-4 对应 script/storyboard/image/video/final
              "step_name": str,            # 人类可读步骤名
              "percent": int,              # 0-100 整体进度
              "message": str,              # 前端可显示的提示文字
              "payload": dict | None,      # checkpoint 审查数据（剧本、分镜、图片 URL 等）
              "ts": int                    # Unix 毫秒时间戳，调用方不填则自动补
            }
    """
    try:
        # 补齐时间戳（毫秒），调用方忘记传也不会报错
        if "ts" not in event or not event.get("ts"):
            event["ts"] = int(time.time() * 1000)

        slot = _ensure_run(run_id)
        slot["history"].append(event)
        # 历史上限 500 条，防止长期运行的 run 把内存吃爆
        if len(slot["history"]) > 500:
            slot["history"] = slot["history"][-500:]

        # 通知所有正在 await 的订阅者（set 之后会被各自订阅者在循环中 clear）
        # 因为 asyncio.Event 是一对多的，所有 waiter 都会被唤醒
        slot["event"].set()

        logger.debug(f"[SSE] 发布事件 run={run_id} type={event.get('type')} "
                     f"step={event.get('step')} pct={event.get('percent')}%")
    except Exception as e:
        # 任何异常都吞掉，绝不影响主进程 / 主 pipeline 流程
        logger.warning(f"[SSE] publish_pipeline_event 发布失败，已忽略: {e}")


# ── 事件格式化工具 ────────────────────────────────────────────
def _format_sse(event_type: str, data: dict) -> str:
    """把事件字典格式化为 SSE 标准协议文本。

    SSE 协议要求：
        event: <事件名>\n
        data: <JSON 字符串>\n
        \n  (空行分隔一条完整事件)
    """
    data_json = json.dumps(data, ensure_ascii=False)
    return f"event: {event_type}\ndata: {data_json}\n\n"


# ── __demo__ 模式：按 1 秒间隔依次推送 5 个假进度 ─────────────
# 前端无需等真实 pipeline，打开页面立即能看到进度条推进 + 5 张卡片填充
_DEMO_STEPS = [
    {"type": "progress",   "step": 0, "step_name": "剧本审查",   "percent": 20,
     "message": "LLM 已生成剧本初稿，等待人工审查",
     "payload": {
         "title": "《星海迷航》第一幕",
         "script": (
             "【场景一】漆黑的宇宙深处，一艘孤独的飞船缓缓前行。\n"
             "舰长林峰（30 岁，眼神坚毅）站在舰桥窗前，凝视着远方的星云。\n"
             "林峰（自语）：「已经三个月了……信号源到底是什么？」\n"
             "副官苏晴（26 岁，冷静干练）快步走来：「舰长，探测器捕捉到异常能量波动，距离我们 0.3 光年。」\n"
             "林峰转身，语气坚定：「调整航向，全速前进。不管那是什么，我们必须搞清楚。」\n"
             "【场景二】飞船靠近一片紫色星云，警报声骤然响起。屏幕上出现一艘形状扭曲的外星舰船。\n"
             "苏晴：「舰长！对方锁定了我们！」\n"
             "林峰：「启动防护罩，武器待命……先别开火，尝试通讯。」\n"
         ),
     }},
    {"type": "progress",   "step": 1, "step_name": "分镜审查",   "percent": 40,
     "message": "分镜 Agent 输出 2 个关键镜头，等待确认",
     "payload": {
         "shots": [
             {
                 "shot_id": "S01_001",
                 "景别": "远景 (LS)",
                 "对白": "",
                 "prompt": "Deep space, a solitary spaceship drifts among purple nebula clouds, cinematic wide shot, volumetric lighting, 8K, photorealistic, sci-fi mood",
                 "duration_sec": 4,
             },
             {
                 "shot_id": "S01_002",
                 "景别": "中景 (MS)",
                 "对白": "「已经三个月了……信号源到底是什么？」",
                 "prompt": "Captain Lin Feng, 30s Asian male, determined eyes, stands on spaceship bridge looking out window, soft blue console light on face, cinematic medium shot, shallow depth of field",
                 "duration_sec": 5,
             },
         ],
     }},
    {"type": "progress",   "step": 2, "step_name": "角色图审查", "percent": 60,
     "message": "ComfyUI 生成 4 张角色定妆照，等待分配角色名",
     "payload": {
         "characters": [
             {"image_seed": "linfeng_captain",  "suggested_name": "林峰（舰长）"},
             {"image_seed": "suqing_officer",   "suggested_name": "苏晴（副官）"},
             {"image_seed": "alien_commander",  "suggested_name": "外星指挥官"},
             {"image_seed": "robot_companion",  "suggested_name": "AI 机器人小七"},
         ],
     }},
    {"type": "progress",   "step": 3, "step_name": "视频预览",   "percent": 80,
     "message": "图生视频 + 字幕合成完成，等待预览",
     "payload": {
         "video_url": "https://www.w3schools.com/html/mov_bbb.mp4",
         "duration_sec": 32,
         "has_subtitle": True,
         "resolution": "1920x1080",
     }},
    {"type": "progress",   "step": 4, "step_name": "最终发布",   "percent": 100,
     "message": "漫剧成片打包完成，可导出或下载",
     "payload": {
         "title": "《星海迷航》第一集：信号之谜",
         "total_duration": "32 秒",
         "scenes": 2,
         "output_size": "1920x1080 H.264",
         "file_size": "48.2 MB",
         "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
     }},
    {"type": "done",       "step": 4, "step_name": "完成",       "percent": 100,
     "message": "🎉 管线全部完成！",
     "payload": None},
]


async def _demo_streamer():
    """__demo__ 模式的异步生成器：依次推送 5 个进度 + 最终 done。"""
    for demo_event in _DEMO_STEPS:
        # 每步等待 1 秒（注意：done 前也要等，让前端有时间渲染最后一步卡片）
        await asyncio.sleep(1.0)
        # 补齐 ts
        event = dict(demo_event)
        if "ts" not in event:
            event["ts"] = int(time.time() * 1000)
        yield _format_sse(event["type"], event)
    # 保持连接一段时间再结束，让前端 done 动画完整播
    await asyncio.sleep(2.0)
    # 最后发一条注释型的 end-of-stream 标识（前端 EventSource 会自动重连，
    # 用自定义 event: end 让前端主动 close）
    yield _format_sse("end", {"reason": "demo_completed"})


# ── 主订阅生成器（真实 run_id） ───────────────────────────────
async def _real_streamer(run_id: str, request: Request):
    """真实 run_id 订阅：回放历史 → 增量推送 → keepalive → 断开清理。"""
    slot = _ensure_run(run_id)

    # 1) 先把已有历史一次性回放（新连上的客户端不会错过之前的进度）
    for ev in list(slot["history"]):
        yield _format_sse(ev.get("type", "progress"), ev)

    # 2) 进入增量推送循环
    last_event_idx = len(slot["history"])
    keepalive_interval = 10.0  # 10 秒发一次 :keepalive

    while True:
        try:
            # 检查客户端是否断开（浏览器关页 / 切路由等）
            if await request.is_disconnected():
                logger.info(f"[SSE] 客户端已断开 run={run_id}，停止推送")
                return

            # 等待新事件，最多等 keepalive_interval 秒，到时后没事件就发心跳
            try:
                await asyncio.wait_for(
                    slot["event"].wait(),
                    timeout=keepalive_interval,
                )
            except asyncio.TimeoutError:
                # 超时：发 SSE 注释行（以 : 开头）作为 keepalive
                # 注释行 EventSource 不会抛给 onmessage，但能阻止 TCP 空闲断开
                yield ":keepalive\n\n"
                continue

            # 有新事件：把 last_event_idx 之后的全部推出去
            new_events = slot["history"][last_event_idx:]
            for ev in new_events:
                yield _format_sse(ev.get("type", "progress"), ev)
            last_event_idx = len(slot["history"])

            # 清除 Event 信号，下次 await 会阻塞直到下一次 publish
            slot["event"].clear()

            # 如果刚推送的是 done / error，就优雅结束连接
            if new_events and new_events[-1].get("type") in ("done", "error"):
                yield _format_sse("end", {"reason": new_events[-1]["type"]})
                return

        except asyncio.CancelledError:
            # FastAPI / uvicorn 在客户端断开时会触发 CancelledError，正常退出
            logger.info(f"[SSE] 订阅任务被取消 run={run_id}")
            return
        except Exception as e:
            # 任何意外都不要影响主进程，打日志后退出循环
            logger.warning(f"[SSE] 推送循环异常 run={run_id}: {e}，结束本次连接")
            return


# ── 对外 SSE 端点 ─────────────────────────────────────────────
@router.get("/events")
async def pipeline_events(run_id: str, request: Request):
    """SSE 实时进度订阅端点。

    前端用法：
        const es = new EventSource('/pipeline/events?run_id=__demo__');
        es.addEventListener('progress', (e) => {
            const data = JSON.parse(e.data);
            console.log(data.step, data.message);
        });
        es.addEventListener('done', (e) => es.close());

    Args:
        run_id: 管线运行 ID；传特殊值 __demo__ 即进入演示模式（立即推 5 个假进度）
        request: FastAPI Request，用于检测客户端断开

    Returns:
        StreamingResponse: media_type=text/event-stream，支持 chunked 推送
    """
    # 基础参数校验：空 run_id 拒绝
    if not run_id or not isinstance(run_id, str) or not run_id.strip():
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="run_id 必填且不能为空")

    # 演示模式：立即按 1 秒节奏推 5 步假进度 + done
    if run_id.strip() == "__demo__":
        return StreamingResponse(
            _demo_streamer(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",  # 告诉 Nginx 不要缓冲 SSE
            },
        )

    # 真实模式：订阅指定 run_id 的增量事件
    return StreamingResponse(
        _real_streamer(run_id.strip(), request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
