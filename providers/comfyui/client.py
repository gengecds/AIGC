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

    # 模型类别 → (节点类型, 输入字段名)：用于枚举本机已安装的模型
    MODEL_NODE_MAP = {
        "checkpoints": ("CheckpointLoaderSimple", "ckpt_name"),
        "diffusion_models": ("UnetLoaderGGUFAdvanced", "unet_name"),
        "text_encoders": ("CLIPLoader", "clip_name"),
        "vae": ("VAELoader", "vae_name"),
    }

    async def list_models(self, model_type: str = "checkpoints") -> list:
        """列出已安装模型。

        按 model_type 从 ComfyUI /object_info 读取对应节点所需的模型列表：
        - checkpoints（出图 SD/FLUX checkpoint，含 LTX-Video 视频模型，经 CheckpointLoaderSimple 加载）
        - diffusion_models（扩散模型，如 FLUX.1 GGUF / LTX-Video diffusion，经 UnetLoaderGGUFAdvanced 加载）
        - text_encoders / vae
        """
        node_cls, field_name = self.MODEL_NODE_MAP.get(
            model_type, self.MODEL_NODE_MAP["checkpoints"]
        )
        info = await self.get_object_info()
        node_info = info.get(node_cls, {})
        req = node_info.get("input", {}).get("required", {})
        opts = req.get(field_name, [None])[0]
        return opts if isinstance(opts, list) else []

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
        loras: Optional[List[Dict[str, Any]]] = None,
    ) -> dict:
        """构建标准文生图工作流（KSampler + SD），可选叠加风格 LoRA。

        :param loras: 风格 LoRA 列表，元素形如
            {"name": "SD1.5/GuoFeng3.2_Lora.safetensors",
             "strength_model": 0.7, "strength_clip": 0.7}
            按顺序用 LoraLoader 链式叠加到 checkpoint 的 MODEL/CLIP 上；
            为空则不挂任何 LoRA（与旧行为一致）。
        """
        wf = {
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

        # 叠加风格 LoRA：LoraLoader 依次串联，前一个的输出接后一个的输入，
        # 最后把 KSampler.model 与两个 CLIPTextEncode.clip 指向链尾。
        if loras:
            prev_model: List[Any] = ["4", 0]
            prev_clip: List[Any] = ["4", 1]
            node_id = 10
            for lora in loras:
                name = lora.get("name")
                if not name:
                    continue
                strength = float(lora.get("strength_model", lora.get("strength", 0.8)))
                nid = str(node_id)
                wf[nid] = {
                    "class_type": "LoraLoader",
                    "inputs": {
                        "lora_name": name,
                        "strength_model": strength,
                        "strength_clip": float(lora.get("strength_clip", strength)),
                        "model": prev_model,
                        "clip": prev_clip,
                    },
                }
                prev_model = [nid, 0]
                prev_clip = [nid, 1]
                node_id += 1
            wf["3"]["inputs"]["model"] = prev_model
            wf["6"]["inputs"]["clip"] = prev_clip
            wf["7"]["inputs"]["clip"] = prev_clip

        return wf

    @staticmethod
    def build_flux_txt2img_workflow(
        unet_name: str = "flux1-schnell-Q5_K_S.gguf",
        dequant_dtype: str = "default",
        clip_l: str = "clip_l.safetensors",
        t5xxl: str = "t5xxl_fp8_e4m3fn.safetensors",
        vae_name: str = "ae.safetensors",
        prompt: str = "",
        width: int = 1024,
        height: int = 1024,
        seed: int = 42,
        steps: int = 4,
        cfg: float = 1.0,
        sampler: str = "euler",
        scheduler: str = "simple",
        batch_size: int = 1,
        loras: Optional[List[Dict[str, Any]]] = None,
    ) -> dict:
        """构建 FLUX.1 文生图工作流（UNET + DualCLIP + VAE）

        参照 ComfyUI 官方 "Text to Image (Flux.1 Dev)" 蓝图：
            UNETLoader → DualCLIPLoader(clip_l, t5xxl, "flux") + VAELoader
            → CLIPTextEncode(prompt) + ConditioningZeroOut(负面词零化)
            → EmptySD3LatentImage → KSampler → VAEDecode → SaveImage

        FLUX-schnell 建议 steps≈4、cfg≈1.0、sampler=euler、scheduler=simple；
        负面词通过 ConditioningZeroOut 零化（不用负面提示词）。

        :param loras: 风格 LoRA 列表，元素形如
            {"name": "FLUX/xxx.safetensors", "strength_model": 0.8}
            FLUX LoRA 只训练了 UNet（text_encoder_lr=0），故用 LoraLoaderModelOnly
            链式叠加到 UNET 的 MODEL 上，CLIP 仍直接取 DualCLIPLoader；
            为空则不挂任何 LoRA（与旧行为一致）。
        """
        wf = {
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
                    "positive": ["7", 0],
                    "negative": ["8", 0],
                    "latent_image": ["9", 0],
                },
            },
            "4": {
                "class_type": "UnetLoaderGGUFAdvanced",
                "inputs": {
                    "unet_name": unet_name,
                    "dequant_dtype": dequant_dtype,
                    "patch_dtype": "default",
                    "patch_on_device": False,
                },
            },
            "5": {
                "class_type": "DualCLIPLoader",
                "inputs": {
                    "clip_name1": clip_l,
                    "clip_name2": t5xxl,
                    "type": "flux",
                },
            },
            "6": {
                "class_type": "VAELoader",
                "inputs": {"vae_name": vae_name},
            },
            "7": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": prompt, "clip": ["5", 0]},
            },
            "8": {
                "class_type": "ConditioningZeroOut",
                "inputs": {"conditioning": ["7", 0]},
            },
            "9": {
                "class_type": "EmptySD3LatentImage",
                "inputs": {"width": width, "height": height, "batch_size": batch_size},
            },
            "10": {
                "class_type": "VAEDecode",
                "inputs": {"samples": ["3", 0], "vae": ["6", 0]},
            },
            "11": {
                "class_type": "SaveImage",
                "inputs": {"filename_prefix": "flux_output", "images": ["10", 0]},
            },
        }

        # 叠加 FLUX 风格 LoRA：LoraLoaderModelOnly 依次串联（只改 MODEL、不动 CLIP），
        # 最后把 KSampler.model 指向链尾。
        if loras:
            prev_model: List[Any] = ["4", 0]
            node_id = 20
            for lora in loras:
                name = lora.get("name")
                if not name:
                    continue
                strength = float(lora.get("strength_model", lora.get("strength", 0.8)))
                nid = str(node_id)
                wf[nid] = {
                    "class_type": "LoraLoaderModelOnly",
                    "inputs": {
                        "lora_name": name,
                        "strength_model": strength,
                        "model": prev_model,
                    },
                }
                prev_model = [nid, 0]
                node_id += 1
            wf["3"]["inputs"]["model"] = prev_model

        return wf

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
        preprocessor: str = "",
    ) -> dict:
        """构建带 ControlNet 的文生图工作流。

        :param controlnet_name: ControlNet 模型文件名，须与 ComfyUI 里
            ControlNetLoader 列出的名字完全一致（含扩展名），不做任何改写。
        :param preprocessor: 预处理方式。"canny" 时在 LoadImage 后插入 Canny 节点
            自动提线稿（工作流自包含，无需事先准备线稿）；空串则把 LoadImage
            的图直接作为控制图。
        """
        wf = {
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
            "8": {"class_type": "ControlNetLoader", "inputs": {"control_net_name": controlnet_name}},
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
        if preprocessor == "canny":
            wf["13"] = {"class_type": "Canny", "inputs": {
                "image": ["6", 0], "low_threshold": 0.4, "high_threshold": 0.8,
            }}
            wf["9"]["inputs"]["image"] = ["13", 0]
        return wf

    @staticmethod
    def build_ipadapter_workflow(
        ckpt_name: str,
        prompt: str,
        negative_prompt: str = "",
        ref_image: str = "",
        preset: str = "PLUS (high strength)",
        width: int = 512,
        height: int = 512,
        seed: int = 42,
        steps: int = 12,
        cfg: float = 7.5,
        ipadapter_weight: float = 0.7,
        batch_size: int = 1,
    ) -> dict:
        """构建 IP-Adapter 角色锁定工作流。

        :param preset: IPAdapterUnifiedLoader 的预设名，决定用哪套 IP-Adapter 模型
            （会自动从 models/ipadapter 与 models/clip_vision 里挑匹配的文件）。
            常用值 "PLUS (high strength)" / "PLUS FACE (portraits)"。

        节点 12 的 weight_type 保持 "standard"（参考图全效）：参考图是「胸像定妆照」，
        standard 才能把角色的年龄/性别/长相锁住。它同时会搬运参考图的构图与背景，
        故调用方只在近/特写类景别挂参考图（见 image_agent._resolve_ref_image）——
        中/全/远挂胸像参考图会被拽成灰底胸像、丢掉分镜场景。
        曾试过 "prompt is more important"（构图听提示词），实测参考图随之失效，
        56 岁老妇被画成年轻女子，故回退。
        """
        return {
            "3": {"class_type": "KSampler", "inputs": {
                "seed": seed, "steps": steps, "cfg": cfg,
                "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0,
                "model": ["12", 0], "positive": ["13", 0], "negative": ["7", 0],
                "latent_image": ["5", 0],
            }},
            "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt_name}},
            "5": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": batch_size}},
            "6": {"class_type": "LoadImage", "inputs": {"image": ref_image}},
            "7": {"class_type": "CLIPTextEncode", "inputs": {"text": negative_prompt, "clip": ["4", 1]}},
            "11": {"class_type": "IPAdapterUnifiedLoader", "inputs": {"preset": preset, "model": ["4", 0]}},
            "12": {"class_type": "IPAdapter", "inputs": {
                "model": ["11", 0], "ipadapter": ["11", 1], "image": ["6", 0],
                "weight": ipadapter_weight, "start_at": 0.0, "end_at": 1.0,
                "weight_type": "standard",
            }},
            "13": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["4", 1]}},
            "14": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
            "15": {"class_type": "SaveImage", "inputs": {"filename_prefix": "ipadapter_output", "images": ["14", 0]}},
        }

    @staticmethod
    def build_ipadapter_faceid_workflow(
        ckpt_name: str,
        prompt: str,
        negative_prompt: str = "",
        ref_image: str = "",
        preset: str = "FACEID PLUS V2",
        lora_strength: float = 0.6,
        provider: str = "CPU",
        width: int = 512,
        height: int = 512,
        seed: int = 42,
        steps: int = 12,
        cfg: float = 7.5,
        weight: float = 1.0,
        weight_faceidv2: float = 1.0,
        batch_size: int = 1,
    ) -> dict:
        """构建 IP-Adapter FaceID 角色锁定工作流（SD1.5）。

        与 build_ipadapter_workflow 的区别：走 IPAdapterUnifiedLoaderFaceID +
        IPAdapterFaceID，会额外加载 insightface 人脸特征与配套 FaceID LoRA，
        因此人物脸部一致性更强。

        :param preset: FACEID 系列预设，"FACEID" / "FACEID PLUS - SD1.5 only" /
            "FACEID PLUS V2" / "FACEID PORTRAIT (style transfer)"。
        :param lora_strength: FaceID 配套 LoRA 强度，0 表示不加载 LoRA。
        :param provider: insightface 推理后端，Mac 上用 "CPU"。
        """
        return {
            "3": {"class_type": "KSampler", "inputs": {
                "seed": seed, "steps": steps, "cfg": cfg,
                "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0,
                "model": ["12", 0], "positive": ["13", 0], "negative": ["7", 0],
                "latent_image": ["5", 0],
            }},
            "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt_name}},
            "5": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": batch_size}},
            "6": {"class_type": "LoadImage", "inputs": {"image": ref_image}},
            "7": {"class_type": "CLIPTextEncode", "inputs": {"text": negative_prompt, "clip": ["4", 1]}},
            "11": {"class_type": "IPAdapterUnifiedLoaderFaceID", "inputs": {
                "preset": preset, "lora_strength": lora_strength,
                "provider": provider, "model": ["4", 0],
            }},
            "12": {"class_type": "IPAdapterFaceID", "inputs": {
                "model": ["11", 0], "ipadapter": ["11", 1], "image": ["6", 0],
                "weight": weight, "weight_faceidv2": weight_faceidv2,
                "weight_type": "linear", "combine_embeds": "concat",
                "start_at": 0.0, "end_at": 1.0, "embeds_scaling": "V only",
            }},
            "13": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["4", 1]}},
            "14": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
            "15": {"class_type": "SaveImage", "inputs": {"filename_prefix": "ipadapter_faceid_output", "images": ["14", 0]}},
        }

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
                info = history.get(prompt_id, {})
                outputs = info.get("outputs", {})
                status = info.get("status", {})
                if not status.get("completed", False):
                    err_msg = "unknown error"
                    for mt, md in status.get("messages", []):
                        if mt == "execution_error":
                            err_msg = md.get("exception_message", err_msg)
                    return {
                        "success": False,
                        "prompt_id": prompt_id,
                        "error": err_msg,
                        "outputs": outputs,
                    }
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
        port: int = 8189,
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
