# 任务模块契约

本文描述 v0.3.1 正式任务实现；v0.4 Knowledge 仍处于开发阶段。

## v0.4.0 P0.3e 增补（开发中，未发布）

- 手动 `POST /api/tasks` 由 `api/routes.py::create_task` 执行 Session/CSRF/HTTP 参数边界，再调用 `task_manager.create_manual_task`；路由不再直接创建 Task ORM。
- 手动和 AI Proposal 共用 Task Service 的日期核心处理和 Task/TaskStatusHistory 持久化入口。AI Proposal 保持原子审批与事务；手动 API 的可选 UUID `Idempotency-Key` 在 `TaskManagerOperation` 按 `(user_id, client_operation_id)` 唯一。手动无 Header 仍可重复创建。
- AI 输入继续限制模块并按用户/模块/规范化标题去重；手动 API 保持已有字段/初始状态契约与同名任务语义。Knowledge Proposal 可保留未排期。完整调用链、测试证据和差异见 [Task Lifecycle 工程说明](task-lifecycle-engineering.md)。

## 职责与调用关系

任务模块维护用户任务、日期、状态、重复检查、操作幂等和任务查询。认证身份只从服务端会话 `current_user` 获得，客户端不能提交可被信任的 `user_id`。

```text
TasksWorkspace ──HTTP──> FastAPI routes ──> task_manager service ──> SQLAlchemy/SQLite
ChatWorkspace ──HTTP──> conversation/task-link API ──> task_manager query service
Agent Runner ──白名单工具──> Tool Registry ──> task_manager service
Dashboard ──GET /api/task-manager/summary──> task_manager.dashboard_task_rows
GET /api/dashboard ────────────────────────┘（复用同一查询服务）
```

FastAPI routes 负责认证、CSRF、HTTP 参数和响应序列化。Agent 负责语言理解及结构化候选参数；Tool Registry 检查白名单、Schema、用户与 operation 所有权。Agent 不直接写 ORM。`task_manager.create_tasks` / `update_task` 执行业务校验、幂等关联、历史记录和事务 flush；外层请求事务成功提交后，API 才返回正式成功。任务 REST handlers 仍负责 HTTP 边界；手动 `POST /api/tasks` 已委托 Task Service 创建，其余既有 CRUD 入口保持现状。

## 命令与接口

### 任务管理命令

`POST /api/task-manager/chat`（已登录、CSRF）请求：

```json
{
  "message": "开发个人网站",
  "mode": "create",
  "conversation_id": null,
  "operation_id": "UUID"
}
```

认证用户由会话注入。`conversation_id` 可选但若提供必须属于当前用户、tasks 领域且未归档/删除。`operation_id` 是同一请求网络重试时复用的幂等键；新指令使用新 UUID。服务端将模式、会话和输入摘要绑定到该键，不能跨用户、跨指令或跨会话重放。

- `create`：该专用界面按钮提交本身就是本次创建授权。读取任务后调用受 Schema 限制的 `create_tasks`；只创建 TODO。模型省略非关键领域时按任务文本推断，未知领域及不一致日期明确拒绝。默认日期为 Asia/Shanghai 当天，不生成钟点。
- `plan`：只读；只提供查询工具，不提供 prepare/write 工具。
- `modify`：仅处理指令明确指向且归属当前用户的任务。删除仍先进入待确认状态。
- `auto`：为旧客户端兼容保留，不作为当前 UI 的创建授权。

成功或可重放响应包含 `operation_id`、`status`、`mode`、`tasks`、`created_tasks`、`updated_tasks`、`task_ids`、`actions`、`run_id`/`audit_run_id`、`error_code`。异步错误使用 HTTP `detail.code/message/request_id`；状态查询 `GET /api/task-manager/operations/{operation_id}` 返回当前用户可见的持久化操作状态。`started` 表示处理中，`completed` 表示结束；明确失败保留错误码。相同已完成键返回原任务 ID，数量不匹配整批不写入。

### 任务读取与 CRUD

- `GET /api/tasks`：按认证用户查询，可选 `q`、`module`、`status`、`timeline`、归档/回收区筛选。
- `GET /api/tasks/{task_id}`：当前用户任务详情、证据、状态/日期历史及关联会话。
- `POST /api/tasks`：手动创建经 `task_manager.create_manual_task`；`PATCH /api/tasks/{task_id}` 等其他手动 CRUD 仍由现有路由处理；DONE 仍受证据门槛保护。
- `POST /api/tasks/{task_id}/evidence`：路由负责登录态、CSRF 和 DTO；`task_manager.add_task_evidence` 校验任务归属/删除状态、创建执行证据，并在任务为 DOING 时按既有规则转入 VERIFY。请求事务由路由提交；证据不独立创建或复制任务。
- `POST /api/tasks/{task_id}/links`、`GET /api/tasks/{task_id}/links`：与会话/消息双向关联，验证两端所有权。
- `GET /api/task-manager/summary`：只读今日、完成、逾期、未排期任务摘要。
- `GET /api/dashboard`：Dashboard 综合数据；任务分桶复用同一 `dashboard_task_rows` 服务，不由模型计算。

Agent 的 `get_active_tasks` 和 `get_conversation_tasks` 也从真实数据库读取；后者限定当前用户、当前有效会话与用户拥有的任务，并排除删除任务及健康/财务内容。

## 状态、日期、事务和幂等

任务状态仍为 `TODO / DOING / BLOCKED / VERIFY / DONE / CANCELLED`。AI 创建只写 TODO，不生成执行证据；DONE 需通过既有证据规则。AI Task Manager/Proposal 的重复检查以当前用户、未删除任务的规范化领域和标题为准；重复项返回已存在 task ID，不复制。手动 `POST /api/tasks` 保留原有不自动去重行为。

AI Task Manager 缺少日期时使用 `settings.app_timezone`（默认 `Asia/Shanghai`）的本地日期；Knowledge Proposal 未指定日期时保留未排期；手动 API 沿用原规则补齐本地日期。只提供日期不推导时间。起止日期按含首尾的天数校验。批量创建在一个请求事务中执行；任一校验/提交失败不得把部分批次报告为成功。日期或状态修改保留历史记录。

网络重试沿用原 operation ID。新用户提交产生新 ID。运行中的操作只查询状态；完成时回放原结果；失败后用户修订/重试使用新 ID。超时恢复先检查持久化 action 记录再判定是否提交。

## 失败码

- `TASK_PARSE_FAILED`：结构化任务缺少可用标题或解析结构不合法。
- `TASK_DOMAIN_INVALID`：领域不在受支持集合。
- `TASK_DATE_RANGE_INVALID`：日期与持续天数无效/不一致。
- `TASK_COUNT_MISMATCH`：模型任务数量与用户明确数量不一致；整批拒绝。
- `TASK_VALIDATION_FAILED`：其他任务业务规则未通过。
- `COMMIT_FAILED`：外层数据库事务提交失败；回滚后记录安全失败状态，调用方先查操作状态再决定是否重试。
- `TASK_WRITE_NOT_AUTHORIZED`：模式、会话/用户归属或操作凭证不匹配。
- `OPERATION_REQUEST_MISMATCH`：同一幂等键被用于不同输入/模式/会话。
- `TASK_OPERATION_INTERNAL_ERROR`：未分类服务器事务错误；详情只进入脱敏审计。

工具审计保存失败码、工具名、Run 和耗时，不保存完整模型输入、密钥或 Cookie。之前生产记录只保存通用 `INVALID_TOOL_ARGUMENTS`，无法事后还原当时不合格字段；本次修复后保留安全的具体分类。

## Chat 与 Dashboard 边界

ChatWorkspace 通过受控任务关联 API 或 Agent 只读工具查询/打开任务，不拼 SQL、不接受网页文本作为写入授权。只有专用任务管理器的当前用户本次 `create` 提交可创建；普通聊天仍需独立授权流程。Dashboard 只请求摘要和列表，不调用 DeepSeek 决定数量/状态。

## 知识模块关联

K2-B 新增独立 `knowledge_service` 和 `/api/knowledge`。Task 模块仅暴露 `knowledge_task_reference(db, user_id, task_id)` 稳定只读 DTO，验证任务归属、未软删除并返回 ID/标题/领域/状态/归档标志；Knowledge 通过该 Facade 校验并写自己的 `knowledge_task_links`。TaskWorkspace 通过 `GET /api/knowledge?task_id=...` 读取关联卡片并跳转。Knowledge 不导入 Task ORM，不修改任务表或其状态；详情见 `knowledge-module-contract.md`。

## 本次真实故障与修复位置

生产只读审计显示最近两次任务操作在 Run 27/28：`get_active_tasks` 成功，`create_tasks` 在参数校验边界被拒绝，操作记录为 `TASK_TOOL_FAILED`，正式任务没有新增。旧实现把所有 `ToolArgumentsError` 折叠成 `INVALID_TOOL_ARGUMENTS`，且 Agent 再折叠为 `TASK_TOOL_FAILED`，所以历史记录无法证明究竟是任务数量、领域还是日期字段不合格；不得把某一种推测冒充已证实根因。

修复位置：`services/tool_registry.py` 为任务 Schema 错误记录安全、可诊断的分类；`services/agent_runner.py` 将必需工具的真实失败码向上保留；`services/task_manager.py` 的工具 Schema 发布合法领域枚举，并对模型漏填领域按标题/描述执行保守默认推断（“个人网站”归 `projects`）；`api/routes.py` 提供任务摘要服务复用、稳定的任务操作字段及明确的事务提交失败状态。模拟 DeepSeek 的跨层测试覆盖 HTTP → Agent 工具 → 实际任务 Service → 独立 SQLite commit → 真实 task ID 回读。

## 删除生命周期接口（v0.3.1 RC）

任务软删除和恢复由 `DELETE /api/tasks/{task_id}` 与 `POST /api/tasks/{task_id}/restore` 提供；查询最近删除使用 `GET /api/tasks?include_deleted=true`，详情必须明确使用 `GET /api/tasks/{task_id}?include_deleted=true`。软删除项通过 `GET /api/tasks/{task_id}/permanent-delete-preview` 预检，再以 CSRF 保护的 `POST /api/tasks/{task_id}/permanent-delete` 提交标题确认。路由只负责认证、HTTP/错误映射，依赖策略、事务与幂等由 `task_manager` Service 负责。每日结算快照或执行中的任务操作阻止清理；任务专属证据/状态/日期历史和会话链接随任务清理，独立聊天、提案及历史备份保留。已清理 ID 以最小回执标识，防止恢复；服务层显式递增新任务 ID，避免复用回执 ID。详见 `deletion-lifecycle.md`。

外部模块仍只使用公开 Task API/只读查询服务，不直接导入任务 ORM。该增量新增永久删除预检与执行端点，以及详情的显式 `include_deleted` 查询参数；既有活跃任务 API 语义不变。
