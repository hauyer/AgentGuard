# 调查策略对比结果（D5，真实运行记录）

- 执行时间：2026-10-25T04:49:30.976081+00:00
- 基础观察：`obs-fixed-observation-eval-base`（来源：live WARDEN 7B observation run）
- source_commit：`a931426`
- 协议：5 策略 × 2 重复 × 同一预算（max_actions=6, 60.0s, model_calls=4）；分桶 held_out_family。

| 策略 | 完成率 | 轮数 | 首证据步数 | 有效动作比 | 冗余动作 | 证据引用数 | 降级率 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| fixed_order | 2/2 | 6.0 | 1.0 | 1.0 | 0.0 | 9.0 | 0.0 |
| risk_score | 2/2 | 6.0 | 1.0 | 1.0 | 0.0 | 9.0 | 0.0 |
| information_value | 2/2 | 6.0 | 1.0 | 1.0 | 0.0 | 9.0 | 0.0 |
| experience | 2/2 | 6.0 | 1.0 | 1.0 | 0.0 | 9.0 | 1.0 |
| llm | 2/2 | 6.0 | 1.0 | 1.0 | 0.0 | 9.0 | 1.0 |

## 说明与限制

- Strategies are compared on investigation behaviour over the same recorded evidence; this is not a model-capability comparison.
- Risk-identification metrics require independent labels and are not computed (null); no detection accuracy is claimed.
- Defense-effectiveness metrics require real interventions and are not computed (null).
- llm 策略在无配置提供商时按合同降级到确定性策略，降级率单独列出，不计为失败。
- 风险识别类指标（Precision/Recall/F1/FPR）需要独立合法取得的真值标注，当前不计算（null）；不得以工具调用成功率替代。
- 防御效果类指标需要真实干预与独立评价，当前不计算（null）。
- 完整逐轮记录：内容寻址工件 `89981ae3e85a553e0ecb87bba1511126dbef410addd3dc525b8b1f4be53288cf`。
