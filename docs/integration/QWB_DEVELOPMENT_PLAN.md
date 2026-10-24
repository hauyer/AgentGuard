# QWB 集成开发计划（C1–C8）

日期：2026-10-24。分支：`feature/qwb-public-api-integration`。
前置审计：QWB_API_MATRIX.md、QWB_CONTRACT_DIFF.md、QWB_DEPENDENCY_BLOCKERS.md。

## 0. 目标

把 AgentGuard 升级为强网杯应用层可稳定调用的上层业务与安全决策平台：
消息级图谱（C1）、实验汇总（C2）、能力发现（C3）、调查时间线（C4）、
证据与报告合同（C5）、防御状态（C6）、SDK/OpenAPI/应用合同（C7）、
整体验收与 `1.1.0rc1` 候选（C8）。修改边界：仅 AgentGuard 仓库；
QWB App / WARDEN / FRACTAL-Bench / StegoPot 只读。

## 1. 实施顺序与阶段定义

按指令顺序串行推进，每阶段完成后运行全量回归并独立提交：

| 阶段 | 交付 | 新端点/合同 | 主要数据源（全部既有，不建第二套存储） |
| --- | --- | --- | --- |
| C1 | 真实消息级交互图谱 | GET /v1/investigations/{request_id}/interaction-graph | WardenWorkflowStore 中 WARDEN run 载荷的 findings[].messages；其次内存 RunReport.observations（标 synthetic） |
| C2 | 实验索引与汇总 | GET /v1/experiments、GET /v1/experiments/{id}、POST /v1/experiments/compare | WardenWorkflowStore.list()（真实工作流）+ LocalArtifactStore run-report 工件元数据（合成引擎运行）；研究注册表可选 |
| C3 | 能力发现 | GET /v1/system/readiness、GET /v1/investigations/options | warden_status()、WardenKernelClient capabilities/contracts/resources、kernel doctor；短 TTL 缓存带来源与时间戳 |
| C4 | 调查时间线 | GET /v1/investigations/{request_id}/timeline（支持 after_event_id 增量） | warden_workflow_events 持久化事件投影 |
| C5 | 证据与报告合同 | GovernedEvidence 加法能力字段；GET /v1/reports 索引 | EvidenceGateway + LocalArtifactStore 元数据 |
| C6 | 防御状态合同 | DefensePreview 加法能力字段；执行入口语义复核 | DefensePlannerService + warden_defense_candidates/events |
| C7 | SDK 与合同固化 | sdk.py 只读方法 + 只读 App 合同测试 | 上述全部端点 |
| C8 | 验收与候选发布 | 全套 release 文档 + 1.1.0rc1 | — |

每阶段验收报告固定九项：实际功能、文件清单、对外接口变化、真实测试结果、
新增安全防护、剩余底层阻塞、与 QWB App 兼容性、Git commit、下一阶段前置条件。

## 2. 横切实现约定

- **合同**：全部 Pydantic v2（ContractModel 基类），`schema_version="1.0"`，
  放 `src/agentguard/contracts/`；路由经 `api/public_routes.py` 挂载，业务逻辑进
  `services/`，不在 app 工厂里堆实现。
- **分页/错误/鉴权/审计**：完全复用既有中间件（X-Request-ID、422 脱敏、限流、
  api_audit_events 自动覆盖新路径）；新错误码 SCREAMING_SNAKE。
- **不可用语义**：每个新读取端点在无合规数据时返回带 `available=false`/
  `data_status="unavailable"` 的 200 语义体或既有 404/503 模式，绝不静默造数据；
  每个新端点都要有对应的"不可用"合同测试。
- **真值/脱敏**：所有新合同字段经 truth_guard 思路审查（不出现 oracle/label 类键）；
  图谱节点/边的 metadata 只放结构化元数据（role、tool、round_index），不放消息正文。
- **测试**：每阶段新增合同测试进 `tests/`；真实 WARDEN 类测试沿用
  `test_live_warden_http.py` 的"未配置即跳过并明示"模式；既有 257 项测试全程保持绿色。
- **架构审计**：每阶段跑 `scripts/audit_architecture.py --check`，新增模块不得
  引入越界导入或依赖环。

## 3. C1 详细设计要点（先行阶段）

1. 新合同 `contracts/interaction_graph.py`：InteractionGraphResponse(v1.0) +
   GraphNode/GraphEdge/GraphCandidatePath（字段见 QWB_CONTRACT_DIFF §3.1）。
2. 新服务 `services/interaction_graph.py`：
   - 来源 A（真实）：workflow entry → context.run/result.run → evidence.findings[] →
     messages[{sender, recipient, message_id, round_index}] → agent 节点 + message 边；
     证据引用指向该 run 的 evidence id；时间戳取 run created_at/updated_at（消息级
     时间戳缺失时置 null 并记 limitation）。
   - 来源 B（合成，内存）：RunReport.observations 中 event_type ∈ message/tool_call/
     tool_result 的 source/target 对 → 节点/边；source 标 agentguard_synthetic_report。
   - 无 A 且请求方明确真实模式 → available=false + GRAPH_SOURCE_UNAVAILABLE；
   - 去重：同 (source,target,type,message_id) 幂等；时间乱序边保留并记 limitation；
   - 上限：节点/边数量封顶（首版 500/2000，超出截断 + limitation）。
3. 风险字段：只有存在经核验的 finding/risk_score 关联时才给非 unknown 分级；
   关联证据不足一律 `unknown`，不用分数造边。
4. 测试（≥12 项）：真实结构化消息转换、Agent→Agent 边、Agent→Tool 调用、
   Tool→Agent 结果、部分元数据缺失、重复事件、时间乱序、无来源 unavailable、
   合成数据隔离（不得冒充真实）、隐藏真值不泄漏、无权事件过滤、未证实路径
   verification_status≠verified。

## 4. 关键决策记录

- **不新建数据库**：实验索引在查询时聚合 workflow store + artifact 元数据，
  必要时对 artifact list_metadata 做内存排序分页；无第二套运行库。
- **experiment_id 语义**：仅当来源真实携带（RunReport.experiment_id）才返回；
  WARDEN 工作流暂无稳定分组 ID → 条目如实标注 experiment_id=null 的运行存在，
  不虚构分组（指令允许的只读索引降级）。
- **readiness 缓存**：进程内 TTL（默认 30s）+ generated_at/is_stale/来源字段；
  调查提交等关键路径仍走实时预检（lifecycle preflight 不变）。
- **时间线不补写**：只投影 warden_workflow_events 真实行；事件不足的步骤在
  响应 limitations 中说明，不生成假事件。

## 5. 版本策略

当前 `1.0.0rc1`。本轮全部为加法式变更 → C8 验收（三版本矩阵全绿 + 包构建 +
wheel 安装 + OpenAPI/SDK 回归）通过后升级 `1.1.0rc1` 并更新 README/CHANGELOG/
API 文档；不满足验收不升版本。1.0.0rc1 的全部路径与字段语义保持不变。

## 6. Git 交付

每阶段一个或多个提交（本轮不推送远程、不建 PR、不合 main，除非用户明确授权）。
提交主题沿用仓库风格（英文祈使句，如 "Add message-level interaction graph API"）。
