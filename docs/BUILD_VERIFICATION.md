# AgentGuard 构建验证记录

验证范围：严格数据合同、Mock 闭环、主动调查与配对设计、图关系/行为检测、线性探针、统计工具、定向防御、REST API、经验库、工件存储、CLI 和发布压缩脚本。

## 1.1.0rc3 候选（2026-10-26）

候选提交为 `9a8ee1f5e78a0b254c17ea66ac5b140aa7e1e89a`，分支
`feature/i-reliability-and-live-validation`，版本 `1.1.0rc3`。GitHub Actions
[40336215846](https://github.com/hauyer/AgentGuard/actions/runs/40336215846) 在 Python 3.11、3.12、
3.13 均成功。

- 每个 CI 版本：**508 passed、0 failed、6 skipped、1 warning**。1 项因未安装可选
  PyTorch 跳过；5 项真实 WARDEN 测试因 `AGENTGUARD_LIVE_WARDEN_URL` 未配置跳过。
- 本地 `.venv\\Scripts\\python.exe -m pytest -o addopts='' -q -rs` 同为 **508 passed、0 failed、
  6 skipped、1 warning**。
- Ruff、`compileall`、架构审计和 `git diff --check` 通过；架构审计没有边界违规或依赖环。
- `python -m build --sdist --wheel --no-isolation` 成功构建 sdist 与 wheel；隔离 wheel 安装检查成功，
  并核对包版本、SDK 导入和 CLI 帮助。默认镜像 TLS 失败后使用本地构建依赖及官方 PyPI 完成验证。
- Synthetic demo smoke 通过，但不构成真实研究或 Live 证据。在线 WARDEN OpenAPI、真实 WARDEN、模型、
  Probe、配对干预和因果验证均为 **NOT_RUN**；真实 WARDEN 仍 **BLOCKED**。
- rc3 OpenAPI 3.1.0 包含 86 paths；快照及运行时 SHA、rc2 语义差异见
  [`integration/openapi-1.1.0rc3.diff.md`](integration/openapi-1.1.0rc3.diff.md)。

历史 rc2 CI 仍作为独立基线保存；本节链接的是 rc3 提交对应的 Actions 运行。

## 本轮自动验证

- AgentGuard 0.6.0 基线测试：**95 tests passed**。
- `python -m compileall -q src scripts tests`: passed。
- `python scripts/export_schemas.py`: **45 JSON Schemas** regenerated.
- Detection layers: rule, graph and behavior are active by default; the semantic scorer is an opt-in structured callback and is not enabled by default.
- `agentguard demo`: passed; output is marked synthetic and does not claim causal validity.
- `agentguard demo --no-auto-intervene`: passed; generates hypotheses, ranked actions, paired design and defense preview without executing the automatic intervention.
- `agentguard run --config examples/experiment.json`: passed; saved a reproducible JSON report.
- `agentguard doctor` and `agentguard adapters check`: passed; these check local configuration/importability, not real integration.
- REST tests cover synchronous/background runs, cooperative cancellation, API token middleware, artifact read/write, preview/execute/verify defenses, probe training/prediction, experience memory and synthetic paired control/treatment/negative-control execution.
- Release packaging test verifies the design document is included and transient `.agentguard` files are excluded.

- 预注册研究 API/CLI：注册哈希校验、案例家族级统计、确认性终点限制和多重比较校正均有自动化测试。
- `agentguard study register/analyze/integrity/analyses`：已用示例数据完成端到端 CLI 验证，输出明确标记为 synthetic。
- 可选 LLM Investigator：使用离线 fake transport 测试 JSON 解析、候选 ID 白名单、无工具请求、密钥不泄漏和错误回退；未调用真实模型 API。

## 本环境未执行的检查

当前执行环境没有安装 Ruff，且包索引网络不可用，因此本次没有实际运行 `ruff check`。CI workflow 已配置在 Python 3.11、3.12 和 3.13 上安装开发依赖并运行 Ruff、pytest、compileall 和演示流程。

## 真实后端验证边界

本次没有在真实模型服务上运行 FRACTAL-Bench / StegoPot，也没有执行真实配对干预。因此没有声称完成真实模型端到端验证、真实防御效果验证或因果归因。`StegoPotAdapter` 真实 `run_file` 路径仍需在装有固定版本底层仓库的开发机上进行现场验收。

## 已知限制

- 多角色协调仍以确定性工作流为主；可选 LLM Investigator 仅对现有 Finding 排序，没有工具权或干预授权权。
- 图路径与行为检测是启发式基线，线性探针训练 API 仍只消费调用者提供的数值表征；新的本地 white-box adapter 能采集 PyTorch 模块输出，但尚未自动生成探针训练数据或接入真实 StegoPot paired replay。
- 配对实验设计可在 Mock 环境执行合成控制/处理/负对照闭环；真实后端仍要求专用 paired executor。
- 案例家族聚类推断、多重比较校正与预注册管理已实现；候选节点中介贡献和 Path Shift 指标也已实现，但都仍需要真实激活干预、原始工件和预先确定的独立分析计划。
- 防御验证 API 接受调用者提交的配对数组；没有原始工件时不能把这些数值当作已独立核验的测量结果。
- 真实防御的风险转移检查、自动回滚演练和后处理重放尚未与底层运行时打通。
- 取消是协作式的；同步底层调用已在执行时不能被外层强制终止。
- AgentGuard 本身不是操作系统安全沙箱。


## 0.6.0 新增验证

- Experience Bandit 使用固定随机种子时输出可复现。
- 未验证、未启用推荐的记录不会进入动作后验；合成/观察性证据不能通过 `ExperienceRecord` 的 verified 合同。
- `GET /v1/experiences/bandit-recommendations` 已用 API 测试覆盖。
- 新增两个 JSON Schema：`experience-bandit-action-score.schema.json` 与 `experience-bandit-recommendation-report.schema.json`。
- 该功能只产生建议，不执行干预；没有真实的前瞻动作选择评估，因此不声称推荐性能提升。

- 全量测试在可写的临时工作副本中运行；项目原始挂载目录为只读挂载，因此不在只读目录执行需要创建临时文件的打包测试。


## AgentGuard 0.7.0 / WARDEN integration check (2026-10-23)

- Current suite: **110 tests passed**.

- Added `WardenKernelClient` and versioned REST bridge for WARDEN health, capabilities, resources, threat contracts, plans and runs.
- Added AgentGuard kernel API routes and CLI commands; `blocked`, `unreachable` and `not_configured` remain explicit states.
- Unit/API integration tests use an injected transport and do not require a live WARDEN server.
- Real end-to-end causal audit remains **not verified** until the deployed WARDEN service, threat contracts, runtime and resource artifacts are available.



## AgentGuard 0.8.0 / WARDEN lifecycle (2026-10-23)

- Added lifecycle orchestration and evidence-gated memory persistence.
- Added unit/API tests for preflight, state normalization, polling deadline, and safe experience-record creation.
- Real WARDEN runtime, model artifacts, and private FRACTAL-Bench access remain external prerequisites and are not claimed as verified by fake-transport tests.


Automated verification for this package build: `pytest` — 124 passed; `python -m compileall -q src` — passed; all 48 checked-in JSON Schemas parse successfully. Ruff was not run because the executable is not installed in the environment. These checks use fake WARDEN responses and do not certify a live model audit.


## 0.9.0 工作流可靠性增量

本版本新增 SQLite 工作流日志、幂等请求校验、状态事件历史、已知 plan/run 恢复和不确定提交保护。测试使用本地 FakeKernel/HTTP transport，不构成真实 WARDEN 或模型端到端验证。


## AgentGuard 0.9.0 — Durable WARDEN workflow journal

- Automated suite: **136 tests passed** in the development workspace.
- `python -m compileall -q src scripts tests`: passed.
- JSON Schema export: **48 schemas** generated.
- Added SQLite idempotency journal, request-payload conflict detection, append-only state history, workflow listing/detail endpoints, and CLI inspection.
- Added recovery of known `plan_id` / `run_id`, plus a fail-closed `submission_uncertain` state after ambiguous remote submission errors.
- Tested with FakeKernel and injected HTTP transport. These tests do not establish live WARDEN runtime or model integration.
- Ruff static analysis was not run unless the executable is available in the packaging environment.
