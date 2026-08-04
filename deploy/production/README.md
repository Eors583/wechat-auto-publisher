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

服务器本身使用固定出口 IP 时保持 `WECHAT_RELAY_ENABLED=false`，并把服务器
公网出口 IP 加入每个公众号的微信开发者 IP 白名单。
