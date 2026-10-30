# OpenAtoms 分阶段实施计划

> 配套：`docs/ARCHITECTURE.md`
> 原则：每个阶段结束都能**端到端跑起来、可以演示**；先打通纵向主干（输入 → Agent → 沙箱 → 预览），再横向扩展角色和功能。
> 工作量按 1 名全栈工程师加 AI 辅助估算，仅供参考。

---

## 里程碑总览

| 阶段 | 名称 | 演示目标 | 估时 |
|---|---|---|---|
| P0 | 地基 | `docker compose up` 起全套依赖；前后端 hello world；类型共享 | 3 天 |
| P1 | 账号与项目 | 注册登录，dashboard 输入框创建项目，进入空工作台 | 4 天 |
| P2 | **单 Agent 纵向打通** | Lite 模式：Alex 在沙箱里写一个 React 页面，并流式展示思考和工具调用 | 2 周 |
| P3 | 工作区面板与预览 | 右侧 AppViewer 实时预览、Editor、Terminal、文件树，历史可回放 | 1.5 周 |
| P4 | 版本与发布 | 每轮生成版本，可恢复；一键发布到 `*.app` 子域名 | 1 周 |
| P5 | **多智能体团队** | Full 模式：Mike 规划，Emma/Bob/Alex/David 协作；审批卡片；计划模式；队列；停止、重试、编辑 | 2.5 周 |
| P6 | 集成与应用后端 | Supabase、GitHub、Secrets；app-sdk 加 Cloud（entities/auth/storage/ai） | 2 周 |
| P7 | 商业化与协作 | 积分计费、Stripe、工作区成员、分享回放、Remix、发现页 | 2 周 |
| P8 | 生产化 | K8s 沙箱 + gVisor、休眠唤醒、预热池、配额、监控告警、压测 | 2 周 |
| P9+ | 进阶 | BoN 竞赛、可视化编辑、深度研究、全栈容器发布、自动化任务、多模态 | 持续 |

关键路径：P0 → P2 → P3 → P5。P1、P4、P6 可以和它们穿插进行。

---

## P0 地基（3 天）

**任务**
1. 建 monorepo：pnpm workspace（`apps/*`、`packages/*`）加 Python uv 项目（`services/server`），Go 模块（`apps/gateway`、`services/sandboxd`）。
2. `deploy/docker-compose.yml`：postgres:16、redis:7、minio、mailpit（本地收邮件）、litellm。
3. `services/server`：FastAPI 骨架，挂载 python-socketio 到 `/socket.io`；统一响应 `{code, message, data, now_ts}`；中间件处理 `X-Request-ID`、日志和异常；Alembic 初始化。
4. `apps/web`：Next.js + Tailwind + shadcn/ui 初始化；落地设计 token（CSS 变量，含亮色和暗色）、字体、基础组件（Button/Input/Card/Dialog/Tooltip/Avatar）。
5. `packages/protocol`：Pydantic → TS 类型生成脚本（`pnpm gen:protocol`），先定义好枚举：Role、MessageType、BlockType、CommandName、ChatRuntimeStatus。
6. 基础设施：pre-commit（ruff、mypy、eslint、prettier）；GitHub Actions 跑 lint、test、build。

**验收**
- `make dev` 一条命令启动全部服务；`GET /api/v1/health` 返回 200；web 能连上 socket 并收到 ping。
- 修改 Pydantic 枚举后重新生成，前端类型同步更新。

---

## P1 账号与项目（4 天）

**后端**
- 表：users、workspaces、workspace_members、chats。
- `POST /v1/user/{register,login}`、`GET /v1/user/detail`；refresh 机制；Google OAuth（可放到 P7）。
- `GET/POST/PATCH/DELETE /v1/chats`；创建用户时自动建个人工作区；`X-Workspace-Id` 中间件。

**前端**
- `/login`、`/register`；路由守卫。
- `/dashboard`：左侧栏（工作区切换、导航、最近项目）、大输入框（Tiptap，`+` 附件按钮先占位，模式下拉，发送）。
- 提交后执行 `POST /chats {title(前 30 字), first_message}`，再跳转到 `/chat/[id]`。
- `/chat/[id]` 布局骨架：44px 头部；可拖动分栏（左 500px）；空的工作区占位。

**验收**：注册 → 登录 → 输入一句话 → 项目出现在"最近"里 → 进入工作台，刷新后状态保持。

---

## P2 单 Agent 纵向打通（2 周）★

### P2.1 沙箱（4 天）
- `images/sandbox/Dockerfile`：Node 20、pnpm、Python 3.12、uv、git、sandboxd；预装模板 `templates/react-shadcn-vite`（含 node_modules 缓存）。
- `services/sandboxd`（Go）：`/exec`（SSE 流式输出，超时，后台进程）、`/fs/*`、`/process/ensure|logs`、`/ports`、`/health`；token 鉴权。
- `oa/sandbox`：`SandboxProvider` 接口加 `DockerProvider`（docker SDK，挂载 `data/chats/{id}/workspace`），`sandboxes` 表，`ensure_running(chat_id)`。
- 首次创建时把模板拷进工作区并执行 `git init`。

### P2.2 Agent 运行时（5 天）
- `oa/llm`：OpenAI 兼容客户端连 LiteLLM，支持流式输出和 tool calls；先配 Claude Sonnet 和 DeepSeek。
- `oa/tools`：`editor_read / editor_write / editor_edit_file_by_replace / terminal_run`。每次工具调用都生成 `action_data` 和 `block_timeline`，快照写到 MinIO。
- `oa/agents`：`Role` 基类（ReAct 工具循环、max_steps、上下文截断）；`Engineer(Alex)` 的系统提示词（技术栈约束：React + Vite + shadcn + Tailwind，必须跑 `pnpm build` 自检）。
- `agent-worker`：arq 任务 `run_round(chat_id, message_id)`；Redis 锁保证单 chat 串行；事件通过 Redis pub/sub 发到 realtime。
- `POST /v1/chats/:id/messages`：保存用户消息 → 入队 → 返回。

### P2.3 前端对话流（3 天）
- `useChatSession`：`join_chat`；处理 `msg:create/update`、`action_data:update`、`timeline:*`；按 `seq` 断线补齐。
- 消息组件：用户气泡、Agent 消息组（头像、名字、时间）、`think` 折叠块、工具卡片（图标 + 命令 + 文件名 + 状态）、Markdown 渲染。
- 输入框发送、"停止"按钮（`POST /chats/:id/stop`，设置 cancel 标志）。

**验收（Demo 1）**：输入"做一个番茄钟页面"，Alex 流式思考，依次出现 editor_write、terminal_run(`pnpm build`) 卡片；构建通过后回复完成。刷新页面，历史完整还原。

---

## P3 工作区面板与预览（1.5 周）

- **网关 `apps/gateway`**（Go）：解析 `{port}-{chat}-preview.app.localhost`，反代到容器端口；HTML 注入 `bridge.js`；沙箱未运行时返回唤醒页并触发 wake。本地用 `*.app.localhost` 免证书。
- sandboxd 托管 dev server：`process.ensure("dev","pnpm dev --host 0.0.0.0")`；端口探测就绪后发 `chat:update {preview_url}`。
- `apps/bridge`：错误、console、路由变化通过 postMessage 上报；父页面"控制台"面板展示。
- **右侧工作区**：
  - AppViewer：iframe、工具栏（刷新、主页、路由下拉、新窗口打开、设备切换、控制台）；
  - Editor：Monaco，只读，显示 Agent 写入的 diff 高亮，"跟随"模式自动切换到正在写的文件；
  - Terminal：xterm 回放 `timeline:content`；
  - Files：文件树（`GET /files?path=`）、查看、下载 zip；
  - 头部工具按钮切换面板；点击消息里的工具卡片跳到对应 block（`chat:show-block`）。
- 回放：`GET /chats/:id/block-timelines`，加时间轴拖动（类似 Atoms 的 Browser-RT 回放）。
- 心跳 `POST /v1/runtime/heartbeat`；15 分钟空闲后自动 stop。

**验收（Demo 2）**：Agent 写代码时右侧编辑器实时出现内容，终端滚动输出，完成后 AppViewer 自动刷新出页面；在页面里制造一个报错，控制台能捕获。关掉浏览器 20 分钟后再打开，沙箱自动唤醒，预览恢复。

---

## P4 版本与发布（1 周）

- 每轮结束：`git commit` 加 tag `vN`；生成 summary（basic 模型）；`pnpm build` 后上传 dist 到 S3；Playwright 截封面；`version:*` 事件。
- `GET /chats/:id/versions`、`POST /versions/:v/restore`（git checkout 到新提交，发 `restore` 消息）；版本历史抽屉（头部 ⟲ 按钮）。
- 发布：`PUT /chats/:id/deployment {version, is_always_latest}`；网关支持 `{uid}-{chat}-{n}-{v|latest}.app.*` 读 S3 静态文件，SPA 回退。
- 发布弹窗：访问权限（public/secret/private）、默认域名、复制链接；分享预览 ticket（`/__preview_auth`）。

**验收（Demo 3）**：两轮修改产生 v1、v2，可回到 v1；点"发布"后得到可公开访问的 URL，后续修改不影响已发布版本，除非勾选 latest。

---

## P5 多智能体团队（2.5 周）★

### P5.1 编排器（1 周）
- `Plan` 模型和工具：`plan_append_task / plan_reset_task / plan_finish_current_task`（Task block，右侧显示计划清单）。
- `TeamLeader(Mike)`：分析需求、拆任务、`mention(role, instruction)`；被 @ 的角色回 `emoji ok` 回执；按计划顺序调度。
- `runs` 表：持久化 Run 状态，支持中断恢复。
- `agent_mode`：`lite` 只用 Engineer，`full` 用整个团队；dashboard 和项目设置里可切换。

### P5.2 角色（1 周）
- Emma（PM）：写 `docs/prd.md`（模板化 PRD：目标、用户故事、功能列表、UI 要点）。
- Bob（Architect）：写 `docs/design.md`（目录结构、数据模型、接口、文件清单）。
- Alex：按 design 实现，build 自检和自动修复循环（最多 3 轮），失败时发审批卡。
- David（Data Analyst）：Notebook 工具（sandboxd 里跑 jupyter kernel）加 Browser 工具（容器内 Playwright：goto/scroll/click/screenshot，截图存 S3，Browser 面板展示）。
- 联网搜索工具（服务端调用 Tavily）。

### P5.3 人机协同与会话控制（0.5 周）
- `ask_human` 生成 `task` 审批卡片（选项、danger）；点击后作为用户回复，Run 继续。
- `questions_card`（澄清问题表单，对应 `POST /submit-questions`）；计划模式（`plan_ready` 卡片，对应 approve/decline-plan）。
- prompt-queue：运行中继续输入会进入队列，支持排序、编辑、删除、暂停、立即发送。
- 编辑或重试历史消息：生成新的 attempt 分支（round_id/attempt），可在分支间切换（round-state）。

**验收（Demo 4）**：Full 模式输入"做一个带登录的待办应用"：Mike 列出 4 个任务 → @David 调研 → Emma 写 PRD → Bob 设计 → Alex 实现、构建、预览；中途 Alex 构建失败时弹出"是否继续修复"，点"继续"后完成。运行中发的消息排进队列，结束后自动执行。

---

## P6 集成与应用后端（2 周）

- Secrets：项目级和工作区级，dev/prod 两套，加密存储，注入沙箱 env；终端回放中打码。
- Supabase：OAuth、选择或创建项目、写入 `.env`；Agent 工具 `backend_create_tables`、`backend_get_schemas`、`exec_sql`。
- GitHub：OAuth App，创建仓库、push/pull、分支切换。
- `packages/app-sdk` 加 OpenAtoms Cloud：
  - 每个应用一个 Postgres schema（开发和生产各一）；
  - 网关路由 `/api/v1/{auth,entities,storage,aihub}`，按 `App-Host` 识别应用；
  - Backend 面板：表浏览器（tables/rows/schema/导入导出 CSV）。
- MCP 连接器（可选）：用户添加 MCP server，绑定为 Agent 工具。

**验收**：生成一个"留言板"应用，数据存在 Cloud（或 Supabase）里，发布后多人访问可读写；GitHub 仓库能看到代码。

---

## P7 商业化与协作（2 周）

- 积分：`credit_ledger`、`usage_events`；LiteLLM 回调计费；发送前预检，余额不足时发 `quota` 消息；余额显示和明细页。
- Stripe：订阅（Free/Pro/Max）、充值、webhook；定价页。
- 工作区：成员邀请（邮件 token）、角色、RBAC 批量校验。
- 分享回放 `/share/[id]/[v]`（只读，按时间线重放，secret 过滤）；Remix（fork 版本到自己的工作区）。
- 发现页：公开项目列表、点赞、浏览量；文件夹、收藏、重命名、删除。
- Google 登录、邮件验证、忘记密码。

---

## P8 生产化（2 周）

- `K8sProvider`：每个 chat 一个 Pod，JuiceFS PVC，gVisor RuntimeClass，NetworkPolicy，资源配额；预热池（空闲 Pod 秒级认领）。
- 网关上 K8s：通配证书，自定义域名（on-demand TLS、DNS 校验）。
- 全栈发布：BuildKit 构建镜像 → Knative 服务，支持缩容到 0。
- 可观测性：OTel、Sentry、Prometheus 指标（冷启动 P95、run 成功率、每轮成本）、告警。
- 安全：限流、滥用检测（挖矿进程、外发扫描）、内容审核、备份与恢复演练。
- 压测：100 个并发 run、500 个运行中沙箱。

---

## P9+ 进阶特性（按价值排序）

1. **BoN / 竞赛模式**：K 个模型并行，用户挑选或评审模型挑选后合并。
2. **可视化编辑**：bridge 选区 → 属性面板 → `edit-session` 直接改源码（Tailwind class/文本），或作为 Agent 指令附件。
3. **Deep Research（Iris）**、SEO（Sarah）、Ads（Adrian）角色。
4. 定时自动化任务（automations）。
5. 多模态工具：图片、视频、音频生成，幻灯片，文档解析。
6. 移动端应用（Expo）模板与预览。

---

## 风险与对策

| 风险 | 对策 |
|---|---|
| Agent 质量不稳定（构建失败、跑偏） | 强约束模板加 build/lint 自检循环；把提示词和工具 schema 做成可回归的评测集（`evals/`，20 个典型需求，每晚跑） |
| LLM 成本高 | prompt caching、上下文截断与摘要、便宜模型做规划/摘要、按轮预算上限 |
| 沙箱冷启动慢 | 镜像预拉、模板 node_modules 走 pnpm store 缓存卷、预热池 |
| 沙箱安全 | gVisor、非 root、出网策略、配额、secret 打码 |
| 实时消息丢失或乱序 | `seq` 加 Redis Stream 补齐，客户端幂等合并 |
| 范围蔓延 | 严格按里程碑推进，P5 之前不做商业化和进阶功能 |

---

## 下一步

从 **P0** 开始：搭 monorepo 骨架、docker-compose、FastAPI + socketio、Next.js 设计 token、protocol 类型生成。
