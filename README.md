# 卸货泊位结算板

供城市配送管理岗核对杆侧停靠、卸货预约与实收回执。阶段导航按采信、匿名关联、占位重建、核价、对账及签发展开。竖向时间轨显示同一次停靠的午夜切分和收费档。

## 启动

```sh
docker compose up --build
```

访问 `http://localhost:18471/`。本地用 Python 3.12 运行。

```sh
python -m pip install -r requirements-test.txt
python -m uvicorn app.main:create_app --factory --port 18471
python -m pytest -q
```

默认数据库为 `var/curb.sqlite3`，容器使用 `curb-dockets` 卷。重置自建环境可在停服后删除该数据库文件或命名卷。生成新的数据库会生成新的车辆代号密钥。

## 核对样例

1. 首页选择「跨夜争议样例」。两个泊位合计应管 240 分钟，133 空闲、7 争议、10 首免、68 基础价及 22 升级价。
2. 「占用区间重建」查去重的 17 分钟；「首免与升级价」选择 11/30 23:40 的 B-01，时间轨显示首免一次、午夜切日、升级档和 5 分钟同泊争议。
3. 「逐单与泊位对账」查看应收 ¥22.40、关联实收 ¥23.10、净差 ¥0.70，以及单列的游离回执 ¥1.40。
4. 点击「运行清算」生成留档，重复运行复用同一编号。进入「理赔证据签发」下载去车牌化的逐单证据。

## 补扫重算

杆侧补扫把到达分钟提前后，原批次挂上的回执会变成游离款。提交补扫时引用原留档编号：

```sh
curl -s localhost:18471/api/dockets -H 'Content-Type: application/json' \
  -d '{"bundle": {...四份原件...}, "parent": "<原留档编号>"}'
```

逐单页的「补扫认回」列出能认回的回执及归属依据，「仍有争议的款」列出不能认回的回执及原因。同一补扫重跑复用同一编号，不重复计收或留档。详见[理赔复核步骤](docs/APPEAL.md)。

独立预览为 `GET /api/cases/{dirty|clean}/{stage}`，阶段名见深度登记；带 `parent=<原留档编号>` 可按补扫口径预览。自有样例可以 `POST /api/dockets`，`bundle` 对象需包含 `survey`、`sensors`、`bookings`、`payments` 四份 UTF-8 字符串，内置数据可用 `{"sample":"dirty"}`，补扫另加 `parent`。历史 `GET /api/dockets`，归档 `GET /api/dockets/{key}`，理赔附件 `GET /api/export/{key}`。

- [字段、计费与边界](docs/DATA-DICTIONARY.md)
- [模块和陷阱](docs/DEPTH.md)
- [理赔复核步骤](docs/APPEAL.md)
