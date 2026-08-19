BlueBloodLab Cockpit 本地 API 桥接器（免安装便携版）

用途：
- 只为 https://api.bluebloodlab.cn 提供浏览器到本机 Cockpit Tools 的安全桥接。
- 在“文章链接/多篇参考”模式下，可由这台电脑获取公众号正文，避免生产服务器被微信拦截。
- 不包含公众号主程序、数据库、飞书、微信发布或后台 Companion。
- 使用本包时，生产网页必须保持打开；需要关闭网页后继续任务时请安装完整 Companion。

使用：
1. 直接双击下载的 BlueBloodLab-Cockpit-Bridge-*.exe（ZIP 包用户需先解压）。
2. 浏览器会自动打开 http://127.0.0.1:11798/setup。
3. 填写 Cockpit Tools 实际的 API Base URL，例如 http://127.0.0.1:21888。
4. 填写 Cockpit API Key，点击“验证并保存本机连接”。
5. 回到生产网站，把本地模型 API Base URL 保持为 http://127.0.0.1:11798/v1。
6. Chrome/Edge 首次访问时，允许“设备上的应用/本地网络访问”。

运行与退出：
- 请保持桥接器控制台窗口打开；关闭窗口即停止桥接。
- 只监听 127.0.0.1:11798，不开放局域网访问。
- 如果提示 11798 被占用，请先退出完整本机助手或其他桥接器，不要同时运行两套。

安全：
- Cockpit URL 只允许 localhost、127.0.0.1 或 ::1，并且必须填写端口。
- API Key 使用 Windows CurrentUser DPAPI 加密，只保存在当前 Windows 用户目录。
- API Key 不会上传生产服务器，也不会写入命令行、URL 或日志。
