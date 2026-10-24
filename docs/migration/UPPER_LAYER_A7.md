# 上层平台重构 A7：公共 API、Python SDK 与受限 MCP

日期：2026-10-24。A7 完成公共接口软件合同和边界验收；不代表新的 WARDEN、真实模型或因果实验已通过。

## 阶段完成报告

### 一、完成内容

- 保留原有 `/v1/kernel/*` 与 `/v1/runs/*` 路由，同时增加 `/v1/system/*`、`/v1/workflows`、`/v1/investigations`、`/v1/evidence`、`/v1/defense-policies` 和 `/v1/reports` 业务路由。调查创建仍通过原 WARDEN 生命周期服务、幂等日志和审批门控；缺少 WARDEN 时明确返回 `WARDEN_NOT_CONFIGURED`。
- 工作流列表增加 `limit/offset/next_offset`。类型化请求继续使用 `WardenAuditRequest`；OpenAPI 测试确认新旧路径及请求合同同时可见。
- 新增只用 Python 标准库的 `AgentGuardClient`，提供健康、能力、内核状态、场景、调查、证据、报告、防御候选、已验证经验、策略和审计页接口。SDK 验证 API origin、拒绝重定向、限制响应大小、不自动重试写请求，并以 `AgentGuardAPIError` 区分 API 状态、错误码和 request ID。
- MCP 改为 SDK/Public API 客户端，不再创建本地引擎、不读写本地经验/工件数据库、不调用 WARDEN 或兼容执行器。它能读状态、场景、调查、治理证据、已验证经验和预览防御候选；调查创建限 observation/capture/detection 且固定 `start_run=false`。MCP 不提供审批或执行防御工具。
- API 提供 `X-Request-ID`、统一安全响应头、每进程直接对端限流、显式 CORS 来源白名单和独立操作员凭证。策略更新/回滚和审计读取使用操作员角色；配置单独操作员 token 后，普通 API token 无法替代。
- API 请求审计事件写入现有 WARDEN 工作流 SQLite，不另建数据库；只记录 request ID、HTTP 方法、路由模板、状态、角色和时间，不保存 query、正文或凭证。先预留事件再分派请求；预留失败时返回 503，不执行 API 操作。进程中断留下 `completed=false` 事件供排查，近期事件滚动保留约 100,000 条。

### 二、代码变化

- 修改 `src/agentguard/api/app.py`：安全中间件、operator token、审计读取、业务路由与工作流分页。
- 新增 `src/agentguard/sdk.py`；重写 `src/agentguard/mcp_server.py`，所有工具经 SDK 调公开服务。
- 修改 `src/agentguard/services/warden_workflow_store.py`：在原 SQLite 增加 API 审计事件和兼容迁移，不建立第二份 workflow store。
- 新增 `docs/PUBLIC_API.md`、本阶段验收报告，以及 `tests/test_public_api_a7.py`、`tests/test_sdk_a7.py`、`tests/test_mcp_boundary_a7.py`；扩展 `tests/test_package_release.py`。
- 更新 README、Security Model、Implementation Status、ChangeLog、依赖图和迁移计划。

### 三、架构变化

- 生产审计边界保持为 AgentGuard 上层服务 → `WardenKernelClient` → WARDEN Public API。新业务路径只转发到现有服务；没有在 AgentGuard 重建低层审计执行器。
- MCP 从本地合成执行器及 CompatPaired 路径迁到 AgentGuard Public API/SDK；`agentguard.mcp_server` 不再依赖 `agentguard.core`、`agentguard.compat`、`agentguard.adapters` 或 `agentguard.memory`。
- 持久化 API 审计与 workflow/defense 事件复用现有 SQLite 文件；增加了 `api_audit_events` 表和加法式迁移。
- 架构扫描结果：78 个模块、500 条导入；低层依赖仍只位于三个既有兼容实现中，`boundary_violations=[]`、`dependency_cycle=[]`。

### 四、测试结果

- A7/工作流定向回归：
  `.\.venv\Scripts\python.exe -m pytest tests\test_public_api_a7.py tests\test_sdk_a7.py tests\test_mcp_boundary_a7.py tests\test_workflow_reliability.py -q -o addopts=''`：21 passed，1 个 Starlette/httpx 弃用警告。
- 全量回归：
  `.\.venv\Scripts\python.exe -m pytest -q -o addopts=''`：245 passed，2 skipped，1 个 Starlette/httpx 弃用警告。
- 两个跳过项的原因分别为：`tests/test_whitebox_activation.py` 因默认 `.venv` 未安装可选 PyTorch；`tests/test_live_warden_http.py` 因未设置 `AGENTGUARD_LIVE_WARDEN_URL`。本阶段没有在未授权/未配置环境下替代执行它们。
- 定向 Ruff：
  `.\.venv\Scripts\ruff.exe check src\agentguard\api\app.py src\agentguard\sdk.py src\agentguard\mcp_server.py src\agentguard\services\warden_workflow_store.py tests\test_public_api_a7.py tests\test_sdk_a7.py tests\test_mcp_boundary_a7.py tests\test_package_release.py`：通过。
- `.\.venv\Scripts\python.exe -m compileall -q src tests scripts`、`git diff --check`：通过。
- `.\.venv\Scripts\python.exe scripts\audit_architecture.py --check --output .agentguard\architecture-a7.json`：通过；无边界违规或依赖环。源码包打包测试检查了 SDK、MCP 与 Public API 文档的收录。

### 五、真实集成状态

| 验证层级 | A7 结果 |
| --- | --- |
| 静态代码与架构边界 | 已通过；78 模块/500 导入，无新底层越界 |
| REST/OpenAPI 合同与应用内替身测试 | 已通过；包含旧路径兼容、分页、权限、审计 fail-closed、限流、CORS 和无 WARDEN 时返回 503 |
| SDK 请求合同 | 已通过注入传输验证 URL/请求头、编码、超时、错误分类及非法 origin 拒绝 |
| MCP 工具边界 | 已通过替身 FastMCP 验证；计划工具不启动运行且没有执行/审批工具 |
| WARDEN 真实 HTTP | 本阶段未运行；live 测试因 WARDEN URL 未配置而跳过 |
| 真实 3B/7B 模型执行 | 本阶段未运行；接口阶段合同验证不代表模型执行 |
| 真实因果干预/防御效果 | 未验证；无实验结论 |

### 六、已知问题

- 若没有配置 API token，服务保留本地开发默认行为；对外暴露前必须配置 API 鉴权和网络隔离。若未配置独立 `AGENTGUARD_OPERATOR_API_TOKEN`，为兼容旧部署，`AGENTGUARD_API_TOKEN` 仍可用于操作员操作。
- 限流是进程内存状态，重启后清空，不在多 Worker 间共享；多进程/外网部署应在可信反向代理做共享限流。AgentGuard 不信任转发 IP 头。
- 审计 SQLite 无法预留事件时请求 fail closed；预留后进程异常会留下未完成事件。完成状态写回失败会记录服务器日志并保留未完成标志。
- WARDEN 的真实运行资源/模型执行、外部证据完整性读取、外部防御执行器和真实 3B/7B/因果验证仍是独立集成阻塞，不由 A7 合同测试解决。
- A7 没有升级包版本；候选版与 `1.0.0` 版本决策留待 A8 验收。

### 七、Git 信息

- 分支：`feature/agentguard-upper-layer-v1`
- A7 公共 API/SDK/MCP 实现：`68db0e7`（`Add governed public API, SDK, and MCP boundary`）
- 审计预留 fail-closed 加固：`32e0529`（`Fail closed when API audit reservation is unavailable`）
- 本报告及状态索引随后作为 A7 文档验收提交；本地提交，不推送。

### 八、下一阶段准备

A7 软件接口和合同验收已完成，可以进入 A8。A8 需核对固定依赖与兼容范围、收敛全仓 Ruff 历史问题、进行包构建/安装验证、整理部署与候选发布报告，并在可用时记录真实 WARDEN/模型集成结果。真实外部能力未配置时必须继续列为阻塞，不得以替身测试替代。A8 完成前不创建独立强网杯应用仓库。
