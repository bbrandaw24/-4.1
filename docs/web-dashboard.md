# Web 可视化看板

`web/` 是一个不依赖构建工具的静态页面，用于现场演示智慧农业端云链路。

## 页面能力

- 读取 API 的设备列表和最新土壤/气候遥测
- 每 5 秒自动刷新，并显示 API 连接状态
- Canvas 绘制土壤湿度趋势
- 通过 API 发布水泵启动/停止指令
- 上传 JPEG/PNG 现场图片并显示服务端生成的缩略图
- AI 区域明确标注为待接入，不虚构模型识别结果

## 运行

```bash
python3 -m http.server 8080 --bind 0.0.0.0 --directory web
```

浏览器访问 `http://<虚拟机 IP>:8080/`。如果 API 不在默认地址，可使用查询参数覆盖：

```text
http://<页面地址>/?api=http://<API 地址>:8000
```

页面默认 API 地址为 `http://192.168.128.129:8000`。API 通过响应头允许本地静态页面跨域调用；生产部署时应将 `CORS_ORIGIN` 限制为实际页面域名。

## 本次验收

- 页面显示 `sim-greenhouse-001` 和实时土壤/气候数据
- 页面显示 `API 已连接`
- 水泵 `start`/`stop` 均返回 HTTP 202，模拟器日志确认状态切换
- 截图留档：`C:\Users\DELL\Desktop\测试实习\smartagri-dashboard.png`
