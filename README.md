# 电网模拟器 grid_simulator

> 微电网大作业 · A 板块(电网模拟器 / 数据源 / 协议中枢)
> 独立开发起步包——先把骨架立起来,再叠加模型、通信、UI

---

## ▶ 立刻运行(6 步,2 分钟)

在 PowerShell 里**进项目根目录**(下面路径就是你现在的工程位置),然后:

```powershell
cd "C:\Users\lenovo\WPSDrive\1743162206\WPS企业云盘\清华大学\我的企业文档\python代码\Qt\大作业"

# ① 建库(从 config/schema.sql 生成 grid.db,内含 19 张表 + 全部点表种子)
python tools/build_db.py

# ② 生成 6 个验收场景 CSV
python tools/gen_scenario.py

# ③ 验证数据库(打出来 11 遥测/8 遥信/2 遥控/3 遥调 + 6 项 P0 验收清单)
python tools/verify_db.py

# ④ 加载默认场景到 env_curve(选场景不写就走"适宜风速"那个)
python tools/load_scenario.py

# ⑤ 跑仿真时钟(30 倍速,跑到 t=120s 自动停)
python main.py --speed 30 --until 120

# ⑥ 抽查 sim_history 落数据情况
python tools/dump_history.py --limit 10
```

**期望看到:**
- `verify_db.py` 末尾打 6 行 `[OK]`
- `main.py` 每 5 行打印一行 `t=Ns wind=Xm/s load=YkW ...`
- `dump_history.py` 显示出 100+ 条 sim_history 记录


---

## ▶ PyCharm 里怎么 Run(推荐)

1. **Open** 整个 `大作业` 文件夹(PyCharm → File → Open → 选 `大作业`)
2. 左侧 Project 面板会显示整个树
3. 想要看效果,依次**右键 Run**:
   - `tools/build_db.py`
   - `tools/gen_scenario.py`
   - `tools/verify_db.py`
   - `tools/load_scenario.py`
   - `main.py`
4. 给 main.py 传参数:PyCharm 顶部菜单 **Run → Edit Configurations → 选中 main → Parameters 框填** `--speed 30 --until 120`,以后每次 Run 都带这些参数
5. 看 db 内容:右键 `grid.db` → **Open Database**(PyCharm Professional 自带 SQLite 客户端;Community 没有就装个 SQLiteStudio 或者用本仓库 `tools/dump_history.py`)

---



## 当前目录

```
大作业/
├─ main.py                 # 仿真时钟主入口(P0 已可跑)
├─ config/
│   └─ schema.sql          # grid.db 的 schema(v0.2:WAL + 19 张表 + 点表种子)
├─ docs/
│   └─ interface.md        # 三方接口文档 v0.2(点表 + TCP 帧 + 交互流程)
├─ tools/
│   ├─ gen_scenario.py     # 6 个验收场景生成器
│   ├─ build_db.py         # 一键建库 / 重建
│   ├─ verify_db.py        # 一键验证 db 结构与点表
│   ├─ load_scenario.py    # 把场景 CSV 灌进 env_curve
│   └─ dump_history.py     # 抽查 sim_history
├─ scenarios/              # gen_scenario 输出位置
├─ grid.db                 # 运行 build_db.py 后生成
└─ README.md
```

后续开发建议的目录(在 `大作业/` 下新增即可):

```
├─ main.py                 # 入口,起 3 个进程(计算 / 通信 / UI)
├─ core/
│   ├─ simulator.py        # 仿真主循环(每秒推进 sim_time)
│   ├─ wind_model.py       # 风机出力模型(读 §3 点表 + device_params)
│   ├─ dg_model.py         # 柴发出力模型
│   └─ power_balance.py    # 负荷 - 风机 = 柴发(夹上下限)
├─ comm/
│   ├─ tcp_server.py       # TCP Server(§5.6 模型)
│   ├─ protocol.py         # 报文编解码(§4.1 ~ §4.5)
│   └─ heartbeat.py        # 心跳与断线检测
├─ db/dao.py               # grid.db 表读写封装
└─ ui/app.py               # Tkinter 主页面
```

---

## 当前 P0 完成情况

| # | 任务 | 工具 | 状态 |
|---|---|---|---|
| 1 | 建库 | `tools/build_db.py` | ✅ 19 张表 + 全部点表种子 |
| 2 | 生成场景 CSV | `tools/gen_scenario.py` | ✅ 6 个验收场景 |
| 3 | 验证 db | `tools/verify_db.py` | ✅ 6 项 P0 验收清单全过 |
| 4 | 场景入 env_curve | `tools/load_scenario.py` | ✅ |
| 5 | 时钟骨架 | `main.py` | ✅ sim_history 已能落数据 |
| 6 | 抽查历史 | `tools/dump_history.py` | ✅ |

---

## 关于曲线没有现成数据

**直接用 `gen_scenario.py` 生成的模板**。6 个场景覆盖了验收要求的所有情况:

| 场景 | 验收看点 |
|---|---|
| 01 无风 | 风机判定停机,柴发承担全部负荷 |
| 02 适宜风速 | 风机按额定发 100kW,验证最大功率点 |
| 03 高风速 | 接近切出(22m/s),看保护逻辑 |
| 04 低负荷 | 柴发接近 P_min,看夹下限行为 |
| 05 高负荷 | 柴发夹上限,系统不平衡功率非零 |
| 06 渐变 | 0→350kW 渐变,综合验证上下限 + 切机 |

老师如要求"像真实气象"的湍流风速,后面加 `gen_realistic_wind.py`(Weibull 分布),P0 阶段不浪费时间。

---

## 后续阶段(独立开发节奏)

| 阶段 | 工时 | 关键产出 |
|---|---|---|
| **P0 骨架** ✅ | 0.5-1 天 | **db 已建 + 6 场景已生成 + verify 通过 + 时钟跑通** |
| P1 仿真内核 | ~2 天 | 风机模型 + 柴发模型 + 功率平衡(替换 main.py 中的占位) |
| P2 通信 | ~3 天 | TCP Server + 报文 + 心跳/断线 |
| P3 UI | ~2 天 | Tkinter 主页面 + 四表 + 曲线 + 历史 |
| P4 联调 | ~2 天 | 与 B/C 对账 + 演示彩排 |

每完成一个 P 就和 B/C 联调一次,避免堆到最后才总联调(历史教训)。

---

## 几个容易踩的坑

1. **WAL 模式忘开**:不开 WAL 的话通信进程和计算进程并发写 grid.db 会"database is locked"。`schema.sql` 已经开了,但你后面建新库要记得。
2. **仿真时刻与真实时刻分开**:`sim_time` 是从启动起累加的仿真秒,`real_time` 是 wall clock。混用会把曲线画成"垂直突变"。
3. **设备参数 vs 场景参数**:`device_params` 是"设备能力",`env_curve` 是"环境输入",不要把 `dg_p_max` 这种塞进 env_curve。
4. **不要现在做 UI**:P0 用控制台 + 打印看输出就够了,Tkinter 一旦开工会消耗 1-2 天,先把模型跑通。
5. **CSV 列名是 `wind_speed_m_s` 不是 `wind_speed`**:脚本里已经做了兼容,但你要写新代码读 CSV 时注意。

---

## 现在可以立刻做的下一步

按数字顺序:

1. **PyCharm Open 项目**,把工程整树熟悉一遍(15 分钟)
2. **在 PyCharm 里 Run 一遍 `main.py`**,看控制台每秒打印 + `dump_history.py` 看到数据(5 分钟)
3. **推送 `docs/interface.md` + `config/schema.sql` + `tools/` 给 B/C 评审**(10 分钟)
4. **群里发起 4 条待定项投票**(柴发参数最急)
5. P1 仿真内核:替换 main.py 里的占位模型,加 `core/wind_model.py` + `core/dg_model.py`
