"""ComfyUIClient - ComfyUI REST API + WebSocket 封装"""

import json
import uuid
import logging
from typing import Optional
from urllib.parse import urljoin

import httpx
import websockets

logger = logging.getLogger(__name__)


class ComfyUIClient:
    """ComfyUI 客户端，管理工作流提交/监听/下载"""

    def __init__(self, host: str = "127.0.0.1", port: int = 8188):
        self.base_url = f"http://{host}:{port}"
        self.ws_url = f"ws://{host}:{port}/ws"
        self.client_id = str(uuid.uuid4())

    async def queue_workflow(self, workflow: dict) -> str:
        """提交工作流，返回 prompt_id"""
        payload = {
            "prompt": workflow,
            "client_id": self.client_id,
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                urljoin(self.base_url, "/prompt"),
                json=payload,
                timeout=60,
            )
            resp.raise_for_status()
            data = resp.json()
            prompt_id = data.get("prompt_id", "")
            logger.info(f"工作流已提交: prompt_id={prompt_id}")
            return prompt_id

    async def wait_for_completion(self, prompt_id: str, timeout: int = 600) -> dict:
        """WebSocket 监听直到任务完成，返回结果元数据"""
        async with websockets.connect(
            f"{self.ws_url}?clientId={self.client_id}"
        ) as ws:
            while True:
                msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
                data = json.loads(msg)
                msg_type = data.get("type", "")

                if msg_type == "executing":
                    node = data.get("data", {}).get("node")
                    if node is None:
                        logger.info(f"任务 {prompt_id} 执行完毕")
                        break
                    else:
                        logger.debug(f"执行节点: {node}")

                elif msg_type == "execution_start":
                    logger.debug(f"任务开始执行: {prompt_id}")

                elif msg_type == "progress":
                    prog = data.get("data", {})
                    logger.info(f"进度: {prog.get('value', 0)}/{prog.get('max', 1)}")

        # 获取历史记录获取结果
        result = await self._get_history(prompt_id)
        return result

    async def upload_image(self, image_path: str, image_type: str = "input") -> dict:
        """上传图片到 ComfyUI（作为 ControlNet 参考或图生视频输入）"""
        import aiofiles
        async with httpx.AsyncClient() as client:
            async with aiofiles.open(image_path, "rb") as f:
                files = {
                    "image": (image_path.split("/")[-1], await f.read(), "image/png"),
                }
            data = {
                "type": image_type,
                "overwrite": "true",
            }
            resp = await client.post(
                urljoin(self.base_url, "/upload/image"),
                files=files,
                data=data,
                timeout=120,
            )
            resp.raise_for_status()
            result = resp.json()
            logger.info(f"图片已上传: {result.get('name', '')}")
            return result

    async def download_output(self, filename: str, subfolder: str = "") -> bytes:
        """下载生成结果（图片/视频）"""
        params = {"filename": filename, "subfolder": subfolder, "type": "output"}
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                urljoin(self.base_url, "/view"),
                params=params,
                timeout=120,
            )
            resp.raise_for_status()
            return resp.content

    async def _get_history(self, prompt_id: str) -> dict:
        """获取任务执行历史"""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                urljoin(self.base_url, f"/history/{prompt_id}"),
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()

    async def get_queue_size(self) -> int:
        """获取当前队列长度"""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                urljoin(self.base_url, "/queue"),
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            return len(data.get("queue_running", [])) + len(data.get("queue_pending", []))

    # ── 便捷方法 ────────────────────────────

    async def run_sd(self, sd_prompt: str, negative_prompt: str = "",
                     ref_image: Optional[str] = None,
                     seed: int = -1,
                     model: str = "realisticVision-v51",
                     width: int = 1080, height: int = 1920) -> str:
        """一键出图——从工作流模板构建 SD workflow 并提交"""
        workflow = self._build_sd_workflow(
            prompt=sd_prompt,
            negative=negative_prompt,
            ref_image=ref_image,
            seed=seed,
            model=model,
            width=width,
            height=height,
        )
        prompt_id = await self.queue_workflow(workflow)
        result = await self.wait_for_completion(prompt_id)
        # 从 result 提取输出图片路径
        return self._extract_output_path(result)

    async def run_video(self, input_image: str,
                        motion_prompt: str,
                        duration: int = 5,
                        model: str = "hunyuan_video") -> str:
        """一键图生视频"""
        workflow = self._build_video_workflow(
            input_image=input_image,
            prompt=motion_prompt,
            duration=duration,
            model=model,
        )
        prompt_id = await self.queue_workflow(workflow)
        result = await self.wait_for_completion(prompt_id)
        return self._extract_output_path(result)

    # ── 工作流构建（骨架，待 Phase 2 完善） ─────

    def _build_sd_workflow(self, **kwargs) -> dict:
        """从模板构建 SD 工作流 JSON"""
        raise NotImplementedError("Phase 2: 从 workflows/sd_shot.json 加载模板")

    def _build_video_workflow(self, **kwargs) -> dict:
        """从模板构建图生视频工作流 JSON"""
        raise NotImplementedError("Phase 2: 从 workflows/img2video.json 加载模板")

    def _extract_output_path(self, history_result: dict) -> str:
        raise NotImplementedError("Phase 2: 从 history 结果中解析输出文件路径")


# 需要安装的库
import asyncio
