# AgentGuard 配对实验统计方法

## 当前实现

`PairedExperimentRunner` 对合成后端按声明种子执行 control、treatment 和同种子 no-intervention replay 三条轨迹，调用后端 evaluator 收集指标，并对配对值计算统计摘要。这个 replay 只检查重放稳定性，不是 sham 或无关位置负对照；合成 runner 将这两个必需对照标记为 `not_run`，且始终只输出 `synthetic_only`。

真实后端必须提供显式 `paired_executor`，并先提交完整预注册设计：方案工件及 SHA-256、scenario、模型 revision、解码设置、匹配条件、clean/secret-off 与 corrupt/secret-on 条件、候选目标、sham 与无关位置操作、正常任务效用与秘密恢复指标、停止条件、排除规则、随机/置换基线，以及至少两个独立案例家族。运行报告必须回显设计指纹，并保留预注册的每个 seed（包括失败对）；成功 pair 需要 control、treatment、sham、无关位置和随机/置换基线五个执行臂的稳定 ID、输出工件引用与全部预注册指标。

这只证明执行器返回的合同完整，不会自动验证它声称的工件内容或来源。报告仍保持 `causal_validation_required`；Evidence Gateway 当前不提升上游的因果声明。真实执行与可信工件/来源核验未配置时保持 `BLOCKED` / `NOT_RUN`，不得用合成统计摘要替代。

`agentguard.core.statistics.paired_effect_summary(control, treatment)` 接收按相同实验单位配对的数值序列，计算：

- 对照组与处理组均值；
- 配对差值的均值、中位数、标准差和标准误；
- 固定随机种子的非参数 Bootstrap 百分位 95% 区间；
- 双侧配对 sign-flip permutation p 值。样本量不超过 16 对时枚举全部符号组合；更大样本使用固定种子的 Monte Carlo 抽样。

`risk_reduction` 在防御验证助手中定义为 `baseline_risk_mean - post_defense_risk_mean`，正值表示输入数组中的风险指标下降。效用变化仍单独报告，不能只展示风险收益。

## 使用示例

```python
from agentguard.core.statistics import paired_effect_summary

summary = paired_effect_summary(
    control=[0.8, 0.9, 0.85, 0.95],
    treatment=[0.3, 0.4, 0.35, 0.5],
    seed=7,
)
print(summary)
```

## 解释限制

- 配对单位必须确实匹配；代码不会验证任务输入、模型版本和环境是否相同。
- Bootstrap 默认按配对差值重采样，不处理案例家族内的聚类依赖。若同一案例家族有多个 episode，应先按预先定义的家族级单位聚合或使用聚类方法。
- 没有多重比较校正。多指标、多策略比较时需要预先确定主要终点并考虑校正。
- p 值不能替代效应大小、实际意义、失败案例和正常任务效用分析。
- 统计函数只分析调用者传入的数组，不会自动读取 StegoPot 原始结果，也不会将结果自动升级为 `causal_validated`。
- 防御 API 的输入数组由调用者提交，除非保存并核验原始工件，否则不应视为可复现的真实测量。

## 案例家族聚类推断（0.5.0）

当一个案例家族含多个 episode 时，不能默认将所有 episode 当作独立观测。`clustered_paired_effect_summary(control, treatment, case_families)` 会先计算每对的处理差值，再在家族内求均值，并以**案例家族等权平均效应**为估计量。95% 区间通过重采样整个家族均值构造；sign-flip 检验也在家族级翻转符号。家族数不超过 16 时枚举全部符号组合，更多家族时使用固定随机种子的 Monte Carlo 近似。

该估计量不是对所有 episode 直接等权的平均效应。若不同家族样本量差异很大，应在预注册阶段明确选择家族等权还是 episode 等权，不能看到结果后再选择更有利的口径。

## 多重比较校正

`adjust_pvalues` 支持：

- `holm`：控制家族错误率，适合确认性分析中的有限终点集合。
- `benjamini_hochberg`：控制预期错误发现率，适用于明确声明为发现性分析的终点集合。
- `none`：不校正；若方案注册了多个终点，必须在注册时明确这一选择并在报告中披露。

研究注册 API 会要求分析请求包含主要终点和所有已登记次要终点，并拒绝未登记的终点进入确认性分析。每个终点同时保留原始 p 值、校正后 p 值、效应方向与主要终点判定。

## 预注册与数据完整性

研究计划存储在 `ResearchRegistry` SQLite 表中，不提供覆盖更新操作。规范化方案内容以 SHA-256 标识，`verify_registration` 用于检测意外更改。分析时，系统对提交的 outcome 数组进行规范化哈希；如果调用方同时提供 `data_sha256`，必须与系统计算值相符。注册哈希和数据哈希都不构成可信时间戳，也无法证明调用者提供的数值确实来自所引用的实验工件。

完整工作流和示例见 [`RESEARCH_WORKFLOW.md`](RESEARCH_WORKFLOW.md)。
