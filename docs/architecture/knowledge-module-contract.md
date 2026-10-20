# Knowledge 模块契约（v0.3.3 K2-C，开发中）

本文件覆盖 v0.3.2 已发布的本地知识收集箱，以及 v0.3.3 K2-C 的检索、授权聊天上下文和经确认的任务转换契约。Alembic revision `f3a4b5c6d7e8` 是数据库结构版本，不是产品发布版本。K2-C 不新增业务数据表。

## 职责边界

Knowledge 负责用户明确保存的知识卡片、领域/类型/审核状态、标签、来源证据、任务关联、搜索筛选、归档、回收、恢复、清理和单卡导出。它不负责聊天持久化、任务创建、Agent 编排或 Notion 同步。七个工作领域共用同一组表和 Service，通过 `domain` 分类，不存在七套知识库。

```text
ChatWorkspace --用户点击“保存为知识 / AI 提炼”--> KnowledgeWorkspace
KnowledgeWorkspace --显式 DeepSeek 请求--> /api/knowledge/refine-from-message --> Knowledge Refinement Adapter
KnowledgeWorkspace --HTTP/DTO 保存--> /api/knowledge --> knowledge_service
ChatWorkspace --知识开关 + 数据授权--> knowledge_service.retrieve_cards --> Agent Runner / DeepSeek
Task Manager --选中卡片 + 单独授权--> knowledge_service.get_active_cards --> prepare_tasks --> 用户确认 --> Task Service
knowledge_service --任务只读 Facade--> task_manager.knowledge_task_reference
```

路由负责 `current_user`、CSRF、输入校验/错误映射；Service 负责业务所有权、卡片/标签/来源关联与事务。任务引用通过 Task Service 的稳定 DTO 验证，不直接依赖 Task ORM。知识检索是只读操作；知识卡片通过用户每次主动开启的范围进入单次 Agent Run，不成为持久模型记忆。专用 Task Manager 流程将草案放在现有 `TaskManagerOperation` 预览记录；Agent `propose_task` 流程将草案放在现有 `AgentProposal` PENDING 记录。两者都只在明确确认后通过既有 Task Service 写入真实任务并关联知识卡片。

## 数据模型

- `knowledge_cards`：用户、标题、摘要、Markdown 正文、领域、类型、审核状态、置顶、归档/删除/清理时间和创建/更新时间。
- `knowledge_tags`、`knowledge_card_tags`：用户级标签及卡片多对多关系。
- `knowledge_source_references`：卡片到用户消息、会话、Agent Run 及实际来源 URL/标题/获取时间/抓取状态的证据 DTO。聊天/Run 标识在来源表中为稳定值而非外键，来源快照不随原始聊天清理而造成级联丢失；创建关联时仍验证当前用户拥有已完成的助手消息、会话和 Run。
- `knowledge_task_links`：卡片与用户拥有任务的唯一关联；复合唯一约束防止重复。
- `knowledge_audit_events`：创建、修改、归档、移入回收、恢复、任务关联和清理事件。永久清理保留最小 tombstone 与审计，不保留可恢复正文。

知识类型为 `Concept / Procedure / Practice / Question / Insight`，审核为 `DRAFT / REVIEWED`。证据类型是来源分类，不是自动升级的可信等级；已抓取正文不表示结论已被核验，模型自述不能产生 `PRACTICE_VERIFIED`。

## 公开 API

所有读取要求登录，所有写操作要求当前用户 Session 与 CSRF；客户端不能指定可信 `user_id`。

- `GET /api/knowledge`：`q/domain/knowledge_type/review_status/tag/task_id/include_archived/include_deleted/limit` 筛选。
- `POST /api/knowledge/retrieve`：CSRF 保护的检索接口，输入 `{query,domains?,top_k?}`；登录用户范围内，按标题（权重 6）、摘要/标签（权重 4）、正文（权重 1）逐词计分，按分数、置顶、ID 稳定排序，最多返回 5 张。返回 `{status:completed|empty,query,items:[真实卡片 ID/标题/摘要/domain/tags/匹配词/有限 excerpt/source references]}`。仅检索未归档、未删除且未清理卡片；无匹配返回 `items:[]`，不合成引用。搜索最多扫描 500 张候选卡片，摘要片段最多 640 字符；它是可解释的 SQLite 词项检索，不是语义搜索。
- `POST /api/knowledge`：创建卡片；可选 `conversation_id/message_id`，由服务端校验属于当前用户的已完成 assistant 消息。
- `GET /api/knowledge/{id}`、`PATCH /api/knowledge/{id}`：详情和编辑。
- `DELETE /api/knowledge/{id}`、`POST /api/knowledge/{id}/restore`：回收与恢复。
- `POST /api/knowledge/{id}/tasks/{task_id}`、`DELETE ...`：幂等关联/解除经 Task Facade 校验的任务。
- `GET /api/knowledge/{id}/export?format=markdown|json`：仅导出这一张卡及其来源/任务 ID，不包含原聊天正文、任务详情、Agent 审计或整个数据库。
- `GET /api/knowledge/{id}/permanent-delete-preview`、`POST .../permanent-delete`：先软删除，再以 CSRF 和标题二次确认清空卡片正文/标签/来源/任务关联，保留 tombstone 与审计；既有备份仍可能留存先前正文。

DTO 使用 `backend/app/schemas/knowledge.py` 的 `KnowledgeCreate / KnowledgePatch / KnowledgePurgeInput`。无权或不存在统一返回不可区分的 404，错误包含稳定业务码。

## 聊天与来源

助手消息栏的“保存为知识”只把消息文本放入可编辑草稿，不调用模型。单独的“AI 提炼”按钮也只打开编辑器；用户再次点击“AI 提炼标题、摘要和正文”才会产生一次模型请求。`POST /api/knowledge/refine-from-message` 接受 `{conversation_id,message_id,selected_text?,sensitive_data_consent}`，必须通过 Session 与 CSRF；后端验证当前用户拥有未删除会话、已完成 assistant 消息，且选中文本必须是原文子串。请求只含该条消息或选中片段，不携带聊天历史、标签、目标或网页内容；不注册搜索工具、不自动重试。健康/财务领域或命中敏感主题需用户额外确认；疑似密码、API Key、Token 等凭证直接拒绝发送。成功响应为 `{candidate:{title,summary,content_markdown,limitations},run_id,usage}`，由严格 Pydantic Schema 校验，模型无法返回来源 URL 或可信证据字段。错误返回稳定业务码，候选失败时编辑草稿不变。

提炼使用已配置的 DeepSeek Provider 凭证/模型及 Agent Run、AgentModelUsage 审计；一次请求最多记录一个模型用量，不保存 Prompt/原文到审计详情。用量记录实际 Provider 返回 Token；当前不按不确定/易变的单价推算费用，用户触发一次请求即可能产生供应商费用，失败后不自动重试。候选仅存在前端内存并显示为“尚未保存或审阅”；用户需明确采用到草稿，再使用既有 `POST /api/knowledge` 保存。Adapter 不访问 Knowledge ORM，提炼不创建卡片、不覆盖 domain/tags/type/review_status、不更改原消息，也不提升来源证据。卡片保存时仍由 Knowledge Service 从原消息对应的真实 Run/Tool Audit 推导来源。未经持久化的聊天消息不能调用该提炼接口，但仍可像以前一样手动保存独立草稿。

一条消息可多次独立保存为不同卡片。提炼失败或用户放弃候选时，原草稿保持不变；异步结果只进入独立候选预览，不会自动覆盖编辑字段。

来源当前保留 conversation/message/run 的稳定 ID 和 URL 快照，不建立独立全局 Source 表；不宣称网页事实已人工核验。聊天被删除/清理不触发知识级联删除。

## K2-C 检索、聊天引用与任务确认

- ChatWorkspace 的“使用我的知识库”开关默认关闭。关闭时请求不会调用知识检索；开启时须同时提交本轮 `consent_granted=true`，并可按知识领域缩小范围。后端仅在显式开关为真时调用 `retrieve_cards`，由认证 Session 的 `user.id` 提供所有权范围；CSRF 与认证依赖沿用 `/api/agent/chat`。
- Agent Run 仅接收最多 5 个卡片的最多 640 字符摘录，并附每卡最多 5 条服务端真实来源 DTO（ID、证据类型、标题、URL、获取状态）。卡片内容以标注为“不可信参考数据”的 user 消息传入；系统规则要求忽略摘录中的指令、区分知识事实和有限推断。来源 ID/标题/URL 只能来自当前用户的检索 DTO，模型自述不能成为 UI 来源。知识内容不注册为 Agent 工具，不可授权它执行工具或更改系统提示。Run 审计只记录卡片 ID 和 completed/empty 状态，不记录摘录正文。
- Agent Runner 对当前用户消息执行有界意图识别：`KNOWLEDGE_QA` 用于知识提问、总结、分析和“下一步”建议；`TASK_DRAFT` 仅匹配明确要求任务草案的表达；`TASK_CREATE` 仅匹配明确创建任务的表达。`不要创建/生成任务` 等否定授权优先阻止任务意图。聊天历史和知识摘录不参与当前轮授权判断，避免残留会话内容授权新副作用。
- `propose_task` 仅在明确 `TASK_DRAFT`/`TASK_CREATE` 且当前调用明确授权个人数据时才出现在工具清单；执行器再次检查授权，未通过时以 `TASK_PROPOSAL_NOT_AUTHORIZED` 拒绝并写入安全审计。该工具只建立既有 PENDING 提案，不等于创建正式任务；正式任务仍须经 Task Manager 现有授权/审批流程。知识问答不运行提案兜底检测，否定式“没有 PENDING 提案”也不再被判为成功声明。
- Provider Function Calling Schema 和 `propose_task` 执行验证都由 `PlanItem` / `ProposeTaskArguments` Pydantic 契约生成，字段新增必须同步落入这一个 schema。`goal_id` 可省略或为 `null`；不允许模型把 `existing_task_id` 解释为父任务；`source_task_ids` 仅表示已存在任务来源。内部知识来源必须使用检索实际返回的 `knowledge_card_ids`，无外部 URL 时 `sources` 可为空。提案 DTO 没有日期字段，未知字段仍拒绝，因此未指定日期不会变成排期。
- 每条知识辅助 `propose_task` 生成独立可编辑的 `AgentProposal.status=PENDING` 记录，并保留真实卡片 ID。该阶段不写入 `tasks`，也不调用会创建真实任务的路径；用户编辑仍更新提案。只有明确批准提案后，Proposal Service 才创建既有 `TaskManagerOperation` 幂等键并调用 `task_manager.create_tasks`，然后由 Knowledge Service 幂等建立卡片-任务关联。操作键由提案 ID 和条目序号稳定生成；重放已批准提案不会重复创建。来源卡片在批准时再次验证所有权和可用状态；删除/归档的卡片阻止批准。专用 Knowledge Task Manager 预览继续使用自己的 `TaskManagerOperation` 幂等确认流程。
- 工具参数错误审计只存 `error_code`、验证阶段、字段路径、预期类型/约束、实际类型和可恢复标志，不写原始工具参数或知识正文。旧审计未持久化失败参数时，不能事后推断哪个字段触发验证。
- Knowledge Retrieval 的 completed 只表示检索调用完成。Agent Runner 仍须取得非空最终回复才将 Run 标记为 completed；空的模型输出将导致 `EMPTY_MODEL_OUTPUT` 和 failed Run。
- 知识检索为空时，Agent Runner 明确要求说明资料不足，不生成知识卡片名称、来源或实时信息声明；聊天响应 `knowledge_status=empty` 且来源列表为空。
- 聊天响应及持久化消息 trace 只呈现当前用户仍可使用的真实卡片 ID、标题、摘要和来源引用；点击引用打开对应卡片。检索为空时状态为 `empty`，卡片后来归档、删除或清理时 trace 不再展示该卡片。知识引用和外部 URL 证据分开呈现；模型推断不会提升为知识来源或外部搜索事实。
- 从已引用知识创建任务草案须显式提供 1–5 个当前用户卡片 ID、`knowledge_consent_granted=true`、启用的聊天持久化与 `mode=auto`。草案经现有 `prepare_tasks` 工具保存在 `TaskManagerOperation`，不写 `tasks` 表；日期必须由用户明确提供，否则保持空。UI 可编辑目标、说明、领域、估时、日期和验收标准。
- 只有用户在 UI 点击“确认并创建真实任务”才提交确认请求。后端重新校验来源所有权/活动状态和候选 DTO，然后调用 `task_manager.create_tasks`。成功返回由数据库读取的真实任务 ID，并经 Knowledge Service 建立幂等卡片-任务链接。相同操作 ID 和请求指纹可安全重放；不同请求复用 ID 返回 409。Provider 失败、草案未确认、来源不可用或没有知识结果均不产生正式任务。
- 自动化测试使用隔离 SQLite；真实模型调用可能计费，测试默认模拟 Provider。R0 已通过真实浏览器 UI + HTTP + 隔离 SQLite/Mock Provider 验证保存知识、检索引用、草案编辑、审批、拒绝、幂等和来源关联；这不代表真实付费 Provider 质量或正式库审批已验收。

## 搜索、权限与隐私

每次 list/detail/source/task/export/retrieve 查询均限定当前用户。健康与财务类别仍受同一逐次聊天授权约束，知识内容不会自动发送给 DeepSeek/Tavily/Notion。用户须明确点击保存；聊天中的模型文字本身不是写入授权。

## K2-D 接口预留

K2-D 可把真实 Daily Review 作为来源建立可编辑知识草稿，仍需用户保存/审阅，不能把模型总结认定为真实实践成果。本轮没有复盘自动提炼。

Notion 四库及任何同步状态/凭证/API 均未实现。

## 迁移、备份与验收

增量迁移 `f3a4b5c6d7e8` 从 `e8f9a0b1c2d3` 创建六张新表，不改动旧业务记录；本地备份摘要纳入知识卡、来源、任务关联计数。测试在隔离 SQLite 上覆盖 HTTP CRUD、用户隔离、真实消息/Run 来源、重复任务链接、删除恢复清理、导出和聊天/任务保留；前端 Vitest 覆盖聊天消息草稿保存。搜索是普通 SQL 模糊匹配且最多返回 100 条；尚无分页、FTS、去重建议、批量导入、移动端真实设备验收、Notion 与异地备份。
