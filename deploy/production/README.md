# 单团队生产部署

当前版本适合单团队内部生产或受控试运行。普通用户之间尚未按租户隔离
公众号、任务和内容，不能直接作为多客户 SaaS 对公网开放。

## 服务

- 运营前台：`http://服务器IP:18775`
- API：`http://服务器IP:18776`
- 商户后台：`http://服务器IP:18777`
- PostgreSQL：仅 Docker 内部网络

## 启动

复制环境模板并替换三个相互独立的长随机值：

- `POSTGRES_PASSWORD`：PostgreSQL 数据库密码；
- `AUTH_STORAGE_SECRET`：登录会话签名密钥；
- `CREDENTIAL_ENCRYPTION_KEY`：模型和公众号凭证加密密钥，必须稳定备份，所有应用实例保持一致。

```bash
cp deploy/production/.env.production.example .env.production
chmod 600 .env.production
docker compose --env-file .env.production -f compose.production.yaml up -d --build
```

健康检查：

```bash
docker compose --env-file .env.production -f compose.production.yaml ps
curl -fsS http://127.0.0.1:18776/health
curl -I http://127.0.0.1:18775/
curl -I http://127.0.0.1:18777/
```

公网访问还需要在云服务器安全组中只向可信来源开放 TCP
`18775-18777`。直接使用 IP 和 HTTP 适合短期测试；正式使用仍应通过
Nginx、域名和 HTTPS，避免登录密码与业务内容以明文传输。

## 从 Git 发布

首次把发布脚本安装到服务器：

```bash
install -m 750 deploy/production/deploy-from-git.sh \
  /opt/wechat-publisher/shared/deploy-from-git.sh
```

以后发布指定分支：

```bash
DEPLOY_BRANCH=main \
  /opt/wechat-publisher/shared/deploy-from-git.sh
```

脚本会维护服务器端裸仓库，拉取远端提交、创建不可变版本目录、构建
Docker 镜像、执行健康检查并切换 `current` 软链接。生产密钥仍只来自
`/opt/wechat-publisher/shared/.env.production`，不会从 Git 读取。

### Cockpit 便携桥接器下载

仅在 EXE 已完成生产代码签名与校验后，将便携桥接器放到：

```text
/opt/wechat-publisher/shared/downloads/BlueBloodLab-Cockpit-Bridge-1.4.1.exe
```

生产 Nginx 通过精确 `/downloads/BlueBloodLab-Cockpit-Bridge-1.4.1.exe`
路由提供下载，不开启目录浏览。上传后执行 `nginx -t` 并重载 Nginx，再校验下载文件
SHA-256 与构建产物一致。

服务器本身使用固定出口 IP 时保持 `WECHAT_RELAY_ENABLED=false`，并把服务器
公网出口 IP 加入每个公众号的微信开发者 IP 白名单。

## 自动清理部署产物

安装磁盘阈值清理脚本和 systemd 定时器：

```bash
install -m 750 deploy/production/cleanup-deploy-artifacts.sh \
  /opt/wechat-publisher/shared/cleanup-deploy-artifacts.sh
install -m 644 deploy/production/wechat-publisher-cleanup.service \
  /etc/systemd/system/wechat-publisher-cleanup.service
install -m 644 deploy/production/wechat-publisher-cleanup.timer \
  /etc/systemd/system/wechat-publisher-cleanup.timer
systemctl daemon-reload
systemctl enable --now wechat-publisher-cleanup.timer
```

定时器每小时检查一次系统盘。占用低于 80% 时不做修改；达到阈值后：

- 保留当前版本和最近 5 个 Release；
- 保留当前容器使用的镜像和最近 5 个应用镜像；
- 删除更旧且未被容器使用的应用镜像；
- 清理 72 小时以前的悬空镜像与无用构建缓存；
- 不清理容器、数据库卷或其他持久化数据。

达到阈值后仍会先执行 fail-closed 安全检查：`current` 必须指向合法的
`releases/git-*` 目录，PostgreSQL、API、Web 和管理端容器必须都在运行，
三个 HTTP 健康检查也必须通过。任一条件不满足时整次清理直接跳过。清理任务
使用低 CPU 和空闲 IO 调度优先级，完成后会再次执行同一组生产健康检查。

清理任务与部署脚本共用 `deploy.lock`，部署期间会自动跳过。可用以下命令查看状态：

```bash
systemctl status wechat-publisher-cleanup.timer
journalctl -u wechat-publisher-cleanup.service --since today
```
