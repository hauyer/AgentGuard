# 上层平台重构 A5：证据治理与定向防御

日期：2026-10-24。开发范围仅限 AgentGuard；WARDEN、FRACTAL-Bench 与 StegoPot 保持只读。

## 实际实现

- 新增 `EvidenceGateway`，将 AgentGuard 报告和已持久化的 WARDEN workflow 投影为上层 `GovernedEvidence`。验证 WARDEN run/plan 身份与完成状态；不复制整个 EvidenceBundle，也不访问 WARDEN 返回的本地路径或 URL。
- 对外证据记录来源系统、运行/计划 ID、工件引用、证据类型和等级、风险类型、限制、验证状态、模型/场景版本与时间。对 AgentGuard 自有内容寻址工件才会读取并核验本地 hash；外部或 opaque WARDEN 引用保持未验证。
- 保留 `synthetic`、`observational`、`interventional` 和 `causal_validated` 四级。最高等级需要完成的运行、经验证的来源、干预/评价方法、至少两次独立重复、匹配及负对照、正常任务效用、工件 hash 和替代解释审查；条件不足时降级并记录限制。
- 防御候选公开排序因素和约束原因：风险影响、证据可靠性、正常任务影响/执行成本、权限与审批、可逆性及匹配的已验证经验。排序为启发式；未知风险降低记为 `0.0`，不当作收益估计或最优策略。
- 可从已完成 WARDEN 工作流证据生成多个安全目标候选，并将候选及状态事件持久化在既有 `WardenWorkflowStore` SQLite 数据库。生命周期覆盖提议、审批、提交、执行、验证、拒绝、不支持、失败和回滚。
- 当前没有 WARDEN 外部防御执行器。缺少审批时进入 `awaiting_approval`；批准后记录 `unsupported` 并返回明确错误，不提交、不执行。现有合成报告路径继续标为 synthetic。调用方提供的数组即使统计检验通过，也不能单独将候选升为 verified。
- 更新公开合同、Schema、API 文档和兼容状态；新增证据过滤、因果门控、生命周期、持久化与 API 回归测试。

## 验收结果

- 全量测试：`225 passed, 1 skipped`。唯一跳过项为真实 WARDEN HTTP 测试，未配置 `AGENTGUARD_LIVE_WARDEN_URL`。
- PyTorch 白盒适配器在 `nanogpt` Miniconda 环境验证：`5 passed`。该测试使用 toy model，不代表真实 3B/7B 模型验证。
- A5 涉及源码、合同、Schema 导出脚本和测试的 Ruff 检查：通过。
- `python -m compileall -q src tests scripts`：通过。
- 架构扫描：77 个模块、485 条导入；3 条对低层依赖的导入均位于 `agentguard.compat`；`boundary_violations=[]`、`dependency_cycle=[]`。
- `git diff --check`：通过。

## 限制与依赖阻塞

- 本阶段没有运行真实 3B/7B 模型，也没有执行真实防御、验证或回滚；不报告相应收益或因果结论。
- WARDEN 真实集成测试因未配置服务而跳过。外部/opaque 工件仍未核验；AgentGuard 不读取 WARDEN 的文件 URI。
- WARDEN 当前没有受支持的消息边/工具权限执行器及匹配的风险转移、正常效用和回滚验证接口。批准只代表业务审批，不代表执行能力。
- 因此，本阶段通过的软件测试仅确认合同、筛选、持久化和状态机行为；后端执行成熟度仍为 `blocked`，因果结论不由此成立。
