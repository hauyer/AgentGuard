# G6: QWB-App 应用交接文档

## 固定版本

- AgentGuard commit: `feature/g-live-evaluation-and-loop` 分支
- Python: 3.12
- OpenAPI schema: 见 `/openapi.json`

## 新增公共接口

### 1. 策略评价列表

```
GET /v1/research/strategy-evaluations
```

**请求参数**:
- `data_partition` (可选): 数据分区筛选
- `limit` (可选, 默认 50): 每页数量 (1-500)
- `offset` (可选, 默认 0): 偏移量

**响应**:
```json
{
  "schema_version": "1.0",
  "items": [
    {
      "evaluation_id": "eval-xxx",
      "comparison_id": "comparison-xxx",
      "report_artifact_id": "art-xxx",
      "data_partition": "held_out_family",
      "source_commit": "abc123",
      "executed_at": "2026-10-25T00:00:00+00:00",
      "status": "available"
    }
  ],
  "count": 1,
  "offset": 0,
  "limit": 50
}
```

**权限**: 只读 API Token

---

### 2. 策略评价详情

```
GET /v1/research/strategy-evaluations/{evaluation_id}
```

**路径参数**:
- `evaluation_id`: 评价 ID

**响应**: 完整 StrategyComparisonReport 结构

**错误码**:
- 404: EVALUATION_NOT_FOUND
- 410: EVALUATION_ARTIFACT_UNAVAILABLE

**权限**: 只读 API Token

---

## 现有接口（未变更）

### 调查相关
- `GET /v1/investigations/{request_id}/risk-analysis`
- `GET /v1/investigations/{request_id}/interaction-graph`
- `GET /v1/investigations/{request_id}/timeline`

### 调查循环
- `GET /v1/investigation-loops`
- `GET /v1/investigation-loops/{loop_id}`
- `GET /v1/investigation-loops/{loop_id}/decisions`
- `GET /v1/investigation-loops/{loop_id}/evidence-changes`
- `GET /v1/investigation-loops/{loop_id}/budget`
- `POST /v1/investigation-loops` (需 Operator Token + 幂等键)

## 安全边界

- 普通 API Token 不能创建调查循环
- 所有 GET 接口不触发新实验或模型调用
- 不暴露系统提示词、隐藏真值和秘密字段
- 不接受任意文件路径参数
- 损坏工件返回明确错误，不产生 500 堆栈

## 已知阻塞

- 无真实 WARDEN 环境，策略评价数据为 synthetic/recorded
- 公共循环观察动作默认关闭
- 经验策略无真实经验库数据
