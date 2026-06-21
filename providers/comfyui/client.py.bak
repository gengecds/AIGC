"""ComfyUIClient - ComfyUI REST API + WebSocket 封装

支持：
- 同步/异步提交工作流
- WebSocket 实时进度回调
- SSH 隧道自动连接
"""

import json
import uuid
import asyncio
import logging
import os
from typing import Optional, Callable, Awaitable, Dict, List, Any
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
import websockets

logger = logging.getLogger(__name__)


class ComfyUIClient:
    """ComfyUI REST + WebSocket 客户端"""

    def __init__(
        self,
        server_addr: str = "127.0.0.1",
        server_port: int = 8188,
        use_https: bool = False,
        timeout: int = 300,
        ssh_tunnel: Optional[dict] = None,
    ):
        self.server_addr = server_addr
        self.server_port = server_port
        self.timeout = timeout
        self.client_id = str(uuid.uuid4())

        protocol = "https" if use_https else "http"
        ws_protocol = "wss" if use_https else "ws"
        self.base_url = f"{protocol}://{server_addr}:{server_port}"
        self.ws_url = f"{ws_protocol}://{server_addr}:{server_port}/ws?clientId={self.client_id}"

        self.ssh_tunnel = ssh_tunnel
        self._http = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout),
        )

    # ── 队列管理 ──────────────────────────────

    async def queue_prompt(self, workflow: dict) -> dict:
        """提交工作流，返回 prompt_id"""
        resp = await self._http.post("/prompt", json={
            "prompt": workflow,
            "client_id": self.client_id,
        })
        resp.raise_for_status()
        return resp.json()

    async def get_queue(self) -> dict:
        """获取队列状态"""
        resp = await self._http.get("/queue")
        resp.raise_for_status()
        return resp.json()

    async def get_history(self, prompt_id: str = "") -> dict:
        """获取执行历史"""
        path = f"/history/{prompt_id}" if prompt_id else "/history"
        resp = await self._http.get(path)
        resp.raise_for_status()
        return resp.json()

    async def get_status(self) -> dict:
        return await self.get_queue()

    # ── 节点信息 ──────────────────────────────

    async def get_object_info(self) -> dict:
        """获取所有可用节点类型"""
        resp = await self._http.get("/object_info")
        resp.raise_for_status()
        return resp.json()

    async def get_node_info(self, node_type: str) -> dict:
        """获取指定节点信息"""
        info = await self.get_object_info()
        return info.get(node_type, {})

    # ── 模型管理 ──────────────────────────────

    async def list_models(self, model_type: str = "checkpoints") -> list:
        """列出已安装模型"""
        info = await self.get_object_info()
        ckpt_node = info.get("CheckpointLoaderSimple", {})
        ckpt_input = ckpt_node.get("input", {}).get("required", {})
        ckpts = ckpt_input.get("ckpt_name", [None])[0]
        return ckpts if isinstance(ckpts, list) else []

    # ── 生成工作流 ────────────────────────────

    @staticmethod
    def build_txt2img_workflow(
        ckpt_name: str,
        prompt: str,
        negative_prompt: str = "",
        width: int = 1024,
        height: int = 1024,
        seed: int = 42,
        steps: int = 20,
        cfg: float = 7.5,
        sampler: str = "euler",
        scheduler: str = "normal",
        batch_size: int = 1,
    ) -> dict:
        """构建标准文生图工作流（KSampler + SD）"""
        return {
            "3": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": seed,
                    "steps": steps,
                    "cfg": cfg,
                    "sampler_name": sampler,
                    "scheduler": scheduler,
                    "denoise": 1.0,
                    "model": ["4", 0],
                    "positive": ["6", 0],
                    "negative": ["7", 0],
                    "latent_image": ["5", 0],
                },
            },
            "4": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": ckpt_name},
            },
            "5": {
                "class_type": "EmptyLatentImage",
                "inputs": {"width": width, "height": height, "batch_size": batch_size},
            },
            "6": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": prompt, "clip": ["4", 1]},
            },
            "7": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": negative_prompt, "clip": ["4", 1]},
            },
            "8": {
                "class_type": "VAEDecode",
                "inputs": {"samples": ["3", 0], "vae": ["4", 2]},
            },
            "9": {
                "class_type": "SaveImage",
                "inputs": {"filename_prefix": "comfyui_output", "images": ["8", 0]},
            },
        }

    @staticmethod
    def build_img2video_workflow(
        input_image_path: str,
        model_name: str = "hunyuan_video",
        prompt: str = "",
        duration: int = 5,
        width: int = 1024,
        height: int = 576,
    ) -> dict:
        """构建图生视频工作流骨架

        PS: 具体 workflow 取决于 HunyuanVideo 节点的 class_type。
        这需要实例安装后才能确定精确的节点 ID。
        当前为占位骨架，实际使用时根据节点实际情况补全。
        """
        raise NotImplementedError(
            "HunyuanVideo 工作流需要在实例上安装后确认节点 class_type。"
            "请安装 ComfyUI-VideoHelperSuite + Kijai 节点后补全此方法。"
        )

    # ── 同步等待执行完成 ──────────────────────

    async def wait_for_completion(
        self,
        prompt_id: str,
        callback: Optional[Callable[[dict], Awaitable[None]]] = None,
        poll_interval: float = 1.0,
    ) -> dict:
        """等待工作流执行完成（轮询模式，不依赖 WebSocket）"""
        import time

        for _ in range(self.timeout):
            queue = await self.get_queue()
            running_ids = [j["prompt_id"] for j in queue.get("queue_running", [])]
            pending_ids = [j["prompt_id"] for j in queue.get("queue_pending", [])]

            if prompt_id not in running_ids and prompt_id not in pending_ids:
                # 执行完成，获取结果
                history = await self.get_history(prompt_id)
                outputs = history.get(prompt_id, {}).get("outputs", {})
                return {
                    "success": True,
                    "prompt_id": prompt_id,
                    "outputs": outputs,
                }

            if callback:
                await callback({
                    "prompt_id": prompt_id,
                    "running": len(running_ids),
                    "pending": len(pending_ids),
                })

            await asyncio.sleep(poll_interval)

        return {
            "success": False,
            "prompt_id": prompt_id,
            "error": f"超时 (>{self.timeout}s)",
        }

    async def close(self):
        await self._http.aclose()
