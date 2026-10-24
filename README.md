# AgentGuard

> **当前开发边界（2026-10-24）：** 在 `feature/agentguard-upper-layer-v1`
> 完成上层平台 A0–A8；WARDEN、FRACTAL-Bench、StegoPot 只读。
> A0–A7 已完成；A8 正在进行候选验收，跨版本 CI 和真实 WARDEN 烟测状态单独记录，见[A0 报告](docs/migration/UPPER_LAYER_A0.md)、
> [A1 报告](docs/migration/UPPER_LAYER_A1.md)、
> [A2 报告](docs/migration/UPPER_LAYER_A2.md)、
> [A3 兼容隔离](docs/migration/UPPER_LAYER_A3.md)、
> [A4 调查编排](docs/migration/UPPER_LAYER_A4.md)、
> [A5 证据治理](docs/migration/UPPER_LAYER_A5.md)、
> [A6 经验门控](docs/migration/UPPER_LAYER_A6.md)、
> [A7 公共 API/SDK/MCP](docs/migration/UPPER_LAYER_A7.md)、
> [模块依赖图](docs/migration/DEPENDENCY_GRAPH.md)和
> [依赖阻塞](docs/DEPENDENCY_BLOCKERS.md)。通过候选验收后才建立独立
> `AgentGuard-QWB-App`，比赛应用只调用 AgentGuard 公共接口。

A8 验收材料：[测试报告](docs/release/A8_TEST_REPORT.md)、[成熟度矩阵](docs/release/FEATURE_MATURITY.md)、
[架构图](docs/release/ARCHITECTURE.md)、[API 参考](docs/release/API_REFERENCE.md)、
[部署指南](docs/release/DEPLOYMENT.md)和[已知限制](docs/release/KNOWN_LIMITATIONS.md)。

> **1.0.0 迁移进度：** 阶段 0 审计和阶段 1 HTTP 联调已完成；额外打通了 WARDEN → StegoPot → 本地 7B 模型的观察性运行，并由 AgentGuard 接收证据。该运行没有因果干预，8 个实验条件中 2 个失败。阶段 2 的重复底层代码尚未删除。查看[真实模型联调记录](docs/migration/OBSERVATION_RUN.md)、[阶段 1 实测报告](docs/migration/STAGE1_REPORT.md)、[重复能力与调用点](docs/migration/DUPLICATION_MAP.md)和[迁移顺序](docs/migration/MIGRATION_PLAN.md)。

阶段 2 已关闭通过 `create_app(engine=...)` 注入真实模型运行时和直接配对执行器的路径；真实审计请使用 `/v1/kernel/workflows`。旧 Probe、白盒和 StegoPot Python 接口仍只供离线兼容研究，迁移和删除条件见[阶段 2 边界报告](docs/migration/STAGE2_BOUNDARY.md)。

**AgentGuard 1.0.0-rc1** 是 AgentGuard 1.0.0 候选版，面向多 Agent 信息流风险研究，提供以上层治理为主的调查编排、WARDEN 审计代理、策略审批、证据整理和稳定公共 API。WARDEN 是生产审计的唯一底层内核；本地 Mock 只用于合成软件测试。

> **重要边界：** 默认运行使用 `MockBackend`，只用于软件与接口验证。历史实测已完成本地 7B 观察运行，但完整条件组有失败，尚未验证真实因果干预或防御效果。任何合成指标都不能作为真实模型行为、真实防御效果或因果结论。

## 0.5.0 本轮主要开发

- **统一合同**：严格 Pydantic 模型、嵌套预算、隐藏真值字段校验、48 份 JSON Schema。
- **主动调查**：从 Finding 生成可证伪假设；按显式能力清单生成和排序候选动作；生成含处理组、匹配对照、负对照和种子的配对实验设计。
- **多层检测**：规则、交互图、行为 fan-out 检测；提供可选结构化语义评分回调（默认关闭），并输出有证据引用的候选路径。
- **监督探针**：二元逻辑回归线性探针、AUROC/AUPRC/FPR/Brier 等指标、按案例家族隔离的训练/测试切分与模型工件预测接口。
- **统计工具**：配对差值、案例家族级 Bootstrap 区间、家族级 sign-flip permutation 检验，以及 Holm / Benjamini–Hochberg 多重比较校正。
- **预注册研究工作流**：注册方案不可原位覆盖；分析只接受预注册的主/次终点，输出注册哈希、输入数组哈希、效应区间、校正后 p 值与偏离记录。
- **可选 LLM 调查建议**：通过 OpenAI-compatible API（默认 DeepSeek）为现有 Finding 排序；模型无工具权限，不能授权或执行干预；无效输出或调用失败时回退到确定性策略。
- **秘密路径归因分析**：新增候选节点的 Secret Recovery / Collusion Success 中介贡献与正常任务效用变化分析，以及基于归一化贡献向量的 Path Shift（Total Variation、Cosine Distance、Top-K Jaccard、Spearman）比较。输入数据仍需由有效的真实干预流程产生。
- **本地白盒激活干预**：新增 PyTorch hook 适配器，支持采集激活、按 token 位置置零、参考激活替换和加噪，并记录输入/输出哈希。该模块接受调用者已加载的模型，不负责下载权重或自动证明因果作用；使用方法见 [`docs/WHITEBOX_INSTRUMENTATION.md`](docs/WHITEBOX_INSTRUMENTATION.md)。方法与限制见 [`docs/STATISTICAL_METHODS.md`](docs/STATISTICAL_METHODS.md) 和 [`docs/PROBE_AND_DETECTION.md`](docs/PROBE_AND_DETECTION.md)。
- **预注册研究工作流**：将主要假设、主要/次要终点、匹配变量、样本量、停止/缺失规则和统计方法写入不可原位更新的注册表；分析只能确认性使用已登记终点，并输出偏离说明与分析工件。
- **可选 LLM 调查建议**：通过 OpenAI-compatible API（默认 DeepSeek）为既有 Finding 排序；模型无工具权限，不能授权或执行干预；无效输出或调用失败时回退到确定性策略。
- **定向防御**：候选预览与执行分离，状态明确记录提议、审批、提交、执行和待验证；候选排序因素是可审查的启发式。caller-supplied 配对数组的统计通过不等于防御已验证。
- **证据与工件**：证据 Gateway 检查 WARDEN run/plan 身份，区分合成、观察、干预和因果验证等级；对本地内容寻址工件核验 SHA-256，忽略 WARDEN 本地文件 URI且不读取它们。外部/opaque refs 默认未验证。
- **经验库**：SQLite 版本化经验存储、搜索和动作统计；未验证经验不能开启自动推荐。
- **证据门控 Thompson Sampling**：按场景/风险过滤，只用已验证且明确允许推荐的干预记录排序候选动作；仅提供建议，不执行动作。
- **外部接口**：扩展 REST API、后台运行与协作式取消、CLI、最小 Web 控制台，以及可选 MCP Server。
- **审计和权限**：可选 API token；真实后端写操作要求操作员审批 token 和授权引用。

逐项验收见 [`docs/IMPLEMENTATION_STATUS.md`](docs/IMPLEMENTATION_STATUS.md)，当前执行顺序以 [A0–A8 迁移计划](docs/migration/MIGRATION_PLAN.md)为准；旧总体设计保留为历史目标参考。

## WARDEN 内核集成

AgentGuard 将 **WARDEN** 作为低层因果审计内核的唯一权威来源：Probe/SAE 资源清单、审计计划编译、低层因果审计执行和内核证据由 WARDEN 负责；AgentGuard 保留上层策略、审批、经验检索、策略推荐、结果汇总和用户接口。AgentGuard 不会把本地模拟分数或缺失的运行资源包装成 WARDEN 结果。

启动 WARDEN（参见 [WARDEN 官方仓库](https://github.com/Xinyuan20061/WARDEN)）后，在 AgentGuard 环境中设置：

```bash
AGENTGUARD_WARDEN_URL=http://127.0.0.1:8765
AGENTGUARD_WARDEN_TIMEOUT=15
AGENTGUARD_WARDEN_READ_RETRIES=0
```

内核连接拒绝 URL 中的凭证、查询参数、片段、相对路径和 HTTP 重定向。
`AGENTGUARD_WARDEN_READ_RETRIES` 可设为 0–3，只重试 GET 的临时网络错误或
408/429/502/503/504；提交类请求始终只发送一次。单次连接/读取使用上述超时，
单个 JSON 响应最多 2 MiB。写请求使用已核对的 API v1；每次工作流预检刷新健康状态。

`kernel submit`、`kernel run` 和两个 API 提交入口共用同一 SQLite 日志。
同一请求的并发处理受本地跨进程锁保护；返回的 `workflow_status` 表示业务
状态，旧 `status` 字段保持兼容。已知运行可在网络恢复后继续查询。
`POST /v1/kernel/workflows/{request_id}/cancel` 只取消尚未提交的本地任务；
外部运行取消不受支持时明确返回 unsupported。

新增接口：

- `GET /v1/kernel/status`：查看未配置、可用或不可达状态。
- `GET /v1/kernel/capabilities`：读取 WARDEN 能力报告。
- `GET /v1/kernel/resources?kind=probe|sae`：读取权威资源清单。
- `GET /v1/kernel/contracts`：读取可用威胁契约。
- `POST /v1/kernel/audits`：创建计划并可选提交运行；阻塞计划保留为 `blocked`。
- `POST /v1/kernel/workflows`：执行能力/资源/契约预检、提交计划、可选有界轮询，并在证据引用充分时写入未验证经验记录；`defense_evaluation` 需要有效审批。
- `GET /v1/kernel/audits/{plan_id}`、`GET /v1/kernel/runs/{run_id}`：查询内核状态与证据引用。
- `GET /v1/kernel/workflows/{request_id}/evidence`：返回有来源、运行状态、等级和限制的治理后摘要；不返回原始 EvidenceBundle，也不打开 WARDEN 工件 URI。
- `GET/POST /v1/kernel/workflows/{request_id}/defenses/preview`：从 completed WARDEN 证据中的安全目标引用生成有解释的上层策略候选，并保存在同一 SQLite 工作流库。
- `POST /v1/kernel/workflows/{request_id}/defenses/execute`：记录审批状态；当前没有可验证的外部策略执行器，因此经审批后明确返回 `unsupported`，不会提交或执行防御。

CLI 示例：

```bash
agentguard kernel status
agentguard kernel capabilities
agentguard kernel resources --kind probe
agentguard kernel contracts
agentguard kernel submit --config examples/warden_audit.example.json --plan-only
# 完整生命周期；需要等待时显式启用有界轮询
agentguard kernel run --config examples/warden_audit.example.json --wait --timeout 60
```

示例中的 `contract_id`、`model_id`、`runtime_id` 和资源 ID 必须替换成当前 WARDEN 部署实际提供的值。创建计划不代表实验已执行；`planned`、`queued`、`blocked` 均不能当作因果结论。

### 内核边界与重复实现治理

| 能力 | 权威归属 | AgentGuard 的职责 |
| --- | --- | --- |
| Activation/梯度采集与模型运行时 | StegoPot 及 WARDEN 配置的 Runtime Adapter | 只通过内核接口请求和引用证据 |
| Probe/SAE 资源发现、审计计划、底层因果审计 | WARDEN | 不维护第二套生产内核；处理任务状态与证据门控 |
| 场景、跨 Agent 交互图、上层风险筛查 | AgentGuard | 形成候选假设，不单独宣称因果成立 |
| 审批、预算、干预授权与经验库 | AgentGuard | 负责策略治理与可追溯决策 |
| 统计汇总、研究注册与路径比较报告 | AgentGuard 研究层 | 消费标注数据与 WARDEN 证据，不替代底层实验执行 |

旧版本地 Probe API 已在 OpenAPI 中标记为 deprecated；本地 Probe、统计归因分析和 `WhiteBoxActivationAdapter` 仍保留为兼容性/离线基线工具，避免破坏 0.6.x 研究脚本。它们不是 WARDEN 内核的替代品，不应被当作生产因果审计入口。更多设计与迁移说明见 [`docs/WARDEN_INTEGRATION.md`](docs/WARDEN_INTEGRATION.md)。

## 1. 安装

要求 Python 3.11 或更高版本。

```bash
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
python -m pip install -e ".[dev]"
```

可选依赖：

```bash
python -m pip install -e ".[mcp]"       # MCP server
python -m pip install -e ".[whitebox]"  # PyTorch / Transformers white-box research dependencies
```

白盒激活采集、干预边界和模型路径适配说明见 [`docs/WHITEBOX_INSTRUMENTATION.md`](docs/WHITEBOX_INSTRUMENTATION.md)。

## 2. 运行实验

```bash
agentguard demo
agentguard demo --no-auto-intervene
agentguard demo --output outputs/demo-report.json
```

`--no-auto-intervene` 会生成候选干预和防御预览，但不自动执行干预，适合演示审批前工作流。

通过配置文件运行：

```bash
agentguard run --config examples/experiment.json --no-auto-intervene
```

配置文件可以是 JSON；YAML 需要安装 PyYAML。请求支持平铺预算字段，也支持设计文档中的嵌套格式：

```json
{
  "scenario_id": "demo_cloud_security",
  "runtime_backend": "mock",
  "auto_intervene": false,
  "budget": {
    "max_interventions": 2,
    "max_runtime_seconds": 120,
    "max_model_calls": 64,
    "max_model_tokens": 50000
  }
}
```

每次 CLI 运行会保存 JSON 报告到 `.agentguard/runs/`（或 `AGENTGUARD_DATA_DIR` 指定目录）。

### 预注册研究与家族级统计

```bash
agentguard study register --config examples/study_registration.example.json
agentguard study list
agentguard study integrity --study-id STUDY_ID
agentguard study analyze --study-id STUDY_ID --data examples/study_analysis.example.json
agentguard study analyses --study-id STUDY_ID
```

示例分析数据是合成数据，仅用于验证工作流。完整说明见 [`docs/RESEARCH_WORKFLOW.md`](docs/RESEARCH_WORKFLOW.md)。

### 秘密路径归因与迁移指标

```bash
agentguard attribution mediation --config examples/attribution_node.example.json
agentguard attribution path-shift --config examples/path_shift.example.json
```

REST 接口为 `POST /v1/attribution/nodes/analyze` 和 `POST /v1/attribution/path-shift`。示例数据均标记为 synthetic。完整限制与解释见 [`docs/RESEARCH_WORKFLOW.md`](docs/RESEARCH_WORKFLOW.md)。

### 可选 LLM 调查排序

默认不调用外部模型。配置 `DEEPSEEK_API_KEY` 后，可以显式运行 `agentguard demo --llm-investigator` 或 `agentguard run --llm-investigator --config ...`。先运行 `agentguard agents status` 检查配置；该命令不会访问网络。模型只负责从现有候选中选择优先调查目标，策略闸门仍由本地代码执行。

## 3. REST API 与 Web 控制台

```bash
agentguard serve --host 127.0.0.1 --port 8000
```

打开 `http://127.0.0.1:8000/console` 查看轻量控制台。API 默认使用 Mock 后端。

主要接口：

- `GET /health`, `/console`, `/v1/capabilities`
- `GET /v1/scenarios`, `/v1/scenarios/{scenario_id}`
- `POST /v1/runs`，支持 `?background=true`；`GET /v1/runs` 和 `GET /v1/runs/{run_id}`
- `POST /v1/runs/{run_id}/cancel`
- `GET /observations`, `/findings`, `/hypotheses`, `/investigation`, `/timeline`, `/evidence`, `/report`；`/evidence` 使用治理后的合同视图。
- `GET /v1/runs/{run_id}/graph` 获取交互图和候选路径。
- `GET/POST /v1/runs/{run_id}/experiments/paired` 查看或执行匹配的对照/处理/负对照实验；默认 Mock 可实际跑通合成闭环，真实后端必须注入经过验证的 paired executor。
- `POST /v1/probes/train` 训练家族级隔离的二元线性探针、`POST /v1/probes/predict` 使用已保存模型预测并保存模型工件；示例输入见 `examples/probe_training_request.example.json`。
- `POST /interventions`
- `GET/POST /defenses/preview`、`POST /defenses/execute`、`POST /defenses/verify`
- `GET /v1/artifacts`、`GET /v1/artifacts/{id}`、`GET /v1/artifacts/{id}/content`
- `POST /v1/experiences`、`GET /v1/experiences`、`GET /v1/experiences/recommendations`、`GET /v1/experiences/action-statistics`、`GET /v1/experiences/bandit-recommendations`
- `POST/GET /v1/studies`，`GET /v1/studies/{id}/integrity`，`POST/GET /v1/studies/{id}/analyses`：预注册研究方案、校验注册哈希并分析配对终点。
- `POST /v1/attribution/nodes/analyze` 和 `POST /v1/attribution/path-shift`：计算候选节点的秘密恢复中介贡献和优化前后路径贡献分布差异。
- `GET /v1/agents/status`：只检查可选 LLM 调查配置，不发起外部请求。

长时间任务可以后台运行。后台 `POST /v1/runs?background=true` 返回 `job_id`，轮询 `GET /v1/runs/{job_id}` 获取状态；完成后用返回的 `result_run_id` 获取报告。取消采用协作式检查点；同步底层调用正在执行时，外层不能强制杀死其内部请求。真实干预执行完成也不等于防御有效。

### API 与操作员授权

本地开发默认不要求 token，服务默认绑定 `127.0.0.1`。部署或共享服务时配置：

```bash
export AGENTGUARD_API_TOKEN='long-random-api-token'
export AGENTGUARD_APPROVAL_TOKEN='separate-operator-approval-token'
```

PowerShell：

```powershell
$env:AGENTGUARD_API_TOKEN = "long-random-api-token"
$env:AGENTGUARD_APPROVAL_TOKEN = "separate-operator-approval-token"
```

配置 API token 后，非公开 API 路径需带 `X-AgentGuard-Token`。真实后端的写操作还需要 `X-Approval-Token` 和 `authorization_ref`，且只能在 `isolated_test_environment` 中执行。不要把 API 暴露到公网，也不要将真实凭证写入配置、源码或压缩包。完整说明见 [`docs/SECURITY_MODEL.md`](docs/SECURITY_MODEL.md)。

## 4. CLI 命令

```bash
agentguard doctor
agentguard capabilities
agentguard scenarios list
agentguard scenarios show demo_cloud_security
agentguard runs status <run-id-or-report-path>
agentguard runs report <run-id-or-report-path>
agentguard adapters check
agentguard artifacts list
agentguard benchmark compare --runs report-a.json,report-b.json
agentguard experience search "unauthorized information flow"
agentguard experience stats --verified-only
agentguard experience recommend --scenario-id demo_cloud_security --risk-type unauthorized_information_flow --seed 7
agentguard serve --host 127.0.0.1 --port 8000
```

`adapters check` 只检查 Python 包是否可导入，不代表真实集成通过。请按 [`docs/INTEGRATION_GUIDE.md`](docs/INTEGRATION_GUIDE.md) 固定底层仓库 commit 并执行现场验证。

## 5. 可选 MCP Server

安装可选依赖并启动：

```bash
python -m pip install -e ".[mcp]"
agentguard-mcp
```

MCP 连接 `AGENTGUARD_BASE_URL` 指向的 AgentGuard 服务，并通过同一 Python SDK 调用公开 API；不会自行创建引擎、读写本地经验库或直接调用 WARDEN。工具限于状态/场景/能力/调查/治理证据查询、只创建不运行的 WARDEN 调查计划、防御候选预览和已验证经验检索。MCP 不提供审批或防御执行工具，也不暴露任意 Shell、Python 或文件系统执行能力。鉴权凭证从 AgentGuard 环境变量读取。

### Python SDK 与稳定业务路由

```python
from agentguard.sdk import AgentGuardClient

client = AgentGuardClient.from_env()
print(client.health())
print(client.list_investigations(limit=20, offset=0))
```

设置 `AGENTGUARD_BASE_URL`、`AGENTGUARD_API_TOKEN`（可选）和 `AGENTGUARD_API_TIMEOUT`。业务路由及旧 `/v1/kernel/*`、`/v1/runs/*` 兼容路径列于 [`docs/PUBLIC_API.md`](docs/PUBLIC_API.md)。

## 6. 架构与模块

```text
CLI / Web Console ──┐
Python SDK ──────────┼──> AgentGuard Public API
Optional MCP ────────┘          |
                        ┌───────┴────────┐
                        |                |
                Upper-layer services   Synthetic research
              WARDEN client + policy       engine
                        |                |
                     WARDEN API       Mock Backend
```

角色协调目前是确定性的、类型化的软件工作流，不是多个独立 LLM Agent 的自主协作。多 Agent 自主调查必须通过与单 Agent 和固定工作流的对照实验来证明价值。

## 7. 真实后端接入边界

- `FractalBenchAdapter.from_installed()` 映射已安装 FRACTAL-Bench 的公开场景目录。
- `StegoPotAdapter.from_config()` 可通过 StegoPot 的 `run_file` API 执行 FRACTAL-Bench 配置，并从报告映射观测和中央评价器指标。
- 适配器不得把隐藏 `trial.truth` 传给 Agent 可见 Observation。
- 通用 `run_file` 调用完成后，不能自动回头修改已完成运行中的消息；真实干预需要配对重放或经过验证的干预回调和明确的 Capability Manifest。
- 本版本未在当前环境完成真实底层仓库的安装、真实模型运行、干预和统计验证。

## 8. 证据等级

- `observational`：观测到的事件或关联。
- `synthetic`：合成场景或 Mock 后端结果。
- `interventional`：真实运行中执行了干预，但仍需要审查设计与替代解释。
- `causal_validated`：需要预先定义的匹配对照、负对照、重复、正常任务效用、统计分析与独立审查。代码不会自动授予该等级。

## 9. 测试与构建

```bash
pytest
ruff check src tests scripts
python -m compileall -q src
python scripts/export_schemas.py
agentguard demo --no-auto-intervene
agentguard doctor
```

- [`docs/IMPLEMENTATION_STATUS.md`](docs/IMPLEMENTATION_STATUS.md)：逐项实现状态和未完成工作。
- [`docs/BUILD_VERIFICATION.md`](docs/BUILD_VERIFICATION.md)：最近验证记录。
- [`docs/STATISTICAL_METHODS.md`](docs/STATISTICAL_METHODS.md)：统计实现与解释限制。
- [`docs/SECURITY_MODEL.md`](docs/SECURITY_MODEL.md)：权限、审批与部署边界。
- [`docs/PROBE_AND_DETECTION.md`](docs/PROBE_AND_DETECTION.md)：多层检测、交互图、线性探针和家族级切分。
- [`docs/AGENT_COORDINATION.md`](docs/AGENT_COORDINATION.md)：角色和消息协议。
- [`docs/EXPERIENCE_BANDIT.md`](docs/EXPERIENCE_BANDIT.md)：经验动作排序、证据门槛和限制。

## License

MIT


## WARDEN 工作流可靠性

AgentGuard 将工作流状态与请求哈希持久化到 SQLite，支持幂等重放、已知 run/plan 恢复、冲突检测和审计事件查询。请先阅读 [`docs/WORKFLOW_RELIABILITY.md`](docs/WORKFLOW_RELIABILITY.md)。这不是远端 exactly-once 保证；不确定的远端提交会停止自动重试并要求对账。
