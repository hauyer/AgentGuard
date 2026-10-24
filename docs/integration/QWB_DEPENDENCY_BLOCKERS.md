# QWB 依赖阻塞登记（C0 接口审计）

日期：2026-10-24。基线：AgentGuard `7d42809`；WARDEN 本地检出 `077f540`（0.2.0，
editable 只读）。权威底层阻塞源仍是 `docs/DEPENDENCY_BLOCKERS.md`（DEP-001–007）；
本文档将其映射到 QWB App 的 QWB-INT 编号，并登记本轮（C0–C8）每个阻塞的
处理边界：**AgentGuard 只做上层，不重实现底层能力。**

## 1. QWB-INT ↔ DEP 映射与本轮处理

| QWB-INT | 对应 DEP | 缺失的底层能力 | 本轮 AgentGuard 上层处理 | 解除条件（在 WARDEN 侧） |
| --- | --- | --- | --- | --- |
| QWB-INT-001（消息级图谱） | （无新底层需求） | 无 —— WARDEN 公共 run 响应已提供消息元数据（见 §2） | **C1 可完整实现**：消费工作流日志中 WARDEN run 载荷的 findings[].messages 构造真实消息边 | 无需 WARDEN 变更；若未来提供更丰富事件流，可增量接入 |
| QWB-INT-002（防御执行） | DEP-007 | 已验证的消息边阻断/字段脱敏/工具权限变更执行器；与效用/回滚联动的防御重放 | C6 只完善候选/审批/能力描述合同；execute 保持诚实的 unsupported | WARDEN 公开防御执行与配对重放 REST 接口并经真实验证 |
| QWB-INT-003（工件下载） | DEP-004 | 受控工件下载 + 端到端完整性 API | C5 提供证据索引与能力声明（content_access_supported 等）；外部工件一律 `unsupported`，不打开 file:// | WARDEN 提供可核验的工件下载/校验接口 |
| QWB-INT-004（取消/恢复） | DEP-002、DEP-003 | 计划/运行重启恢复；取消与回滚路由 | C4 时间线如实呈现恢复与对账事件；语义保持"不确定不重提、本地停止≠远端取消" | WARDEN 公开恢复/取消 API |
| QWB-INT-005（Probe/SAE/白盒 REST） | DEP-001、DEP-005 | 默认组合的 Activation Backend、Probe/SAE 权重；REST 等价 Probe 训练/预测/白盒/配对重放 | C3 options 如实报告哪些 mode 可提交/仅可计划/不支持；Probe 兼容端点保持 deprecated | WARDEN 公开相应接口与资源 |
| QWB-INT-006（真实因果验证） | DEP-006 | 历史观察 6/8 完成 2 失败；无经验证因果结果 | C2 索引如实标注观察级与缺失指标（null），绝不生成虚假趋势 | 真实干预实验与验证完成 |
| QWB-INT-007（模型运行时状态） | DEP-001（关联） | 模型运行时无公开查询接口 | **C3 可完整实现**：readiness 分层报告 model_runtime/probe/sae/audit_execution 各自状态；WARDEN health=true 不推断为可执行 | 模型运行时公开状态接口后，readiness 自动升级为 available |

## 2. 真实数据可用性核对（C1/C2 的数据基础）

对 `integration-data/` 既有真实运行记录（只读）核对结果：

- `agentguard-observation-async.sqlite3` 内两条真实 7B 观察工作流
  （`live-observation-7b-async-20261024`、`live-observation-graph-7b-20261024`，
  均 finished）的 WARDEN run 载荷包含：
  `run.evidence.findings[]`，其中 `messages: [{message_id, sender, recipient, round_index}]`
  —— **真实消息级边元数据，来源明确（WARDEN 公共响应、经工作流日志持久化）**。
  另含 `metrics.trace_alignment.message_count`、`artifacts[]`（file:// URI，仅引用不打开）。
- `agentguard-observation.sqlite3` 中 `real-observation-ad7b…` 处于
  `submission_uncertain`（真实的不确定提交记录，C4 时间线/恢复语义的测试素材）。
- `agentguard_workflows.sqlite3` 中 `real-http-stage1-001`（真实 HTTP 阶段验证，finished）。
- `real-model-runs/*/manifest.json` 为 FRACTAL 运行清单（含 plan/config/preflight）。

结论：C1 的真实数据来源优先级第 1 层（WARDEN 公共响应消息边元数据）**在既有真实
运行上成立**；无该元数据的运行将返回 `available=false`（GRAPH_SOURCE_UNAVAILABLE）
或 `partial`，绝不编造 Agent→Agent 边。

## 3. 本轮明确不做的事（边界重申）

- 不实现 Probe 训练器/SAE/Activation Hook/EAP/真实模型运行时/白盒因果干预执行器。
- 不打开 WARDEN 返回的 file URI 或本地路径；不将绝对路径转成下载端点。
- 不把 FRACTAL-Bench 隐藏真值、系统提示词、私有工具参数带进任何公共响应。
- 不因 WARDEN health 通过而宣称模型审计可执行；不把观察性结果升级为因果结论。
- 不为缺指标的实验填默认值（不为 0）；合成数据一律带 synthetic 标记。
