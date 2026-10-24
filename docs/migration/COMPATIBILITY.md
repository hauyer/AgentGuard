# 0.9.0 兼容入口迁移说明

## 当前保留入口

| 旧入口 | A3 处理 | 推荐的 1.0 用法 | 移除评估条件 |
| --- | --- | --- | --- |
| `agentguard.core.linear_probe` | 保留导入垫片；实现移入 `agentguard.compat.linear_probe` | 生产审计使用 WARDEN Probe 资源与 `/v1/kernel/workflows`。本地向量训练/预测仅作为弃用离线基线 | WARDEN 提供正式训练/预测 API 与兼容工件合同，调用者迁移完成后再评估 1.1 移除 |
| `POST /v1/probes/train`、`POST /v1/probes/predict` | 保持现有请求/响应合同；OpenAPI 标记 deprecated；描述明确说明不调用 WARDEN | 使用 WARDEN 资源和审计工作流。当前 WARDEN REST 没有等价训练/预测端点，不能直接替换原训练客户端 | 对应 WARDEN 公共接口发布并完成训练模型工件迁移后，再公布移除版本 |
| `agentguard.core.paired_runner` | 保留导入垫片；实现移入 `agentguard.compat.paired_runner` | 合成配对研究通过 AgentGuard API/MCP；真实配对重放由 WARDEN 实现 | WARDEN 提供真实配对重放并完成既有研究调用者迁移后评估 |
| `agentguard.whitebox.activation` 和 `agentguard.whitebox` | 保留导入垫片及包级导出；实现移入 `agentguard.compat.whitebox_activation` | 生产白盒审计使用 WARDEN；旧脚本可在受控本地环境继续导入 | WARDEN 正式公开等价运行/干预接口、结果迁移和调用者迁移完成后评估 |
| `agentguard.adapters.fractal_bench` | 保留导入垫片；实现移入 `agentguard.compat.fractal_bench`；只映射公开目录信息 | AgentGuard 新应用通过 WARDEN 合同与工作流。不得将基准隐藏真值传入 Agent | 明确兼容包归属、公开场景目录合同和所有旧调用者迁移后评估 |
| `agentguard.adapters.stegopot` | 保留导入垫片；实现移入 `agentguard.compat.stegopot`；生产 API 禁止注入真实 AgentGuard runtime | 实际审计通过 WARDEN 公共 API；文件级旧适配器仅本地兼容 | WARDEN 正式运行 API 覆盖所需能力且调用者和工件迁移后评估 |
| `agentguard.core.attribution` | 保留在 core，作为上层输入数组统计 | 将来源、证据等级、运行状态和限制随统计报告保留 | 不计划删除；统计结果本身不验证工件来源或升级因果证据 |

## 兼容范围与限制

- A3 未删除任何 API 或 Python 导入路径。五份本地实现集中到兼容包，原文件只
  负责转出原有符号，避免维护两套副本。
- API 的 Probe 路由只训练调用方提交的数值向量，不提取激活、不调用 WARDEN，
  返回值也不作为 WARDEN 风险证据或已验证因果结果。
- API/MCP 配对接口在合成后端下使用 `synthetic` 级别。真实 AgentGuard runtime
  与直接 paired executor 注入仍被拒绝；不可用、失败或被阻止的 WARDEN 工作流
  不会回退到这些旧实现。
- 旧 Python 导入垫片保留至 1.0 兼容窗口结束；1.1 是重新评估候选版本，不是
  无条件删除日期。任何后续移除都要更新发布说明并列出实际文件。
- 当前没有能够替代本地 Probe 训练/预测或白盒/配对实验的 WARDEN 公共 REST
  接口，因此兼容实现不得从 AgentGuard 直接删除。依赖缺口见 `../DEPENDENCY_BLOCKERS.md`。
