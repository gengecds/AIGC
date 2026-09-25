# CHANGELOG

## 2026-09-25 — 管线收尾：优雅关闭 + 出片时长精度 + 超分接回

### 背景
四条独立缺陷长期叠加：① SSE 长连接永不结束，`uvicorn.run(reload=True)` 热重载卡在
`Waiting for connections to close`（实际挂死），改后端代码只能 `kill -9` 再启动；② 成片时长与配音
总时长对不齐，`setpts=PTS*ratio` 变速后帧间隔不再均匀，这种非均匀 PTS 直接喂给 `xfade`（按时间戳做
alpha 混合）会在转场处插入 0.4~1.16s 的保持帧，表现为画面冻结；③ 上传参考音频返回的
`duration_seconds` 恒为 0，前端拿不到试听时长；④ 超分（`AIGC_SUPER_RES`）在 `_cinematic_compose`
被删除后成为死代码簇——`use_super_res` 只赋值、无人读取，开关实际无效。

### 完成
- **优雅关闭机制（新增 `api/shutdown.py`）**：提供 `shutdown_event` / `INTERRUPTED` /
  `request_shutdown()` / `reset_shutdown()` / `install_signal_hook()` / `wait_or_shutdown()`。
  触发点是**信号处理器**（SIGINT/SIGTERM，先 `request_shutdown()` 再链式调用 uvicorn 原 handler），
  而非 lifespan 退出——lifespan 退出太晚，连接早被 `timeout_graceful_shutdown` 硬 cancel。
  `wait_or_shutdown(awaitable, timeout)` 用 `asyncio.wait(FIRST_COMPLETED)` 竞速，外层取消时把
  内部 task 一并 cancel。
- **`api/main.py`**：lifespan 内 `reset_shutdown()` + `install_signal_hook()`，`yield` 后
  `request_shutdown()`（通知 SSE 长连接收尾）再 `restore_signals()`；`uvicorn.run()` 增加
  `timeout_graceful_shutdown=3`。热重载不再挂死，改后端代码可直接靠 reload 生效。
- **SSE 客户端资源清理（`api/routes/pipeline.py`）**：`sse_event_sender` 的循环体包进
  `try/finally`，客户端断开/服务端关闭都会取消生成器，在 `finally` 里把队列从 `_sse_clients` 摘掉。
  修复前 `_sse_broadcast` 会永远往死队列塞事件，队列只增不减（内存泄漏）。
- **出片时长精度对齐（`agents/video_compose_agent.py`）**：每段视频在 `xfade` 前插入 `fps=25`
  CFR 归一化，消除非均匀 PTS 造成的冻结帧；变速基准改为各读自己的**流时长**
  （`_probe_duration(path, "v:0"/"a:0")`）——LTX 输出容器时长比视频流长约 0.27s，混用会导致累计偏移。
  非末段多留 `XFADE_TRANSITION`(1.0s)，使总时长 = Σ分镜时长、第 i 镜起点 = Σ前 i 个分镜时长，与 SRT 严格对齐。
- **音频时长元数据补全（`api/routes/audio_router.py`）**：新增 `_probe_audio_duration()`
  （ffprobe 实测，读不到兜底 0.0），`upload_reference` 返回 `duration_seconds`。
- **删死代码（`agents/video_compose_agent.py`）**：删除 `_cinematic_compose()`（84 行）与模块常量
  `FADE = 0.4`——前者早已不在任何调用路径上。
- **超分接回（`agents/video_compose_agent.py`，方案 B）**：`AIGC_SUPER_RES` 重新生效。
  `_super_resolve()` 重写：缩放改为 `scale=iw*2:ih*2:flags=lanczos`（保比例 2x；原写死
  `scale=1024:1024` 会把 576x1024 竖屏拉成方块），放大 / unsharp 锐化 / 电影化滤镜合并为**一次编码**
  （原来分两次，多一个中间文件 + 一代画质损失）；失败时保留原成片并返回 `False`，成功用
  `Path.replace()` 原子替换（原来是先 `unlink` 再 `rename`，中途失败会丢成片）。接入点在
  `run()` 中增强合成成功之后、烧字幕之前——字幕 PNG 按成片分辨率渲染后叠加，才不会被放大/颗粒二次处理糊掉文字。
- **仓库整洁**：删除 5 个 `.bak` 冗余文件（`frontend/app.js.bak`、`frontend/index.html.bak`、
  `frontend/style.css.bak`、`frontend/prototype_bak.html`、`providers/comfyui/client.py.bak`）与
  11 个 `.DS_Store`；`.gitignore` 追加 `*.bak`。

### 验证
- **出片时长**：39 段素材合成 `/tmp/verify_fixed3.mp4`，冻结帧 10 → **0**；容器 111.600s /
  视频流 111.600s / 音频流 111.428776s，与 SRT 末尾 111.600s 一致。
- **超分（真实跑完整 `run()`：3 段 576x1024 竖屏 + 音轨 + SRT，分镜各 4s）**：

  | 检查项 | 结果 |
  |---|---|
  | 分辨率 | 576x1024 → **1152x2048**（严格 2x，比例未变形） |
  | 时长 | **12.000000s** = Σ分镜 4+4+4，xfade 重叠被正确吸收 |
  | 音轨 | `aac` 保留 |
  | 字幕 | 字幕帧亮像素 8051 / 空档帧 0；文字包围盒 634x86px，距底边 60px 未出画 |
  | 电影化滤镜 | 顶部 y=5、底部 y=2043 取样纯黑（遮幅生效），中部为画面内容 |
  | 失败兜底 | 传不存在路径 → 返回 `False`，原成片不动，不抛异常 |
  | 开关 | `AIGC_SUPER_RES=0` → `use_super_res = False` |
  | 临时文件 | 无 `*_sr.mp4` / `*_hd.mp4` 残留 |
- **回归**：`python -m unittest tests.test_ffmpeg_enhanced` → `Ran 3 tests / OK`（含新增断言
  「每段必须出现一次 `fps=25`」）；`tests/test_pipeline_light.py` 10 个 Agent 数据流完整性验证通过；
  `ast.parse` + 模块导入通过。

### 已知副作用（用户决定暂不处理）
- `CINEMATIC_FILTER` 的上下 7% 遮幅黑边**只在超分开启时出现**（增强主链用的是 colorgrade/eq，不含黑边）。
  字幕位置固定在底部，实测文字包围盒 86px 高中有 83px 压在黑带上——白字压纯黑可读性更好，但
  `_burn_subtitles` 原本的「半透明圆角黑底条」在黑带上不可见。修复只需把
  `_burn_subtitles` 的 `y = H - img.height - int(H*0.02)` 上移 7% 画布高度。**用户明确要求先不改字幕位置。**

## 2026-09-19 — 成片管线：并发幂等去重 + 取消真正中断

### 背景
前端重复点击提交 / 多标签同时提交，会为「同一输入 + 同一风格」起多条并发管线；而本机 LTX 出片是串行跑的
（24GB 内存，两条并发互相挤爆 swap）。另外 `/pipeline/cancel` 原先只把 `_active` 里的状态改成 cancelled，
既不 cancel asyncio 任务、也不杀 LTX 子进程，表现为「取消后仍在跑」「管线看似已结束却继续落盘、稍后复活」。

### 完成
- **`api/routes/pipeline.py` 幂等去重**：新增活跃态常量 `_LIVE_STATUS`（queued/running/review）与
  `_find_live_pipeline(text, styles)`；同输入同风格且仍在跑时直接复用已有 `pipeline_id`，响应带 `duplicated: true`；
  `_active[pid]` 记录 `input` / `styles` 作为去重依据。
- **取消真正中断**：新增任务句柄表 `_pipeline_tasks: dict[str, asyncio.Task]`（登记后台任务 + `add_done_callback`
  完成后自动摘除）；`cancel` 路由重写为 `task.cancel()`（中断执行） + `pipe.cancel()`（唤醒审核断点）
  + `_set_job_status(job_id, "cancelled")`（DB 终态收尾） + 广播 `pipeline_cancelled` + 清理 `_active` / `_pipeline_instances`。
- **`_execute` 取消收尾**：新增 `except asyncio.CancelledError` 分支。`CancelledError` 继承自 `BaseException`，
  不会被 `except Exception` 捕获，必须单独收尾，否则任务静默消失、引用残留；分支内清引用后原样 `raise`。
- **`pipeline/scheduler.py` 合作式取消**：`Pipeline` 新增 `_cancelled` 标志、`cancel()`、`_check_cancelled()`；
  `run()` 在每个 agent 执行前检查一次，`wait_for_review()` 进入/退出各检查一次；`cancel()` 内 `_review_lock.set()`
  唤醒审核等待者，避免 clear/set 竞态导致永久死等。
- **`providers/ltx_mlx_provider.py` 子进程兜底**：`generate()` 读日志 + `proc.wait()` 改为 try/finally，
  若 `proc.returncode is None` 则 `proc.kill()` + `await proc.wait()`，防止取消后 `ltx-2-mlx` 变孤儿进程
  继续吃内存并往 output 目录写文件。

### 验证
- 静态检查：`compileall` + 三模块 `import` 全部通过。
- 隔离 harness：审核断点上 `pipe.cancel()` 能唤醒并以 `CancelledError` 退出；子进程探针在取消后被兜底杀掉、无孤儿。
- 真实路由（后端 8888 实测）：重复 POST 返回同一 `pipeline_id` + `"duplicated": true`；
  cancel 后 `_active` 为空、日志出现「🛑 收到取消请求」「管线已取消，执行已中断」、取消点之后再无任何 agent 活动、
  0 个 LTX 进程、二次 cancel 返回 404、DB job 状态为 `cancelled`。

### 已知遗留
- ~~`api/main.py` 使用 `uvicorn.run(reload=True)`，但永不结束的 SSE 长连接会让热重载卡在
  `Waiting for connections to close`（实际挂死）；改后端代码后需硬重启（`kill -9` 再启动），不能指望热重载生效。~~
  **已于 2026-09-25 修复**：新增 `api/shutdown.py` 优雅关闭信号 + `timeout_graceful_shutdown=3`，
  见上方条目。

## 2026-09-14 — 成片时长对齐配音：预测量 + 按台词行拆镜

### 背景
成片时长与配音总时长对不上（成片约 111.32s vs 配音 111.60s），且配音按固定 slot 排布，
出现「有声音但和人物对不上」——时间轴与画面不同步。

### 完成
- **改为「时长由配音决定」**：`AudioAgent.plan_voices(script)` 在 storyboard 之前先跑一遍 TTS 时长预测量，
  按 `slot = 台词时长 + LINE_GAP(0.4s)` 产出 `voice_plan` 并透传下游。
- **`pipeline/scheduler.py`**：storyboard 分支用 `asyncio.to_thread(plan_voices)` 执行预测量，
  并用 `asyncio.run_coroutine_threadsafe` 把进度回推进度回调。
- **`agents/storyboard_agent.py`**：接收 `voice_plan`，按台词行拆镜，使分镜时长与配音 slot 对齐。
- **`agents/audio_agent.py`**：`synthesize(voice_plan=...)` 用 `voice_cache`（按台词文本 `setdefault`）
  复用预测量阶段已合成的 WAV，避免重复 TTS。
- **前端**：新增 SSE 事件 `voice_plan_progress`，`frontend/app.js` 监听并展示预测量进度。

### 验证
- 出片验收：`storage/output/ep_1_final_audio.mp4`（约 111.32s，h264 576x1024 25fps + aac 44100Hz mono）；
  `storage/output/ep_1.srt` 共 39 条，0.000 → 111.600s，逐句不重叠；`publish_manifest.json` segments = 39。

## 2026-08-30 — Phase 3 · 视频层 Skills 接入 + 题材/用途配方深化

### 背景
用户强调「我提供给你的……需要你去分析和梳理」，不能照抄零散片段。此前 image 题材层已接入；
本轮把最碎片化的 `video/genre`、`video/use` 两个维度，按「每个维度 = 一套可执行系统」重写，
并把 video 层词块真正接入 `video_agent` 生成管线（此前 video 层完全没进数据流，LTX 收到空 prompt 回退默认值）。

### 完成
- **统一骨架**：每个维度拆成若干可独立执行的子维度（原每类仅 1~2 个模糊短语，AI 只能自由发挥）。
- **video/genre.yaml 重写**：6 类题材（影视与综艺 / 新闻与资讯 / 生活与Vlog / 知识与教育 / 娱乐与游戏 / 科技与数码），
  每类 6 维配方（镜头惯例 / 光影色调 / 节奏与剪辑 / 构图特色 / 氛围与配乐 / 负向约束）。
- **video/use.yaml 重写**：5 类用途（广告与宣传片 / 监控与安防 / 医疗与工业 / AI训练 / 直播带货），
  每类 6 维配方（制作目的 / 镜头惯例 / 光影色调 / 节奏与剪辑 / CTA氛围 / 负向约束）。
- **image/scene.yaml 补构图**：新增「构图与取景」分组（纵深与分层 / 前景框架 / 广角与透视 / 大远景交代镜头 /
  对称与居中 / 三分法与引导线 / 留白与减法），共 7 条。
- **resolver.py 视频层自动匹配**：新增 `shot_camera_name/block`、`shot_genre_name/block`、`shot_use_name/block`，
  据分镜 `camera_movement/scene/action/background/sd_prompt` 用中英文关键词自动推断运镜/题材/用途分组，
  复用 `_hit_kw` 统一匹配规则（中文=子串 / 英文=单词边界）。
- **video_agent.py 接入**：构造 `video_data` 时用 `shot_map[sid]` 反查分镜，`_shot_prompt()` 拼装
  「sd_prompt + 运镜/题材/用途词块」写入 `prompt` 字段，使 `ComfyLTXVideoProvider` 不再回退默认 "cinematic motion..."。

### 修复
- **video/genre.yaml、video/use.yaml 的 note 内嵌 ASCII 双引号**：导致整文件 YAML 解析失败（该维度全部词块丢失），
  已改为全角引号「」。

### 验证
- `resolver` 三个新接口对样例分镜（缓慢前推 / Vlog 记录 / 手机广告 / 环绕广告）均正确输出对应词块。
- `video_agent.py` 通过 `py_compile`、AST 解析、模块 import 三连验证。

## 2026-08-30 — Phase 2 · Skills 知识库扩充

### 背景
用户反馈「分镜出图提示词太简洁、AI 抓不住想要的画面」，以「人物与肖像」为例，旧词库只拆出
`cinematic close-up portrait · natural skin texture · group portrait · layered depth · shallow focus · clean studio headshot · neutral background`
7 个短语。问题在于：① 特写 / 群体 / 证件照三种不同景别被串成一条，语义自相冲突；② 每类仅 1~2 个词，
缺乏镜头参数、光线方向、皮肤质感、眼神等细节，AI 只能自由发挥，无法复现意图。

### 完成
- **skills/image/topic.yaml 重写**：把「人物与肖像」拆成三个可独立出图的完整题材，每个题材提供景别构图 /
  镜头景深 / 光线方向 / 皮肤真实质感 / 眼神微表情 / 头发细节 / 背景分离 / 画质后缀等 8 层英文细节，
  词条间可无缝叠加、不冲突；同步细化「自然与风景」「动植物」「建筑与城市」「商品与静物」「艺术与抽象」。
  - `人物与肖像·单人电影特写`（85mm f/1.8、伦勃朗主光、可见毛孔/次表面散射、发丝、奶油虚化）
  - `人物与肖像·群体合影`（前后错位层次、统一光线、人脸一致性、景深全脸清晰）
  - `人物与肖像·棚拍证件照质感`（端正居中、统一柔光、无畸变 85mm、纯灰渐变背景）
- **resolver.py**：`_GROUP_FILES` 已纳入第一阶段新增的 `image/scene.yaml`、`video/camera.yaml`、`video/motion.yaml`；
  `category_block()` / `list_categories()` 已验证可正确返回新拆分的题材词块（`人物与肖像` 旧名已拆分故返回空）。
- **题材词块自动注入**：`resolver.py` 新增 `shot_topic_block(shot)`，据分镜 `characters` 数量（单人→单人电影特写 /
  多人→群体合影）及 `scene/action/background` 关键词（自然/动植物/建筑/商品/艺术）自动匹配题材；
  `image_agent.py` 的 `_shot_skills()` 已无条件叠加该题材词块，与 photoreal/anatomy/emotion 共同拼入 `sd_prompt`，
  解决「提示词只有景别、缺题材摄影细节」问题。
- **无人物镜头的兜底匹配**：`shot_topic_name()` 对无人物镜头先用 `scene/action/background` 中文匹配，未命中再兜底匹配
  英文 `sd_prompt`；`_TOPIC_KEYWORDS` 为每类补英文关键词，英文用单词边界（防 `fish` 误命中 `selfish`），中文用子串。

### 修复
- **前端加载失败**：config.yaml 端口 8000→8888；api/main.py 补 CORSMiddleware；
  frontend/src/views/PipelineView.vue `API_BASE` 8000→8888；frontend/app.js `SK_DIM_LABELS` 补充新维度中文标签。
  重启后端后 skills 各维度接口均返回 200。

### 定位结论（出图效果分析）
旧 7 词拼接后，同一 prompt 同时要求「特写 + 群体 + 证件照」，SD 无法同时满足 → 大概率生成一张
「一群人排排站、脸部被裁切、背景又说要净又要虚」的混乱图；且无光圈/光线方向/皮肤/眼神细节，
人物呈现塑料感、眼神空洞、背景与主体不分层。新词库按题材分块给出可直接执行的摄影参数，出图可控、可复现。

## 2026-06-19 — Phase 1 完成

### 新增
- **MockProvider**: MockImageProvider + MockVideoProvider（0 API 费用，占位验证数据流）
- **LLM Provider**: DeepSeekProvider + OllamaProvider
- **Agent 1 - ScriptAgent**: 剧本生成（DeepSeek）
- **Agent 2 - StoryboardAgent**: 分镜生成 + ShotValidator 校验层（shot_type/duration/prompt）
- **Agent 3 - CharacterAgent**: 角色定妆照 + 双轨制资产判断（is_asset_library）
- **Agent 4 - ImageGenAgent**: 批量出图骨架（Mock）
- **Agent 5 - VideoGenAgent**: 图生视频骨架（Mock）
- **Agent 6 - SubtitleAgent**: SRT字幕生成
- **Agent 7 - ComposeAgent**: FFmpeg 视频合成骨架
- **Pipeline 调度器**: 支持断点恢复 + 7 Agent 串联
- **API 路由**: POST /api/v1/pipeline/run, GET /api/v1/pipeline/status
- **Vue 3 前端**: Vite 脚手架 + PipelineView（进度展示）
- **tests/test_pipeline.py**: 管线验证脚本（无 API key 用预设数据）

### 修改
- config.yaml: engine → mock, 移除通义万相, 加 style_profiles + human_review
- db/models.py: Character 加 is_asset_library 字段
- providers/comfyui/client.py: 重写为异步回调队列版本
- agents/base.py: Agent 继承 BaseAgent

### 移除
- 通义万相所有残留引用（6处）
- 错误的 sed 产物（FFmpeg真合成、MockProvider返回假...等乱文件）

## 2026-06-19 — Phase 0 脚手架
- 项目目录搭建
- ComfyUIClient 骨架
- DB models
- FastAPI 入口
- config.yaml
- README + CHANGELOG
