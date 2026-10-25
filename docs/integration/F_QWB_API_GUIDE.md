# F6: QWB 应用策略评价接口指南

## 接口设计

### 策略评价索引

```
GET /v1/research/strategy-evaluations
```

查询已持久化的策略评价报告。

**查询参数：**
- `limit`: 每页数量（1-500，默认 50）
- `offset`: 偏移量
- `data_partition`: 数据分区过滤（training/validation/test/held_out_family）

**返回结构：**
```json
{
  "schema_version": "1.0",
  "items": [
    {
      "evaluation_id": "eval-xxxx",
      "comparison_id": "comparison-xxxx",
      "strategies": ["fixed_order", "information_value"],
      "repetitions": 3,
      "data_partition": "held_out_family",
      "source_commit": "abc123",
      "executed_at": "2026-10-25T...",
      "report_artifact_id": "art-xxxx",
      "sample_count": 6
    }
  ],
  "count": 1,
  "offset": 0,
  "limit": 50
}
```

### 单个评价报告

```
GET /v1/research/strategy-evaluations/{evaluation_id}
```

返回完整的策略评价报告。

**返回结构包含：**
- `runs`: 每次运行的详细记录
- `aggregates`: 按策略聚合的统计
- `limitations`: 局限性说明
- `source_commit`: 代码版本
- `data_source`: 数据来源标记
- `real_llm_used`: 是否使用真实 LLM
- `degradation_observed`: 是否有降级发生

## 安全规则

1. **只读**：GET 端点不触发模型调用
2. **无任意路径**：不接受任意文件路径
3. **无隐藏真值**：不访问 FRACTAL-Bench 隐藏真值
4. **指标真实**：不生成虚假指标，无数据时返回 null
5. **溯源完整**：显示 source_commit、数据来源、实验协议版本
6. **因果验证标记**：未通过独立验证不标记因果防御成功

## 当前状态

- 接口设计完成
- 实现状态：待开发（最小只读接口）
- 复用现有：InvestigationEvaluationService + LocalArtifactStore
