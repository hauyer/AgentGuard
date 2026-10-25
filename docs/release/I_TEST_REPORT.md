# I 阶段测试报告

## 测试范围

| 测试编号 | 测试目标 | 状态 |
|----------|----------|------|
| T01 | Ruff 和基础 CI | ✅ 通过 |
| T02 | CLI 真正运行策略评价 | ✅ 通过 |
| T03 | CLI/API 共享存储，重启后可查 | ✅ 通过 |
| T04 | 工件完整性，索引和报告引用一致 | ✅ 通过 |
| T05 | 重复 evaluation_id 幂等 | ✅ 通过 |
| T06 | 并发注册 | ⚠️ 单元测试未覆盖 |
| T07 | 损坏报告工件返回 410 | ✅ 通过 |
| T08 | 无效研究案例被正确拒绝或分类 | ✅ 通过 |
| T09 | 预算与状态恢复 | ⚠️ 基础幂等通过 |
| T10 | Loop 创建幂等 | ✅ 通过 |
| T11 | 外部执行状态未知 | ⚠️ 未测试 |
| T12 | 新增观察动作严格经过门控 | ✅ 观察=None |
| T13 | 新证据反馈影响下一轮 | ⚠️ 未测试 |
| T14 | 五策略实验同等条件对照 | ✅ 框架就绪 |
| T15 | 数据来源隔离 | ✅ 字段已区分 |
| T16 | SDK 与 API 一致 | ✅ 已验证 |
| T17 | 架构边界 | ✅ 审计通过 |
| T18 | QWB 交接 | ✅ 文档完成 |

## 验收等级

| 功能 | 等级 |
|------|------|
| 策略评价列表 API | ✅ INTEGRATION_TESTED |
| 策略评价详情 API | ✅ INTEGRATION_TESTED |
| CLI → 持久化 → API | ✅ INTEGRATION_TESTED |
| 损坏工件处理 | ✅ UNIT_TESTED |
| 幂等键创建 | ✅ UNIT_TESTED |
| Operator 权限 | ✅ UNIT_TESTED |
| 真实 WARDEN 观察动作 | ⚠️ BLOCKED |
| 真实 LLM 策略实验 | ⚠️ BLOCKED |
| 真实五策略对照实验 | ⚠️ BLOCKED |

## 测试命令

```bash
python -m pytest -q
ruff check src tests scripts
python -m compileall -q src scripts
python scripts/audit_architecture.py --check
```
