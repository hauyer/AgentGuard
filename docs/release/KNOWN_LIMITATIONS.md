# 1.1.0-rc1 已知限制

## 真实依赖阻塞

- 当前环境没有配置 `AGENTGUARD_WARDEN_URL` 与 `AGENTGUARD_LIVE_WARDEN_URL`，所以真实
  WARDEN HTTP 烟测、plan/run 生命周期与生产资源核验均为 blocked。C1 图谱与 C3
  readiness/options 的真实分支仅在伪内核与既有真实 7B 观察载荷结构上验证。
- 公开 WARDEN 组合未提供 AgentGuard 所需的 Activation/Probe/SAE 权重与成对因果重放
  执行器；消息边阻断、字段脱敏、工具权限改写等策略仍不能据此宣称已执行或有效。
- 本轮未运行本地 3B/7B 模型；真实模型与真实因果防御均未验证。历史 7B 观察组
  6/8 完成、2 失败、整体 failed，只能作为观察性记录。
- WARDEN 工件下载与端到端完整性验证 API 未核实；AgentGuard 只保存/投影安全引用，
  不打开 `file://`、本地路径或不可信 URI。C5 的 `integrity_verified` 仅覆盖
  AgentGuard 本地内容寻址工件，外部工件保持 `read_only_reference`。

## 运行边界

- API 未配置令牌时鉴权关闭；必须将服务绑定在可信私网，或配置令牌并放到 TLS/网络
  访问控制后的可信代理后面。
- 限流是单进程内存窗口，不跨 worker 或主机共享；内存中的研究任务/报告/取消状态也
  不适合未经专门验证的多 worker 横向部署。C5 的报告重建使持久化报告跨重启可读，
  但运行中的后台任务状态仍在进程内。
- Health 只检查 AgentGuard API 存活。CORS、鉴权、限流与 audit middleware 的合同
  已通过自动测试，但未完成生产反向代理或多节点部署审计。C3 readiness 的缓存是
  进程内 TTL（默认 30 秒），同样不跨 worker 共享。
- 本地 SQLite 恢复/并发有软件测试，不代表真实断电、磁盘耗尽、跨进程长期压力或
  多主机故障注入已全部实测。
- Swagger/ReDoc、OpenAPI、health 和 console 当前是公开路由；生产环境需在可信代理
  侧按需要限制文档访问。

## API 与研究解释

- 旧 `/v1/kernel/*` 与 `/v1/runs/*` 保留供迁移期使用；旧 Probe 路由已 deprecated，
  后续需结合下游迁移情况决定删除版本。
- Demo/Mock 仅代表 synthetic。observational、interventional、blocked、failed 与
  causal-validated 状态保持区分；任务成功、统计显著或防御候选存在均不自动证明因果
  防御成功。
- C1 图谱的边只代表已记录的通信元数据，不是秘密泄漏证据；消息级时间戳缺失时排序
  依赖 round_index。C2 实验索引中 WARDEN 工作流暂无稳定实验分组 ID
  （experiment_id=null），compare 结果是描述性差异，不构成防御效果测量。C6 能力
  字段中审批通过不改变 execution_supported_overall。
- GitHub Actions 的 Python 3.11/3.12/3.13 矩阵须在候选提交后全部通过，才完成 RC
  兼容性验收。矩阵通过也不能解除真实后端/模型阻塞。
