# Windows 节点源码与发版来源

本文件说明项目（含 Windows 舰队节点，本机 `DESKTOP-VST7EIE`）的权威版本化源码与发版方式。

## 现状：单一权威仓库

2026-10-20 完成谱系合并。此前有两条**无共同祖先**的谱系：

- `dengyie/agent-fleet`：34 条提交的公开历史，缺 Windows 探针枚举等功能。
- Windows 节点树：live 目录（`C:\Users\mango\agent-fleet`）的快照，功能是公开谱系的路径超集。

节点树是路径超集：公开侧 `hub/`、`tools/`、`connectors/`、`tests/` 的文件它全都有，另有 426 个文件只存在于节点侧；只有 18 个公开侧旧前端文件（`frontend/api|views|styles`、`hub/static/*.js`、`hub/templates/*.html`）被新前端取代。节点树还多出约一个月的新工作：分层 `hub/{domain,application,infrastructure,http}`、`frontend/assets` 新前端、`tools/platform`、`tools/browser_bridge`、`supervisor`，以及 `tools/probe/discovery.py` 的 Windows 枚举。

合并方式：**把节点树作为项目现状，叠在公开历史之上**（合并提交 `6c87d0d`），历史全部保留。此后 `E:\code\agent-fleet` 是唯一权威源码。

> 历史备份：本地分支 `backup-pre-merge`（合并前 HEAD）与私有仓库 `dengyie/agent-fleet-windows`（节点谱系快照）。合并落地后二者仅供追溯，不再是发版来源。

## 发版方式

```bash
# 在权威仓库执行；package-release.sh 使用 git archive HEAD，
# 因此只有已提交的内容会进入发布包。
./deploy/package-release.sh
```

live 目录（`C:\Users\mango\agent-fleet`）无 `.git`，是部署目标；`agent-fleet-stage-*` 是历史快照。**先提交，再发版**——只在 live 目录手改、未提交的修复会在下次 `git archive HEAD` 时丢失。

注意：磁盘上的可执行位不随文件内容走。合并时曾从公开侧恢复 13 个脚本的 `100755`（`deploy/*.sh`、`.githooks/pre-push`、`tools/supervisor/control_client_cli.py`）；节点快照自身丢失过这些位，部署到 Linux 节点前请确认脚本仍可执行。

## 启动策略（勿绕过）

所有本机 console 子进程必须经过 `tools/local_process.py`：

- `creationflags |= CREATE_NO_WINDOW`：否则 pythonw 无主控台，每个 console 子进程都会弹出一个可见窗口。
- 文本模式下强制 `errors="replace"`：否则子进程输出 GBK/UTF-16 时读取线程崩溃，`stdout` 变成 `None`。

`tests/test_local_process.py` 用 AST 扫描 `connectors/`、`hub/`、`tools/` 下所有 `subprocess.run/Popen/call/check_call/check_output` 调用，缺 `creationflags` 即失败。新增 spawn 站点请改走 `local_process.run` / `local_process.popen`，或显式声明 `creationflags`。
