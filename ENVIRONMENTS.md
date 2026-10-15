# Local / Prod 运行与发布

当前部署：同一 OpenClaw Gateway，同一模型和 Ego 浏览器依赖；两个 Agent、workspace、
WebsiteChecker 项目目录、Python 环境、API、Worker 池和数据库。Gateway/浏览器/模型额度/主机资源共享。
本文件不包含密钥。Local 与 Prod 均已启用数据库多 Token，分别管理各自的调用凭证。
多 Token 的管理员命令和教程见 README 的“内部多 Token 管理与使用”。
发布此版本后、启动 API 前，必须执行目标环境的 tokens migrate 和 import-legacy（或 create）。
当前功能记录最后认证时间与任务 Token 来源，不包含逐次请求日志。

| 项目 | Local | Prod |
|---|---|---|
| 项目目录 | `/Users/newpages/Documents/websiteChecker-local` | `/Users/newpages/Documents/websiteChecker` |
| API 端口 | 18001 | 18000 |
| 数据库 | website_checker_local | website_checker |
| 数据库用户 | wc_local | wc_prod |
| OpenClaw Agent | website-checker-agent-local | website-checker-agent |
| workspace | `/Users/newpages/.openclaw/workspace-website-checker-agent-local` | `/Users/newpages/.openclaw/workspace-website-checker-agent` |
| Gateway | `http://127.0.0.1:18789` | `http://127.0.0.1:18789` |

各目录有自己的 `.env` 和 `.environment.json`。Settings 从代码目录读取 `.env`，不随当前 shell 目录改变。
启动时检查环境、数据库连接身份、Agent 和端口是否匹配绑定文件；错误的环境变量覆盖会拒绝启动。
API 响应 Header `X-WebsiteChecker-Environment` 标示环境。Gateway Token 共用，API Token 独立。
Token 存储在各环境数据库中；`.env` 的 API_TOKEN 只用于旧凭证导入，现有正式 Token 已导入并保持可用。不要复制 `.env` 或绑定文件进行发布。
数据库运行账号只有本环境的 SELECT/INSERT/UPDATE/DELETE 权限；迁移使用管理员账号单独执行。

## 日常运行

在任一项目目录执行 `./manage`，第一个参数必须指定 local 或 prod。使用后台进程，退出终端仍运行；
这不等于开机自动启动。API 日志分别在各项目 `.runtime/api.log`，Worker 日志在 `.runtime/workers/logs/`。

```sh
./manage local status
./manage prod status
./manage local api start
./manage local api restart
./manage local api stop
./manage prod api restart
```

Worker 数量由使用者决定；版本更新应记录并恢复更新前的目标数量，以 status 的实时结果为准。
`start --count N` 是目标总数，不是额外新增数量。API 启动不会自动启动 Worker。

```sh
# 以下 2 和 3 仅为示例，请自行选择数量
./manage local workers start --count 2
./manage local workers scale --count 3
./manage local workers status
./manage local workers scale --count 0
./manage local workers stop
./manage prod workers status
```

stop 等当前任务结束；需要通过 status 确认管理器和 Worker 已全部退出。
不要同时运行未受管理的 `python -m app.worker`，否则实际并发数会超过设置。
在停止全部 API/Worker 后重启机器，需要分别恢复 API 并自行指定 Worker 数量。

Swagger：
- Local: http://127.0.0.1:18001/websiteChecker/api/v1/docs
- Prod: http://127.0.0.1:18000/websiteChecker/api/v1/docs

## 开发与更新

日常编辑 Local 项目及 Local workspace 的 `skills/web-checker/`，不要直接编辑 Prod。
发布工具同步白名单：app、tests、contracts、prompts、sql、scripts、README、ENVIRONMENTS、manage、
依赖清单、示例配置，以及 Agent 的 `skills/web-checker/`。
Agent 身份、AGENTS/SOUL/USER 等启动文件不自动覆盖；需要修改这些文件时先审阅环境差异，单独更新。
现有 web-checker 对共享 ego-browser skill 的引用保持不变。

1. Local 执行单元测试和一次真实审核，确认规则/输出格式与 WebsiteChecker 兼容。
2. 预览更新：

```sh
./manage prod update --version v1.1.0
```

3. 更新窗口内停止 Prod API 接收新请求，停止 Worker 并等待当前任务完成：

```sh
./manage prod api stop
./manage prod workers stop
./manage prod status
```

4. 确认 manager_running=false，执行：

```sh
./manage prod update --version v1.1.0 --apply
```

工具拒绝覆盖已有同名版本；检查 API、Worker、数据库中没有 running 任务；
保存候选快照及旧版备份和 SHA256 清单；执行候选测试和 Skill 测试后更新白名单文件。
运行中的 Local 仍然保持原状。不要在发布期间绕过 manage 手工启动 Prod 进程，或直接调用 Prod Agent。
不复制 `.env`、`.environment.json`、`.venv`、`.runtime`、数据库、证据、会话或凭证。
不会重启 Gateway，不自动启动任何 Worker。

5. 如包含数据库结构修改，使用管理员账号评审和应用迁移；本工具不自动执行 SQL。
涉及 requirements/锁文件变化时工具会拒绝自动更新，需另行安排依赖安装和发布。
6. 恢复 API，检查 Health 和列表后，自行选择正式 Worker 数量：

```sh
./manage prod api start
./manage prod workers start --count 3
```

## 文件回退

停止 Prod API/Worker 并确认任务已完成后，预览和执行：

```sh
./manage prod rollback --version v1.1.0
./manage prod rollback --version v1.1.0 --apply
```

这会恢复应用该版本之前的代码和 Skill；若 Prod 文件已有后续变化则拒绝覆盖，需先检查。
回退不回滚数据库或依赖，也不删除历史任务/报告。恢复服务仍需使用者指定 Worker 数量。
备份位于 Prod `.runtime/releases/<version>/`，不要清理仍需要回退的版本。

初始化拆分备份位于 Prod `.runtime/environment-split-*/`；其中包含原配置，应作为敏感备份保护。

## 2026-10-15 图片请求体保护

- 每张供模型查看的图片必须经过 Skill `prepare-visual.py`；原始证据不改写。
- Pillow 已安装于主机 `python3`（不是应用 .venv）；Worker preflight 检查该依赖。
- 每段会话图片的累计 base64 预算为 6 MiB。Worker 接受内部 `checkpoint` 信封，
  使用 `openclaw sessions compact` 对同一原生会话进行整理，只在原生后端明确
  `completed:true, pending:false` 后归档旧预算、开启下一代预算。报告结构不变。
- 超限补丁只把原生转发器的请求过大错误改为明确的 413；不提高 32 MiB 上限。
- `scripts/relay_payload_guard.py --stage <全新目录>` 创建隔离候选与原始备份；
  `node scripts/test-relay-payload.mjs <候选目录>` 测试真实转发器边界；
  仅在 Gateway 维护窗口 `--apply <候选目录>`，随后受控重启 Gateway。
- `--restore <候选目录>` 按 SHA256 拒绝覆盖后续变动；恢复后同样需要重启。
- **OpenClaw/插件升级会替换补丁**。升级后执行 `--check` 并复测，不要盲目套旧补丁。
  Worker 默认 `payload_guard_required=true`，每次发起模型任务前检查当前插件，
  补丁不在则以 `payload_guard_unverified_after_runtime_change` 明确失败，不执行模型请求。
  不要在生产关闭该检查。此检查证明磁盘代码，不替代 Gateway 重启及加载验证。
- 已超限旧会话不能原样重试。先保留该任务证据并评估是否能受支持地整理；
  不删除原任务、替换 TaskSpace 或伪造已检查状态。
