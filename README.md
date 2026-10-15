# Website Checker API

Local / Prod 已分开部署；运行、Worker 数量设置和版本发布见 [ENVIRONMENTS.md](ENVIRONMENTS.md)。

推荐使用 `./manage local|prod api start`；API 启动不自动启动 Worker。

Python + FastAPI 接收任务和提供查询；独立 Worker 调用 OpenClaw 的 `website-checker-agent`。

## 安装与启动

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
./manage prod api start
# 启动 Local 使用 ./manage local api start
```

每份部署独立保存 `.env` 和 `.environment.json`；修改环境绑定需有意执行，不能互相复制。
保留现有 `.env`，按 `.env.example` 设置：

- `DB_HOST`、`DB_PORT`（3306）、`DB_NAME`、`DB_USER`、`DB_PASS`：数据库连接。
- `API_TOKEN`：仅用于一次性导入旧 Token；运行时使用 api_tokens 数据库表认证。
- `OPENCLAW_BASE_URL`（http://127.0.0.1:18789）、`OPENCLAW_TOKEN`：Worker 连接 OpenClaw。
- `OPENCLAW_AGENT_ID=website-checker-agent`、`OPENCLAW_MODEL=openai/gpt-6-astra`、`OPENCLAW_THINKING=low`。

有效 API Token 均为内部共享访问凭证，持有者可查询所有任务；project_id/company_id 用于关联，
不是租户权限校验。Token 不应发到浏览器前端或提交到 Git。

数据库迁移：新数据库按顺序执行 `sql/001_create_audits.sql`、`sql/004_audit_reports.sql`。
旧数据库只执行尚未应用的迁移：002（自增 ID 和时间）、003（Worker 字段）、004（报告表）、005（删除旧版本的三个预留索引）。
005 仅适用于仍有这三个旧索引的已有数据库；使用当前 001 + 004 新建的数据库跳过 005。
002/003 只执行一次；不要在已包含相应字段的新建表上重复执行。

Base URL：`http://10.10.10.102:18000/websiteChecker/api/v1`
Swagger：`http://10.10.10.102:18000/websiteChecker/api/v1/docs`

## Health

`GET /health`，无需 Token。实际请求 OpenClaw `/healthz`，默认超时 5 秒。

成功 HTTP 200：`{"status":1,"checked_at":1789804800}`。
异常 HTTP 503：`{"status":0,"error_code":"OPENCLAW_UNAVAILABLE"}`。
checked_at 为 Unix 秒；只代表 Gateway HTTP 存活，不保证模型或浏览器可用。

## 创建任务

```http
POST /audits
Authorization: Bearer <API_TOKEN>
Content-Type: application/json
```

```json
{"project_id":"001","company_id":"002","website_url":"https://example.com"}
```

两个 ID 为非空字符串，最长 255 字符；URL 必须是 HTTP/HTTPS，最长 2048 字符。
成功提交事务后返回 HTTP 202：

```json
{"status":1,"job_id":"4b2b1c2e-9511-431f-910d-28b5541f3a39","job_status":"queued"}
```

每次提交创建独立 UUID v4。job_id 唯一索引防重复，碰撞重试；重复内容也会创建新任务。

## 分页获取任务列表

`GET /audits`，需要 `Authorization: Bearer <API_TOKEN>`。返回未软删除任务，按自增 ID 从新到旧排序。

使用 `GET /audits?page=1&page_size=20`：page 默认 1，范围 1–2147483647；
page_size 默认 20，可设为 1–20，每次最多返回 20 个任务。省略参数也会分页。
响应保留 status 和 lists，新增 page、page_size、total、total_pages。
翻页期间如有新增或删除任务，页内位置可能变化。

```json
{"status":1,"lists":[{"job_id":"4b2b1c2e-9511-431f-910d-28b5541f3a39","status":"queued"}],"page":1,"page_size":20,"total":1,"total_pages":1}
```

无任务或页码超过末页时 lists 为空；无任务时 total 和 total_pages 都为 0。外层 status 表示请求成功；列表里的 status 是
queued/running/completed/failed。失败统一只返回 status 和 error_code：

```json
{"status":0,"error_code":"UNAUTHORIZED"}
```

- HTTP 401：UNAUTHORIZED（Token 缺失或错误）。
- HTTP 503：DATABASE_UNAVAILABLE。
- HTTP 500：INTERNAL_ERROR。
- HTTP 422：VALIDATION_ERROR（分页参数无效）。

## 查询任务状态

`GET /audits/{job_id}`，必须携带 `Authorization: Bearer <API_TOKEN>`。

HTTP 200 示例：

```json
{
  "status": 1,
  "job_id": "4b2b1c2e-9511-431f-910d-28b5541f3a39",
  "project_id": "001",
  "company_id": "002",
  "website_url": "https://example.com/",
  "job_status": "completed",
  "created_at": 1789804800,
  "updated_at": 1789804900,
  "started_at": 1789804805,
  "finished_at": 1789804900,
  "coverage_status": "partial",
  "limitations": ["A required page could not be inspected."],
  "report_available": true,
  "error_code": null
}
```

status=1 代表查询成功；job_status 是 queued/running/completed/failed。
coverage_status 是审核覆盖情况 completed/partial/blocked/out_of_scope，尚无结果时为 null。
所有数据库时间为 Unix 秒；未开始/未结束时间为 null。
completed 代表任务已产生有效报告，不表示所有检查通过；coverage_status=partial 的有效报告同样直接保存、生成 HTML 并发送，不再请求自动续查。未完成范围保留在内部 limitations 中；blocked/out_of_scope 不按此规则完成。

## 查询报告

`GET /audits/{job_id}/report`，同样需要 Bearer Token。

HTTP 200 返回 `{"status":1,"job_id":"...","report":{...}}`。
report **只有** generated_at、basic_info、checklist、issues_found 四个字段，
字段契约见 `contracts/README.md` 和 `contracts/web-checker-report.schema.json`。
报告 generated_at 保留 skill 要求的带时区 RFC3339 字符串，与数据库的 Unix 时间区别开。
Checklist 固定八项：status 只允许整数 1/0，severity 只允许 low/medium/high。
只按已确认问题判断：该项有对应问题则为 0/FAIL，严重性取对应问题的最高值；没有对应问题则为 1/PASS、low。
验证受阻或证据不明确不会产生问题或 FAIL；真实覆盖状态和限制只保留在内部，不写入客户报告。
*.newpages.xyz 演示网站的 News/Latest News 404 和 Visitor: 空计数值不计为问题。
旧版本历史报告保留原始格式；二进制规则适用于新生成报告。
不返回 agent 内部文件路径、Token、原始模型回复或 token usage。

错误响应：

| HTTP | 情况 |
|---|---|
| 401 | Token 缺失或错误 |
| 404 | 任务不存在／已软删除；已完成任务的报告不存在／已软删除 |
| 409 | 查询报告时任务尚在排队、执行中或已失败 |
| 422 | 请求参数或 UUID 无效 |
| 503 | 数据库不可用、Token 未配置或存储数据无效 |

列表接口使用上述 error_code 格式；其他接口所有 API 失败响应（含 Health、参数校验、未知路径及未捕获异常）只包含两个字段：

```json
{"status":0,"error_code":"VALIDATION_ERROR"}
```

HTTP 状态码保留，不返回 message、detail、checked_at 或异常内部信息。

| HTTP | error_code | 含义 |
|---|---|---|
| 401 | UNAUTHORIZED | Token 缺失或错误 |
| 404 | AUDIT_NOT_FOUND / REPORT_NOT_FOUND | 任务或报告不存在 |
| 404 | NOT_FOUND | API 路径不存在 |
| 405 | METHOD_NOT_ALLOWED | 请求方法不支持 |
| 409 | REPORT_NOT_AVAILABLE / HTML_REPORT_NOT_AVAILABLE | 报告尚不可用 |
| 422 | VALIDATION_ERROR | 参数、UUID 或 JSON 无效 |
| 500 | INTERNAL_ERROR | 未预期的内部错误 |
| 503 | DATABASE_UNAVAILABLE | 数据库不可用 |
| 503 | STORED_AUDIT_INVALID / STORED_REPORT_INVALID | 存储数据无效 |
| 503 | OPENCLAW_UNAVAILABLE | Health 无法确认 Gateway 正常 |

查询成功但任务 job_status 为 failed 时，仍为 HTTP 200 / status=1；
该响应的 error_code 是任务执行失败原因，不是本次 API 请求失败。

## 多 Worker 管理

项目使用独立 Supervisor 管理进程，只通过本地 Unix socket 控制，不新增 TCP 端口。
支持当前 macOS 和 Linux。先安装 requirements.txt 中的依赖。

```sh
cd /Users/newpages/Documents/websiteChecker

# 后台启动 3 个 Worker，关闭当前终端后仍继续运行
.venv/bin/python -m app.workers start --count 3

# 将总数调整为 5（增加 2 个，不重启原有 3 个）
.venv/bin/python -m app.workers scale --count 5

# 将总数降为 2（其余进程完成当前任务后退出）
.venv/bin/python -m app.workers scale --count 2

# 查看各进程 PID、状态、是否正在退出以及日志路径
.venv/bin/python -m app.workers status

# 暂停领取：缩到 0，现有任务完成后退出；管理进程继续运行
.venv/bin/python -m app.workers scale --count 0

# 停止管理进程和全部受管 Worker（等待当前任务完成）
.venv/bin/python -m app.workers stop
```

`--count` 是目标 Worker **总数**，不是增量；无固定应用级并发数量上限，但受主机资源和上游限制。
`scale` 需要管理进程已启动，`start` 在已运行时也可调整数量。扩缩容命令立即返回，
不等待长任务结束。缩容期间 status 中 `draining: true` 的进程仍可能在执行原任务，
所以进程总数暂时会高于目标；退出中的进程不会再接下一项。
缩容尚未完成时仍可扩容，新进程独立添加。不要把进程的 RUNNING 与任务的 running 混淆：
RUNNING 进程也可能正在等待队列。

每个 Worker 同时执行一项。任务领取由数据库行锁防重，每项任务使用独立 OpenClaw session、
`audits/<job_id>/` 输出目录，以及提示词明确要求的独立 Ego TaskSpace。
缩容和 stop 使用 SIGTERM，当前任务完成后退出；Supervisor 的强制退出等待上限是
创建该进程配置时的 WORKER_REQUEST_TIMEOUT_SECONDS + 60 秒。超过上限会被强制结束，
上游执行结果可能未知，后续心跳恢复逻辑会处理遗留 running 任务，不会自动重发。
更改请求超时等运行配置后，应停止整个池，等待退出，再执行 start；这会刷新运行配置。

进程成功启动后若异常退出，Supervisor 自动重启它。连续启动失败最多尝试 3 次后显示 FATAL，
需检查日志；不会无限高速重启。日志按文件大小轮转，目录为 `.runtime/workers/logs/`。
运行配置、socket、PID 和日志都在 `.runtime/workers/`，已加入 .gitignore。
这是后台进程管理，不是开机自动启动；电脑重启后重新执行 start。
standalone 的 `python -m app.worker` 不在这个池的管理范围内，避免混用导致实际数量超出预期。

OpenClaw 仍有共享的 `agents.defaults.maxConcurrent` 和模型额度限制。
增加 Worker 不会自动提高 Gateway 的全局上限，超出上游容量的请求可能在 OpenClaw 内排队。
当前未修改共享 Gateway 配置。浏览器 TaskSpace 和会话隔离不等于 CPU、登录态或额度完全隔离。

管理方式参考：[Supervisor 配置](https://docs.supervisord.org/configuration.html) 和
[运行管理](https://docs.supervisord.org/running.html)。

## Worker 与报告入库

```sh
# 仅连接测试，不领取任务
.venv/bin/python -m app.worker --probe

# 持续处理队列
.venv/bin/python -m app.worker

# 处理最多一个任务，或只处理指定任务
.venv/bin/python -m app.worker --once
.venv/bin/python -m app.worker --once --job-id UUID
```

每个 Worker 进程一次处理一项，每 3 秒轮询；领取用事务和 FOR UPDATE SKIP LOCKED，
要求 MySQL 8+ / MariaDB 10.6+。每 15 秒更新心跳，超过 120 秒未更新的任务标记 failed，
错误 worker_lost_outcome_unknown；不自动重发。请求超时默认 900 秒，可设置
WORKER_REQUEST_TIMEOUT_SECONDS；超时不代表 OpenClaw 已取消。SIGTERM/Ctrl+C 等当前任务结束后退出。

提示词 `prompts/web-checker-v3.txt` 强制调用 web-checker 及其 ego-browser 依赖。
Agent 在独立 audits/<job_id>/ 目录保留证据，用 skill 校验器导出 report.json，
并将文件内容放进 Worker 专用 JSON envelope 返回。Worker 不依赖共享文件目录，
也不读取模型返回的任意路径。模型通过 x-openclaw-model 指定，low 使用 /think:low，
因为当前 OpenClaw Responses 的 reasoning 字段会被忽略。每个 job_id 有独立 session。

Worker 二次校验固定 JSON 格式、Checklist 顺序、状态/严重性、问题编号与排序。
有效报告写入 audit_reports，同一事务更新 audits 为 completed。
无效 JSON、格式不符或基础设施错误仍为 failed；Checklist 含失败项不会导致任务失败。
blocked/out_of_scope 也导出带明确验证限制的报告，保存后任务为 completed，覆盖状态如实保留。
上游证据的真实性仍由 skill 的采集与证据校验负责，服务器格式校验不能证明审核观察正确。

数据关系：
- audits.id：从 1 开始自增主键，允许间隔；job_id 为对外 UUID 唯一键。
- audit_reports.id：自增主键；audit_id 唯一外键关联 audits.id，每个任务一份报告。
- audit_reports 保存 schema_version、report_json、created_at、updated_at、deleted_at。
- 新记录 updated_at 等于 created_at，deleted_at 为 null。查询隐藏软删除记录，尚无删除接口。
- audits.result_json 仅保存覆盖限制及上游 usage；公开报告的数据库来源为 audit_reports。
- audits 保留 id 主键、job_id 唯一索引、状态队列索引和心跳恢复索引；audit_reports 保留 id 主键、audit_id 唯一索引及外键。
- 每项任务的上游 Token 用量原样保存在 audits.result_json 的 usage 中，通常包含 input_tokens、output_tokens、total_tokens 及上游提供的细分字段。当前查询 API 不返回 usage。
- 这不是所有任务都完整的用量账本：上游缺少 usage 时为 null；超时、连接失败、上游未完成或无有效文本响应时可能无法取得用量，不能当作零消耗。


## HTML 报告（Python 模板）

现有数据库执行一次 `sql/006_report_html.sql`；新数据库依次执行 001、004、006。
迁移增加 nullable `report_html LONGTEXT` 和 `template_version`，保留原始 JSON。
Worker 先提交 JSON 和 completed 状态，再用 `app/templates/report.html` 渲染并保存 HTML。
渲染失败只记录日志，不丢失 JSON、不重跑 OpenClaw；completed/report_available 仍表示 JSON 可用。
部署时先执行迁移，再让 Worker 完成当前任务后重新启动以加载代码。

`GET /audits/{job_id}/report/html` 使用相同 Bearer Token，返回 `text/html; charset=utf-8`。
任务未完成或 HTML 尚未生成时返回 409；任务或报告不存在/软删除返回 404。
原有 `/report` JSON 接口保持原样。HTML 使用内嵌 CSS，无脚本和外部资源。
Requestor 不在当前数据契约内，不显示示例姓名。通过项严重程度按新契约显示 Low；
旧契约 null 状态显示 NOT VERIFIED。覆盖限制继续保存在 audits.result_json，HTML 不显示 Coverage 和 Verification Limitations。
Generated At 按 Asia/Kuala_Lumpur 时区显示，例如 `2026 Sep 19 05:02 PM`。

补生成所有缺少 HTML 的已完成报告（不调用 OpenClaw）：

```sh
.venv/bin/python -m app.render_reports
# 指定报告重新生成（例如模板更新后）
.venv/bin/python -m app.render_reports --job-id UUID
# 本地验证
.venv/bin/python -m unittest discover -s tests -v
```

模板版本保存在数据库；修改样式时同步更新 `TEMPLATE_VERSION` 及模板标记。

## 内部多 Token 管理与使用

Local 与 Prod 均使用数据库多 Token 管理。以下日常示例使用 Local；管理正式环境时将 local 换成 prod，API 端口改为 18000。两个环境的 Token 和数据各自独立。

### 权限与行为

- 管理员通过服务器终端运行 `./manage local tokens ...`。本版不提供网页管理后台，也不提供创建或撤销 Token 的 HTTP 接口。
- “管理员”指有权限登录服务器并运行项目管理命令的操作系统账号；`created_by` / `revoked_by` 记录该账号。
- 所有有效 Token 都能提交检查、查询所有任务、读取所有 JSON / HTML 报告；不按部门、company_id、project_id 或 Token 隔离数据。
- Token 没有有效日期，直到管理员撤销。撤销后不能恢复，请生成新的 Token。
- 数据库只存 SHA-256 哈希值；新 Token 由 32 字节随机数生成，完整值仅在创建输出中出现一次。
- 列表返回 fingerprint（哈希摘要的前 12 位），用于识别记录，不是可用 Token。
- 每次成功认证更新 `last_used_at`；这表示认证通过的时间，即使业务请求后来返回 404/422 等错误。
- 新任务记录 `api_token_id`，任务状态接口也返回此字段；旧任务为 null，不推测历史归属。
- 撤销仅阻止后续认证，不取消已接受的请求、排队任务或运行任务，不删除报告。
- API_TOKEN 现在只供 `import-legacy` 读取，HTTP 验证不再读取它；OPENCLAW_TOKEN 保持原用途。

### 1. 管理员进入 Local 项目

```sh
cd /Users/newpages/Documents/websiteChecker-local
./manage local tokens list
```

Local 与 Prod 已分别完成迁移并导入各自原有 Token，名称为“Local 原有内部调用”和“Prod 原有内部调用”。不需要重复迁移或导入。

### 2. 为内部系统生成 Token

```sh
./manage local tokens create --name "内部系统 A" --note "供内部网站调用"
```

示例输出（示例 Token 不能使用）：

```json
{
  "environment": "local",
  "id": 8,
  "name": "内部系统 A",
  "expires": "never",
  "token": "wc_local_<随机内容>",
  "notice": "Save now. This token cannot be retrieved again."
}
```

保存 `id` 和 `token`。把完整 Token 发给对应内部系统负责人，由对方保存在后端服务配置或密钥管理器中。
不要把它放在网页 JavaScript、URL 参数、Git 或公开截图中。创建命令不要用于共享日志，终端输出是唯一取回机会。
同名 Token 可以存在，操作时以 ID 为准；可以为不同系统重复运行创建命令。新增 Token 无需重启 API。

### 3. 调用 API

以下在这台 Mac 的 zsh 终端执行。提示输入 Token 时不会回显，也不会把密钥字面值写入输入命令历史：

```sh
read -rs 'WC_API_TOKEN?请输入 Token: '
echo
WC_API_BASE='http://127.0.0.1:18001/websiteChecker/api/v1'
```

这里的 127.0.0.1 仅适用于运行 API 的这台 Mac。其他电脑应使用其实际可访问的服务地址；端口 18001 是 Local，18000 是 Prod。
不要把 Local Token 发给 Prod 调用方；两个环境的数据库分别保存 Token，自动生成的 Token 不互通。

先查询任务列表，不会创建检查：

```sh
curl --fail-with-body -sS "$WC_API_BASE/audits?page=1&page_size=20" \
  -H "Authorization: Bearer $WC_API_TOKEN"
```

提交检查（此步骤会创建真实任务，并使用 OpenClaw）：

```sh
curl --fail-with-body -sS "$WC_API_BASE/audits" \
  -H "Authorization: Bearer $WC_API_TOKEN" \
  -H 'Content-Type: application/json' \
  --data '{"project_id":"001","company_id":"002","website_url":"https://example.com"}'
```

成功返回 HTTP 202 和 `job_id`。将返回的 job_id 填入下面变量，再查询状态：

```sh
WC_JOB_ID='替换为返回的job_id'
curl --fail-with-body -sS "$WC_API_BASE/audits/$WC_JOB_ID" \
  -H "Authorization: Bearer $WC_API_TOKEN"
```

状态响应包含 `api_token_id`，管理员可与 `tokens list` 的 `id` 对照。
等待 `job_status` 为 `completed` 后读取报告：

```sh
curl --fail-with-body -sS "$WC_API_BASE/audits/$WC_JOB_ID/report" \
  -H "Authorization: Bearer $WC_API_TOKEN"

curl --fail-with-body -sS "$WC_API_BASE/audits/$WC_JOB_ID/report/html" \
  -H "Authorization: Bearer $WC_API_TOKEN" \
  -o report.html

unset WC_API_TOKEN
```

JSON 已完成但 HTML 仍在生成时，HTML 接口可能返回 409；稍后重试 HTML 接口，不要重新提交检查。
浏览器直接打开报告 URL 不会自动添加 Bearer Header。可用 Swagger 的 Authorize 输入 Token（仅输入完整 Token 值），或通过上述 curl 下载。

### 4. 查看与撤销

```sh
./manage local tokens list
./manage local tokens revoke --id 8
```

将 8 替换为实际 ID。`revoked_at: null` 表示有效，非 null 表示已撤销；重复撤销同一记录返回 `changed: false`。
`created_at`、`last_used_at`、`revoked_at` 是 Unix 秒；`last_used_at: null` 表示尚未成功认证。
撤销立即对后续认证生效，无需重启。不存在的 ID 会报错，不会撤销其他记录。

### 5. 更换／遗失 Token

1. 生成一个新 Token。
2. 在调用方后端替换，并先用列表接口验证。
3. 用旧 Token 的 ID 执行 revoke。

旧任务与报告保留，新的有效 Token 仍能查看所有数据。泄露时可先撤销旧 Token，再发放新值。

### 6. 首次安装与旧 Token 迁移（已启用的 Local 不必再做）

先确保 audits / audit_reports 已按现有 001、004、006 等迁移创建，再执行：

```sh
./manage local tokens migrate --admin-socket /tmp/mysql.sock
./manage local tokens import-legacy --name "原有内部调用"
./manage local api restart
```

`--admin-socket` 使用当前操作系统账号通过本机 MySQL socket 完成建表／加字段；本机 newpages 账号具备权限。
日常 wc_local / wc_prod 数据库账号没有建表权限，不能直接用于迁移。迁移命令不提高日常账号权限。
若另一个部署的数据库账号本身有 DDL 权限，可以省略 `--admin-socket`；该选项不适用于远程 MySQL。
不要直接只运行 007 SQL 文件：管理命令还负责幂等添加 `audits.api_token_id` 和索引。
重复 migrate 安全；重复 import-legacy 会拒绝，不会生成重复记录或重新激活已撤销 Token。
没有旧 API_TOKEN 时跳过导入，改用 create 生成首个 Token；完成初始化前有效凭证无法认证。
导入成功后可在后续配置维护时清除旧 API_TOKEN，其值不再参与运行时验证。

### 7. Prod 发布说明

本版本已同步到 Prod。以下为后续重新部署或首次安装的步骤，当前环境无需重复执行：
先按 ENVIRONMENTS.md 停止 Prod API 并等待 Worker 排空，发布 Local 代码；启动 API 前执行
`./manage prod tokens migrate --admin-socket /tmp/mysql.sock`，然后
`./manage prod tokens import-legacy --name "Prod 原有内部调用"`，最后再启动 Prod API 和所需数量的 Worker。
发布工具不会自动应用 SQL；务必在启动新 API 前完成迁移与导入。以后创建的 Prod Token 前缀为 wc_prod_。
代码回退会恢复旧的 .env 验证方式，因此回退前应检查旧 API_TOKEN 是否曾被撤销，避免重新放行旧凭证。

### 常见问题

| 结果 | 含义／处理 |
|---|---|
| 401 UNAUTHORIZED | 未带 Token、Token 错误或已撤销；检查环境和完整值 |
| 503 DATABASE_UNAVAILABLE | 检查数据库、权限与 007 迁移；不会回退使用 .env 验证 |
| 404 AUDIT_NOT_FOUND | 任务不存在或已软删除 |
| 409 REPORT_NOT_AVAILABLE / HTML_REPORT_NOT_AVAILABLE | 报告尚未就绪，稍后读取 |
| Token already exists | 旧 Token 已导入；撤销后的 Token 也不能重复导入 |
| Target does not match deployment binding | 命令环境与部署不匹配，检查 Local / Prod 选择 |

本版记录创建／撤销管理员、最后认证时间和任务来源，不包含逐次请求日志、配额或网页管理员登录功能。

## HTML 完成后的项目讨论通知

开启 `DISCUSSION_NOTIFICATIONS_ENABLED=true` 并在当前环境 `.env` 配置
`DISCUSSION_API_TOKEN`。Token 不写入源码、日志或报告，Local/Prod 分别配置。
先用对应目录的 Python 执行迁移，再重启该环境 Worker（保留原数量）：

```sh
.venv/bin/python -m app.discussion_notifications migrate --admin-socket /tmp/mysql.sock
.venv/bin/python -m app.discussion_notifications status
```

通知地址固定为 `https://www.newpages2u.com/admin/project/ProjectAPI.php`。
使用 POST 表单：`action=post-discussion`、`system_id=2`、
`email=websitechecker@newpages.com.my`，任务的 `company_id` / `project_id`，
`report_html` 保存正文 HTML 片段和 inline CSS（`inline-fragment-v2`），不含文档或 main 外壳。
去除重复样式、通用 class 和固定 id，避免与接收页面样式冲突；不删减报告内容。
中文、符号及非 ASCII URL 字符使用 HTML 数字实体，浏览器还原后显示内容不变。
HTML 接口返回该片段，新版报告的 `post_message` 与数据库内容相同；旧版 Unicode
报告在发送前仅转换为等价数字实体，实际参数仍完整记录到 request_parameters。
生成后和发送前检查标签闭合、表格嵌套、残缺属性及无效字符。
发送前按最终实体编码后的 HTML 计算字节数，保守上限为 60,000 bytes，预留接收端包装空间。
这是本系统的保护预算，并非已确认的 NEWPAGES API 限额。超限返回
`discussion_html_too_large`，损坏返回 `discussion_html_invalid`，通知记为 failed，
不调用外部 API、不截断、不自动重试；完整报告仍保留在本系统，可在报告页面查看。
上线不会自动重生成或重发历史报告，已经发送到外部的损坏内容需另行处理。
片段不包含源码换行，保留联系方式之间的 `<br>`。
`app/templates/report.html` 维护正文结构，`app/discussion_html.py` 维护内联样式，
不包含媒体查询或伪类交互样式。`token` 来自环境配置。

首次成功保存 HTML 时，在同一事务写入 `discussion_notifications` 待通知记录。
Worker 在审核轮次结束后领取一条待发送通知（空闲时也会处理），不会调用 OpenClaw。
每个审核最多一条记录，领取使用行锁并在 HTTP 请求前提交 `unknown` 状态，
多个 Worker、重启和重新生成已有 HTML 都不会重复发送。默认不补发历史已有 HTML。
手动补生成原本缺失的 HTML 会入队，需要运行中的 Worker 才会发送。

连接超时 5 秒，其余 HTTP 阶段超时 20 秒；禁止 HTTP 自动重试和重定向。
HTTP 2xx 且 JSON 整数 `status=1` 并有消息 ID 时记录 `sent` 和讨论消息 ID；
HTTP 2xx 且 JSON 整数 `status!=1` 记录 `failed/api_rejected`。
超时、网络错误、非 2xx、无法解析的响应均为 `unknown`，不自动重发。
发送前将实际表单参数保存到 `discussion_notifications.request_parameters`（JSON），
包含完整 `post_message`，`token` 固定记录为 `[REDACTED]`，不存真实 Token。
参数保存失败时不发送请求；历史通知保持 NULL，不补发、不重建历史参数。
此字段是发送前快照，不代表对方已收到，送达状态仍看 `status`。
实际 HTTP 请求仍为 POST 表单。JSON 中的引号转义不改变 HTML 原文。
只存 HTTP 状态与固定错误代码，不存对方响应正文。

`unknown/interrupted_or_in_progress` 表示正在发送或发送过程被中断；
若长期保持该状态，应到项目讨论区核实。即使已经发送成功但保存结果失败，
也保留 `unknown`，绝不自动重发。发送前进程崩溃也可能导致没有实际发送，
这是“最多尝试一次”的取舍，不能保证必达。
通知结果不改变审核的 completed 状态，不重新运行审核。
管理员命令 migrate/status 不会发送任何通知，没有自动或手动重发命令。

## 失败任务重试 API

部署前由数据库管理员在目标环境执行 `sql/009_audit_retries.sql`，然后按该环境的
发布流程加载新 API 代码。迁移不会创建审核任务。本接口沿用现有共享内部 Bearer
Token 权限模型，不提供浏览器链接或独立链接凭证。

`POST /websiteChecker/api/v1/audits/{原任务 job_id}/retry`

请求需要 `Authorization: Bearer <API_TOKEN>`，无需请求体。服务器读取原任务的
`project_id`、`company_id`、`website_url`，创建新的 queued 任务，由现有 Worker
处理；原失败任务及其错误保留。新任务的 `api_token_id` 记录本次调用使用的 Token。
本接口不会在失败后自动调用，也不会修改 Discussion 通知流程。

首次成功返回 HTTP 202，例如：

```json
{
  "status": 1,
  "original_job_id": "11111111-1111-4111-8111-111111111111",
  "job_id": "22222222-2222-4222-8222-222222222222",
  "job_status": "queued",
  "created": true
}
```

重复 POST 原任务返回 HTTP 200、`created: false` 和同一个重试任务的当前状态。
对同一个原任务，即使其重试任务已经 completed 或 failed，也不会再创建任务。可通过现有任务状态、
JSON 和 HTML API 查询返回的新 `job_id`。

重试任务再次失败后，可以对它的新 `job_id` 调用相同接口，继续创建下一次重试，
任务链不限制重试次数。例如 A 失败后重试得到 B，B 失败后调用 B/retry 得到 C；
重复调用 A/retry 始终返回 B，重复调用 B/retry 始终返回 C。
数据库原任务行锁及唯一关系确保每个任务只创建一个直接子任务，
子任务和关联记录在同一事务提交。子任务被软删除后也不会重复创建。

| HTTP | error_code | 含义 |
| --- | --- | --- |
| 401 | UNAUTHORIZED | 缺少或无效的 Bearer Token |
| 404 | AUDIT_NOT_FOUND | 原任务不存在或已删除 |
| 409 | AUDIT_NOT_RETRYABLE | 原任务不是 failed（包括仅 HTML 失败的 completed 任务） |
| 409 | RETRY_TASK_DELETED | 已创建的重试任务被删除，不重复创建 |
| 409 | AUDIT_OUTCOME_UNKNOWN | 上游执行结果未知，不能安全启动第二次检查 |
| 422 | VALIDATION_ERROR | job_id 格式无效 |
| 503 | DATABASE_UNAVAILABLE | 数据库操作不可用，包括尚未应用迁移 |

错误统一返回 `{"status":0,"error_code":"..."}`。GET 不会创建重试任务。

## 可选 NEWPAGES 项目标识

创建审核 `POST /websiteChecker/api/v1/audits` 只要求 `website_url`。
`company_id`、`project_id` 均可省略；null、空字符串和纯空格统一为 `""`，
状态 API 也返回空字符串。提供时仍需为最长 255 字符的字符串。
只提供一个标识也可正常检查，但不会发送 NEWPAGES Discussion。
仅当两个标识均非空且通知功能已启用时，HTML 报告才会加入通知队列并发送。
未提供标识的任务仍保存报告，支持原有状态、报告查询和 retry API；重试继承原标识。

## 失败任务的 cPanel 重试链接

各环境在自己的 `.env` 设置（Prod 替换为正式 cPanel 地址）：

```dotenv
AUDIT_RETRY_PAGE_URL=https://yifantest.newpages.com.my/admin/website_checker/
```

未设置时不生成链接。Local 已配置上面的 staging 地址；Prod 配置需在发布时独立设置，不能复制整个 `.env`。
修改后需重新加载对应 API/Worker 配置。本次实现未重启服务。
cPanel 的 API 地址和 Token 必须指向产生该任务的同一环境，否则会查不到任务。

`GET /websiteChecker/api/v1/audits/{job_id}` 新增：
- `retry_url`：失败任务的 cPanel 地址，参数 `retry_job_id` 为原任务 UUID；其他状态或未配置返回 null。
- `retry_allowed`：普通失败为 true，其他状态及 `outcome_unknown` 为 false。

API 保持 JSON 响应，调用方通过 retry_url 导航，不返回 HTTP 302。
cPanel 登录后打开链接自动显示弹窗，查询当前状态；用户点击按钮才 POST 原任务的 `/retry`。
重复点击复用同一个重试任务；重试创建新任务，不重启 Gateway 或 Worker 服务。

启用现有 `DISCUSSION_NOTIFICATIONS_ENABLED`、配置通知 Token 和重试页地址后，
新发生的任务失败与通知排队在同一数据库事务提交（包括心跳过期回收）。
任务必须同时有 company_id 和 project_id。通知包含网站、任务 ID、错误代码和重试链接，
所有动态 HTML 内容均转义。复用现有 discussion_notifications 表，无新增迁移。
保持 at-most-once 投递，不自动补发历史失败或重发结果未知的通知。
HTML 单独渲染失败仍是 completed，不发送任务失败通知。
API/数据库整体不可用时无法保证当时返回链接或发送通知。
