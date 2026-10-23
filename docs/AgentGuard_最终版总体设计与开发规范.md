# AgentGuard 最终版总体设计与开发规范

> **文档类型：** 产品需求文档（PRD）+ 总体架构设计 + 技术开发规范  
> **项目代号：** AgentGuard  
> **建议版本：** Target v1.0  
> **文档状态：** 目标态设计，供后续实现、论文实验与竞赛作品开发使用  
> **核心定位：** 以 FRACTAL-Bench 和 StegoPot 为底层研究基础，构建面向多智能体协同系统的风险检测、主动因果调查、可控干预、定向防御与效果验证平台。

---

## 0. 阅读说明与事实边界

本文描述的是 **AgentGuard 最终应达到的目标形态**，不是对当前代码完成度的陈述。文中出现的模块、接口、指标和工作流，除非明确标注“当前已有”，均属于目标设计或待实现内容。

项目当前已形成的基本构想是：

- **FRACTAL-Bench**：作为基准、场景、实验条件和评价体系的基础。
- **StegoPot**：作为多智能体运行、模型观测与可控干预的研究基础。
- **AgentGuard**：作为上层安全分析、主动调查、定向防御、实验管理和外部服务平台。

参考仓库：

- FRACTAL-Bench：<https://github.com/Xinyuan20061/FRACTAL-Bench>
- StegoPot：<https://github.com/Xinyuan20061/stegopot>

在实际开发前，必须固定两个底层仓库的 commit，并逐项核对它们的真实公开 API、数据格式、依赖与测试状态。**不得根据本设计文档推断底层库已经具备某项能力，也不得在没有真实实验的情况下把目标功能描述为已实现。**

特别需要区分以下三种状态：

1. **软件流程可运行**：接口、编排和错误处理正常。
2. **真实模型实验可运行**：真实模型与底层运行时已接通，实验数据来自实际执行。
3. **科研结论成立**：实验设计、控制组、统计分析和泛化评估支持所声称的结论。

前一项成立，不代表后一项自动成立。Mock 后端的结果只用于开发和接口验证，不得作为论文或竞赛中的实证性能结果。

---

# 1. 项目愿景与核心问题

## 1.1 项目愿景

AgentGuard 不是一个简单的“异常消息分类器”，也不是把多个 Agent 用工作流串起来的普通应用。它应当是一套面向多智能体系统的、可扩展的安全研究与防御基础平台。

系统需要能够：

1. 接收可复现的多智能体测试场景。
2. 对 Agent 消息、工具调用、任务状态和可用的模型内部信号进行统一观测。
3. 识别可疑的信息流、异常协同行为和潜在策略违规。
4. 根据当前证据与剩余实验预算，主动选择下一步调查动作。
5. 通过受控干预和配对重放，验证候选路径是否影响风险结果。
6. 根据归因证据生成最小必要的防护候选方案。
7. 在隔离环境中验证防护效果、正常任务效用和副作用。
8. 保存完整的证据链、实验配置、版本信息和复现材料。
9. 将经过验证的经验用于后续调查策略，但不把未经验证的判断直接固化为事实。
10. 通过稳定的 Python 接口、REST API、CLI 及可选 MCP Server 向外提供能力。

## 1.2 核心研究问题

AgentGuard 的技术主线应围绕一个核心问题展开：

> **面对多智能体系统中的隐蔽或违规协同行为，能否通过预算约束的主动调查与可控因果干预，定位对风险结果具有关键作用的传播环节，并选择风险降低明显、正常任务效用损失较小的定向防御策略？**

这个问题可拆分为五个子问题：

- **RQ1：检测。** 相比规则、传统分类器和单模型判断，联合分析消息、工具与多 Agent 关系能否提高风险检测质量？
- **RQ2：调查。** 主动选择干预位置是否比随机干预、固定顺序或穷举调查更省预算？
- **RQ3：归因。** 观测证据、行为级干预和模型内部干预分别能支持什么强度的结论？
- **RQ4：防御。** 依据因果证据选择局部防护，能否在相同安全水平下减少正常任务损失？
- **RQ5：迁移。** 在一种案例家族中积累的调查经验，能否迁移到未见过的拓扑、载体、任务或模型配置？

## 1.3 产品价值主张

AgentGuard 应当能够回答的不只是“哪里看起来异常”，还包括：

- 风险发生在哪个场景、哪条信息流、哪个 Agent 或工具交互中？
- 当前判断依赖哪些可追溯证据？
- 哪些结论只是相关性或异常提示，哪些已接受干预检验？
- 下一步最值得执行的调查是什么，预计花费多少预算？
- 哪项防御可以切断已验证的风险路径？
- 防御后风险下降了多少，正常任务损失了多少？
- 其他研究者能否在相同版本和配置下复现实验？

## 1.4 非目标

v1.0 不以以下目标为主：

- 直接取代通用 SIEM、EDR、NDR 或完整 SOC 平台。
- 一开始就覆盖所有 LLM、Agent 框架、模型供应商和工具协议。
- 无条件在线微调模型或允许 Agent 修改自身代码。
- 在生产网络中自动执行不可逆的高影响操作。
- 声称仅凭日志即可证明模型内部机制。
- 将所有多 Agent、MCP、强化学习、RAG、知识图谱、数字孪生功能同时塞进首版。
- 将模拟演示结果包装成真实攻击检测率或因果归因性能。

---

# 2. 总体架构

## 2.1 分层架构

AgentGuard 采用“两个底层研究库 + 一个统一适配层 + 一个上层安全平台 + 多种对外接口”的结构。

```text
┌──────────────────────────────────────────────────────────────┐
│                    外部接入与交互层                           │
│ REST API / CLI / Web Console / Python SDK / Optional MCP     │
└────────────────────────────┬─────────────────────────────────┘
                             │
┌────────────────────────────▼─────────────────────────────────┐
│                    AgentGuard 上层平台                        │
│                                                              │
│  实验管理器 ─ 多 Agent 调查协调器 ─ 风险检测引擎               │
│       │                │                  │                  │
│       ├─ 因果调查规划器 ├─ 定向防御规划器 ─┤                  │
│       ├─ 证据与审计库   ├─ 效果验证器       ┤                  │
│       └─ 经验与策略库   └─ 报告生成器       ┘                  │
└────────────────────────────┬─────────────────────────────────┘
                             │
┌────────────────────────────▼─────────────────────────────────┐
│                    统一适配与协议层                            │
│ ScenarioProvider / RuntimeProvider / InterventionProvider    │
│ EvaluatorProvider / ArtifactStore / PolicyEngine              │
│ Schema Validation / Capability Discovery / Version Mapping    │
└───────────────┬─────────────────────────────┬────────────────┘
                │                             │
┌───────────────▼────────────────┐ ┌──────────▼────────────────┐
│ FRACTAL-Bench Adapter          │ │ StegoPot Adapter          │
│ 场景、案例、实验条件、评价接口   │ │ 运行、观测、干预、工件接口 │
└───────────────┬────────────────┘ └──────────┬────────────────┘
                │                             │
┌───────────────▼────────────────┐ ┌──────────▼────────────────┐
│ FRACTAL-Bench                  │ │ StegoPot                  │
│ 基准与实验定义                 │ │ 模型与多 Agent 研究运行时  │
└────────────────────────────────┘ └───────────────────────────┘
```

## 2.2 两个底层库的职责边界

### FRACTAL-Bench：基准与评价基础

在核实仓库真实能力后，优先由它承担或提供以下能力：

- 场景和案例的发现、加载与版本管理。
- 测试条件、任务设置和实验合同。
- 可复现的场景参数、案例家族与数据划分。
- 评价真值或 Oracle 相关信息的受控访问。
- 基准规定的评价逻辑和指标计算。
- 案例、实验结果与数据集版本的关联。

原则上，FRACTAL-Bench 应回答：

- **测什么？**
- **场景条件是什么？**
- **评价依据是什么？**
- **如何公平比较不同方法？**

但其实际 API 与职责必须通过源码确认。如果某项能力目前只是文档规划或尚未完成实验验证，AgentGuard 应在能力清单中标为 `planned` 或 `unverified`，不得假设可直接调用。

### StegoPot：运行时与干预基础

在核实仓库真实能力后，优先由它承担或提供：

- 多 Agent 任务执行或运行时集成。
- 消息、工具调用和任务状态的观测。
- 在适用模型上采集白盒内部信号。
- 执行支持的行为级或模型内部干预。
- 保存运行轨迹、激活或其他研究工件。
- 将干预执行结果返回给上层。

StegoPot 应回答：

- **如何运行？**
- **能够观测什么？**
- **允许实施哪些干预？**
- **运行结果与工件在哪里？**

如果后端不支持白盒访问，必须声明能力缺失，而不是伪造激活数据。若底层框架与不可信插件处于同一进程，也不能把该框架本身宣称为操作系统级安全沙箱。

### AgentGuard：上层安全智能

AgentGuard 不重复实现两个底层库已有的场景构造或模型 Hook 机制。其主要责任是：

- 统一不同底层的接口与数据格式。
- 管理实验运行状态、预算和权限。
- 组合检测、调查、归因、防御与验证算法。
- 协调多个 Agent 的工作。
- 将证据与防御决策连接起来。
- 提供可复现的实验管理、报告和外部调用接口。

## 2.3 依赖规则

必须遵守以下依赖原则：

1. `contracts` 不依赖任何底层仓库。
2. `core` 只依赖 contracts 和 provider protocols，不直接导入 FRACTAL-Bench/StegoPot 私有模块。
3. `adapters` 负责所有底层类型转换。
4. `agents` 通过已注册的工具和服务调用 core，不绕过权限层直接操作底层。
5. `api` 和 `cli` 只调用应用服务，不复制业务逻辑。
6. `evaluation` 与被测策略逻辑分离，防止检测器或 Agent 读取隐藏真值。
7. `mock` 仅用于开发、测试和展示，不参与真实效果结论。

---

# 3. 核心业务工作流

## 3.1 标准闭环

```text
创建实验
   ↓
加载场景与能力声明
   ↓
检查预算、权限、模型能力和实验合同
   ↓
执行基线任务并采集观测
   ↓
生成候选风险与证据引用
   ↓
调查协调器评估不确定性与调查价值
   ↓
选择候选消息边、工具边或模型内部目标
   ↓
执行受控干预与匹配重放
   ↓
独立评价器计算风险与正常任务指标
   ↓
判断证据是否充分
   ├── 否：继续调查（预算允许时）
   └── 是：生成归因报告
              ↓
         生成候选防御策略
              ↓
         在隔离环境中执行防御重放
              ↓
         验证残余风险、效用与副作用
              ↓
         生成审计报告并保存经验
```

## 3.2 实验状态机

建议状态包括：

- `created`
- `validated`
- `queued`
- `running`
- `observing`
- `investigating`
- `intervening`
- `evaluating`
- `completed`
- `partial`
- `failed`
- `cancelled`
- `timed_out`

状态变化必须通过状态机校验，禁止任意模块直接修改状态。每次状态转换记录时间、执行者、输入引用和错误信息。

## 3.3 失败处理

至少要区分：

- `SCENARIO_NOT_FOUND`
- `SCHEMA_VALIDATION_FAILED`
- `CAPABILITY_UNAVAILABLE`
- `BUDGET_EXCEEDED`
- `POLICY_DENIED`
- `TARGET_NOT_FOUND`
- `INTERVENTION_REJECTED`
- `RUNTIME_ERROR`
- `EVALUATION_ERROR`
- `ARTIFACT_MISSING`
- `TIMEOUT`
- `CANCELLED`

失败结果应保留已产生的证据，并标记报告为 `partial` 或 `failed`。不能因中间步骤失败而生成看似完整的成功报告。

---

# 4. 统一接口与数据契约

## 4.1 契约设计原则

- 使用 Pydantic v2 或等价的严格 Schema。
- 每个对象都包含 `schema_version`。
- 默认禁止未声明字段。
- 时间统一使用带时区的 ISO 8601。
- ID 使用稳定、唯一且可追溯的标识。
- 大型张量、模型权重、原始长日志不直接放入 API JSON；使用 Artifact URI、内容哈希和元数据引用。
- 所有外部输入都要验证长度、枚举、范围和权限。
- 任何隐藏真值、答案标签或 Oracle 输出都不得进入 Agent 可见的 Observation。
- 所有干预都必须显式声明目标、操作、参数、预算和授权上下文。
- 适配器返回明确的成功、拒绝、不支持或运行错误，不允许静默降级。
- 对破坏性接口变更升级主版本；兼容新增字段可在次版本中进行。

## 4.2 核心数据对象

### ScenarioSpec

描述 Agent 可见的场景信息，不应包含隐藏评价真值。

建议字段：

```json
{
  "schema_version": "1.0",
  "scenario_id": "scenario-001",
  "name": "Controlled multi-agent information-flow scenario",
  "track": "core",
  "description": "Scenario description visible to the tested system",
  "allowed_targets": ["message", "tool_result"],
  "public_context": {},
  "capabilities_required": ["message_trace"],
  "metadata": {
    "scenario_version": "1.0"
  }
}
```

### ExperimentRequest

```json
{
  "schema_version": "1.0",
  "experiment_id": "exp-unique-id",
  "scenario_id": "scenario-001",
  "goal": "investigate policy-violating information flow",
  "runtime_backend": "stegopot",
  "seed": 7,
  "budget": {
    "max_interventions": 10,
    "max_tool_calls": 40,
    "max_runtime_seconds": 1800,
    "max_model_tokens": 50000
  },
  "policy_profile": "isolated_research",
  "tags": []
}
```

### Observation

```json
{
  "schema_version": "1.0",
  "event_id": "evt-unique-id",
  "experiment_id": "exp-unique-id",
  "timestamp": "2026-10-23T12:00:00Z",
  "actor_id": "agent-a",
  "event_type": "message",
  "source": "agent-a",
  "target": "agent-b",
  "target_ref": "message-17",
  "summary": "Sanitized event summary",
  "features": {
    "authorization": "denied",
    "sensitivity": "high"
  },
  "visibility": "research_only",
  "provenance": "artifact://trace/evt-unique-id"
}
```

Observation 只记录实际运行可观察到的事件和允许访问的特征。不要加入诸如 `is_true_stego`、`oracle_label`、`ground_truth_path` 之类的隐藏标签。

### DetectionFinding

```json
{
  "schema_version": "1.0",
  "finding_id": "finding-001",
  "risk_type": "unauthorized_information_flow",
  "severity": "high",
  "target_type": "message",
  "target_ref": "message-17",
  "score": 0.92,
  "rationale": "Observed policy and trace features meet detector criteria",
  "evidence_refs": ["evt-unique-id"],
  "detector_version": "rules-0.1"
}
```

`score` 是检测器输出分数，不应默认解释为校准后的概率。每个 Finding 必须能回溯到输入证据。

### InterventionRequest

```json
{
  "schema_version": "1.0",
  "experiment_id": "exp-unique-id",
  "intervention_id": "int-001",
  "target_type": "message",
  "target_ref": "message-17",
  "operation": "block",
  "control_group": "matched-control-001",
  "parameters": {
    "scope": "isolated_test_environment"
  },
  "authorization_ref": "approval-or-policy-reference"
}
```

目标类型可以包括 `message`、`tool_result`、`activation` 等，但后端必须通过 Capability Manifest 明确声明支持情况。

操作类型建议分为：

- 行为级：`block`、`replace`、`redact`、`mask`、`restrict`
- 模型内部：`ablate`、`patch`、`scale`、`zero` 等（仅限真实后端支持的操作）

不要把模型内部操作和生产防御操作混为同一个权限等级。

### InterventionResult

必须包含：

- `status`: `completed` / `rejected` / `unsupported` / `runtime_error`
- `changed`
- `effect_status`: `not_measured` / `simulated` / `measured`
- `evidence_refs`
- `error_code`
- `before_artifact_ref`
- `after_artifact_ref`
- `execution_duration`
- `backend_version`
- `metadata`

`completed` 只表示操作执行完成，不代表操作有效，更不代表因果结论成立。

### EvaluationReport

至少包含：

- 检测指标：Precision、Recall、F1、FPR、AUROC（视任务适用）。
- 归因指标：Top-K 命中率、关键边定位率、路径定位准确率、干预预算。
- 防御指标：风险降低幅度、残余风险、正常任务效用、误阻断率。
- 成本指标：运行时间、Token 数、工具调用数、干预次数。
- 泛化指标：不同案例家族、拓扑、模型与任务条件的结果。
- 统计信息：样本量、置信区间、重复次数、统计检验方法。
- `evidence_mode`：`synthetic` / `observed` / `measured`。
- `limitations`：当前实验无法支持的结论。

### EvidenceRecord

每条证据应包含：

- 唯一证据 ID。
- 证据类型与来源。
- 关联的实验、运行、场景、模型和代码版本。
- Artifact URI 与哈希。
- 证据等级。
- 可支持的结论范围。
- 时间戳与访问级别。

证据等级建议：

1. `observational`：观测到某事件或关联。
2. `synthetic`：合成场景或模拟后端生成。
3. `interventional`：真实执行了干预并观察到结果变化。
4. `causal_validated`：通过预先定义的对照、重复、负对照和统计分析标准后，由可信评价流程确认。

MVP 不得自动把单次干预结果标为 `causal_validated`。

## 4.3 Provider Protocols

AgentGuard 的核心代码只依赖抽象协议。

建议至少定义：

```python
class ScenarioProvider(Protocol):
    def list_scenarios(self) -> list[ScenarioSpec]: ...
    def get_scenario(self, scenario_id: str) -> ScenarioSpec: ...

class RuntimeProvider(Protocol):
    def capabilities(self) -> CapabilityManifest: ...
    def execute(self, request: ExperimentRequest) -> RunHandle: ...
    def get_observations(self, run_id: str) -> list[Observation]: ...
    def intervene(self, request: InterventionRequest) -> InterventionResult: ...

class EvaluatorProvider(Protocol):
    def evaluate(self, request: EvaluationRequest) -> EvaluationReport: ...

class ArtifactStore(Protocol):
    def put(self, data: bytes, metadata: ArtifactMetadata) -> ArtifactRef: ...
    def get_metadata(self, artifact_ref: ArtifactRef) -> ArtifactMetadata: ...
```

实际代码应根据异步任务、长时间运行和底层调用特征，选择同步接口或 `async` 接口。不要同时设计两套重复协议；若采用异步接口，应从第一版统一使用。

## 4.4 Capability Manifest

每个后端必须声明能力，而不是通过异常猜测能力。

示例：

```json
{
  "provider_id": "stegopot-adapter",
  "provider_version": "0.1.0",
  "capabilities": {
    "message_trace": true,
    "tool_trace": true,
    "activation_read": false,
    "activation_intervention": false,
    "counterfactual_replay": true,
    "seed_control": true,
    "deterministic_replay": false
  },
  "supported_target_types": ["message", "tool_result"],
  "supported_operations": ["block", "replace"],
  "limitations": [
    "Activation access has not been configured"
  ]
}
```

以上仅为协议示例，真实能力值必须由适配器运行时探测或配置确认。

---

# 5. 多 Agent 协调系统

## 5.1 设计原则

多 Agent 不是为了让系统看起来“更前沿”。只有在角色具有不同职责、不同工具权限、不同证据视图，且协作能够带来可测量收益时，才值得保留多 Agent 结构。

建议先使用可替换的角色与工具协议，不强绑定某一个 Agent 框架。第一版可用确定性工作流和规则规划器运行；后续再接入 LLM 驱动的自主规划。

## 5.2 角色划分

### A. Supervisor / Orchestrator Agent

职责：

- 接收调查目标与实验预算。
- 分解调查任务并选择执行阶段。
- 管理各角色的执行状态与超时。
- 检查预算、权限和终止条件。
- 决定继续调查、生成报告或请求人工审批。

不应：

- 读取隐藏评价真值。
- 绕过 Policy Engine 执行高风险操作。
- 把子 Agent 的未验证结论直接当作事实。

### B. Trace Analyst Agent

职责：

- 整理消息、工具调用和 Agent 关系图。
- 识别可能违反授权策略的信息流。
- 生成候选风险边、候选节点和证据引用。
- 输出可解释的候选路径与不确定性。

输出必须区分事实、推断和假设。

### C. Causal Investigator Agent

职责：

- 读取候选风险路径与现有证据。
- 设计候选干预和匹配对照。
- 估计不同调查动作的信息价值、执行成本和风险。
- 在预算内选择下一次干预。
- 检查结果是否受到混杂因素、运行随机性或场景变化影响。

它负责“调查设计与执行建议”，最终指标仍由独立评价器计算。

### D. Defense Planner Agent

职责：

- 根据已验证或置信度足够的证据提出防御候选。
- 比较消息边阻断、字段脱敏、工具权限收紧等策略。
- 估计风险降低、正常任务损失、实施成本和可回滚性。
- 对高影响操作请求人工审批。

不得仅根据一个未经验证的异常分数直接封禁整个 Agent。

### E. Verifier Agent

职责：

- 检查防御前后是否使用可比的任务条件。
- 运行匹配重放与负对照。
- 验证指标是否来自真实后端。
- 检查是否出现正常任务损失或风险转移。
- 生成验证结论和剩余不确定性。

### F. Memory / Learning Agent（后续阶段）

职责：

- 检索过去相似场景和已验证的策略。
- 提供调查动作的历史成本和成功率。
- 在新案例中推荐调查顺序。
- 记录策略适用范围和失败条件。

记忆中的条目必须带有来源、版本、验证状态和适用边界。未验证的假设不能写入“已确认规则”。

## 5.3 Agent 间通信格式

所有 Agent 间消息使用统一的 `AgentMessage`，至少包含：

- `message_id`
- `run_id`
- `sender_role`
- `recipient_role`
- `message_type`
- `payload_schema_version`
- `payload`
- `evidence_refs`
- `confidence`
- `created_at`
- `deadline`
- `policy_context`

建议的 `message_type`：

- `task_assignment`
- `observation_summary`
- `finding_proposal`
- `intervention_proposal`
- `intervention_result`
- `defense_proposal`
- `verification_result`
- `approval_request`
- `termination`

Agent 间消息不允许随意传递大型原始数据。敏感日志与张量应通过受控 Artifact 引用访问。

## 5.4 何时需要真正的多 Agent

先建立单一编排器与模块化工具，完成稳定闭环，再进行多 Agent 消融：

- 单 Agent + 工具调用。
- 固定工作流、多角色但无动态规划。
- 多 Agent 动态调查。
- 多 Agent + 经验检索。
- 多 Agent + 主动调查策略。

如果多 Agent 不能提高调查成功率、降低预算或改善防御质量，应优先采用更简单的架构。

---

# 6. 风险检测引擎

## 6.1 检测输入

检测引擎可在授权范围内组合以下输入：

- Agent 消息元数据和内容特征。
- 消息发送方、接收方、时间和上下文关系。
- MCP/工具调用的名称、参数摘要、授权结果与返回值元数据。
- 任务状态、决策和最终行为。
- 数据敏感级别与策略标签。
- 模型内部信号（仅当真实后端支持且研究设计允许时）。
- 过去已验证案例的特征摘要。

## 6.2 检测层次

建议实现可比较的多层检测，而不是只使用一个 LLM 评分器。

1. **策略规则层**：权限违规、禁止的信息流、工具参数超出范围。
2. **行为统计层**：异常调用频率、异常路径、消息时序与角色关系。
3. **监督检测层**：使用有标注训练数据的分类器。
4. **语义分析层**：对消息和工具交互做上下文判断。
5. **表征分析层**：在白盒研究环境中使用 Probe、激活或其他内部特征。
6. **关系/路径分析层**：分析跨 Agent 信息传播路径与多轮协同结果。

各层应独立评测，最后再评估融合策略。LLM 评估器本身也可能不稳定，必须记录模型版本、提示模板、温度、输出格式和重复运行结果。

## 6.3 Finding 的质量要求

每个 Finding 必须包含：

- 风险类型与目标引用。
- 证据列表。
- 检测方法和版本。
- 分数含义与校准说明。
- 支持的推断范围。
- 未排除的替代解释。
- 建议的后续调查动作。

检测输出只能作为调查入口，不能直接被描述为“已证明存在隐蔽通信”。

---

# 7. 主动因果调查与归因引擎

这是 AgentGuard 最重要的研究模块之一。

## 7.1 核心思想

当检测器发现候选路径时，系统不应立刻给出确定性归因，而应维护一组候选假设，并根据证据和预算选择下一次最有价值的干预。

候选对象可能包括：

- 某条 Agent 消息。
- 某次工具调用或工具返回字段。
- 某个 Agent 的上下文可见范围。
- 某个模型内部激活或特征。
- 某条信息传播路径上的关键节点或边。

## 7.2 调查假设

每条假设应明确：

- `hypothesis_id`
- `target_ref`
- `expected_effect`
- `supporting_evidence`
- `contradicting_evidence`
- `alternative_explanations`
- `confidence`
- `status`
- `next_test`

假设状态建议包括：

- `proposed`
- `under_test`
- `supported`
- `weakened`
- `rejected`
- `inconclusive`

## 7.3 干预选择

第一阶段可以使用可解释的启发式策略：

- 优先选择高风险且证据充分的候选边。
- 优先选择成本较低、可回滚的干预。
- 优先选择能区分多个竞争假设的动作。
- 对已重复验证且信息价值低的动作降低优先级。
- 不对后端不支持的目标类型发起干预。

第二阶段可研究预算约束的主动调查策略，例如：

\[
a_t^* =
\arg\max_{a \in \mathcal{A}_t}
\frac{\mathbb{E}[\Delta H_t \mid a]}
{\operatorname{Cost}(a)+\lambda \operatorname{Risk}(a)}
\]

其中：

- \(\Delta H_t\) 表示调查动作预计减少的候选假设不确定性。
- `Cost(a)` 表示运行时间、模型调用、干预次数或资源成本。
- `Risk(a)` 表示干预可能造成的副作用或实验污染。
- \(\lambda\) 用于控制成本与风险权衡。

这个公式是研究方向，不意味着系统已具备可靠的熵估计器。第一版可以使用候选排序、启发式分数或上下文多臂老虎机，并通过实验判断是否有收益。

## 7.4 反事实与对照设计

每个关键干预尽可能配套：

- 原始基线运行。
- 干预组运行。
- 匹配控制组。
- 负对照干预。
- 正常任务控制。
- 多随机种子重复。
- 运行环境和模型版本固定。
- 对失败与不确定结果的记录。

必须控制接收端状态、上下文、任务参数和其他可能影响结果的因素。单次运行中移除一条消息后输出发生变化，只能说明该次运行中观察到结果差异；不能自动证明普遍因果机制。

## 7.5 归因输出

归因报告至少包括：

- 候选关键路径和对应节点/边。
- 观测证据与干预证据分开列出。
- 每个干预的处理组、对照组和运行条件。
- 风险结果变化和正常任务效用变化。
- 重复次数与置信区间（适用时）。
- 替代解释和未解决问题。
- 结论等级：观测支持、干预支持、经验证因果结论或证据不足。

AgentGuard 不应把所有问题压缩成一个“因果分数”。归因结果应能够被研究者审查和复现。

---

# 8. 定向防御引擎

## 8.1 防御原则

防御目标不是“封禁越多越安全”，而是根据风险证据选择范围最小、可回滚、可验证的措施。

建议候选动作包括：

### 消息与信息流级

- 阻断指定消息边。
- 对特定字段脱敏或替换。
- 限制特定 Agent 向特定接收方传递某类数据。
- 为敏感字段附加来源和权限标签。

### 工具调用级

- 限制工具白名单。
- 限制参数范围。
- 限制返回字段。
- 对高风险工具调用增加审批。
- 限制工具之间的组合使用。
- 对工具结果进行模式校验和字段级过滤。

### Agent 权限级

- 降低特定 Agent 的可见数据范围。
- 限制 Agent 能调用的工具集合。
- 限制任务权限与数据访问范围。
- 对异常 Agent 实施短时隔离并支持回滚。

### 模型内部研究级

- 对已定位的候选表征做受控干预。
- 比较不同干预强度与任务效用。
- 仅用于研究环境，不能直接视作生产防御机制。

## 8.2 防御优化目标

可以把防御选择定义为约束优化问题：

\[
\pi^* =
\arg\min_{\pi \in \Pi}
\left[
R_{\mathrm{residual}}(\pi)
+ \lambda C_{\mathrm{intervention}}(\pi)
+ \mu C_{\mathrm{utility}}(\pi)
\right]
\]

满足：

\[
U_{\mathrm{benign}}(\pi) \ge U_0 - \varepsilon
\]

其中：

- \(R_{\mathrm{residual}}\)：防御后的残余风险。
- \(C_{\mathrm{intervention}}\)：干预实施、维护与回滚成本。
- \(C_{\mathrm{utility}}\)：对正常任务、工具可用性和延迟的损失。
- \(U_0\)：防御前的正常任务效用。
- \(\varepsilon\)：可接受的效用损失上限。

在数据不足时，不应声称求得全局最优策略。可以先实现候选策略排序、约束过滤和 Pareto 对比。

## 8.3 防御安全闸

所有动作必须经过 Policy Engine：

1. 校验动作是否属于当前环境允许范围。
2. 校验目标是否存在于本次实验。
3. 校验后端能力是否支持该操作。
4. 校验预算与调用次数。
5. 校验动作是否可回滚。
6. 对高影响动作要求人工审批。
7. 记录审批、执行与验证结果。

研究模式默认使用合成或隔离环境。AgentGuard 不得默认获得生产系统的写权限。

## 8.4 防御后验证

每个防御候选必须经过独立验证：

- 风险指标是否下降？
- 正常任务是否仍能完成？
- 是否导致误阻断？
- 是否把风险转移到另一条路径？
- 是否增加显著延迟、成本或失败率？
- 是否在不同随机种子或任务条件下稳定？
- 是否能回滚？

报告应同时显示风险收益和业务代价，不允许只展示风险下降百分比。

---

# 9. 经验库与持续学习

## 9.1 目标

持续学习应从“经验可追溯、策略可评估、跨场景可验证”开始，而不是一开始就在线更新模型参数。

## 9.2 经验条目

每条经验至少包括：

- `experience_id`
- 场景类型和适用条件。
- 风险假设。
- 观测证据摘要。
- 实际执行的调查动作。
- 干预结果与对照条件。
- 防御效果与正常任务效用。
- 运行和模型版本。
- 验证等级。
- 失败条件与已知局限。
- 是否允许用于自动策略推荐。

只有经独立评价的经验才能进入“已验证策略”索引。尚未验证的经验可进入待验证区，但不能直接触发高影响防御。

## 9.3 学习路线

### L0：版本化案例检索

按场景、风险类型、工具路径和干预动作检索历史实验。

### L1：调查动作统计

统计不同调查动作的成功率、平均成本和适用条件。

### L2：预算约束的策略选择

采用启发式、上下文多臂老虎机等方法，选择下一步调查动作。

### L3：跨场景泛化

在训练案例家族积累经验，在独立测试家族评估收益。

### L4：可选强化学习

只有当状态、动作、奖励、模拟环境和评估协议稳定后，才考虑更复杂的强化学习。必须与规则、启发式和 bandit 基线比较，避免为使用 RL 而使用 RL。

## 9.4 防止数据泄漏

- 训练、验证和测试应按案例家族或其他适当的独立单位划分。
- 测试集结果不得回写为测试时策略的已知答案。
- 所有记忆条目记录来源和写入时间。
- 评价真值与经验检索系统保持边界。
- 跨场景结果应明确区分同分布测试与迁移测试。

---

# 10. MCP、REST API、CLI 与 Web 控制台

## 10.1 外部接口策略

外部接口是 AgentGuard 的使用方式，不应与内部核心逻辑绑定。

建议优先级：

1. Python SDK 与内部服务。
2. REST API。
3. CLI。
4. Web 控制台。
5. 可选 MCP Server。

MCP Server 应暴露受控的调查工具，而不是让外部 Agent 直接获得底层运行时的任意执行能力。

## 10.2 REST API 目标

建议接口：

| 方法 | 路径 | 作用 |
|---|---|---|
| `GET` | `/health` | 健康检查 |
| `GET` | `/v1/capabilities` | 查询后端能力 |
| `GET` | `/v1/scenarios` | 查询可用场景 |
| `GET` | `/v1/scenarios/{id}` | 查询场景公开信息 |
| `POST` | `/v1/runs` | 创建实验运行 |
| `GET` | `/v1/runs/{id}` | 查询运行状态与摘要 |
| `POST` | `/v1/runs/{id}/cancel` | 请求取消 |
| `GET` | `/v1/runs/{id}/observations` | 查询授权范围内的观测 |
| `GET` | `/v1/runs/{id}/findings` | 查询检测结果 |
| `GET` | `/v1/runs/{id}/evidence` | 查询证据索引 |
| `POST` | `/v1/runs/{id}/interventions` | 提交受控干预请求 |
| `POST` | `/v1/runs/{id}/defenses/preview` | 生成防御预览 |
| `POST` | `/v1/runs/{id}/defenses/execute` | 在授权隔离环境执行防御 |
| `GET` | `/v1/runs/{id}/report` | 获取完整报告 |
| `GET` | `/v1/artifacts/{id}` | 获取工件元数据或授权下载链接 |

所有长时间任务应支持任务 ID、状态轮询、超时和取消。对于执行写操作的接口，应考虑幂等键和重复请求处理。

## 10.3 MCP 工具目标

建议工具：

- `list_scenarios`
- `get_capabilities`
- `start_experiment`
- `get_run_status`
- `get_observation_summary`
- `get_findings`
- `propose_intervention`
- `run_controlled_intervention`
- `evaluate_defense`
- `get_evidence_report`
- `search_verified_experiences`

安全约束：

- 每个工具声明输入与输出 Schema。
- 工具按角色和环境授权。
- 查询工具与执行工具分离。
- 高影响执行动作需要审批或预授权策略。
- 工具调用必须进入审计日志。
- 禁止工具返回隐藏测试真值。
- 不向不可信 Agent 暴露任意 Python、Shell 或文件系统执行工具。
- 工具结果视为不可信数据，不能将返回文本中的指令当作系统策略。

## 10.4 CLI 目标

建议命令：

```bash
agentguard doctor
agentguard capabilities
agentguard scenarios list
agentguard scenarios show <scenario-id>
agentguard run --config configs/experiment.yaml
agentguard runs status <run-id>
agentguard runs report <run-id>
agentguard artifacts list --run-id <run-id>
agentguard adapters check
agentguard benchmark compare --runs <run-a>,<run-b>
agentguard serve --host 127.0.0.1 --port 8000
```

CLI 必须区分退出码：参数错误、配置错误、后端不支持、运行失败、评价失败和成功。

## 10.5 Web 控制台目标

核心页面：

1. **项目总览**：运行状态、后端能力、近期实验。
2. **场景浏览器**：场景公开信息、条件和适用的实验配置。
3. **运行时间线**：Agent 消息、工具调用、状态变化。
4. **风险图谱**：节点、消息边、工具边和风险标记。
5. **调查工作台**：候选假设、证据、不确定性和剩余预算。
6. **干预对比页**：基线、处理组、控制组、指标变化。
7. **防御策略页**：候选动作、预期收益、成本、审批与回滚。
8. **证据审计页**：工件引用、哈希、版本和证据等级。
9. **实验报告页**：指标、置信区间、限制与复现配置。

可视化不能只显示漂亮的风险路径图；用户必须能查看每条边的来源、证据引用和验证状态。

---

# 11. 评价体系与实验规范

## 11.1 检测指标

按具体任务选择：

- Precision / Recall / F1。
- False Positive Rate。
- AUROC / AUPRC（标签和任务适用时）。
- 每个案例家族的指标。
- 正常场景误报率。
- 检测延迟与调用成本。

不应只报告总体准确率，尤其在类别不平衡时。

## 11.2 归因指标

- 关键节点 Top-K 命中率。
- 关键边 Top-K 命中率。
- 路径定位准确率。
- 归因结果稳定性。
- 每个正确定位所需的干预次数。
- 每个正确定位所需的模型调用、Token 和运行时间。
- 预算耗尽率。
- 不确定结论比例。

如果基准没有可用的可信真值，不应计算或宣称“归因准确率”。

## 11.3 防御指标

- 干预前后风险指标变化。
- 残余风险。
- 正常任务效用保持率。
- 正常任务失败率变化。
- 误阻断率。
- 风险转移率。
- 延迟与资源开销。
- 防御回滚成功率。

风险下降和正常任务效用必须一起报告。

## 11.4 基线与消融

至少比较：

- B0：不防御。
- B1：固定规则检测 + 固定防御。
- B2：传统监督检测 + 固定防御。
- B3：单 Agent LLM 调查。
- B4：多角色固定工作流。
- B5：多 Agent 主动调查。
- B6：主动调查 + 因果证据驱动定向防御。
- B7：B6 + 经验证的经验检索（后续阶段）。

根据研究问题选择必要对比，不要求首版一次完成全部方法。每项实验应事先确定主要指标与比较方式。

## 11.5 统计要求

- 报告样本数和重复次数。
- 固定并记录随机种子，但不要只使用单个种子。
- 适当报告置信区间。
- 在需要时进行配对统计检验。
- 说明缺失值、失败运行和排除标准。
- 记录所有基线的配置与版本。
- 避免在测试集上调参。
- 公开或保存生成结果所需的配置和脚本。

## 11.6 泛化评估

建议至少考察：

- 不同案例家族。
- 不同通信拓扑。
- 不同信息分布或任务目标。
- 不同模型或模型版本（条件允许时）。
- 不同消息/工具载体。
- 不同干预预算。
- 正常任务与风险任务的混合情况。

跨场景迁移结果应单独报告，不要把同分布随机划分结果当作强泛化证据。

---

# 12. 安全、权限与数据治理

## 12.1 权限分层

建议角色：

- `viewer`：查看脱敏报告和状态。
- `researcher`：创建实验、查询观测和提出干预。
- `operator`：在授权隔离环境执行干预。
- `approver`：审批高影响操作。
- `admin`：管理配置、凭证和后端连接。

所有权限检查应在服务端执行，不能只依赖前端隐藏按钮。

## 12.2 环境隔离

- Mock 环境：开发和接口测试。
- Research 环境：固定模型与数据的研究实验。
- Sandbox 环境：受控防御重放和可回滚操作。
- Production 环境：不作为 v1.0 自动操作目标；如未来接入，必须单独设计审批、权限和审计。

不要把 Python 插件的同进程隔离当成安全沙箱。执行不可信代码需要进程、容器或其他操作系统级隔离，并限制网络、文件系统和资源。

## 12.3 数据与凭证

- API Key 从环境变量或密钥管理系统读取，不写入仓库。
- 日志默认不保存不必要的敏感正文。
- 原始数据、激活张量和报告应有访问控制。
- 工件记录哈希、生成时间、版本和保留策略。
- 删除或过期策略应可配置。
- API 不应默认向公网开放。
- 不可信工具输出必须视为数据，而不是指令。
- 任何真实网络或真实系统测试都必须获得授权。

## 12.4 审计

以下动作必须写入不可随意覆盖的审计记录：

- 实验创建、取消和配置变更。
- 后端能力变更。
- 干预请求、审批、执行和结果。
- 防御策略预览与执行。
- 权限变化。
- 工件访问和报告导出。
- 经验库写入、修改与标记为已验证。

---

# 13. 代码仓库与工程组织

建议 AgentGuard 作为独立仓库，不直接复制两个底层仓库源码。

```text
AgentGuard/
├── pyproject.toml
├── README.md
├── LICENSE
├── docs/
│   ├── PRD.md
│   ├── ARCHITECTURE.md
│   ├── INTERFACE_SPEC.md
│   ├── SECURITY_MODEL.md
│   ├── EVALUATION_PLAN.md
│   ├── ADR/
│   └── development/
├── schemas/
│   ├── scenario.schema.json
│   ├── experiment.schema.json
│   ├── observation.schema.json
│   ├── intervention.schema.json
│   ├── evaluation.schema.json
│   └── evidence.schema.json
├── src/agentguard/
│   ├── contracts/
│   ├── protocols/
│   ├── core/
│   │   ├── orchestrator/
│   │   ├── detector/
│   │   ├── investigation/
│   │   ├── causal/
│   │   ├── defense/
│   │   ├── verification/
│   │   └── policy/
│   ├── agents/
│   ├── adapters/
│   │   ├── fractal_bench/
│   │   ├── stego_pot/
│   │   ├── mock/
│   │   └── custom/
│   ├── evaluation/
│   ├── artifacts/
│   ├── memory/
│   ├── api/
│   ├── mcp/
│   ├── cli/
│   └── reporting/
├── configs/
│   ├── defaults.yaml
│   ├── policies/
│   └── experiments/
├── examples/
│   ├── mock_demo/
│   ├── adapter_contract/
│   └── experiment_configs/
├── tests/
│   ├── unit/
│   ├── contract/
│   ├── adapter/
│   ├── integration/
│   ├── security/
│   └── end_to_end/
├── scripts/
│   ├── run_experiment.py
│   ├── compare_runs.py
│   └── validate_artifacts.py
└── outputs/                 # 默认不纳入版本控制
```

## 13.1 依赖管理

- Python 版本与主要依赖必须锁定。
- 核心包不应强制依赖所有模型库。
- `torch`、`transformers` 等放在可选 extra。
- 真实适配器依赖应与核心包隔离。
- 提供锁文件或可复现的安装说明。
- CI 运行格式检查、静态检查、单元测试和契约测试。

## 13.2 配置管理

实验配置必须可序列化、可校验、可保存。禁止依赖隐式全局变量来决定模型、干预强度或评价口径。

配置至少记录：

- scenario ID 和版本。
- provider ID 与版本。
- 模型 ID、版本和参数。
- 随机种子。
- 调查预算。
- 策略版本。
- 评价器版本。
- 代码 commit。
- 运行环境信息。

---

# 14. 与两个底层库的集成策略

## 14.1 先固定版本，再开发适配器

真实集成的第一步不是编写猜测性的 import，而是：

1. 固定 FRACTAL-Bench 的 commit。
2. 固定 StegoPot 的 commit。
3. 检查 README、公开接口、目录和测试。
4. 找到真实场景加载、运行、干预和评价入口。
5. 编写字段映射表。
6. 给每个映射编写测试。
7. 用一个真实场景完成端到端实验。
8. 记录尚未实现的能力和失败条件。

## 14.2 适配器职责

FRACTAL-Bench Adapter：

- 加载场景公开部分。
- 将案例条件映射到 `ScenarioSpec`。
- 将实验配置映射到底层实验配置。
- 通过独立评价路径获得评价结果。
- 严格隔离隐藏真值。
- 返回底层版本、案例版本与评价版本。

StegoPot Adapter：

- 根据请求创建底层运行。
- 将运行事件映射成 `Observation`。
- 声明运行时能力。
- 校验目标与操作是否被底层支持。
- 执行允许的干预并映射结果。
- 将大型工件存储为引用。
- 记录运行异常和部分完成状态。

## 14.3 禁止的集成方式

- 不要在 AgentGuard core 中直接导入底层私有模块。
- 不要为了通过测试而在适配器里伪造真实数据。
- 不要在 Observation 中塞入 benchmark truth。
- 不要把底层运行错误转换成“成功但无发现”。
- 不要把 Mock 输出混入真实实验汇总。
- 不要在未验证兼容性时宣称支持所有底层版本。

---

# 15. 开发路线图

## Phase 0：接口与版本冻结

目标：为并行开发建立可靠契约。

交付：

- 两个底层仓库的 commit 记录。
- 接口与字段规范。
- Capability Manifest。
- 错误码和状态机。
- 数据可见性与真值隔离规范。
- 最小 Mock Provider。
- 契约测试模板。

验收：

- 任意模块可以只依赖协议独立开发。
- 所有协议对象都通过 Schema 校验。
- 隐藏真值字段会被拒绝或在边界处隔离。
- 不支持的能力有明确返回状态。

## Phase 1：最小端到端闭环

目标：先验证架构，不追求复杂智能体。

交付：

- 场景加载。
- 运行与观测。
- 规则检测。
- 一次候选干预。
- 独立评价。
- JSON 报告。
- CLI 和基础 API。
- Mock 环境端到端测试。

验收：

- Demo 可以完整运行。
- 所有模拟结果都明确标记 synthetic。
- 失败运行能保留部分证据。
- 报告中没有虚构真实因果结论。

## Phase 2：真实底层适配

目标：连接真实 FRACTAL-Bench 与 StegoPot。

交付：

- 两个适配器的真实回调或原生实现。
- 能力探测。
- 真实场景端到端运行。
- 真实观测工件。
- 版本和环境记录。
- 底层失败与不支持测试。

验收：

- 至少一个真实场景可运行。
- 至少一种真实干预可执行并被记录。
- 中央真值未暴露给被测 Agent。
- 真实运行和 Mock 运行可清晰区分。

## Phase 3：主动调查与因果证据

目标：形成核心研究贡献。

交付：

- 候选假设管理。
- 调查动作排序。
- 干预预算管理。
- 匹配对照和负对照支持。
- 统计评价脚本。
- 归因报告。
- 固定基线与消融实验。

验收：

- 可以比较主动调查与随机/固定调查策略。
- 可以复现干预组与对照组。
- 报告清楚区分观测证据和干预证据。
- 结论强度与实验设计相匹配。

## Phase 4：定向防御与验证

目标：把归因证据转化为安全收益。

交付：

- 防御策略库。
- 约束与权限检查。
- 防御预览、审批和执行。
- 防御前后匹配重放。
- 正常任务效用评价。
- 风险转移与误阻断检查。

验收：

- 至少比较两类定向防御与一类粗粒度防御基线。
- 同时报告风险下降和正常任务损失。
- 所有执行动作在隔离环境中可复现、可审计。
- 高影响动作不会在无授权时执行。

## Phase 5：多 Agent 自主协调

目标：证明多 Agent 的实际价值。

交付：

- Supervisor。
- Trace Analyst。
- Causal Investigator。
- Defense Planner。
- Verifier。
- Agent 间统一消息协议。
- 工具权限与超时控制。
- 单 Agent、固定工作流、多 Agent 的对照实验。

验收：

- 多 Agent 系统能动态选择调查动作。
- 不同角色使用受限工具和证据视图。
- Agent 数量增加不会绕过预算与权限。
- 对照实验显示多 Agent 是否带来可测量收益。

## Phase 6：经验学习、MCP 与可视化

目标：增强可复用性与竞赛展示效果。

交付：

- 版本化经验库。
- 跨案例检索。
- 主动调查策略学习。
- MCP Server。
- 风险路径与时间线可视化。
- 实验对比页面。
- 一键生成研究报告。

验收：

- 经验条目有来源与验证状态。
- 测试集信息不泄漏进经验库。
- MCP 工具受权限和预算约束。
- 可视化结果可以追溯到实验工件。

---

# 16. 最终验收标准

AgentGuard v1.0 只有满足下列要求，才可称为完整的研究与防御平台。

## 16.1 工程验收

- [ ] 核心协议稳定、版本化且有文档。
- [ ] FRACTAL-Bench 与 StegoPot 适配器经过真实运行验证。
- [ ] Mock 与真实后端完全区分。
- [ ] 有可运行的 CLI、REST API 和 Python 接口。
- [ ] 有统一运行状态、错误码和取消机制。
- [ ] 有自动化测试和持续集成。
- [ ] 有可复现的配置、日志与工件索引。
- [ ] 有权限、审计、预算和能力检查。
- [ ] 文档能让新开发者独立安装、运行和接入适配器。

## 16.2 研究验收

- [ ] 有明确威胁模型与研究假设。
- [ ] 有真实模型行为实验。
- [ ] 有检测基线。
- [ ] 有主动调查基线。
- [ ] 有干预组、控制组和负对照。
- [ ] 有正常任务效用指标。
- [ ] 有消融实验。
- [ ] 有独立案例家族的泛化评估。
- [ ] 有统计不确定性和失败案例分析。
- [ ] 论文中的每个性能数字都能追溯到实验记录。

## 16.3 安全验收

- [ ] 隐藏真值不暴露给被测 Agent。
- [ ] 不支持的干预不会被伪装成成功。
- [ ] 未授权动作会被拒绝。
- [ ] 高影响操作需要审批。
- [ ] 工件访问受到控制。
- [ ] Agent 不可信输出不会覆盖系统策略。
- [ ] 真实测试范围有明确授权。
- [ ] 防御操作有回滚或明确的不可逆风险说明。

---

# 17. 论文与竞赛如何共用平台

AgentGuard 应作为共享技术底座，而不是让论文和每个比赛各自开发一套代码。

## 17.1 论文主线

建议聚焦：

> **预算约束的主动因果调查如何帮助定位多智能体协同风险，并支持保持正常任务效用的定向防御？**

论文需要重点说明：

- 问题定义与威胁模型。
- 基准与案例构建方式。
- 关键变量、处理条件和对照条件。
- 主动调查算法。
- 定向防御策略。
- 检测、归因和防御基线。
- 统计结果与局限。

不要把 AgentGuard 的整个工程架构都写成论文贡献。论文的贡献应落在可以被实验验证的方法或发现上。

## 17.2 强网杯作品赛

重点呈现：

- 实际网络安全问题与威胁模型。
- 多智能体协同风险的检测与调查。
- 因果证据如何改变防御决策。
- 相比一刀切封禁的改进。
- 可运行系统、演示和评测报告。
- 应用边界、部署条件与安全限制。

## 17.3 华为 ICT 及其他创新赛

重点呈现：

- 平台架构与模块化扩展能力。
- 多 Agent 自主调查与工具调用。
- 可视化证据链。
- 统一接口和第三方适配能力。
- 可复现的端到端演示。
- 实际实验数据支持的技术优势。

不同比赛应根据当年正式赛题调整提交形式。共享代码不代表所有赛道的参赛资格、评审规则和成果形式相同。

---

# 18. 演示脚本设计

最终作品的演示应当展示真实闭环，而不是只播放预先生成的动画。

建议演示顺序：

1. 选择一个公开场景并展示实验条件。
2. 运行多 Agent 任务，生成真实轨迹。
3. 检测器标出候选风险路径。
4. 调查协调器展示候选假设、剩余预算和下一步选择理由。
5. 在隔离环境执行一次干预与匹配重放。
6. 展示干预前后指标、正常任务效用和证据引用。
7. 让防御规划器比较至少两种防御方案。
8. 执行已授权的定向防御并再次重放。
9. 展示风险降低、正常任务损失、执行成本和回滚结果。
10. 导出可复现报告。

演示中要明确区分：

- 真正的实时运行结果。
- 为了演示界面而使用的静态样例。
- Mock 合成结果。
- 真实实验结果。

如果真实后端运行失败，应如实显示失败状态，而不是自动切换到合成结果后仍显示“实验成功”。

---

# 19. 当前 MVP 与最终版的差距

当前的 AgentGuard 0.1.0 初版是一个接口优先的起点。它已包含基础合同、Mock 后端、简单规则检测、一次干预流程、评价报告、CLI、FastAPI 与自动化测试，但它不能被视为最终研究平台。

| 能力 | MVP 起点 | 最终版目标 |
|---|---|---|
| 合同模型 | 基础数据对象 | 完整版本治理、能力声明、兼容性测试 |
| FRACTAL-Bench 接入 | 回调式接缝 | 固定版本、真实场景和独立评价器集成 |
| StegoPot 接入 | 回调式接缝 | 真实运行、观测、干预与工件映射 |
| 检测 | 简单规则基线 | 多层检测、校准与系统对比 |
| 调查 | 简单候选排序 | 预算约束的主动调查算法 |
| 因果归因 | 模拟干预流程 | 对照实验、统计分析和可审查归因 |
| 防御 | 单个候选干预 | 多策略约束优化与防御后验证 |
| 多 Agent | 预留扩展 | 有权限分离和对照实验的协同调查 |
| 持续学习 | 尚未实现 | 版本化经验、跨场景策略学习 |
| MCP | 尚未实现 | 受限工具、权限与审计 |
| Web UI | 尚未实现 | 时间线、风险图谱、证据和指标对比 |
| 科研结论 | 无真实结论 | 基于真实实验的可复现结果 |

因此，后续开发应围绕“真实适配—主动调查—防御验证”逐步推进，而不是只扩大代码体量。

---

# 20. 开发过程的强制规则

1. 任何新模块先定义输入、输出、错误和能力要求。
2. 每个真实后端功能都必须有集成测试。
3. 所有研究指标必须说明来源与计算口径。
4. 所有模拟结果必须显式标记。
5. 不允许把失败或不支持伪装成成功。
6. 不能让被测 Agent 访问隐藏真值。
7. 每项新增复杂技术都必须有明确的研究问题或工程价值。
8. 新增多 Agent 角色前，先说明它相对单 Agent 的必要性。
9. 新增强化学习前，先建立规则与启发式基线。
10. 新增在线学习前，先建立版本化经验和防数据泄漏机制。
11. 任何防御动作都必须经过权限和预算检查。
12. 每次实验都要保存可复现配置与代码版本。
13. 论文或竞赛材料中不能将计划中的功能写成已实现功能。
14. 不能把“发现相关性”写成“证明因果机制”。
15. 不能把“成功执行干预”写成“防御有效”，必须有独立评价结果。

---

# 21. 最终定义

AgentGuard 的最终形态应当是：

> **一个以 FRACTAL-Bench 为场景与评价基础、以 StegoPot 为运行与可控干预基础，通过统一协议连接底层研究能力，并在上层实现多 Agent 协调、风险检测、主动因果调查、定向防御、效果验证与经验学习的可扩展安全研究平台。**

它的技术价值不在于把两个库包装成一个统一入口，而在于将底层可复现的实验能力转化为一套可以自主调查、基于证据决策、验证防御效果且支持外部扩展的系统。

最终成功标准不是代码文件数量，也不是 Agent 数量，而是以下闭环是否真实成立：

**场景可复现 → 风险可检测 → 假设可检验 → 干预可控制 → 防御可验证 → 结果可审计 → 经验可迁移。**

只要每个环节都有明确接口、可信数据和可重复实验，AgentGuard 就能同时作为科研平台、网络安全创新作品和后续多智能体安全研究的长期技术底座。


---

# 0.6.0 实现补充：证据门控经验动作排序

本节是对既有总体设计的实现增补，不替代原始设计要求。

## 目标

经验库可以根据已验证案例为后续调查提供动作排序，但历史记录可能存在选择偏差、证据等级差异和场景漂移。因此，AgentGuard 0.6.0 增加证据门控的 Beta-Bernoulli Thompson Sampling，将“检索历史经验”与“基于合格经验进行候选动作排序”区分开。

## 输入门槛

只有同时满足以下条件的记录才进入排序：

- `verification_status=verified`；
- `allow_auto_recommendation=true`；
- `evidence_grade` 为 `interventional` 或 `causal_validated`；
- 场景与风险类型匹配当前请求；
- outcome 为 `success`、`partial` 或 `failure`。

`inconclusive` 不更新后验。合成或观察性记录不能被当成干预奖励。

## 后验更新

对每种动作类型分别使用 Jeffreys 先验：

\[
\theta_a \sim \operatorname{Beta}(0.5,0.5)
\]

每条记录作为一条 outcome 观测。成功令 \(\alpha\) 增加 1，失败令 \(\beta\) 增加 1，部分成功令二者各增加 0.5。系统按固定随机种子从后验抽样，并按抽样值排序。重复次数作为记录元数据报告，但不被直接扩展为独立观测，因为当前经验合同不保存每次重复的单独结果。

## 权限边界

排序结果只返回 `action_type`、后验参数、抽样分数、记录数和证据引用，不生成干预请求，不执行动作，也不提供审批权限。所有后续操作仍必须经过能力清单、策略闸门、预算、隔离环境与必要审批。

## 评价边界

后验均值不是经过校准的真实成功概率，Thompson 抽样值也不是置信度。该模块是一个候选排序基线，不是完整的强化学习系统。需要在前瞻留出数据上与固定规则、按频率排序等基线比较，报告效用、风险、探索成本和上下文漂移，再决定是否用于实际防御工作流。

---

# 0.7.0 架构修订：WARDEN 作为低层因果审计内核（2026-10-23）

本节优先于本文件早期将 Probe 训练、Activation hook 或低层因果审计直接放在 AgentGuard 内部的设计。AgentGuard 的定位调整为上层安全工作流与决策平台；低层审计内核统一由 WARDEN 提供，避免两个仓库重复维护相同的运行时和科学方法。

## 职责划分

- **WARDEN**：能力发现、Probe/SAE 工件目录、审计计划编译、低层审计执行、运行状态、阻塞原因和低层证据引用。
- **StegoPot**：WARDEN 可调用的白盒模型运行时与 Activation/梯度/干预适配能力。
- **FRACTAL-Bench**：威胁场景、因子设计、行为真值与独立评价契约。
- **AgentGuard**：多 Agent 编排、候选风险筛查、策略审批、预算、经验库、动作排序、统计分析、结果报告和对外 REST/CLI/MCP 接口。

AgentGuard 不直接导入 WARDEN 的 infrastructure 层，不访问内核 Activation 文件，也不复制 WARDEN 的 Probe/SAE 资源管理或审计计划执行逻辑。系统通过 `WardenKernelClient` 与 WARDEN `/v1` API 通信。

## 接口要求

1. `AGENTGUARD_WARDEN_URL` 是显式配置项；未配置或不可达时，状态分别为 `not_configured` / `unreachable`，不可将其解释为“未检测到风险”。
2. `GET /v1/kernel/capabilities`、`/resources`、`/contracts` 必须读取 WARDEN 的实际响应，不在 AgentGuard 侧猜测能力。
3. `POST /v1/kernel/audits` 首先创建审计计划；只有 WARDEN 返回 `planned` 状态才可提交运行。`blocked`、`failed` 和未知状态不得伪装为成功。
4. `defense_evaluation` 必须经过 AgentGuard 审批校验。`authorization_ref` 与 `start_run` 属于上层控制字段，不传入 WARDEN 严格审计请求。
5. `planned`、`queued` 或 `completed` 只描述工作流状态；最终因果结论仍需审查干预对照、证据引用、秘密恢复、任务效用与替代解释。

## 重复实现迁移

0.7.0 新增唯一生产内核适配入口 `src/agentguard/adapters/warden.py`，并新增 `/v1/kernel/*` API、`agentguard kernel` CLI、请求 Schema、示例与集成测试。旧版本地 Probe API 在 OpenAPI 中标记为 deprecated；`core/linear_probe.py`、`core/attribution.py` 和 `whitebox/activation.py` 暂时保留用于 0.6.x 兼容与离线方法基线，不再作为生产低层因果审计入口。后续主版本在迁移完调用方后再移除旧接口。

当前测试验证的是接口契约、阻塞状态传播、审批门控和错误处理；只有在 WARDEN 服务、实际威胁契约、模型运行时及 Probe/SAE 工件可用的环境完成端到端运行后，才能宣称真实因果审计链路验证通过。
