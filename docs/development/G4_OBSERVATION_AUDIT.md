# G4: 受控启用真实观察动作审计

## 当前状态

公共调查循环初始化时使用 `observation=None`，这是安全设计：
- 公共 API 不自动发起新的模型观察任务
- 避免未经授权的真实模型调用
- 避免预算逃逸

## 现有能力

AgentGuard 内部已有 `ObservationRunService`，支持：
- 连接 WARDEN 内核客户端
- 解析 Threat Contract
- 提交观察任务
- 查询运行状态
- 接收证据引用

## 安全开关设计

建议环境变量：
```
AGENTGUARD_ENABLE_LOOP_OBSERVATION=false
```

默认关闭。启用时必须同时满足：
1. WARDEN 真实配置（WARDEN_URL 已设置）
2. 模型运行时已确认
3. 合法 Threat Contract 已加载
4. 当前只使用受支持的 observation 模式
5. 服务端 Operator 授权
6. 幂等机制完整
7. 预算限制有效
8. 远端状态能受控查询

任何条件不满足，返回 `blocked/unsupported`，不降级成伪造的观察成功。

## Stub 集成测试

当前环境无真实 WARDEN，只完成 Stub 软件测试：
- ✅ 单元测试通过
- ✅ Stub 集成测试框架已就位
- ❌ 真实 WARDEN HTTP 联调未执行
- ❌ 真实模型运行未执行

## 阻塞项

- 无真实 WARDEN 环境
- 无真实模型运行时
- 未配置 Threat Contract

## 结论

观察动作门控设计已完成，软件层面就绪。生产环境启用需在具备真实 WARDEN 资源后进行联调。
