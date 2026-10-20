# Personal OS R0 方案、风险与验收门槛

状态：实施计划提案；无业务实施授权。日期：2026-10-19。

## 当前约束与第一个切片

当前工作区实际版本基线是 v0.3.3；`develop/v0.4` 有未提交 Review 候选，且其功能/测试本轮没有运行。首要工作是按既有 `review-module-contract.md` 完成 v0.4 验收并由负责人决定发布；本目标升级不得扩大 v0.4 的迁移和业务范围。长期愿景文档不改变 `release_history.json`。

建议第一个获批实施切片为 **R1a：决策案例只读/手工记录骨架**：先验证权限、DTO、稳定 Facade、数据审计、备份计数和双用户隔离；不接 Agent、不调模型、不计算概率、不创建 Task。该切片是架构/数据准入，不宣称完成端到端 MVP。完整的首个业务 MVP 由 R1-R3 合起来达成闭环。

## 完整决策闭环 MVP 验收定义

可复现同一真实 DecisionCase 的全过程：

1. 用户创建 Case，声明问题、目标、时间范围、硬约束/软偏好。
2. 至少两个竞争假设；至少一条人工录入证据/基础率，保留来源、适用群体、时间、范围、核验状态和依赖分组。
3. 使用显式先验/似然确定性算法计算后验，显示中间值、公式版本、输入 hash、限制；无法计算时返回 UNKNOWN/结构化失败，不生成数字。
4. 至少两个 ActionOption，分离情景概率、效用、时间/资金成本、风险、可逆性和机会成本；推荐可以被用户覆盖并记录原因。
5. 用户确认一个 action intent；UI/Agent 提案可编辑；批准前任务数不变；批准后只通过既有 Task Service 产生 Task ID；重复相同幂等请求不重复建任务。
6. 为任务预先定义结果指标与预期；之后记录实际值和真实来源/核验状态，关联 TaskEvidence/Review，但不把自报声明成独立验证。
7. 对比预测和结果，展示偏差；样本不足时不计算校准分数。若样本达到预设范围，可计算 Brier Score 等可复现指标，并标明样本量和适用假设。
8. 任何用户只能读取、更新、审批和检索自己的 case、evidence、snapshot、assessment、intent 和 outcome；未登录/无 CSRF 写请求失败。

## 按依赖排序的施工阶段

以下文件是**候选修改范围**，均待每阶段单独授权。实际开发前应根据当时源代码、Git 状态和接口再核对；不要在本 R0 创建这些文件。

| 阶段 | 候选模块/文件 | 契约与副作用 | 必需测试 | 完成定义 |
|---|---|---|---|---|
| R0（本轮） | `docs/architecture/personal-os-{charter,as-is-audit,target-contracts,r0-roadmap-and-acceptance}.md` | 只记录事实、候选接口和阶段门槛；不改代码/数据 | 无需应用测试；文档路径/链接/状态人工审阅 | 审计证据、未知项、TARGET 与阶段方案完整；审批前不扩边界 |
| R1a Case / 所有权骨架 | 新 `backend/app/services/decision_service.py`、`schemas/decision.py`、`api/decision_routes.py`；`models/entities.py`、Alembic 新 revision（都需先审批）；`main.py` 注册；前端新增 Decision Workspace/API client | 创建/读取本人 DecisionCase；所有写入 CSRF；统一 DTO；Case 建立后只写决策域。预计需新表和迁移，先做不可变历史设计 | service 单测、API auth/CSRF、IDOR/双用户隔离、SQLite 升级/降级/历史记录不变、前端 API 状态 | 两账户隔离通过；migration 从已支持 revision 前滚/恢复验证；既有业务表行数/哈希未变；不调用模型 |
| R1b 假设/来源证据 | decision Service/Schema/API、Decision UI；Knowledge Service 只读 Facade | 假设/基础率/来源/核验信息可编辑；Evidence 与 Knowledge Source 引用分离；重复/相关证据有依赖组 | 来源边界、重复幂等、无来源/过期/范围不匹配、非本人拒绝、未知证据状态测试 | 可从 DTO/DB 复现 Case evidence 列表，未确认来源保持 UNVERIFIED |
| R2 概率与效用 | 独立纯函数模块如 `services/decision_calculations.py`、Schema、版本化 DTO、UI 显示；持久化 BeliefSnapshot/Assessment 由 decision_service 写入 | 计算器无 I/O；先验/似然及效用来源显式；输入 hash/公式版本；不完整数据 fail-closed | 真值表/边界/舍入/相关证据/非互斥假设/概率不合法/成本限制/重放测试；Property tests 若依赖现有测试设施 | 同输入和算法版本确定同输出；结果逐项可解释；不足不返回概率/推荐 |
| R3 授权行动闭环 | Proposal Service、Agent Runner/Tool Registry 仅经稳定接口；Task Service；Decision UI + API | 建立可编辑 ActionIntent/Proposal；明确批准后复核 case/version/hash/来源/用户；幂等调用 Task Service | 未批准无 Task、批准唯一 Task、并发/重复提交幂等、失败不产生假成功、撤销/拒绝和跨用户 IDOR、真实隔离 DB Playwright | E2E 从案例到真实 Task ID；审批前后 DB oracle 正确；Agent 无授权不能调用写工具 |
| R4 反馈和校准 | Review/TaskEvidence Facades、Outcome Service、Decision Workspace、算法模块 | 计划指标和预测留痕；实际值带证据级别；校准仅在足够样本条件下可用 | 自报/系统证据等级、缺失/反事实、Brier Score fixtures、相关性不做因果、重启恢复/用户隔离 E2E | 完整预测→行动→结果证据链；样本不足明确不校准 |
| R5 外部情报接入 | 仅届时新增 `api/intelligence_routes.py`、source schemas / adapter、设置/密钥运维文档；外部采集器独立仓库 | 按 `/api/intelligence/v1/signals` 接受候选；签名、幂等、去重、限流、审核；不直连外部 DB | 签名/重放/篡改/限流/schema 兼容/去重/用户审核门槛/数据删除测试 | API 契约版本可兼容演进；外部数据默认 UNVERIFIED；未审核不可进入后验或触发 Task |

业务版本映射（v0.5+）尚未决定：仓库 roadmap 已将 v0.5.0 规划给 K3、v0.6/0.7 给 Notion；是否调整必须由产品负责人另行批准，本文件不改发布记录或重排 roadmap。

## 最小新增模型判断

- G01 优先映射现有 `UserGoal`/账户设置，先不复制一份 Goal 表。复用映射不能表达硬/软约束、单位、有效期、当前/期望资源时，提出兼容扩展方案后再迁移。
- R1 需持久化 `DecisionCase`、`Hypothesis`、`EvidenceRecord` 和假设-证据评估关系；R2 需追加式 `BeliefSnapshot`、`ActionOption`、`DecisionAssessment`；R3 才需决定是否用独立 `ActionIntent` 表，或能否安全映射 Proposal + `TaskManagerOperation` 并保留 decision-case 引用。R4 需 `OutcomeRecord` 或等效结构化记录。
- 因为证据/假设/概率快照/选项/结果都有多对多关系、所有权、历史和可追溯性，**完整闭环大概率需要增量 Alembic 表**；不是 R0 已批准建表。不要把完整对象压进 Task/Knowledge/JSON 字段，也不添加第二套 Task。
- 迁移前必须审查 revision graph、支持起始 revision、SQLite foreign key/索引、旧数据兼容及备份恢复。任何回滚不得 drop/覆写用户记录；schema downgrade 若会丢新数据则采用前滚兼容或人工恢复备份的方式，而不是自动降级删除。

## 风险和保护措施

| 风险 | 保护/回退 |
|---|---|
| v0.4 未提交候选被架构工作覆盖 | 先保留现有 dirty/untracked 状态；只在独立文件/提交切片施工；不 stash/reset/checkout 覆盖 |
| 个人真实数据跨账户泄漏 | 每个读写/检索/Agent context 都由 Session 注入 user_id；双用户 API + E2E 负向验收；关联 ID 必做 owner 验证 |
| 新迁移损坏历史库 | 隔离备份并验证 SHA/SQLite integrity/FK；复制历史版本库做前滚/恢复实验；正式库前后只读计数与 hash oracle；负责人明确批准维护窗 |
| 不可重复或过度自信概率 | 公式、输入、来源、口径、依赖关系、版本、hash 追加保存；缺信息明确 fail/UNKNOWN；禁止 LLM 做数值权威 |
| 模型提案误写正式 Task | 所有写入继续经过人工确认、Proposal Service、Task Service、CSRF、幂等；单测与 E2E 验证“审批前 Task 数不变” |
| 外发敏感信息或调用成本失控 | 模型为可选；发送前清晰授权/最小上下文；单用户日/周配额、超额拒绝与无 Key 降级；R0 不触发外网/付费调用 |
| 公开仓库暴露凭证/数据/许可不清 | 当前远端 404、remote 状态 UNKNOWN；推送前核实所有权/可见性、全 Git 历史 secret scan、数据库/备份忽略策略、个人文件 staging allowlist 和 LICENSE；不发布个人数据 |
| 回退丢失新业务记录 | 兼容迁移不自动删表；停用新入口可回退应用；备份恢复在隔离路径验收；需要 schema rollback 时先导出保留数据并人工批准 |

## 安全、运行与发布缺口优先级

### P0：进入多用户 Beta 前必须闭合

- 账户开通策略：当前首账号 setup 不是自助注册/邀请；定义受控管理员/邀请方案、账号停用和会话撤销流程。
- 用户数据 ownership/authorization 全面测试：会话、消息、任务、知识、Review、Agent Run/Proposal/ToolCall、Provider credentials、检索 context 和所有关联对象；任何 owner mismatch fail-closed。
- 通用 API 限流/暴力破解保护与稳定部署策略；现有登录失败限制为进程内内存字典，重启/多实例不共享，不能视作多租户级限流。
- 隐私与发布：日志/提示词/密钥脱敏 review；加固错误、cookie/proxy/origin 配置；公开仓库 secret/data/license 扫描与历史审查；远端身份/访问权限确认。
- 配置与安装文档、依赖锁定策略及锁文件一致性；明确 Python/Node/pnpm/uv 版本和启动、停止、健康、迁移、恢复流程。
- 真实双用户隔离测试、API 集成、E2E、安全回归必须在隔离数据库和 Mock Provider 上可重放；生产检查不进入本 R0。
- 无 LLM Key 时核心手工记录/计算闭环可用；AI 功能明确不可用/不发送，不伪造回答；逐用户请求/token/预算硬限制和超额失败行为可测。

### P1：Beta 后续

- Docker/Compose 或其他发行封装（先保留现有 Windows + Tailscale 方案；不预设 Docker 是必要项）。
- 更丰富的资产/能力资源模型、自动导入、可视化预算、复杂风险模型、因果推断和统计校准。
- I01 外部采集器/多来源自动核验、语义搜索；无前置真实闭环数据前不做。
- 多节点/云托管、横向扩展和高可用基础设施。

### 不纳入当前 v0.4

- 新决策/贝叶斯/效用业务代码、决策数据库迁移、Agent 决策自动化。
- Docker、微服务、向量库、消息队列、独立情报采集器。
- Notion 写入或数据同步、新外部付费 API。
- 改写 v0.4 release_history 或宣称 v0.4 已发布/验收。

## v0.4 与后续 Beta 发布门槛

**v0.4 Review & Knowledge Feedback**：仅验收既定契约；相关后端/前端测试和构建实际执行；Review→草稿→用户手动保存端到端通过；双账户隔离/CSRF/证据归属通过；迁移/备份确认无新增结构；Release history 仅在负责人审批发布后更新；不混入新领域。

**未来决策 Beta**：至少完整 MVP 验收标准全部通过；双用户越权测试与来源/检索上下文隔离无失败；纯计算器 fixtures 和可重放版本快照通过；Task 唯一写入口、人工批准和幂等 E2E 通过；迁移、备份、恢复演练通过；无 Key 仍可完成手工 Case→计算→Task→Outcome；配置校验、限流、日志脱敏、错误码与预算限制通过；公开/私有仓库身份、secret scan、个人数据、许可证和发布证据经负责人审核；发布说明清楚列出未实现与限制。

## 学习者工程复习清单（当前真实入口）

| 主题 | 可阅读入口 | 复习问题 |
|---|---|---|
| FastAPI 路由/依赖注入 | `backend/app/main.py`、`backend/app/api/deps.py`、`backend/app/api/routes.py` | Cookie Session 与 CSRF 如何进入 endpoint？哪些业务 SQL 目前仍在 routes？ |
| Pydantic 接口契约 | `backend/app/schemas/api.py`、`schemas/agent.py`、`schemas/knowledge.py` | 哪些输入在边缘拒绝？错误码如何稳定返回？ |
| SQLAlchemy / SQLite | `backend/app/models/entities.py`、`database.py` | user_id/外键/级联/幂等唯一约束如何阻止跨账户写入？ |
| Alembic 与历史兼容 | `backend/alembic/env.py`、`alembic/versions/f1a2b3c4d5e6_p1b_account_and_chat.py`、`f3a4b5c6d7e8_create_knowledge_inbox.py` | revision 如何从旧账户/聊天结构增量迁移？downgrade 会否丢数据？ |
| Task Service / 幂等 | `backend/app/services/task_manager.py`、`docs/architecture/task-module-contract.md` | 事务提交、operation ID 和重复请求怎样保证单一 Task 事实源？ |
| Proposal / Agent 权限 | `backend/app/services/proposal_service.py`、`agent_runner.py`、`tool_registry.py` | 为什么模型草案不是 Task？写工具如何依赖显式授权？ |
| Knowledge / 来源 | `backend/app/services/knowledge_service.py`、`docs/architecture/knowledge-module-contract.md` | 来源 DTO 如何归属当前用户？知识摘录怎样作为不可信上下文？ |
| Review / 证据等级 | `backend/app/services/review_service.py`、`docs/architecture/review-module-contract.md`、`TaskEvidence` model | 自报快照和 TaskEvidence、目标成功、独立验证有何不同？ |
| React 工作区与 API 状态 | `frontend/src/ChatWorkspace.tsx`、`TasksWorkspace.tsx`、`KnowledgeWorkspace.tsx`、`DailyReviewWorkspace.tsx` | 加载/错误/提交后如何避免 UI 假成功？ |
| 测试隔离 | `backend/tests/conftest.py`、`frontend/vitest.config.ts`、`frontend/playwright.config.ts` | 测试如何选择隔离 SQLite？Mock Provider 和真实 E2E 各自能证明什么？ |
| Windows 生命周期 | `scripts/personal_os_manager.ps1`、`scripts/windows_service.py`、`docs/operations/service-lifecycle.md` | 健康、启动、退出、登录自启哪些是脚本逻辑，哪些仍需人工实机验收？ |
| 证据与发布纪律 | `release_history.json`、`CHANGELOG.md`、`docs/releases/v0.3.3-r0-acceptance.md` | 如何区分源码存在、历史测试通过、当前独立复测和正式发布？ |

复习时只读材料，不以文件/测试名称推断功能当前通过；任何运行测试须在未来实施任务中单独授权并确认隔离库。
