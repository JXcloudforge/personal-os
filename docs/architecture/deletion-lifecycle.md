# 持久化对象删除生命周期

本文为 v0.3.1 正式实现契约；v0.4 Knowledge 生命周期由 K2-B 增量补充。

## 状态语义

持久化业务对象的通用语义是 `ACTIVE → SOFT_DELETED → RESTORED` 或 `SOFT_DELETED → PURGED`。归档是单独的可见性状态，不是删除。最近删除中的项目应能恢复，并提供经确认的永久清理路径；各对象按其依赖关系执行自己的清理策略。

| 对象 | 软删除/恢复 | 永久清理 |
|---|---|---|
| 正式任务 | `DELETE /api/tasks/{id}`；`POST /api/tasks/{id}/restore`；最近删除列表用 `include_deleted=true` | `GET /api/tasks/{id}/permanent-delete-preview` 后，`POST /api/tasks/{id}/permanent-delete`，须 CSRF、标题确认及事务内重新检查 |
| 普通会话 | `DELETE /api/agent/chat/conversations/{id}`；`POST .../{id}/restore` | `POST .../{id}/permanent-delete`，界面二次确认与输入标题 |
| 任务管理员会话 | 复用 Conversation API 和任务管理员回收区 | 同普通会话；关联的正式任务独立保留 |
| 提案 | `DELETE /api/agent/proposals/{id}`；`POST .../{id}/restore` | `POST .../{id}/permanent-delete`，未决提案须先取消/拒绝；仅清理提案正文 |
| 知识卡片（K2-B） | `DELETE /api/knowledge/{id}`；`POST .../{id}/restore`；`include_deleted=true` | 先软删除，再以预检及 CSRF/标题二次确认清理卡片正文和卡片自身关联；保留最小 tombstone 与操作审计 |

## 任务清理依赖与保留

任务清理仅适用于已软删除、属于当前用户且标题二次确认匹配的任务。预检报告证据、状态历史、日期历史和会话关联数量。事务删除任务及其专属证据/状态/日期历史和任务—会话关联；独立会话、消息、提案、其他任务和备份不删除。任务 ID 的最小不可恢复回执及脱敏安全审计保留，用于拒绝伪恢复、区分重复请求；任务创建服务从现存任务 ID 与清理回执 ID 的最大值递增分配，避免 SQLite 删除最大 rowid 后重用已清理 ID。不保留可重建任务正文的回执内容。

每日结算快照是不可变事实，引用任务时阻止永久清理，返回 `409 TASK_PURGE_BLOCKED`；不得级联删除、改写快照或制造半清理状态。执行中的任务操作也阻止清理。SQLite 外键约束和事务负责并发期保护；完成后检查 `foreign_key_check`。

历史备份是独立副本，永久删除不回写或清理旧备份，旧备份可能保留删除前内容。

## 权限、错误与确认

所有列表/详情按当前用户隔离；读操作需登录，修改操作需 CSRF 与所有权检查。不存在或不属于当前用户的任务不泄露差异。已清理任务返回 `410 TASK_PURGED`；活跃任务跳步清理返回 `409 TASK_MUST_BE_TRASHED`；标题不符返回 `422 CONFIRM_TITLE_MISMATCH`；存在不可变依赖返回 `409 TASK_PURGE_BLOCKED`。清理回执使成功重试幂等。前端只在服务端确认后更新回收区。

会话与提案使用各自的清理策略，不能将通用 CASCADE 套用于所有业务对象。清理不得由模型文本单独触发，必须来自专门的用户确认行为。

知识卡片清理只解除本卡的标签、来源快照和任务链接，不删除原任务、对话、消息或 Agent Run；卡片审计事件保留。来源记录保存稳定的会话/消息/Run 标识，不用数据库级外键级联原始会话。独立历史备份仍可能保留清理前正文。详见 `knowledge-module-contract.md`。

## 验收依据与限制

任务生命周期由 `task_manager` Service 和 `/api/tasks` 路由实现；普通/任务管理员会话复用 Conversation 路由；提案由 proposal 路由管理。自动化回归验证用户隔离、CSRF、恢复、阻塞、幂等及关联会话保留。尚未实现的 Knowledge 对象不在本契约覆盖范围。
