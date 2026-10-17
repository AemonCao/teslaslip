# TeslaSlip

停车或充电结束后，TeslaSlip 生成一张只能靠链接打开的中文总结页，并用 [Bark](https://github.com/Finb/bark-server) 推到手机。

行程和充电数据来自 TeslaMate 的 Postgres。何时去查库，看 TeslaMate 已经发到 MQTT 上的车辆状态。本服务对数据库只有只读查询，不改 TeslaMate，也不接 Tesla 账号。

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

## 页面

| 路径 | 作用 |
| --- | --- |
| `/d/{token}` | 出门总结 |
| `/c/{token}` | 充电总结 |
| `/healthz` | 健康检查 |
| `/` | 返回「未找到」 |

`token` 是 16 字节 URL-safe 随机串。地址里没有 drive id 或充电 id。未知链接、过期链接、以及库里对不上的记录，都返回 404，正文是「未找到」。

`TRIP_LINK_TTL_DAYS` 为 `0` 时链接一直有效。正整数表示从创建起多少天，超过这个时间再打开返回 404。

没有登录，没有历史列表，没有管理页，也没有 OpenAPI 文档。

时间按 `Asia/Shanghai` 显示。TeslaMate 里的 `timestamp without time zone` 按 UTC 理解。坐标在服务端从 WGS-84 转成 GCJ-02 再交给页面，中国境外的点保持原坐标。

出门页有起止时间、总里程、驾驶时长、出门耗时、起终点围栏和地址、轨迹、续航与电量、车内外温度、爬升和下降、胎压、空调。多于一段时还有分段表。链接上如果记了导航目的地，页面会写「停车前最后一次导航目的地，可能为空或不准」。

地图上每一段行程是实线，段与段之间用虚线并标注「停车」。一段超过 2000 个点时均匀抽稀，保留首尾。配置了 `AMAP_JS_KEY` 时，浏览器加载高德 JS API 2.0，折线已经是 GCJ-02。没配 Key 时只画无底图折线，并写「未配置高德 Key」。

充电页是一个点，位置来自 `charging_processes.position_id`。页面有起止、时长、加入电量、充电消耗、起止电量和续航、围栏、地址、室外温度，以及该次 `charges` 采样里的充电器功率、电压、电流、相数、线缆和快充信息。功率、电压、电流、相数注明是采样最大值。`cost` 为空时不显示费用。

## Bark

请求是 `POST {BARK_URL}/push`，JSON 里带 `device_key`、标题、正文、页面绝对地址，`group` 为 `teslaslip`，`isArchive` 为 `1`，`level` 为 `active`。

出门标题：两端围栏都有时为「起点围栏 → 终点围栏」；只有一端就写那一个名字；都没有则为「出门 {里程} km」。正文是里程、驾驶时长、额定续航差、结束时的额定续航。

充电标题：有围栏用围栏名，否则是「充电完成」。正文是加入电量、时长、结束电量。

通知正文只放数字和围栏名。街道、门牌和导航目的地只出现在网页上。

## 进程

一个容器里一个 Python 进程：

- FastAPI 提供页面和 `/healthz`
- 一个 MQTT 任务，只订下面三个主题
- 一个本地定时器，负责关单重试、安静期和 Bark 重发

定时器不扫描数据库。查 TeslaMate 的时机是进程启动、MQTT 连上后的追赶、状态变化，以及已经写在 SQLite 里的下一次检查时间。

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
| `TRIP_LINK_TTL_DAYS` | `0` | `0` 表示链接不过期 |
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

## 运行

依赖钉在 `requirements.txt`：Python 3.12、FastAPI、uvicorn、Jinja2、aiomqtt 2.x、psycopg 3.x、httpx。

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements-dev.txt
python -m app
```

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
app/geo.py           WGS-84 转 GCJ-02，轨迹抽稀
app/templates/       出门页、充电页
tests/               单元测试和验收测试
开发计划.md          实现规格
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
