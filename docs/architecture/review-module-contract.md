# Review 模块契约（v0.4 开发中）

## 职责与数据所有权

Review 负责用户填写的每日复盘和对既有复盘数据的周视图。它复用 `DailyReview`、`DailyTaskSnapshot`、`Task`、`TaskEvidence` 和 `KnowledgeCard`，不保存第二份任务实体，不新增表或迁移。

```text
DailyReviewWorkspace ──HTTP──> Review routes ──> DailyReview / DailyTaskSnapshot
                                     └────────> review_service.weekly_summary (只读聚合)
Task Evidence UI ──HTTP──> /api/tasks/{id}/evidence ──> task_manager.add_task_evidence
已保存每日复盘 ──用户打开草稿──> KnowledgeWorkspace ──用户保存──> Knowledge Service
```

服务端会话是用户身份的唯一来源。每日复盘和周汇总按 `user_id` 过滤；每条任务快照还须关联到同一用户的真实 Task。周汇总不调用模型，不自动标记任务完成，也不写入任务表。

## 接口契约

### 每日复盘

- `GET /api/reviews/daily` 与 `GET /api/reviews/daily/{review_date}` 读取当前用户记录。
- `POST /api/reviews/daily` 需要登录和 CSRF，按用户与日期幂等更新每日复盘；任务快照只允许引用当前用户未删除任务。同一复盘不能重复引用同一任务。
- 用户填写的 `report_category`、`actual_progress` 和 `evidence_note` 是自报数据。保存复盘不会更改任务状态或自动创建 Task Evidence。
- 非用户任务返回 `TASK_NOT_FOUND`；非法逾期归类返回 `TASK_NOT_OVERDUE`；重复任务返回 `DUPLICATE_REVIEW_TASK`。

### 每周总结

- `GET /api/reviews/weekly?week_start=YYYY-MM-DD` 需要登录，省略日期时使用应用时区的本周周一。
- `week_start` 必须为周一；否则返回 HTTP 422 和 `WEEK_START_MUST_BE_MONDAY`。
- 返回七天的已保存复盘摘要与用户任务快照，以及复盘天数、自报分类计数、自报证据条数。没有记录的日期保留为空日。
- `basis` 明确提示统计来自用户输入，不代表系统独立验证任务结果。接口只读，不创建周回顾记录。

### 任务执行证据

- `POST /api/tasks/{task_id}/evidence` 需要登录和 CSRF；API 路由委托现有 `task_manager.add_task_evidence`。
- Task Service 验证任务属于当前用户且未删除，随后加入 `TaskEvidence`；当前任务为 DOING 时按现有业务规则转为 VERIFY。调用方提交事务。
- 其他用户任务及已删除/不存在任务统一返回 404。证据不会将任务置为 DONE。

### Knowledge Feedback

- 每日复盘 UI 仅从已读取/保存的复盘生成本地可编辑内容，并通过现有 `KnowledgeDraftIntent` 打开 KnowledgeWorkspace。
- 进入工作区不会写数据库；用户可修改标题、摘要、内容和领域。只有用户在 KnowledgeWorkspace 点击保存后，既有 `/api/knowledge` 与 Knowledge Service 才创建知识卡。
- 不发送给模型、不推断未记录的经验、不自动将每日总结存为知识。

## 验证与限制

- 后端测试使用 `tests/conftest.py` 创建的隔离 SQLite，覆盖账户隔离、周起始日期校验、自报数据边界及证据写入归属。
- 前端 Vitest 覆盖每周读取展示与“带入知识草稿但不直接创建知识卡”。
- 当前周总结按每日复盘快照统计；没有每日记录的执行活动不会被补造。Task Evidence API/UI 复用 v0.3.3 已存在的数据结构。
