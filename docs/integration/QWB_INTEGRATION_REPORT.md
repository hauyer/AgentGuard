# QWB 应用层集成交付报告（C0–C8，1.1.0-rc1）

日期：2026-10-24。分支 `feature/qwb-public-api-integration`
（`7d42809` → 本报告提交）。应用层仓库 AgentGuard-QWB-App 全程只读。

## 1. 目标达成情况

| 应用层缺口 | 解决方式 | 状态 |
| --- | --- | --- |
| QWB-INT-001 消息级图谱 | `GET /v1/investigations/{request_id}/interaction-graph`；真实边仅来自 WARDEN run 元数据；合成显式标注；无来源 available=false | 已解决（Live 图谱数据取决于 WARDEN run 是否携带消息元数据） |
| QWB-INT-006 实验列表/汇总 | `GET /v1/experiments`、`/{experiment_id}`、`POST /compare`；指标缺失为 null；不可比返回原因 | 已解决（真实因果对比仍受 DEP-006 限制，接口不虚构趋势） |
| QWB-INT-007 模型运行时状态 | `GET /v1/system/readiness` 分层报告；health 不推断可执行 | 已解决 |
| QWB-INT-005 能力发现 | `GET /v1/investigations/options`；模式三态能力 + 真实资源；Probe/SAE REST 底层缺口如实标记 | 上层已解决；底层 DEP-001/005 维持 |
| 调查进度靠推导 | `GET /v1/investigations/{request_id}/timeline`（journal 投影 + 增量拉取） | 已解决 |
| QWB-INT-003 证据可见性 | GovernedEvidence 工件能力字段 + `GET /v1/reports` 索引 + 重启可读 | 上层已解决；外部工件下载维持 DEP-004 |
| QWB-INT-002 防御状态 | DefensePreview 能力描述字段；审批≠执行 | 合同已补齐；执行器维持 DEP-007 unsupported |
| QWB-INT-004 取消/恢复 | C4 时间线如实呈现恢复/对账事件 | 语义已固化；远端取消维持 DEP-003 |

## 2. 应用页面接入指引（QWB App 侧后续工作）

- 系统总览：新增 `readiness()` 调用替换 status_service.py 中硬编码的 features 判定。
- 调查创建：新增 `options()` 调用驱动表单（mode 可提交性、契约、Probe/SAE）。
- 调查详情：`timeline()` 替换 `_derive_timeline()` 推导逻辑。
- 交互图谱 Live：`get_investigation_graph(request_id)`；BFF 将 `path_id/risk_level`
  映射到 graph.ts 的 `id/risk`（映射表见 QWB_CONTRACT_DIFF.md §3.1）。
- 实验对比：`list_experiments()/compare()`；`metrics_available=false` 时保持"无数据
  不生成趋势图"的现有行为。
- 报告中心：`list_reports()` 分页索引 + `get_report()` 详情。
- 证据/防御：现有端点不变，新增字段直接可读（向后兼容默认值）。

## 3. 接口与合同

- OpenAPI：68 → 76 paths，**纯加法**（tests/test_qwb_contract_compat.py 固化回归）。
- 新合同 schema_version="1.0"：interaction_graph、experiment_index、system_readiness、
  investigation_timeline；GovernedEvidence/DefensePreview 以默认值加法扩展（0.1 合同
  原地兼容，53 份 JSON Schema 重新导出）。
- SDK：新增 8 个只读方法；既有方法零变化。
- 版本：`1.0.0rc1` → `1.1.0rc1`（pyproject/CHANGELOG/README/API_REFERENCE/成熟度/
  限制文档同步更新；A8 历史报告保留未覆盖）。

## 4. 验收执行摘要

- 全量测试 333/0/2（跳过原因：torch 可选模块、未配置 live WARDEN），Ruff/compileall/
  架构审计/Schema 导出全绿；真实 uvicorn HTTP 集成链路通过；包构建 + 干净 wheel
  安装 + `agentguard --help` 通过。详见 [TEST_REPORT.md](../release/TEST_REPORT.md)。
- **候选后追加**：① 真实 WARDEN 内核 live 验证两轮——plan 级组合 + **完整 7B
  观察组合**（本地 qwen2.5:7b 专用 ollama 实例）：C1 图谱从真实运行载荷投影出
  3 条真实 Agent→Agent 消息边，options 发布内核声明的运行时标识，证据/实验索引/
  时间线全链路真实数据（TEST_REPORT §4a）；真实运行暴露并修复了经验记录的
  file:// URI 缺陷与 options 运行时标识缺失；② 第十阶段概念区分保障测试 8 项
  （TEST_REPORT §2a）。全量为 **342 通过 / 0 失败 / 4 跳过**（收集 346 项；live
  类在未配置 `AGENTGUARD_LIVE_WARDEN_URL` 时跳过并明示，配置后实测通过——分环境
  计数见 TEST_REPORT §0）。
- **应用层自带检查脚本兼容性复验（AgentGuard-QWB-App
  `scripts/live_agentguard_check.py`，只读运行，WARDEN 完整组合环境）**：
  对 `cacd15b` 得 **14 PASS / 0 FAIL / 1 BLOCKED_UPSTREAM / 0 UNSUPPORTED**
  （App 记录的 `907aa19` 基线为 12 PASS / 2 BLOCKED / 1 UNSUPPORTED）。变化：
  defense_policies 由 QWB-INT-008 的 500 转 PASS（C6 修复确认）；
  interaction_graph 与 investigation_timeline 由 UNSUPPORTED（未发布）转 PASS
  （已发布且脚本形状验证通过）；唯一剩余 BLOCKED 为 report_read——脚本硬编码
  演示标识（contract-qwb-001/agent-suite-2.1/runtime-b11）在本内核上被 preflight
  如实拒绝，属 App 侧测试数据选择，采用 C3 options 的真实标识后即可跑通。
- 三版本 CI 矩阵待推送后在 Actions 执行（发布流程最后一步，未获推送授权前不执行）。

## 5. 阶段提交记录

| 阶段 | 提交 | 内容 |
| --- | --- | --- |
| C0 | `dbb8be6` | 接口审计 4 文档 + OpenAPI 基线 |
| C1 | `c5028ac` | 消息级交互图谱 |
| C2 | `907aa19` | 实验索引与对比 |
| C3 | `0e02547` | readiness 与 investigation options |
| — | `b190e33` | C0–C3 阶段验收（含真实 HTTP 集成测试） |
| C4 | `1a6a988` | 调查时间线 |
| C5 | `c077d64` | 证据能力字段 + 报告索引 + 重启重建 |
| C6 | `7ff7229` | 防御能力字段 + 公共 GET 路由缺陷修复 |
| C7 | `043e4f5` | SDK 方法 + QWB 合同兼容测试 + PUBLIC_API 文档 |
| C8 | 本提交 | 1.1.0-rc1 候选：版本、文档、验收报告 |

## 6. 未完成与移交事项

- 真实 WARDEN 执行/模型/因果验证阻塞（DEP-001–007）不在本轮范围，按
  QWB_DEPENDENCY_BLOCKERS.md 登记；解除条件变更时由后续轮次更新 readiness 语义。
- 推送远程、创建远程 PR、合并 main 未获授权，未执行；三版本 CI 矩阵在推送后运行。
- QWB App 仓库的 BFF/前端接入（§2）由应用层团队在其仓库完成；本轮未修改其任何文件。

## 7. 面向 main 的 PR 草案（文本，未创建远程 PR）

> **标题**：QWB application integration: message graph, experiment index, readiness,
> timeline and contract hardening (1.1.0-rc1)
>
> **变更说明**：在 1.0.0-rc1 之上按 C0–C8 完成 AgentGuard-QWB-App 所需的全部上层
> 公共接口：`GET /v1/investigations/{id}/interaction-graph`（消息级图谱，仅真实
> WARDEN 元数据，合成显式标注，无来源即 unavailable）、`GET /v1/experiments(+/compare)`
> （只读实验索引与可比性检查，缺指标为 null）、`GET /v1/system/readiness` 与
> `GET /v1/investigations/options`（分层能力发现，health 不升级为可执行）、
> `GET /v1/investigations/{id}/timeline`（journal 投影时间线）、证据/报告工件能力
> 字段与 `GET /v1/reports` 索引（含服务重启后的报告重建）、防御能力描述字段与
> 公共防御 GET 路由缺陷修复。OpenAPI 68→76 纯加法（回归测试固化）；SDK 新增 8 个
> 只读方法；全量 333 测试 / 0 失败（2 项按预期跳过）+ uvicorn 真实 HTTP 集成；
> 版本 1.1.0rc1，sdist/wheel 构建与干净环境安装验证通过。真实后端/模型/因果验证
> 阻塞维持 DEP-001–007 如实呈现。合并前置：三版本 CI 矩阵全绿。
>
> **评审要点**：docs/integration/（审计 4 文档 + 阶段/集成报告 + OpenAPI 快照）、
> docs/release/TEST_REPORT.md、CHANGELOG 1.1.0-rc1 条目。
