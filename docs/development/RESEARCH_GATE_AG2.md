# AG-2：Study / Arm / Run 研究对象门

日期：2026-10-28。结论：**Study/Arm 预注册与本地统计结构通过；真实 RemoteRun 关联和执行 BLOCKED / NOT_RUN。**

## 复用与实现

- `StudyRegistrationRequest`/`RegisteredStudy` 扩展可选 track、协议、数据/split SHA、案例家族、模型 revision、tokenizer、解码设置、label semantics、metric definitions、Probe 坐标和 Arm 列表。启用 track 时要求完整冻结字段和 baseline + treatment。
- `registration_sha256` 冻结整个 preregistration。注册表仍是既有 `ResearchRegistry` / SQLite；没有新 DB、Job 表或状态机。
- Arm 定义保存 `method_id`、`method_version`、`target_ref`、强度、对照 Arm 及参数引用/hash。它们只是计划值，不触发算子执行。
- 既有 payload 的哈希核验按入库原始 JSON 计算，新增可选字段不会使 rc3 study 失去历史完整性。
- `StudyArmAnalysisInput` 保留 completed/partial/failed/blocked/not_run/unknown。没有结果的 Arm 在报告中保留 null metric 与原因。SDK 只封装公开 REST；MCP 仅提供只读 Study/能力查询。

## 本地证据

| 命令 | 结果 |
|---|---|
| `pytest tests/test_study_analysis.py tests/test_research_registry.py` | 退出码 0；多臂 family 匹配、丢失 Arm、引用路径拒绝、旧 hash 兼容通过 |
| `pytest tests/test_research_api.py` | 退出码 0；Operator/approval 写门、全局门和审计事件通过 |
| `audit_architecture.py`（最终执行见 AG-4） | 新能力未改变 compat/import 边界 |

## Run 阻塞

固定 WARDEN 没有客户端幂等键、进程重启后的查询或研究执行端点。AgentGuard 不允许把调查 `request_id` 冒充 Study/Arm run ID，也不能从 caller 输入任意远端 ID 建真实关联。因此本轮不创建 RemoteRun、不改现有 Investigation/Loop ID 语义、不 POST 研究任务。真实 Study→Arm→RemoteRun 映射为 `BLOCKED / NOT_RUN`；需 WARDEN 合同后通过现有 `WardenLifecycleService`/`WardenWorkflowStore` 加只读关联。
