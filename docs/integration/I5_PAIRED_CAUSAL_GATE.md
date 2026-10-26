# 配对干预与因果证据门控

## 状态

**真实配对执行：BLOCKED / NOT_RUN。因果验证：BLOCKED。** 本文件记录上层执行合同和软件门控，不代表 WARDEN、StegoPot 或真实模型已执行。

## 预注册执行合同

`PairedExperimentDesign` 保留既有计划字段，并可固定完整真实执行条件：

- 预注册方案工件引用和 SHA-256、scenario、模型 revision 与解码设置；
- clean/secret-off 对照和 corrupt/secret-on 处理条件；
- 候选目标、干预类型、正常任务效用指标、秘密恢复指标；
- sham、无关层/位置负对照、随机/置换基线；
- 停止条件、预先声明的排除规则、未向模型输入隐藏真值的声明；
- seed 安排与至少两个独立案例家族；匹配维度包含场景、任务输入、模型 revision、解码设置、案例家族和 seed。

任何真实提交在触发执行器前都会要求配置可信预注册工件验证器，并检查预注册完整性、场景/模型匹配和目标/操作一致；没有验证器时在提交前 `BLOCKED`。真实执行器返回后，AgentGuard 检查设计指纹、方案引用及哈希、设置摘要、条件、操作、案例家族、seed 集合和每对的保留情况。报告中的 `status=completed` 还要求五个执行臂均成功，并附稳定 ID、输出工件引用，以及主要终点、效用和秘密恢复指标。失败或缺失的 repetition 不能被静默删除。

内置 synthetic runner 仍只执行 control、treatment 和同 seed replay。它将 sham、无关位置及随机/置换基线标为 `not_run`；报告始终是 `evidence_mode=synthetic`、`causal_claim_status=synthetic_only`。这种 runner 只验证上层状态流，不是因果对照实验。

## 因果等级门控

`CausalValidationCriteria` 0.3 要求独立运行与案例家族、预注册/配置指纹、不同实验条件、匹配/sham/无关位置负对照及随机/置换基线、效用、五臂输出、执行日志、干预参数、评价器版本、效应量、置信区间、多重比较方法、统计报告和审查记录。引用必须是 SHA-256 内容寻址引用；方案、执行日志和干预参数声明的哈希必须与引用一致。`GovernedEvidence` 还要求所有引用出现在证据工件列表中。

结构通过不等于内容或来源真实。Evidence Gateway 对 AgentGuard 报告中的因果声明降级；对 WARDEN 上游声明也不会据布尔字段提升等级。工件必须由本地受控内容寻址存储验证 run/plan 所属关系和字节哈希，来源和方法审查还需可信记录。当前 WARDEN 公共 API 没有白盒配对执行、工件下载或可验证来源签名合同，因此没有路径可完成真实因果晋级。

## 本地验证与真实验证

- 合同、synthetic runner 和故障门控通过定向单元测试时，只证明 AgentGuard 软件行为。
- 真实 WARDEN、真实模型、五臂配对运行和真实工件/独立审查：**NOT_RUN**。
- 不报告真实检测率、防御成功率或因果效应；已有 I6 对照仍为 synthetic。

底层能力依据见 [`WARDEN_CAPABILITY_MATRIX.md`](WARDEN_CAPABILITY_MATRIX.md) 和 [`I5_WARDEN_LIVE_GATE.md`](I5_WARDEN_LIVE_GATE.md)。
