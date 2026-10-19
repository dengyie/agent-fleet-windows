# Windows 节点源码与发版来源

本文件说明 Windows 舰队节点（本机 `DESKTOP-VST7EIE`，live 目录 `C:\Users\mango\agent-fleet`）的权威版本化源码，以及为什么发版必须从这里构建。

## 权威仓库

- **`dengyie/agent-fleet-windows`（私有仓库）** 是本节点的权威版本化源码。live 目录无 `.git`，`agent-fleet-stage-*` 快照即本仓库的工作副本，发版产物由它构建。
- 历史：`e3711cc`（Windows 节点部署快照）→ `17a1d4c`（根因修复：集中化 console 窗口 + 解码策略）→ `f0bc095`（记忆化 bash 解析）。

## 与 `dengyie/agent-fleet` 的关系

两者**没有共同祖先**（`git merge-base` 为空）。`dengyie/agent-fleet`（public）是另一条谱系，**不含** Windows 探针枚举功能：

- `tools/probe/discovery.py` 的 PowerShell / `EncodedCommand` 枚举路径只存在于本仓库。
- 从 `dengyie/agent-fleet` 对本节点发版会移除 Windows 采集。**除非先完成谱系合并，否则禁止从该仓库对本节点发版。**

## 发版方式

```bash
# 在本仓库（或其工作副本）执行；package-release.sh 使用 git archive HEAD，
# 因此只有已提交的内容会进入发布包。
./deploy/package-release.sh
```

任何只在 live 目录手改、未提交的修复，都会在下次 `git archive HEAD` 发版时丢失——**先提交，再发版**。

## 启动策略（勿绕过）

所有本机 console 子进程必须经过 `tools/local_process.py`：

- `creationflags |= CREATE_NO_WINDOW`：否则 pythonw 无主控台，每个 console 子进程都会弹出一个可见窗口。
- 文本模式下强制 `errors="replace"`：否则子进程输出 GBK/UTF-16 时读取线程崩溃，`stdout` 变成 `None`。

`tests/test_local_process.py` 用 AST 扫描 `connectors/`、`hub/`、`tools/` 下所有 `subprocess.run/Popen/call/check_call/check_output` 调用，缺 `creationflags` 即失败。新增 spawn 站点请改走 `local_process.run` / `local_process.popen`，或显式声明 `creationflags`。
