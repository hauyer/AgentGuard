# GSEI 实现、合同与操作

## 分层

WARDEN核心、StegoPot/FRACTAL子模块、模型Runtime、Probe/LEACE/Patch、SQLite队列均复用。第一版只在WARDEN添加本轮文档。真实节点、消息、应用状态与L12/resid_post/token=-1冻结合同已通过现有服务查询核对。

AgentGuard新增 contracts/graded_security.py、services/graded_security.py、intervention_decision.py、graded_registry.py与api/graded_security.py。模型只验证和计算，决策模块只筛选；执行仍调用StudyExecutionService.submit或submit_batch。原有结构风险发现不自动提升为H或概率；GSEI使用同一已核验消息图事实作为等级绑定依据。

复用内容寻址LocalArtifactStore保存profile/event/decision/execution，独立gsei标签与命名空间；每次直接读取重新验证内容哈希与Study归属。无新业务数据库迁移，无历史指标或工件覆盖。单AG服务进程内序列化相同工件写入，延续现有single_host部署边界。

## 增量API

- GET /v1/studies/{study_id}/security-profile：已登记配置列表，空列表意味着未知。
- POST 同路径：操作者登记不可变SecurityProfile，必须提供来源、Study、配置依据的workflow、节点和边等级；检查对象实际存在。
- POST /v1/studies/{study_id}/value-events?workflow_id=...&profile_artifact_id=...：操作者登记研究标注，绑定真实Event/Run/目标/证据。
- POST /v1/studies/{study_id}/intervention-decisions/preview：body仅workflow_id、profile_artifact_id、event_artifact_id、可选batch_id。保存决策，不创建Run。提供batch_id时事件Run必须属于该不可变批次。
- GET /v1/studies/{study_id}/intervention-decisions/{decision_id}：按内容哈希重新读取原决策。
- POST 同路径/execute：body仅arm_id和可选paired_batch。操作者再次核验冻结合同，使用decision_id派生稳定幂等键。paired_batch=true时创建全部已注册臂的新批次，使用现有submit_batch，不把新Run塞入旧批次。返回明确的执行回执和workflow/批次引用。

旧111个操作与组件Schema逐字段保留。新快照为openapi-gsei-v1.json，102路径/117操作；旧v1/v2快照保留。

## App

BFF在/projects/{project_id}/studies/{study_id}下提供对应入口，保留项目归属检查。配置、事件标注和执行要求operator或admin及CSRF；预览要求analyst及CSRF。分析员预览不会透传操作者权限，不能通过预览获得执行权限。Demo写入明确拒绝。

检测台/干预台展开“节点保护、通道保障与四层干预决策”：选真实节点或边；选择已有安全配置，或由操作者填写等级与审核依据登记；选来源事件、H/B/违规状态；登记标注并预览。未知等级不默认赋值，节点显示S未配置，边显示Q未配置。风险显示单值/区间、政策输入与证据，候选显示支持情况、目标匹配和效果不足原因。

L1/L3执行按钮禁用。L2/L4只有真实冻结合同匹配才允许提交。选“新建完整注册批次”会实际执行该Study全部注册臂，包含baseline；不勾选时为单臂诊断，不能冒称同批次比较。现有结果分析台与PairedRunReplay继续从正式批次服务读取结果。

## 运行与复现

沿用已有本地启动器work/start_remediation_stack.py和原数据库。端口WARDEN19765、AgentGuard19081、BFF19082、前端19173，单Worker。模型仍为现有Qwen2.5-0.5B-Instruct，CPU/float32/2线程。配置本身不加载模型。

启动器与受限工件留在任务工作目录。outputs/gsei保存HTTP证明、测试、浏览器截图、决策工件引用、真实Run结果、同批次比较和重启回查记录。报告逐项区分单元、HTTP、模型执行、行为观察与未验证的因果结论。
