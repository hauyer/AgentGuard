# 调查策略评价实验协议（D5）

版本：1.0（2026-10-25，与 `scripts/run_investigation_eval.py` 对应）。

## 1. 被比较对象

同一 `InvestigationLoopService` 下的五种动作选择策略：

`fixed_order`、`risk_score`、`information_value`、`experience`、`llm`。

策略只决定**动作顺序**；可执行动作集合、预算门控、停止条件、证据判定对所有
策略完全一致（配对设计：同一基础调查、同一预算、同一代码路径）。

## 2. 基础调查与数据隔离

- 基础证据：一次固定的真实观察运行（live WARDEN + qwen2.5:7B；运行配置
  `fixed-observation-eval-base`，seed=7，`data_partition=held_out_family`）。
- 隔离规则：
  - 被测策略只消费公共投影（图谱/证据/索引/经验检索），不接触隐藏真值；
    真值字段在合同层递归拒绝。
  - 经验策略检索的是**历史已验证**经验库；评估会话的经验库为空时按合同降级
    并计入降级率，不回填同次结果。
  - 评估工件标签带 `data_partition`，不跨桶复用。

## 3. 执行协议

1. 若配置 `AGENTGUARD_LIVE_WARDEN_URL`：先执行固定观察运行得到基础
   `request_id`；无内核时脚本显式输出 NOT EXECUTED 并退出，不伪造结果。
2. 对每个策略 × 每次重复，以相同 `InvestigationLoopBudget`
   （max_actions=6、60s、model_calls=4、no_new_evidence_rounds=3）运行循环。
3. 每次运行记录 `StrategyRunRecord`（逐轮决策见对应 loop 报告工件）；
   blocked/failed/timed-out 一律计入聚合，不剔除。
4. 聚合输出均值与完成/阻塞计数；重复数=1 时显式声明无离散估计。
5. 全部记录以内容寻址工件持久化，RESULTS.md 由脚本从真实记录生成。

## 4. 预算敏感性与消融

- 预算敏感性：以 max_actions ∈ {3, 5, 8} 重跑同一协议（ABLATION_PLAN.md）。
- 消融：无经验库（experience→降级）、无 LLM（llm→降级）即协议内建；
  无证据反馈变体见 ABLATION_PLAN.md 的待执行项。

## 5. 统计规则

- 小样本：报告逐次记录 + 均值 + 明确的不确定性声明；不使用显著性措辞宣称
  优越性；不预填胜率或检测性能。
- 需要独立真值/真实干预的指标组恒为 null（METRICS.md §1/§4）。
