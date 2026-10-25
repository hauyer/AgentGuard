# F8: 最终测试报告

## 测试范围

| 检查项 | 结果 |
|--------|------|
| pytest -q | ✅ 全部通过 |
| ruff check src tests scripts | ✅ All checks passed |
| compileall src scripts | ✅ 通过 |
| audit_architecture.py --check | ✅ 无边界违规，无依赖循环 |

## 各阶段完成状态

| 阶段 | 状态 | 说明 |
|------|------|------|
| F0 代码审计基线 | ✅ 完成 | 基线确认，环境检查，文档生成 |
| F1 幂等性修复 | ✅ 完成 | 强制要求 X-Loop-Idempotency-Key，先登记后执行 |
| F2 风险分析审计 | ✅ 完成 | 合同字段完整，安全语义正确，无需修改 |
| F3 状态一致性 | ✅ 完成 | 边界测试：ID 语义、预算 null、证据变化、缺失工件 |
| F4 观察能力审计 | ✅ 完成 | ObservationRunService 已实现，公共 API 默认关闭（安全设计） |
| F5 策略评价协议 | ✅ 完成 | 实验协议文档，指标定义，数据隔离规则 |
| F6 评价接口设计 | ✅ 完成 | 只读接口设计，安全规则定义 |
| F7 安全可靠性回归 | ✅ 完成 | 10 项安全审计全部通过 |
| F8 最终验收 | ✅ 完成 | 本报告 |

## 真实 WARDEN 联调状态

- **Live 测试**: 未运行（无配置真实 WARDEN 环境）
- **Synthetic 测试**: 全部通过
- 所有测试结果标记为 synthetic 数据，不作为真实环境验证结论

## 已知限制

1. **真实 WARDEN 环境**: 未配置，live 测试无法运行
2. **真实模型运行时**: 未配置，LLM 策略自动降级
3. **经验库**: 无经验记录，experience 策略自动降级
4. **防御执行器**: 未接入，防御指标保持 null
5. **公共 API 观察动作**: 未启用，当前为只读分析模式
6. **策略评价接口**: 设计完成，待开发最小只读实现

## 版本信息

- **基线提交**: 3d508bf (E 轮完成)
- **F 轮开发分支**: feature/f-loop-hardening-and-evaluation
- **不自动合并 main**

## QWB-App 接入 API

已可用的公共 API：
- 风险分析：`GET /v1/investigations/{request_id}/risk-analysis`
- 调查循环：`GET /v1/investigation-loops/{loop_id}`
- 决策记录：`GET /v1/investigation-loops/{loop_id}/decisions`
- 证据变化：`GET /v1/investigation-loops/{loop_id}/evidence-changes`
- 预算：`GET /v1/investigation-loops/{loop_id}/budget`
- 创建循环：`POST /v1/investigation-loops`（需 Operator Token + 幂等键）

待开发：
- 策略评价查询：`GET /v1/research/strategy-evaluations`

## 是否具备合并条件

**当前不建议合并到稳定分支**，原因：
1. 无真实 WARDEN 环境验证
2. 策略评价接口未实现
3. 公共 API 观察动作未启用
4. 需要真实环境联调后再评估

建议在真实 WARDEN 环境就绪后，完成 live 联调和策略评价接口实现，再合并到稳定分支。
