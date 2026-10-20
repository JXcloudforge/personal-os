# Windows 后台生命周期与部署

当前已发布版本记录为 v0.3.3；下述 Windows 管理器流程形成于 v0.3.2 阶段。用户当时已确认生命周期验收完成；该确认不代表 Codex 执行了桌面或生产生命周期操作。事故细节与证据边界唯一来源：[生命周期复盘](../postmortems/2026-10-13-background-lifecycle.md)。

## 正式启动方法

唯一入口 PersonalOS-Manager.cmd。管理器使用 Start-Process Hidden 启动现有 backend/.venv/Scripts/pythonw.exe、原 windows_service.py，工作目录固定 backend。关闭窗口或菜单退出不停止服务；仅显式停止/重启会结束已核验实例。无控制台不是 Windows Service，不保证注销后在线。

```powershell
& '<APP_ROOT>\app\scripts\personal_os_manager.ps1' -Action Start
& '<APP_ROOT>\app\scripts\personal_os_manager.ps1' -Action Status
# 等待现有请求结束后才明确停止/重启：
& '<APP_ROOT>\app\scripts\personal_os_manager.ps1' -Action Stop
& '<APP_ROOT>\app\scripts\personal_os_manager.ps1' -Action Restart
```

脚本 UTF-8 BOM 兼容 Windows PowerShell 5.1，保留 CMD 原 UTF-8 加载方式，不绕过执行策略。当前登录 Session 互斥锁串行化启动，健康实例复用，不健康或外来监听拒绝重复启动。启动超时报告 launcher 退出码或运行中状态，不盲目重试。

## 配置与数据

backend/.env 从模块绝对位置读取。正式 SQLite 固定 <APP_DATA_DIR>\personal_os.sqlite3，缺失拒绝启动。保留原账号、Fernet 主密钥、媒体、日志、备份；不做 LocalService/SID/ACL 迁移。

v0.4 开发代码的 production 启动契约：部署者先确认目标数据库与可恢复备份，再显式执行 Alembic `upgrade head`；FastAPI Lifespan 在备份调度器启动之前从迁移脚本确定唯一 Head，并用 SQLite 只读连接读取目标库唯一的 `alembic_version`。库/版本缺失、旧版、未知版、多版本或不可读均中止启动，错误仅包含排障类别，不输出数据库内容。启动不会执行 `create_all` 或自动迁移；迁移失败后停止部署，保留原库并在隔离副本排查。该检查只证实 revision 一致，不逐表证明 schema 完整性。

**探针边界：** Liveness 回答进程是否存活；Readiness 回答实例能否承接业务。当前没有单独的 readiness API，production 通过拒绝不兼容 schema 的进程启动阻止 `/api/health` 错报 200；健康接口继续执行基础 `SELECT 1`，其 200 不等于完整业务读写、备份恢复或 Tailscale HTTPS 验收。

**v0.4 安全边界审计：** 只读执行 `tailscale serve status` 成功，当前配置显示 HTTPS Serve、Tailnet only，输出没有 Funnel 配置；没有向正式服务发送请求或改动 Serve。测试中的 HTTPS/TestClient 和 `X-Forwarded-Host` 只验证代码分支，不代替远端浏览器验收真实 Origin、代理清洗、限流来源及 Secure Cookie。

Windows ACL 元数据核查发现 `app/data`、`app/data/private` 与 `theme-backgrounds` 的继承 ACL 对 Authenticated Users 赋予 Modify、Builtin Users 赋予 Read/Execute；没有读取这些目录中的文件。计划任务配置为 Interactive/Limited，但本次没有核验活动服务进程与计划任务账户 SID 是否相同。背景目录的 POSIX 风格 `chmod(0700)` 失败时会忽略异常，不能覆盖该 ACL。修复 ACL 前需先设计并验证服务账户访问、升级继承、备份/恢复权限，再安排经授权的部署操作；本轮不修改现有 ACL。

Provider 安全由两个不同边界构成：数据库用户凭证按 user_id 加密存储；P0.4c-3a 后，普通 Web 用户的模型调用只允许使用本人启用的凭证，不从 `PERSONAL_OS_DEEPSEEK_API_KEY` 自动回退。仅有进程 Key 的旧账户需本人在“设置 → 模型 Provider”录入个人 Key。备份/迁移包仍有独立的环境凭证运维路径，本轮没有验证真实包内容。现有 Token 统计不是费用控制。

文件日志为当前 LOCALAPPDATA/PersonalOS/logs/fastapi.log，2 MB 轮转、3 份；manager-lifecycle.log 记录明确启停和 launcher PID。run-state 缺少清理只证明未正常结束，不证明终止者。菜单 5 查看日志。

## 状态、停止与端口冲突

以唯一 127.0.0.1:8000 监听和健康 status=ok、database=ok 为准。分离启动后计划任务 Ready 不代表离线。旧直接托管任务 Running 时先核验动作再 Stop-ScheduledTask；独立进程重新核验 PID/项目命令行后只停止该 PID。身份不可读则拒绝停止，不误杀其他 Python。停止可能中断请求，不能保证退出日志完整。

```powershell
Get-NetTCPConnection -LocalPort 8000 -State Listen
Invoke-RestMethod 'http://127.0.0.1:8000/api/health'
Invoke-RestMethod 'https://<tailnet-host>/api/health'
tailscale serve status
```

FastAPI 仅绑定 loopback；Serve 保持 tailnet only，不开 Funnel。Tailnet 502 先查本机健康，不重置账号。健康正常后登录由用户本人使用原密码验收。

## 登录自启：可配置、未验收

用户明确选择菜单安装/修复才更新 <APP_SERVICE_TASK>，先导出旧 XML，仅接受已知旧/新动作。保留当前用户 Interactive + AtLogOn；新动作 PowerShell -File 管理脚本 -Action Start 调用同一分离路线。无额外候选任务，不使用 LocalService/S4U。权限不足停止，管理员操作需另行批准，不自动提升项目脚本。

登录动作退出后不持续监督 pythonw；旧 RestartCount 不保证应用异常恢复。本轮未注册或更改正式任务，注销/登录及 Windows 重启测试均待验收。

## 验收、部署与回滚

保留健康进程，不无意义重启。执行应用代码更新前先隔离 pytest、Vitest、类型检查和构建。若需部署，由用户在管理器明确重启后核验新版路径。关窗、原账号业务访问和重复启动按用户验收记录分别验证。

登录自启、Windows 重启和异常恢复本轮未执行，仍为 Pending。release_history.json 是版本唯一来源，系统 API 与前端读取同一版本信息。

保留实机确认的 host-verified 保护快照，不覆盖备份。运维回滚仅恢复本轮管理器/入口，不替换数据库或 .env。若后来改过任务，用改动前 XML 恢复；确保端口无双实例。可使用用户已实测 Start-Process 路线恢复，不回退业务代码。

v0.3.2 建立本地 Git 基线，不推送远程。运维回滚不替换数据库或 .env。关机、睡眠、注销和异常自动恢复未覆盖。

## R1 · 2026-10-20 只读运行核对与本机隔离验收

正式运行快照的账户、进程与主机细节已移至私有运维记录。公开记录只保留结论：运行实例与当前源码版本的对应关系需在维护窗口独立核验。

通过正式进程只读 GET `/api/health` 得到 `status=ok,database=ok`；`/openapi.json` 的应用版本是 `0.3.3`，但路由表含 `/api/auth/register`、Review、Knowledge、Proposal 等 v0.4 开发路由。当前 `routes.py`、`main.py`、Provider Service 和 `schema_startup.py` 的工作区修改时间晚于该进程启动时间；正式 HTML 仍引用 2026-10-14 构建的 JS/CSS，两个资源 GET 均为 200，JS bundle 未含“邀请码注册”入口。Manager `Status` 核验任务动作符合预期，但只能确认 PID 由当前项目启动器管理，不能反推出进程启动时的源码快照。因此运行中的进程/静态资源不是当前工作区的可证明快照，不能把其 `0.3.3` 标签解释为干净的 v0.3.3 基线，也不能据此远程写入测试数据。正式数据库 revision 未读取，NOT VERIFIED；当前健康 200 只证明基础数据库连接查询成功。

当前 Cookie 契约由设置控制：生产启动要求 `PERSONAL_OS_COOKIE_SECURE=true`；Session 为 HttpOnly、SameSite=Lax，客户端 CSRF Cookie 可读并随写请求发送 `X-CSRF-Token`。认证写入还检查 Origin 与 Host/Forwarded-Host。FastAPI TrustedHost 包含 `*.ts.net`。本机模拟没有验证 Tailscale Serve 实际转发头、手机浏览器 Cookie 或真实证书/Origin。

本机隔离验收使用排除 `.env`、正式 `app/data` 和备份的 TEMP 源码副本、合成 SQLite 和 Mock Provider：空库 Alembic 迁移成功；生产配置实例通过 Schema Startup Guard，健康、页面和 JS/CSS 分别返回 200；Playwright `5 passed`（注册/登录/Session、Task 刷新持久化和双用户隔离、Knowledge/Proposal/批准、Review、错误反馈）；Schema Startup Guard 单测 `10 passed`。正式服务和 Tailscale Serve 没有改动。手机真实设备测试仍为 WAITING FOR DEVICE TEST。

Windows ACL 只读核验补充：活动任务 principal 和监听进程 SID 相同，但 `app/data`、SQLite、`private/theme-backgrounds`、`app/data/backups` 仍继承 `Authenticated Users: Modify` 与 `BUILTIN\Users: ReadAndExecute`。实际备份服务默认目录在 `%LOCALAPPDATA%\PersonalOS\backups`，其 ACL 较窄，但存在 `CodexSandboxUsers: ReadAndExecute` ACE，须核验组成员和用途。ACL 风险尚未修复，正式外部用户访问仍阻断。

经负责人批准后，临时远程测试可以仅新增 HTTPS 8443 → `127.0.0.1:8301` 的 Tailnet Serve 映射；移除时应使用相同 HTTPS 端口的 `off` 命令，只撤销该端点。不得使用 `tailscale serve reset`，以免清除现有 443 → 8000 映射。精确命令与回退步骤见[项目工程总账 R1 记录](../architecture/PROJECT_ENGINEERING_LEDGER.md#remote-delivery-sprint-r1--运行核对接口地图与排障)。

## R2 · 邀请初始化与 Windows ACL 收口准备

### 8443 合成实例

R2 使用隔离源码副本，FastAPI 仅绑定 `127.0.0.1:8301`，SQLite 位于 TEMP 源码副本的 `app/data/personal_os.sqlite3`；Profile、`LOCALAPPDATA` 和备份目录也定向到同一 TEMP 根目录。通过显式 `PERSONAL_OS_DATABASE_URL` 在隔离实例上运行 `backend/scripts/initialize_admin.py`，创建合成首账户；该 CLI 不授予 Web 管理员角色。随后 `/api/auth/status` 返回 `setup_required=false`，未领取的邀请码仍为 OPEN，HTTPS 注册 challenge 返回 200。正式 SQLite、正式 `.env`、备份和 443 映射均未作为初始化目标。

当前经负责人批准的临时映射为 `https://<tailnet-host>:8443/ → http://127.0.0.1:8301`；原 `443 → 127.0.0.1:8000` 保持不变。两个入口均为 tailnet only，未启用 Funnel。主机经 Tailnet HTTPS 请求 health 返回 200，注册 challenge Cookie 显示 `HttpOnly; Secure; SameSite=Strict`；这些结果不代替手机浏览器验收。手机注册、Session 刷新、Task、Review、Knowledge 和 Agent/Proposal 流程仍待负责人实测。完成后只执行 `tailscale serve --bg --https=8443 off`，再确认 443 映射未变；不得执行 `serve reset`。

### Windows 运行身份与数据 ACL

当前计划任务 `<APP_SERVICE_TASK>` 和正式 8000 监听进程均由 `<WINDOWS_USER>`（SID `<WINDOWS_USER_SID>`）运行。`NT AUTHORITY\SYSTEM` 和 `BUILTIN\Administrators` 作为系统维护主体保留。Windows 运行身份是操作系统文件访问主体；应用层 Session 的 `user_id` 是业务授权主体。`user_id` 过滤不能阻止同机进程直接读取 SQLite，NTFS ACL 也不能替代 API 的资源 ownership 校验。

此前只读基线显示 `app/data` 及其子项继承 `Authenticated Users: Modify` 与 `BUILTIN\Users: ReadAndExecute`。R2 元数据复核包含 19 个对象，均未保护继承、没有显式 ACE；SQLite 文件内容未读取。独立备份目录 `%LOCALAPPDATA%\PersonalOS\backups` 有 30 个对象，同样未保护继承、没有显式 ACE；其继承 ACL 含 `CodexSandboxUsers: ReadAndExecute`，本机组成员包括 CodexSandbox 运行账户，需在维护前确认该访问是否仍需要。当前 Codex PowerShell 未提升为管理员；实际运行用户对受保护正式 ACL 的写权限没有在正式路径上试改。

**建议的最小正式 ACL：** 对 `<APP_DATA_DIR>` 和独立备份目录分别保护根目录 DACL，并仅授予服务账户 `<WINDOWS_USER>` 对子目录/文件继承的 Modify，授予 SYSTEM 和本机 Administrators Full Control；不给 Authenticated Users、BUILTIN Users 或 CodexSandbox 组数据访问。服务维持 `<WINDOWS_USER>` 身份，暂不迁移服务账户。SQLite/WAL、私有主题文件和备份读写必须在维护窗口验证后才恢复服务。

先在提升的 PowerShell 维护窗口保存 ACL 快照，并记录退出码与哈希：

```powershell
$data = '<APP_DATA_DIR>'
$backup = Join-Path $env:LOCALAPPDATA 'PersonalOS\backups'
$snapshotDir = Join-Path $env:LOCALAPPDATA 'PersonalOS\acl-baseline-R2'
New-Item -ItemType Directory -Force $snapshotDir | Out-Null
icacls $data /save (Join-Path $snapshotDir 'app-data.acl') /T /C
icacls $backup /save (Join-Path $snapshotDir 'backups.acl') /T /C
Get-FileHash (Join-Path $snapshotDir 'app-data.acl'), (Join-Path $snapshotDir 'backups.acl') -Algorithm SHA256
```

确认两份快照成功且父目录 ACL 不会改变后，逐个目录执行并逐项验证；每个根路径单独处理，避免权限范围扩大：

```powershell
$principals = @('<WINDOWS_USER>:(OI)(CI)(M)', 'NT AUTHORITY\SYSTEM:(OI)(CI)(F)', 'BUILTIN\Administrators:(OI)(CI)(F)')
icacls $data /inheritance:r
icacls $data /grant:r $principals
icacls $backup /inheritance:r
icacls $backup /grant:r $principals
```

验收必须确认 data、`private/theme-backgrounds`、data 内备份目录和独立备份目录的子项均不再包含宽泛主体，`<WINDOWS_USER>` 可启动应用、执行 SQLite 写入、主题文件写入、备份与恢复；同时运行 `/api/health` 与隔离业务读写回归。ACL 修改后若任一检查失败，先停止 FastAPI，再只回退本次改动。

R2 TEMP 演练中，`<WINDOWS_USER>:Modify`、SYSTEM/Administrators Full Control 足以创建 SQLite、复制、删除并恢复合成备份文件。`icacls /reset /T /C` 在所有对象原本只继承且父级未改动时，逐对象 DACL 与基线完全一致；本轮正式 `app/data` 19 个对象和独立备份 30 个对象均满足这个前提。非提升 shell 的 `icacls /restore` 曾返回 Windows 错误 1300，因此不能把该命令作为未经验证的现场回退。快照仍须保留；若 ACL 出现显式项、父级改动或 reset 验收不一致，应在管理员维护窗口用保存快照的 `/restore` 精确恢复并核对 DACL，未成功前不重启服务。

```powershell
icacls $data /reset /T /C
icacls $backup /reset /T /C
```

**新安装权限检查：** 在执行 Alembic 和首次启动前，以最终计划任务账户检查数据根路径及目标备份路径存在、可写，并用 `icacls <path> /T /C` 确认继承主体；不要通过临时授予 Users/Authenticated Users 权限来排障。若 health 失败，先确认任务账户、实际 FastAPI PID 所属 Windows 账户、SQLite 路径、ACL 和 schema revision 一致，再检查应用日志中的非敏感 request id/错误类别。ACL 拒绝属于操作系统文件访问问题；登录成功但访问他人任务被拒绝属于应用层 `user_id` 授权问题。优先在 TEMP 合成数据库复现，不读取正式 DB 内容。
