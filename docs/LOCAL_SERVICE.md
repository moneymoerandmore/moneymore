# MoneyMore 本地常驻服务

MoneyMore 可以作为 Windows 登录自启任务运行，不需要打开 Codex。API 进程只启动一个 worker，内置每日 18:30 调度器；Dashboard 使用本地服务模式运行。监督进程每 10 秒检查一次 API 和 Dashboard，异常退出后自动拉起。

首次安装：

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\install_local_service.ps1
```

日常可直接双击项目根目录的：

- `启动MoneyMore.cmd`
- `停止MoneyMore.cmd`
- `查看MoneyMore状态.cmd`

也可以使用 PowerShell：

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\local_service.ps1 -Action Status
```

Dashboard 地址：[http://127.0.0.1:3000](http://127.0.0.1:3000)。日志保存在 `logs/api.*.log` 和 `logs/web.*.log`。运行 PID 与停止标记保存在 `state/runtime`。

移除自启任务但保留数据与程序：

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\uninstall_local_service.ps1
```

注意：Windows 登录后服务才会自动启动；电脑关机或休眠时无法执行任务。再次登录并恢复服务后，现有恢复流水线会检查并补齐缺失交易日。更新服务配置后，重新运行安装脚本即可覆盖自启配置。
