# 电网模拟器 grid_simulator

> 微电网大作业 · A 板块(电网模拟器 / 数据源 / 协议中枢)
> **当前状态:全部完成(建库 + 场景 + 仿真内核 + TCP 总机 + PySide6 界面+联调)。**

---

## ▶ 立刻运行(一条命令全包)

在 PowerShell 里**进项目根目录**,然后:

```powershell
cd "C:\Users\lenovo\WPSDrive\1743162206\WPS企业云盘\清华大学\我的企业文档\python代码\Qt\大作业"

# ① 建库(从 config/schema.sql 生成 grid.db,19 张表 + 全部点表种子)
python tools/build_db.py

# ② 生成 6 个验收场景 CSV(全部 ramp 渐变,非常量,写完自检)
python tools/gen_scenario.py

# ③ 验证数据库结构 + P0 验收清单
python tools/verify_db.py

# ④ 加载默认场景到 env_curve(不指定就走"适宜风速")
python tools/load_scenario.py

# ⑤ 起仿真 + TCP 通讯总机(9000 端口,同一进程,PyCharm 右键 Run main.py 效果相同)
python main.py

# ⑥ 另开一个终端起监视界面(边跑边看)
python ui/app.py
```

**期望看到:**
- `verify_db.py` 末尾 6 行 `[OK]`
- `main.py` 每 5 行打印一行 `t=Ns wind=Xm/s load=YkW ...`,EMS / 风机控制器(B/C 端)可拨入 9000 端口
- `ui/app.py` 弹出主窗口:曲线面板异步加载(<0.2s 出窗),每秒刷新实时表
- 想整体体检 P1 成果:`python tools/verify_p1.py` → 末行 `6/6 全部 PASS`

---

## ▶ PyCharm 里怎么 Run(推荐)

1. **Open** 整个 `大作业` 文件夹(File → Open)
2. 依次右键 Run:`tools/build_db.py` → `tools/gen_scenario.py` → `tools/verify_db.py` → `tools/load_scenario.py` → `main.py` → `ui/app.py`
3. main.py 常用参数(Run → Edit Configurations → Parameters):
   - `--scenario scenarios/02_适宜风速_风机满发.csv` 指定场景
   - `--speed 10` 10 倍速;`--until 120` 跑到 t=120s 自动停
   - `--no-comm` 退回纯仿真不开总机;`--port 9001` 换端口
   - **默认已开通讯总机**,要关才需要 `--no-comm`
4. 看 db:PyCharm Professional 右键 `grid.db` → Open Database;Community 用 `tools/dump_history.py`

---

## 当前目录(实际)

```
大作业/
├─ main.py                 # 仿真时钟 + 通讯总机 同进程主入口
├─ config/schema.sql       # grid.db 的 schema(WAL + 19 张表 + 点表种子)
├─ docs/interface.md       # 三方接口文档(点表 + TCP 帧 + 交互流程)
├─ core/
│   ├─ wind_model.py       # 风机出力模型(桨距 η=1−β/30,A 自演 / C 接管两模式)
│   ├─ dg_model.py         # 柴发模型(ramp 速率可热更新)
│   └─ syslog.py           # 关键事件写 log 表(UI 运行日志面板的数据源)
├─ comm/server.py          # TCP 总机(9000):REG/ACK/HEART/PUSH,15s 无报文踢线
├─ ui/
│   ├─ app.py              # PySide6 主窗口(QDockWidget 多面板,matplotlib 延迟加载)
│   ├─ db.py / theme.py    # 只读取数封装 / QSS 主题
│   └─ widgets/            # sim_ctrl 仿真控制台 / scada_table 实时表 /
│                          # hist_view 历史 / log_view 日志 / dev_params 设备参数 /
│                          # curve_view 曲线(可拖拽改环境)
├─ tools/
│   ├─ build_db.py         # 一键建库/重建
│   ├─ gen_scenario.py     # 6 场景生成(全 ramp,自检非常量)
│   ├─ load_scenario.py    # 场景 CSV 灌 env_curve
│   ├─ verify_db.py        # db 结构 + P0 验收清单
│   ├─ verify_p1.py        # P1 整体体检(6 场景逐个跑+查账,6/6 PASS)
│   ├─ verify_r02_2.py     # R02:2 柴发指令回显专项验证(含 dg_sp<0 钳 30)
│   ├─ verify_dg_ramp.py   # 柴发爬坡速率验证
│   ├─ verify_pitch_instant.py  # 桨距角即时生效验证
│   ├─ diagnose_ems_conn.py     # EMS 连不上时的诊断
│   └─ dump_history.py     # 抽查 sim_history
├─ scenarios/              # 6 个场景 CSV(gen_scenario 输出)
├─ grid.db                 # 运行 build_db.py 后生成
└─ README.md
```

---

## 完成情况

| 阶段 | 内容 | 状态                       |
|---|---|----------------------------|
| P0 骨架 | 建库 + 6 场景 + verify 通过 + 时钟跑通 | ✅                         |
| P1 仿真内核 | wind_model / dg_model / 功率平衡 / sim_history 落数据 | ✅ `verify_p1.py` 6/6 PASS |
| P2 通信 | TCP 总机(REG/ACK/HEART/PUSH,断线检测,15s 踢线) | ✅ 默认随 main.py 启动     |
| P3 UI | PySide6 主窗口 + 六面板 + 曲线拖拽改环境 | ✅ `ui/app.py`             |
| P4 联调 | 与 B(EMS)/ C(风机控制器)对账 + 演示彩排 | ✅️                         |

---

## 三方联调要点(A 端视角)

- **总机地址**:`0.0.0.0:9000`(B/C 用 TCP 连进来,协议见 `docs/interface.md`)
- **推送节奏**:HEART 每 5s 广播 sim_time;PUSH 每 1s 只推 R01:1 W_SPD + R01 YT1 WT_P_SET
- **假死/真停机**:任意字节到达即刷新活跃时间;**15s 无任何报文**才断线 —— 期间数据保持旧值,可区分"断线"与"停机"
- **通信三灯**:R01:3 / R03:2 / R03:3 由总机维护,连接状态自动反映到遥信表

### 关键业务规则(改代码前必读)

1. **R02:2 DG_P_SET_ACK**:EMS 下发过指令就回显指令值;否则回显 `dg_sp = load_kw − wt_act`,且 **dg_sp < 0 时钳到柴发下限 30kW(柴发不能关)** —— 见 `main.py` 与 `tools/verify_r02_2.py`
2. **R03:1 SIM_RUN** 跟随仿真状态(0/1/2),不再硬编码 1
3. **桨距算法与 C 端等价**:A 端 `η = 1 − β/30` 与 C 端反解 `β = max_pitch·(1 − set/avail)` 数学等价(前提 max_pitch=30、avail 公式一致);停机时 A 报 90°、C 报 30° 是设计差异,出力都是 0
4. **C 接管桨距**:`pitch_set` 有值走 C 端模式(A 不钳位),5s 无更新(C_PITCH_HOLD_S)A 收回自演
5. **场景参数**:LOAD_HIGH=380 / DG_P_MIN=30,柴发爬坡速率在 `device_params.dg_ramp`,支持界面热更新

---

## UI 操作速查

- **改环境曲线(拖拽)**:曲线面板上 **左键拖** = 改风速(0~30),**右键拖** = 改负荷(0~400),直接改写 env_curve 表
  - 只对**未到来的仿真时刻**生效;拖已过去的时间或暂停状态(仿真控制台按暂停)无效
  - 松手后**看底部状态栏**:绿字=写入成功,红字=原因(超范围 / 改历史 / 暂停中)
- **面板都是 QDockWidget**:顶部"窗口"菜单开关,可拖动/浮动/停靠
- **启动不卡**:matplotlib 是异步延迟加载,主窗口 <0.2s 出现,曲线面板稍后自动补上,属正常现象

---

## 场景清单

| 场景 | 验收看点 |
|---|---|
| 01 无风 | 风机判定停机,柴发承担全部负荷 |
| 02 适宜风速 | 风机按额定发 100kW,验证最大功率点 |
| 03 高风速 | 接近切出(22m/s),看保护逻辑 |
| 04 低负荷 | 柴发接近 P_min(30kW),看夹下限行为 |
| 05 高负荷 | 负荷 380kW,柴发夹上限,系统不平衡功率非零 |
| 06 渐变 | 负荷渐变综合验证上下限 + 切机 |

---

## 几个容易踩的坑

1. **WAL 已开**,通信线程和仿真主循环并发写 grid.db 不会锁;自建新库时 schema 已带,别去掉
2. **sim_time 与 real_time 分开**:曲线画的是仿真秒,混用会画成垂直突变
3. **CSV 列名是 `wind_speed_m_s`** 不是 `wind_speed`(脚本已兼容,写新读代码注意)
4. **改完代码跑一遍 `tools/verify_p1.py`**,6/6 PASS 才算没弄坏
5. **中文路径下不要用 `python -c "..."`**,嵌套引号必炸,写成 .py 文件再跑

---

