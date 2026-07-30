# 微信公众号固定 IP 中转

该中转只代理 `api.weixin.qq.com` 官方接口，让所有客户公众号从云服务器的固定公网 IP 访问微信。文章抓取、AI 模型和飞书请求不会经过这里。

## 产品默认连接信息

- 网关：`https://bluebloodlab.cn/wechat-relay`
- 固定出口 IP：`47.99.126.8`

桌面端只公开以上两项。每位客户的 Basic Auth 凭证封装在单字段接入码中，
不会写入向导状态或日志。

## 部署

1. 将云服务器公网 IP 加入每个公众号的“开发者 IP 白名单”。
2. 安装 Basic Auth 和接入码签发所需工具：

   ```bash
   sudo apt update
   sudo apt install -y apache2-utils openssl
   ```

3. 将 `nginx-location.conf.example` 中的两个 `location` 放入域名的 HTTPS `server` 块。
4. 检查并重载：

   ```bash
   sudo nginx -t
   sudo systemctl reload nginx
   ```

5. 为首位客户签发独立接入码：

   ```bash
   chmod +x ./manage-access-code.sh
   sudo ./manage-access-code.sh issue wechat-client-001
   ```

   脚本安全生成 256 位随机密码，以 bcrypt 更新 Nginx `htpasswd`，
   并只向标准输出写一行接入码：

   ```text
   wr1.<username-base64url>.<password-base64url>.<checksum>
   ```

   接入码包含可还原的中转凭证，安全级别等同于密码。请通过密码管理器等
   安全渠道交付，不要贴入工单、聊天群、命令参数或日志。

6. 客户在桌面应用的首次配置向导中粘贴接入码。客户端校验版本和 checksum
   后，使用系统固定网关，并通过现有 Windows 用户级加密存储保存密码；
   接入码本身不持久化、不回显。

7. 应用使用真实公众号执行只读素材与草稿检查，通过后才启用中转。

## 签发、轮换与撤销

每位客户必须使用不同用户名。再次对同一用户名执行 `issue` 会生成新密码并
立即使旧接入码失效：

```bash
sudo ./manage-access-code.sh issue wechat-client-001
```

客户离场或接入码泄露时立即撤销：

```bash
sudo ./manage-access-code.sh revoke wechat-client-001
```

`htpasswd` 文件由 Nginx 每次请求读取，撤销后无需重载 Nginx。可通过自定义
文件路径或环境变量管理非默认部署：

```bash
sudo ./manage-access-code.sh issue wechat-client-002 /path/to/relay.htpasswd
sudo WECHAT_RELAY_HTPASSWD_FILE=/path/to/relay.htpasswd \
  ./manage-access-code.sh revoke wechat-client-002
```

`checksum` 仅用于发现复制错误和截断，不是数字签名，不能替代接入码的安全
传输与保管。

## 安全要求

- 只开放 HTTPS 443，不要公开上游 Node/Python 端口。
- 每个客户使用独立 Basic Auth 账号，离场后立即撤销。
- 不记录带查询参数的访问日志；微信 `access_token` 位于查询参数中。
- 不在服务器保存客户 AppID、AppSecret 或 access_token。
- 不把接入码或明文 Basic Auth 密码写入日志、配置仓库或客户支持记录。
- 定期轮换 Basic Auth 密码和 TLS 证书。
- `draft/add` 不允许普通网络重试；客户端使用持久写入账本和草稿对账避免重复草稿。
