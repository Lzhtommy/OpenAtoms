# OpenAtoms 技术架构设计

> 目标：复刻 atoms.dev（多智能体全栈应用搭建平台）。
> 依据：`re/REPORT.md`（前端/协议逆向）、`re/SERVER.md`（服务端逆向）。
> 标记说明：**〔对齐〕** 表示与 Atoms 观测一致，照搬；**〔补齐〕** 表示 Atoms 不可见，按业界经验设计；**〔取舍〕** 表示有意与 Atoms 不同。

---

## 1. 设计原则

1. **协议先行**：数据模型、REST 和 Socket 事件尽量沿用 Atoms 的命名（`msg:create`、`timeline:content`、`block_type`、`command_name`……）。前端交互已有现成参照，可以少走弯路。
2. **存算分离**：项目工作区放在持久卷上，沙箱容器可以随时销毁或重建〔对齐〕。
3. **事件溯源式渲染**：Agent 的每一步都落成"消息 + action_data + block_timeline"三类记录，实时推送和历史回放共用一套渲染逻辑〔对齐〕。
4. **可插拔**：LLM 供应商、沙箱提供方、对象存储、部署目标都抽象成接口。本地开发全部可以用 docker-compose 跑起来。
5. **单 chat 串行**：同一个项目同一时刻只运行一轮 Agent，其余输入进入 prompt-queue〔对齐〕。

---

## 2. 总体架构

```
                                   ┌──────────────── 用户浏览器 ────────────────┐
                                   │  Web App (Next.js)    预览 iframe            │
                                   └──────┬───────────────────┬─────────────────┘
                                          │HTTPS/WSS          │HTTPS
                     ┌────────────────────▼──────────┐   ┌────▼──────────────────────────┐
                     │  Edge / Ingress (Caddy/Traefik)│   │  Preview & App Gateway (Go)    │
                     │  oa.dev, /api, /socket.io      │   │  *-preview.app.oa.dev  → 沙箱   │
                     └───┬─────────────┬──────────────┘   │  *.app.oa.dev          → 静态/容器│
                         │             │                  │  注入 bridge.js、ticket 鉴权     │
             ┌───────────▼───┐   ┌─────▼─────────────┐    └────▲───────────────▲──────────┘
             │ api (FastAPI) │   │ realtime          │         │               │
             │ REST /api/v1  │   │ (python-socketio) │         │               │
             └──┬──────┬─────┘   └─────▲─────────────┘         │               │
                │      │ enqueue       │ Redis pub/sub         │               │
                │   ┌──▼───────────────┴──────────────┐        │               │
                │   │ agent-worker (Python, asyncio)   │        │               │
                │   │  Orchestrator → Roles → Tools    │──LLM──▶ llm-gateway (LiteLLM)
                │   │  事件流 → DB + Redis             │        │               │
                │   └──┬────────────────────────────────┘        │               │
                │      │ sandbox API (HTTP/WS)                   │               │
                │   ┌──▼───────────────────────────┐             │               │
                │   │ sandbox-manager              │  创建/唤醒/休眠/回收            │
                │   │  provider: docker | k8s | e2b│─────────────┘               │
                │   └──┬───────────────────────────┘                             │
                │      ▼                                                         │
                │   ┌──────────────── Sandbox 容器（每个 chat 一个）──────────┐    │
                │   │ sandboxd (Go/Py 守护进程) :49999                         │    │
                │   │  exec/pty · fs · jupyter kernel · 进程管理 · 端口探测     │    │
                │   │ dev server :5173 / :3000      Chromium(Playwright)      │◀───┘
                │   │ /workspace  ← 持久卷 /data/chats/{chat_id}/workspace     │
                │   └──────────────────────────────────────────────────────────┘
                │
   ┌────────────▼───────────┬──────────────┬──────────────────┬──────────────────┐
   │ PostgreSQL (主库)        │ Redis         │ S3/MinIO          │ 共享卷 (NFS/JuiceFS)│
   │ 用户/项目/消息/版本/计费   │ 队列/锁/pubsub │ 快照/产物/上传/发布  │ 项目工作区            │
   └────────────────────────┴──────────────┴──────────────────┴──────────────────┘
```

### 2.1 服务清单

| 服务 | 语言/框架 | 职责 | 来源 |
|---|---|---|---|
| `web` | Next.js 15 (App Router) + React 19 + Tailwind + shadcn/ui | 营销页、dashboard、chat 工作台、分享回放 | 〔取舍〕Atoms 用 Nuxt |
| `api` | Python 3.12 + FastAPI + SQLAlchemy 2 + Alembic | REST、鉴权、项目/消息/版本/文件/部署/计费 | 〔对齐〕Python |
| `realtime` | python-socketio (ASGI) + Redis manager | WebSocket 房间、事件扇出 | 〔对齐〕 |
| `agent-worker` | Python asyncio + arq (Redis 队列) | 跑多智能体回合、工具调用、流式事件 | 〔补齐〕 |
| `llm-gateway` | LiteLLM Proxy | 多供应商路由、密钥、限流、计量、fallback | 〔补齐〕 |
| `sandbox-manager` | Python（与 api 同仓） | 沙箱生命周期、调度、配额 | 〔补齐〕 |
| `sandboxd` | Go（单二进制，打进镜像） | 容器内 RPC：命令执行、PTY、文件、kernel、端口 | 〔补齐〕 |
| `gateway` | Go (net/http + httputil.ReverseProxy) | 预览/发布域名路由、ticket 鉴权、HTML 注入 | 〔对齐〕mgx-nginx |
| `bridge.js` | TS（独立打包） | 注入到用户应用：错误上报、控制台转发、可视化选区、角标 | 〔对齐〕appPlugins.js |

开发期把 `api + realtime + sandbox-manager` 合成一个进程跑（同一个 ASGI app 挂载 socketio），生产期再拆开部署。

---

## 3. 仓库结构（monorepo）

```
OpenAtoms/
├─ apps/
│  ├─ web/                    Next.js 前端
│  ├─ bridge/                 注入脚本（Vite lib 构建）
│  └─ gateway/                Go 预览/发布网关
├─ services/
│  ├─ server/                 Python：api + realtime + sandbox-manager + agent-worker
│  │  ├─ oa/api/              FastAPI 路由
│  │  ├─ oa/realtime/         socketio 服务
│  │  ├─ oa/agents/           编排器、角色、提示词
│  │  ├─ oa/tools/            工具实现（调用 sandboxd）
│  │  ├─ oa/sandbox/          provider 抽象 + docker/k8s/e2b 实现
│  │  ├─ oa/llm/              LLM 客户端（经 llm-gateway）
│  │  ├─ oa/domain/           领域模型、服务层
│  │  ├─ oa/db/               SQLAlchemy 模型 + Alembic 迁移
│  │  └─ oa/worker.py         arq worker 入口
│  └─ sandboxd/               Go 守护进程
├─ images/
│  └─ sandbox/Dockerfile      沙箱基础镜像（Node20/pnpm/bun/Python3.12/uv/Chromium/sandboxd）
├─ templates/                 项目模板（react-shadcn-vite、nextjs、fullstack-fastapi…）
├─ packages/
│  ├─ protocol/               共享类型：事件、枚举、DTO（由 Pydantic 生成 TS）
│  └─ app-sdk/                生成应用使用的 SDK（auth/entities/storage/ai）
├─ deploy/
│  ├─ docker-compose.yml      本地全栈
│  └─ k8s/                    Helm charts
├─ docs/                      本设计、计划
└─ re/                        逆向资料
```

`packages/protocol` 是唯一的类型真源：后端在 Pydantic 里定义模型，用 `pydantic2ts`（或导出 JSON Schema 再生成）输出 TS 类型，前端直接 import。

---

## 4. 领域模型（PostgreSQL）

```sql
users(id, email, name, avatar, password_hash, google_sub, plan, created_at)
workspaces(id, name, owner_id, plan, created_at)
workspace_members(workspace_id, user_id, role)           -- owner/admin/member/viewer
chats(                                                    -- = 项目
  id uuid(hex32), workspace_id, user_id, title, status,   -- ready|forking|failed
  access_mode,                                            -- private|secret|public
  config jsonb,                                           -- agent_mode, framework, template, integrations…
  llm jsonb,                                              -- default_model
  runtime_status,                                         -- running|stopping|sleeping|stopped|activating|failed
  current_version, fork_from, folder_id, is_starred, created_at, updated_at)
messages(
  id bigserial, chat_id, role,                            -- User|Team Leader|Product Manager|Architect|Engineer|Data Analyst…
  type,                                                   -- message|think|task|emoji|questions_card|plan_ready|error|…
  content jsonb,                                          -- Quill Delta / 卡片 JSON
  refer_id, uuid, version,
  round_id, attempt, prev_round_id, prev_attempt,         -- 分支树
  is_finished, metadata jsonb, user_id, created_at, updated_at)
action_datas(
  id, uuid, chat_id, message_uuid, command_name, block_type,
  file, src_path, extra jsonb, status,                    -- running|success|failed
  created_at, updated_at)
block_timelines(
  id, chat_id, index int, block_type, block_owner, command_name,
  file, src_path, content_ref, extra_data jsonb, version, created_at)
  UNIQUE(chat_id, index)
versions(chat_id, version 'v1', summary, cover, link, snapshot_ref, metadata jsonb, created_at)
round_state(chat_id, active_branches jsonb, active_attempts jsonb)
prompt_queue(id, chat_id, position, content jsonb, metadata jsonb, status, created_at)
deployments(chat_id, version, kind,                       -- static|container
            domain, status, url, is_always_latest, created_at)
domains(id, workspace_id, chat_id, hostname, verify_status, ssl_status)
secrets(id, scope, chat_id, workspace_id, key, value_enc, env)   -- dev|prod
integrations(id, workspace_id, provider, account jsonb, tokens_enc)
credit_ledger(id, workspace_id, user_id, chat_id, delta, reason, ref, created_at)
usage_events(id, chat_id, message_id, model, input_tokens, output_tokens, cost_usd, created_at)
sandboxes(chat_id, provider, external_id, node, status, last_heartbeat, ports jsonb)
```

**分支模型**〔对齐〕：一次用户输入加上该轮所有 Agent 输出属于同一个 `round_id`。编辑或重试会生成同一 `prev_round_id` 下的新 `attempt`。`round_state.active_attempts` 记录用户当前查看的分支，查询消息时按活跃路径过滤。

**存储分层**：
- 工作区文件存在共享卷 `/data/chats/{id}/workspace`。
- block 快照（终端输出、编辑器 diff、浏览器截图）存在 S3 `chats/{id}/.storage/{index}/{hash}/…`，`block_timelines.file` 存相对路径。
- 版本快照：`git commit` 加 tag（工作区本身是一个 git 仓库），同时把 tar.zst 存到 S3，方便冷恢复。〔补齐：Atoms 不可见，用 git 管版本更省事，diff 和回滚也是现成的〕

---

## 5. 实时协议

### 5.1 通道
- Socket.IO v4，路径 `/socket.io`，`transports:['websocket']`，`auth/query.token=JWT`〔对齐〕。
- 房间名为 `chat:{chat_id}`、`user:{user_id}`、`workspace:{id}`。
- 多实例之间用 `socketio.AsyncRedisManager` 互通。agent-worker 不直接连 socket，只往 Redis 发布事件，由 realtime 服务转发。

### 5.2 事件（沿用 Atoms 命名）
| 方向 | 事件 | 载荷要点 |
|---|---|---|
| C→S | `join_chat` / `leave_chat` | `{chat_id}`，回 `join_chat_ack {chat_id, ok, code}` |
| S→C | `msg:create` | 完整消息对象 |
| S→C | `msg:update` | `{id, chat_id, content: Delta增量, is_finished}` 或 `{id, metadata_only, metadata}` |
| S→C | `action_data:update` | `{chat_id, action_data}` |
| S→C | `timeline:start / content / complete` | `{chat_id, index, block_type, block_owner, command_name, content, content_index}` |
| S→C | `version:create / update / complete` | `{chat_id, version, link, summary}` |
| S→C | `build:update / error / complete`、`deploy:update` | 构建与部署进度 |
| S→C | `chat:update` | 项目字段变化（runtime_status、title…） |
| S→C | `prompt_queue:update / item_sent` | 队列 |
| S→C | `chat_error:create / delete`、`quota:*`、`disk:*` | 告警 |

### 5.3 可靠性〔补齐〕
- 每个事件带 `seq`（按 chat 单调递增，Redis `INCR`）。客户端记住 `last_seq`，重连后调用 `GET /chats/:id/events?after=seq` 补齐，事件在 Redis Stream 里保留 24 小时；如果补不齐，就重新拉取 messages 和 timelines。
- `msg:update` 的增量带 `content_index`，保证幂等。
- 服务端每 N 个 token 合并一次再发（约 50ms 节流），避免消息风暴。

---

## 6. 多智能体运行时（核心）〔补齐，参考 MetaGPT 加 Atoms 观测〕

### 6.1 概念
```
Run (一个 round 的一次 attempt)
 └─ Orchestrator（由 Team Leader 驱动）
     ├─ Plan: Task[] (id, title, assignee, status, deps)    ← plan_append_task / plan_finish_current_task
     ├─ Roles: { TeamLeader(Mike), ProductManager(Emma), Architect(Bob), Engineer(Alex), DataAnalyst(David), ... }
     └─ Mailbox: 角色间消息（@mention = 派发任务）
```

### 6.2 角色定义（声明式）
```yaml
# oa/agents/roles/engineer.yaml
name: Alex
role: Engineer
model: ${chat.default_model}          # 可按角色覆盖
system_prompt: prompts/engineer.md
tools: [editor.*, terminal.*, appviewer.check, plan.finish_current_task, ask_human]
max_steps: 60
```
| 角色 | 工具集 | 产出 |
|---|---|---|
| Team Leader | plan.*、mention(role, instruction)、ask_human、reply_user | 计划、派发、汇报 |
| Product Manager | editor.write、web_search | `docs/prd.md` |
| Architect | editor.*、terminal.run（只读命令） | `docs/design.md`、目录结构 |
| Engineer | editor.*、terminal.*、appviewer.check、checkui、deploy | 代码、可运行应用 |
| Data Analyst | notebook.exec、browser.*、editor.* | 调研报告、数据分析 |

### 6.3 回合循环
```
on user message (round r, attempt a):
  lock chat (Redis SETNX, TTL+续期)；若已有运行 → 进 prompt_queue
  ensure sandbox running (activating → running)
  if agent_mode == lite:  roles = [Engineer]，跳过 TL
  TL.step():
     think(stream) → tool_calls[plan.append_task × n, mention(DA, "...")]
  loop while plan 有未完成任务 and not cancelled:
     role = 当前任务 assignee
     role.run_task(task):              # ReAct / tool-calling 循环
        for step in range(max_steps):
          resp = llm.stream(messages, tools)       → msg:create(think) + msg:update(delta)
          for call in resp.tool_calls:
             ad = action_data(start)                → action_data:update / timeline:start
             result = tools[call.name](args)       → timeline:content(流式输出)
             ad.finish(result)                      → timeline:complete
          if call == plan.finish_current_task: break
          if call == ask_human: 发 task/questions_card 消息，挂起 Run，等用户回复后恢复
     TL 汇总 → reply_user(message)
  snapshot → version:create … version:complete（git commit + 构建 + 预览链接）
  release lock，pop prompt_queue
```

### 6.4 关键机制
- **上下文管理**：每个角色有自己的消息历史。长输出（终端、文件内容）在上下文里只保留截断摘要，并附上 `action_data.uuid` 以便回读。超过阈值时由便宜模型做摘要压缩。项目级记忆写入 `/workspace/.atoms/memory.md` 和 `.wiki.md`〔对齐〕。
- **人机协同**〔对齐〕：
  - `ask_human` 生成 `type=task` 卡片（选项加 danger 标记）；
  - `questions_card` 用于需求澄清；
  - `plan_ready` 进入计划模式，用户 approve 或 decline 后继续。
- **中断与恢复**：Run 状态（plan、各角色游标、挂起点）序列化存进 `runs` 表。用户点"停止"时设置 cancel 标志，worker 在下一个 await 点退出。worker 崩溃后可以从最后一个 action 恢复。
- **自检与修复**：Engineer 写完代码自动跑 `pnpm build` 和 lint，并通过 `appviewer.check` 抓取预览页的控制台错误（由 bridge.js 上报），失败最多自动修复 N 轮〔对齐：checkui_run / Build failing 卡片〕。
- **BoN / Boost**（后期）：同一个 Run 用 K 个模型在 K 个沙箱分支（git worktree）里并行跑，用户选一个，或由评审模型挑选后合并回主分支。

### 6.5 工具层（`oa/tools`）
每个工具声明 `name / block_type / json_schema / handler`，handler 通过 `SandboxClient` 调用 sandboxd。命令名沿用 Atoms 的 `command_name` 枚举（`editor_write`、`terminal_run`、`browser_goto`、`plan_append_task`……），前端渲染可以直接对上。

| 工具 | block_type | sandboxd 调用 |
|---|---|---|
| editor.read / write / edit_by_replace / append | Editor | `fs.read/write`，并保存 diff 快照 |
| terminal.run | Terminal | `exec.stream`（超时、后台模式、输出截断） |
| notebook.exec | Notebook | `kernel.execute`（Jupyter kernel gateway） |
| browser.goto / click / scroll / type / screenshot | Browser | `browser.*`（容器内 Playwright，返回截图和可访问性树） |
| appviewer.check | Browser-RT | 预览页 headless 加载，收集 bridge 上报的错误 |
| plan.* | Task | 纯服务端 |
| search.web | — | 服务端（Tavily/Bing API） |
| backend.* / deploy.* | Backend | 服务端 |

---

## 7. LLM 层〔补齐〕

- 统一走 **LiteLLM Proxy**（OpenAI 兼容接口），后面挂 Anthropic、OpenAI、DeepSeek、Gemini、Moonshot 等。
- 模型档位〔对齐 configs/general〕：`basic_model`（摘要、标题生成）、`default_model`（Engineer）、`adv_models`（用户可选）、`auto`（按任务路由）。
- 开启 prompt caching（Anthropic cache_control）：系统提示加工具定义加项目上下文作为缓存前缀。
- 计量：LiteLLM 回调把 token 写入 `usage_events`，按价目表折算成积分扣进 `credit_ledger`。每轮开始前预检余额，余额不足时发 `quota` 消息〔对齐〕。

---

## 8. 沙箱〔补齐，对齐 Atoms "自建容器 + 持久卷"〕

### 8.1 Provider 抽象
```python
class SandboxProvider(Protocol):
    async def create(self, chat_id, image, volume) -> SandboxHandle
    async def start(self, handle) / stop(handle) / destroy(handle)
    async def status(self, handle) -> Status
    async def endpoint(self, handle, port) -> str        # 供 gateway 反代
```
- `DockerProvider`：本地开发和单机部署使用，通过 docker SDK，挂载 `./data/chats/{id}/workspace:/workspace`。
- `K8sProvider`：生产使用，每个 chat 一个 Pod，PVC 或 JuiceFS 挂到 `/workspace`，RuntimeClass 用 gVisor 隔离，NetworkPolicy 限制出网（默认允许公网 HTTP，禁止访问内网网段）。
- `E2BProvider`：可选，用于快速起步或弹性兜底（此时文件读写要走 E2B FS API，存算分离会打折扣）。

### 8.2 生命周期
```
stopped ──wake──▶ activating ──ready──▶ running ──idle 15min──▶ stopping ──▶ stopped
                                         ▲  heartbeat（前端每 60s，agent 运行中持续）
```
- 唤醒的触发点：打开项目、发送消息、访问预览。冷启动目标在 5 秒以内（镜像预拉、节点上预热池）。
- 配额：同一工作区同时运行的沙箱数量有上限，超出返回 `4122 sandboxFull`〔对齐〕。
- dev server 由 sandboxd 托管（`processes.ensure("dev", "pnpm dev --host 0.0.0.0 --port 5173")`），崩溃自动拉起，日志通过 SSE 提供〔对齐 log-files/stream〕。

### 8.3 sandboxd API（容器内 :49999，只在集群内可访问）
```
POST /exec            {cmd, cwd, env, timeout, background}  → SSE stdout/stderr/exit
WS   /pty             交互式终端（用户在终端面板手动操作）
GET  /fs/list|read    POST /fs/write|mkdir|rm|mv            （大文件分块）
POST /kernel/execute  {code} → SSE jupyter 消息
POST /browser/{action}                                     （Playwright，后期）
GET  /ports           监听端口列表
POST /process/ensure|stop|logs
GET  /health
```
鉴权：sandbox-manager 在创建时注入一次性 token，只有内网可以访问。

### 8.4 基础镜像 `images/sandbox`
Debian bookworm-slim，加上 Node 20、pnpm、bun、Python 3.12、uv（把 pip 包装成 uv pip〔对齐〕）、git、Chromium 与 Playwright、jupyter kernel gateway、sandboxd。以非 root 用户 `user` 运行，模板的 node_modules 走 pnpm store 缓存卷。

---

## 9. 预览与发布网关（`apps/gateway`，对齐 mgx-nginx）

### 9.1 域名规则
| 用途 | 域名 | 后端 |
|---|---|---|
| 开发预览 | `{port}-{chat_id}-preview.app.oa.dev` | 沙箱 Pod `:port` |
| 版本发布 | `{uid}-{chat_id}-{n}-{v3\|latest}.app.oa.dev` | S3 静态产物 / 容器服务 |
| 自定义域名 | `www.customer.com` | 查 `domains` 表 → 同上 |

### 9.2 功能
- 通配证书（`*.app.oa.dev`，ACME DNS-01）。自定义域名走 on-demand TLS（Caddy 或 certmagic）。
- **预览鉴权**〔对齐〕：私有项目的预览先由 web 端调用 `POST /chats/:id/preview-ticket` 获取 ticket，再跳转到 `/__preview_auth?ticket=&redirect=`，网关校验后在该子域写入 cookie。
- **HTML 注入**〔对齐〕：响应类型为 `text/html` 时，在 `</body>` 前插入 `<script src="https://cdn.oa.dev/bridge-{ver}.js">`，同时处理 gzip/br。
- 沙箱休眠时：返回"正在唤醒"页（503），同时异步触发 wake；页面轮询，就绪后自动刷新。
- 静态发布：从 S3 `deploys/{chat}/{version}/` 读取，SPA 路由回退到 `index.html`，`Cache-Control` 分级设置。
- 统一加 `CSP frame-ancestors` 白名单（只允许 oa.dev 嵌入）。

### 9.3 bridge.js（注入到用户应用）
- 捕获 `window.onerror`、`unhandledrejection`、console，通过 `postMessage` 发给父窗口（`mgx-appview-error/console`）〔对齐〕。
- 路由变化、可视化编辑选区（hover 高亮，点击回传选择器、源码位置和文本）〔对齐 appviewer:sync-design-selection〕。
- "Made with OpenAtoms" 角标（付费可关闭）。
- 与父窗口的 RPC：`{protocolVersion, sessionId, kind: request|response|event}`。

---

## 10. 构建、版本与部署

```
version:create
 → git add -A && git commit -m "{summary}" && git tag v{n}           （在沙箱内）
 → pnpm build（模板约定 build 命令与 dist 目录）
 → 上传 dist → S3 deploys/{chat}/{v}/
 → 截图生成封面（Playwright）
 → version:complete {link}
```
- **静态发布**（P1 优先）：`PUT /chats/:id/deployment {version, domain, is_always_latest}` 只是把域名指向某个版本的产物，属于零构建切换。
- **全栈发布**〔补齐，替代 Atoms 的 ECR+Lambda〕：模板里带 `Dockerfile`，用 BuildKit（rootless 的 buildkitd）构建镜像，推送到镜像仓库，再部署成 Knative Service 或 K8s Deployment，按请求缩容到 0。不引入 Jenkins，直接用 worker 加 BuildKit。
- 构建状态枚举沿用 `QUEUED / BUILDING / SUCCESS / FAILURE / CANCELLED`。

---

## 11. 应用后端（BaaS，"OpenAtoms Cloud"）〔补齐〕

两条路线并存：
1. **Supabase 集成**（先做）：OAuth 连接用户的 Supabase 组织，在其中建项目，把 URL 和 anon key 注入 `.env`，Agent 用 SQL 工具建表〔对齐〕。
2. **自带 Cloud**（后做）：给每个应用分配一个 Postgres schema（或 Neon 分支），开发和生产各一套〔对齐 db_mode:double〕。提供兼容 `packages/app-sdk` 的网关接口：
   ```
   /api/v1/auth/{me,logout,token/exchange}       平台账号 SSO 加应用自有用户
   /api/v1/entities/{Entity}[/all|/:id|/batch]   通用 CRUD（JSON query → SQL，带 RLS 列 owner_id）
   /api/v1/storage/*                             S3 预签名
   /api/v1/aihub/{gentxt(SSE),genimg,…}          经 llm-gateway，按应用计费
   ```
   通过 `App-Host` 请求头识别是哪个应用〔对齐〕。

---

## 12. 前端架构（`apps/web`）

### 12.1 技术选型
- Next.js App Router：营销页和 SEO 页用 SSR/SSG，`/chat/[id]` 用客户端组件。
- 状态：**Zustand**（UI 和会话状态，按 Atoms 的 Pinia store 拆分）加 **TanStack Query**（服务端数据）。
- 组件：shadcn/ui + Radix，样式 Tailwind 加 CSS 变量 token（照搬 `re/design-tokens.json` 的语义层：`--color-bg-base-default` 等）。
- 编辑器 Monaco；终端渲染 xterm.js（只读回放加交互 PTY）；Markdown 用 react-markdown + shiki + katex + mermaid；输入框用 Tiptap（输出 Delta 兼容结构：text、mention、附件）。
- 实时：`socket.io-client` 加一个 `useChatSession(chatId)` hook，把消息、timeline、版本归并成规范化 store。
- 国际化：next-intl，先做中文和英文。

### 12.2 关键页面
| 路由 | 内容 |
|---|---|
| `/` | 落地页；登录后跳到 `/dashboard` |
| `/dashboard` | 大输入框（模式：构建/计划/研究，模型选择，附件），模板与社区 |
| `/chat/[id]` | 左边对话（500px，可拖动），右边工作区：AppViewer / Editor / Terminal / Files / Backend / Overview |
| `/share/[id]/[v]` | 只读回放（dataSource=replay，按时间线逐条重放） |
| `/my-projects`、`/settings/*`、`/pricing`、`/login`、`/register` | — |

### 12.3 消息渲染映射
`message.type × role` 决定渲染组件：
- `think` 渲染为可折叠思考块，内嵌 `action_data` 工具卡片，点击后右侧切到对应 block；
- `task` 渲染为审批卡片；`questions_card` 渲染为表单；`emoji` 渲染为回执；
- `plan_ready` 渲染为计划草案；`error` / `quota` 渲染为告警。

### 12.4 设计 token（摘自逆向）
- 字体：IBM Plex Sans（正文，14px 基准）、IBM Plex Serif（大标题）、IBM Plex Mono（代码）。
- 颜色：页面底色 `#ebebea`，卡片 `#fff`，品牌色 `#4267ff`；文本用 alpha-black 分级。
- 圆角：4 / 6 / 8 / 12 / 16 / full。
- 角色色：Mike 橙、Emma 粉、Bob 蓝、Alex 天蓝、David 绿。

---

## 13. 鉴权与安全

- JWT：access token 15 分钟加 refresh token 30 天（httpOnly cookie）。WebSocket 用短期 token〔补齐：Atoms 用 30 天长 token 放请求头，安全性较弱〕。
- 登录方式：邮箱密码（argon2）加 Google OAuth，邮件走 Resend 或 SES。
- RBAC：工作区角色加项目 `access_mode`。所有 `/chats/:id/*` 统一经 `ChatAccessGuard` 校验。
- 密钥管理：用户 secret 用 KMS 或 libsodium 信封加密，注入沙箱 env，**终端输出和日志里做打码处理**（逆向时发现 Atoms 的回放里能看到明文 `.env`，这里要避免同样的问题）。
- 沙箱隔离：gVisor，非 root，只读根文件系统加可写卷，出网 egress 策略，CPU/内存/磁盘配额，fork bomb 防护（pids limit）。
- 公开分享时自动过滤包含 secret 的 block。

---

## 14. 计费（后期）

积分账本采用复式记录：`credit_ledger`（订阅月度额度、赠送、购买、消耗）。消耗来源包括 LLM token、沙箱运行分钟数、存储和自定义域名。支付接 Stripe Checkout 和 Billing Portal，webhook 写入账本。

---

## 15. 可观测性与运维

- OpenTelemetry trace 贯穿 api → worker → sandboxd → llm-gateway，以 `X-Request-ID` 和 `run_id` 关联。
- Sentry（前后端）、Prometheus + Grafana（沙箱数量、冷启动耗时、run 成功率、token 成本）、Loki（日志）。
- 产品分析：PostHog（自托管可选）。
- 部署：本地用 docker-compose；生产用 K8s + Helm + ArgoCD。数据库用托管 Postgres，Redis 用托管服务，对象存储用 S3 或 R2，共享卷用 JuiceFS（底层 S3 加 Redis 元数据）。

---

## 16. 与 Atoms 的差异汇总

| 项 | Atoms | OpenAtoms | 理由 |
|---|---|---|---|
| 前端框架 | Nuxt 3 / Vue | Next.js / React | 生态与生成应用栈统一 |
| 计费服务 | 独立 gRPC 服务 | api 内模块 | 早期简化 |
| 发布 | Jenkins → ECR → Lambda | worker + BuildKit → Knative/静态 S3 | 自托管、少依赖 |
| Token | 30 天 JWT 放请求头 | 短 access + refresh cookie | 安全 |
| 版本 | 未知 | git commit/tag + S3 tar | diff/回滚简单 |
| 回放隐私 | 明文 .env 可见 | secret 打码 | 安全 |
