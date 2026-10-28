# WARDEN 研究接口合同（固定版本审计）

**核查日期：** 2026-10-28  
**WARDEN 仓库：** [`Xinyuan20061/WARDEN`](https://github.com/Xinyuan20061/WARDEN)  
**固定源码提交：** [`2e121c5c8da41dc4ccbaa72418ecaa3698c2428e`](https://github.com/Xinyuan20061/WARDEN/commit/2e121c5c8da41dc4ccbaa72418ecaa3698c2428e)  
**WARDEN 包版本：** `0.4.0rc1`  
**OpenAPI：** `3.1.0`，9 paths  
**固定 OpenAPI 文件：** [`warden-openapi-0.4.0rc1.json`](../../tests/fixtures/warden-openapi-0.4.0rc1.json)  
**Fixture 原始 SHA-256：** `e4af2ac3ee880bbef26f4c4c69ce4808340992d7d8cef374dcecd95197fb533a`  
**规范化 JSON SHA-256：** `8fc591e7179a0429dbbd55de32c9e6200f872b62fb6ab57379e7c4769b746015`

以上是固定源码合同，不代表本机或线上服务版本。本轮没有连接真实 WARDEN；Live smoke、远端幂等、重启恢复、模型执行和工件下载均为 **`NOT_RUN`**。

## 公开 REST 路由

合同来自固定提交的 [API 路由](https://github.com/Xinyuan20061/WARDEN/blob/2e121c5c8da41dc4ccbaa72418ecaa3698c2428e/warden/interface/api/app.py)、[HTTP Schema](https://github.com/Xinyuan20061/WARDEN/blob/2e121c5c8da41dc4ccbaa72418ecaa3698c2428e/warden/interface/api/schemas.py)、[默认组合工厂](https://github.com/Xinyuan20061/WARDEN/blob/2e121c5c8da41dc4ccbaa72418ecaa3698c2428e/warden/interface/factory.py)及对应存储/工件适配器。API 路径只列公开 HTTP 边界；AgentGuard 不读取 WARDEN 私有数据库、内部 Python 对象或本地文件。

| Operation | 请求与响应 | 认证与提交语义 | AgentGuard 处理 |
| --- | --- | --- | --- |
| `GET /v1/health` | 返回服务/API 版本和健康状态 | 不证明模型、运行时或执行器就绪 | 仅记录 API 连接与版本 |
| `GET /v1/capabilities` | 返回组件可用性、能力和原因 | 只有明确组件声明才作为能力证据 | 逐组件判断 Runtime、dispatcher、artifact reader；不从健康状态推断 |
| `GET /v1/resources` | 可选 `kind=probe\|sae`；返回资源清单 | `ready` 只证明资源清单状态 | 不将资源存在当作 Probe 训练或干预接口 |
| `GET /v1/contracts` | 返回合同 ID 列表 | 仅证明合同可见 | 预检确切合同 ID |
| `POST /v1/audit-plans` | `AuditRequestSchema`；必需 `request_id`、`contract_id`、`model_id`、`runtime_id`，含 mode、Probe/SAE、capture targets 和 metadata；rc1 另有可选 `research_protocol_ref`、`study_arm_ref` | 两个研究引用必须同时提供。研究请求使用 Bearer token。`request_id` 不是计划 POST 的幂等键；计划 ID 由服务端生成 | 先验证 OpenAPI、授权、token 和研究能力；POST 不自动重试。研究映射未通过预检时不提交 |
| `GET /v1/audit-plans` | 返回当前服务可见计划 | 默认存储不保证跨进程/服务重启可查询 | 只用于已知请求的只读对账；空列表不能证明 POST 未成功 |
| `GET /v1/audit-plans/{plan_id}` | 查询确切 plan ID | 默认组合的可见性受服务内存状态影响 | 身份与请求体不匹配时拒绝对账 |
| `POST /v1/runs` | `RunRequestSchema` 仅含 `plan_id`；返回 run 状态 | 研究 run 使用 Bearer token。公开请求本身没有幂等键 | POST 仅调用一次；超时后通过已有 plan/run ID 只读查询，禁止盲目重提 |
| `GET /v1/runs`、`GET /v1/runs/{run_id}` | 查询可见运行和确切 run ID | 默认组合没有跨重启恢复保证 | 未知状态保持 `unknown` / `reconciliation_required` |
| `GET /v1/artifacts/{artifact_id}/content` | 运行时返回工件原始字节（`application/octet-stream`） | 始终需要 Bearer；要求服务装配了 `artifact_reader`。ArtifactGateway 只允许明确批准的 public-summary 工件并限制读取大小 | 固定 OpenAPI 声明的响应媒体类型是 `application/json`，与运行时二进制响应不一致。AgentGuard 的读取器在不一致消除前 fail-closed，不导入测量结果 |

OpenAPI 中 Artifact 成功响应当前写成 `application/json`，而运行时接口按二进制流返回；这是固定版本中发现的合同差异。客户端不能把 JSON 解析器成功或哈希匹配当成来源真实性证明。

## 幂等、持久化与恢复

- 计划创建 POST 没有公开幂等键或可按 `request_id` 查询的专用路由。重复 POST 可能生成不同 plan ID；AgentGuard 不会自动重试不确定的计划提交。
- WARDEN 源码包含可持久的 SQLite run dispatcher 实现：相同 `request_id` 和相同请求体可重放既有 run；同键不同请求体冲突。该实现只在部署显式注入此 dispatcher 时成立，不能从类存在或 API 健康推导为默认能力。
- SQLite dispatcher 接收提交并排队；执行由 dispatcher 的执行循环处理。进程在运行中崩溃时需运维显式 reconcile interrupted 状态；不自动再次执行。
- 默认服务组合没有提供可验证的 durable-dispatcher 声明，也没有证明默认计划/运行记录可跨重启查询。因此 `GET /v1/runs/{run_id}` 只对已知且仍可见的 ID 有效；404 不等价于“未曾执行”。
- ArtifactGateway 的公共读取策略约束 artifact 类型、授权与字节上限；AgentGuard 仍须验证实际字节 SHA-256，并将工件与已验证的 WARDEN run、Study、Arm 关联后才能考虑可信分析。

## 默认服务组合的能力边界

固定提交的默认工厂不会加载模型，也不注入 `agent.runtime` 或 `activation.backend`。真实执行所需的 Dispatcher、研究阶段白名单映射、Artifact reader 和服务端研究 token verifier 必须由运行部署显式装配。`GET /v1/capabilities` 没有为上述默认部署提供可验证的持久 dispatcher、研究阶段映射或 artifact reader 就绪声明。

固定 OpenAPI 没有独立的 Probe 训练、LEACE、激活/残差 patch、消息 block/replace 或多智能体隔离提交/查询路由。这些能力不能由普通 audit-plan/run 路由、命令行、Python 类或存在的资源推断。

## AgentGuard 能力投影

| Capability | 固定公开合同 | 当前状态 | 具体阻塞 |
| --- | --- | --- | --- |
| `research_dataset_mechanism` | plan/run 与研究引用字段 | `SUPPORTED_BUT_BLOCKED` | AgentGuard research token 未配置；研究阶段白名单未广告；默认 Runtime/dispatcher 未就绪；Live smoke `NOT_RUN` |
| `sender_receiver_baseline` | 无独立 baseline 操作映射 | `UNSUPPORTED` | 无专用合同 |
| `agent_isolation` | 无隔离操作 | `UNSUPPORTED` | 无公开提交/读取合同 |
| `message_block` | 无消息干预操作 | `UNSUPPORTED` | 无公开提交/结果合同 |
| `message_replace` | 无消息替换操作 | `UNSUPPORTED` | 无公开提交/结果合同 |
| `leace_fit_apply` | 无 LEACE 操作 | `UNSUPPORTED` | 无公开计算/参数工件合同 |
| `activation_patch` | 无 activation patch 操作 | `UNSUPPORTED` | 无公开坐标、授权、提交和回滚合同 |
| `residual_edge_patch` | 无 residual-edge 操作 | `UNSUPPORTED` | 无公开边级干预合同 |
| `probe_training` | 仅有资源发现，无训练操作 | `UNSUPPORTED` | 无公开训练与模型工件合同 |
| `persistent_run` | run 查询路由存在；持久 dispatcher 可选 | `SUPPORTED_BUT_BLOCKED` | 默认部署未广告 durable dispatcher；跨重启对账 `NOT_RUN` |
| `artifact_content_read` | 带认证的 GET 路由存在 | `SUPPORTED_BUT_BLOCKED` | 默认未装配 artifact reader；token 缺失；OpenAPI media type 不匹配；字节 hash smoke `NOT_RUN` |

状态语义：`SUPPORTED_AND_READY` 要求兼容合同、明确能力声明、必要配置/资源和针对该能力的验收证据；`SUPPORTED_BUT_BLOCKED` 表示公开操作存在但关键条件缺失；`UNSUPPORTED` 表示兼容合同没有对应操作；`UNAVAILABLE` 表示 URL/服务/必需资源不可用；`UNKNOWN` 表示合同不兼容或无法可靠诊断。当前没有任何研究能力达到 `SUPPORTED_AND_READY`。

## 本地证据和 Live 状态

- AgentGuard 固定测试夹具来自 WARDEN `2e121c5c8da41dc4ccbaa72418ecaa3698c2428e`，不是一次在线服务响应。
- 当前环境未配置 `AGENTGUARD_WARDEN_URL`、`AGENTGUARD_LIVE_WARDEN_URL`、`AGENTGUARD_WARDEN_RESEARCH_TOKEN`、`AGENTGUARD_OPERATOR_API_TOKEN` 或 `AGENTGUARD_API_TOKEN`。
- 研究能力诊断是只读 GET；测试夹具只证明适配器的合同校验和 fail-closed 行为。
- 真实 WARDEN plan/run、模型、Probe、跨重启恢复、真实 artifact 字节读取和因果实验：**`NOT_RUN`**。

