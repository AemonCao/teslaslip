# TeslaSlip

停车或充电结束后，TeslaSlip 生成一张可用随机链接分享的中文总结页，并用 [Bark](https://github.com/Finb/bark-server) 推到手机。登录后的历史总览可查看已生成报告、全部已结束行程和充电记录。

行程和充电数据来自 TeslaMate 的 Postgres。单次总结由 TeslaMate 已经发到 MQTT 上的车辆状态触发；周报、月报和年报由独立定时任务生成。本服务对数据库只有只读查询，不改 TeslaMate，也不接 Tesla 账号。

后续图表功能的阶段安排、数据依据和验收要求见 [图表开发计划](图表开发计划.md)。

## 一次出门

相邻两段 `drives` 的间隔（下一段 `start_date` − 上一段 `end_date`）小于 `OUTING_GAP_MINUTES`（默认 10 分钟）时，合成同一张页、同一条通知。

一段同时满足下面两条才算挪车：

- 距离小于 `MIN_PUSH_DISTANCE_KM`（默认 0.3 km）
- 时长小于 `MIN_PUSH_DURATION_MIN`（默认 2 分钟）

距离或时长为空时，这段不算挪车。整次出门的每一段都是挪车时，水位向前拨过，不建链接，也不发 Bark。夹在中间的挪车段仍画在同一张页上。

车辆 `state` 离开 `driving` 之后，程序按 15 秒、60 秒、3 分钟查看 TeslaMate 是否写上 `end_date`。写上之后，安静截止时间是该段结束时间加上出门间隔，并记在 SQLite 里，重启后续上。截止前又变成 `driving`，这次出门继续，水位不动。截止到达且当前状态不是 `driving` 时，才组页、发通知。

## 一次充电

每一条已经写上 `end_date` 的 `charging_processes` 单独一张页、单独一条 Bark。下面任一变化都会去查库：

- `charging_state` 变成 `Complete`、`Disconnected`、`Stopped` 或 `NoPower`
- 车辆 `state` 离开 `charging`

`end_date` 还没出现时，同样按 15 秒、60 秒、3 分钟再查。多次充电不会并成一张页，也没有最小电量门槛。`Stopped` 有时是预约充电暂停，TeslaMate 仍会关单，第一版会照常推送。

## 水位

SQLite 里的 `drive_watermark` 和 `charge_watermark` 是已经处理过的最大 id。Bark 发送成功之后才前移。失败时按 30 秒、2 分钟、10 分钟重试同一条链接。

第一次启动、库里还没有水位时，水位设成「当前已结束记录的最大 id」。那之前的行程和充电不补推。同时为水位之下最近一次合格出门生成预览链接，写进 `teslaslip.preview` 日志，不发 Bark。

连上 MQTT 后，先记下当前保留的 `state` 和 `charging_state`。这条保留值不当成新的停车或充电结束。记下之后，再补水位以上、安静期已过的出门，以及水位以上已经结束的充电。这是补上进程宕机期间错过的状态变化。

## 周报、月报和年报

报告提供六项总览：行驶里程、驾驶时长、出门次数、用车天数、充入电量和已记录充电费用。周报、月报按日展示里程与充电量趋势，年报按月展示；同时提供上一周期对比、充电次数与时长、费用完整度、最多三条事实亮点和常去地点。年报额外展示已记录的到达城市。

默认开启自动报告，按上海时间在以下日期的 `REPORT_HOUR` 点（默认 09:00）生成并通过 Bark 推送随机链接：

- 周一：上一个周一 00:00 到本周一 00:00 的周报。
- 每月 1 日：上一个自然月的月报。
- 每年 1 月 1 日：上一个自然年的年报。

首次启用只初始化各周期游标，不补推已经到期的历史报告。如果在到期日推送时间之前启动，当天到时仍会推送。之后服务重启会补各类最近一期已到期报告；停机跨过多个周期时，各类仅补最新一期，历史可用命令手动生成。`REPORTS_ENABLED=false` 关闭自动生成，手动生成仍可用。

生成结果是 SQLite 中的固定快照，打开页面和 Bark 重试不会再次查询 TeslaMate。每车、每类、每周期只有一份报告；报告、随机链接、发件箱和周期游标一起提交。成功发送后标记已发送，失败沿用 Bark 的重试间隔及原链接。发送成功但本地确认前进程退出的极短窗口仍可能重复通知，不保证跨网络的恰好一次投递。

统计口径：

- 使用上海时间的半开区间 `[开始, 结束)`。只计已结束记录，行程和充电整条归入结束日；跨周期记录不拆分时长、里程和电量。
- 出门在各周期内沿用 `OUTING_GAP_MINUTES` 分组和挪车门槛；跨周期的一次出门在各周期分别合并。纯挪车不计出门次数，里程和时长仍计入。
- 用车天数按有已结束行程的日期去重。无记录不能据此推断车辆未使用或采集正常。
- 费用缺失显示未知，零费用保留为零；费用沿用 TeslaMate 原记录币种，不额外假设币种。部分费用缺失时展示已知合计及有费用记录的次数。
- 里程、时长、电量缺失或无效时仅合计已知值并注明缺失数，相关上期比较停用；上期为零时不计算增长百分比。
- 数据库最早记录晚于本期开始时标为范围不完整。只有最早记录覆盖两个周期，才提供上期对比；这不代表中间没有采集缺口。自然月、自然年的总量比较未按天数归一化。
- 充入电量不当作行驶耗电，不估算百公里能耗、电池健康、回收率或准确行驶电费。第一版不包含通勤推断、足迹地图和年度分享卡。

本机使用相同环境变量及 `SQLITE_PATH`，可手动生成已结束周期，日期填写该周期内任一天：

```powershell
.\.venv\Scripts\python -m app.report week 2026-10-19
.\.venv\Scripts\python -m app.report month 2026-09-01
.\.venv\Scripts\python -m app.report year 2025-01-01
.\.venv\Scripts\python -m app.report month 2026-09-01 --push
```

命令输出完整私有链接，请妥善保存。默认仅生成不推送；`--push` 加入发件箱，由使用同一 SQLite 的运行中服务在约一分钟内接手。对已发送报告重复 `--push` 不重发。NAS 容器内可将解释器改为 `python`。命令同样会写 SQLite，NAS 上执行仍遵守无校验盘的写盘确认要求。

报告链接沿用 `TRIP_LINK_TTL_DAYS`，匿名访问过期链接返回 404，登录后仍可查看；同周期重复生成复用原链接与快照，不刷新有效期。数据库后续补录或修改费用不会改变已生成报告。通知只含日期和汇总数字，地点只显示在详情页里；报告列表仅登录后可见。

## 页面

| 路径 | 作用 |
| --- | --- |
| `/d/{token}` | 出门总结 |
| `/c/{token}` | 充电总结 |
| `/r/{token}` | 周报、月报、年报固定快照 |
| `/settings` | 这台手机的显示设置 |
| `/healthz` | 健康检查 |
| `/` | 登录后的报告、行程、充电历史总览 |
| `/login` | 管理员登录，成功后返回原页面 |
| `/logout` | POST 退出并撤销当前会话 |
| `/history/drives/{id}` | 仅登录可见的历史单段行程 |
| `/history/charges/{id}` | 仅登录可见的历史充电详情 |

分享链接中的 `token` 是 16 字节 URL-safe 随机串，不包含 drive id 或充电 id。未知链接及库里对不上的记录返回 404，正文是「未找到」。未登录打开过期链接时返回 404 和登录入口，不读取或展示详情；登录成功后回到原链接。

`TRIP_LINK_TTL_DAYS` 为 `0` 时链接一直有效。正整数表示从创建起允许匿名访问的天数。登录用户不受链接有效期限制，过期的链接映射与报告快照保留。修改此配置只影响之后新建的链接，不会追溯修改已有链接的有效期。

历史总览支持类型、年份、日期筛选，每页 20 条，按时间倒序。报告按周期开始日筛选，行程和充电按结束日筛选，日期包含所选结束日。行程列表逐条展示 TeslaMate 中已结束的 `drives`，包括未推送及挪车记录；通知中的出门总结继续沿用原有合并规则。列表仅查询汇总字段，不读取轨迹或充电采样。历史详情中的「生成分享链接」会新建随机链接并打开分享页，复制浏览器地址即可分享；单纯浏览历史不会生成分享链接、发送 Bark 或改动处理水位。每次点击均生成独立链接，不延长旧链接有效期。

分享页只包含当前记录及登录总览入口，不提供其他记录的链接。没有 OpenAPI 文档。`/settings` 只改当前浏览器里的主题、地图和显示哪些块，不改服务配置。

## 启用历史总览

第一版为单管理员账号。先在本机交互生成密码哈希，密码至少 12 个字符，不通过命令行参数传入：

```powershell
.\.venv\Scripts\python -m app.auth
```

将输出的完整哈希填入部署环境的 `ADMIN_PASSWORD_HASH`，使用单引号包住值，防止 Docker Compose 展开 `$`；不要提交实际哈希或密码。`ADMIN_USERNAME` 默认为 `admin`。应用本身不自动加载本机 `.env`，本机运行时需通过环境变量注入；Compose 使用现有 `env_file`。

哈希为空时登录及总览关闭，显示未配置提示并返回 503，原有单页和通知继续工作。密码采用带随机盐的 scrypt 哈希，SQLite 只保存随机会话令牌的 SHA-256 摘要。登录状态持续 7 天、重启后保留，退出立即撤销当前会话；更换账号或密码哈希并重启服务后，旧会话失效。全局每 5 分钟最多允许 10 次登录尝试，计数跨重启保留；登录、退出和分享操作均校验表单防伪令牌。

Cookie 使用 `HttpOnly`、`SameSite=Lax`；`PUBLIC_BASE_URL` 为 HTTPS 时额外启用 `Secure`，此时登录需使用 HTTPS 入口，HTTP 可用于访问匿名分享页。外网登录使用 HTTPS，并让 `PUBLIC_BASE_URL` 与浏览器使用的入口一致。页面均设置 `Cache-Control: no-store`、禁止搜索索引及嵌入 iframe。登录页、总览和历史详情使用 `Referrer-Policy: same-origin`，以保留同站表单的来源信息，同时不向外站发送来源；分享详情仍使用 `no-referrer`，避免泄露随机链接。

**需要分享自动到期时，将 `TRIP_LINK_TTL_DAYS` 配为正整数，例如 `7`。默认 `0` 表示永久有效，已有永久链接也不会因新配置自动到期。**

时间按 `Asia/Shanghai` 显示。TeslaMate 里的 `timestamp without time zone` 按 UTC 理解。坐标在服务端从 WGS-84 转成 GCJ-02 再交给页面，中国境外的点保持原坐标。

出门页有起止时间、总里程、驾驶时长、出门耗时、起终点围栏和地址、轨迹、续航与电量、车内外温度、爬升和下降、胎压、空调。多于一段时还有分段表。链接上如果记了导航目的地，页面会写「停车前最后一次导航目的地，可能为空或不准」。

地图上每一段行程是实线，段与段之间用虚线并标注「停车」。一段超过 2000 个点时均匀抽稀，保留首尾。配置了 `AMAP_JS_KEY` 时，浏览器加载高德 JS API 2.0，折线已经是 GCJ-02。没配 Key 时只画无底图折线，并写「未配置高德 Key」。

驾驶路线默认按速度着色：以本页全部行程原始采样的最低和最高速度为范围，映射至 0–100 共 101 个色阶，从慢速红色经黄色渐变至快速绿色。下方色标显示本次范围（km/h），不同出门页的同一颜色不代表相同绝对速度。高德地图与无底图折线使用相同配色，主题切换不会改变速度颜色。每条线段取相邻保留点速度的平均值，因此采样间及抽稀后的颜色表示近似速度；相邻同色线段合并绘制。全程速度相同时统一显示黄色。零速有效，缺失或无效速度的路段使用主题色并标注说明；停车连接线保持虚线，不参与速度配色。

充电页是一个点，位置来自 `charging_processes.position_id`。页面有起止、时长、加入电量、充电消耗、起止电量和续航、围栏、地址、室外温度，以及该次 `charges` 采样里的充电器功率、电压、电流、相数、线缆和快充信息。功率、电压、电流、相数注明是采样最大值。`cost` 为空时不显示费用。

## Bark

请求是 `POST {BARK_URL}/push`，JSON 里带 `device_key`、标题、正文、页面绝对地址，`group` 为 `teslaslip`，`isArchive` 为 `1`，`level` 为 `active`。

行程、充电和报告通知均附带「查看详情」「历史总览」两个 Markdown 链接；直接点击通知仍打开本次详情，展开通知或进入 Bark 历史可选择总览入口。纯文本 `body` 同时保留摘要和总览完整地址，供不支持 Markdown 的客户端显示。总览链接不携带登录凭证，未登录时需要验证账号，不会因通知泄露其他记录。

出门标题：两端围栏都有时为「起点围栏 → 终点围栏」；只有一端就写那一个名字；都没有则为「出门 {里程} km」。正文是里程、驾驶时长、额定续航差、结束时的额定续航。

充电标题：有围栏用围栏名，否则是「充电完成」。正文是加入电量、时长、结束电量。

通知正文只放数字和围栏名。街道、门牌和导航目的地只出现在网页上。

## 进程

一个容器里一个 Python 进程：

- FastAPI 提供页面和 `/healthz`
- 一个 MQTT 任务，只订下面三个主题
- 一个本地定时器，负责关单重试、安静期和 Bark 重发
- 一个独立报告任务，负责自然周、月、年到期生成及失败重试

单次总结定时器不扫描 TeslaMate 数据库。查 TeslaMate 的时机是进程启动、MQTT 连上后的追赶、状态变化，以及已经写在 SQLite 里的下一次检查时间。独立报告任务只在周期到期或生成失败重试（间隔 5 分钟）时查库：按车辆、结束时间范围读取本期与上期的行程及充电汇总，再查询最早记录时间，不读取轨迹和充电采样。常规定时器最多每分钟检查本地 SQLite 发件箱，以接收手动生成的待推送报告。

主题（`CAR_ID` 默认 `1`；`MQTT_NAMESPACE` 非空时插在 `teslamate/` 和 `cars/` 之间）：

- `teslamate/cars/{CAR_ID}/state`
- `teslamate/cars/{CAR_ID}/charging_state`
- `teslamate/cars/{CAR_ID}/active_route`

QoS 为 1。断线后指数退避重连，间隔从 1 秒翻倍，上限 60 秒。`active_route` 在 `error` 为空、且车辆处于 `driving` 时记住 `destination`，这次出门关闭时抄到链接上。

`GET /healthz` 在三件事都成功时返回 200：SQLite 可写、Postgres `SELECT 1` 成功、MQTT 当前已连接。响应是 `{"ok": true}`。任一失败返回 503 和 `{"ok": false}`。

普通日志会去掉坐标、token、device key、数据库密码和 MQTT 密码。预览链接是例外，完整 URL 只出现在 `teslaslip.preview` 这一行。

## 配置

在仓库根目录放一份 `.env`。该文件已在 `.gitignore` 里。密钥只放环境变量。

必填：`PUBLIC_BASE_URL`、`DATABASE_URL`、`MQTT_USERNAME`、`MQTT_PASSWORD`、`BARK_DEVICE_KEY`。

```env
PUBLIC_BASE_URL=http://192.168.1.223:8791
DATABASE_URL=postgresql://teslaslip_ro:替换成密码@192.168.1.223:45432/teslamate
MQTT_USERNAME=替换成用户名
MQTT_PASSWORD=替换成密码
BARK_DEVICE_KEY=替换成设备密钥
ADMIN_USERNAME=admin
ADMIN_PASSWORD_HASH=''

BIND_HOST=0.0.0.0
BIND_PORT=8791
MQTT_HOST=192.168.1.217
MQTT_PORT=1883
MQTT_CLIENT_ID=teslaslip
MQTT_NAMESPACE=
CAR_ID=1
BARK_URL=http://192.168.1.223:21850
AMAP_JS_KEY=
AMAP_SECURITY_JS_CODE=
OUTING_GAP_MINUTES=10
MIN_PUSH_DISTANCE_KM=0.3
MIN_PUSH_DURATION_MIN=2
TRIP_LINK_TTL_DAYS=0
REPORTS_ENABLED=true
REPORT_HOUR=9
TZ=Asia/Shanghai
SQLITE_PATH=/data/teslaslip.sqlite
LOG_LEVEL=info
```

`BARK_URL` 只写到服务根地址，不要带 `/push`。`PUBLIC_BASE_URL` 末尾的斜杠会被去掉。高德控制台按这个地址的域名做 referrer 限制；需要安全密钥时再填 `AMAP_SECURITY_JS_CODE`。

本机直接跑时，把 `SQLITE_PATH` 改成可写文件，例如 `./teslaslip.sqlite`。

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `PUBLIC_BASE_URL` | 必填 | Bark 里的链接前缀，例如 `http://192.168.1.223:8791` |
| `DATABASE_URL` | 必填 | 只读账号的 Postgres 连接串 |
| `MQTT_USERNAME` | 必填 | MQTT 用户名 |
| `MQTT_PASSWORD` | 必填 | MQTT 密码 |
| `BARK_DEVICE_KEY` | 必填 | Bark 设备密钥 |
| `ADMIN_USERNAME` | `admin` | 历史总览管理员账号 |
| `ADMIN_PASSWORD_HASH` | 空 | `python -m app.auth` 生成的 scrypt 哈希；空时关闭登录与总览 |
| `BIND_HOST` | `0.0.0.0` | 监听地址 |
| `BIND_PORT` | `8791` | 监听端口 |
| `MQTT_HOST` | `192.168.1.217` | broker 地址 |
| `MQTT_PORT` | `1883` | broker 端口 |
| `MQTT_CLIENT_ID` | `teslaslip` | 不要和 Home Assistant 用同一个 client id |
| `MQTT_NAMESPACE` | 空 | 这台 broker 留空 |
| `CAR_ID` | `1` | TeslaMate 的 `cars.id` |
| `BARK_URL` | `http://192.168.1.223:21850` | Bark 服务根地址 |
| `AMAP_JS_KEY` | 空 | 空则页面只有无底图折线 |
| `AMAP_SECURITY_JS_CODE` | 空 | 高德安全密钥 |
| `OUTING_GAP_MINUTES` | `10` | 两段间隔小于该值才算同一次出门 |
| `MIN_PUSH_DISTANCE_KM` | `0.3` | 挪车距离门槛 |
| `MIN_PUSH_DURATION_MIN` | `2` | 挪车时长门槛 |
| `TRIP_LINK_TTL_DAYS` | `0` | 匿名分享有效天数；`0` 永久，登录后不受限；只影响新链接 |
| `REPORTS_ENABLED` | `true` | 自动生成周报、月报和年报；支持 `true` / `false` / `1` / `0` |
| `REPORT_HOUR` | `9` | 上海时间的报告推送小时，0–23 |
| `TZ` | `Asia/Shanghai` | 页面时区 |
| `SQLITE_PATH` | `/data/teslaslip.sqlite` | 水位、链接和发件箱 |
| `LOG_LEVEL` | `info` | 日志级别 |

## Postgres 只读账号

连接使用 `default_transaction_read_only=on`，语句也只接受 `SELECT` / `WITH`。部署时在 `teslamate` 库执行一次，密码自己生成，写进 NAS 上的 `.env`：

```sql
CREATE ROLE teslaslip_ro LOGIN PASSWORD '替换成生成的密码';
GRANT CONNECT ON DATABASE teslamate TO teslaslip_ro;
GRANT USAGE ON SCHEMA public TO teslaslip_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO teslaslip_ro;
```

轨迹查询带 `drive_id`，并限制在该段的 `start_date` 和 `end_date` 之间。

## 过程图表

出门页提供速度、输出／回收功率、电量曲线；充电页提供功率、电量、原始累计充入电量曲线。Vue 3 岛屿用原生 SVG 绘图，不依赖地图或高德 Key。两类图表可在设置页分别关闭，旧偏好默认开启。

同页曲线共用真实时间轴和游标，上海时间显示；点按、横向拖动或键盘方向键查看最近保留的实际采样及时间差。停车和长缺口断线，不补零、不插值。统计使用抽稀前的有效采样；绘图按时间桶保留首尾与峰谷，阶梯及状态图额外保留变化前后的点。单条序列通常不超过约 2000 点；分段、缺口边界及阶梯变化必须保留，极端情况下允许超过目标。

更多环境与车辆图表默认折叠，展开才挂载：海拔、车内外温度、胎压、额定／理想续航、空调／除霜／电池加热状态，以及充电电压、电流、外温。全空辅助指标不显示；状态缺失为未知，不外推到段首尾。

驾驶页另有按相邻有效采样间隔及前值累计的速度时长分布，以及连续零速至少 30 秒的段内等待区间。分母为采样覆盖的驾驶时间，未覆盖时间单列；跨段停车、长缺口不计入。累计充入电量下降／重置时断线并标注，不将它修正为连续曲线，也不据此估算损耗或净能量。

所有采样复用当前随机链接的有界只读查询，内嵌安全 JSON，不新增 HTTP API、SQLite 采样表或 MQTT 订阅。无 JavaScript 时保留原文字总结。

## 运行

依赖钉在 `requirements.txt`：Python 3.12、FastAPI、uvicorn、Jinja2、aiomqtt 2.x、psycopg 3.x、httpx。

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements-dev.txt
python -m app
```

页面样式和交互在 `web/`。镜像构建时会执行 `npm ci` 和 `npm run build`，产物放进 `app/static`，由这个 Python 进程内联样式和首屏脚本，并托管岛屿脚本。

本机改界面时另开一个终端：

```powershell
cd web
npm install
npm run dev
```

再设 `TESLASLIP_VITE_DEV=1` 后启动 `python -m app`。这个变量只给本机开发用，不要写进 NAS 的 `.env`。没设它、又没有构建产物时，页面仍能打开，只是没有样式、设置开关和地图脚本。

容器监听 `8791`。镜像基于 `python:3.12-slim`，时区是 `Asia/Shanghai`，健康检查访问 `http://127.0.0.1:8791/healthz`。

```powershell
docker compose up -d --build
```

`docker-compose.yml` 把宿主机 `8791` 映射进容器，并把 `/mnt/user/appdata/teslaslip` 挂到 `/data`。Compose 项目名是 `teslaslip`。这个服务不声明对 TeslaMate 容器的依赖，数据库、Bark 和 MQTT 都走已经在宿主机上打开的地址。

这台 Unraid 阵列没有校验盘。把镜像和 `appdata` 写上 NAS 之前要先确认。

## 测试

测试不连接 NAS、MQTT 或高德：

```powershell
.\.venv\Scripts\pytest
cd web
npm test
```

覆盖出门分组、挪车判断、水位、GCJ-02、链接过期、页面、Bark 文案、MQTT 状态机、只读 SQL 和日志脱敏。

## 目录

```text
app/main.py          页面、健康检查、进程入口
app/runtime.py       MQTT 循环、安静定时器、Bark 发件箱
app/mqtt.py          订阅主题和状态变化
app/outing.py        出门分组、挪车、水位
app/charge.py        充电采样汇总
app/teslamate.py     TeslaMate 只读查询
app/db.py            SQLite
app/bark.py          Bark 文案和 HTTP
app/pages.py         页面数据
app/charts.py        图表序列、峰谷抽稀和速度时长统计
app/reports.py       报告周期、汇总快照和持久化调度
app/report.py        手动生成报告的命令入口
app/geo.py           WGS-84 转 GCJ-02，轨迹抽稀
app/webassets.py     前端构建产物
app/templates/       出门页、充电页、设置页
web/                 Vue 3 岛屿、主题脚本和样式
tests/               单元测试和验收测试
开发计划.md          尚未实现的候选功能
```

## 范围

下面这些不在这个服务里：

- Tesla Fleet API 和 Tesla 账号 token
- 哨兵录像、Autopilot 事件、官方导航历史
- 电池包容量估算；页面给出的是额定续航差、理想续航差和电量差
- 修改 TeslaMate、Grafana 或 Postgres 里的业务数据
- 补推第一次启动之前已经结束的行程和充电
- 账号、公开列表、管理后台
- Cloudflare、Tailscale、FRP；外网入口自己配，链接前缀用 `PUBLIC_BASE_URL`
