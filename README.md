# AI 漫剧工坊

AI 驱动的动漫视频自动生成管线：**剧本 → 分镜 → 角色定妆 → 出图 → 图生视频 → 字幕 → 合成**

**全本地运行**：LLM、出图、视频、合成全部在本机（MacBook Air M4）完成，不依赖任何外部 API 或云 GPU。

## 整体架构

```
用户（一句话故事梗概）
    │
    ▼
┌─────────────────────────────────────────────────┐
│  Pipeline Scheduler                              │
│  ┌──────────┐  ┌──────────┐  ┌────────────────┐ │
│  │ Agent 1  │→│ Agent 2  │→│ ...           │ │
│  │ 剧本生成  │  │ 分镜拆解  │  │ Agent 7 合成   │ │
│  └────┬─────┘  └────┬─────┘  └───────┬────────┘ │
└───────┼──────────────┼────────────────┼──────────┘
        │              │                │
        ▼              ▼                ▼
   Ollama 本地     ComfyUI 本地      FFmpeg
   (qwen3:8b)     (SD + LTX-Video)
```

## 管线流程

| 步骤 | Agent | 功能 | 实现 |
|------|-------|------|------|
| 1 | ScriptAgent | 根据故事梗概生成完整剧本 | Ollama qwen3:8b（本地） |
| 2 | StoryboardAgent | 剧本 → 分镜表 | Ollama qwen3:8b（本地） |
| 3 | CharacterDesignAgent | 角色定妆照生成 | ComfyUI + SD1.5（本地） |
| 4 | ImageGenAgent | 批量分镜出图 | ComfyUI + SD1.5（本地） |
| 5 | VideoGenAgent | 图→视频生成 | ComfyUI + LTX-Video（本地） |
| 6 | SubtitleAgent | SRT 字幕生成 | 本地规则引擎 |
| 7 | VideoComposeAgent | 视频拼接 + 字幕烧录 | FFmpeg |

## 快速开始（本地）

### 1. 启动 ComfyUI（端口 8189）

```bash
cd /Users/a715/git/ComfyUI/ComfyUI-Installs/ComfyUI/ComfyUI
nohup ./.venv/bin/python main.py --listen 127.0.0.1 --port 8189 &
# 验证: curl http://127.0.0.1:8189/api/system/stats
```

### 2. 启动 Ollama（brew 服务已常驻）

```bash
ollama serve        # 若未运行
ollama list         # 应显示 qwen3:8b
```

### 3. 启动后端 API（端口 8888）

```bash
cd /Users/a715/git/AIGC
nohup .venv/bin/python -m uvicorn api.main:app --host 127.0.0.1 --port 8888 &
# 验证: curl http://127.0.0.1:8888/health → {"status":"ok"}
```

### 4. 运行测试

```bash
# 轻量管线测试（Mock，秒级）
.venv/bin/python tests/test_pipeline_light.py

# 全管线（真实 LLM + 出图 + 视频，需人工审核确认）
curl -X POST http://127.0.0.1:8888/api/v1/pipeline/run \
  -H "Content-Type: application/json" \
  -d '{"input": "一句话故事梗概"}'
```

## 项目结构

```
├── agents/                  # 7个 Agent
├── providers/               # 引擎适配层（Ollama / ComfyUI / Mock）
│   ├── llm.py               # OllamaProvider（本地 LLM）
│   ├── comfyui_provider.py  # ComfySDImageProvider + ComfyLTXVideoProvider
│   └── comfyui/client.py    # ComfyUI REST + WebSocket 客户端
├── pipeline/                # 管线调度 + checkpoint 断点续传
├── api/                     # FastAPI 后端
├── frontend/                # Web 前端
├── workflows/               # SD 工作流 JSON
├── config/config.yaml       # 配置中心（本地模式）
└── docs/                    # 设计文档（含本地部署方案）
```

## 环境变量

```env
# 默认读 config.yaml，通常无需设置
# IMAGE_PROVIDER=comfyui
# VIDEO_PROVIDER=comfyui
# LLM_PROVIDER=ollama
```

## 依赖模型（本地）

| 模型 | 位置 | 用途 |
|:---|:---|:---|
| Ollama qwen3:8b | ~/.ollama | 剧本/分镜 LLM |
| SD1.5 (v1-5-pruned-emaonly) | SD-WebUI 共享 | 出图 |
| LTX-Video 2B + t5xxl | ComfyUI-Shared | 图生视频 |

> 详见 [docs/本地环境部署方案_v1.md](docs/本地环境部署方案_v1.md)

## 已验证

- ✅ Ollama qwen3:8b 剧本+分镜生成（全本地）
- ✅ ComfyUI SD1.5 出图（512×512，MPS）
- ✅ LTX-Video 图生视频（MPS，替代 HunyuanVideo）
- ✅ 7 Agent 全管线真实视频输出（剧本→分镜→定妆→出图→视频→字幕→合成）
- ✅ 全本地运行，无任何外部 API/云 GPU 依赖
