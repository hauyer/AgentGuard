# 只读底层依赖阻塞

日期：2026-10-24。WARDEN、FRACTAL-Bench、StegoPot 均为只读依赖。
本文件描述接口需求与缺失能力，不授权修改这些仓库。

核对目标为 WARDEN `077f540` 的公开 `/v1` 接口及组合入口。A1 通过只读运行
的真实服务核对 API v1、8 个合同与缺失运行能力，结果明确 blocked；没有新
模型实验。历史真实 7B 观察链路与失败条件见 `migration/OBSERVATION_RUN.md`。
以前保留的未提交底层草稿不作为可用能力。

GitHub 更新补充：上游 main 已到 WARDEN 0.3.0 `381fc08`，增加白盒 CLI、
Corpus 和 Probe Suite，但默认 REST 执行组合仍未补齐；本机未升级或运行
新版本。当前阻塞不因新版本发布而解除。另需保留计划真值过滤，以及区分
上游标准构建与本地 observation 扩展。详见
`migration/UPSTREAM_REVIEW_2026-10-24.md`。

| ID | 缺失能力与依据 | AgentGuard 行为 | 不受阻塞的上层工作 |
| --- | --- | --- | --- |
| DEP-001 | 默认组合没有可用 Activation Backend 或 Probe/SAE 权重；`compiler` 对因果/防御模式要求激活读写、梯度和重放能力 | 实际能力不足时 blocked；候选防御可提出，不能标为 executed/verified | 调查策略、审批、能力感知排序、证据门控 |
| DEP-002 | 默认 WARDEN 计划和运行存于进程内；本地异步调度器没有重启恢复保证 | AgentGuard 保存已知外部 ID；远端丢失时保持 unknown/unavailable，不能重跑当作恢复 | SQLite 幂等、故障恢复、状态查询与审计日志 |
| DEP-003 | 公开 API 没有运行取消或回滚路由 | 外部取消/回滚 unsupported；本地停止轮询不能冒称内核已取消 | 本地跟踪控制、审批撤销、显式状态与说明 |
| DEP-004 | 证据带 `ArtifactRef`，但未提供已核实的受控工件下载/完整性验证 API | 保存引用与声明哈希，完整性 unknown/unverified；不打开 file URI | Evidence Gateway、来源校验、摘要投影、报告 |
| DEP-005 | WARDEN 0.3.0 增加白盒 CLI 和 Probe Suite，但公开 REST 仍无等价 Probe 训练/预测接口、白盒模型运行接口或配对因果重放工作流；观察运行不等于这些能力 | 旧 Probe HTTP 与 Python 接口集中到 `agentguard.compat`；Probe 路由 deprecated，不能伪装为 WARDEN 代理；不支持时不得回退执行 | 完成兼容 API/SDK、调用者迁移和上层证据治理；等待受支持的 WARDEN 公共接口后再评估删兼容代码 |
| DEP-006 | 历史真实 7B 观察组为 6/8 条件完成、2 失败，汇总 failed；无经验证的因果结果 | 保留实际状态与 observational 等级，不产生成功奖励或性能优势结论 | 软件合同测试、策略对照协议、未验证经验过滤 |
| DEP-007 | 当前公开 WARDEN/runtime 接口没有已验证的消息边阻断、字段脱敏或工具权限变更执行器，也没有与正常任务效用、风险转移和回滚相连的真实防御重放 | 真实候选只可提议；缺执行能力时返回 unsupported/等待外部执行，不在 AgentGuard 伪造执行或验证；Mock 只保留 synthetic 标记 | 证据治理、候选排序、人工审批和状态追踪 |

只有真实 capability/resource/API 响应才可解除阻塞。测试替身通过只证明软件
行为，不能解除真实后端或模型验收项。外部依赖不可用不阻止 A4–A7 的独立
工程功能；成熟度应分别标为 unit_tested、contract_tested 或 blocked。
