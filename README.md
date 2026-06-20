# AI 漫剧制作管线

AI驱动的小说→剧本→分镜→出图→视频→字幕→合成的全自动制作管线。

---

## 一、环境配置

### 1.1 系统要求

- Python 3.11+
- macOS / Linux（Windows 未测试）
- 至少 15GB 磁盘空间（LLM 模型 + 代码）
- 可选：NVIDIA GPU 24GB+（用于 ComfyUI 出图/视频生成）

### 1.2 拉取代码

```bash
# 克隆项目
git clone <你的仓库地址> ~/AIGC
cd ~/AIGC
```

### 1.3 安装依赖

```bash
# 建议创建虚拟环境（可选但推荐）
python3 -m venv venv
source venv/bin/activate

# 安装依赖
pip install -r requirements.txt
```

如果 `requirements.txt` 不存在或过时，手动安装核心依赖：

```bash
pip install httpx python-dotenv fastapi uvicorn sqlalchemy aiosqlite
```

### 1.4 配置 API Key

创建 `.env` 文件：

```bash
echo 'DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx' > .env
```

> DeepSeek API Key 在 [platform.deepseek.com](https://platform.deepseek.com) 申请，用于剧本和分镜生成。

### 1.5 验证环境

```bash
cd ~/AIGC
python3 -c "
import sys; sys.path.insert(0, '.')
import os, asyncio
os.environ['DEEPSEEK_API_KEY'] = '你的key'
from agents.script_agent import ScriptAgent
async def t():
    r = await ScriptAgent().run('一句话测试')
    print(r.data['title'] if r.success else 'FAIL: ' + r.error)
asyncio.run(t())
"
```

如果输出剧本标题，环境就配好了。

---

## 二、目录结构说明

```
AIGC/
├── agents/                      ← Agent 逻辑代码（7个Agent）
│   ├── base.py                  # Agent 基类 + AgentResult
│   ├── script_agent.py          # Agent 1: 剧本生成（DeepSeek）
│   ├── storyboard_agent.py       # Agent 2: 分镜生成（DeepSeek + 校验器）
│   ├── character_agent.py        # Agent 3: 角色定妆照（Mock→ComfyUI）
│   ├── image_agent.py            # Agent 4: 出图（**占位，待实现**）
│   ├── video_agent.py            # Agent 5: 视频（**占位，待实现**）
│   ├── subtitle_agent.py         # Agent 6: 字幕（**占位，待实现**）
│   └── compose_agent.py          # Agent 7: 合成（**占位，待实现**）
│
├── providers/                  ← API 适配层
│   ├── base.py                  # LLM/Image/Video 抽象基类
│   ├── llm.py                   # DeepSeek API / Ollama 本地调用
│   └── mock_provider.py         # Mock 图片/视频（开发调试用）
│
├── pipeline/
│   └── scheduler.py             ← 管线调度器（断点续跑/Checkpoint）
│
├── api/
│   └── main.py                  ← FastAPI Web 入口
│
├── config/
│   └── config.yaml              ← 主配置（provider/风格/ComfyUI路径/等）
│
├── db/
│   └── models.py                ← SQLite 数据库模型（Story/PipelineJob等）
│
├── storage/                     ← 数据存储（不入git）
│   ├── output/                  # 🎬 最终产出（图片/视频/字幕）
│   ├── checkpoints/             # 💾 管线断点（Agent中间结果JSON）
│   ├── models/                  # 🧠 本地模型文件（LLM等）
│   └── workflows/               # ⚙️ ComfyUI 工作流 JSON 模板
│
├── .env                         ← API Key（**勿提交git**）
├── .gitignore
├── requirements.txt
└── README.md                    ← 本文件
```

---

## 三、模型下载与放置

### 3.1 必须的模型（HunyuanVideo 图生视频）

| 模型 | 大小 | 下载地址 | 放置路径（AutoDL） |
|------|------|----------|-------------------|
| LLM 文本编码器 | **15GB** | [Kijai/llava-llama-3-8b-text-encoder-tokenizer](https://huggingface.co/Kijai/llava-llama-3-8b-text-encoder-tokenizer) | `/root/autodl-fs/llava-llama-3-8b-text-encoder-tokenizer/` |
| HunyuanVideo UNet | **25GB** | [Kijai/HunyuanVideo_comfy 或官方](https://huggingface.co/Kijai/HunyuanVideo_comfy) | `/root/autodl-tmp/models/unet/HunyuanVideo/` |
| SVD-XT | **9GB** | [stabilityai/stable-video-diffusion-img2vid-xt](https://huggingface.co/stabilityai/stable-video-diffusion-img2vid-xt) | `/root/autodl-tmp/models/checkpoints/SVD-XT.safetensors` |
| CLIP-ViT | **1.6GB** | `ComfyUI 管理器自动下载` | `/root/autodl-tmp/models/clip/clip-vit-large-patch14/` |

### 3.2 本机下载方式（LLM 文本编码器为例）

```bash
# 用 aria2c 多线程下载（推荐）
aria2c -x 4 -s 4 -k 1M \
  "https://huggingface.co/Kijai/llava-llama-3-8b-text-encoder-tokenizer/resolve/main/model-00001-of-00004.safetensors"

# 其他 shard 同理（model-00002 ~ 00004）
# 配置文件单独下载：
# config.json, generation_config.json, model.safetensors.index.json,
# special_tokens_map.json, tokenizer_config.json, tokenizer.json
```

### 3.3 上传到 AutoDL

```bash
# 从本机上传到实例
scp -P 30476 -r local_model_dir root@connect.bjb2.seetacloud.com:/root/autodl-fs/

# 实例上软链到 ComfyUI
ln -sf /root/autodl-fs/llava-llama-3-8b-text-encoder-tokenizer/ /root/ComfyUI/models/LLM/llava-llama-3-8b-text-encoder-tokenizer
```

### 3.4 本地搭建（全本地运行）

如果要完全本地跑（不依赖 AutoDL）：

```bash
# 创建模型目录
mkdir -p AIGC/storage/models/ComfyUI/
ln -sf /你的ComfyUI目录/models AIGC/storage/models/ComfyUI/models
```

> **注意**：HunyuanVideo UNet 单模型就 25GB，需要至少 40GB 空闲磁盘。

---

## 四、配置详解

### 4.1 `config/config.yaml` 完整说明

```yaml
engine:
  image_provider: "mock"        # 图片生成：mock(开发) / comfyui(生产)
  video_provider: "mock"        # 视频生成：mock(开发) / comfyui(生产)
  llm_provider: "deepseek"      # 文本生成：deepseek / ollama

comfyui:
  host: "127.0.0.1"
  port: 8188
  timeout: 600

deepseek:
  model: "deepseek-chat"

ollama:
  host: "127.0.0.1"
  port: 11434
  model: "qwen3:14b-q8_0"

database:
  url: "sqlite:///storage/ai_drama.db"

storage:
  output_dir: "storage/output"          # 最终产出目录
  checkpoint_dir: "storage/checkpoints"  # 断点目录

server:
  host: "0.0.0.0"
  port: 8000

# 风格切换（切换时自动换模型+workflow+参数）
style_profiles:
  realistic:
    sd_model: "realisticVision-v51"
    video_workflow: "img2video_realistic.json"
  anime:
    sd_model: "anything-v5"
    video_workflow: "img2video_anime.json"
  ink_wash:
    sd_model: "base_model + 水墨LoRA"
    video_workflow: "img2video_ink.json"

hunyuanvideo:
  unet_model: "HunyuanVideo/hunyuan_video_720_cfgdistill_fp8_e4m3fn.safetensors"
  use_fp8: true
  default_fps: 24
  max_frames: 129
  attn_mode: "flash_attn_varlen"

human_review:
  storyboard: true      # 分镜出图前确认
  character: true       # 定妆照确认后用ControlNet
```

### 4.2 风格切换方法

```bash
# config.yaml 中修改 style_profiles 下的配置
# 或通过 API:
curl -X POST http://localhost:8000/config/style -d '{"style": "anime"}'
```

目前支持三种风格（源文件已有配置，模型需自行下载）：
- `realistic` — 写实风格（RealisticVision）
- `anime` — 动漫风格（Anything V5）
- `ink_wash` — 水墨风格（基础模型 + 水墨 LoRA）

---

## 五、运行管线

### 5.1 Phase 1：剧本 + 分镜 + 定妆照（已跑通 ✅）

```bash
cd ~/AIGC
python3 -u -c "
import sys; sys.path.insert(0, '.')
import os, asyncio, json
os.environ['DEEPSEEK_API_KEY'] = '你的key'

from agents.script_agent import ScriptAgent
from agents.storyboard_agent import StoryboardAgent
from agents.character_agent import CharacterDesignAgent

async def run():
    # Agent 1
    r1 = await ScriptAgent('deepseek').run('你的故事')
    script = r1.data
    script['episodes'] = [script['episodes'][0]]  # 只处理第1集
    
    # Agent 2
    r2 = await StoryboardAgent('deepseek').run(script)
    
    # Agent 3
    r3 = await CharacterDesignAgent().run(script, {})
    
    print(f'剧本: {script[\"title\"]}')
    print(f'分镜: {len(r2.data[\"episodes\"][0][\"shots\"])}个')
    print(f'定妆照: {len(r3.data[\"characters\"])}个')

asyncio.run(run())
" 2>&1
```

### 5.2 使用 API 运行

```bash
# 启动 API 服务
cd ~/AIGC && python3 -m uvicorn api.main:app --host 0.0.0.0 --port 8000

# 提交制作请求
curl -X POST http://localhost:8000/api/pipeline/run \
  -H "Content-Type: application/json" \
  -d '{"story": "一句话故事", "style": "realistic"}'

# 查询进度
curl http://localhost:8000/api/pipeline/status/{pipeline_id}

# 从断点恢复
curl -X POST http://localhost:8000/api/pipeline/resume/{pipeline_id}
```

### 5.3 全管线流程（概念图）

```
Phase 1 (已跑通)          Phase 2 (需要ComfyUI)    Phase 3 (需要GPU)
┌──────────────┐          ┌──────────────┐         ┌──────────────┐
│ Agent1 剧本   │ ──→     │ Agent4 出图    │ ──→    │ Agent5 视频    │
│ (DeepSeek)    │          │ (ComfyUI/SD)  │        │ (HunyuanVideo) │
├──────────────┤          ├──────────────┤         ├──────────────┤
│ Agent2 分镜   │          │ 需要模型:      │        │ 需要模型:      │
│ (DeepSeek)    │          │ - SD checkpoint│        │ - Hunyuan UNet│
├──────────────┤          │ - ControlNet   │        │ - LLM编器    │
│ Agent3 定妆照  │          │ - IP-Adapter   │        │ - CLIP-ViT    │
│ (Mock/ComfyUI) │          │ - VAE          │        │ - SVD(备选)   │
└──────┬───────┘          └──────────────┘         └──────┬───────┘
       │                                                  │
       │                    ┌──────────────────┐          │
       │                    │ Agent6 字幕       │ ←────────┘
       │                    │ (DeepSeek+FFmpeg) │
       │                    ├──────────────────┤
       │                    │ Agent7 合成       │
       │                    │ (FFmpeg合成)     │
       │                    └────────┬─────────┘
       │                             ↓
       │                     ┌──────────────────┐
       └─────────────────→   │ 🎬 最终成片        │
                             │ MP4 + 字幕封装   │
                             └──────────────────┘
```

---

## 六、产出文件对照

| 类型 | 路径 | 说明 |
|------|------|------|
| **剧本文档** | `storage/checkpoints/script_agent_checkpoint.json` | 剧本 JSON（标题/角色/集数/对话） |
| **分镜文档** | `storage/checkpoints/storyboard_agent_checkpoint.json` | 分镜 JSON（景别/时长/sd_prompt） |
| **定妆照** | `storage/output/xxxx.png` | Mock 或 ComfyUI 产出的角色正面照 |
| **出图** | `storage/output/ep{N}_shot{M}.png` | 逐帧出图（待实现） |
| **视频** | `storage/output/ep{N}_shot{M}.mp4` | 图生视频（待实现） |
| **字幕** | `storage/output/ep_{N}.srt` | 对白字幕（待实现） |
| **合成片** | `storage/output/ep_{N}.mp4` | 最终成品（待实现） |

**AutoDL 上模型存放路径：**

```
/root/autodl-fs/
├── llava-llama-3-8b-text-encoder-tokenizer/    ← LLM 15GB
│   ├── config.json
│   ├── model-00001-of-00004.safetensors  (4.6GB)
│   ├── model-00002-of-00004.safetensors  (4.7GB)
│   ├── model-00003-of-00004.safetensors  (4.6GB)
│   ├── model-00004-of-00004.safetensors  (1.1GB)
│   └── tokenizer_config.json, tokenizer.json, ...

/root/autodl-tmp/models/
├── unet/HunyuanVideo/          ← UNet 25GB
├── checkpoints/SVD-XT.safetensors     ← SVD 9GB
├── clip/clip-vit-large-patch14/       ← CLIP 1.6GB
└── vae/                                ← VAE 471MB
```

ComfyUI 软链：`/root/ComfyUI/models -> /root/autodl-tmp/models`

---

## 七、API 参考

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/pipeline/run` | POST | 提交新制作任务 |
| `/api/pipeline/status/{id}` | GET | 查询任务进度 |
| `/api/pipeline/resume/{id}` | POST | 从断点恢复 |
| `/api/pipeline/list` | GET | 历史任务列表 |
| `/config/style` | POST | 切换风格画风 |
| `/health` | GET | 健康检查 |

---

## 八、开发说明

### 8.1 添加新 Agent

1. 在 `agents/` 下创建 `xxx_agent.py`，继承 `Agent` 基类
2. 实现 `async def run(self, ...) -> AgentResult`
3. 在 `pipeline/scheduler.py` 的 `Pipeline.run()` 中添加条件判断
4. 构造函数的 `name` 属性与调度器中的判断条件保持一致

### 8.2 Mock 模式

开发阶段 `config.yaml` 设为 `mock`，可跳过出图/视频步骤：

```yaml
engine:
  image_provider: "mock"
  video_provider: "mock"
```

### 8.3 断点续跑

管线每完成一个 Agent 自动保存 checkpoint 到 `storage/checkpoints/`。
传入 `resume=True` 即可从上次中断处恢复：

```python
result = await pipeline.run(agents, user_input, resume=True)
```

### 8.4 关键配置路径

| 配置项 | config.yaml 路径 | 说明 |
|--------|-----------------|------|
| 图片提供者 | `engine.image_provider` | mock / comfyui |
| 视频提供者 | `engine.video_provider` | mock / comfyui |
| LLM提供者 | `engine.llm_provider` | deepseek / ollama |
| ComfyUI地址 | `comfyui.host:port` | 默认 127.0.0.1:8188 |
| 输出目录 | `storage.output_dir` | 默认 storage/output |
| 风格 | `style_profiles.{name}` | realistic/anime/ink_wash |

---

## 九、运行记录

| 时间 | 阶段 | 结果 | 备注 |
|------|------|------|------|
| 2026-06-20 | Phase 1 管线 | ✅ 剧本→分镜→定妆照 全部跑通 | DeepSeek API + Mock Image |
| | Agent 1 剧本 | ✅ 《猫之异界：程序员的逆袭》3集 | 每次生成内容不同（LLM随机性） |
| | Agent 2 分镜 | ✅ 第1集 22个分镜（中8/近6/远4/特写3/俯拍1） | 默认标准化后合法 |
| | Agent 3 定妆照 | ✅ 4个角色（林风/白露/黑曜/雪莉） | Mock生成占位图 |
| | AutoDL | ✅ LLM 15GB 上传至 /root/autodl-fs/ | 通过软链映射到 ComfyUI |
| 2026-06-20 | 环境配置 | ✅ Python 3.14 / httpx / FastAPI 可用 | 无需虚拟环境 |
| | | ⚠️ python-dotenv 依赖未安装 | 直接用 os.environ 设置 key |
| 2026-06-20 | 完整管线（Mock） | ✅ Agents 1→2→3→4→5→6→7 全跑通 | Mock模式，最后FFmpeg因 Mock mp4 无真实流报错 |
| | Agent 1 | ✅ 《程序员的猫娘奇遇记》4角色 | 6集生成，保留第1集（20分镜） |
| | Agent 2 | ✅ 第1集 20个分镜 | 校验器标准化 shot_type |
| | Agent 3 | ✅ 4个角色定妆照 | Mock生成占位PNG |
| | Agent 4 出图 | ✅ 20张图 | Mock生成占位PNG |
| | Agent 5 视频 | ✅ 20段视频 | Mock生成5秒假MP4 |
| | Agent 6 字幕 | ✅ 1个SRT | 从分镜对话生成时间轴 |
| | Agent 7 合成 | ⚠️ FFmpeg失败 | 假MP4无真实流，ComfyUI下无此问题 |

---

## 十、常见问题

**Q: DeepSeek API 返回 401？**
A: 检查 `.env` 中的 API Key，确保完整复制没有省略号。

**Q: 分镜生成一直卡住？**
A: 脚本多集时速度较慢（每集调一次 DeepSeek，约 10-20s/集）。建议测试时只保留第1集。

**Q: 磁盘不够放模型？**
A: LLM 文本编码器 15GB 是必须的。可以通过 `config.yaml` 的 `hunyuanvideo.use_fp8: true` 减少显存占用。

**Q: 本地没有 GPU 能跑吗？**
A: Phase 1（剧本+分镜+定妆照）不需要 GPU，完全可用。Phase 2/3 需要 NVIDIA GPU 24GB+。
