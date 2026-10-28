# I5：WARDEN 0.4.0rc1 研究接入审查

**核查日期：** 2026-10-28  
**AgentGuard 分支：** `feature/i-reliability-and-live-validation`  
**AgentGuard 包版本：** `1.1.0rc5`  
**AgentGuard API 实现提交：** [`d85aa97e1566e2c50816f2ade3129235266ec704`](https://github.com/hauyer/AgentGuard/commit/d85aa97e1566e2c50816f2ade3129235266ec704)  
**固定 WARDEN 提交：** [`2e121c5c8da41dc4ccbaa72418ecaa3698c2428e`](https://github.com/Xinyuan20061/WARDEN/commit/2e121c5c8da41dc4ccbaa72418ecaa3698c2428e)  
**Live 执行：`BLOCKED / NOT_RUN`**

本文件根据 WARDEN 固定源码和 OpenAPI fixture 审查公开合同。本轮没有真实 WARDEN URL、
研究 token、Operator token 或 API token；没有提交真实 plan/run，没有真实模型调用、工件下载
或新增真实观察数据。HTTP transport 测试与冻结 fixture 只验证 AgentGuard 行为，不能算作
Live 通过。

## WARDEN 公共合同与能力判定

完整的版本化方法、字段、状态和 OpenAPI SHA 见
[`WARDEN_RESEARCH_CONTRACT.md`](WARDEN_RESEARCH_CONTRACT.md) 与
[`WARDEN_CAPABILITY_MATRIX.md`](WARDEN_CAPABILITY_MATRIX.md)。固定 OpenAPI 为 3.1.0、9 paths；
AgentGuard 使用唯一现有的 `WardenKernelClient`、`WardenLifecycleService` 和
`WardenWorkflowStore`，没有新增平行执行器或本地成功 ID。

| 要求 | 固定 WARDEN 0.4.0rc1 合同结论 | AgentGuard 处理 |
| --- | --- | --- |
| 稳定远端操作 ID | 创建成功时返回服务端生成的 plan/run ID；调用方不能指定稳定 ID，未收到响应时无法恢复未知 ID | 只保存真实返回的 ID；缺 ID 时记为待对账，不制造替代 ID |
| 远端提交幂等 | `POST /v1/audit-plans` 不幂等；`POST /v1/runs` 的 SQLite dispatcher 可选实现支持 request id/body 去重，但默认服务未证明注入 | 研究 preflight 因默认幂等能力未声明而阻塞；不自动重试 POST |
| 按操作 ID 查状态 | `GET /v1/audit-plans/{plan_id}`、`GET /v1/runs/{run_id}` 可查服务当前可见记录；run 状态包括 `planned`、`blocked`、`queued`、`running`、`completed`、`failed`、`cancelled` | 只查已记录的真实 ID；状态未知和 404 不被解释为未执行 |
| 服务重启后恢复 | 默认组合的 plan、run 存储为进程内状态；可选持久 dispatcher 未由默认能力响应确认 | 重启后未知状态保持待对账，禁止因空列表或 404 再次提交 |
| 获取结果和证据引用 | 可见 `AuditRun` 可返回 EvidenceBundle、artifacts、metrics 等；artifact content 有公开 GET 路由，但默认未装配 reader | 只有读到真实结果才能记录 evidence；未读取字节的 URI/hash 不进入已验证证据 |
| 来源与权限可验证 | 研究请求和 artifact read 使用 Bearer 凭证；公开合同未提供可验证的来源签名/Study-Arm 归属证明。artifact 路由运行时二进制与 OpenAPI `application/json` 声明不一致 | research token 必须配置；media mismatch 时 artifact adapter fail-closed；证据不升级为 verified |

公开路径、方法、字段、认证和状态逐项见固定合同文档。默认服务缺少 Runtime、durable
dispatcher、研究阶段 allowlist 和 artifact reader 的可验证就绪声明，因此没有研究能力能达到
`SUPPORTED_AND_READY`。存在可选实现类或路由不等于部署已配置。

## AgentGuard 行为和阻塞原因

- 提交前检查固定合同、研究引用配对、Operator/审批授权、研究 Bearer token、能力诊断和预算。
- 只有 WARDEN 实际返回的 plan/run ID 才能写入 workflow journal。无 ID 或超时后状态未知时进入
  `reconciliation_required`；不生成本地替代 ID，不重放外部 POST。
- 研究 Study 与 Arm 引用记录在现有工作流请求记录。没有被真实返回且可核验的远端 run 和结果，
  不生成完成的真实 Study run，也不把结果计入 observed/measured evidence。
- `evidence_mode=measured` 当前返回 `ARTIFACT_CONTENT_NOT_VERIFIED`。Synthetic、Stub、
  `script_replay` 与未验证 observed 数据保持不同来源标签；synthetic 不成为真实观察或因果验证。
- readiness/能力报告保留具体 blocker：WARDEN URL/连接、研究 token、研究阶段白名单、默认持久
  dispatcher、Runtime、artifact reader、artifact media 合同、授权与 provenance。健康检查成功不清除这些阻塞。
- Loop 只有获得真实运行结果和新增证据引用后才能完成；仅有远端 `completed` 状态不足以报告成功。

真实阻塞项为：计划创建没有远端幂等协议；默认部署没有可核验的跨重启任务状态；结果工件读取器
未在默认能力声明中就绪且媒体类型合同矛盾；缺少来源签名与 Study/Arm 归属验证；真实环境凭证及
服务不可用。上述条件未满足前维持 `BLOCKED`，不修改 readiness 让 Live 显示绿色。

## 验收覆盖与执行状态

下表中的本地测试验证 AgentGuard 失败关闭、持久化、合同解析和 Stub 隔离。它们不是 WARDEN
服务验收。真实环境列全部为 `NOT_RUN`。

| 场景 | AgentGuard 本地覆盖 | 真实环境 |
| --- | --- | --- |
| 未配置 WARDEN / 缺少 Operator 凭证 | `test_unconfigured_warden_reports_unavailable_with_blocker`；`test_post_requires_operator_or_approval_credential`；`test_research_warden_workflow_requires_operator_or_approval_before_remote_lookup` | NOT_RUN |
| 远端提交超时、缺远端 ID | `test_uncertain_plan_submission_is_never_retried_automatically`；`test_uncertain_run_submission_is_never_retried_automatically`；`test_malformed_run_response_missing_id_is_rejected` | NOT_RUN |
| 重启后状态未知、不能重复提交 | `test_restart_with_unknown_remote_run_never_resubmits`；`test_unknown_journal_without_remote_id_never_resubmits` | NOT_RUN |
| 相同 AgentGuard 幂等键与已存在操作对账 | `test_idempotent_retries_return_the_same_loop`；`test_workflow_journal_resumes_known_run_without_starting_duplicate`；`test_operator_reconciliation_queries_known_run_without_any_submission` | NOT_RUN；不证明 WARDEN 远端幂等 |
| Stub/synthetic 证据隔离与反馈 | `test_synthetic_or_stub_workflow_is_not_emitted_as_warden_evidence`；`test_http_stub_observation_feedback_informs_the_next_decision`；synthetic 来源分析测试 | NOT_RUN |
| 缺少真实结果时 Loop 不成功 | `test_completed_run_without_evidence_refs_blocks_loop_completion` | NOT_RUN |
| 固定 WARDEN API、鉴权、Artifact media/hash | `test_warden_transport.py` 中 fixture 合同、Bearer header、bounded bytes 与 mismatch fail-closed 测试 | NOT_RUN |
| 真实模型、真实研究工件、真实因果效应 | 无真实执行 | NOT_RUN |

本轮验收：Ruff、compileall、架构审计和 sdist/wheel 构建通过；完整测试为 540 passed、
6 skipped、0 failed、1 warning。6 项跳过为 5 项未配置 WARDEN URL 的 Live 测试和 1 项未安装
可选 PyTorch 的测试。隔离 wheel 安装脚本因代理/SSL 无法连接 Python 包索引而未完成依赖安装；
因此 wheel import/CLI 隔离验证为 `NOT_RUN`。GitHub Actions 对本提交尚未运行，因为本轮按开发
指南不推送。此前其他提交的 CI 不能代替本提交 CI。
