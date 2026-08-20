# Network Stability Monitor

一款面向 Windows 11 的网络稳定性测试工具，用图形界面实时展示下载、上传、延迟和网页访问体验，并区分国内直连与 Clash 代理线路。

## 主要功能

- 国内直连和 Clash 代理两种测试配置
- 下载、上传、双向并发、网页体验四种测试模式
- 实时显示 Mbps、MB/s、延迟、抖动、错误数和流量
- 显示出口 IP、运营商与地区
- ChatGPT、Google、GitHub、Cloudflare 等网页体验测试
- 测试结论与逐站点诊断
- 测速历史、CSV 日志与明暗主题
- Windows 11 风格的无边框、圆角、高 DPI 界面

## 环境要求

- Windows 10/11
- Python 3.10 或更高版本
- Python 安装中包含 Tkinter

安装依赖：

```powershell
python -m pip install -r requirements.txt
```

启动图形界面：

```powershell
python .\net_stability_gui.py
```

也可以单独使用命令行下载稳定性测试：

```powershell
python .\net_stability_test.py --duration 60 --connections 4
```

## 测试数据

程序会把界面设置和 CSV 测试记录保存在：

```text
D:\ProgramData\NetworkStabilityTest
```

测速记录可能包含公网 IP 和地区信息，已通过 `.gitignore` 排除，不应提交到仓库。

## Clash 测试说明

代理模式使用 Windows 系统代理。请确认所选测速域名在 Clash 中命中目标代理节点。如果启用了 Clash TUN 或全局路由接管，程序中的“直连”只能绕过 Python/Windows HTTP 代理，不能绕过操作系统层面的流量接管。

## 注意事项

测速会产生较大的网络流量，并可能持续占用上下行带宽。移动网络、按流量计费连接或生产网络中请谨慎设置测试时长和并发数。测试结果也会受到测速源负载、路由、代理节点和运营商策略影响。
