# H0: 开发基线审计

## Git 信息

- **分支**: feature/h-runtime-integration
- **基线提交**: 4715e6fc08dad5ff2e2c1f69bbf2537dcde89ec1
- **基线分支**: feature/g-live-evaluation-and-loop
- **工作区状态**: 脚本有未提交修改（Ruff 已修复）

## 软件版本

- **Python**: 3.12.10
- **Pytest**: 9.1.1
- **Ruff**: 已安装

## 测试结果

| 检查项 | 结果 |
|--------|------|
| pytest -q | ✅ 全部通过 |
| ruff check src tests scripts | ✅ All checks passed |
| compileall src scripts | ✅ 通过 |
| audit_architecture.py --check | ✅ 无边界违规，无依赖循环 |

## CI 状态

- Ruff I001 导入顺序问题：✅ 已修复
- 三个 Python 版本 CI：待 GitHub Actions 验证

## 当前公共 API

### 调查相关
- `GET /v1/investigations/{request_id}/risk-analysis`
- `GET /v1/investigations/{request_id}/interaction-graph`
- `GET /v1/investigations/{request_id}/timeline`

### 调查循环
- `GET /v1/investigation-loops`
- `GET /v1/investigation-loops/{loop_id}`
- `GET /v1/investigation-loops/{loop_id}/decisions`
- `GET /v1/investigation-loops/{loop_id}/evidence-changes`
- `GET /v1/investigation-loops/{loop_id}/budget`
- `POST /v1/investigation-loops`（需 Operator Token + 幂等键）

### 策略评价
- `GET /v1/research/strategy-evaluations`
- `GET /v1/research/strategy-evaluations/{evaluation_id}`

## 已知问题

1. **H-02**: 多案例实验 CLI 使用临时目录，与生产服务数据库脱节
2. **H-03**: 评价 API 跨进程可见性待验证
3. **H-04**: 公共循环观察动作未启用（observation=None）
4. **H-05**: 未执行真实多案例对照实验

## 未配置的底层资源

- **WARDEN_URL**: 未配置
- **模型运行时**: 未配置
- **经验库**: 空
- **Threat Contract**: 未配置
