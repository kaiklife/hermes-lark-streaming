# 更新日志

## v2.0.32（2026-11-06，kai fork）—— 二轮审查后的收敛

| 类型 | 变更 |
|------|------|
| 修复 | **回显判据收紧到真源**：hermes 只有 `terminal` 发裸围栏块（`run_turn_progress.py:275`），且围栏恒为裸 ```。v2.0.31 的「emoji+任意小写词」标题行会把 `✅ done` / `💡 tip` 后的**答案代码块**当回显吞掉（正文丢失）。现在只认 `<emoji> terminal` 标题 + 裸围栏，带 info string 的一律是正文 |
| 修复 | **回显不再进面板预算**：剥掉即弃。面板已有真实工具行，塞命令正文会把 12 行预算挤满（实测 20 个回显挤掉 `read_file` 行） |
| 修复 | **clarify 恢复正文重复根治**：`_merge_resumed_answer` 补「段内增长」重叠拼接（base 尾与 snap 头重叠时只接增量）。hermes 边界会 reset accumulated，snap 逐帧生长，旧版每帧复制一遍尾段（平方级膨胀） |
| 修复 | **空 seed 帧不再阻断复活**：`has_content` 只用于「撤落定窗口」，复活段无条件执行。否则 clarify 窗口内封卡 + 空 seed → 同回合又变两张卡 |
| 修复 | **GC 冷却对齐复活窗口**：已定稿状态回收从固定 60s 改为 `_REVIVE_WINDOW_SEC`（600s），否则长工具回合（>60s）复活必然落空，机制等于没修 |
| 修复 | **其余正文写入点统一过剥离**：`_create_card` 首段 / `_patch_answer`（edit 通道）/ `_finalize` 终值 |
| 修复 | **耗时回灌去掉锁与即时整卡重建**：只写 `tool_durations` + bump rev，渲染交给下一帧/定稿（工具密集回合不再堆几十次 full rebuild）。旧快照由 `rev`/`finalized` guard 拦 |
| 修复 | 复活卡定稿跳过二次 `close_streaming`（`skip_close`）——已关过的卡再关是未验证路径；页脚照常重插（全量重建会抹掉旧页脚） |
| 修复 | 看门狗句柄去重（每 chat 至多一个 pending，docstring 语义真正落地） |
| 新增 | `scripts/probe_closed_card.py`：真飞书探针（只建卡不发送），复核「关流式后能否 update / 插页脚」 |
| 验证 | 全量 pytest **787 passed**（3 个失败为 HEAD 既存）；`test_v2030_kaifork_cardfix.py` 20 项全绿，新增「答案自己的 ```python 块不被吞」「非 terminal emoji 行算正文」回归 |

## v2.0.31（2026-11-06，kai fork）—— 一轮审查后的修正

| 类型 | 变更 |
|------|------|
| 修复 | **回显剥离换了判据**：v2.0.30 按 ```` ```plain_text ```` 精确匹配是错的——hermes 进度块是**裸围栏**（`run_turn_progress.py:289` 无 info string），`plain_text` 只是飞书把无语言围栏渲染出来的样子，全机 `grep '```plain_text'` 零命中 → 旧逻辑永不触发。改为 `_detach_tool_echoes`：只认「单行 emoji+工具名标题 + 紧随围栏块」这一 hermes 专属形态（真实回答不会单独出现 `💻 terminal` 这种行），并顺带吃掉闭合围栏后的 `(×N)` 去重后缀 |
| 修复 | **复活不再重复拼正文**：`_merge_resumed_answer` 幂等化——快照已包含 base（工具边界定稿后复活 / 同段重放）直接返回快照，不再 `base + 快照`。审查给出实测反例：clarify + 段定稿 + 复活 → `A B / B C` |
| 修复 | **空终帧不再拆掉落定窗口**：hermes 会发 `send_stream_frame("", finalize=True)` 关空气泡；旧逻辑无条件撤窗口 → 卡停在未定稿态等 120s watchdog。现在只有带内容（answer 或 progress 非空）的帧才撤销，且要求 turn 上下文存在 |
| 修复 | **耗时回灌持 chat 锁**：`_feed_tool_duration` 原先是唯一不持锁的 `_patch_status` 写者——27s 挂起能与 finalize 并行的根因 |
| 修复 | 补齐 `rev` bump：`_on_interim` / `suspend_for_clarify` / `_feed_tool_duration`（旧版漏掉 → 带 `💬 等待你的选择...` 占位的旧快照能通过护栏落盘） |
| 修复 | 复活后重挂 watchdog（`_arm_watchdog`），长回合复活卡不再无人兜底 |
| 验证 | 真飞书 API 探针（只建卡不发送，零可见噪声）：`stream_element` 关流式后 → 300309 拒绝；**`cardkit_update` 关流式后 → OK**（连续两次 OK）；`batch_update` 插页脚 → OK。复活通道成立 |
| 验证 | 全量 pytest **785 passed**（3 个失败为 HEAD 既存，改动前即挂）；`test_v2030_kaifork_cardfix.py` 17 项全绿 |

## v2.0.30（2026-11-06，kai fork）

| 类型 | 变更 |
|------|------|
| 修复 | 一回合多卡：同 `turn_id` 的后继帧撤销落定窗口；已按段定稿的同 turn 卡**复活**（流式已关 → 走 `cardkit_update` 全量重建，`stream_element` 会 300309）。实测「为什么昨晚有个空卡片」一个提问曾产出 3 张卡（答案 554/1690/1291、页脚 1m15s/2m12s/1m33s 互相矛盾） |
| 修复 | 正文堆命令回显：native 进度 overlay 的裸 ```` ```plain_text ```` 围栏块混进 answer 快照 → 正文 20+ 个命令块、面板只记 7 个工具（真实 54 次）。新增 `_strip_command_echo`：按 info string 精确剥离并回灌面板（真实代码块带语言名/裸围栏，不受影响） |
| 修复 | 旧快照擦卡：面板全量重建请求挂 27s，定稿后被 300317 自愈重发，按旧快照整卡重建 → 答案抹回占位、页脚消失（11-05 22:47 空卡）。`call_with_seq_recovery` 新增 `guard`：`st.rev` 变化或已定稿即拒绝（含自愈重发） |
| 新增 | `CardTurnState` 三个字段：`rev`（内容版本）、`finalized_at`（复活窗口基准）、`rebuild_only`（关闭流式后的重建通道） |
| 验证 | 全量 pytest 781 passed（3 个失败为 HEAD 既存：`test_v210_notice_rows` / `test_v212_layout_md`×2，与本次改动无关）；新增 `tests/e2e/test_v2030_kaifork_cardfix.py` 13 项 |

## v2.0.29（2026-10-27）

| 类型 | 变更 |
|------|------|
| 说明 | V2 线唯一发版条目，合并 v2.0.0 → v2.0.28 全部迭代；V1（1.8.4）→ V2 升级步骤见 README「从 V1 升级」，agent 手册见 SKILL.md 第 6 节 |
| 重构 | standalone 钩子插件（运行时 monkey-patch GatewayRunner / FeishuAdapter）→ 正规 platform 插件：`FeishuStreamingAdapter` 继承内置 `FeishuAdapter`（WebSocket/webhook、媒体、threads、群门控、cron 投递能力零损失），官方 `register_platform` 协议注册；删除 `patching/`、`controller/`（钩子与 token 级控制器）；平台名 `feishu-streaming` 与原生 `feishu` 二选一，改回即整体回滚，无运行时残留 |
| 重构 | 新增 composer 帧重组引擎：官方 metadata 四通道分类（answer 流 / progress 面板 / interim / final）→ CardKit v2.0 卡片生命周期，per-chat 帧串行化；clarify 交互卡片闭环（select/input/button/retry 四分支 + 操作者授权 fail-closed） |
| 新增 | native streaming 通道：适配器声明官方 `SUPPORTS_NATIVE_STREAMING`，答案流走 `send_stream_frame` 原生帧——工具边界不封卡、单卡全回合；任何帧失败自动回落 edit/send；`native_streaming: false` 一键回 edit-based |
| 新增 | agent loop 状态面板：工具行/思考行实时渲染（真实 progress 模板结构化解析、思考按轮分组、单工具耗时、官方图标 token、思考正文 markdown 渲染）；纯 answer 回合面板懒渲染（无空壳）；DeepSeek reasoning 隐藏流经官方 `on_stream_delta` 观察者钩子回灌面板（`stream_reasoning_deltas: true`） |
| 新增 | 卡片 reply 引用触发消息（私聊/群聊；锚点非 `om_` 或 reply 失败自动降级直发）；cron 定时投递卡片化（任务名标题 + job_id 溯源页脚）；回合外投递与 ≥200 字长文一次性静态卡（`oneshot_cards` 可关）；子代理失败/超时通知并入面板或 early-create 回合卡；页脚 `reasoning` 推理等级；`card_style.text_size` 自定义字号（完全 opt-in，未配置零注入） |
| 新增 | clarify 暂停/续流：澄清等待期卡片保持「进行中」+ 面板暂停行，选择后**同一张卡**续流至定稿，一次澄清回合一张卡（`clarify_suspend_sec`，默认 1.2s，0 = 关闭） |
| 修复 | 稳定性主线（真飞书实测沉淀）：300317 sequence 冲突对齐重发自愈、长回合断卡（GC 回收条件）、burst 帧多孤儿卡（帧串行化）、一问多卡（stream_element 空串守卫）、emoji 开头 answer 快照误拆进面板（行语法扫描根治）、status 通报幽灵卡、孤儿卡看门狗（回合外投递永不挂卡）、clarify fallback 假确认、双 finalize 幂等、200 元素上限裁剪、流式光标剥除、思考行吸附与答案复读过滤、围栏内文本不被误改写、长文切点围栏闭合校验 |
| 兼容 | hermes-agent ≥ v0.22（`SUPPORTS_NATIVE_STREAMING`；启动 fail-fast 探测，失配即报点位）；plugin.yaml 声明 `python_dependencies`（PEP 508）；`requires-python >=3.11,<3.15`（官方运行时 3.14）；lark-oapi ≥ 1.6.4（≥1.7.x 回调类路径迁移已多候选兼容） |
| 清理 | V1 遗留死代码/死配置/死测试分批清除（钩子层约 2700 行 + 后续累计约千行）；删除 `state/`、`flush/` 遗留包 |
| 文档 | 文档体系重组：README（人类向：项目说明 + 部署/升级）、SKILL.md（agent 向：安装/验证/升级手册）、API.md（配置键位 + 出站帧契约）、CHANGELOG.md 同归 docs/；CI：Gitee → GitHub 全分支 + tag 每小时强制镜像、PR/Issue 双向飞书通知、hermes 上游发版飞书监控 |
| 已知 | 页脚 cost/tokens/cache 字段在 V2 数据契约下不可达（配置自动省略）；卡片转发复制为飞书平台默认能力（`enable_forward`），插件不额外干预 |
| 验证 | 真飞书 E2E 11 场景（`scripts/e2e_v2.py`，hermes v0.21.6/v0.22 + Python 3.14）；全量 pytest 771 passed |
