# Personal OS 更新记录

产品版本以 `release_history.json` 为唯一发布记录；Alembic revision 仅代表数据库结构版本。

> **发布候选状态（2026-10-20）：** 当前源码包版本为 `0.4.0`，`release_history.json` 将其标记为 `source_candidate`；v0.3.3 的正式历史记录保留。此状态不是 GitHub Release、发布 Tag 或正式服务升级声明。R4a-3 安装/回归结果见 `docs/releases/RELEASE_CANDIDATE_CHECKLIST.md` 的最终候选附录；独立负责人审批报告不随源码分发。

- R4a-3 候选复验：锁定依赖安装与锁检查通过；后端 pytest `195 passed, 23 warnings`；前端 Vitest `39 passed`；TypeScript 通过；隔离 Playwright `5 passed`。一次旧的 Engineering Journal 版本文案断言先失败，更新为 Source Release Candidate 后通过。运行范围为同一 Windows 主机、合成数据和 Mock Provider；空库 Alembic 迁移、本机初始化管理员及交互式邀请码 CLI 也通过。Vite 仍有 >500 kB chunk 提示；最终逐项状态见 Release Candidate Checklist。

## v0.3.2 — 已正式发布（2026-10-14）

### Fixed

- 管理器固化用户实测的 Start-Process + 项目 pythonw.exe + 原入口；重新核验后精确停止监听 PID，拒绝外来/不健康实例重复启动，当前 Session 串行化启动。
- UTF-8 BOM 修复 Windows PowerShell 5.1 直接加载中文脚本解析错误。

### Improved

- 提供非交互 Start/Stop/Restart/Status，登录自启可复用同一路线；本轮未更改正式任务。
- 系统信息展示 v0.3.2 正式版本及五项可展开经验。

### Documentation

- [完整生命周期复盘](docs/postmortems/2026-10-13-background-lifecycle.md) 保留失败路线、成功实验和未知根因；运维指南记录 pythonw.exe + Start-Process 的复用方法。

### Known Limitations

- 本次正式发布基于用户确认验收完成；该确认不代表 Codex 执行了桌面操作或 Windows 生命周期实测。
- 登录自启、Windows 重启及异常恢复未在本轮执行，仍待单独验收。
- 分离进程不受短期登录任务持续监督；注销、睡眠、关机与异常恢复不保证。

## v0.3.3 — Knowledge Intelligence（本地验收归档；未完成远端发布）

- 扩展既有 Knowledge Service，按认证用户检索活动卡片，支持领域过滤、可解释词项排序、Top-K、有限摘录和真实来源引用。
- ChatWorkspace 增加默认关闭的知识库授权开关；知识以不可信参考数据进入既有 Agent Runner/Provider 上下文，聊天显示真实卡片来源。
- 复用既有 Task Manager 预览与 Task Service：知识辅助任务可编辑、日期不推断，须用户明确确认后创建并建立来源链接；操作可幂等重放。
- 修复知识问答被任务提案兜底文案覆盖：Agent Run #32 的只读审计确认仅执行知识检索、没有提案工具调用或任务操作；否定式 `PENDING` 文案会被宽泛正则误识别为提案声明，原模型 completion 未留存，无法确定兜底覆盖前的原始回答。
- 增加 `KNOWLEDGE_QA / TASK_DRAFT / TASK_CREATE` 有界意图识别；仅当前消息明确请求任务操作且用户授权个人数据时才暴露 `propose_task`，工具执行层再次校验。知识摘录同时携带真实来源元数据，模型无法调用任务工具时仍由后端拒绝伪造调用。
- 修复 `propose_task` 参数契约与知识引用：Provider Function Schema 与执行器共用 `PlanItem` DTO；`goal_id` 可省略或为 `null`，内部来源使用真实 `knowledge_card_ids`，不要求伪造 URL，日期不得由模型补入。用户可编辑 PENDING 草案；确认时经既有 Task Service 幂等创建未排期任务并关联知识卡。
- 为工具参数验证失败记录脱敏的 `validation_stage / field_path / expected_type_or_constraint / actual_type / recoverable` 诊断，并回传安全修复提示；不保存无效调用的原始参数。
- Run #34 的两次 `propose_task` 失败审计未保留参数（`arguments_summary={}`）；可确认失败发生于本地工具参数验证且没有进入工具副作用，但无法追溯具体字段，故不将 `goal_id=null` 记录为历史根因。
- 知识检索为空时明确提示证据不足且不伪造引用；只有取得非空最终回复 Run 才报告为 completed。
- 前序离线验证：全量后端回归 `124 passed, 0 failed`；API Smoke `14 passed`；新增假 Provider HTTP 链路验证提案拒绝、来源传递和回复持久化。未调用付费 Provider，真实知识卡回答质量仍待用户验收。
- 本轮 `propose_task` 专项完成后，全量后端回归 `126 passed, 0 failed`；定向工具与意图测试 `31 passed`；前端 Vitest `26 passed`、TypeScript `tsc -b` 和 Vite 生产构建通过（有 chunk size 提示）。自动化使用隔离 SQLite / Mock Provider，没有调用付费模型。
- R0 隔离浏览器验收 `<TEST_RUN_ID>` 通过：经 UI 保存测试知识、授权检索、生成 PENDING 草案、编辑并持久化、明确批准创建真实任务、重复批准保持单条任务、拒绝另一草案；同时核验来源关联、任务详情和用户/AI 消息剪贴板复制及权限失败反馈。
- 最新隔离数据库 `integrity_check=ok`、`foreign_key_check=0`；批准草案对应真实 Task #1 和知识来源卡片 #1，未排期字段为空；拒绝草案没有写入真实 Task。
- 本轮回归：后端 `127 passed`；前端 Vitest `28 passed`；`pnpm build` 内含 TypeScript `tsc -b` 与 Vite 生产构建通过（保留 chunk >500 kB 提示）；Playwright `1 passed`。使用独立 SQLite 与本地 Mock Provider，未调用付费 API。
- 正式库只读检查通过，用户任务数保持 8，知识卡 1 张，提案 #4/#5 均保持 `PENDING`；未对其审批或写入。该检查不等同于正式库实机审批验收。
- E2E 截图、HTML 报告、结构化隔离库审计及失败/修复记录保存在 `<TEMP_EVIDENCE_DIR>`。隔离数据库、后台测试进程和 bootstrap 临时页已清理。
- 本地发布提交和 annotated tag 保留在 `1569b04` / `v0.3.3`。拟定 GitHub 仓库已创建为 Private 并配置 `origin`，但本机 Git HTTPS 传输无法连接 GitHub；本地验收后未完成 push 或 GitHub Release。
- 正式服务已通过 PersonalOS Manager 重启，健康与数据库接口通过、API 版本为 0.3.3。生产登录态下设置页、任务/知识读取和消息复制未验收；重启后有一条与显式 Manager 重启时间相邻的 `previous_run_missing_exit` ERROR，不能表述为无异常发布验收。

## v0.4.0 — Source Release Candidate（尚未发布）

### 各开发阶段验收记录（分阶段证据；不替代最终发布候选复验）

- Review 复用现有数据结构，提供每日记录、周汇总、Task 摘要只读 Facade 与用户归属校验；Review 自报内容不会自动成为正式 TaskEvidence。
- 修复 Daily Review 异步读取与用户编辑竞态，增加可控请求顺序的前端测试；隔离 Playwright 验证 Review 保存、刷新持久化和跨用户任务访问拒绝。
- 在没有管理员角色模型时，默认拒绝 Web 端实例级备份、备份列表和整库迁移导出入口；CLI/后台备份路径按 P0.3b-1 验收继续保留。
- Tool Registry 校验 Agent Run 的内存所有者与数据库持久化所有者必须匹配；不匹配时在工具读取、执行和 ToolCall 写入前拒绝。
- 普通 Proposal 和 Knowledge Proposal 的正式任务创建统一经过 Task Service；人工批准、任务来源关联、状态历史、Task Manager 幂等操作与审计保留在同一事务内。失败先回滚，Proposal 保持待处理状态。
- 手动 `POST /api/tasks` 已委托 Task Service 写入；手动和 AI Proposal 共用正式 Task 持久化/日期计算/状态历史入口。可选 UUID `Idempotency-Key`：相同 Key/内容回放原任务，不同内容返回 409；无 Header 的每次请求仍创建新操作，允许重复同名任务。AI Proposal 保留原子审批、事务回滚和幂等控制；人工与 AI 的状态/领域校验、去重及审计差异有意保留。用户范围唯一键、不同 Proposal 键、重复/并发审批及失败回滚通过隔离测试验证（后端 142 passed，前端 35 passed，Playwright 2 passed；仍待 v0.4.0 发布验收）。
- P0.4b-1 新增 `Invitation` ORM 与 Alembic `f4b5c6d7e8f9`：只存随机邀请码的 SHA-256 摘要，带过期、领取、撤销元数据和唯一约束；条件更新保证单次原子领取。新增 `invitation_service` 事务契约（不自行提交）及本机 `scripts.manage_invites` CLI：创建仅在交互式终端显示一次，列表/详情不暴露令牌，支持状态查询与撤销；未新增 Web 注册/邀请码管理 API、User 角色或限流表。
- P0.4b-1 隔离验证：邀请专项 7 passed；后端全量 149 passed；前端 Vitest 35 passed；TypeScript `tsc --noEmit` 通过；临时目录 Vite 构建通过（保留既有 chunk size 提示）；Playwright 2 passed。迁移在空库及合成旧 schema 副本验证，保留旧 User/Session，并覆盖重复升级、schema 冲突/迁移失败安全行为、令牌唯一性、领取并发及事务回滚；未连接正式 SQLite。该结果是 P0.4b-1 切片验收成绩，不是本轮 UI 更新的重跑成绩，也不是 v0.4.0 发布验收。
- P0.4b-2a 新增邀请注册后端闭环：`GET /api/auth/registration/challenge` 与 `POST /api/auth/register`；Origin 校验、一次性 HttpOnly/SameSite challenge、用户名规范化、密码策略、邀请状态统一失败响应。注册通过 Task 外的独立服务事务原子写入 Invitation 领取、User 与初始 Session；登录继续兼容旧 Session 创建契约。
- P0.4b-2a 新增 Alembic `a6c7d8e9f0a1` 与 `AuthSecurityState`：只存 HMAC 摘要，原子 upsert 维护 IP 总量及 IP/规范化用户名组合限流，单次 challenge 消费；限流计数先在独立事务提交，注册失败不会回滚计数。迁移场景与 18 项注册专项测试通过；后端全量 `167 passed`，不是 v0.4.0 发布验收。
- P0.4b-2b 新增登录页内的邀请注册表单：客户端验证用户名/密码规则和确认密码；每次提交先取新 Challenge，再以 `X-Registration-CSRF` 与同源 Cookie 调用注册 API；成功后以 `/api/auth/me` 验证 Session，再进入应用。邀请码、密码与 Challenge 不写入 URL/localStorage；网络结果不确定时提示尝试登录核实，不自动重放注册请求。
- P0.4b-2b 隔离浏览器闭环验证：两位合成用户分别注册，A 创建任务并刷新后读取，退出后 API 返回 401，重新登录读回任务；B 对 A 任务的读取/修改均返回 404 且不泄露标题；已消费邀请码不能再次注册。还验证过期邀请码统一错误、错误 Challenge 403、前端对 HTTP 429 限流响应的提示、Session/CSRF Cookie 属性、Challenge HttpOnly/SameSite 属性和注册网络中断提示。当前 E2E 使用本机 HTTP，故生产 HTTPS `Secure` Cookie 与实际 Tailscale 代理信任拓扑未由此证明。
- P0.4b-2b 本轮实跑：后端全量 pytest `167 passed`；前端 Vitest `39 passed`；TypeScript `tsc -b` 通过；独立临时目录 Vite 构建通过（保留现有 chunk size 提示）；隔离 Alembic 空库升级到 head；Playwright 全量 `5 passed`。测试使用临时 SQLite、合成邀请码/账户和本地 Mock Provider；不代表全资源隔离或公网部署验收。
- P0.4b-2a 已实现后端闭环；P0.4b-2b 已实现前端注册与 Task 双用户浏览器闭环，但全资源双用户隔离及 v0.4.0 发布验收仍未完成。注册业务事务提交后才签发 Session Cookie；提交后 HTTP 响应丢失时可用密码登录恢复访问，已消费邀请码不可重用。
- P0.4c-1 新增隔离双用户 API 回归 `tests/test_mult_user_isolation.py`：合成 A/B 用户验证 Task、Evidence、Knowledge 检索/修改、Conversation/Message、Proposal、Agent Run/ToolCall、Review、Study/Health、Provider 凭证响应、跨用户关系 ID 与实例级备份/导出拒绝；数据库读回断言拒绝操作未修改 A 的任务、知识、Proposal、Evidence 或关系记录。
- P0.4c-1 本轮最终实跑：后端全量 pytest `168 passed`（含新增跨资源隔离测试）；前端 Vitest `39 passed`；TypeScript `tsc -b` 通过；独立临时目录 Vite 构建通过（存在既有 chunk size 警告）；Playwright 全量 `5 passed`，覆盖注册双用户 Task 读改隔离、Review 与审批。测试为本机临时 SQLite/合成账户和 Mock Provider，不证明生产 TLS/代理、文件系统跨账户 ACL 或全资源浏览器 E2E。
- P0.4c-1 权限结果为部分验证：服务端 Session 与 owner 查询在上述资源上有隔离证据；主题图片/上传路径仅从代码核查 user-specific 目录及安全文件名，本轮未执行文件系统双用户 E2E；Session 撤销/账户停用、Invitation CLI 运维边界和共享环境 Provider Key 的调用成本策略也未由本轮双用户 API 场景完整覆盖。细分见[账户邀请与授权设计](docs/architecture/account-invitation-and-authorization-design.md)及[工程总账](docs/architecture/PROJECT_ENGINEERING_LEDGER.md)。
- P0.4c-2 在不含正式数据的独立目录执行 `uv sync --locked`、`pnpm install --frozen-lockfile`、空库 Alembic 升级、合成首账号与本机邀请 CLI、浏览器注册/任务刷新、停服重启读回。最初 `uv sync --locked` 因 `uv.lock` 本地项目版本仍为 0.3.2 而拒绝；仅将锁内该版本校正为当前已发布事实 0.3.3，未调整依赖解析。备份服务的当前 schema 常量由 `f3a4b5c6d7e8` 更新为实际 Head `a6c7d8e9f0a1`；先新增失败测试，再确认修复。
- P0.4c-2 合成 v0.3.3 schema（Tag 内迁移链到 `f3a4b5c6d7e8`）含 User/Session/Task/Knowledge/Review/Proposal 各一条，升级到 Head 并重复执行后记录数与归属不变，SQLite 完整性及外键检查通过。本机 SQLite CLI 备份/新路径恢复保留 ID、关系、审批审计、Invitation 状态；损坏加密包与已有目标均拒绝覆盖。合成 Provider 凭证的加密迁移包经 Service 验证并恢复，旧 Session 清除；恢复库启动、登录、Task 读写通过。普通 SQLite 快照仍保留 Session，且不独立包含 Provider 主密钥；Web 整库导出继续拒绝，可信本机加密包导出 CLI 尚缺。
- P0.4c-2 本轮实跑：后端全量 pytest `169 passed`（新增当前 Head 备份回归）；前端 Vitest `39 passed`；TypeScript `tsc -b` 与临时 Vite build 退出码 0；全新副本注册 Playwright `2 passed`；全量隔离 Playwright `5 passed`。测试仅证明同机独立目录与合成数据，不证明干净操作系统、正式 HTTPS/Tailscale、旧个人库升级或真实 Provider 恢复。
- P0.4c-2 新发现的发布阻断尚未修复：合成旧库故意使 Alembic Head 升级退出 1 后，production 模式的隔离应用仍能启动且 `/api/health` 返回 200；当前启动只检查数据库文件存在，没有检查 revision 与应用期望一致。正式启动校验属于下一项需批准的运行契约修复；不能据健康 200 宣称迁移成功。
- P0.4c-2a 修复上述启动门禁：production Lifespan 在后台备份调度器创建前，从代码迁移目录确定唯一 Head，并用 SQLite `mode=ro` 读取配置库的 `alembic_version`。缺库、不可读、缺表、旧版、未知版、多个数据库 revision 或多个脚本 Head 均拒绝启动；不自动建表或升级。先以合成旧库/故障库复现 7 项失败，再使定向测试 `23 passed`、后端全量 `179 passed`。前端 `39 passed`、类型检查与临时构建通过、Playwright `5 passed`；隔离浏览器脚本明确固定测试 Cookie `Secure=false`，不再受本机配置影响。该门禁只校验 revision，不证明所有业务表、文件或部署拓扑健康。
- P0.4c-2a 启动门禁只在合成库和隔离 production Lifespan 下验证；真实 Windows/Tailscale 服务尚未重启或部署。后续仍需真实 HTTPS/代理、Session/文件/Provider 安全、独立机器安装、可信本机加密包导出、文件系统及全资源 E2E、许可证与公开历史审查。v0.4.0 仍为 Unreleased。
- P0.4c-3 修复主题背景目录逐层 NTFS junction 重解析绕过；新增 Session 退出/过期/停用/错误密码回归，并修复 inactive 账户仍能签发不可用 Session 的登录缺陷。先失败后通过的 junction 与账户测试使用隔离目录、SQLite 和合成用户。全量后端 pytest `186 passed`；前端 Vitest `39 passed`；`pnpm exec tsc --noEmit` 退出码 0；临时目录 Vite build 退出码 0（有既有大 chunk 提示）；隔离 Playwright `5 passed`；Schema Startup Guard 包含于全量后端测试；`git diff --check` 退出码 0（仅 Git 换行提示）。
- P0.4c-3 安全边界仍有发布阻断：真实 NTFS ACL 元数据显示 `app/data`、`private`、`theme-backgrounds` 继承 Authenticated Users Modify 与 Builtin Users Read/Execute；未修改 ACL。Provider 共享环境 Key 可被普通已登录用户通过 Mock 验证调用，方案 A/B 待负责人决定。Tailscale Serve 仅做本机配置读取，未进行真实远端浏览器/代理验收；服务进程账户也未与计划任务 SID 完成交叉核验。以上均未用真实 Provider 或正式业务数据测试。
- P0.4c-3a 按方案 A 封闭 Web AI 凭证 fallback：Chat、Agent、计划/晚间复盘、Knowledge 提炼、Provider 连接测试统一要求服务端可信用户 ID 对应的个人 Provider 记录具备有效 Fernet 密文且已启用；不再从进程环境 Key 解析凭证。无凭证/禁用返回 503 和设置提示；Provider 设置状态也不再把全局 Key 显示为当前用户已配置。User A 的个人 Key 不能被 User B 读取或使用；Tool Registry 搜索仍只读取当前 user_id 的 Tavily 凭证。
- P0.4c-3a 合成失败测试先证明旧路径会消费共享 Key、disabled/无凭证会发送外部请求并把环境 Key 显示为用户配置；修复后专项 13 passed，后端全量 pytest `195 passed`；前端 Vitest `39 passed`；`pnpm exec tsc --noEmit` 退出码 0；临时 Vite build 退出码 0（存在既有 chunk-size 提示）；隔离 Playwright `5 passed`。未读取真实 `.env` 内容、未调用真实 Provider，未新增数据库结构。
- P0.4c-3a 兼容影响：依赖 `PERSONAL_OS_DEEPSEEK_API_KEY` 的默认账户在配置自己的 DeepSeek Key 前，Chat、Agent 任务管理、AI 计划/晚间复盘、Knowledge 提炼及 Provider 连接测试会得到 503；离线 mock 模式与非 AI 业务保留。用户需通过“设置 → 模型 Provider”自行粘贴并保存/启用个人 Key，再按需测试连接；运维不得自动复制共享环境值。
- P0.4b-1 当时的交付边界：Invitation Service 不自行 `commit`；注册 API、注册 User/Session 原子事务、跨进程共享限流和 React 注册页面尚未实现。P0.4b-2a/2b 分别完成后端注册事务与前端注册闭环，不追溯改变当时的验收口径。
- 本轮工程归档：扩展 `app/README.md`、Task Lifecycle 工程学习索引和现有“设置 → 版本与工程记录”页面；新增只读分阶段技术记录与核心机制总览。前端本轮 Vitest 37 passed、TypeScript `tsc --noEmit` 通过、独立临时目录 Vite 构建通过、隔离 Playwright 3 passed；后端没有重跑，沿用上述 P0.4b-1 已验收的 149 项基线。本轮页面没有显示注册入口。
- P0.4c-3b / R3 收口：复核登录页邀请码注册、Invitation 单次领取、原子注册事务、Session 登录恢复、退出失效及双用户 Task 隔离；本轮隔离全量 Playwright 5 passed，包含邀请注册、Review、知识/Chat Mock、Proposal 审批与幂等。负责人报告手机已完成核心流程；设备逐项证据未由自动化代替。
- R2c Review 排版：为每日结算表单增加窄屏适配和控件布局规则；新增浏览器几何断言，桌面 1280px 与窄屏 390px 均验证标签/textarea/按钮边界，窄屏输入、保存和刷新读回通过。未更改 Review Service、API 或数据库契约。
- 本轮实际回归：后端全量 pytest `195 passed`、前端 Vitest `39 passed`、TypeScript 检查通过、临时目录 Vite 构建通过、Schema Startup Guard `10 passed`、隔离 Playwright `5 passed`、`git diff --check` 通过。Playwright 使用合成账户、独立临时 SQLite 和本机 Mock Provider；不代表真实 Provider、手机网络记录或公开部署验收。
- 新增 [v0.4.0 Release Candidate 检查清单](docs/releases/RELEASE_CANDIDATE_CHECKLIST.md)，记录公开文件边界与未关闭发布门槛。v0.4.0 仍为 Unreleased；正式 Windows ACL、许可证、干净机器安装/恢复复验和公开仓库审查仍阻断正式发布。

### Engineering Design

- **ID** 标识不同对象与操作：`user_id` 定义数据所有者，`proposal_id` 标识 AI 草案，`operation_id` 标识可重试写操作，`task_id` 标识正式任务；它们不互相替代。
- **State Machine** 限制状态变化：Proposal 从 `PENDING` 经审批认领进入 `APPROVING`，提交后成为 `APPROVED`；正式 Task 在批准写入后按规则以 `TODO` 创建。
- **Service** 收口业务规则：Task Service 负责正式 Task 创建、日期规则、状态历史、归属与幂等；API/Proposal 通过已定义入口调用。
- **Transaction** 保障一致性：Proposal 状态、Task、状态历史、来源关联和审批审计在批准事务中共同提交；异常时回滚。
- **Idempotency** 约束重试副作用：操作键按用户唯一；相同键和相同请求可重放结果，不同请求冲突；无手动 `Idempotency-Key` 的旧请求仍按兼容语义创建新任务。
- **Authorization** 使用可信身份：API 从服务端 Session 取得用户身份，Service/查询按资源所有者过滤；实例级备份 Web 入口继续默认拒绝。
- **Audit Trail** 支持追溯：Proposal 审批/拒绝、Agent 工具调用和 Task Manager 操作保留相应审计记录；不把模型文案当成正式执行证据。
- 全局版本时间线、功能矩阵、事故与复刻顺序见[项目工程总账](docs/architecture/PROJECT_ENGINEERING_LEDGER.md)；详细调用链和面试复习材料见[Task Lifecycle 工程说明](docs/architecture/task-lifecycle-engineering.md)；邀请与管理员边界草案见[账户邀请与授权设计](docs/architecture/account-invitation-and-authorization-design.md)。

### 发布及部署边界

- 历史阶段记录保留其当时状态和验收成绩；最终源码候选通过与否、未验证边界和最终版本信息以 [最终发布审批报告](docs/releases/FINAL_RELEASE_APPROVAL.md) 为准。

### 发布待办与未验证边界（阶段记录，按最终审批报告复核）

- 仍待正式 Windows ACL 修复与正式服务升级；本机隔离演练不替代真实生产环境验收。其他来源版本的阶段结论以最终审批报告为准。

### 仅完成 R0 规划

- Goal & Constraint、World Model & Bayesian Belief、Decision Engine、Action Orchestrator、Feedback & Learning、External Intelligence Gateway 仍为长期目标架构与契约草案；未作为 v0.4.0 业务功能实现。

### Remote Delivery Sprint R1 · 已完成本机隔离验收，手机验收待执行

- Remote Delivery Sprint R1 文档与隔离验收：确认正式进程对外标记 `0.3.3`、加载路由含 v0.4 开发能力，且晚于进程启动的工作区源码和 2026-10-14 前端构建导致运行快照不可证明；TEMP 无 `.env`/正式数据副本全量 Playwright `5 passed`、Schema Startup Guard `10 passed`，生产配置临时 loopback 实例健康与静态资源通过。真实手机/HTTPS 仍等待负责人批准临时 Tailnet 8443 映射并由设备验证。

### 既有 v0.4 Review & Knowledge Feedback 范围说明

- 基于 v0.3.3 本地稳定 tag 在 `develop/v0.4` 开发；当前开发范围聚焦每日任务回顾、执行证据、人工确认后沉淀知识，以及每周执行总结。
- 复用 DailyReview、DailyTaskSnapshot、TaskEvidence、Knowledge Service 和既有任务 API；周总结只读聚合，不新增事实表或数据库迁移。
- 任务证据写入由 Task Service 收口；复盘带入知识工作区只是可编辑草稿，用户在知识工作区明确保存后才创建卡片。
- 开发中记录不是正式发布，也不改变当前稳定版本 0.3.3。

### 早期探索记录（非新版关窗验收证据）

- 追踪 2026-10-13 后台进程控制中断：计划任务直接启动服务入口，管理器关闭分支不发出停止命令；历史触发来源因 Task Scheduler Operational 日志当时禁用而无法确认。
- 服务入口为运行写入关联 run_id、退出原因和退出码；管理器显式停止记录独立生命周期事件，并明确菜单退出只关闭管理器。
- 两次结束管理器终端会话后等待 60 秒复核：本机与 Tailnet 健康检查通过，未出现重复监听实例；桌面窗口关闭按钮与原账号登录仍待用户验收。
- 复核管理器菜单正常退出后服务继续运行满 60 秒；服务入口增加非敏感运行标记，下次启动可报告上一 run 缺少退出记录（原因未知），管理器记录正常启动/退出意图。
- 本轮只修改 Windows 运维脚本、服务入口测试和生命周期文档；未修改计划任务定义或数据库结构。新版管理器现复用用户已实测的无控制台启动组合。
- 收敛 Windows 服务生命周期到一个双击管理入口；登录触发的计划任务直接运行项目虚拟环境中的受控 Uvicorn 入口，不再经过多层命令包装。
- 增加服务健康检查、单实例防护、轮转日志、失败重启设置及 Tailscale Serve 状态查看。
- 增加统一部署与回滚运维说明；本轮不改业务数据、数据库结构或正式版本号。
- 修复 2026-10-13 后台服务退出后 Tailnet 502 被前端显示为通用 `Request failed` 的运维反馈问题；生产 SQLite 相对路径固定按 backend 根目录解析，缺少正式库时拒绝静默建库。
- 保留原账号与生产记录；本轮无 Alembic 迁移、无凭证修改、无真实模型/搜索请求。实际网页登录由用户验收。

## v0.4 K2-B.1 — 开发中（产品版本仍为 0.3.1）

- 提升聊天 AI 回复的“保存为知识”可见性，并增加进入知识编辑器的 AI 提炼快捷入口；两者均不自动调用模型。
- 新增按需单次 DeepSeek 提炼候选，来源限定为当前用户拥有的已保存助手消息或原文中的用户选区；记录 Agent Run 与 Token 用量，不自动写入知识卡片。
- 提炼候选需用户显式采用、编辑并再次保存；领域、标签、知识类型、审核状态和原消息保持原样。
- 健康/财务及敏感内容发送前要求明确确认，疑似密钥/密码/访问令牌阻止发送；模型来源/证据声明不作为可信来源。
- 自动化测试仅使用模拟 Provider；无数据库迁移、无真实模型调用、无 Notion 接入。

## v0.4 K2-B — 已实现，等待用户验收（产品版本仍为 0.3.1）

- 开发统一 Knowledge 收集箱，使用共享知识服务支持七个领域分类。
- 记录知识卡片与原始聊天消息、实际来源证据及任务的稳定关联；不复制或改写原始对象。
- 当前阶段不接入 Notion，不调用模型/联网服务，不自动生成知识结论。
- 已实现本地卡片 CRUD、领域/类型/状态/标签/正文搜索、原消息及真实 Run 来源追溯、任务双向关联、回收恢复/确认清理与单卡 Markdown/JSON 导出。
- 数据库增量结构为 Alembic `f3a4b5c6d7e8`；这不是产品版本号。发布记录仍保持 v0.3.1，K2-C/K2-D 未开始。

## v0.3.1 — AI 任务管理稳定性更新（2026-10-13 16:50 Asia/Taipei）

- 修复 AI 任务管理员真实创建路径、创建授权模式、参数错误反馈及操作 ID 幂等问题；成功以数据库提交后的真实任务 ID 确认。
- 支持明确的创建、只读规划、修改模式，并避免用户指定任务数被错误拆分。
- 完善任务时间线、跨天日期、延期历史、每日结算，以及 Dashboard 今日/逾期/未排期分组。
- 修复已删除任务详情读取，并完善任务、会话和提案的归档、恢复、软删除及受保护的永久清理。
- 新增任务模块接口契约和统一删除生命周期文档，改进宽屏任务工作区。
- 限制：Notion 和结构化知识库尚未实现；Windows 主机离线时 Tailnet 服务不可访问；异地备份未接入；私有主题背景图未包含在加密迁移包中；本轮未完成新的 iPhone 实机/完整生产 Agent E2E 验收。

## v0.3.0 — AI 对话与任务工作台（2026-10-13）

- 分领域持久化多轮对话，支持会话管理、完整导出和按需加载历史消息。
- AI 消息可追溯真实工具调用、来源及 Agent Run；经授权可查询当前会话关联任务。
- 统一任务工作区提供创建、编辑、状态、证据及会话关联；增加 Token 用量与预算。
- 增加 Classic/Cyberpunk Night 主题、侧栏折叠和聊天阅读排版优化。
- 限制：Notion、结构化知识卡片与异地备份尚未实现；Windows 主机需保持在线；当前加密迁移包不含私有背景图。

## v0.2.0 — 账号与数据管理

- 账号资料和会话基础存储、本机备份及加密迁移包。

## v0.1.0 — 基础框架

- React、FastAPI、SQLite、登录、基础任务与私有 Tailscale 访问。
