# AgentGuard 0.8.0 大规模开发验收清单

## 1.0.0 迁移阶段 1（2026-10-23）

- [x] 与独立 WARDEN 进程完成真实 HTTP health、capabilities、resources、contracts 和 blocked audit plan 联调，并将状态写入 SQLite。详见 `migration/STAGE1_REPORT.md`。
- [x] 核对 WARDEN `api_version=v1`、调度器状态和真实 `EvidenceBundle.artifacts` 格式；公开计划剔除隐藏真值。
- [x] 主环境 pytest 137 通过、2 跳过；nanogpt/PyTorch 环境 142 通过、1 跳过；WARDEN pytest 20 通过；FRACTAL-Bench 74 通过。
- [ ] 默认 WARDEN 无真实模型 Runtime、执行阶段、调度器、Probe/SAE 工件；模型级审计未执行。
- [ ] AgentGuard 全仓 Ruff 历史问题与 StegoPot 无测试文件的问题仍待处理。


## 1.0.0 迁移阶段 0 审计（2026-10-23）

- [x] 将无 Git 历史的 0.9.0 压缩包导入本地基线，创建 `feature/warden-kernel-migration`。
- [x] 核对 WARDEN `3116af8` 的真实 `/v1` 源码与默认 FastAPI 响应；完成模块调用点和迁移映射。见 `docs/migration/`。
- [x] nanogpt/PyTorch 环境白盒测试 5 通过；完整 pytest 135 通过、1 失败（Windows SQLite 临时文件占用）。
- [ ] 完整测试和 Ruff 尚未通过：Ruff 基线 173 项问题；项目虚拟环境未装 PyTorch 时白盒测试在收集阶段出错。
- [ ] 尚未执行 AgentGuard→独立 WARDEN 服务的真实 HTTP 联调；WARDEN 默认服务没有 Runtime、Probe/SAE 资源或 Dispatcher。
- [ ] 尚无真实模型运行、真实干预或因果有效性验收。阶段 1–7 均未开始。

以上是 1.0.0 迁移项目的新阶段编号，不覆盖下文旧设计文档的 Phase 0–6 编号。

## 0.8.0 WARDEN 生命周期编排（2026-10-23）

- [x] 新增 `WardenLifecycleService`：health/capabilities/contracts/resources 预检、计划提交、运行提交和有界轮询。
- [x] 预检失败时不创建计划；仅在 WARDEN 返回 `planned` 时提交运行。
- [x] 支持 `queued` / `running` 轮询、终态识别、超时和未知状态 fail-closed 处理。
- [x] 新增 `POST /v1/kernel/workflows` 与 `agentguard kernel run`。
- [x] 仅在 `completed` 且存在明确证据引用并提供场景/风险标签时保存经验；记录固定为未验证、观察性、结果不确定、禁止自动推荐。
- [x] 对相同 run ID 的经验写入做幂等处理。
- [x] 加强 WARDEN list 响应结构校验，拒绝错误形状的 capability/resource/contract 响应；启动任务后强制校验 `run_id`。
- [x] 自动化验收：124 项测试通过；Python 编译与 48 份 JSON Schema 解析通过；Ruff 因环境未安装而未执行。
- [ ] 尚未在真实 WARDEN 服务与实际模型工件上验证端到端运行；自动化测试使用可控替身。

## 0.7.0 WARDEN 内核集成补充（2026-10-23）

- 已实现 `WardenKernelClient`，对接 WARDEN `/v1/health`、`/v1/capabilities`、`/v1/resources`、`/v1/contracts`、`/v1/audit-plans` 和 `/v1/runs`。
- 已增加 AgentGuard `/v1/kernel/status`、`/capabilities`、`/resources`、`/contracts`、`/audits`、`/audits/{plan_id}` 与 `/runs/{run_id}`。
- 已增加 `agentguard kernel status|capabilities|resources|contracts|submit` CLI。
- 已加入请求契约、示例配置、错误/超时处理、blocked 状态传播及 fake-transport/API 测试。
- 已将低层审计的权威归属明确为 WARDEN；AgentGuard 本地旧 Probe、Activation 和归因模块尚保留，供兼容/离线基线使用，不属于已删除模块。
- 尚未验证真实 WARDEN 服务、真实 FRACTAL-Bench 合同、Probe/SAE 权重和 StegoPot 白盒运行时的端到端实验。当前测试证明的是集成客户端和 API 契约行为。

---


本文件按 `AgentGuard_最终版总体设计与开发规范.md` 的 Phase 0–6 和最终验收标准对照代码状态。这里的“已实现”指当前仓库中存在代码并有相应测试，不等于真实模型环境已经验证。

## Phase 0：接口与版本冻结

- [x] Pydantic 严格合同，拒绝未声明字段。
- [x] 实验预算支持顶层兼容字段与嵌套 `budget` 对象。
- [x] Scenario、Observation、Finding、Intervention、Evaluation、Evidence、Capability、AgentMessage、RunReport 等 Schema。
- [x] Observation 对已知隐藏真值字段名做拒绝校验。
- [x] Capability Manifest 显式声明后端能力。
- [x] 48 份可导出的 JSON Schema。
- [x] Mock/真实后端状态分离。

## Phase 1：最小端到端闭环

- [x] 场景加载、观测、检测、调查、策略闸门、干预、评价与报告。
- [x] 时间线、预算使用、证据索引与内容寻址工件。
- [x] CLI、REST API 和最小 Web 控制台。
- [x] 合成结果标记为 `synthetic` / `simulated`。
- [x] 失败和协作式取消返回部分报告。

## Phase 2：真实底层适配

- [x] 从安装的 FRACTAL-Bench 目录映射公开场景元数据。
- [x] StegoPot `run_file` 配置执行适配与消息/最终答案观测映射。
- [x] 中央评价指标读取与真值字段隔离逻辑。
- [ ] 本开发环境未安装/未运行真实 FRACTAL-Bench 与 StegoPot 工作副本。
- [ ] 尚未完成真实模型端到端 smoke test、真实干预和真实配对重放验收。

## Phase 3：主动调查与因果证据

- [x] 将 Finding 转为可证伪调查假设，并记录支持证据、替代解释和下一步测试。
- [x] 根据能力清单生成候选动作并以透明启发式分数排序。
- [x] 规则/图关系/行为 fan-out 多层检测基线和交互图候选路径。
- [x] 可选语义评分回调层；默认关闭，不把外部模型评分伪装成已验证的语义分类器。
- [x] 二元逻辑回归线性探针、二分类指标与按案例家族隔离的训练/验证/测试切分。
- [x] `POST /v1/probes/train` 保存模型工件并返回独立测试集指标。
- [x] 自动生成带种子、匹配变量、处理组、对照组和负对照的配对实验设计。
- [x] 提供配对 Bootstrap 区间与 sign-flip permutation 检验工具。
- [ ] 真实后端尚未接入可验证的 matched replay/perturbation API；必须提供专用 paired executor，不能把文件级实验运行冒充为反事实重放。
- [x] 案例家族级 Bootstrap / sign-flip 推断、预注册注册表、确认性终点限制、Holm / Benjamini–Hochberg 校正与偏离报告。
- [x] 基于配对恢复率的候选节点中介贡献摘要，分开报告共谋成功和正常任务效用。
- [x] 优化前后贡献分布的 Path Shift 指标：Total Variation、Cosine Distance、Top-K Jaccard 与 Spearman；请求要求前后归因协议指纹一致。
- [x] 本地 PyTorch 白盒适配器支持 activation capture、zero、reference replacement、noise patch 和审计哈希；已用 toy model 自动测试。
- [ ] 尚未在真实开源 LLM 权重上验证模型架构路径、真实激活 patching 或秘密恢复效果。

## Phase 4：定向防御与验证

- [x] 根据 Finding 和 Capability Manifest 生成最小范围防御候选预览。
- [x] REST API 执行前检查目标、权限、隔离范围、预算、授权引用与真实后端审批。
- [x] 防御执行结果和证据进入运行报告。
- [x] 支持对配对风险/效用数组进行统计验证。
- [ ] 防御后的真实自动重放、风险转移测试和回滚演练尚未集成到底层运行时。
- [ ] Caller-supplied 数组不能自动视为真实实验工件。

## Phase 5：多 Agent 自主协调

- [x] Supervisor、Trace Analyst、Causal Investigator、Defense Planner、Policy Engine、Runtime、Evaluator、Verifier 的类型化消息轨迹。
- [x] 调查排序结果影响候选目标/操作选择。
- [x] 可选 OpenAI-compatible LLM Investigator 能在既有 Finding 集合内提出优先级建议；候选 ID 校验、审计与确定性回退均已实现。
- [x] CLI 可通过 `--llm-investigator` 显式启用；REST 服务可通过 `AGENTGUARD_LLM_INVESTIGATOR=1` 显式启用；状态端点不发起网络调用。
- [x] 每次运行创建独立协调器并测试并发隔离。
- [ ] 角色目前是确定性软件组件，不是独立 LLM Agent。
- [ ] 尚未完成单 Agent / 固定工作流 / 动态多 Agent 的真实模型消融。

## Phase 6：经验库、MCP 与可视化

- [x] SQLite 版本化经验库、文本检索、按场景/风险过滤和动作统计。
- [x] 未验证经验不能开启自动推荐；验证记录必须有重复次数、证据引用和验证说明。
- [x] 可选 MCP Server，暴露限定研究工具，无任意 Shell/Python 执行工具。
- [x] 最小 Web 控制台显示场景、能力、运行、报告、证据、防御预览与交互图。
- [x] CI 工作流覆盖 Python 3.11/3.12/3.13、compileall、Ruff、pytest 与演示命令。
- [x] 经验动作排序接入证据门控 Beta-Bernoulli Thompson Sampling（0.6.0）；只用已验证且显式允许推荐的干预记录，不执行动作。
- [ ] 尚未完成真实线上前瞻评估、上下文嵌入学习或强化学习训练；Thompson 排序仅是候选动作决策支持。
- [ ] 控制台已有交互式 SVG 风险图谱，但尚无配对结果可视化和用户/角色管理。

## REST API

- [x] `GET /health`, `/console`, `/v1/capabilities`, `/v1/scenarios`, `/v1/scenarios/{id}`
- [x] `POST /v1/runs`（同步/后台）、`GET /v1/runs`, `GET /v1/runs/{id}`, `/cancel`
- [x] `GET /observations`, `/findings`, `/hypotheses`, `/investigation`, `/graph`, `/timeline`, `/evidence`, `/report`
- [x] `POST /v1/probes/train` 与 `/v1/probes/predict`，要求 case-family split 并将模型保存为工件
- [x] `GET/POST /v1/runs/{id}/experiments/paired`；执行前检查剩余干预预算，结果保存为内容寻址工件
- [x] `POST /interventions`, `/defenses/preview`, `/defenses/execute`, `/defenses/verify`
- [x] `GET /artifacts`, `/artifacts/{id}`, `/artifacts/{id}/content`
- [x] 经验库写入、搜索、推荐和动作统计端点。
- [x] 可配置 API token；真实后端写操作使用独立审批 token。
- [x] 预注册研究 API：创建/列出注册、注册完整性校验、运行终点分析和查询分析历史。
- [x] `POST /v1/attribution/nodes/analyze`、`POST /v1/attribution/path-shift`，分析报告保存为内容寻址工件。
- [x] `GET /v1/agents/status` 提供无网络调用的可选模型配置检查。

## 最终验收标准现状

- 工程层面：Mock 闭环、合同、审计、REST/CLI、工件与测试已覆盖大部分；真实适配验收仍待本机执行。
- 研究层面：已有主动调查规划与统计工具，但没有真实多次处理/对照实验数据，因此不报告性能提升或因果有效性。
- 安全层面：策略闸门、能力声明、审批、隔离范围与真值字段拒绝已实现；AgentGuard 不是 OS 沙箱，外部部署仍需网络与进程隔离。

## 本地验收命令

```bash
python -m pip install -e ".[dev]"
pytest
ruff check src tests scripts
python -m compileall -q src
agentguard demo --no-auto-intervene
agentguard doctor
agentguard adapters check
```

真实后端集成步骤见 `INTEGRATION_GUIDE.md`；统计方法见 `STATISTICAL_METHODS.md`；权限边界见 `SECURITY_MODEL.md`。


## 0.5.0 新增验收

- [x] 不可原位更新的研究注册记录与 SHA-256 完整性校验。
- [x] 案例家族级配对统计，避免把同一案例家族内的 episode 直接当作独立簇。
- [x] Holm 与 Benjamini–Hochberg 校正，并按注册方案限制可用于确认性分析的终点。
- [x] REST/CLI 注册、分析、历史查询和完整性检查工作流。
- [x] 可选 LLM Investigator 通过 OpenAI-compatible API 调用；模型无工具权限，不可直接执行干预。
- [x] 研究思路中的节点中介贡献和路径迁移描述性指标已实现为可审计分析函数及 REST/CLI 接口。
- [ ] 真实模型 API 未在本次自动化测试中调用；真实 FRACTAL-Bench / StegoPot 适配和配对重放仍需现场验收。


## 0.6.0 新增验收

- [x] 经验动作排序 API/CLI 使用 Beta-Bernoulli Thompson Sampling。
- [x] 只接收已验证、允许推荐且具有干预级证据的记录；不可执行推荐结果。
- [x] 固定 seed 可复现，按场景/风险过滤，返回排除原因和证据引用。
- [x] 增加 2 份公开 JSON Schema 与自动化测试。
- [ ] 尚未做真实前瞻评估；该功能不是 RL 训练，也不提供校准成功概率。
