# 多层检测、交互图与线性探针

## 多层检测基线

当前默认检测器由三类透明组件组合：

1. **规则层**：显式策略违规、授权拒绝、高异常分数和长编码样式。
2. **图关系层**：将消息、工具调用和结果映射为有证据引用的交互边，输出候选路径。
3. **行为层**：以单次采集窗口内的工具 fan-out 作为简单异常基线。

融合器按 `(target_ref, risk_type)` 合并重复候选，并保留不同风险类型。图分数和 fan-out 分数都是启发式分数，未校准为概率。图路径缺失边、身份合并和时间窗口都会影响分析；它们不证明 Agent 合谋或因果关系。

运行报告提供 `graph_analysis`，REST API 可通过 `GET /v1/runs/{run_id}/graph` 获取。

## 二元线性探针

`agentguard.core.linear_probe.BinaryLinearProbe` 是一个不依赖第三方数值库的 L2 正则逻辑回归基线，可用于研究者已经合法提取的数值表征或人工特征。它实现标准化、批量梯度下降、预测分数、模型序列化和二分类指标。

它**不会**自动读取模型激活，不负责生成标签，也不证明某个方向是隐蔽通信的因果机制。若要将其用于内部激活，需要先在隔离研究环境中实现授权的特征提取器，并记录模型版本、层、token/位置、池化方式、任务标签来源和数据哈希。

## 避免案例家族泄漏

训练/验证/测试切分必须按 case family 分组，将整个 family 放入三个集合中的唯一一侧。`split_by_group_three_way` 提供三路切分；`split_by_group` 保留为只需要两路拆分的通用工具。不要把同一基础案例的不同随机种子随机分到两侧，否则测试指标可能因近重复样本而虚高。

REST API `POST /v1/probes/train` 要求 `features`、`labels` 与 `case_families`，自动执行家族级 train/validation/test 切分；若未指定阈值，则只在验证集上按 F1 选择阈值，最后在测试集上报告指标，并将模型 JSON 以 SHA-256 内容寻址方式保存。原始数据不会被该端点保存，但数据哈希不能替代数据来源记录。

示例请求结构：

```json
{
  "features": [[-1.2, 0.3], [0.8, 1.1], [-0.9, 0.2], [1.3, 0.9]],
  "labels": [0, 1, 0, 1],
  "case_families": ["family-a", "family-b", "family-c", "family-d"],
  "feature_names": ["activation_0", "activation_1"],
  "seed": 7,
  "test_fraction": 0.25
}
```

此示例只展示字段格式；实际训练需要更多案例家族，并确保训练和测试两侧都包含两个类别。

## 评价指标

`evaluate_binary_classifier` 输出 accuracy、precision、recall、F1、FPR、AUROC、average-precision AUPRC、Brier score 和混淆矩阵。AUROC/AUPRC 需要测试集中同时存在正负样本。阈值必须在验证集选择，不能根据最终测试集调优。

这些工具提供研究基线，不替代案例家族独立测试、置信区间、多重比较处理和外部复现。
