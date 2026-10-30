# OpenAtoms 实现契约（binding）

> 本文是 ARCHITECTURE.md 的落地细化，**所有组件必须严格遵守此处的名字、端口、路径与数据形状**。
> 若实现中需要偏离，必须同步修改本文。

## 0. 运行模式与端口

| 组件 | 目录 | 端口(dev) | 说明 |
|---|---|---|---|
| web (Next.js 15, React 19, Tailwind v4) | `apps/web` | 3000 | 通过 Next rewrites 把 `/api/*` 与 `/socket.io/*` 代理到 api |
| api (FastAPI + python-socketio 同进程) | `services/server` | 8100 | `/api/v1/*`、`/socket.io`、`/internal/*` |
| worker (arq) | `services/server` (`oa.worker`) | — | 仅 `OA_QUEUE=arq` 时使用；默认 `inproc` 在 api 进程内跑 Run |
| gateway (Go) | `apps/gateway` | 8080 | `*.app.localhost:8080` 预览 / 发布 / 应用 API |
| sandboxd (Go) | `services/sandboxd` | 每沙箱一个（容器内 49999；local 模式随机端口） | 容器内 RPC |
| bridge.js | `apps/bridge` | — | gateway 内嵌并在 `/__oa/bridge.js` 提供 |

两种基础设施模式（环境变量 `OA_MODE`）：
- `lite`（默认，零依赖）：SQLite(`data/oa.db`)、内存事件总线 + 内存事件缓冲、本地文件存储(`data/storage`)、`OA_SANDBOX_PROVIDER=local`（在宿主机上直接起 sandboxd 子进程，工作区 `data/chats/{chat_id}/workspace`）。
- `full`：Postgres、Redis（socketio AsyncRedisManager、事件 Stream、锁、arq）、MinIO(S3)、`OA_SANDBOX_PROVIDER=docker`。

关键环境变量（api）：
```
OA_MODE=lite|full
OA_DATABASE_URL=sqlite+aiosqlite:///./data/oa.db | postgresql+asyncpg://oa:oa@localhost:5432/oa
OA_REDIS_URL=redis://localhost:6379/0
OA_STORAGE=local|s3   OA_STORAGE_DIR=./data/storage   OA_S3_ENDPOINT/OA_S3_BUCKET/OA_S3_KEY/OA_S3_SECRET
OA_DATA_DIR=./data    (chats/{id}/workspace 在其下)
OA_SANDBOX_PROVIDER=local|docker   OA_SANDBOX_IMAGE=openatoms/sandbox:latest   OA_SANDBOXD_BIN=services/sandboxd/bin/sandboxd
OA_QUEUE=inproc|arq
OA_JWT_SECRET  OA_INTERNAL_TOKEN (gateway↔api 共享)
OA_LLM_BASE_URL (OpenAI 兼容，例如 LiteLLM http://localhost:4000/v1)  OA_LLM_API_KEY
OA_DEFAULT_MODEL=oa-mock   OA_BASIC_MODEL=oa-mock   OA_ADV_MODELS=逗号分隔
OA_PUBLIC_WEB_URL=http://localhost:3000  OA_APP_DOMAIN=app.localhost:8080  OA_APP_SCHEME=http
OA_TAVILY_API_KEY  OA_STRIPE_SECRET/OA_STRIPE_WEBHOOK_SECRET  OA_GITHUB_CLIENT_ID/SECRET  OA_SUPABASE_CLIENT_ID/SECRET  OA_GOOGLE_CLIENT_ID/SECRET
OA_SECRET_KEY (secrets 加密用 Fernet key 派生)
```
模型名 `oa-mock` 是内置的**确定性脚本模型**（无需 API key），能按角色产出真实工具调用并生成可运行应用，用于演示与 e2e 测试；其它模型名一律走 `OA_LLM_BASE_URL`。

## 1. REST 约定

- 前缀 `/api/v1`。响应统一 `{"code": int, "message": str, "data": any, "now_ts": float}`；`code=0` 成功。
- 错误码：`4400` 参数错误(HTTP 400/422)、`4401` 未认证(401)、`4403` 无权限(403)、`4404` 不存在(404)、`4409` 冲突(409)、`4122` sandboxFull(429)、`4290` 额度不足(402)、`5000` 内部错误(500)。
- 请求头：`Authorization: <jwt>` 或 `Authorization: Bearer <jwt>` 均可；`X-Workspace-Id`；`X-Request-ID`（回显到响应 `X-Request-ID`）。
- access token 15 分钟（JWT HS256，payload `{user_id,email,iat,exp,typ:"access"}`），refresh token 30 天，存 httpOnly cookie `oa_refresh`（path `/api/v1/user`），`POST /user/refresh` 换新 access。
- 分页：`?cur_page=1&page_num=20` → `data: {items: [...], total: int, has_more: bool}`。
- 时间一律 ISO8601 UTC 字符串。

### 1.1 用户 / 工作区
```
POST /user/register   {email, password, name?}          → {access_token, user}   (同时 set-cookie oa_refresh；自动建个人工作区；赠送初始积分)
POST /user/login      {email, password}                 → {access_token, user}
POST /user/refresh                                      → {access_token}
POST /user/logout
GET  /user/detail                                       → User
PATCH /user/detail    {name?, avatar?}
POST /user/pwd/forgot {email}   → 发送重置邮件（dev：写入日志 + data/outbox/*.eml；响应 data.dev_token 仅在 lite 模式返回）
POST /user/pwd/reset  {token, password}
POST /user/activation {token}                           (邮箱验证)
GET  /user/google/login → {url}；GET /user/google/callback?code= → 302 到 web /oauth/google?token=…   (未配置时 4400)

User = {id, email, name, avatar, plan, email_verified, created_at, current_workspace_id}

GET  /workspaces                                        → [Workspace]
POST /workspaces      {name}                            → Workspace
PATCH /workspaces/:wid {name}
GET  /workspaces/:wid/members                           → [{user_id, email, name, avatar, role}]
POST /workspaces/:wid/invitations {email, role}         → {token, url}
POST /workspaces/invitations/:token/accept              → Workspace
PATCH /workspaces/:wid/members/:uid {role}   DELETE /workspaces/:wid/members/:uid
POST /rbac/batch-check {checks:[{resource:"chat", id, action:"read|write|admin"}]} → {results:[bool]}
Workspace = {id, name, owner_id, plan, role, created_at}
角色：owner > admin > member > viewer
```

### 1.2 配置 / 模型
```
GET /configs/general → {basic_model, default_model, adv_models:[str], agent_mode:"full"|"lite", models:[{id,name,provider,description}]}
GET /ai/models       → [{id,name,provider,description}]
```

### 1.3 项目（chat）
```
GET    /chats?cur_page&page_num&is_starred&folder_id&keyword
POST   /chats {title?, first_message?: SendMessageBody, llm?:{default_model}, config?:{agent_mode, template_name}, remix?:{chat_id, version}}
       → Chat（若带 first_message 则同时发送，并开始运行）
GET    /chats/:id  → Chat
PATCH  /chats/:id  {title?, llm?, config?(merge), access_mode?, is_starred?, folder_id?}
DELETE /chats/:id
POST   /chats/:id/fork {version?}  → Chat   (Remix)
GET/POST/PATCH/DELETE /folders[/:fid] {name}

Chat = {
  chat_id, title, workspace_id, user_id, status:"ready"|"forking"|"failed", access_mode:"private"|"secret"|"public",
  folder_id, is_starred, fork_from, created_at, updated_at,
  config:{agent_mode, template_name, framework, plan_mode:bool, github?:{repo, branch}, supabase?:{project_ref,url}, cloud_enabled:bool},
  llm:{default_model},
  metadata:{status: ChatRuntimeStatus, is_running: bool, preview_port: 5173},
  uris:{dev: str|null, prod: str|null},
  version:{version, summary, cover, link}|null,
  stats:{likes, views}, is_liked: bool
}
ChatRuntimeStatus = running|stopping|sleeping|stopped|activating|failed
dev uri = {scheme}://{preview_port}-{chat_id}-preview.{OA_APP_DOMAIN}
```

### 1.4 消息
```
GET  /chats/:id/messages → {messages:[Message], action_datas:[ActionData], round_state:{active_attempts:{round_id: attempt}}, is_running:bool, last_seq:int}
     （按 round_state 过滤只返回活跃分支；每轮附带 attempts 数用于分支切换）
POST /chats/:id/messages  SendMessageBody → {message: Message, queued: bool, queue_item?: QueueItem}
     运行中 → 进入 prompt_queue（queued=true）。
SendMessageBody = {content: Delta, type?: "message", refer_id?: int, metadata?: {agent_mode?, default_model?, plan_mode?, attachments?: [{name,path,url,mime}], task_reply?: {message_id, option_index}}}
POST /chats/:id/messages/:mid/edit  {content}   → 新 attempt（同 round_id，attempt+1），并运行
POST /chats/:id/messages/:mid/retry             → 对该用户消息所在 round 新建 attempt 并重跑
POST /chats/:id/stop                            → 取消当前 Run
GET  /chats/:id/messages/search?keyword=
PUT  /chats/:id/round-state {active_attempts:{round_id: attempt}}
GET  /chats/:id/action-datas/:uuid → ActionData
GET  /chats/:id/block-timelines?version= → [BlockTimeline]
GET  /chats/:id/events?after=<seq>      → {events:[{seq, event, payload}], complete: bool}  (complete=false 表示缓冲已丢失，需全量重拉)
POST /chats/:id/approve-plan            → 继续运行
POST /chats/:id/decline-plan {reason?}
POST /chats/:id/submit-questions {message_id, answers:{qid: str|[str]}}
```
```
Message = {id, uuid, chat_id, role, name, type, content, refer_id, round_id, attempt, attempts, is_finished,
           metadata, user_id, created_at, updated_at, reply_messages:[Message]}
role ∈ "User"|"Team Leader"|"Product Manager"|"Architect"|"Engineer"|"Data Analyst"|"Deep Researcher"|"System"
name ∈ 用户名 | Mike|Emma|Bob|Alex|David|Iris
type ∈ message|think|task|emoji|questions_card|plan_ready|error|quota|restore|remix|version
Delta = [Op]; Op = {"insert": str} | {"insert": {"mentiontrigger": {"id": role, "char": "@", "value": name}}}
                  | {"action_data": {"uuid": str}} | {"insert": {"attachment": {name,url,mime}}}
think   ：content 为 Delta：模型思考文本 + 内联 action_data 引用（工具卡片）
task    ：content = {name, description, option:[{label, content, danger?}], status:"pending"|"finished", selected?: int}
questions_card：content = {title, questions:[{id, question, type:"single"|"multi"|"text", options?:[str]}], status:"pending"|"finished", answers?}
plan_ready：content = {summary, tasks:[Task], status:"pending"|"approved"|"declined"}
emoji   ：content = {id:"ok"}，refer_id 指向被 @ 的消息；GET 时挂到父消息 reply_messages
version ：content = {version, summary, link}
error/quota：content = {message, code?}

ActionData = {id, uuid, chat_id, message_uuid, command_name, block_type, file, src_path, extra, status:"running"|"success"|"failed", timeline_index, created_at, updated_at}
BlockTimeline = {id, chat_id, index, block_type, block_owner, command_name, file, src_path, content, is_running, extra_data, version, created_at}
Task = {id, title, assignee(role), status:"pending"|"running"|"done", instruction}
```

### 1.5 队列 / 版本 / 部署 / 预览
```
GET    /chats/:id/prompt-queue → {paused: bool, items:[QueueItem]}
POST   /chats/:id/prompt-queue {content, metadata?}
PATCH  /chats/:id/prompt-queue/:item {content}
DELETE /chats/:id/prompt-queue/:item
POST   /chats/:id/prompt-queue/reorder {ids:[int]}
POST   /chats/:id/prompt-queue/pause {paused: bool}
POST   /chats/:id/prompt-queue/:item/send     (若空闲立即运行，否则置顶)
QueueItem = {id, position, content, metadata, status:"queued"|"sent", created_at}

GET   /chats/:id/versions → [Version]
PATCH /chats/:id/versions/:v {summary}
POST  /chats/:id/versions/:v/restore → 新版本(内容回到 v)，并插入 type=restore 消息
Version = {version:"v1", summary, cover, link, commit, status:"building"|"ready"|"failed", created_at}
link = 静态预览：{scheme}://{uid}-{chat_id}-0-{version}.{OA_APP_DOMAIN}

GET    /chats/:id/deployment → Deployment|null
PUT    /chats/:id/deployment {version, is_always_latest, access_mode?} → Deployment
DELETE /chats/:id/deployment
Deployment = {chat_id, version, is_always_latest, access_mode:"public"|"private"(默认 public，控制发布站点访问；项目 access_mode 只控制预览), status:"deploying"|"success"|"failed", url, seq, updated_at}
url = {scheme}://{uid}-{chat_id}-{seq}-{latest|vN}.{OA_APP_DOMAIN}

POST /chats/:id/preview-ticket → {ticket, url}  url = 预览域/__preview_auth?ticket=…&redirect=/
POST /runtime/heartbeat {chat_id}
POST /chats/:id/runtime/start | /runtime/stop
GET  /chats/:id/logs/stream   (SSE，dev server 日志：data: {"line": "..."})
```

### 1.6 文件
```
GET    /chats/:id/files?path=          目录 → {type:"dir", entries:[{name,path,type,size,mtime}]}；文件 → {type:"file", path, content, size, binary:bool}
       path 以 ".storage/" 开头时从对象存储读取 block 快照
GET    /chats/:id/files/raw?path=      原始字节
GET    /chats/:id/files/tree           → 递归树（忽略 node_modules/.git/dist）[{name,path,type,children?}]
PUT    /chats/:id/files {path, content}
DELETE /chats/:id/files?path=
POST   /chats/:id/files/upload (multipart file, dir?) → {path, url}
GET    /chats/:id/files/export         → zip
```

### 1.7 集成 / Secrets / Cloud 数据库
```
GET/POST /chats/:id/secrets {key, value, env:"dev"|"prod"}   GET 值打码 "••••1234"
DELETE   /chats/:id/secrets/:sid
GET/POST/DELETE /workspaces/:wid/secrets
GET  /integrations                          → [{provider, connected, account}]
GET  /integrations/:provider/connect        → {url}  (github|supabase)；未配置 client id 时返回 4400 "not configured"
GET  /integrations/:provider/callback?code=&state=
POST /integrations/:provider/token {token}  (手动填 PAT / Supabase access token 的兜底)
DELETE /integrations/:provider
POST /chats/:id/github/repo {name, private}  POST /chats/:id/github/push {message}  POST /chats/:id/github/pull
POST /chats/:id/supabase/link {project_ref}

POST /chats/:id/cloud/enable                    → 为应用建 schema app_{chat}_dev / app_{chat}_prod（lite: 独立 sqlite 文件）
GET  /chats/:id/database/environments           → [{env, ready}]
GET  /chats/:id/database/:env/tables            → [{name, columns:[{name,type}], row_count}]
GET  /chats/:id/database/:env/tables/:t/rows?cur_page&page_num
GET  /chats/:id/database/:env/tables/:t/export  (CSV)   POST .../import (multipart CSV)
```

### 1.8 计费 / 协作 / 发现 / 分享
```
GET  /transaction/balance → {credits, plan, monthly_quota, used_this_month}
GET  /transaction/ledger?cur_page → 分页 [{id, delta, reason, ref, created_at}]
GET  /billing/plans → [{id:"free"|"pro"|"max", name, price_usd, monthly_credits, features}]
POST /billing/checkout {plan?|pack?} → {url}   (未配置 Stripe 时返回 dev 模拟 url: web /billing/mock?session=…)
POST /billing/mock/complete {session}           (仅 lite/dev)
POST /billing/webhook                           (Stripe 签名校验)
GET  /discover?sort=hot|new&cur_page → 公开项目 [{chat_id,title,cover,link,likes,views,author}]
POST /chats/:id/like   DELETE /chats/:id/like
GET  /share/:chat_id?version=   (无需登录；access_mode≠private) → {chat, messages, action_datas, timelines, versions}；含 secret 的 block 内容被打码
```

### 1.9 内部（gateway → api，头 `X-Internal-Token: OA_INTERNAL_TOKEN`）
```
GET  /internal/resolve?host=<Host 头>&path=<path>&cookie=<oa_preview cookie 值>
  → {kind:"sandbox", upstream:"http://127.0.0.1:54321"}           预览，已运行
  | {kind:"waking"}                                                 沙箱未运行（已异步触发唤醒）→ gateway 返回 503 唤醒页
  | {kind:"auth_required"}                                          私有项目且无有效 cookie → 403 页
  | {kind:"static", base:"http://127.0.0.1:8100/internal/static/{chat_id}/{version}"}   发布/版本
  | {kind:"notfound"}
GET  /internal/static/{chat_id}/{version}/{path}    从存储读发布产物（不存在 → 404，gateway 负责 SPA 回退到 index.html）
POST /internal/preview-auth {ticket, host} → {cookie, max_age}  校验票据，返回 cookie 值（gateway 写 oa_preview cookie）
应用后端（Cloud）请求：gateway 把发布域/预览域上 `/api/*` 请求代理到 api `/apps/api/*`，并加头 `X-OA-App-Chat: {chat_id}`、`X-OA-App-Env: dev|prod`、`App-Host: <host>`。
```

### 1.10 应用 SDK 后端（Cloud BaaS，api 路由前缀 `/apps/api/v1`，不走统一 envelope，直接返回 JSON）
```
GET  /auth/me   POST /auth/register {email,password}  POST /auth/login → {token, user}   POST /auth/logout
GET  /entities/{Entity}?query=<json>&sort=&limit=&skip=     → [row]
GET  /entities/{Entity}/{id}  POST /entities/{Entity}  PUT /entities/{Entity}/{id}  DELETE /entities/{Entity}/{id}
POST /entities/{Entity}/batch [rows]
     表不存在时自动建表（JSON 列 + id/created_at/updated_at/owner_id）
POST /storage/upload (multipart) → {url}   GET /storage/files/{key}
POST /aihub/gentxt {messages, model?, stream?} → SSE data: {"content":"…"} … data: [DONE]
GET  /config → {chat_id, env}
```
`packages/app-sdk`（TS，ESM，零依赖）导出 `createClient({baseUrl?})` → `{auth, entities: Proxy, storage, ai}`。模板内置 `src/lib/oa.ts` 复制此 SDK。

## 2. Socket.IO

- 路径 `/socket.io`，`transports:["websocket"]`，连接时 `auth: {token}` 或 `query.token`（access JWT）；无效 → 拒绝连接。
- 自动加入房间 `user:{user_id}`。
- C→S：`join_chat {chat_id}` → 回发 `join_chat_ack {chat_id, ok, code}`（code 0 / 4403 / 4404）；`leave_chat {chat_id}`。
- S→C 事件，载荷均含 `chat_id` 与 `seq`（每 chat 单调递增，所有事件共享同一计数，存于事件缓冲以供 `/events?after=`）：

| 事件 | 载荷 |
|---|---|
| `msg:create` | `{chat_id, seq, message: Message}` |
| `msg:update` | `{chat_id, seq, id, delta?: Delta(追加), content?: 全量(替换), is_finished?: bool, metadata?: dict}` |
| `action_data:update` | `{chat_id, seq, action_data: ActionData}` |
| `timeline:start` | `{chat_id, seq, timeline: BlockTimeline}` |
| `timeline:content` | `{chat_id, seq, index, content: str, mode:"append"|"replace"}` |
| `timeline:complete` | `{chat_id, seq, index, file, extra_data, is_running:false}` |
| `version:create` / `version:update` / `version:complete` | `{chat_id, seq, version: Version}` |
| `build:update` / `build:error` / `build:complete` | `{chat_id, seq, status, log?}` |
| `deploy:update` | `{chat_id, seq, deployment: Deployment}` |
| `chat:update` | `{chat_id, seq, chat: Partial<Chat>}`（含 metadata.status / is_running / title） |
| `prompt_queue:update` | `{chat_id, seq, paused, items}` |
| `prompt_queue:item_sent` | `{chat_id, seq, item_id, message_id}` |
| `chat_error:create` / `chat_error:delete` | `{chat_id, seq, error:{id, message}}` |
| `run:start` / `run:end` | `{chat_id, seq, round_id, attempt, status:"success"|"cancelled"|"failed"}` |
| `quota:update` | `{user_id, credits}`（发往 user 房间） |

- 流式节流：`msg:update`/`timeline:content` 约 50ms 合并一次。

## 3. Block 内容格式（timeline content 与 .storage 快照）

| block_type | command_name | 实时 content | 快照文件（.storage/{index}/{hash8}/…） | extra_data |
|---|---|---|---|---|
| `Editor` | editor_write / editor_edit_file_by_replace / editor_append_file / editor_read | replace：文件新全文 | `{name}.json` = `{path, before, after}` | `{path, language}` |
| `Terminal` | terminal_run | append：首行 `$ cmd\n` 然后输出 | `terminal.log` | `{cmd, exit_code}` |
| `Task` | plan_append_task / plan_finish_current_task / plan_reset_task | replace：JSON `{tasks:[Task], current_task_id}` | `plan.json` | — |
| `Browser` | browser_goto/click/scroll/type | replace：JSON `{url, title, screenshot, text}`（screenshot 为 `.storage/...png` 路径或 null） | `page.json` + `screenshot.png` | `{page_url}` |
| `Notebook` | dataanalyst_write_and_exec_code | replace：JSON `{cells:[{source, outputs:[{type:"stream"|"error"|"image", text?, data?}]}]}` | `code.ipynb`(简化 JSON) | — |
| `Browser-RT` | checkui_run | replace：JSON `{url, status, errors:[str], console:[str]}` | `check.json` | — |
| `Search` | searchenhancedqa_run | replace：JSON `{query, results:[{title,url,content}], answer}` | `search.json` | — |
| `Backend` | backendmanager_* | replace：JSON `{action, tables|sql, result}` | `backend.json` | — |

`src_path` = 沙箱路径 `/workspace/...`（Editor/Notebook），其余为 null。
秘钥打码：Terminal/Editor 内容中出现任一 secret 值（长度 ≥6）时替换为 `••••••`。

## 4. 智能体

| name | role | 工具 |
|---|---|---|
| Mike | Team Leader | plan_append_task, plan_reset_task, mention(role, instruction), ask_human, ask_questions, reply_to_human, plan_finish_current_task |
| Emma | Product Manager | editor_write, editor_read, searchenhancedqa_run, plan_finish_current_task |
| Bob | Architect | editor_write, editor_read, terminal_run, plan_finish_current_task |
| Alex | Engineer | editor_read, editor_write, editor_edit_file_by_replace, editor_append_file, terminal_run, checkui_run, backendmanager_*, ask_human, plan_finish_current_task, reply_to_human |
| David | Data Analyst | dataanalyst_write_and_exec_code, browser_goto, browser_click, browser_scroll, browser_type, searchenhancedqa_run, editor_write, plan_finish_current_task |

- `agent_mode=lite`：只有 Alex，无计划（直接 ReAct 至 reply_to_human）。
- `agent_mode=full`：Mike 先产出计划（plan_append_task × n，然后 mention 第一个 assignee）；被 @ 的角色先发 `emoji ok` 回执；按计划依次执行；全部完成后 Mike reply_to_human 总结。
- `plan_mode=true`：Mike 产出计划后发 `plan_ready` 卡片并挂起 Run；approve → 继续执行；decline → 结束。
- `ask_human`：发 `task` 卡片并挂起；用户回复（任意新消息，或卡片点击 = 带 `metadata.task_reply` 的消息）恢复并把回复作为工具结果。
- Alex 收尾：`pnpm build` 必须通过（最多自动修复 3 轮；仍失败 → ask_human 卡片 "Decision to continue fixing the build"）。
- 每个 Run 成功结束后：创建版本（git commit + tag vN → `pnpm build` → 上传 dist 到 `deploys/{chat}/{vN}/` → version:complete），若 deployment.is_always_latest 则自动切换。

## 5. sandboxd API（Go，头 `X-Sandbox-Token`，工作区根 `--root`，容器内 `/workspace`）

```
GET  /health → {"ok":true,"version":"…","root":"/workspace"}
POST /exec {cmd, cwd?, env?, timeout_ms?(默认 600000)} → text/event-stream
      data: {"type":"stdout","data":"…"} / {"type":"stderr","data":"…"} / {"type":"exit","code":0,"timed_out":false}
     （bash -lc；进程组超时 kill；cwd 相对 root）
GET  /fs/list?path=&depth=1 → {"entries":[{"name","path","type":"file"|"dir","size","mtime"}]}  path 相对 root
GET  /fs/read?path=   → 原始字节（404 {"error"}）
GET  /fs/stat?path=   → {"exists","type","size","mtime"}
POST /fs/write {path, content?|content_b64?, mode?}   自动建父目录
POST /fs/mkdir {path}   POST /fs/rm {path}   POST /fs/mv {from,to}
POST /process/ensure {name, cmd, cwd?, env?} → {"name","pid","status":"running","restarts"}   崩溃自动重启（退避）
POST /process/stop {name}   GET /process/list → [{name,pid,status,restarts,cmd}]
GET  /process/logs?name=&follow=0|1&tail=200 → follow=1 时 SSE data: {"line":"…"}；否则 {"lines":[…]}
GET  /ports?check=5173,3000 → {"open":[5173]}   (TCP 连接 127.0.0.1 探测)
POST /kernel/execute {code, timeout_ms?} → {"outputs":[{"type":"stream","name":"stdout","text":…}|{"type":"error","ename","evalue","traceback"}|{"type":"image","mime":"image/png","data":b64}], "ok":bool}
      （内嵌 Python 驱动的持久 REPL，状态跨调用保留；matplotlib 图自动转 png）
POST /kernel/restart
```
启动参数：`sandboxd --listen :49999 --root /workspace --token $SANDBOX_TOKEN`；也可用环境变量 `SANDBOXD_LISTEN/SANDBOXD_ROOT/SANDBOXD_TOKEN`。

## 6. 网关（Go，`apps/gateway`，:8080）

- Host 规则（去掉端口后匹配，`OA_APP_DOMAIN` 的主机部分默认 `app.localhost`）：
  - `^(\d+)-([0-9a-f]{32})-preview\.app\.localhost$` → 预览
  - `^(\d+)-([0-9a-f]{32})-(\d+)-(latest|v\d+)\.app\.localhost$` → 发布/版本
- 所有决策交给 api `/internal/resolve`（缓存 2s）。
- `kind=sandbox`：httputil.ReverseProxy（支持 WebSocket upgrade，用于 Vite HMR）。
- `kind=static`：GET `{base}/{path}`；404 且非资源扩展名时回退 `{base}/index.html`。
- `text/html` 响应（解 gzip 后）在 `</body>` 前注入 `<script src="/__oa/bridge.js" defer></script>`，并删 `Content-Length`/`Content-Security-Policy`，设置 `Content-Security-Policy: frame-ancestors 'self' http://localhost:3000 {OA_PUBLIC_WEB_URL}`。
- `/__oa/bridge.js`：内嵌（go:embed `apps/gateway/assets/bridge.js`，由 `apps/bridge` 构建产物复制而来）。
- `/__preview_auth?ticket=&redirect=`：调 api `/internal/preview-auth`，写 cookie `oa_preview`（HttpOnly, SameSite=None 在 https 下 / Lax 在 http 下），302 到 redirect。
- `kind=waking`：503 HTML 唤醒页（每 2s 自动刷新）。`auth_required`：403 页。`notfound`：404 页。
- `/api/*` 请求（在应用域名上）→ 代理到 api `/apps/api/*`，附加 `X-OA-App-Chat`、`X-OA-App-Env`（预览=dev，发布=prod）、`App-Host`。
- 环境变量：`GATEWAY_LISTEN=:8080`、`OA_API_URL=http://127.0.0.1:8100`、`OA_INTERNAL_TOKEN`、`OA_PUBLIC_WEB_URL`。

## 7. bridge.js（`apps/bridge`，TS → 单文件 IIFE）

- 向 `window.parent` postMessage：
  - `{type:"mgx-appview-error", payload:{message, stack, source, line, col}}`（onerror / unhandledrejection）
  - `{type:"mgx-appview-console", payload:{level, args:[str]}}`（console.log/info/warn/error 包装）
  - `{type:"mgx-appview-route", payload:{path, title}}`（pushState/replaceState/popstate/hashchange）
  - `{type:"mgx-appview-ready", payload:{path, title}}`
  - 可视化选区：收到父窗口 `{type:"appviewer:design-mode", enabled}` 后 hover 高亮、click 发 `{type:"appviewer:sync-design-selection", payload:{selector, tag, text, classes, rect}}`
- 收到 `{type:"appviewer:navigate", path}` 时 `history.pushState` + 触发 popstate；`{type:"appviewer:reload"}` 时 reload。
- 右下角 "Made with OpenAtoms" 角标（仅在非 iframe 中显示，即直接访问发布页时）。

## 8. 模板 `templates/react-shadcn-vite`

Vite 5 + React 18 + TS + Tailwind 3（沙箱镜像 Node 22，pnpm 11 要求）+ 少量 shadcn 风格组件（button/card/input）+ `src/lib/oa.ts`(app-sdk)。
- `package.json` 脚本：`dev`=`vite --host 0.0.0.0 --port ${PORT:-5173}`（通过 vite.config 读 `process.env.PORT`），`build`=`tsc -b && vite build`(若嫌慢可只 vite build)，`lint`。
- `vite.config.ts`：`server.allowedHosts: true`、`server.hmr.clientPort` 由 env `OA_HMR_CLIENT_PORT` 决定（经 gateway 时 8080）。
- 首次创建工作区：复制模板（不含 node_modules）→ `pnpm install --prefer-offline`（共享 store）→ `git init && git add -A && git commit -m init`。
- `.atoms/memory.md` 存项目记忆。

## 9. 前端（`apps/web`）路由

`/`(落地) `/login` `/register` `/password/forgot` `/password/reset` `/verify-email` `/oauth/google` `/dashboard` `/chat/[id]` `/share/[id]` `/my-projects` `/discover` `/pricing` `/settings`(profile/workspace/members/secrets/integrations/billing) `/workspace/invite/[token]` `/billing/mock`
access token 存 localStorage `oa_token`；401 → 调 `/user/refresh` 一次，失败跳 `/login`。

## 10. Python 内部接口（services/server/oa）

已存在（由主线编写，勿改签名）：`oa/config.py`(settings)、`oa/db/models.py`、`oa/db/session.py`(SessionLocal/session_scope/get_session/init_db)、`oa/core/events.py`(bus.emit/emit_user/replay/persist_seq)、`oa/core/storage.py`(get_storage)、`oa/domain/serializers.py`。

**工作区存算分离**：工作区始终在宿主路径 `settings.workspace_dir(chat_id)`（docker provider bind-mount 到容器 `/workspace`）。平台侧文件 API 直接读写宿主路径，不需要沙箱运行。

### 10.1 沙箱（owner: agents 线）`oa/sandbox/`
```python
# oa/sandbox/client.py
class SandboxClient:
    def __init__(self, base_url: str, token: str): ...
    async def health(self) -> dict
    def exec(self, cmd: str, cwd: str | None = None, env: dict | None = None, timeout_ms: int | None = None) -> AsyncIterator[dict]   # sandboxd SSE 事件
    async def run(self, cmd: str, cwd=None, env=None, timeout_ms=None) -> tuple[int, str]       # 汇总输出
    async def read(self, path: str) -> bytes ; async def write(self, path: str, content: str | bytes) -> None
    async def list(self, path: str = ".", depth: int = 1) -> list[dict] ; async def stat(self, path) -> dict
    async def rm(self, path) ; async def mv(self, src, dst) ; async def mkdir(self, path)
    async def process_ensure(self, name, cmd, cwd=None, env=None) -> dict ; async def process_stop(self, name)
    def process_logs(self, name: str, follow: bool = True) -> AsyncIterator[str]
    async def ports(self, check: list[int]) -> list[int]
    async def kernel_execute(self, code: str, timeout_ms: int = 120000) -> dict

# oa/sandbox/manager.py
async def init_workspace(chat_id: str, from_chat: str | None = None, version: str | None = None) -> None  # 模板拷贝/fork 拷贝 + git init
async def ensure_running(chat_id: str) -> SandboxClient     # 必要时 init_workspace；启动 provider；等健康；确保 dev 进程；更新 Chat.runtime_status 并 emit chat:update；配额超限抛 SandboxFull
async def get_client(chat_id: str) -> SandboxClient | None  # 运行中才返回
async def stop(chat_id: str) -> None
async def resolve_port(chat_id: str, port: int) -> str | None   # 运行中且端口就绪 → upstream URL
async def wake(chat_id: str) -> None                         # 后台 ensure_running（去重）
async def heartbeat(chat_id: str) -> None
async def reaper_loop() -> None                              # 空闲 settings.sandbox_idle_seconds 后 stop
class SandboxFull(Exception)
```

### 10.2 Agent 运行时（owner: agents 线）`oa/agents/runner.py`
```python
async def start_round(chat_id: str, message_id: int) -> int   # 调用方已存好用户消息(round_id/attempt)；创建 Run 并调度（inproc 任务或 arq）；返回 run_id
async def resume(chat_id: str, action: str, message_id: int | None = None, payload: dict | None = None) -> bool
      # action: "reply"(ask_human 回复) | "approve" | "decline" | "questions"
async def cancel(chat_id: str) -> bool
async def suspended_run(chat_id: str) -> Run | None
```
运行开始/结束必须设置 `Chat.is_running` 并 emit `chat:update` 与 `run:start`/`run:end`；结束后调用 `oa.domain.queue.pop_next(chat_id)`；成功后调用 `oa.domain.versions.create_version(chat_id, summary, run_id)`。
`oa/worker.py`：arq WorkerSettings，函数 `run_job(ctx, run_id)`。

### 10.3 平台域服务（owner: core 线）`oa/domain/`
```python
# oa/domain/versions.py
async def create_version(chat_id: str, summary: str, run_id: int | None = None) -> Version    # git commit+tag → pnpm build → 上传 dist → version:* 事件 → always_latest 自动部署
async def restore_version(chat_id: str, version: str, user_id: int) -> Version
# oa/domain/queue.py
async def pop_next(chat_id: str) -> None     # 未暂停且有排队项 → 作为用户消息发送并 start_round
# oa/domain/messages.py
async def post_user_message(session, chat, user, body: dict) -> tuple[Message, bool, PromptQueueItem | None]  # 处理 round/attempt、排队、启动
# oa/domain/secrets.py
async def secrets_env(chat_id: str, env: str = "dev") -> dict[str, str]
def mask(text: str, values: list[str]) -> tuple[str, bool]
# oa/domain/billing.py
async def charge_usage(chat_id: str, run_id: int | None, role: str, model: str, input_tokens: int, output_tokens: int) -> None
async def has_credits(chat_id: str) -> bool
# oa/domain/cloud.py（Cloud BaaS，供 backendmanager_* 工具与 /apps/api 使用）
async def ensure_cloud(chat_id: str) -> None
async def create_table(chat_id: str, env: str, name: str, columns: list[dict]) -> dict
async def get_schemas(chat_id: str, env: str) -> list[dict]
async def insert_rows(chat_id: str, env: str, table: str, rows: list[dict]) -> int
```
`oa/main.py`：`app`（FastAPI，含 socketio ASGI 挂载于 `/socket.io`），lifespan 中 `init_db()`、`bus.attach(sio)`、启动 `reaper_loop`。启动命令：`uv run uvicorn oa.main:app --port 8100`。

## 11. 实现说明与偏离（随实现同步）

**端口**：dev 下 api 改为 **8100**（8000 常被其它本地服务占用）；容器/K8s 内仍为 8000。

**REST / 实时**
- `POST /chats/:id/messages` 排队时返回 `message: null, queued: true, queue_item`；用户消息在队列项真正发送时才创建。
- `/internal/*` 返回裸 JSON，同时附带 `code/message/data` 字段，网关两种都能解析。
- JWT 以 `sha256(OA_JWT_SECRET)` 为 HS256 密钥；鉴权也接受 `?token=`（SSE、下载链接）。
- `oa.main:app` 是包裹 FastAPI（导出名 `api`）的 Socket.IO ASGI 应用。
- 部署有独立 `access_mode`（默认 public，控制发布站点）；项目 `access_mode` 只控制开发预览与版本预览链接。`DELETE /deployment` 为软删除，删除后再发布 `seq+1`（URL 变化），在线改指向不变 URL。
- 版本/恢复消息 role=`System`。时间戳一律带 UTC 偏移。
- `run:end` 可为 `status:"suspended"` 并带 `suspend_reason`；`run:start` 带 `resumed`。
- `plan_ready` 待确认时发送自由文本 = 拒绝该计划并按反馈重新规划；`start_round` 会取消残留的挂起/陈旧 Run。
- 额外端点：`user/activation/resend`、`workspaces/{wid}/switch`、`GET workspaces/invitations/{token}`、`billing/packs`、`billing/mock/{sid}`、`POST chats/:id/versions`（手动快照）、`messages/:mid/usage`，以及 Atoms 别名 `mgx-runtime/heartbeat`、`log-files/chats/:id/stream`、`files/project-export`。

**应用后端（Cloud）**：`/apps/api/v1` 信任网关注入的 `X-OA-App-Chat`（网关会丢弃客户端自带的同名头）；实体查询支持 Mongo 风格操作符；`aihub/gentxt` 走 `OA_LLM_BASE_URL`，无 key 或 `oa-mock` 时为离线回显流。

**网关**：预览 cookie 在 https 与 `*.localhost`（浏览器视为安全上下文）下为 `SameSite=None; Secure; Partitioned`（CHIPS），可在工作台 iframe 中使用；其它 http 主机回退 Lax。注入 `window.__OA_HOME` 供角标链接。

**沙箱**：sandboxd `/exec` 用 `bash -c`（继承 PATH，不读 login profile）；`/workspace/...` 路径映射到 `--root`。镜像固定 pnpm store 到预热目录；`Sandbox.ports` 额外存 `_dev_env`。LocalProvider 会从子进程环境中剔除 `OA_*` 与凭据类变量。端口就绪以沙箱内 `/ports` 探测为准（Docker Desktop 端口代理会提前接受连接）。附加 `manager.restart_dev()/destroy()`；`K8sProvider`（每 chat 一个 Pod、PVC subPath、gVisor）已在 kind 上实测，包括 gVisor（见 §11.1）。沙箱 Pod 的 uid（`OA_K8S_RUN_AS_USER`，默认 1000）必须与写共享 PVC 的 api/worker uid 一致，镜像和清单都已设为 1000。

**数据库**：lite 用 `create_all`；full 用 Alembic（`services/server/oa/db/migrations`，`uv run alembic revision --autogenerate`），启动时在 Postgres advisory lock 下 `upgrade head`。

**对象存储**：compose 使用 versitygw（S3 API over POSIX 目录，宿主端口 9000，凭据即 `OA_S3_KEY/OA_S3_SECRET` 默认值）。MinIO 官方镜像已不可拉取，SeaweedFS 单节点在压测下失去 leader 导致 PutObject 全部 502，故弃用。S3 客户端使用自适应重试与更大连接池。macOS 上 `localhost` 解析每次多约 1 s，所以本地默认端点一律用 `127.0.0.1`。

**数据库连接**：请求处理中会嵌套开第二个 session，因此连接池默认较大，可用 `OA_DB_POOL_SIZE` / `OA_DB_MAX_OVERFLOW` / `OA_DB_POOL_TIMEOUT` 调整。根治方案（复用调用方 session）见 `impl/prod.md` §8。

**Alembic 版本链**：`0001_baseline_schema` → `0002_p9_features`（新增 `chats.parent_chat_id`、`boosts`、`automations`）。新迁移必须接在当前唯一 head 之后。

**客户端事件处理**：web 端用 `socket.onAny` 消费所有带 `seq` 的 chat 事件。发现缺口时调用 `/events?after=` 回放；回放进行中到达的实时事件会先缓冲，回放结束后按 seq 顺序应用，这样 `lastSeq` 不会越过尚未回放的事件。

### 11.1 生产化（P8，详见 `docs/impl/prod.md`）
- **预热池**：`OA_SANDBOX_WARM_POOL`（docker/k8s 默认 2）。认领一个 slot 时，把工作区 rename 进 slot 目录，原路径留相对 symlink，平台侧路径不变。slot 只用一次，release 时把工作区移回原位并销毁 slot。full 模式下就绪队列在 Redis。
- **配额**：并发沙箱超限返回 `4122`；磁盘超限 `4292`，并推送 `disk:quota:update` / `disk:exceeded`；每日积分超限 `4293`，并推送 `quota:daily_exhausted`；限流返回 HTTP 429 + `4291` + `Retry-After`；请求体过大返回 HTTP 413 + `4413`。全局准入由 `OA_SANDBOX_MAX_TOTAL` 控制，达到上限的启动排队等待，不直接失败。套餐限额可用 `OA_PLAN_LIMITS` 覆盖。
- **内容审核**：每条 prompt（含入队的）都会审核，命中返回 HTTP 400 + `4451`（带 category）；可设 `OA_MODERATION=keyword|webhook|off`。滥用监控会 kill 挖矿进程和长时间满载 CPU 的进程，发现端口扫描时停止沙箱，并推送 `chat_error:create`。
- **可观测性**：api `/metrics`（可选 `OA_METRICS_TOKEN`）、worker `OA_WORKER_METRICS_PORT`、网关 `/__oa/metrics`；设置 `OTEL_EXPORTER_OTLP_ENDPOINT` 启用 OTel，设置 `SENTRY_DSN` / `NEXT_PUBLIC_SENTRY_DSN` 启用 Sentry；`OA_LOG_FORMAT=json` 输出结构化日志。
- **运维脚本**：`scripts/backup.sh` / `scripts/restore.sh`、`scripts/loadtest.py`、`scripts/measure_sandbox_start.py`、`scripts/k8s_kind_e2e.sh`（kind 上验证了 K8sProvider 与清单）。gVisor：`OA_KIND_GVISOR=1` 时沙箱跑在 runsc 上，P1–P7 e2e 通过；netstack 模式下经 podIP 访问（生产路径）也已验证。集群外 api 用 port-forward 访问时需改用 `network=host` 的 handler（`gvisor-hostinet`），因为 port-forward 连不到 netstack 的监听。Docker provider + runsc 未实测。详见 `impl/prod.md` §7.1。

### 11.2 P9 功能（详见 `docs/impl/p9-features.md`）
- **竞赛模式（BoN）**：`POST /chats/:id/messages` 带 `use_boost, boost_models[2..4]`。每个候选是一个隐藏的子 chat（`parent_chat_id`），各自走普通的一轮。事件：`boost:update` / `boost:all_end` / `boost:merge_end`。接口：`GET /chats/:id/boost`、`POST .../boost/select {index}`、`POST .../boost_abort`、`DELETE .../boost`。候选数受套餐并发沙箱数限制，超出返回 4400；子 chat 不出现在 `/chats`、`/discover`。
- **专家角色**：Iris（深度研究，带引用的报告）、Sarah（SEO）、Adrian（广告投放计划）。
- **定时任务**：`/automations` 系列 REST 接口与 `automation:update` 事件。cron 为 5 字段，按 IANA 时区计算；调度器用对 `next_run_at` 的 CAS 认领，保证不重复触发。inproc 模式为进程内循环，arq 模式为 30 s 的 cron 任务。
- **多模态工具**：`imagecreator_generate_image`、`imageanalyzer_analyze_images`、`pdfanalyzer_analyze_pdfs`、`documentextractor_extract_documents`、`markdownpdfcreator_convert_file`。图片结果用 `Assets` 块，文档用 `File` 块。
- **移动端**：`config.template_name: "expo-app"`（Expo SDK 58 + react-native-web）。`dev` 是 Metro web 开发服务器，`build` 为 `expo export --platform web`，版本与发布流程不变。沙箱镜像预热了 Expo 依赖，修改模板后必须 `make image`。
- **行为变化**：工具内抛出 `SandboxFull` 会让整个 run 失败，并插入一条说明套餐限制的 System 消息。

### 11.3 可视化编辑（详见 `docs/impl/visual-edit.md`）
- 模板的 Vite 插件为每个 JSX 元素注入 `data-oa-loc="path:line:col"`；bridge 提供 design mode。
- 接口：`GET|POST|DELETE /chats/:id/edit-session`，`POST .../edit-session/commit` 返回 `{version, files, edits}`。commit 全有或全无：有失败返回 4400 并带逐条错误，agent 运行中返回 4409。新增事件 `edit_session:update`（共用 chat seq）。
- 可视化编辑生成的消息 role 为 `System`（带用户名），不进入 round/attempt 分支树。

### 11.4 验证范围（详见 `docs/impl/verify.md`）
- **已实测**：真实 Playwright（checkui）；真实 LLM 客户端经 HTTP 调用 OpenAI 兼容服务，含注入 503 重试和 LiteLLM；GitHub / Supabase / Stripe / Google 对接本地伪服务端；mailpit SMTP。
- **需要真实账号才能验证**：模型 API key、各 OAuth 应用、Stripe 测试密钥、真实 SMTP。
