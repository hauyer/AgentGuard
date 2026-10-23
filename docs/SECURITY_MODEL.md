# AgentGuard 安全模型与操作边界

## 默认边界

- 默认运行时为 `MockBackend`，只产生合成事件。
- 默认 API 监听 `127.0.0.1`；不要未经鉴权和网络隔离就绑定公网地址。
- 可通过 `AGENTGUARD_API_TOKEN` 要求所有非公开 API 请求携带 `X-AgentGuard-Token`。
- 真实后端写操作必须提供 `authorization_ref`，并要求设置 `AGENTGUARD_APPROVAL_TOKEN`，在请求中使用 `X-Approval-Token`。
- 真实后端的 message、tool-call、tool-result 等写操作只能使用 `isolated_test_environment` 范围；策略层拒绝真实后端的 `synthetic_demo` 范围。
- 工具结果、消息内容和经验库条目均视为不可信输入；它们不能覆盖策略代码或系统配置。
- `Observation` 拒绝已知的隐藏真值字段名。底层适配器仍有责任只映射 Agent 可见的运行信息，不得把原始 `trial.truth` 转换为任意新名字后绕过此约束。
- 工件按 SHA-256 内容寻址；内容读取时重新计算哈希。

## 真实写操作的授权路径

1. 运行配置与后端 Capability Manifest 明确目标和操作支持。
2. 防御先通过 `preview` 生成候选，不执行任何修改。
3. 操作必须匹配本次运行的 Finding 目标，不能传入任意目标。
4. Policy Engine 检查场景、能力、隔离范围、预算、授权引用和操作员审批。
5. API 记录结果，但不会把 `completed` 等同于防御有效。
6. 必须使用匹配基线/处理组、正常任务效用、风险转移和回滚测试评价防御。

## 不是安全沙箱

AgentGuard 目前不是容器或操作系统隔离设施。不要在服务进程中加载不可信 Python 插件，不要将任意 Shell、文件系统或通用代码执行能力暴露给 MCP。真实实验应在单独的受控工作区运行，并限制凭证权限、网络访问和文件系统范围。

## API 示例

```bash
# 本地开发，无需配置 token
agentguard serve --host 127.0.0.1 --port 8000

# 需要鉴权时，先设置随机长 token
export AGENTGUARD_API_TOKEN='replace-with-a-long-random-token'
export AGENTGUARD_APPROVAL_TOKEN='separate-operator-approval-token'
agentguard serve --host 127.0.0.1 --port 8000
```

PowerShell 可使用 `$env:AGENTGUARD_API_TOKEN = "..."`。不要将真实凭证写入配置模板、日志、源码或压缩包。
