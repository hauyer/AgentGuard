# WARDEN 接口能力矩阵

**核查日期：** 2026-10-26  
**AgentGuard Git 基线：** `63fe1b5662df93fbf06750ecf09c14ee9e6bd5cc`，初始版本 `1.1.0rc2`；本地未提交候选 `1.1.0rc3`  
**WARDEN 源码基线：** `381fc08170386a2dbd4c2ded3f0f724f871e9636`（官方仓库 `main`，版本 `0.3.0`）  
**在线 OpenAPI 抓取：** `NOT_RUN`。本机 WARDEN URL 未配置，`127.0.0.1:8765/openapi.json`
不可访问。

本表是对固定 WARDEN 源码和契约测试的只读核对，不是运行中服务的 OpenAPI 导出。在线
服务版本、配置、模型与资源仍为 `unknown`，不能用本表解除真实服务或模型验收阻塞。

## 公开 REST 路由

来源：[WARDEN `app.py`](https://github.com/Xinyuan20061/WARDEN/blob/381fc08170386a2dbd4c2ded3f0f724f871e9636/warden/interface/api/app.py)、
[HTTP Schema](https://github.com/Xinyuan20061/WARDEN/blob/381fc08170386a2dbd4c2ded3f0f724f871e9636/warden/interface/api/schemas.py)、
[公开路由契约测试](https://github.com/Xinyuan20061/WARDEN/blob/381fc08170386a2dbd4c2ded3f0f724f871e9636/tests/contract/test_api.py)。FastAPI 源码默认生成 `/openapi.json`；本轮没有运行该服务或从在线服务取文档。

| 方法与路径 | 请求 / 响应结构 | 资源与成功条件 | 能力状态 / 阻塞 | 验收证据 |
| --- | --- | --- | --- | --- |
| `GET /v1/health` | 无请求体；`status`、`version`、`api_version` 字符串 | HTTP 200，且 `status=ok`、`api_version=v1` | 只证明 API 健康，不证明 Runtime、Probe、SAE 或执行器可用 | 源码和 WARDEN API 契约测试 |
| `GET /v1/capabilities` | `CapabilityReport[]`：`component_id`、`available`、`capabilities`、`reason`、`metadata` | 按组件判断；缺失组件保持 unknown/unavailable | 默认组合可明确报告 Runtime/Activation 不可用 | AgentGuard transport/readiness 测试 |
| `GET /v1/resources[?kind=probe\|sae]` | 可选 `kind` 查询；`ResourceManifest[]` | 至少一个 `state=ready` 资源才算该类可用 | 资源清单不提供 Probe 训练或模型执行 API | AgentGuard transport/readiness 测试 |
| `GET /v1/contracts` | 无请求体；`string[]` | 非空合同清单 | 空清单不算合同源可用 | AgentGuard 诊断测试 |
| `POST /v1/audit-plans` | `AuditRequestSchema`：必需 `request_id`、`contract_id`、`model_id`、`runtime_id`；`mode` 仅允许 `capture_only`、`detection`、`causal_audit`、`defense_evaluation`；包含可选 Probe/SAE、`CaptureTargetSchema[]` 和 metadata；响应 `AuditPlan` | 合同及编译器所需资源满足，计划 `status=planned` 才可继续；AgentGuard 的高层 `observation` 请求在边界映射为 WARDEN `capture_only` | `request_id` 不是幂等键；每次 POST 都会创建计划；缺资源时可返回 `blocked` | 固定源码的 HTTP Schema 与 AgentGuard OpenAPI/负向测试 |
| `GET /v1/audit-plans` | 无请求体；`AuditPlan[]` | 列出当前服务进程的计划 | 计划只存进程内存；无重启恢复保证 | WARDEN `WardenService.list_plans()` |
| `GET /v1/audit-plans/{plan_id}` | 无请求体；`AuditPlan`，未知 ID 为 404 | 计划仍在当前进程内存 | 不支持跨重启查询 | WARDEN `WardenService.get_plan()` |
| `POST /v1/runs` | `RunRequestSchema`：仅 `plan_id`；响应 `AuditRun` | 计划可运行且部署层提供 Dispatcher；成功须读取真实终态和结果 | 无远端幂等键；没有 Dispatcher 时返回 `blocked`，不代表执行 | WARDEN Schema 与 AgentGuard 不重试测试 |
| `GET /v1/runs` | 无请求体；`AuditRun[]` | 列出当前进程可见运行 | 运行状态在进程内存中，重启会丢失 | WARDEN `WardenService.list_runs()` |
| `GET /v1/runs/{run_id}` | 无请求体；`AuditRun`，未知 ID 为 404 | 运行仍在当前进程内存 | 重启后 404 不能证明此前未执行 | WARDEN `WardenService.get_run()` 与恢复测试 |

FastAPI 路由没有声明认证依赖。代理层认证没有在此源码合同中验证。AgentGuard 不得将
请求成功或 `health=ok` 作为授权、模型执行或因果验证证据。

## 白盒、干预和工件能力

| 能力 | 源码合同状态 | AgentGuard 报告 | 解除阻塞所需证据 |
| --- | --- | --- | --- |
| 白盒 Probe 工作流 REST | `app.py` 没有白盒任务提交/状态/取消/工件路由；WARDEN 提供 `warden whitebox-preflight` 和 `warden whitebox-run` CLI | `unsupported`；不通过 SSH/远程 CLI 绕过 REST 边界 | 版本化 REST/OpenAPI、请求/响应 Schema、任务状态和验收测试 |
| Probe/SAE 资源 | `/v1/resources` 可枚举资源；资源存在不等于训练/预测接口可用 | 只从真实资源响应报告 ready 清单；无在线响应为 `unknown` | 在线资源清单及对应可执行接口的真实验证 |
| 干预执行 | 当前公开 REST 无干预执行端点 | `unsupported`；Activation 能力不等同干预 API | 公开干预、授权、回滚和效用评估合同及真实验证 |
| 工件访问/来源证明 | `ArtifactRef` 可含 URI/hash/metadata；无公开下载和来源签名路由 | 外部工件仅保留受限引用，完整性/来源不能独立验证时为 `unknown/unverified` | 受控读取接口、内容校验及可验证来源证明 |
| 远端提交幂等 | 两个 POST 均无客户端幂等键合同 | 本地 journal 只防 AgentGuard 盲重试，不声称远端 exactly-once | 远端稳定幂等键和重复请求语义 |
| 重启恢复/取消 | 计划/运行在内存映射中；无公开取消或恢复路由 | 已知 ID 时只查询；不可查询为 `unknown/ambiguous`，不重提 | 持久状态、跨重启查询和取消/恢复合同 |

WARDEN [白盒工作流文档](https://github.com/Xinyuan20061/WARDEN/blob/381fc08170386a2dbd4c2ded3f0f724f871e9636/docs/whitebox-workflow.md)。CLI 可用不代表 AgentGuard 能通过公开 REST 远程调用。

## 当前验收状态

- AgentGuard 对固定公开路径的适配由 `tests/test_warden_lifecycle.py`、
  `tests/test_warden_transport.py`、`tests/test_system_readiness.py` 覆盖；这些是本地合同/故障
  测试，不是 WARDEN 在线集成测试。
- 分支 CI 的 Python 3.11/3.12/3.13 均通过；实时 WARDEN 测试因服务 URL 未配置而跳过。
- 在线 `/openapi.json`、运行中服务版本与模型资源状态为 **NOT_RUN / unknown**。
- 建议继续使用 `feature/i-reliability-and-live-validation`；它与 `main` 共享祖先
  `3bc9584`，无需强制合并。当前 `main` CI 在 Ruff 阶段失败。
