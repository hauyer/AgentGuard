# F4: 真实新观察动作能力审计

## 审计结论

**ObservationRunService 已实现，但公共 API 中 observation=None 是安全设计。**

## 现状

### 已实现组件

- `ObservationRunService`：标准化观察工作流，基于 `WardenLifecycleService`
- 支持：计划、执行、记录标准化观察调查
- 验证：运行时配置通过内核自身声明验证，不信任调用方传入的 demo ID
- 语义：观察模式仅返回观察结果，不暴露因果干预入口

### 公共 API 状态

当前公共调查循环（`public_loops`）初始化时：
- `observation=None`
- 含义：公共循环不会自动发起新的模型观察执行流程

### 安全设计理由

1. 发起新观察需要：
   - 真实 WARDEN 内核配置
   - 有效模型运行时
   - 权限门控（operator/approval）
   - 预算控制
2. 公共 API 中默认关闭，避免意外触发外部审计
3. 私有服务层（`InvestigationLoopService`）已支持注入 observation 服务

## WARDEN API 支持情况

| 能力 | 状态 | 说明 |
|------|------|------|
| 新观察任务 | ✅ 支持 | ObservationRunService 已实现 |
| 真实模型运行时 | ⚠️ 未配置 | 当前环境无 WARDEN_URL |
| 威胁契约获取 | ⚠️ 依赖内核 | 需内核配置 |
| 任务和运行 ID 返回 | ✅ 设计支持 | 观察记录包含 run_id |
| 新证据查询 | ✅ 设计支持 | 证据网关统一查询 |
| 幂等恢复 | ✅ 设计支持 | 工作流存储持久化 |
| 服务端预算内执行 | ✅ 设计支持 | InvestigationLoopBudget 控制 |

## 阻塞项

1. **真实 WARDEN 环境**: 未配置，live 测试无法运行
2. **公共 API 观察动作**: 未启用，当前为只读分析模式
3. **Stub 集成测试**: 未编写，需要模拟观察动作的完整流程

## 安全限制

- 禁止将 `observation=None` 改为伪造可执行对象
- 禁止重新实现 WARDEN 运行时
- 真实观察集成测试需要真实 WARDEN 环境后才能执行

## 后续

- 真实 WARDEN 环境就绪后，启用公共 API 的观察动作
- 编写 Stub 集成测试验证完整流程
- 真实环境联调验证
