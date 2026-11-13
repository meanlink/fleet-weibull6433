# 测试记录 · gb-1017 截止月寿命样本归并

本环境无 Docker 守护进程，五服务容器联调未执行；下列结果均为本地真实运行所得，未把容器检查写成通过。

## 已执行（通过）

命令：`PYTHONPATH=backend python -m pytest test-artifacts/gb-1017/tests -q`，结果 **24 passed**。

### test_business.py（17 项，纯业务逻辑）
- 演示台账结构：200 资产、1204 行（6 个月台账 + 2 条晚到 + 2 条截止月后补录）。
- 截止 2026-11 基线样本：AC-8 恰好 100 件、失效 ≥10、无冲突；2026-12 补录全部 excluded。
- 晚到记录按观测月归位：AC-8-007 的 2026-09 补录存在且为 superseded，选中观测仍为 2026-11。
- 提前截止月到 2026-09：选中观测随之改为 2026-09，更晚月份不参与。
- `observed_month` 缺失时按批次来源月归位并计入 late_arrivals。
- 失效后又报在役、里程倒退两类冲突均被识别并使 `run_analysis` 抛错阻止估计；冲突资产整组排除。
- 冲突演示批次在 AC-8 / AC-12 两个口径下分别覆盖 revival 与 mileage_regression。
- 完整分析输出含截止月快照与 β/η/B10/目标里程，且 0 < B10 < η。
- 非法截止月（`2026/11`、`2026-13`）、样本量不足、非法失效标记均被拒绝。
- 失效后持续报失效不误判为复活；同月多条记录取行号更大者且不误报倒退；截止月当天包含、次月剔除；跨月型号不一致列为 model_mismatch 冲突并阻止估计。

### test_integration.py（6 项，FastAPI TestClient + SQLite 临时库）
- `POST /jobs/preview` 只读预览返回样本统计与数据指纹；viewer 可调用。
- 建任务时服务端再次校验冲突，冲突批次返回 422，不产生任务。
- worker 同步执行后 `GET /jobs/{id}/sample` 可回看 200 个资产的完整取舍快照。
- 接入新批次后旧任务（含已批准）置 `stale=true`，批准状态与快照不变；审计含 `job.stale_marked`。
- 重算任务保留原截止月 2026-11，新资产进入样本、截止月后记录仍剔除；旧截止月 2026-09 的任务快照口径保持 2026-09。
- viewer 建任务 403；非法月份 422；无观测月旧台账可接入并按来源月归位。

## 其他已执行检查
- `python -m scripts.seed_demo` 在 SQLite 上成功且二次运行幂等（用户/批次/任务计数不变）。
- 迁移原地升级：对缺少 `stale`、`dataset_checksum` 列的旧 jobs 表执行 `migrate()` 自动补列，旧行默认 `stale=false`、指纹空串。
- 分发归档 JSON 含 `sample_snapshot` 与 `stale` 字段，已批准简报不受失效标记影响。
- 全部后端模块 `import` 无报错；旧 `analyze(records, params)` 入口保持兼容。
- 全部前端 JS 通过 `node --check` 语法检查；工作台与结果页在 completed/stale/failed/running/无快照旧任务五种状态下的渲染冒烟通过（mock API，非浏览器实测）。
- CSV 解析对含/不含 `observed_month` 列两种文件均通过。

## 未执行（环境限制，不得记为通过）
- `docker compose up --build` 五服务健康检查、Nginx 反代与真实浏览器页面截图。
- PostgreSQL 16 下的 JSON 列与 ALTER TABLE 升级（仅验证了 SQLite 等价 DDL）。
- Redis 队列投递与 worker `blpop` 实链路（SQL 排队兜底路径已由直接调用 `worker.execute` 覆盖）。
