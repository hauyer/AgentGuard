# 阶段 1：真实 WARDEN HTTP 联调记录

日期：2026-10-23。AgentGuard 分支 `feature/warden-kernel-migration`；WARDEN 分支 `feature/agentguard-runtime-integration`。固定上游起点：WARDEN `3116af8`、StegoPot submodule `f374a39`（2.1.0）、FRACTAL-Bench submodule `8081bc6`（0.4.0）。三库以 editable 安装在 AgentGuard 的 Python 3.12 虚拟环境；nanogpt Miniconda 的 PyTorch 2.11 仅供白盒测试使用。

## 实际完成

- WARDEN 公开计划响应现在只保留威胁合同的 `contract_id` 与 `scenario_id`。AgentGuard 客户端对未升级的 WARDEN 服务也做同样投影，避免隐藏真值、目标 ID 进入 API、CLI 和 SQLite 工作流快照。内核内部的 `ThreatContract.truth` 仍留给受控评价。
- WARDEN 的 capability 报告新增 `run.dispatcher`，明确显示是否配置运行调度器。
- AgentGuard 增加 `agentguard kernel doctor` 和 `GET /v1/kernel/doctor`，分别检查 API 版本、运行时、激活后端、调度器、两个上游包、合同和指定 Probe/SAE。健康状态 `ok` 不代表具备审计执行能力。
- 生命周期预检拒绝非 `v1` API；按实际 `AuditRun.evidence.artifacts[]` 提取工件 URI；修复了可选 PyTorch 测试收集、Windows SQLite 连接释放与 `/health` 版本漂移。
- 新增 opt-in 真实 HTTP 集成测试。只有配置 `AGENTGUARD_LIVE_WARDEN_URL` 才执行；无服务时明确 skip，绝不换成替身后端。

## 真实联调环境与结果

使用 FRACTAL-Bench 官方 CLI 生成一个 `release_review` 案例家族、8 个因子条件的合成基准数据集，配置 `WARDEN_FRACTAL_DATASET` 并在 `127.0.0.1:8765` 启动**真实 WARDEN FastAPI 进程**。这是软件集成测试数据，不是模型性能数据。

| 步骤 | 结果 |
| --- | --- |
| `GET /v1/health` | HTTP 200，`version=0.2.0, api_version=v1` |
| `GET /v1/capabilities` | FRACTAL-Bench/StegoPot 包可用；`agent.runtime`、`activation.backend`、`run.dispatcher` 不可用 |
| `GET /v1/resources` | HTTP 200，空列表；无 Probe/SAE 工件 |
| `GET /v1/contracts` | HTTP 200，8 个真实加载的基准合同 ID |
| AgentGuard 提交 `capture_only` 计划 | WARDEN 返回 `blocked`，明确缺 `activation_read, agent_message_hook, model_invocation_hook`；没有提交模型运行 |
| AgentGuard SQLite | 工作流最终 `finished`，5 条状态事件；计划不含隐藏真值 |
| 真实 HTTP 自动化测试 | `tests/test_live_warden_http.py` 配置 URL 后 1 通过；无 URL 时 1 跳过 |
| WARDEN 查询计划 | `threat_contract` 公开字段仅 `contract_id, scenario_id` |

完整 AgentGuard pytest：主环境 **137 通过、2 跳过**（未装 PyTorch 时跳过白盒测试、未配置实时 URL 时跳过 HTTP 测试）；nanogpt/PyTorch 环境 **142 通过、1 跳过**；配置真实 WARDEN URL 的专项 HTTP 测试 **1 通过**。WARDEN pytest：**20 通过**，源包和 wheel 构建通过。FRACTAL-Bench 测试套件 **74 通过**。固定的 StegoPot submodule 未包含 `test_*.py`，pytest 收集 0 项，不能据此声称该库测试通过。AgentGuard/ WARDEN 新增与修改的关键文件 Ruff 检查通过；AgentGuard 全仓 Ruff 原有问题尚未完成清理。Python `compileall` 通过。

## 边界与下一步

本阶段证明了真实 HTTP 合同、计划阻塞和 AgentGuard 工作流持久化。默认 WARDEN 未注入多 Agent Runtime、白盒模型、执行阶段、调度器或已训练 Probe，因此**没有完成模型审计或防御效果验证**。在清理 AgentGuard 的旧白盒/Probe 代码前，应先在 WARDEN 独立分支建立 WARDEN→StegoPot 的可执行链并完成真实模型/配对实验。无资源时继续返回 blocked。
