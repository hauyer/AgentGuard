# F0: 开发基线审计

## Git 信息

- **分支**: feature/f-loop-hardening-and-evaluation
- **基线提交**: 3d508bfbbc7e4bcd4914bd7ebbbaece8b2229258
- **基线分支**: feature/e-investigation-service-integration
- **工作区状态**: 干净（仅 3 个未跟踪日志文件）

## 软件版本

- **Python**: 3.12.10
- **项目版本**: 见 pyproject.toml
- **Pytest**: 9.1.1
- **Ruff**: 已安装

## 测试结果

| 检查项 | 结果 |
|--------|------|
| pytest -q | ✅ 全部通过 |
| ruff check src tests scripts | ✅ All checks passed |
| compileall src scripts | ✅ 通过 |
| audit_architecture.py --check | ✅ 无边界违规，无依赖循环 |

## OpenAPI 状态

当前公共 API 端点（自 C0 基线以来新增）：

- `/v1/experiments`
- `/v1/experiments/compare`
- `/v1/experiments/{experiment_id}`
- `/v1/investigation-loops`
- `/v1/investigation-loops/{loop_id}`
- `/v1/investigation-loops/{loop_id}/budget`
- `/v1/investigation-loops/{loop_id}/decisions`
- `/v1/investigation-loops/{loop_id}/evidence-changes`
- `/v1/investigations/options`
- `/v1/investigations/{request_id}/interaction-graph`
- `/v1/investigations/{request_id}/risk-analysis`
- `/v1/investigations/{request_id}/timeline`
- `/v1/reports`
- `/v1/system/readiness`

## 底层阻塞

1. **真实 WARDEN 环境**: 未配置，所有 live 测试未运行
2. **真实模型运行时**: 未配置，LLM 策略自动降级
3. **经验库**: 无经验记录，experience 策略自动降级
4. **防御执行器**: 未接入，防御指标保持 null

## 本轮需要修复的问题

### F1: 幂等性问题
- `create()` 先执行后登记，存在重复执行风险
- `create_with_idempotency_key()` 先登记后执行，需要统一
- 无幂等键的 POST 应明确限制

### F2: 风险分析接口审计
- 需确认 RiskFinding 合同字段完整性
- 确认 score 可 null、未校准分数不被解释为攻击概率
- 确认不泄漏隐藏真值、不串 request_id

### F3: 调查循环状态一致性
- 确认 ID 语义严格区分
- 决策数据不事后补造
- 证据变化不重复计数
- 预算 unknown 保持 null

### F4: 真实观察动作能力
- 当前 observation=None，公共循环不发起新观察
- 需审计 WARDEN API 是否支持新观察任务

### F5: 策略真实评价
- E3 证明策略行为不同，未证明优劣
- 需建立可复现的受控实验

### F6: 策略评价公共接口
- 应用层需要查询持久化的评价报告
- 需新增只读评价索引接口

### F7: 安全可靠性回归
- 统一审计 API Token 泄漏、权限门控、并发、预算逃逸

### F8: 最终验收
- 全量测试、wheel 构建、安全审查、发布文档
