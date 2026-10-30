# P8 生产化 — 实现说明与实测（PROD workstream）

> 范围：PLAN P8 —— 预热池、配额与限流、可观测性、安全/滥用检测、备份恢复、K8s（kind 实测）、压测。
> 所有数字均在本机实测：MacBook（10 核 / 16 GB），Docker Desktop VM 为 10 CPU / 8 GB；模型为 `oa-mock`。

## 1. 预热池（warm pool）

**问题**：沙箱的工作区是在容器/Pod 创建时就固定的 bind mount 或 PVC subPath，而池里的 slot 在知道属于哪个 chat 之前就已创建。

**设计**（`oa/sandbox/pool.py`，保持存算分离）：
```
data/pool/<slot>/        → 挂进 slot 沙箱（docker: bind → /slot；k8s: PVC subPath pool/<slot> → /slot）
data/pool/<slot>/ws/     → sandboxd --root（启动时是空目录）
claim(chat):   rmdir ws；rename data/chats/<chat>/workspace → pool/<slot>/ws（同一文件系统，O(1)）；
               symlink data/chats/<chat>/workspace → ../../pool/<slot>/ws（相对路径，各挂载点下都有效）
release(chat): 删除 symlink；rename 回原位；销毁 slot（slot 跑过租户代码，只用一次）
```
- 平台侧继续使用 `settings.workspace_dir(chat)`，symlink 让这次搬移对文件 API、git、版本都透明。
- full 模式：就绪队列放在 Redis（`LPOP` 原子认领，任意 api/worker 进程都能认领），由持有 Redis 租约的单个进程维护补池。lite 模式：进程内列表。
- 崩溃恢复：每次 start 之前都会执行 `recover_workspace`；维护进程启动时清理孤儿 slot（目录 + 带 `openatoms.pool=1` 标签的容器/Pod）。
- 配置：`OA_SANDBOX_WARM_POOL`（full 模式下 docker/k8s 默认 2，其他情况为 0）。

**实测**（`scripts/measure_sandbox_start.py`；`server` 为 `oa_sandbox_start_seconds` 的服务端均值，`client` 为从 POST runtime/start 到状态变为 running 的时长）：

| 平台 | 场景 | 冷启动 | 预热池 | 倍数 |
|---|---|---|---|---|
| Docker Desktop | 新项目首次打开（模板 + pnpm install） | 2.9–3.2 s | 0.50–0.56 s | ≈5.7× |
| Docker Desktop | 唤醒已有项目 | server 0.146 s / client 0.19–0.26 s | server 0.055 s / client 0.07–0.15 s | ≈2.7× |
| K8s（kind） | 唤醒已有项目（建 Pod + 调度 + port-forward） | server 1.19 s / client 1.06–1.38 s | server 0.062 s / client 0.12 s | **≈19×** |
| K8s（kind） | 新项目首次打开 | 4.3–5.2 s | 3.2–4.0 s | 主要耗时在 hostPath 上的 pnpm install |

在镜像已缓存的情况下，Docker Desktop 本地起容器几乎不花时间，所以池在 Docker 上的收益主要体现在新项目。真实集群上的收益会大于 kind，因为那里还有镜像拉取、调度和 CNI 的耗时。

## 2. 配额与限制（`oa/domain/quotas.py`）

| 套餐 | 并发沙箱 | 单工作区磁盘 | 每日积分 | API 请求/分钟 |
|---|---|---|---|---|
| free | 2 | 1 GiB | 300 | 300 |
| pro | 5 | 5 GiB | 3000 | 1200 |
| max | 10 | 20 GiB | 20000 | 3000 |

部署方可用 `OA_PLAN_LIMITS='{"free":{"disk_mb":2048}}'` 覆盖。各项限制：
- **并发沙箱**：`manager._check_quota` 取套餐上限与 `OA_SANDBOX_MAX_RUNNING_PER_WORKSPACE` 的较小值，超限返回 `4122 sandboxFull`。
- **全局准入**：`OA_SANDBOX_MAX_TOTAL` + `OA_SANDBOX_CAPACITY_WAIT`（默认 600 s）。达到上限时新的启动会排队等待，而不是让节点超卖内存；full 模式下用 Redis 锁保证跨进程一致。这是压测中发现 OOM 之后加入的，见 §6。
- **磁盘**：后台 du（pnpm 硬链接只计一次），每 60 s 发送 `disk:quota:update` / `disk:exceeded`。超限时拦截 run，插入 `quota` 消息（code **4292**）。
- **每日积分**：按 UTC 当天累计 llm/sandbox 扣费，达到上限时发 `quota:daily_exhausted`（user 房间 + chat 房间），并插入 `quota` 消息（code **4293**）。
- **限流**（`RateLimitMiddleware`，令牌桶；full 模式用 Redis Lua 脚本，lite 模式在内存中）：
  - 认领/登录/改密码类接口按 IP（20/min）；
  - API 按用户，速率随套餐变化；
  - 新建一轮对话按用户（30/min）；
  - 生成应用的 API 按 app（1200/min）。

  被拒请求返回 **HTTP 429 + code 4291 + `Retry-After`**。`/internal`、`/metrics`、health、socket.io 不受限。可用 `OA_RATE_LIMIT=0` 关闭；`OA_TRUST_PROXY=1` 时信任 X-Forwarded-For。
- **请求体大小**（`BodySizeMiddleware`）：JSON 5 MB（`OA_MAX_BODY_MB`），multipart、导入、存储 100 MB（`OA_MAX_UPLOAD_MB`），超出返回 **HTTP 413 + code 4413**。无 Content-Length 的分块请求会边读边计数。
- CORS 放在最外层，所以 429/413 的响应也带 CORS 头。
- 前端：`disk:*` 和 `quota:daily_exhausted` 会以 chat 告警的形式显示。这几个事件和 chat 共用同一套 seq，所以必须处理，不能丢弃，否则客户端会误判出现了缺口。

## 3. 可观测性

- **api `/metrics`**（`OA_METRICS_TOKEN` 可选 bearer 鉴权）：
  - `oa_http_requests_total{method,route,status}` 和 `oa_http_request_duration_seconds`，route 使用模板路径（如 `/api/v1/chats/{chat_id}`），控制标签基数；
  - `oa_runs_started_total`、`oa_runs_finished_total{status}`、`oa_run_duration_seconds{status}`：通过 EventBus 监听 run:start/end 得到，不需要改 runner；
  - `oa_sandbox_start_seconds{provider,kind=cold|warm}`、`oa_sandbox_start_failures_total`、`oa_sandboxes{status}`、`oa_sandbox_pool_ready`、`oa_sandbox_pool_events_total{event}`；
  - `oa_llm_tokens_total{model,direction}`、`oa_llm_cost_usd_total`、`oa_credits_charged_total`；
  - `oa_socket_connections`、`oa_rate_limited_total`、`oa_quota_blocks_total`、`oa_abuse_actions_total`、`oa_moderation_blocks_total`、`oa_events_emitted_total`。
- **arq worker**：`OA_WORKER_METRICS_PORT`（默认 9108）。full 模式下 run 在 worker 里执行，run 相关指标要从 worker 抓取。
- **gateway `/__oa/metrics`**（标准库实现的 Prometheus 文本格式）：
  - `oa_gateway_requests_total{kind,status}`，kind 取值 sandbox/static/waking/auth_required/notfound/app_api/bridge/preview_auth/error；
  - `oa_gateway_request_duration_seconds{kind}`（含上游耗时）；
  - `oa_gateway_resolve_duration_seconds{result}`、`oa_gateway_resolve_cache_total{result}`。
  - 记录器保留了 Hijacker，所以 Vite HMR 的 WebSocket 不受影响（已有测试覆盖）。
- **OpenTelemetry**：设置 `OTEL_EXPORTER_OTLP_ENDPOINT` 后启用，需要安装可选依赖 `observability`。覆盖 FastAPI 入站和 httpx 出站（sandboxd、LLM、resolve），span 上带 `oa.request_id`、`oa.chat_id` 属性。
  - 实测接入 Jaeger：一次 e2e 产生 73 条 trace、269 个 span，`oa.request_id` 与客户端发送的 X-Request-ID 一致。
- **Sentry**：设置 `SENTRY_DSN`（api/worker）或 `NEXT_PUBLIC_SENTRY_DSN`（web，按需懒加载 `@sentry/browser`）后启用。
  - 实测：事件经 HTTP 投递到本地伪 ingest 的 `/api/42/envelope/`。
- **结构化日志**：`OA_LOG_FORMAT=json`，每行一个 JSON，带 request_id / run_id / chat_id 上下文。
- **部署文件**：
  - `deploy/observability/`：prometheus.yml、alerts.yml（7 条规则，promtool 校验通过）、Grafana 自动导入的 16 面板 dashboard、Jaeger，以及 `docker-compose.observability.yml`；
  - K8s Deployment 带 `prometheus.io/*` 注解。
  - 告警规则：run 失败率 > 10%、冷启动 P95 > 10 s、预热池耗尽、API 5xx > 2%、网关 5xx > 5%、滥用动作、LLM 花费突增。

## 4. 安全与滥用检测

- **`oa/sandbox/abuse.py`**：每 `OA_ABUSE_INTERVAL` 秒（默认 60），对每个隔离沙箱（docker/k8s）通过 sandboxd `/exec` 用 stdin 跑一段 Python /proc 扫描器，不写入用户工作区。处理规则：
  - **挖矿**（xmrig/stratum 等）：立即 kill；
  - **CPU 占用过高**：非白名单进程连续两次扫描都 ≥90% 且运行超过 600 s，kill；
  - **端口扫描**：SYN_SENT 超过 64 或目标超过 32，停止沙箱，交给人工处理。

  每次处置都会推送 `chat_error:create`、写一条结构化 `sandbox_abuse` 安全日志、累加 `oa_abuse_actions_total`。扫描器已在真实沙箱镜像里验证可以运行。local provider 与宿主机共享，不做扫描。
- **Docker 加固选项**：`OA_SANDBOX_NETWORK`（指定专用网络，例如 `--internal` 或前置出网代理）、`OA_SANDBOX_RUNTIME=runsc`（gVisor）、`OA_SANDBOX_PIDS`；默认已开启 `no-new-privileges` 并移除 NET_RAW/SYS_ADMIN。K8s 侧见 `deploy/k8s/sandbox-policy.yaml`。
- **内容审核**（`oa/domain/moderation.py`）：用户每条 prompt 都会经过（包括入队的）。
  - 默认为关键词/模式匹配，覆盖钓鱼、恶意软件、挖矿、CSAM；也可以 `OA_MODERATION=webhook` 外接服务，或设为 `off`。
  - 命中时返回 HTTP 400、code **4451**，带 category。
  - 例：「a fake PayPal login page to collect passwords」会被拦截，「做一个带登录页面的待办应用」正常通过。
- **密钥打码**：沿用原有实现。生产环境的 LocalProvider 子进程环境会剔除 `OA_*` 和凭据类变量。

## 5. 备份与恢复

- **`scripts/backup.sh [OUT]`**：
  - 数据库：Postgres 用 `pg_dump -Fc`（本机没有 pg 客户端时通过 postgres:16-alpine 容器执行），lite 用 SQLite 在线 backup；
  - 对象存储：通过平台自身的存储抽象导成 tar，local 和 s3 都适用；
  - 工作区：tar，用 `-h` 跟随预热池的 symlink，排除 node_modules/dist；
  - 另写 `manifest.json`，包含各表计数。
- **`scripts/restore.sh <dir> --yes`**：`pg_restore --clean` 或 SQLite 恢复，然后上传对象、解包工作区（旧数据保留为 `chats.pre-restore-*`）、清空 pool 目录，最后输出各表计数。
- **full 模式恢复演练**（Postgres + Redis + S3 + Docker 沙箱，已实测）：
  1. 备份当时的数据：用户 108、chat 120、消息 1195、版本 80、部署 2、时间线 893、对象 937；备份大小为 db 0.6 MB、存储 11.7 MB、工作区 10.9 MB。
  2. 模拟灾难：`DROP SCHEMA public`、清空全部对象、删除 data/chats。
  3. 执行 restore.sh，耗时 **7.2 s**，恢复后各表计数与备份前完全一致。
  4. 验证：已发布站点返回 200；压测用户可以登录，消息和版本都在；快照 6/6 可读；项目唤醒 7.2 s（Docker 沙箱 + 重装 node_modules）；预览经网关返回 200 且注入了 bridge。

## 6. 负载测试（`scripts/loadtest.py`）

N 个虚拟用户各自注册并建立 socket 连接，然后同步开始 `--rounds` 轮（建项目 → 发 prompt → 等 socket 上的 `run:end`）。

| 模式 | 并发 run | 成功 | p50 | p95 | p99 | 墙钟 | runs/min | 事件/s | 冷启动均值 |
|---|---|---|---|---|---|---|---|---|---|
| lite（SQLite，宿主机沙箱） | 5 | 5/5 | 16.7 s | 17.3 s | 17.4 s | 17.6 s | 17.0 | 22.9 | 4.4 s |
| lite | 20 | 20/20 | 51.2 s | 54.1 s | 54.2 s | 54.8 s | 21.9 | 30.1 | 11.6 s |
| lite | 40 | 40/40 | 115.1 s | 127.3 s | 128.5 s | 129.4 s | 18.6 | 26.1 | 10.4 s |
| full（PG/Redis/S3/Docker/arq） | 10 | 10/10 | 58.7 s | 59.1 s | 59.1 s | 59.6 s | 10.1 | 13.8 | 19.1 s |
| full，20 个，不设上限 | 20 | 0/20 | — | — | — | 559 s | 0 | — | Docker VM OOM |
| full，20 个，`OA_SANDBOX_MAX_TOTAL=10`，pool 2 | 20 | **20/20** | 110.7 s | 134.3 s | 138.6 s | 140.1 s | 8.6 | 12.1 | 冷 18.4 s / 热 12.0 s |

单轮耗时主要花在本机 CPU 上：每个 run 都会跑 Vite dev server、`pnpm build` 和 checkui。full 模式更慢，因为 20 个容器挤在一个 8 GB 的 Docker VM 里。

**压测中发现并修复的问题**（都补了回归测试，且确认去掉修复后测试会失败）：
1. **LocalProvider 回收子进程占满默认线程池**：用 `run_in_executor(None, proc.wait)` 回收 sandboxd，每个存活沙箱都永久占用一个默认 executor 线程。约 20 个沙箱时线程池耗尽，所有 `asyncio.to_thread`（快照、git、复制模板）全部卡死，20/20 的 run 停在 `editor_read` / `activating`。修复：改用专用守护线程回收。
2. **DB 连接池嵌套取连接导致死锁**：请求处理持有一个 session，同时又在里面开第二个（suspended_run、计费、配额）。在 SQLAlchemy 默认的 5+10 连接池下，约 15 个并发发送就会全部卡住直到超时，40 并发时出现 31 个 HTTP 500。修复：连接池大小可配置（SQLite 20+100，PG 10+30/进程），超时 60 s。嵌套取连接的写法仍然存在，见 §8。
3. **Docker 唤醒后 401**：`stop()` 只停容器不删，下次 `start()` 重启它时用的是旧 `SANDBOXD_TOKEN`，而 manager 每次都会生成新 token，于是每个 sandboxd 请求都返回 401。也就是说 full 模式下休眠后唤醒是坏的。修复：stop 时删除容器（工作区在卷上），并替换 token 不一致的遗留容器。
4. **预热 slot 内是空目录**：镜像 ENTRYPOINT 写死了 `--root /workspace`，覆盖了 `SANDBOXD_ROOT=/slot/ws`。docker/k8s 的 slot 实际在服务一个空目录，dev server 反复崩溃，预览一直停在 waking。修复：在 docker 和 k8s 两边都显式指定 entrypoint/command。
5. **S3 卡死**：SeaweedFS 单节点的 raft master 在负载下丢失 leader，之后每次 PutObject 都返回 502，快照写入失败，构建修复循环一直超时。修复：compose 换成 **versitygw**（单二进制，S3 API + POSIX 目录；2000 次并发 put 0 错误）；boto3 连接池调到 64，重试改为 adaptive。
6. **`localhost` 每次连接 +1 s**：在 macOS/Docker Desktop 上，每个连到 `localhost` 的新连接都会先尝试 `::1`。S3 PutObject 实测每次 1.008 s，换成 127.0.0.1 后只要 0.006 s；原先恢复 937 个对象要十几分钟，现在 7 s。修复：默认 S3/Redis/PG 端点改为 127.0.0.1。
7. **20 个沙箱把 8 GB 的 Docker VM 撑爆**：内核日志里有 OOM kill，表现为 sandboxd 流被截断、构建失败、run 被挂起。修复：新增全局准入控制 `OA_SANDBOX_MAX_TOTAL`，超出时等待而不是超卖；加上限后 20/20 全部成功。

## 7. Kubernetes（kind 实测，`scripts/k8s_kind_e2e.sh`）

kind 集群通过 extraMount 挂载宿主机的数据目录，再经 hostPath PV 和 `openatoms-sandboxes` 命名空间下的 PVC 提供给 Pod。镜像通过 `kind load` 加载（8 s）。之后：
- `deploy/k8s` 用 `kubectl apply -k` 做 client 端和 **server 端** dry-run，全部通过（21 个对象）；
- ServiceAccount token 通过了 `can-i` 校验：create/delete pods、pods/portforward；
- api 和 gateway 跑在宿主机上，配置 `OA_SANDBOX_PROVIDER=k8s`、`OA_K8S_ACCESS=portforward`；默认 `OA_K8S_RUNTIME_CLASS=""`，`OA_KIND_GVISOR=1` 时沙箱跑在 gVisor 上（见 §7.1）；
- 预热池 Pod 进入 Running；
- **`scripts/e2e.py` P1–P7 全部通过**：预览、发布、队列、恢复、Cloud、分享、Remix；
- 唤醒延迟见 §1；
- 测完删除集群。

K8sProvider 的改动：
- `runtimeClassName` 改为可选；
- 显式指定 sandboxd 的 command/args；
- token 不一致的旧 Pod 会被替换；
- 支持 port-forward 访问（`OA_KUBECTL`）、pool slot（PVC subPath `pool/<slot>`）和 `list_slots`；
- 可配置 CPU/内存的 request/limit 和 imagePullPolicy；
- 移除 NET_RAW。

`deploy/k8s` 的修复：
- 删掉 kustomization 里的全局 `namespace:`。它会把两个 Namespace 对象都改名成 openatoms 而冲突，还会把 sandbox 侧的 NetworkPolicy/Quota/Role 搬进 openatoms；
- 改为在每个对象上显式写命名空间；
- 新增 `openatoms-sandboxes` 命名空间下的 PVC（PVC 是命名空间级资源，沙箱 Pod 原本挂不上 openatoms 里的那个）；
- RBAC 增加 `pods/portforward`；
- ConfigMap 增加预热池、准入上限、JSON 日志和 worker 指标端口；
- Deployment 加上 Prometheus 抓取注解。

### 7.1 gVisor（`OA_KIND_GVISOR=1 scripts/k8s_kind_e2e.sh`）

`scripts/kind_gvisor.sh` 从官方发布桶下载 gVisor（固定 `GVISOR_RELEASE=20261026`，校验 `.sha512`，缓存在 `.tools/gvisor/`；2026 版起发布物是 `gvisor.tar.bz2`，`latest/` 别名已不存在，runsc 还需同包的 `gvisor-bin/`），装进 kind 节点并在 containerd 注册两个 handler：
- `runsc`：netstack（gVisor 自带网络栈），对应生产 RuntimeClass `gvisor`；
- `runsc-hostinet`：`network=host`，系统调用和文件仍经 Sentry/gofer，只是 socket 用 Pod netns 的。

实测结论：
1. **生产路径**（集群内 api/gateway 经 podIP 访问沙箱）：`gvisor` 下的 sandboxd Pod 被另一个 Pod 经 podIP 调用 `/health`、`/exec` 均正常，`uname -r` = `4.19.0-gvisor`，node v22 / pnpm 11 正常；`dmesg` 首行为 `Starting gVisor...`。
2. **`kubectl port-forward` 连不上 netstack 下的 gVisor Pod**：CRI port-forward 在 Pod netns 里拨 127.0.0.1，而 netstack 的监听不在那个 netns（`connect: connection refused`）。所以 api 跑在集群外、用 port-forward 访问时，沙箱改用 `gvisor-hostinet`。生产 api 在集群内，不受影响；`K8sProvider` 文档里已写明。
3. **完整 e2e**：预热池 Pod 和项目沙箱 Pod 全部是 RuntimeClass `gvisor-hostinet`，`/proc/version` 为 gVisor 内核；`scripts/e2e.py` P1–P7 全部通过（预览、发布、队列、恢复、Cloud、分享、Remix）。唤醒（预热池）0.12–0.13 s；新项目首次打开 21–28 s（runc 为 4–5 s），慢在 pnpm install 经 gofer 写 macOS 文件共享。
4. **滥用扫描器**：进程列表与 CPU 统计在 gVisor 下正常；`/proc/net/tcp` 有真实连接状态。SYN_SENT 在本机任何运行时都造不出来（Docker Desktop 的网络代理会替所有出站连接完成握手，runc 对照组同样只有 ESTABLISHED），端口扫描判定只验证了数据来源可用。

由此发现并修复的**生产问题**：沙箱 Pod 以 uid 1000 运行，而 api/worker 镜像以 root 运行、清单也没设 `runAsUser`。在真实 RWX PVC 上，平台写出的工作区（模板复制、恢复、可视化编辑）是 root:0755，沙箱无论 runc 还是 gVisor 都写不进去。之前 kind 能过，只是因为 Docker Desktop 的文件共享不检查权限；gVisor 的 Sentry 会检查，于是暴露出来。修复：
- `deploy/docker/server.Dockerfile` 以 uid 1000 运行（Playwright 浏览器放到 `/ms-playwright`）；
- `deploy/k8s/api.yaml`、`worker.yaml` 加 `securityContext {runAsUser,runAsGroup,fsGroup: 1000}`；
- 沙箱 uid 可配（`OA_K8S_RUN_AS_USER`，默认 1000，测试 `tests/test_k8s_manifest.py`）。kind+gVisor 测试里宿主机 api 写的文件在节点内显示为 root 所有，所以只有这个测试脚本设为 0。
- server 镜像改动没有实际构建验证（含 Playwright 依赖，构建较重）。

## 8. 已知限制与后续

- 嵌套取 DB session 的写法（请求 session 之内再开 SessionLocal）仍存在于 core/agents 代码中，目前靠更大的连接池规避。根治需要把 `has_credits` / `suspended_run` / 配额检查改为复用调用方的 session。
- 内容审核默认是关键词规则。正式上线应接入审核模型（`OA_MODERATION=webhook`）。
- gVisor 已在 kind 上实测（§7.1），但只在 Docker Desktop 的 LinuxKit 内核上跑过；真实节点（GKE Sandbox / 自建 containerd）上的性能和兼容性仍需在目标集群验证。Docker provider 的 `OA_SANDBOX_RUNTIME=runsc` 未实测：需要在 Docker daemon 里注册 runsc，会改动本机全局 Docker 配置，因此没做。
- 浏览器端 Sentry 只验证了初始化代码路径和构建；投递验证是在服务端做的。
- 预热池只在 docker/k8s provider 上默认开启。local provider 也支持（测试中使用），但 local 本身启动就很快。
- `deploy/s3.json` 是旧 SeaweedFS 的凭据文件，已不再被引用；CONTRACT §11 中关于对象存储的描述需要相应更新（S3 已换成 versitygw）。

## 9. 复现

```bash
# 单元/集成测试
cd services/server && uv run pytest -q tests/test_prod_*.py
cd apps/gateway && go test -race ./...
# 压测（建议先 OA_RATE_LIMIT=0 启动栈）
uv run scripts/loadtest.py --api http://127.0.0.1:8100 --users 20
# 冷启动 / 预热池对比
uv run scripts/measure_sandbox_start.py --api http://127.0.0.1:8100 --projects 3
# kind 端到端
./scripts/k8s_kind_e2e.sh            # KEEP=1 保留集群，OA_SANDBOX_WARM_POOL=0 测冷启动
# 第二套 full 基础设施（端口 15432/16379/19000）
docker compose -p oa-prod -f deploy/docker-compose.yml -f deploy/ports.alt.yml up -d postgres redis s3
# 备份 / 恢复
./scripts/backup.sh ./backups && ./scripts/restore.sh ./backups/oa-backup-<ts> --yes
# 可观测性栈
docker compose -f deploy/observability/docker-compose.observability.yml up -d
```
