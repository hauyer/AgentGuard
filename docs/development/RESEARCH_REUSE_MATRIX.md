# AgentGuard 研究开发复用矩阵

## 基线

- 核查工作树：`feature/i-reliability-and-live-validation`，HEAD `a5d5603cc653e17025038f3eb0606bc28d8bbd70`；工作树干净。
- 运行时代码基线：`9a8ee1f5e78a0b254c17ea66ac5b140aa7e1e89a`，版本 `1.1.0rc3`。`a5d5603` 只记录发布验收和 Live 阻塞。
- AgentGuard OpenAPI：3.1.0，86 paths；rc3 文件 SHA-256 `fadab4d701d23fabda785413cf0c4536fcffec521ea09cbe6ad73a417c15de1e`，规范化 JSON SHA-256 `52ad0d5dc175e58759196588d1c4ab5b45e82751f882c82ed8540cb963d942f6`。
- QWB-App 固定合同来自 `hauyer/AgentGuard-QWB-App` 的 `feature/b25-b33-real-integration`：AgentGuard pin `63fe1b5662df93fbf06750ecf09c14ee9e6bd5cc` / rc2，86 paths，快照文件 SHA-256 `2ce83660a67111a346a2de9772fcaec1938a4a5de713439de34a83e372b9a5e5`，规范化 SHA-256 `430a8115f4dfb8c865060d45345a58bf267f60657b1ba8ab1c0faa2ba3acf41c`。
- App 清单列出的 33 个支持操作已与 rc3 OpenAPI 的完整 operation object 比较，33/33 相同。精确旧操作快照在 `tests/fixtures/qwb_app_supported_operations_rc2.json`。
- WARDEN 固定源码为 `381fc08170386a2dbd4c2ded3f0f724f871e9636` / 0.3.0。未配置 Live URL；在线服务、在线 OpenAPI 与运行模型资源为 `NOT_RUN`。

## 需求到现有能力

| 需求 | 可复用文件 / 符号 | 当前调用者与生产边界 | 本轮选择 | 新增理由与验证 |
|---|---|---|---|---|
| WARDEN HTTP、固定 API 合同与失败语义 | `adapters/warden.py::WardenKernelClient`、`openapi_summary`、`capabilities`、`resources`、`contracts` | `WardenLifecycleService`、`warden_diagnostics`、readiness；唯一生产 HTTP 边界 | 继续复用，不加第二客户端；仅以其公开 GET 诊断研究能力 | 新 capability projection 测试只检查公开合同/声明；无外部写请求 |
| 远端计划/运行、幂等与不确定提交 | `services/warden_lifecycle.py::WardenLifecycleService`、`services/warden_workflow_store.py::WardenWorkflowStore` | Investigation API、Loop observation adapter；真实 WARDEN POST 的唯一上层生命周期 | 不另建 Job DB / 状态机；研究执行在远端能力齐备前保持 `BLOCKED` | 既有超时、对账、不得盲重提测试；新研究阻塞测试确认不会提交 |
| 调查 Loop 与预算 | `services/investigation_loop.py`、`services/loop_public.py`、`services/loop_registry.py` | `/v1/investigation-loops` | 仅作为调查编排；不把 Loop 当 Study/Arm 或配对实验 | 既有 `tests/test_loop_api.py` 回归 |
| 配对设计、实验臂、失败计数 | `contracts/paired.py::PairedExperimentDesign`、`core/paired_runner.py`（兼容导出）与 `compat/paired_runner.py` | Paired API 与离线 runner；只有配置可信 verifier/executor 才能进入真实 runner | Study 使用冻结的 arm 描述；不复用 synthetic runner 生成真实 RemoteRun | 字段/验证测试；真实执行因 WARDEN 合同缺失为 `NOT_RUN` |
| Study 预注册、分析、存档 | `contracts/research.py::{StudyRegistrationRequest,RegisteredStudy,StudyAnalysisRequest,StudyAnalysisReport}`、`research_registry.py::ResearchRegistry`、`core/study_analysis.py::analyze_registered_study` | `/v1/studies`；SQLite `research_registry.sqlite3` 与本地内容寻址报告 | 扩展同一不可变注册和分析记录，增加可选轨道、数据/模型匹配和 Arm 计划字段；不新建注册表/分析引擎 | 新旧 payload、哈希、授权、隐藏真值测试；统计继续调用 `core/statistics.py` |
| 只读实验索引 | `services/experiment_index.py::ExperimentIndexService`、`contracts/experiment_index.py` | `/v1/experiments` 与 detail/compare | 不将 Study 伪装成已有 WARDEN run；可比运行仍由现有 WARDEN journal 投影 | 旧 33 操作 golden 回归；Study/run 关联保持 BLOCKED，直到真实引用可由 journal 证明 |
| 证据来源、哈希和因果门 | `services/evidence_gateway.py::EvidenceGateway`、`contracts/evidence.py::{CausalValidationCriteria,GovernedEvidence}`、`artifacts/local_store.py` | workflow/evidence 和 paired report | 继续拒绝未读取上游字节/无可验证来源的引用；analysis 显示来源限制 | 既有 evidence 与 synthetic 隔离测试；无上游内容 API 时 integrity/causal 不晋级 |
| WARDEN 能力和 readiness | `services/warden_diagnostics.py::diagnose_kernel`、`services/system_readiness.py::SystemReadinessService`、`contracts/system_readiness.py` | `/v1/kernel/status`、`/v1/system/readiness`、options | 新增只读研究能力投影，逐项给出状态、版本、时间和 blocker；不据 health 推断能力 | fake transport 只用于状态映射测试，Live 保持 `NOT_RUN` |
| AgentGuard public API | `api/public_routes.py`、`api/app.py` 与 `tests/test_qwb_contract_compat.py` | App/BFF 与其他直连客户端；旧 App pin 是只读合同 | 保持固定 33 个 operation object 不变；研究写路径使用现有 token/operator/approval 与审计 | 完整 33 operation golden fixture + 三种授权结果 |
| SDK / MCP | `sdk.py::AgentGuardClient`、`mcp_server.py::create_mcp_server` | 标准库 HTTP SDK；MCP 薄代理，只提供有限 API 工具 | 增加 capability/study 公开 API 调用 wrapper；MCP 仅增加只读能力查询，不实现写入或分析算法 | SDK 请求路径/header 测试及 MCP 边界测试 |
| compat 与旧 Python API | `compat/{linear_probe,paired_runner,whitebox_activation,stegopot,fractal_bench}.py` | 离线/兼容工具；架构审计将这些旧依赖限制在 compat 层 | 保留并标明非生产真实证据；不让其服务新 Live 路径 | `audit_architecture.py` 与 import boundary 测试；不删除旧 API |
| 研究统计 RQ1/RQ2/RQ3 | `core/statistics.py::{clustered_paired_effect_summary,adjust_pvalues}`、`core/study_analysis.py` | `/v1/studies/{study_id}/analyses` | RQ1 outcome 名称与 prereg 复用配对 family bootstrap；RQ2/RQ3 仅在输入定义及匹配字段冻结后扩展现有 statistics；未知值需保留 null | 不单独复制 stats engine；任何 caller-supplied arrays 继续明确为未经独立来源验证 |

## 兼容与成熟度边界

- App 固定的 33 个旧操作需逐个比较 method、path parameters、query、requestBody、responses、security、headers 与错误响应；golden 文件锁定源 pin 的实际 operation object。
- AgentGuard 自己的 rc3 OpenAPI 86 paths 也作为历史快照留档；若增加 API/Schema，版本、快照与 diff 同步更新。
- `compat` 下旧 Probe/paired/whitebox 接口是离线兼容实现，不表示 WARDEN 执行或可信因果证据。
- 508 项基线通过、6 项跳过；跳过项中 5 项因 `AGENTGUARD_LIVE_WARDEN_URL` 未设置，1 项因可选 PyTorch 未安装。真实 WARDEN/模型实验为 `NOT_RUN`。
- 真实四类干预、Receiver 结果、上游工件字节读取与签名均依赖 WARDEN 公共合同；在此之前不得建成功 RemoteRun、不得填充真实结果，也不得从 synthetic 生成 live/causal 声明。
