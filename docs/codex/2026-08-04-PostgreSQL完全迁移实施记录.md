# PostgreSQL 完全迁移实施记录

## 背景与目标

项目的 Docker 服务已经连接 PostgreSQL，但工作区仍保留旧版 `data/app.db`、安装测试数据库和 SQLite 运行回退。旧库中还有大量没有进入 PostgreSQL 的历史任务、选题、关注文章和评审记录，不能直接删除。

本次目标是：在不覆盖 PostgreSQL 新数据的前提下，将旧 SQLite 业务数据按用户归属合并到 PostgreSQL；把旧凭证转换为服务器可用的加密格式；禁止应用运行时回退 SQLite；完成验证后删除工作区中的 SQLite 数据文件。

## 迁移边界

- 迁移目标：本机 Docker PostgreSQL `wechat_publisher`。
- 迁移源：原 `data/app.db`。
- PostgreSQL 现有数据优先，SQLite 历史数据以映射和追加方式合并。
- `token_cache` 和 `user_sessions` 属于短期认证状态，不迁移，服务重启后重新签发。
- 不修改远端生产服务器 `47.99.126.8`。
- SQLite 解析器仅保留在自动化测试和一次性历史导入工具中；它不再是应用运行数据库，也不会创建 `data/app.db`。

## 安全措施与备份

迁移前停止用户端、API 和管理端写入，只保留 PostgreSQL。备份目录：

`data/backups/postgres-cutover-20260804-100702`

其中包含：

| 文件 | 用途 | SHA-256 |
|---|---|---|
| `postgres-before-merge.dump` | 合并前 PostgreSQL 的 `pg_dump -Fc` 备份 | `8490115567EF7B3449C1970E143D6398F5E44C17803DE07414CC368C7360C95A` |
| `sqlite-primary-before-merge.zip` | 主历史 SQLite 的压缩回滚副本 | `74957B4F54C0784C7520A6B8BA80F0BBD0F3D70ADA0E0AE392D245BBDA21C64F` |
| `all-sqlite-files-before-cleanup.zip` | 删除前全部 7 个 SQLite 数据文件归档 | `121E4D251337D8F09A772F8E2687D66B581EF1D9C8A01F11DA0C85245C06CAC7` |

PostgreSQL dump 已通过 `pg_restore -l` 读取验证；两个 ZIP 均通过 CRC 完整性检查。

## 数据合并规则

迁移工具采用单个 PostgreSQL 事务，并记录源库 SHA-256 迁移标记，重复执行不会重复导入。

- 用户按规范化用户名映射，所有客户数据重写为 PostgreSQL 用户 ID。
- 公众号按“用户＋AppID”映射，禁止相同 AppID 跨用户合并所有权。
- 平台模型按厂商协议、API 地址和模型名映射。
- 任务全部追加新的 PostgreSQL 序列 ID，并同步重写批次关系、版本、评审和尝试记录。
- 选题来源按“用户＋source_key”映射，选题条目跟随来源 ID 重写。
- 关注公众号按“用户＋名称”映射，关注文章跟随所属账号重写。
- 提示词、创作方案和评审方案按用户及名称映射。
- 用户设置跟随用户 ID；平台设置仍保持平台共享。
- DPAPI/Base64 历史凭证在 Windows 原用户上下文解密后，用 `CREDENTIAL_ENCRYPTION_KEY` 重新加密为 Fernet 格式。

## 迁移结果

合并前后关键记录数：

| 表 | 合并前 PostgreSQL | 合并后 PostgreSQL |
|---|---:|---:|
| `jobs` | 6 | 187 |
| `batches` | 3 | 132 |
| `batch_jobs` | 6 | 187 |
| `topic_items` | 8 | 712 |
| `followed_articles` | 0 | 75 |
| `editorial_reviews` | 0 | 7 |
| `official_accounts` | 3 | 5 |
| `ai_models` | 1 | 2 |
| `job_versions` | 0 | 11 |
| `processed_events` | 0 | 29 |
| `prompt_templates` | 0 | 2 |

本次新增或映射：181 篇历史任务、129 个历史批次、704 条历史选题、75 篇关注文章、7 条评审、11 个文章版本、29 个已处理事件、2 个公众号和 1 个模型。另有 3 个公众号、4 个关注账号、6 个选题来源和 2 个用户映射到 PostgreSQL 现有逻辑记录。

共 8 处现有或历史敏感凭证被转换为服务器可移植加密格式。

## 完整性验证

以下检查全部为 0：

- 找不到任务的批次任务关系；
- 找不到批次的批次任务关系；
- 找不到来源的选题；
- 找不到关注账号的关注文章；
- 找不到用户的任务、批次或公众号；
- 同一用户下重复的 `source_key`；
- 非 Fernet 格式的模型密钥；
- 非 Fernet 格式的公众号 AppSecret。

使用同一个源库再次执行迁移返回 `already_migrated: 1`，证明幂等保护生效。

## PostgreSQL-only 收口

- `DATABASE_URL` 现在是运行时必填项，只接受 PostgreSQL URL。
- 无 PostgreSQL 配置时，API、用户端、管理端和本地桌面入口在监听或打开窗口前失败，不会退回 `data/app.db`。
- 当前配置和示例配置只声明 PostgreSQL。
- 安装包远程客户端自检不再构造数据库、不再查询 `sqlite_master`/`PRAGMA`，也不会生成本地数据库。
- 生产 Compose 强制要求数据库、会话签名和凭证加密密钥。
- 工作区原有 7 个 `.db` 数据文件已在备份验证后删除，当前活动数据只保存在 PostgreSQL。

## 风险与运维要求

1. 必须同时备份 PostgreSQL 和 `CREDENTIAL_ENCRYPTION_KEY`；丢失加密密钥会导致模型和公众号凭证无法解密。
2. 不要随意修改已经运行实例的 `CREDENTIAL_ENCRYPTION_KEY`。需要轮换时必须提供显式的密钥轮换迁移。
3. 回滚时先停止三个应用服务，再使用 `postgres-before-merge.dump` 恢复到新数据库；不要在活动库上直接覆盖。
4. SQLite 归档只用于约定保留期内的灾难恢复，不应重新挂载为运行数据库。

## 验收标准

- PostgreSQL 容器健康，用户端、API 和管理端均能启动并返回成功响应。
- `lanxue` 和普通用户登录后只能读取自己的公众号、任务和设置。
- 历史任务、选题、关注文章和评审可从 PostgreSQL 查询。
- 新注册用户首次打开选题中心不再触发默认来源所有权冲突。
- 完整自动化测试通过；测试结束后工作区仍不存在活动 `.db` 文件。

## 最终验收结果

- 自动化测试：`802 passed`，仅有第三方库弃用警告，无测试失败。
- 测试隔离回归：修复了一个 UI 测试会在项目 `data/` 下隐式创建测试库的问题；第二次完整测试后扫描结果为 0 个 `.db/.sqlite/.sqlite3` 文件。
- 静态检查：本次涉及文件的 import 规则和 Pyflakes 检查通过；Python `compileall` 通过。项目中仍有历史行长和现代化风格提示，但不影响运行。
- Docker：重新构建并启动 `api`、`web`、`admin`，三个服务均确认使用 PostgreSQL。
- HTTP 冒烟：API `18776/health`、用户端 `18775/`、管理端 `18777/` 均返回 200。
- 认证租户冒烟：默认管理员与临时注册用户各获得 6 个默认选题来源，来源键相同但数据库 ID 完全隔离；临时用户和测试数据随后已清理。
- 容器日志：重启和认证冒烟后未发现 `Internal Server Error`、所有权冲突、deleted client 或 Python traceback。
- 最终关键数量：187 篇任务、132 个批次、187 条批次任务关系、712 条选题、75 篇关注文章、7 条评审、2 个用户。

## 迁移后功能读取与线上影响复核（发布前）

2026-08-04 使用默认管理员通过真实 HTTP 登录链路，对迁移后的本地 PostgreSQL 执行了只读功能巡检。以下接口全部返回 200：

- 公众号列表：4 个启用账号（数据库共 5 个账号，其中 1 个停用）；
- 平台文本模型和图片模型：各 1 个启用模型；
- 新手配置状态和逐公众号微信连接健康：正常返回；
- 选题来源：6 个；最近一年可见选题：680 条；
- 关注公众号：6 个；默认过滤条件下可见关注文章：74 篇；
- 历史批次：132 个；
- 待审核收件箱：86 篇；
- 写入失败收件箱：1 篇；
- 生成失败收件箱：23 篇；
- 今日完成收件箱：0 篇。

接口数量与物理表数量存在合理差异：接口默认隐藏停用公众号、忽略文章及时间范围外记录，不代表迁移丢失。数据库归属复核显示，187 篇任务、132 个批次和 5 个公众号均属于原管理员 `lanxue`；另一历史用户 `eros` 没有被错误分配这些数据。

上述检查发生在生产发布前，当时没有修改 `47.99.126.8`。后续受控发布、备份、数据库增量迁移和线上验收结果记录在下一节。

## 生产发布记录

### Git 与发布版本

- GitHub 分支：`codex/production-git-deploy`。
- PostgreSQL 迁移与用户隔离主体提交：`5faec520873a1c973a875dc898d8148aa7293f28`。
- 公网审核链接修复提交：`5bdf471f515365bac4ee6864a1960e90e2e75f92`。
- 部署记录同步提交：`e42c957230aeb74e47477d133b16d6630f1d7192`；该提交只更新文档，应用源码与 `5bdf471` 一致。
- GitHub Draft PR：<https://github.com/Eors583/wechat-auto-publisher/pull/1>。
- 生产当前 release：`/opt/wechat-publisher/releases/git-e42c957230ae`。
- 服务器继续采用裸 Git 镜像、不可变 release 目录和 `current` 原子软链接切换；没有在生产目录直接修改源码。
- 第二个小版本发布时服务器到 GitHub 的 HTTPS 链路超时，因此从本地将同一 Git 提交直接推送到服务器裸仓库，再让既有发布脚本以 `SKIP_GIT_FETCH=true` 构建和切换；提交 SHA 与 GitHub 分支一致，发布方式仍保留完整 Git 版本记录。

### 发布前备份与密钥

发布前创建了服务器私有备份目录：

`/opt/wechat-publisher/backups/predeploy-20260804T035003Z`

备份包含生产 PostgreSQL 自定义格式快照、环境配置、当前 release 信息、Nginx 配置及迁移前计数。数据库快照信息：

- 文件：`postgres.dump`；
- 大小：185330 字节；
- SHA-256：`cbe50b0eb430fd41482d7807ac1f76e7a2fef24850fc819daa2b0d421fde729e`；
- `pg_restore -l` 校验：155 行目录记录，可正常读取；
- 备份目录和环境文件权限均限制为服务器管理员访问。

生产环境新增了独立、稳定的 `CREDENTIAL_ENCRYPTION_KEY`，只保存在权限为 `600` 的服务器环境文件及其私有备份中，没有写入 Git、日志或部署记录。发布前线上两个模型 Key 和两个公众号 AppSecret 均为旧 `base64:` 格式；新版仍兼容读取。为保持旧 release 的紧急代码回滚能力，本次没有立即批量改写为 Fernet。新保存的凭证会使用 Fernet；待回滚观察期结束后，应执行一次受控凭证轮换或重新保存，再移除旧格式兼容依赖。

### 生产数据库增量迁移

生产发布前后的业务数量保持一致：3 个用户、2 个公众号、2 个模型、5 个任务、5 个批次、5 条批次任务关系、6 个选题来源和 4 个关注公众号。升级后：

- 公共业务表由 29 张增至 30 张；
- 新建 `user_settings`，迁移得到 2 条用户设置；
- 9 张客户业务表建立 `owner_user_id`；
- 公众号、任务、批次、选题来源和关注公众号的空归属总数为 0；
- 未认证访问公众号、批次和选题来源 API 均返回 401；
- 管理员 API 范围读取到 2 个公众号，第二个启用用户范围读取到 0 个，与数据库各自归属计数一致。

### 服务与公网验收

最终生产镜像为 `wechat-auto-publisher:git-e42c957230ae`，其中应用源码与已验收的 `5bdf471` 完全一致。PostgreSQL、API、用户端和管理端四个容器均运行正常，API 容器健康状态为 `healthy`。以下入口实际返回 200：

- 用户端：<http://47.99.126.8/>；
- 管理端：<http://47.99.126.8/admin/>；
- Nginx API 健康路由：<http://47.99.126.8/publisher-api/health>；
- HTTPS API：<https://api.bluebloodlab.cn/health>；
- 服务器内部 `18775`、`18776`、`18777` 三端健康检查。

API、飞书生成的统一审核链接现在使用 `http://47.99.126.8/?view=review&batch_id=...&job_id=...`，不再指向从公网不可达的 `:18775`。模型、批次、选题来源、当前用户和待审核收件箱的已认证冒烟接口均正常；临时测试会话在验收结束后注销。四个生产容器最近日志未发现 Traceback、未处理异常、迁移失败、数据库锁或 `deleted client`。

### 已知限制与后续动作

1. 用户端和管理端目前只有 HTTP。`publisher.bluebloodlab.cn` 与 `publisher-admin.bluebloodlab.cn` 尚无 A 记录，无法签发可信 TLS 证书。正式向外部客户开放登录前，应先将两个域名解析到 `47.99.126.8`，再配置 HTTPS；在此之前至少应继续限制安全组来源。
2. 公网直连 `18775-18777` 从当前网络仍超时，但 Nginx 的 80 端口入口可用。生产访问应使用上述 Nginx URL，不应依赖容器端口。
3. 本次没有重置或读取现有用户密码，也没有执行真实微信草稿写入；认证中间件、会话、租户隔离和读取接口已经通过临时受控会话验证。真实公众号只读预检和测试草稿应由有业务权限的账号在界面中执行。
4. 本次主体版本发布前已经通过 802 项自动化测试；审核链接热修复另外通过 13 项相关回归测试和 Compose 配置校验。
