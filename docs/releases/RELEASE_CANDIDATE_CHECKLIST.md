# v0.4.0 Release Candidate Checklist

**审查日期：** 2026-10-20
**历史检查表：** 下方 R4a-1 / R4a-2 结果保留为历史记录；最终 R4a-3 状态见本节后续附录；审批报告单独交付。
**边界：** 本表基于本地仓库、隔离测试和负责人手机实测反馈；不构成发布授权，也不代表已公开 GitHub 仓库或公网部署验收。

## 版本与工作区证据

| 检查 | 状态 | 证据与边界 |
|---|---|---|
| 正式版本基线 | PASS | `release_history.json` 的 `current_version` 为 `0.3.3`；`v0.3.3` 标签与 HEAD `1569b04` 一致。 |
| v0.4.0 发布状态 | PASS | `app/backend/pyproject.toml`、`app/frontend/package.json` 仍为 `0.3.3`；CHANGELOG 标记 `v0.4.0 — Unreleased`。没有 v0.4.0 Tag。 |
| 开发工作区保护 | PASS | 分支 `develop/v0.4` 上有多项未提交及未跟踪改动，包含业务代码、迁移、测试、R0 文档和个人工作区文件；本审查未执行 Git 写操作。 |
| 远端可见性与内容同步 | NOT VERIFIED | `origin` 指向 `JXcloudforge/personal-os`；本机 `git ls-remote origin HEAD` 没有返回 ref。不能据此确认 GitHub 仓库公开/私有状态或远端文件内容。 |

## R3 邀请注册与核心闭环

| 检查 | 状态 | 证据与边界 |
|---|---|---|
| 本机管理员初始化、CLI 邀请和 Web 注册入口 | PASS | 现有 `initialize_admin` / `manage_invites` CLI、匿名注册 Challenge/API 与 React 登录页注册分支；本机合成 R2 数据库只读核对显示 2 个合成用户，邀请码元数据状态为 `CLAIMED`。没有显示或读取令牌摘要。 |
| 邀请状态、单次消费、并发消费 | PASS | `test_invitation_service.py` 覆盖哈希存储、到期、撤销、单次使用、原子并发领取及外层事务回滚；`test_registration_workflow.py` 覆盖同一邀请码并发注册仅创建一个账户。 |
| 注册事务、Session 与恢复 | PASS | 定向测试覆盖 User/邀请/Session 同事务、失败回滚、提交后登录恢复、单次 Challenge、限流持久化和统一的无效邀请错误；注册 E2E 覆盖成功态、退出后 401、重新登录及重用邀请码拒绝。 |
| 双用户 Task 隔离 | PASS | 本轮隔离 Playwright 注册两个合成账户；A 创建并刷新读回任务，退出后旧 Session 被拒绝；B 无法读取或修改 A 的 Task。 |
| 多资源 API 隔离 | PASS | `test_mult_user_isolation.py` 覆盖 Task、TaskEvidence、Knowledge/检索/关联、Chat/Message、Review、Proposal、Agent Run/ToolCall、学习、健康、Provider 和实例级导出拒绝。该项是 API 测试，不等同于所有资源均有双用户浏览器 E2E。 |
| Chat / Agent / Proposal 审批 | PASS | 本轮隔离 Playwright 使用本地 Mock Provider，经过 Chat、Knowledge 引用、Proposal 编辑、批准/拒绝、Task Service 写入和幂等重放；没有调用真实 Provider。 |
| Review 桌面与窄屏交互 | PASS | 390px 与 1280px 浏览器视口检查表单标签、textarea、按钮边界和无横向溢出；窄屏输入、保存、点击 Review 内刷新并读回成功。只修改 CSS 与 E2E 测试，没有改 Review Service/API/schema。 |
| 负责人手机实测 | PASS（负责人报告） | 负责人反馈“已测完整核心流程”。本轮未取得可独立核验的手机截图、请求日志或逐项设备记录；不把 Playwright 视口模拟当作手机证据。 |
| 手机 TLS / Origin / Cookie / CSRF | NOT VERIFIED（设备证据） | 代码及主机 HTTPS 探测不是浏览器设备证据；本轮没有单独收集手机证书链、Cookie 属性或 Origin/CSRF 网络记录。 |

## 本轮自动化验证

| 检查 | 状态 | 结果 |
|---|---|---|
| 后端全量 pytest | PASS | `195 passed, 23 warnings`，退出码 0；pytest fixture 创建独立 TEMP SQLite。 |
| 前端 Vitest | PASS | `9` 个测试文件、`39 passed`，退出码 0。 |
| TypeScript | PASS | `pnpm exec tsc --noEmit`，退出码 0。 |
| Vite 生产构建 | PASS | 输出到独立 TEMP 目录，退出码 0；已有 JS chunk 大于 500 kB 提示。 |
| Playwright 全量 E2E | PASS | `5 passed`，独立 TEMP 源码副本、SQLite、Mock Provider 和专用端口；覆盖注册、Review 双视口保存读回、知识、Chat、Proposal 审批、任务幂等和工程记录页面。 |
| Schema Startup Guard | PASS | `10 passed, 9 warnings`，退出码 0；只使用合成数据库。 |
| `git diff --check` | PASS | 退出码 0；Git 提示工作区 LF/CRLF 可能转换，不是空白错误。 |
| 正式数据库 / `.env` / 备份 | PASS（未触碰） | 测试使用临时副本；本轮未读取正式数据库、`.env` 或个人备份内容。 |

## R4 交付准备与公开仓库风险

| 检查 | 状态 | 证据与建议 |
|---|---|---|
| 前端 README | PASS | `app/README.md` 约 78 行，包含产品定位、功能状态、架构、Quick Start、测试和文档入口；本轮增加本清单链接。根目录 `README.md` 描述另一个个人迭代系统，应排除在 Personal OS 产品发布白名单外。 |
| Python 依赖锁 | PASS | `backend/pyproject.toml` 声明 Python `>=3.11`，存在 `uv.lock`。 |
| 前端锁文件 | PARTIAL | `pnpm-lock.yaml` 存在并可用 frozen install；`package.json` 多项直接依赖写为 `latest`，缺少 `packageManager`/pnpm 版本契约，建议改为经过验收的精确版本后再做干净安装。 |
| 环境模板 | PASS | `backend/.env.example` 使用占位符，列出 Secret、SQLite URL、Provider、Cookie、Origin、Trusted Hosts 和前端目录配置；真实 `backend/.env` 由 `.gitignore` 忽略。 |
| 空库/旧库安装升级 | NOT VERIFIED（本轮） | 旧版合成库升级证据存在于既往 P0.4c-2 验收档案；本轮没有重新执行干净机器安装、v0.3.3 旧库升级或恢复演练。 |
| 迁移与启动顺序 | PASS（代码契约） | production Lifespan 的只读 Schema Guard 在后台调度器前拒绝不匹配 revision；部署需先由可信 CLI 执行 `alembic upgrade head`，Web 不自动迁移。真实主机升级未在本轮执行。 |
| Windows 生命周期 | PARTIAL | 有 `PersonalOS-Manager.cmd` 与 `docs/operations/service-lifecycle.md`；全新 Windows 用户安装、启动/停止、登录自启、失败回退需在独立用户/机器复测。 |
| 正式 ACL | FAIL | 既往只读审计记录数据目录存在宽泛 Windows 权限；R2 在 TEMP 合成目录演练可回退，但正式 ACL 尚未修复和复核。开放其他真实用户前必须关闭。 |
| HTTPS / Tailscale | PARTIAL | 负责人反馈手机完成核心流程；本机 Serve 配置中 443→8000 与 8443→8301 保持独立、Tailnet-only、无 Funnel。HTTPS 证书/浏览器状态缺少本轮独立设备证据；公网部署不在验收范围。 |
| 备份恢复 | NOT VERIFIED（本轮） | 既往合成恢复演练结果已归档；本轮未重跑完整备份、恢复、启动和业务读回。正式发布前应按候选版本再演练一次。 |
| License | FAIL | 仓库没有 LICENSE/COPYING/NOTICE 文件。确定并添加许可证前不得宣称允许公众再分发。 |
| 第三方许可清单 | NOT VERIFIED | 尚无本轮生成的 Python/npm 依赖许可证清单、NOTICE 或兼容性审查。 |
| `.gitignore` | FAIL | 已忽略 `.env`、数据库、`data/`、媒体、备份和依赖目录；但仓库根目录当前存在未跟踪的个人状态/计划 Markdown 文件，未被当前规则排除。公开前必须采用精确文件白名单，并审查/补足忽略规则，不能使用 `git add -A`。 |
| Git 历史敏感扫描 | PARTIAL | 对可读应用文件及可达历史执行了有限模式扫描，未发现匹配的常见 API 私钥模式；扫描不是完整 DLP/密钥审查，不能证明历史不存在秘密。当前工作区还含私有 `.env`、SQLite 与个人文件，均不得打包。 |
| 文件白名单 | FAIL（待批准） | 建议仅纳入 `app/` 中经审查的源码、迁移、测试、`README.md`、CHANGELOG、锁文件、`.env.example` 和必要文档；明确排除根级个人 OS Markdown、`archive/`、`tools/`、运行数据、真实日志、数据库、备份、`.env`、媒体与构建产物。 |
| GitHub 远端公开状态 | NOT VERIFIED | 当前设置 `origin`，但远端没有可读取的 HEAD ref；是否公开、远端内容及保护规则需负责人在 GitHub 控制台确认。 |

## v0.3.3 → v0.4.0 的真实变化

- 在既有 React/FastAPI/SQLite 技术栈内完善 Review/Task Facade、前端异步竞态保护、Task Service 正式写入边界、Agent Run/User 归属和多用户授权检查。
- 增加 Invitation/注册限流数据结构、Alembic 迁移、可信 CLI 邀请管理、邀请注册 API 与 React 注册流程。
- 封堵普通 Web 用户对实例级备份/导出的入口；普通用户的模型调用限定为该用户已启用的个人 Provider 凭证。
- 增加生产启动 Schema Guard 和工程文档/学习记录。
- R0 的世界模型、贝叶斯更新、决策引擎等仍是设计内容，不属于 v0.4.0 已实现功能。
- 以上属于未提交开发工作区变化；当前 Tag 与正式发布记录仍为 0.3.3。不得将这些内容描述为已推送或已发布。

## 发布授权前必须关闭

1. 负责人批准并完成正式数据目录 ACL 的最小权限修复与回退演练。
2. 清理公开候选边界：确定 LICENSE，生成依赖许可证清单，修正根级个人文件的排除规则，并逐项审查精确 staged 文件白名单。
3. 完成一次干净安装/旧库升级/备份恢复的候选版本复验，并保留无敏感数据的命令结果。
4. 以目标候选版本重新执行全部回归，补充手机设备 TLS/Cookie/Origin 逐项记录；不得只引用历史基线。
5. 核验 GitHub 远端可见性、提交内容、分支保护和 Tag/Release 方案；负责人另行批准后才能执行 commit、push、tag 或 release。

**当前结论：** R3 自动化邀请注册与核心业务闭环通过；负责人报告手机核心流程完成。v0.4.0 尚不满足公开 Release Candidate 门槛，尤其阻断项为正式 ACL、许可证、公开文件边界、独立安装/恢复复验和手机安全证据归档。

## R4a-1 公开候选复核（2026-10-20）

本节是 R4a-1 公开源代码准备时的检查结果；后续状态以 R4a-3 最终检查及审批报告为准。

| 检查 | 状态 | 证据与边界 |
|---|---|---|
| Git 根、分支与提交 | PASS（本地事实） | Top-Level 是工作区根；`develop/v0.4` HEAD 与 `v0.3.3` tag 同为 `1569b04`。当前有大量既有修改和未跟踪文件，未暂存。 |
| 精确公开文件清单 | PASS（候选） | [PUBLIC_FILE_ALLOWLIST.md](PUBLIC_FILE_ALLOWLIST.md) 列出当前 158 个候选路径；将 `app/` 内容复制到新公开仓库根目录，`app/.gitignore` 成为新仓库 `.gitignore`。工作区根 `.gitignore` 不复制。不是执行 Git 暂存的授权。 |
| 根目录个人资料排除 | PASS（工作区忽略规则） | 根 `.gitignore` 已排除根个人 Markdown、`archive/`、`tools/` 和测试证据目录。忽略规则不保护历史或手工暂存内容。 |
| 当前候选文档脱敏 | PASS（有限字段） | 公开工作区文档中的 Windows 用户/SID、运行 PID、本机绝对路径与临时证据路径替换为说明占位符；原始运维记录保存在工作区外的用户私有目录。仍须对最终导出再次复核。 |
| Git 历史个人信息扫描 | PARTIAL | 两个可达提交中未发现当前文档里的 Windows 账户/SID；历史包含日期化 `%TEMP%` 测试证据路径。有限扫描不能证明没有秘密。不得直接公开现有历史；优先采用全新、经审核的公开仓库历史。 |
| 真实密钥扫描 | PARTIAL | 常见令牌模式扫描没有确认真实密钥；误报来自文件名和测试合成值。该筛查不是完整 DLP，也不能替代人工检查。 |
| 产品版本 | PASS（未发布状态） | 当前发布事实仍为 `0.3.3`；Python/前端包元数据保持 `0.3.3`，CHANGELOG 的 v0.4 为 Unreleased；本轮没有创建 0.4 tag。 |
| 前端依赖声明 | PASS（隔离锁定安装） | `latest` 改为锁文件解析的精确版本；声明 `pnpm@11.25.0`、Node `>=24.19.0 <25`。TEMP 白名单副本 `pnpm install --frozen-lockfile` 退出 0，构建退出 0。 |
| Python/uv 约定 | PASS（元数据） | Python `>=3.11` 保持；uv 声明 `>=0.11.31,<0.12`。本机 Python 3.14.6、uv 0.11.31；其他 Python 版本未由本轮实测覆盖。 |
| MIT 授权 | FAIL / 待负责人确认 | 本轮不创建 LICENSE。拟议文本中的 `Copyright (c) [YEAR] [COPYRIGHT HOLDER]` 待版权持有人确认；历史提交作者信息不是所有权证明。 |
| 第三方许可审查 | PARTIAL / NOT VERIFIED | 当前 Python/npm 直接依赖元数据含 MIT、Apache-2.0、BSD-3-Clause、ISC 等；Python cryptography 为 Apache-2.0 OR BSD-3-Clause。依赖树元数据还出现 MPL、BlueOak 等标识和未注明字段。尚未产出逐个 lockfile 包的许可证/NOTICE 兼容清单。应用未跟踪独立图片/字体/图标文件（图标通过 lucide-react 包依赖提供）；这不是完整版权审计。 |
| 干净机器安装与恢复 | PARTIAL / NOT VERIFIED | 白名单 TEMP 副本完成 Python/前端冻结安装、迁移与构建；这不是全新 Windows 主机验收。隐藏密码的管理员 CLI 交互未完成；本轮没有重跑备份恢复。正式 ACL 与服务升级仍未验收。 |
| 正式 ACL 与服务升级 | FAIL / 未验收 | 正式 ACL 未修改或复核；当前正式 8000 实例尚未证明运行 v0.4 候选源码。 |

### MIT 文本草案（不得直接作为已授权 LICENSE）

```text
MIT License

Copyright (c) [YEAR] [COPYRIGHT HOLDER]

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

此文本依据 [OSI MIT License](https://opensource.org/license/mit) 编排。确认版权人、年份及第三方素材边界后，才可生成最终 LICENSE。

### 隔离安装复演与回归（2026-10-20）

| 检查 | 状态 | 实测 |
|---|---|---|
| 白名单副本 | PASS | 仅复制 `PUBLIC_FILE_ALLOWLIST.md` 中的路径；副本只含一个临时 `.env`（合成随机 Secret）和一个合成 SQLite。没有复制正式数据库、配置、备份、日志或依赖环境。 |
| Python 冻结安装 | PASS | `uv sync --locked` 和 `uv lock --check` 均退出 0，uv `0.11.31`。 |
| Alembic 空库 | PASS | 临时 SQLite `upgrade head` 退出 0，读回 `a6c7d8e9f0a1`。没有连接正式数据库。 |
| 前端冻结安装与构建 | PASS | `pnpm install --frozen-lockfile`、`pnpm run build` 均退出 0；Node `24.19.0`、pnpm `11.25.0`。 |
| 后端全量 pytest | PASS | TEMP 副本 `195 passed, 23 warnings`，退出码 0。首轮副本测试暴露 TestClient Host 与干净环境不匹配；将 `testserver` 加入 pytest fixture 后复跑通过，未放宽 production Trusted Hosts。 |
| 前端 Vitest | PASS | TEMP 副本 9 files / 39 tests passed，退出码 0。 |
| 初始化管理员 CLI | NOT VERIFIED | `getpass` 在非交互管道中等待终端输入；已中断该 TEMP 测试。没有创建账户，也未将密码写入报告。需后续使用受控交互终端或专用 CLI 集成测试验收。 |
| 邀请创建 CLI | NOT VERIFIED（本轮） | 未在交互终端运行一次性代码输出命令；既有 CLI 代码和此前阶段测试仍需与本轮安装演练区分。 |
| 备份恢复 | NOT RUN | 依赖与迁移收口不替代恢复验收；按候选版本完成 ACL/目录边界复核后再执行合成恢复演练。 |

测试临时副本留在 `%TEMP%`，含合成配置、合成 SQLite 与依赖环境；本轮未执行清理。

## R4a-3 最终源码候选封板（2026-10-20）

本节覆盖最终 `0.4.0` 源码候选。当前没有 Push、Tag、GitHub Release 或仓库可见性变更。负责人审批报告单独交付，不随公开源码分发。

| 检查 | 状态 | 证据与边界 |
|---|---|---|
| MIT LICENSE | PASS | 候选根 `LICENSE` 为标准 MIT 全文，署名 `Copyright (c) 2026 JXcloudforge`；这是负责人本轮明确授权。 |
| 版本一致 | PASS（候选） | `pyproject.toml`、uv lock 根项目、前端 `package.json`、`release_history.json` candidate、README 和 Release Notes 均为 `0.4.0`；v0.3.3 历史记录保留。pnpm v9 lockfile 不单独记录 importer 包版本，依赖图通过冻结安装核验。 |
| 冻结安装 | PASS（SAME-HOST CLEAN INSTALL） | 候选副本新建 Python venv 执行 `uv sync --locked` 与 `uv lock --check`；前端执行 `pnpm install --frozen-lockfile`；当前主机 Python 3.14.6、uv 0.11.31、Node 24.19.0、pnpm 11.25.0。 |
| 自动回归 | PASS | 后端全量 195 passed / 23 warnings；前端 39 passed；TypeScript 退出 0；Vite 构建退出 0（既有单 chunk >500 kB 提示）；Playwright 5 passed。一次过期文案断言失败后更新候选状态文案与断言，再次全套通过。 |
| 关键隔离安装 | PASS（同机合成） | 空库升级到 `a6c7d8e9f0a1`；本机 CLI 初始化合成账户、创建一次性邀请码；Playwright 覆盖邀请注册、登录、任务读回、Review、Proposal 审批、双用户 Task 隔离。没有调用真实 Provider。 |
| 迁移与恢复 | PASS（合成范围） | 定向 `tests/test_schema_startup_guard.py tests/test_p1b_account_migration.py tests/test_backup_restore.py tests/test_registration_workflow.py tests/test_invitation_service.py` 为 50 passed / 20 warnings；另将合成库回退至 v0.3.3 revision `f3a4b5c6d7e8` 再升级 Head，用户记录保留；备份恢复检查 `integrity_check=ok`、外键违规 0。历史 R4a-2 还验证合成 Task/Knowledge 记录升级保留。真实个人旧库未使用。 |
| 第三方许可 | PASS（源码分发范围） | 锁定安装扫描 38 个 Python 第三方发行包、207 个 npm 包；npm 均有 license 元数据，Python 唯一未注明项是本项目自身发行包。核对到 certifi 的 MPL-2.0、cryptography/packaging 的双许可证，以及 lightningcss 的 MPL-2.0、lru-cache 的 BlueOak-1.0.0、mdn-data 的 CC0-1.0 上游许可文件。依赖源码和 Vite 构建产物不随源码仓库提交；下游分发依赖/构建包须保留各自条款。未发现本候选必须附带第三方 NOTICE 的 vendored source。 |
| 敏感文件与历史 | PASS（限定候选） | 最终导出从 161 路径白名单逐项复制，不带旧 `.git` 历史；剔除 `.env`、SQLite、备份、日志、媒体、依赖目录和构建输出。有限敏感字符串与路径扫描不构成完整 DLP 保证。 |
| GitHub 远端 | BLOCKED（尚非公开） | 认证 GitHub 元数据显示 `JXcloudforge/personal-os` 存在但为 Private、size 0、默认分支 `main`；本机 `git ls-remote` 无 refs。此轮不改变私有/公开状态。创建公开 Release 前需要单独批准可见性变更或选择新公开仓库。 |
| 正式部署 | NOT VERIFIED | Windows 正式数据 ACL 仍待修复，现有正式 8000 服务未确认升级至 v0.4.0；本轮同机隔离结果不覆盖生产服务或全新 Windows 主机。 |

安装与回归证据只证明 SAME-HOST CLEAN INSTALL；不声称全新主机、真实生产 ACL、正式服务升级、公网部署或真实模型质量验收。


### R4a-2 最终公开候选构建（2026-10-20）

| 检查 | 状态 | 证据与边界 |
|---|---|---|
| 精确导出 | PASS | 依照白名单生成 158 个源文件，旁置 SHA256SUMS；候选根目录无 `.git`，无 `.env`、SQLite、备份、日志或构建依赖目录。运行依赖和测试产物保存在独立验证副本，不进入公开候选。 |
| 公开文档/路径扫描 | PASS（有限扫描） | 脱敏文档扫描未发现本机用户、SID、Tailnet 主机名或私人路径；发现首账户初始化 UI 含开发机绝对路径，候选副本已改为 `<PERSONAL_OS_ROOT>`。这是候选副本修改，须合并回正式源码前复核。有限扫描不等于完整 DLP 保证。 |
| 冻结安装与构建 | PASS（SAME-HOST CLEAN INSTALL） | Python 3.14.6、uv 0.11.31 执行 `uv sync --locked` 与 `uv lock --check`；Node 24.19.0、pnpm 11.25.0 执行 `pnpm install --frozen-lockfile`、TypeScript 与 Vite 生产构建。其他 Python、Node、Windows 版本未验证。Vite 有一个大于 500 kB 的 chunk 警告。 |
| 空库迁移 | PASS | 全新合成 SQLite 从空库升级至 `a6c7d8e9f0a1`，数据库 revision 与代码 head 一致。 |
| v0.3.3 合成旧库升级 | PASS（同机合成） | 使用 v0.3.3 Tag 中的 `f3a4b5c6d7e8` revision 建库，写入合成 User、Task、KnowledgeCard，再升级至 `a6c7d8e9f0a1`；记录、用户归属、integrity_check 与外键检查均通过。未读取真实旧库。 |
| 初始化、邀请与注册 | PASS（合成） | 本机 CLI 初始化合成首账户并创建一次性邀请；Web API 使用该 CLI 签发邀请完成注册、登录、Session 读取、Task 创建与读回。邀请码/密码未写入公开候选或验收输出。Playwright 注册 E2E 使用独立临时数据库的测试邀请。 |
| Agent/Proposal、Knowledge、Review 浏览器链 | PASS（隔离 Mock） | Playwright 5/5：工程记录、Proposal 审批、Review、自助邀请注册、Task 持久化、退出及双用户 Task 隔离；仅 Mock Provider。 |
| 备份恢复 | PASS（合成 SQLite） | 项目备份脚本将合成库备份并恢复到新目标；两者 `integrity_check=ok`、外键违规 0、记录数与 Alembic revision 一致。 |
| 自动化回归 | PASS | 后端 195 passed（23 warnings）；前端 Vitest 39 passed；Playwright 5 passed；Schema Guard/合成旧库迁移/备份恢复专项 25 passed（11 warnings）。仅指本次同机隔离环境。 |
| 依赖许可证元数据 | PARTIAL | 安装环境清单含 Python 38 个第三方发行包与 npm 207 个包；npm 均有 package.json license 字段，Python 有一个未注明项是本项目自身发行包。发现 MPL-2.0、BlueOak-1.0.0、CC0-1.0 等标识。CSV 清单保留在候选副本之外；未逐包核对许可证正文、NOTICE、源文件版权或商标义务。 |
| LICENSE/权属 | FAIL / 待负责人确认 | MIT 草案仍含 `[YEAR]` 与 `[COPYRIGHT HOLDER]` 占位符。AI 辅助代码、原始源码与文档的权利授权须由权利人确认；本轮不添加 LICENSE。 |
| 全新 Windows 机器、正式 ACL/服务/Tailscale | NOT VERIFIED | 同机 TEMP 环境不等于全新机器验收。正式 Windows ACL、服务升级和 Tailnet 部署不属于本轮验证。 |

候选 E2E 仅为避开已占用的本机端口，在验证副本调整了隔离端口与 Mock Provider 端口；测试数据库哨兵指向 TEMP 合成路径。最终干净导出与验证副本的 158 个白名单文件哈希一致后才交付。候选中的首账户路径文案也做了 `<PERSONAL_OS_ROOT>` 替换。
