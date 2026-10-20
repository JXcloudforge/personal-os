# Personal OS

Windows 自托管 AI Agent 工作流平台，面向希望把个人任务、知识来源和 AI 建议组织成可追溯、由用户授权行动流程的个人用户。它避免模型建议被误当成正式业务事实。

## 版本与能力

- **v0.4.0 · Source Release Candidate**：公开源码候选版本；尚未发布 GitHub Release，也不表示正式服务已升级。
- **v0.3.3**：保留为上一条历史发布记录。正式 Windows ACL 仍待修复，当前正式 8000 服务尚未升级到 v0.4.0。
- 当前代码包含 Task、Review、Knowledge、Chat、Agent/Proposal、账户 Session 和本机备份相关能力。真实 Provider 质量、全面双用户隔离和公网部署尚未验收；P0.4c-2 仅在同一主机的独立目录验证了锁定依赖安装、合成旧库升级与恢复。
- **邀请注册范围：** 本机可信 CLI 发放一次性邀请码；Web 登录页已提供邀请注册，后端在统一事务中创建账户、领取邀请并创建 Session。临时 SQLite 浏览器验收覆盖了注册、登录恢复、退出、任务刷新持久化和 Task 双用户读写隔离；其他资源的全量双用户矩阵、生产代理/HTTPS 与 v0.4.0 整体发布验收仍待完成。不是开放式自助注册。
- R0 世界模型、贝叶斯更新、决策引擎和行动反馈只是设计，不是现有功能。

## 架构

```text
React / Vite / TypeScript
  → HTTP（Session Cookie / CSRF）
  → FastAPI Routes（认证、请求/响应）
  → Service（业务规则、授权、事务）
  → SQLAlchemy Session / ORM
  → SQLite
```

Agent Runner 异步请求已配置 Provider，Context Builder 限制个人数据范围，Tool Registry 只开放受控工具。任务草案先成为 Proposal `PENDING`；用户批准后才由 Task Service 创建正式 Task `TODO`。

**技术栈：** React、Vite、TypeScript、pnpm；FastAPI、SQLAlchemy、SQLite、Alembic；Agent Runner/Tool Registry；Windows 本地运维与 Tailscale 私有 HTTPS。

## Quick Start

候选运行时契约：Python `>=3.11`、uv `>=0.11.31,<0.12`、Node `>=24.19.0 <25`、pnpm `11.25.0`。本机实际演练版本为 Python 3.14.6、uv 0.11.31、Node 24.19.0、pnpm 11.25.0；其他 Python/Windows 版本尚未在本轮验证。前端依赖使用锁文件对应的精确版本，并要求冻结安装。依照 `backend/.env.example` 创建本机配置，给 `PERSONAL_OS_DATABASE_URL` 指定全新且独立的 SQLite 路径，设置随机长 `PERSONAL_OS_SECRET_KEY`。不要把正式 `.env` 或数据库路径复制进测试环境。

```powershell
cd backend
uv sync --locked
uv run python -c "from alembic.config import Config; from alembic import command; from app.config import settings; c=Config('alembic.ini'); c.set_main_option('sqlalchemy.url', settings.database_url.replace('%', '%%')); command.upgrade(c, 'head')"
uv run python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

另开终端：

```powershell
cd frontend
pnpm install --frozen-lockfile
pnpm dev
```

- 配置模板：`backend/.env.example`；后端依赖锁：`backend/uv.lock`；前端锁：`frontend/pnpm-lock.yaml`。
- 上述迁移命令明确使用应用配置的数据库 URL，并转义 Windows URL 中的 `%` 以满足 ConfigParser；`alembic.ini` 的静态默认 URL 不会自动跟随 `.env`。执行前先检查目标路径；旧库升级须先在合成库或受保护副本演练并有可验证备份。应用启动不负责自动迁移。
- v0.4 开发代码在 `production` 启动时先以只读连接核验 SQLite 的单个 Alembic revision 与代码迁移 Head 一致，失败则拒绝启动且不启动后台备份调度器；先完成受信任的 `alembic upgrade head` 再启动。`/api/health` 的 200 仅表示已启动实例的基础数据库查询成功，不替代备份恢复、完整 schema 或生产部署验收。
- 首账号可通过本机可信 `uv run python -m scripts.initialize_admin` 初始化；其后本机交互终端用 `uv run python -m scripts.manage_invites create` 发放一次性邀请码。注册页面使用该邀请码；普通 Web 用户不能管理邀请码或整库备份。
- 本机 SQLite 快照：`uv run python ../scripts/backup_database.py backup <源库> <新备份路径>`；恢复只写不存在的目标：`uv run python ../scripts/backup_database.py restore <备份路径> <新目标路径>`。普通快照保留 Session 且不含独立的 Provider 主密钥；跨实例迁移包的恢复 CLI 已存在，但 Web 整库导出仍封堵且缺少可信本机导出 CLI。见[安装与恢复演练](docs/architecture/PROJECT_ENGINEERING_LEDGER.md#p04c-2-独立安装升级与恢复验收)。
- Windows/Tailscale 是私有主机部署，不等于公网 SaaS；无 Provider Key 时 AI 路径不可用，手工业务路径可单独使用。

## 测试

```powershell
cd backend
uv run pytest

cd frontend
pnpm test
pnpm exec tsc --noEmit
pnpm exec vite build --outDir <独立临时目录> --emptyOutDir
pnpm test:e2e
```

只在已确认隔离 SQLite、临时目录和 Mock Provider 后运行。每次结果按具体切片报告，不把历史基线当作本轮重跑，也不把 Mock 或构建结果当作生产验收。

## 文档入口

- [全历史工程总账](docs/architecture/PROJECT_ENGINEERING_LEDGER.md)：Git 时间线、版本/功能矩阵、事故、架构、复刻顺序和面试材料。
- [更新日志](CHANGELOG.md) · [发布版本事实](release_history.json)
- [v0.4.0 Source Release Notes](docs/releases/v0.4.0-RELEASE-NOTES.md)
- [Task 生命周期](docs/architecture/task-lifecycle-engineering.md) · [Task 契约](docs/architecture/task-module-contract.md)
- [Knowledge 契约](docs/architecture/knowledge-module-contract.md) · [Review 契约](docs/architecture/review-module-contract.md)
- [账户邀请与授权设计](docs/architecture/account-invitation-and-authorization-design.md)
- [v0.4.0 Release Candidate 检查清单](docs/releases/RELEASE_CANDIDATE_CHECKLIST.md)：发布门槛、未验证边界和公开文件风险。
- [公开文件白名单](docs/releases/PUBLIC_FILE_ALLOWLIST.md)：逐项列出候选文件，发布前仍需复核 staged diff。
- [项目证据包](docs/portfolio/PROJECT_EVIDENCE.md)：架构、验证结果、演示路径与技术边界。
- **许可证：** MIT；见根目录 [LICENSE](LICENSE)。第三方依赖仍按各自许可条款分发，本仓库不包含依赖源代码副本。
- [长期宪章](docs/architecture/personal-os-charter.md) · [AS-IS 架构](docs/architecture/personal-os-as-is-audit.md) · [目标契约](docs/architecture/personal-os-target-contracts.md) · [R0 路线](docs/architecture/personal-os-r0-roadmap-and-acceptance.md)

项目根目录 README 维护另一个个人迭代系统的工作规范；本应用的产品说明和工程入口统一放在本文件。
