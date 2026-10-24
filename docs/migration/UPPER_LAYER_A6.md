# 上层平台重构 A6：经验门控与持续学习

日期：2026-10-24。复用既有 `ExperienceStore` 和 Thompson Sampling；未创建第二套经验数据库，也未修改 WARDEN、FRACTAL-Bench 或 StegoPot。

## 实际实现

- 将经验合同升级到 0.2，记录审计运行、来源系统/终态、假设、选中动作、证据等级、工件引用、风险结果、正常任务效用、执行成本、模型/策略版本、可复现摘要、数据分区、验证时间、到期时间和限制。
- 自动奖励只接受来自已完成 WARDEN 运行、来源工件经可信流程核验、可复现摘要完整、至少两次重复、具有风险指标和正常效用测量、训练分区、未过期且显式批准推荐的干预证据。Mock、synthetic、observational、inconclusive、旧格式、无工件、缺指标、未核验或不完整记录不会产生奖励。
- 公共经验提交接口拒绝调用方自称 `verified`。现有 WARDEN 自动记录仍是 observational/unverified；由于外部 WARDEN 工件下载校验接口未提供，当前不会有新记录自然满足可信奖励门控。
- 现有 SQLite 在原地升级；旧记录缺少可信分区时迁入 `held_out_family`，旧合同在重新审查前不能用于奖励。迁移同时重建模型/策略版本索引。
- 强制一个 `case_family` 只能属于一个分区。经验检索支持场景、风险、动作、模型/策略版本、证据等级、最近验证时间和分区过滤；公共 API/CLI 检索不返回 test 或 held-out 记录。
- 所有推荐仅从训练分区取已通过门控的记录，并排除当前案例家族。Test 与 held-out 运行可使用其他训练家族的经验，但不会读取自身的测试标签或事后评价数据。
- 冲突检测对同一运行、同一证据引用和同一上下文的相反结果进行隔离；到期记录从推荐和动作统计中排除。未解决冲突不会以多数票掩盖。
- 保留 Beta-Bernoulli Thompson Sampling；每条已验证记录贡献一个结果，不把重复次数伪作独立样本。输出增加后验不确定性、平均执行成本和成本调整后的抽样分数。所有分数都是决策支持量，不是校准概率或因果效应。
- 推荐策略配置作为不可变版本保存在现有 SQLite 内，记录激活/回滚事件。REST 策略变更要求已配置的 AgentGuard API Token；CLI 提供策略列表和回滚命令。策略版本可由运行请求固定。
- A4 的经验调查排序和 A5 防御候选排序使用同一严格奖励门控，并记录上下文、成本、不确定性和策略版本。
- 新增/更新 53 份 JSON Schema，覆盖经验合同、策略版本、调查请求和 Bandit 报告。

## 验收结果

- A6 定向回归：`86 passed`。
- 全量测试：`236 passed, 1 skipped`。唯一跳过项为真实 WARDEN HTTP 测试，未配置 `AGENTGUARD_LIVE_WARDEN_URL`。
- A6 涉及源码、测试、CLI/API、Schema 脚本 Ruff：通过。
- `python -m compileall -q src tests scripts` 与 `git diff --check`：通过。
- 架构扫描：77 个模块、493 条导入；3 条底层依赖导入仍限于 `agentguard.compat`；`boundary_violations=[]`、`dependency_cycle=[]`。

## 限制与成熟度

- 本阶段没有运行本地 3B/7B 模型，也没有新的真实 WARDEN 防御或处理/对照实验；没有报告策略收益或因果结论。
- 由于无受支持的 WARDEN 工件完整性读取接口，也尚未提供带身份/角色验证的独立经验审查流程，新的真实经验记录仍保持 unverified，不会被用作奖励。策略优化目前只有合同和软件行为测试。
- A6 完成表示经验数据合同、SQLite 分区迁移、查询/隔离、冲突/过期处理和推荐算法经过测试；真实收益与真实模型效果仍为 `blocked`，不等同于 real_backend_validated 或 real_model_validated。
