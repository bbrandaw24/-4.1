# 腾讯云公网部署

当前部署目标为腾讯云 Ubuntu 24.04 实例 `43.156.230.129`。公开入口只有网页网关：

```text
http://43.156.230.129:8080/
```

Compose 服务使用独立项目名 `smartagri-cloud`。网页网关在容器内部通过 Docker 服务名访问 API 和 AI，因此浏览器不需要直接访问内部服务地址。

## 端口策略

- `8080/tcp`：公网网页入口，需要在腾讯云安全组放行。
- MySQL、MQTT、API、AI：只绑定 `127.0.0.1`，不对公网提供端口。
- 现有服务器上的 VpnHood/Outline 服务未停止、未覆盖。

## 更新命令

```bash
cd /home/ubuntu/smartagri-repo
git pull --ff-only origin day02-mqtt
sudo docker compose -p smartagri-cloud up -d --build
```

网页网关会代理 `/api/*` 和 `/ai/*`，因此页面和后端使用同源地址，适合手机、同事电脑等外部设备访问。
