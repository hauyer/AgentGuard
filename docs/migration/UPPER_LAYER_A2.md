# 上层平台 A2 阶段完成报告

## 一、完成内容

在原有工作流服务与 SQLite 日志内完成同请求并发互斥、状态规则与业务映射、
终态保护、重试/恢复计数和状态事件。两个 API 提交入口及 CLI submit/run
统一走该服务。支持取消未提交本地工作；外部取消明确 unsupported。

## 二、代码变化

新增 `services/workflow_state.py`、`tests/test_workflow_reliability.py`。
修改 `warden_lifecycle.py`、`warden_workflow_store.py`、API、CLI 和相关
生命周期/合同/真实 HTTP 测试。更新 README、CHANGELOG 和可靠性文档。
没有删除业务文件，没有新增第二套工作流数据库。

## 三、架构变化

SQLite 仍保存请求、哈希、审批引用、外部计划/运行、状态、结果和错误；表结构
未变，旧记录直接兼容。业务状态由新枚举和映射提供，保留旧日志 status 字段。
所有终态禁止倒退和覆盖结果。已知 run ID 的恢复只查询原运行；返回身份不符
或缺少提交 ID 时保留不确定状态，不能重新创建实验。

本地 OS 文件锁按数据库路径和 request_id 协调执行，避免两个进程都看到
created 后重复提交；崩溃由 OS 释放锁，不需要过期租约或裸线程。网络文件
系统和多主机部署不在本实现保证内。长任务由外部 WARDEN 持有，AgentGuard
保存 run ID 和有界跟踪状态，没有另建模型执行器或隐式后台任务队列。

新旧状态映射是双向的：只有已有 WARDEN 状态才能映回远端；validating、
submission_uncertain、timed_out 等上层状态不会被发送成虚构内核枚举。
首次导入旧 checkpoint 可从 created 进入已有检查点；后续迁移按规则限制。

## 四、测试结果

完整 pytest：195 通过、2 跳过。覆盖两个独立 Store 的同请求并发、真正
子进程退出后的锁释放、终态不可覆盖、Windows 数据库连接关闭、API/CLI
共享幂等、审批恢复、异常提交身份、取消及全部实际观察状态的事件历史。
主要工作流服务、新模块与新增测试 Ruff 通过；Python 编译和架构检查通过
（68 模块、444 导入，无新增生产越界或直接依赖环）。全仓旧质量缺口仍在 A8。

## 五、真实集成状态

真实 WARDEN HTTP 专项 1 通过，同一 blocked 工作流重放不重复处理并保留
SQLite 与业务状态。运行时仍不可执行；queued/running/completed 与并发异常
路径由替身合同测试验证，不能称为本轮真实模型或真实干预验证。
WARDEN 0.3.0 仅做 GitHub 静态核对，未在本机升级或执行。

## 六、已知问题

外部运行取消/回滚和远端重启丢失任务仍是依赖阻塞。GET 工作流仅返回最近
本地快照，刷新远端状态通过相同请求重入；未伪造自动后台队列。
现有共享审批 token 的进一步业务审批/RBAC 迁移留到 A5/A7。

## 七、Git 信息

分支 `feature/agentguard-upper-layer-v1`，阶段提交
`Serialize durable workflows and enforce lifecycle state rules`。

## 八、下一阶段准备

A2 的本机工作流可靠性与兼容入口验收通过。继续 A3 对旧 Probe、Hook、
直接底层适配器和合成配对研究入口的隔离与弃用；底层三库保持只读。
