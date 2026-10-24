# QWB API 覆盖矩阵（C0 接口审计）

日期：2026-10-24。审计分支：`feature/qwb-public-api-integration`（基线提交
`7d42809172059b1f38200bd8308c73345efd77dc`，与 AgentGuard-QWB-App
`contracts/agentguard-version.json` 固定的 pin 完全一致）。

只读核对对象：AgentGuard-QWB-App（B0–B8 已完成，React 前端 + FastAPI BFF）。
本文档回答三个问题：每个应用页面通过哪个 AgentGuard API 获取数据、缺少哪些字段、
修改后如何回归。

## 1. 环境与基线核对记录

| 项 | 值 |
| --- | --- |
| Git 基线 | `7d42809` "Record A8 cross-version acceptance"，工作区干净 |
| 新开发分支 | `feature/qwb-public-api-integration`（自上述提交创建） |
| Python | 仓库 `.venv` 3.12.10（系统 3.13.9；CI 矩阵 3.11/3.12/3.13） |
| 包版本 | agentguard `1.0.0rc1`（pyproject），fastapi 0.161.1，pydantic 2.14.1，pytest 9.1.1，ruff 0.17.2 |
| 下层依赖（editable，只读） | warden-kernel 0.2.0、fractal-bench 0.4.0、stegopot 2.1.0（均挂在 `D:\study homework\stego\WARDEN`） |
| 基线测试 | 257 通过 / 0 失败 / 2 跳过（torch 可选的 whitebox 模块、未配置 live WARDEN 的 HTTP 联调），36.6s |
| Ruff / compileall / 架构审计 | 全部通过；81 模块、0 边界违规、0 依赖环，低层导入仅位于 `agentguard.compat` |
| OpenAPI | 68 paths，`openapi-c0-baseline.json`（本目录）；与 QWB App 侧快照 `integration-data/agentguard-openapi-7d42809.json`（68 paths）一致 |

版本策略核对结论：pyproject 为 `1.0.0rc1`；CHANGELOG 以 "Unreleased" + 里程碑条目
管理，无正式 semver 政策条文；兼容承诺是"迁移期保留原路径与字段"（PUBLIC_API.md）。
本轮全部为加法式新端点，不触碰既有字段语义 → 在 C8 验收通过后升级为 `1.1.0rc1`，
不构成破坏性变更（详见 QWB_DEVELOPMENT_PLAN.md §5）。

## 2. QWB App 页面 → AgentGuard API 覆盖矩阵

BFF 方法清单来自 `backend/app/integrations/agentguard_client.py`（13 个方法）；
页面行为来自 `backend/app/services/*` 与 `frontend/src/types/*`（只读核对）。

| 应用页面/能力 | BFF 方法 → AgentGuard 端点 | 现状 | 缺口 |
| --- | --- | --- | --- |
| 系统总览（B2） | `health()` → GET /v1/system/health | 已覆盖 | 无；health 不代表 WARDEN/模型可用（双方已按此语义实现） |
| 系统总览（B2） | `capabilities()` → GET /v1/system/capabilities | 已覆盖 | 该清单是 AgentGuard 本地引擎的 CapabilityManifest，不证明模型运行时可审计（QWB-INT-007） |
| 系统总览（B2） | `kernel_status()` → GET /v1/kernel/status | 已覆盖 | 只有 WARDEN 连接态；无分层 readiness（模型运行时/Probe/SAE/审计执行各层），App 无法准确归因"不可用"的层级 |
| 调查创建（B3） | `list_scenarios()` → GET /v1/scenarios | 已覆盖 | 场景目录来自本地合成引擎；与 WARDEN 威胁契约是两套来源，无 options 端点统一"当前可提交什么" |
| 调查创建（B3） | `create_investigation()` → POST /v1/investigations | 已覆盖 | 缺少"哪些 mode 可提交/仅可计划/不支持"的能力发现（QWB-INT-005），表单只能按能力清单猜测 |
| 调查列表（B3） | `list_investigations()` → GET /v1/investigations | 已覆盖 | 无 |
| 调查详情（B3） | `get_investigation()` → GET /v1/investigations/{request_id} | 已覆盖 | 无 |
| 调查时间线（B3） | 复用 `get_investigation()` 后由 `_derive_timeline()` 推导 | **推导** | App 正在用 status/plan_id/run_id 猜测各步骤状态（investigations_service.py:235-288）；AgentGuard 已持久化 `warden_workflow_events` 却没有公共时间线端点（C4 缺口） |
| 交互图谱 Live（B4） | 无（`graph_service.py:35-40` Live 模式直接 503 GRAPH_API_UNAVAILABLE） | **缺失** | QWB-INT-001：无消息级图谱公共 API（C1 缺口）。现有 GET /v1/runs/{run_id}/graph 只服务本地合成引擎内存报告，键是 run_id 不是 request_id，且不消费 WARDEN 消息元数据 |
| 证据分析（B5） | `get_evidence()` → GET /v1/evidence/investigations/{request_id} | 已覆盖 | C5 增强：证据索引/工件访问能力字段（metadata_available、content_access_supported、integrity_verified、access_restriction）与报告索引 GET /v1/reports |
| 定向防御（B5） | `get_defense_policies()` / `preview_defense_policies()` → GET/POST /v1/defense-policies/{request_id}[/preview] | 已覆盖 | C6 增强：候选审批/提交/执行/验证能力描述统一化（现 DefenseCandidate.execution_supported 恒为 false 是事实，但缺 per-candidate 审批态与效果验证工件可得性的显式字段） |
| 防御执行（B5） | `POST /v1/defense-policies/{request_id}/execute`（BFF 未封装调用） | 兼容入口 | 外部执行器缺失 → 保持 unsupported（QWB-INT-002，底层阻塞，非本轮可解除） |
| 实验对比（B6） | 无（`reports_service.py:62-67` Live 模式 503 EXPERIMENTS_UNAVAILABLE） | **缺失** | QWB-INT-006：无实验列表/汇总 API（C2 缺口）。只有单报告 GET /v1/reports/{run_id} |
| 报告中心（B6） | `get_report()` → GET /v1/reports/{run_id} | 部分覆盖 | 只有按 run_id 单读；无报告索引/分页/筛选（C5 缺口） |
| 经验中心（B6） | `search_verified_experiences()` → GET /v1/experiences | 已覆盖 | 无 |
| 系统设置（B8） | `get_api_audit_events()` → GET /v1/system/audit-events | 已覆盖 | 无 |

## 3. 下游依赖的响应字段与语义合同（已核实）

以下为 QWB App 侧实际消费的字段（来源：`docs/api/AGENTGUARD_API_MATRIX.md` §响应合同、
`frontend/src/types/*.ts`、BFF 服务层代码）。新端点必须沿用同一套约定：

- 分页：`{items, count, offset, limit, next_offset}`；查询参数 `limit`(1–500)/`offset`(≥0)。
- 错误体：`{"detail": {"error": CODE, "message": ...}}`，必带 `X-Request-ID`；
  422 只含 type/loc/通用消息，不回显输入。
- 鉴权：`X-AgentGuard-Token`（通用）、`X-AgentGuard-Operator-Token`（操作员路由）、
  `X-Approval-Token`（审批敏感操作）；未配置 token 时鉴权关闭。
- 证据等级：`synthetic / observational / interventional / causal_validated`；
  verification_status：`verified / unverified / blocked / failed / unsupported`。
- 调查详情字段集：`{request_id, status, workflow_status, revision, statistics,
  created_at, updated_at, plan_id, run_id, result, error_code, error_message}`。
- 前端类型补充期望（`frontend/src/types/graph.ts`）：节点需 `label`、`risk`（字符串分级）、
  `source_id`；边需 `message_id`、`risk`；候选路径需 `label`、`risk`。C1 合同按
  QWB_CONTRACT_DIFF.md §3 的映射吸收这些字段，不逐字复制。
- Live/演示隔离：App 侧 `data_mode ∈ {live, recorded, synthetic, unavailable}`，
  由 BFF 判定；AgentGuard 侧责任是让 Live 模式有真实端点可用，且合成数据自带
  `synthetic` 标记（App 的 `_derive_timeline` 等"诚实降级"逻辑在新端点落地后由
  App 侧自行切换，AgentGuard 不替它决定 data_mode）。

## 4. 回归测试策略

每阶段（C1–C8）在本仓库内完成三层回归，全部命令化：

1. **单元/合同测试**：`python -m pytest -q`（新增端点的合同测试与全部存量 257 项）。
2. **OpenAPI 回归**：`tests/test_openapi_contract.py` 风格的路径/方法/字段断言 +
   与 `openapi-c0-baseline.json` 的 diff 审查（只允许加法）。
3. **真实服务互通**：`uvicorn agentguard.api.app:app` 起服后用
   `agentguard.sdk.AgentGuardClient` 走新端点；真实 WARDEN 类测试仅在
   `AGENTGUARD_WARDEN_URL` 配置时运行，跳过时明确报告（沿用
   `tests/test_live_warden_http.py` 的既有模式）。

QWB App 侧回归（App 仓库自行执行，本仓库只读）：更新其
`contracts/agentguard-version.json` pin → BFF 增加对应客户端方法 → 合同测试。
本仓库 C7 阶段会以只读方式引用其类型定义做兼容性检查。
