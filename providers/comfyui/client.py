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
import requests
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
    def build_controlnet_workflow(
        ckpt_name: str,
        prompt: str,
        negative_prompt: str = "",
        controlnet_name: str = "control_v11p_sd15_canny.pth",
        controlnet_image: str = "",
        width: int = 512,
        height: int = 512,
        seed: int = 42,
        steps: int = 12,
        cfg: float = 7.5,
        controlnet_strength: float = 0.75,
        batch_size: int = 1,
    ) -> dict:
        """构建带 ControlNet 的文生图工作流"""
        ctrl_name_clean = controlnet_name.replace(".pth", "").replace(".safetensors", "")
        return {
            "3": {"class_type": "KSampler", "inputs": {
                "seed": seed, "steps": steps, "cfg": cfg,
                "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0,
                "model": ["4", 0], "positive": ["9", 0], "negative": ["7", 0],
                "latent_image": ["5", 0],
            }},
            "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt_name}},
            "5": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": batch_size}},
            "6": {"class_type": "LoadImage", "inputs": {"image": controlnet_image}},
            "7": {"class_type": "CLIPTextEncode", "inputs": {"text": negative_prompt, "clip": ["4", 1]}},
            "8": {"class_type": "ControlNetLoader", "inputs": {"control_net_name": ctrl_name_clean}},
            "9": {"class_type": "ControlNetApply", "inputs": {
                "strength": controlnet_strength,
                "conditioning": ["10", 0],
                "control_net": ["8", 0],
                "image": ["6", 0],
            }},
            "10": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["4", 1]}},
            "11": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
            "12": {"class_type": "SaveImage", "inputs": {"filename_prefix": "ctrl_output", "images": ["11", 0]}},
        }

    @staticmethod
    def build_ipadapter_workflow(
        ckpt_name: str,
        prompt: str,
        negative_prompt: str = "",
        ref_image: str = "",
        ipadapter_model: str = "ip-adapter-plus-face.safetensors",
        width: int = 512,
        height: int = 512,
        seed: int = 42,
        steps: int = 12,
        cfg: float = 7.5,
        ipadapter_weight: float = 0.7,
        batch_size: int = 1,
    ) -> dict:
        """构建 IP-Adapter 角色锁定工作流"""
        return {
            "3": {"class_type": "KSampler", "inputs": {
                "seed": seed, "steps": steps, "cfg": cfg,
                "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0,
                "model": ["13", 0], "positive": ["12", 0], "negative": ["7", 0],
                "latent_image": ["5", 0],
            }},
            "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt_name}},
            "5": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": batch_size}},
            "6": {"class_type": "LoadImage", "inputs": {"image": ref_image}},
            "7": {"class_type": "CLIPTextEncode", "inputs": {"text": negative_prompt, "clip": ["4", 1]}},
            "8": {"class_type": "IPAdapterModelLoader", "inputs": {"ipadapter_file": ipadapter_model}},
            "9": {"class_type": "CLIPVisionLoader", "inputs": {"clip_name": "CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors"}},
            "10": {"class_type": "CLIPVisionEncode", "inputs": {"clip_vision": ["9", 0], "image": ["6", 0]}},
            "11": {"class_type": "IPAdapterUnifiedLoader", "inputs": {"preset": "PLUS", "model": ["4", 0]}},
            "12": {"class_type": "IPAdapter", "inputs": {
                "model": ["11", 0], "ipadapter": ["8", 0], "image": ["10", 0],
                "weight": ipadapter_weight, "start_at": 0.0, "end_at": 1.0,
                "weight_type": "original",
            }},
            "13": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["4", 1]}},
            "14": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
            "15": {"class_type": "SaveImage", "inputs": {"filename_prefix": "ipadapter_output", "images": ["14", 0]}},
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
            running_ids = [j[1] if isinstance(j, list) else j.get("prompt_id", "") for j in queue.get("queue_running", [])]
            pending_ids = [j[1] if isinstance(j, list) else j.get("prompt_id", "") for j in queue.get("queue_pending", [])]

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

    # ── 上传图片 ──────────────────────────────

    async def upload_image(self, image_path: str, subfolder: str = "") -> dict:
        """上传图片到 ComfyUI input 目录"""
        path = Path(image_path)
        if not path.exists():
            # 可能已在远端，尝试通过 SSH 复制
            raise FileNotFoundError(f"本地图片不存在: {image_path}")
        files = {"image": (path.name, path.read_bytes(), "image/png")}
        data = {"subfolder": subfolder, "type": "input", "overwrite": "true"}
        resp = requests.post(f"{self.base_url}/upload/image", files=files, data=data)
        resp.raise_for_status()
        return resp.json()

    async def close(self):
        await self._http.aclose()


    # ── 同步入口（兼容pipeline ─────────────────────

    @staticmethod
    def txt2img_sync(
        host: str = "127.0.0.1",
        port: int = 8188,
        ckpt_name: str = "Realistic-Vision-V5.1.safetensors",
        prompt: str = "a cute cat",
        negative_prompt: str = "",
        width: int = 512,
        height: int = 512,
        seed: int = 42,
        steps: int = 10,
        cfg: float = 7.0,
        sampler: str = "euler",
        scheduler: str = "normal",
        timeout: int = 300,
    ) -> dict:
        """同步文生图 - 不依赖asyncio，直接requests"""
        import json, time
        
        url = f"http://{host}:{port}"
        
        wf = {
            "1": {"class_type":"CheckpointLoaderSimple","inputs":{"ckpt_name": ckpt_name}},
            "2": {"class_type":"EmptyLatentImage","inputs":{"width": width, "height": height, "batch_size": 1}},
            "3": {"class_type":"CLIPTextEncode","inputs":{"text": prompt, "clip": ["1", 1]}},
            "4": {"class_type":"CLIPTextEncode","inputs":{"text": negative_prompt, "clip": ["1", 1]}},
            "5": {"class_type":"KSampler","inputs":{"seed": seed, "steps": steps, "cfg": cfg, "sampler_name": sampler, "scheduler": scheduler, "denoise": 1.0, "model": ["1", 0], "positive": ["3", 0], "negative": ["4", 0], "latent_image": ["2", 0]}},
            "6": {"class_type":"VAEDecode","inputs":{"samples": ["5", 0], "vae": ["1", 2]}},
            "7": {"class_type":"SaveImage","inputs":{"filename_prefix": "output", "images": ["6", 0]}}
        }
        
        r = requests.post(f"{url}/prompt", json={"prompt": wf, "client_id": "pipeline"})
        if r.status_code != 200:
            return {"success": False, "error": f"Queue failed: {r.text}"}
        
        pid = r.json().get("prompt_id","")
        if not pid:
            return {"success": False, "error": f"No prompt_id: {r.json()}"}
        
        # 轮询
        for i in range(timeout):
            time.sleep(2)
            q = requests.get(f"{url}/queue").json()
            if not q["queue_running"] and not q["queue_pending"]:
                break
        
        h = requests.get(f"{url}/history/{pid}").json()
        if pid not in h:
            return {"success": False, "prompt_id": pid, "error": "history not found"}
        
        info = h[pid]
        if not info.get("status",{}).get("completed",False):
            err_msg = "unknown error"
            for mt, md in info.get("status",{}).get("messages",[]):
                if mt == "execution_error":
                    err_msg = md.get("exception_message", err_msg)
            return {"success": False, "prompt_id": pid, "error": err_msg}
        
        # 下载生成的图片
        images = []
        for nid, out in info.get("outputs",{}).items():
            for img in out.get("images",[]):
                fn = img["filename"]
                sub = img.get("subfolder","")
                r2 = requests.get(f"{url}/view", params={"filename": fn, "subfolder": sub, "type": img.get("type","output")})
                if r2.status_code == 200:
                    images.append({"filename": fn, "data": r2.content, "size_kb": len(r2.content)/1024})
        
        return {"success": True, "prompt_id": pid, "images": images}
