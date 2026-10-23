# WARDEN 唯一审计内核迁移顺序

本计划对应阶段 0 审计，不是阶段 1–7 的完成声明。每一步先验收再进入下一步；Warden、FRACTAL-Bench 和 StegoPot 的公开接口必须按固定 commit 重新核实。

| 顺序 | 变更与依赖 | 可验证验收 | 回滚点 |
| --- | --- | --- | --- |
| 1. WARDEN 真实接口联调 | 在独立环境启动固定 commit 的 WARDEN；核 `api_version`、capabilities/resources/contracts、严格请求和 `AuditRun.evidence.artifacts`；给 AgentGuard 增 `kernel doctor`、真实 HTTP 集成测试；隔离 `AuditPlan.threat_contract.truth` | 不存在合同/资源/运行时分别明确阻塞；真实响应进入 SQLite；普通 API、Agent 工具与 LLM 不可见隐藏真值；无 Mock 自动回退 | 保留 0.9.0 公开接口与现有工作流存储；新映射可单独回退 |
| 2. 补 WARDEN 运行组合 | 在 **WARDEN 独立开发分支** 配置 `RuntimeAdapter`、StegoPot 模型运行、Probe/SAE 工件、实际 AuditStage 和 RunDispatcher；FRACTAL-Bench 合同源固定版本 | 至少一个真实多 Agent 场景产生可追踪的运行和工件；无能力时返回 blocked | 不改 AgentGuard 的底层执行器；关闭新的 WARDEN 部署并回到只读诊断 |
| 3. 迁移 AgentGuard 生产调用者 | 生产审计只经 `WardenKernelClient`；保留旧 Probe/WhiteBox/StegoPot 入口作明确弃用的离线兼容路径；确认 API/CLI/MCP/文档/Schema/打包测试调用点 | WARDEN 证据与 synthetic 分离；旧客户端有明确迁移提示；全量测试与真实集成通过 | 分模块恢复到 0.9.0 兼容层，禁止把旧本地结果冒充 WARDEN |
| 4. 清理重复实现 | 仅在 WARDEN 公共等价能力已实测、所有调用者已迁移且弃用窗口结束后删除本地 Probe/Hook 等 | 搜索无生产调用者；合同、集成、CLI、导入测试及发布包检查通过；提供删除清单 | 使用对应迁移提交恢复被删兼容模块 |
| 5. 真实因果与防御闭环 | WARDEN→StegoPot 配对重放、处理/对照/负对照与正常任务效用；AgentGuard 仅编排、审批与统计 | 报告含真实工件哈希、执行状态、效果与限制；没有真实模型时明确 blocked | 停用新实验入口，保留数据与审计日志，不推断无风险 |

## 阶段 1 前置条件

- 固定 WARDEN commit；单独记录 StegoPot/FRACTAL-Bench 的可用 commit、依赖和模型/数据工件。当前只读克隆没有初始化它们的 submodule，也没有真实模型。
- 保留本地 SQLite 的 `submission_uncertain` 语义。远端在提交超时后没有已知 ID 时不得盲目重试。
- 使用 WARDEN 真实 `EvidenceBundle` 响应制定契约测试；保留 `blocked/failed/timed_out` 的独立含义。
- 修复 WARDEN 计划响应透传 `ThreatContract.truth` 的 P0 数据边界问题；原始真值只能留在受控评价侧。阶段 0 的合成哨兵烟测已证实完整计划响应会包含该字段。
- 先修基线验证障碍：可选 PyTorch 缺失时的白盒测试收集错误、Windows SQLite 连接释放、`/health` 版本漂移。改动须在后续阶段单独提交测试，不混进本阶段文档提交。
- 首先交付真实服务的 `health→capabilities→resources→contracts→plan` 链；只有 Runtime、Dispatcher、真实场景与资源齐备，才继续验证模型运行，不把健康检查当作执行能力。

## 兼容性与发布注意

本地 `/v1/probes/train|predict` 已标记 deprecated，仍有脚本与测试用户。`WhiteBoxActivationAdapter` 是公开 Python 导出；`PairedExperimentRunner` 被 API/MCP 调用。删除这些入口会造成可见破坏，需先提供迁移说明与已验证替代。统计归因工具属于上层研究分析，可长期保留，但证据等级不能由它自行升级。`FractalBenchAdapter` 和 `StegoPotAdapter` 的直接调用路径最终须从生产审计中退出。

## 阶段门槛

阶段 0 的文档与基线可以独立审查。当前完整 pytest 仍有 1 项失败、Ruff 173 项问题；真实 WARDEN HTTP 联调和模型实验未做，因此**阶段 1 尚未通过，也不进入阶段 2**。各后续阶段分别提交、报告测试结果、兼容风险与独立 Git commit；不自动推送或创建 PR。
