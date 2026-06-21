# AI 漫剧工坊

AI 驱动的动漫视频自动生成管线：**剧本 → 分镜 → 角色定妆 → 出图 → 图生视频 → 字幕 → 合成**

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
   DeepSeek API   ComfyUI API      FFmpeg
                  (SD + HyVideo)
```

## 管线流程

| 步骤 | Agent | 功能 | 实现 |
|------|-------|------|------|
| 1 | ScriptAgent | 根据故事梗概生成完整剧本（多集+对话） | DeepSeek |
| 2 | StoryboardAgent | 剧本 → 分镜表（12-18个分镜/集） | DeepSeek |
| 3 | CharacterDesignAgent | 角色定妆照生成 | ComfyUI SD |
| 4 | ImageGenAgent | 批量分镜出图 | ComfyUI SD (异步批量) |
| 5 | VideoGenAgent | 图→视频生成 | ComfyUI HyVideo (异步批量) |
| 6 | SubtitleAgent | SRT 字幕生成 | 本地规则引擎 |
| 7 | ComposeAgent | 帧→MP4 合成 + 字幕烧录 | FFmpeg |

## 快速开始

### 1. GPU 端（AutoDL）

```bash
# 启动 ComfyUI（端口 8188）
cd /root/ComfyUI
python main.py --listen 0.0.0.0 --port 8188

# 本地建立 SSH 隧道
ssh -L 18188:localhost:8188 root@your-instance -p your_port
```

### 2. 本地运行测试

```bash
# 安装依赖
pip install httpx openai python-dotenv

# 运行全链路测试
python3 pipeline/test_real_full.py

# 单独测试 SD 出图
python3 test_comfyui_pipeline.py

# 单独测试 HunyuanVideo
python3 test_hunyuanvideo.py
```

### 3. 预览前端

```bash
python3 scripts/serve.py
# 浏览器打开 http://localhost:4333
```

## 项目结构

```
├── agents/                  # 7个 Agent
│   ├── script_agent.py      # Agent 1: 剧本生成
│   ├── storyboard_agent.py  # Agent 2: 分镜
│   ├── character_agent.py   # Agent 3: 角色定妆
│   ├── image_agent.py       # Agent 4: 批量出图
│   ├── video_agent.py       # Agent 5: 图生视频
│   ├── subtitle_agent.py    # Agent 6: 字幕
│   ├── compose_agent.py     # Agent 7: 合成
│   └── base.py              # Agent 基类
├── providers/               # 外部服务适配层
│   ├── comfyui/             # ComfyUI API 客户端
│   │   ├── client.py        # Queue/Watch/History API
│   │   └── workflow_*.json  # SD/HyVideo 工作流
│   ├── comfyui_provider.py  # ComfySDImage + ComfyHyVideo Provider
│   ├── llm.py               # LLM Provider (DeepSeek/Ollama)
│   └── utils.py             # 帧→视频合成工具
├── pipeline/                # 管线调度
│   ├── scheduler.py         # Pipeline + PipelineState
│   └── test_real_full.py    # 全链路真 Provider 测试
├── frontend/                # Web 前端
│   ├── index.html / app.js / style.css  # 正式前端
│   └── review.html          # 成果预览页
├── scripts/
│   ├── serve.py             # 本地预览服务器
│   └── download_videos.py   # GPU→本地视频下载
├── workflows/               # ComfyUI 工作流 JSON
├── storage/                 # 产出
│   ├── output/              # SD 出图
│   ├── videos/              # 合成视频
│   └── checkpoints/         # Pipeline 断点
└── docs/                    # 设计文档
```

## 环境变量

```env
DEEPSEEK_API_KEY=your_key_here
```

## 已验证

- ✅ DeepSeek 剧本+分镜生成
- ✅ ComfyUI SD 出图（512×512，Realistic-Vision-V5.1）
- ✅ HunyuanVideo 图生视频（25帧，fp8_e4m3fn_fast）
- ✅ 异步批量提交 + 后台轮询收集
- ✅ 全链路真 Provider 测试（16 分镜完整通过）
- ✅ 帧 → WebM 视频合成
- ✅ Web 前端预览
