# 上层 A8 候选发布验收

日期：2026-10-24。A8 以 AgentGuard 代码、合同、构建和发布文档为范围；没有修改
WARDEN、FRACTAL-Bench 或 StegoPot。

## 完成项

- 修复目标分支 CI 中 Ruff 的未使用导入、异常处理、重复测试命名问题；保留测试
  数量及参数化边界，不放宽 lint。
- 将公共业务 alias 路由移出大 API factory，并把 console 静态 HTML 移至独立模块；
  兼容路由仍由原服务回调提供。OpenAPI 合同用于防止旧/新接口漂移。
- 对 API 输入校验和中间件异常日志加真值/敏感信息回归；API 校验不回显请求值，
  中间件不记录异常文本。
- 加入 Python 3.11–3.13 CI 矩阵使用的 runtime/dev 约束锁和 wheel/sdist 构建步骤。
- 增加架构图、成熟度矩阵、API、部署、测试报告和限制文档。

## 验收报告

测试结果、目标 Actions 失败基线、SQLite/安全检查和待办项见
[`../release/A8_TEST_REPORT.md`](../release/A8_TEST_REPORT.md)。成熟度与真实依赖状态见
[`../release/FEATURE_MATURITY.md`](../release/FEATURE_MATURITY.md) 和
[`../DEPENDENCY_BLOCKERS.md`](../DEPENDENCY_BLOCKERS.md)。

真实 WARDEN HTTP 烟测、真实模型运行和因果防御执行没有配置/执行条件，均按 blocked
记录。候选发布前仍须等待分支上 Python 3.11、3.12、3.13 GitHub Actions 全绿；不以
本机 Python 3.12 或模拟测试代替矩阵与真实后端验收。
