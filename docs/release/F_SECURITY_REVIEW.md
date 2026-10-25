# F7: 安全和可靠性回归审计

## 审计范围

所有新旧公共 API 端点：
- `/v1/investigations` (CRUD)
- `/v1/investigations/{request_id}/risk-analysis`
- `/v1/investigations/{request_id}/interaction-graph`
- `/v1/investigations/{request_id}/timeline`
- `/v1/investigation-loops` (CRUD)
- `/v1/investigation-loops/{loop_id}/decisions`
- `/v1/investigation-loops/{loop_id}/evidence-changes`
- `/v1/investigation-loops/{loop_id}/budget`
- `/v1/defense-policies/{request_id}/preview`
- `/v1/defense-policies/{request_id}/execute`

## 安全审计结果

### API Token 泄漏

| 检查项 | 状态 | 说明 |
|--------|------|------|
| 审计日志不记录 Token | ✅ | 已有测试验证 |
| 错误响应不回显 Token | ✅ | 422 验证测试不回显输入 |
| 普通 API Token 不泄漏 Operator Token | ✅ | 权限隔离测试 |

### 权限门控

| 检查项 | 状态 | 说明 |
|--------|------|------|
| 普通 API Token 不能创建调查循环 | ✅ | 401 测试 |
| 普通 API Token 不能执行防御动作 | ✅ | Approval Token 门控 |
| 伪造角色声明无效 | ✅ | X-Role 伪造测试 |
| Operator Token 不能提升权限 | ✅ | 权限边界测试 |

### 幂等与并发

| 检查项 | 状态 | 说明 |
|--------|------|------|
| 幂等键强制要求 | ✅ | F1 修复，422 测试 |
| 并发创建不重复 | ✅ | 并发测试通过 |
| 幂等键冲突检测 | ✅ | 409 冲突测试 |
| 重启恢复查询 | ✅ | 重启测试通过 |

### 预算逃逸

| 检查项 | 状态 | 说明 |
|--------|------|------|
| 模型不能扩大预算 | ✅ | LLM 安全原则 |
| 预算服务端限制 | ✅ | 请求校验 |
| 预算超限拒绝 | ✅ | 422 测试 |

### Prompt Injection

| 检查项 | 状态 | 说明 |
|--------|------|------|
| 不可信证据文本不形成系统指令 | ✅ | LLM 设计原则 |
| 模型 rationale 不持久化 | ✅ | 设计原则 |
| 模型不能自定义工具 | ✅ | 候选动作 ID 限制 |

### 数据隔离

| 检查项 | 状态 | 说明 |
|--------|------|------|
| 跨 request_id 不串数据 | ✅ | 隔离测试 |
| 隐藏真值隔离 | ✅ | 不访问 ground_truth |
| WARDEN file:// 路径不暴露 | ✅ | 设计原则 |

### 状态与恢复

| 检查项 | 状态 | 说明 |
|--------|------|------|
| 注册先于执行 | ✅ | F1 修复 |
| 崩溃后可查询不确定状态 | ✅ | CREATED/RUNNING 状态 |
| 不盲目重发外部审计 | ✅ | 设计原则 |

## 结论

安全和可靠性回归审计通过，未发现高危问题。
