# I6: 五策略对照实验协议

**协议标识：** `I6-STRATEGY-COMPARISON-v1`

**状态：** 协议冻结；本协议对应的多案例实验尚未执行。

**策略合同：** `src/agentguard/contracts/investigation_loop.py` 与
`src/agentguard/contracts/investigation_eval.py`。

## 研究问题

在完全相同的调查起点、证据、候选动作、预算和底层能力条件下，五种策略的
调查行为和证据收集情况有何差异？本实验不把动作选择差异解释为模型能力、攻击
检测准确率或防御效果差异。

## 比较策略

| 合同 ID | 含义 |
| --- | --- |
| `fixed_order` | 按固定候选顺序选取动作 |
| `risk_score` | 按风险分数排序 |
| `information_value` | 按启发式信息价值排序 |
| `experience` | 使用符合隔离规则的历史经验；无可用经验时必须记录降级 |
| `llm` | 使用受候选集约束的模型建议；未配置模型或调用失败时必须记录降级 |

不得为了改变策略排序、移除失败记录或让某种策略占优而修改比较规则。策略的
阻塞、失败、跳过和降级都保留在逐次运行记录中。

## 分析单位与案例隔离

- 分析单位是一个冻结的基础调查输入（含 case family、seed、初始状态和证据）在
  某策略与重复编号下的一次运行。
- 配对比较必须对每个策略使用同一个基础调查快照、相同候选动作集合和相同 seed。
- 同一历史观察的多次回放只用于检查运行稳定性；不能计作多个独立真实案例。
- 跨案例汇总必须保留 `case_family` 分层。只有一个 family 时只报告描述统计，不作
  显著性或泛化结论。
- `experience` 只可访问预先划定的训练数据和运行前已登记的经验；不得读取验证、
  测试或 held-out 案例的后验结果。提交实验前需记录经验库快照/哈希。
- LLM 的提供商、模型标识、参数、提示模板版本、调用预算和降级原因均需记录；无
  提供商时标记 `skipped_not_configured` 或合同记录的对应状态，不声称发生模型调用。

## 固定条件与运行记录

每个配对组固定以下条件：

1. 完全相同的基础调查状态和初始证据引用；
2. 完全相同的候选动作 ID 和动作能力；
3. 完全相同的 `max_actions`、`max_seconds`、`max_model_calls` 与
   `no_new_evidence_rounds`；
4. 相同的数据分区（`training`、`validation`、`test` 或 `held_out_family`）；
5. 相同的 WARDEN 能力快照、授权和远端配置；
6. 相同的运行超时、轮询规则和证据读取规则。

每个运行保留基础 `request_id`、案例 family、seed、策略、重复编号、预算、执行
状态、停止原因、降级标志、逐轮记录、source commit、执行时间和报告工件 ID。失败、
阻塞及超时不能从分母中删除。实际报告必须同时记录输入清单/哈希、数据来源类别、
完整代码 SHA、evaluation ID 和报告工件 ID；任何字段缺失时结果不得标为可复现。

## 指标与解释

| 合同字段 | 解释 |
| --- | --- |
| `steps_to_first_evidence` | 首次新增证据的轮数；不是秒数。无新增证据时为 `null`。 |
| `evidence_refs_collected` | 新增的独立证据引用数；不代表检测准确率。 |
| `effective_action_ratio` | 有效动作比例；无可评估动作时保持 `null`。 |
| `redundant_action_count` | 冗余动作数量。 |
| `blocked_ratio` | 阻塞动作比例。 |
| `degradation_rate` | 策略降级运行比例；同时列出每次降级原因。 |
| `model_calls_used` | 实际模型调用数；没有发生调用时才为 0。 |
| `elapsed_seconds` | 实测运行耗时，不由步骤数推算。 |
| `stop_reason` | 每次运行的停止原因，逐次报告。 |

保留 `risk_identification_metrics` 和 `defense_effectiveness_metrics` 的合同
空值。没有独立真值时不计算 Precision、Recall、F1 或 FPR；没有真实干预及独立
评价时不报告防御成功率或因果效果。

## 数据来源分层

| 类别 | 标记和含义 |
| --- | --- |
| Synthetic Control | `synthetic`；人工构造或合成后端，只支持软件/受控行为验证 |
| Recorded Observational | `observed`；有来源、时间与证据引用的历史真实观察记录 |
| Live Observational | `live_observational`；本次经授权实际调用 WARDEN 并追溯到真实证据 |
| Interventional | `interventional`；真实受控干预及其配对、独立结果 |
| Stub | `stub`；隔离的 HTTP/服务替身，仅验证合同与反馈流程 |

这些类别分开报告。Stub 和 synthetic 运行不计入 live 或 interventional 样本。

## 汇总方法

- 先按 strategy、case family 和 repetition 列出逐次结果与状态，再给描述性汇总。
- 在具有多个独立案例 family 且配对完整时，报告配对差值、样本量和不确定性；按
  预注册方案控制多重比较。小样本不使用“显著”措辞。
- 缺失或未知指标用 `null` 表示；不得把失败、缺失记录或未知远端结果填为零。
- 预算敏感性分析必须逐预算单独记录完整输入、配置和报告工件，不与主实验混为一组。

## 当前执行状态

- 五种策略与评价字段已在合同中定义；CLI/API 持久化链路已有 synthetic 标记的端到端
  软件测试。
- 已执行一组单案例 synthetic control 比较：5 策略 × 2 次技术重复。数据不是独立真实
  观察，结果和全部原始工件见 `I_RESULTS.md`。
- 尚无符合条件的 Recorded Observational、Live Observational 或 Interventional I6
  结果；synthetic control 不支持真实性、准确率或策略泛化结论。
- 真实 WARDEN 观察能力仍受 I5 的授权、幂等与远端持久化门控；真实模型实验未执行。
- 旧 `docs/research/RESULTS.md` 中的 D5 摘要缺少可核验的 evaluation ID 和本地可读
  报告工件，已从 I6 有效结果中排除，详情见 `I_RESULTS.md`。
