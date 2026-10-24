# 1.1.0-rc1 测试报告（C0–C8：QWB 应用层集成）

日期：2026-10-24。分支 `feature/qwb-public-api-integration`（自 `7d42809` 创建）。
范围：仅 AgentGuard 上层；WARDEN、FRACTAL-Bench、StegoPot 与 AgentGuard-QWB-App
保持只读。历史 A8 验收记录见 [A8_TEST_REPORT.md](A8_TEST_REPORT.md)，未覆盖。

> **候选后追加（同日）：** ① 真实 WARDEN 内核 live 验证（见 §4a）；② 第十阶段
> 研究概念区分保障测试 8 项（见 §2a）。全量更新为 **342 通过 / 0 失败 / 3 跳过**
> （新增的第 3 项跳过 = `test_live_warden_c_round`，仅在未配置
> `AGENTGUARD_LIVE_WARDEN_URL` 时跳过并明示）。

## 4a. 真实 WARDEN 内核 live 验证（候选后追加）

以本机 WARDEN 检出（`077f540`，0.2.0）启动真实 HTTP 服务（plan 级组合：
`WARDEN_FRACTAL_DATASET=integration-data/dataset.json`，8 个契约实例，不注入
模型运行时——agent.runtime 如实报 unavailable），`AGENTGUARD_LIVE_WARDEN_URL`
指向该服务后运行 `tests/test_live_warden_c_round.py`，**通过**：

- readiness：warden_connectivity/available、api_compatibility/available、
  available_contracts/available（contract_count=8 与内核一致）、
  model_runtime/unavailable（与真实能力报告一致，非 health 推断）、
  intervention_execution/unsupported。
- options：threat_contracts 与内核 `/v1/contracts` 逐项一致；无运行时 →
  全部模式 submittable=false 且 blocker 指明 COMPONENT_UNAVAILABLE:agent.runtime。
- 公共 API 真实提交 observation 计划（start_run=false）→ journal 落盘 →
  timeline 首事件 workflow_created、实验索引出现 warden_workflow 条目
  （execution_status=planned）、图谱如实 available=false（无 run 载荷）。
- 既有 `test_live_warden_http.py`（preflight/plan/幂等重放）同轮通过。

限制：完整观察运行（将产生 C1 真实消息边）需 StegoPot+本地模型；当前 ollama
服务可达但无已加载模型，按诚实原则未拉取模型，该项保持不可执行并记录，
不冒充通过。

## 2a. 第十阶段：研究概念区分保障（候选后追加）

`tests/test_concept_separation.py`（8 项，全过）逐项固化公共输出中的概念边界：
Observation（记录的事件，无风险判定）≠ Finding（带检测器来源的候选风险）≠
Hypothesis（绑定 finding 的可证伪陈述）≠ Intervention（真实执行记录，
simulated_only 不升级）≠ Verification（causal_claim_status 与 provenance 门控）≠
Defense Recommendation（expected_risk_reduction 是承诺不是效果，
execution_performed=false）≠ Defense Effectiveness（仅 verified 生命周期可达）；
统计输出不自带因果标签；研究分析必须先有预注册；plan-only 工作流不产生
run/证据/图谱边；隐藏真值不出现在任何可查询输出。

## 1. 质量检查（全部通过）

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 全量测试 | `python -m pytest -q` | **333 通过 / 0 失败 / 2 跳过**（junitxml 核对；约 65s） |
| Lint | `ruff check src tests scripts`（0.17.2） | 全绿，未放宽规则 |
| 编译 | `python -m compileall -q src scripts` | 通过 |
| 架构审计 | `scripts/audit_architecture.py --check` | 0 边界违规、0 依赖环；低层导入仅位于 `agentguard.compat` |
| Schema 导出 | `scripts/export_schemas.py` | 53 份 JSON Schema 与合同一致（C5/C6 字段变更后重新导出） |

跳过的 2 项与 A8 相同：`tests/test_whitebox_activation`（无 PyTorch 时整模块可选跳过）
与 `tests/test_live_warden_http`（未配置 `AGENTGUARD_WARDEN_URL`，明确报告而非视为通过）。

## 2. 测试构成（相对 1.0.0-rc1 基线 257 项 → 333 项）

| 新增测试文件 | 数量 | 覆盖 |
| --- | --- | --- |
| test_interaction_graph.py | 17 | C1：真实消息转换、Agent→Agent/Tool→Agent 边、部分缺失/重复/乱序、无来源 unavailable、合成隔离、真值拒绝、私有事件过滤、路径不冒充因果、API/OpenAPI |
| test_experiment_index.py | 12 | C2：双来源索引、分页/状态/场景/来源筛选、时间排序、重启恢复、旧日志行、多键解析详情、指标 null 语义、compare 可比/不可比/请求校验 |
| test_system_readiness.py | 11 | C3：11 层检查、health 不升级、transport/degraded/API 版本区分、契约/运行时缺失、显式干预声明升级、逐模式能力、TTL 缓存来源、API 路由与 404 边界 |
| test_investigation_timeline.py | 8 | C4：全生命周期投影、仅 created 不合成、增量拉取、取消事件、重启与顺序、detail 白名单防泄漏、API/404/422 |
| test_report_index.py | 6 | C5：本地核验工件能力字段、外部引用只读、默认引用语义、报告索引/筛选/分页、重启重建报告、OpenAPI |
| test_defense_capabilities.py | 4 | C6：能力字段、审批不改变执行支持、verified 候证标志、引擎报告保守语义（另修复公共 GET 路由的 keyword-only 参数缺陷） |
| test_live_agentguard_http.py | 1 | uvicorn 子进程 + 原生 HTTP：readiness→options→experiments→compare→runs→索引→图谱 全链路与不可用分支 |
| test_sdk_c7.py | 6 | C7：8 个新 SDK 方法路径/参数/引号/错误分类 |
| test_qwb_contract_compat.py | 10 | C7：C0 基线 OpenAPI 纯加法回归、App 依赖的 9 类合同形状、422 不回显、伪造角色不越权、审计不泄漏凭据/查询 |

## 3. 第二类：真实 AgentGuard HTTP 集成

`tests/test_live_agentguard_http.py` 启动真实 uvicorn 服务（含中间件、鉴权、审计、
SQLite 存储），以原生 HTTP 客户端串行验证：readiness 如实报告未配置 WARDEN →
options 无可提交模式 → 实验索引空 → compare 拒绝未知运行 → 创建合成运行 → 索引
带 provenance → 图谱 404/合成回退/标记，全部通过并校验 `X-Request-ID` 回传。

## 4. 第三类：真实 WARDEN 验证

未运行（`AGENTGUARD_WARDEN_URL` 未配置）。C1 的真实数据分支基于既有真实 7B 观察
运行的载荷结构（`integration-data/agentguard-observation-async.sqlite3` 中
`findings[].messages` 的 sender/recipient/message_id/round_index）建立合同测试；
C3 readiness 使用伪内核测试。真实阻塞维持 DEP-001–007 登记，**不标记为通过**。

## 5. 包构建与安装（本地轮次）

- `python -m build`：sdist + 纯 Python wheel（`agentguard-1.1.0rc1`）。
- 干净虚拟环境安装 wheel：`agentguard --help` 正常、SDK 导入成功、
  `create_app().openapi()` 为 76 paths。
- GitHub Actions 三版本矩阵（3.11/3.12/3.13）须在推送候选提交后执行，作为 RC
  兼容性验收的最终依据（与 A8 流程一致；矩阵通过不解除真实后端/模型阻塞）。

## 6. 安全核对（新增项）

- 图谱/实验合同 metadata 经 truth_guard 递归拒绝隐藏真值字段。
- 图谱与证据引用复用 EvidenceGateway 安全规则：`file://`、绝对路径、带凭证 URL
  丢弃且不打开；真实载荷中的 file URI 在测试中验证不出现于响应。
- compare 拒绝未记录模型版本的对照；描述性对比显式声明不支持因果结论。
- 公共防御 GET 路由的既有缺陷（keyword-only `persist` 参数导致 500）已修复并加回归。
- 伪造角色 header 不能越权访问 operator 路由；审计事件不含查询串/凭据；
  422 响应不回显输入值。

## 7. 结论

C0–C8 全部阶段完成：76 个 OpenAPI 路径（较 1.0.0rc1 纯加法 +8），SDK 8 个新只读
方法，QWB App 合同兼容性由只读测试固化。真实后端/模型/因果验证阻塞维持原状，
全部不可用语义（unavailable/unsupported/blocked/unknown/null）如实呈现。
`1.1.0rc1` 候选成立，待三版本 CI 矩阵与远程推送授权后完成发布流程。
