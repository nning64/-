-- ============================================================
-- 微电网大作业 - 电网模拟器 grid.db schema (v0.2)
-- 与三方确认的"四遥点表"逐项对齐后的版本
--
-- 结构分 5 层:
--   ① 运行配置 : sim_params / env_curve / device_params / rtu
--   ② 点表定义 : rtu_yc_info / rtu_yx_info / rtu_yk_info / rtu_yt_info
--                  (静态字典: 点号-报文Key-名称-单位-类型-量程-死区-来源)
--   ③ 实时值   : yc_realtime / yx_realtime / yk_realtime / yt_realtime
--                  (动态值, 按 (rtu_id, point_no) 变位即更新)
--   ④ 历史     : yc_history / yx_history / yk_history / yt_history
--   ⑤ 仿真全景 : sim_history / log
--
-- 关键约定:
--   * 报文协议寻址 = RTU号 · 类别 · 点号, 与 ② 点表定义唯一对应。
--   * B/C 读协议时按 info 表把 点号 → 报文Key/单位/量程, 再与 ③ 实时值联查。
--   * 点号在每个 (RTU, 类别) 内从 1 独立编号, 不做全局连续。
-- ============================================================

PRAGMA journal_mode = WAL;       -- 并发读写必备 (通信进程+计算进程)
PRAGMA foreign_keys = ON;
PRAGMA synchronous = NORMAL;     -- 配合 WAL, 写入性能与安全平衡

-- ============================================================
-- ① 运行配置
-- ============================================================

-- 1. 仿真参数表 (key-value, 存运行期可变参数)
CREATE TABLE IF NOT EXISTS sim_params (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

INSERT OR IGNORE INTO sim_params(key, value) VALUES
    ('sim_time',           '0'), -- 当前仿真时刻(秒, 自启动起累加)
    ('sim_step_s',         '1'), -- 仿真步长
    ('sim_state',          '0'), -- 0=未启动 1=运行 2=暂停
    ('sim_start_real',     ''),  -- 真实启动时间
    ('control_cycle_s',    '5'), -- 调度决策周期 (EMS 主站)
    ('sample_cycle_s',     '1'), -- 数据采集周期
    ('scenario_file',      ''),  -- 当前加载的场景 CSV 路径
    ('ems_control_mode',   '0'), -- EMS 调度 开环=0 / 闭环=1
    ('wt_control_mode',    '1'); -- 风机控制 开环=0 / 闭环=1

-- 2. 环境曲线表 (风速/负荷, 按仿真时刻索引)
CREATE TABLE IF NOT EXISTS env_curve (
    t           INTEGER PRIMARY KEY,   -- 仿真秒
    wind_speed  REAL    NOT NULL,      -- m/s
    load_kw     REAL    NOT NULL       -- kW
);

-- 3. 设备参数表 (风机/柴发/电网的运行参数, (device,key) 联合主键)
CREATE TABLE IF NOT EXISTS device_params (
    device TEXT NOT NULL,              -- WT / DG / GRID
    key    TEXT NOT NULL,
    value  TEXT,
    unit   TEXT,
    remark TEXT,
    PRIMARY KEY (device, key)
);

INSERT OR IGNORE INTO device_params(device, key, value, unit, remark) VALUES
    -- 风机 (确认值)
    ('WT','cut_in_wind',    '3.0',  'm/s','切入风速'),
    ('WT','rated_wind',     '12.0', 'm/s','额定风速'),
    ('WT','cut_out_wind',   '25.0', 'm/s','切出风速'),
    ('WT','rated_power',    '100.0','kW', '额定功率'),
    -- 注: wt_control_mode 只存 sim_params 一处(运行期可切换), 此处不重复存
    -- 柴发 (v0.3 已定:额定 300kW,出力 30~300kW)
    ('DG','dg_rated',       '300.0', 'kW', '额定功率(系统总装机 ≈ WT100+DG300=400kW)'),
    ('DG','dg_p_max',       '300.0', 'kW', '出力上限(等于额定,允许满载)'),
    ('DG','dg_p_min',       '30.0',  'kW', '出力下限(10%额定,柴油机最低稳定连续运行点)'),
    ('DG','dg_ramp',        '0.0',   'kW/s','出力爬坡率(0=不限,瞬时跟踪EMS指令;联调期发现限功率会让unbalance偏大,默认改0)');

-- 4. RTU 注册表 (B/C 客户端连接时由 A 维护在线状态)
CREATE TABLE IF NOT EXISTS rtu (
    rtu_id         TEXT PRIMARY KEY,   -- R01=WT  R02=DG  R03=GRID
    name           TEXT NOT NULL,
    device_type    TEXT NOT NULL,      -- WT / DG / GRID
    client_ip      TEXT,
    client_port    INTEGER,
    online         INTEGER DEFAULT 0,  -- 0/1
    last_heartbeat TEXT
);

INSERT OR IGNORE INTO rtu(rtu_id, name, device_type) VALUES
    ('R01','风电机组',   'WT'),
    ('R02','柴油发电机组','DG'),
    ('R03','电网与负荷', 'GRID');

-- ============================================================
-- ② 点表定义 (静态字典, 与报文协议一一对应)
-- ============================================================

-- 5. 遥测点表 (YC, 模拟量)
CREATE TABLE IF NOT EXISTS rtu_yc_info (
    rtu_id     TEXT NOT NULL,
    point_no   INTEGER NOT NULL,
    code       TEXT NOT NULL,          -- 报文Key
    name       TEXT NOT NULL,          -- 显示名称
    unit       TEXT,
    data_type  TEXT NOT NULL,          -- REAL / INTEGER
    min_val    REAL,                   -- 量程下限 (确认后在 DB 中生效)
    max_val    REAL,                   -- 量程上限
    deadband   REAL,                   -- 变位死区 (越过才变位上报)
    source     TEXT,                   -- 数据来源
    PRIMARY KEY (rtu_id, point_no)
);

INSERT OR IGNORE INTO rtu_yc_info(rtu_id,point_no,code,name,unit,data_type,min_val,max_val,deadband,source) VALUES
    -- R01 风电机组 (确认值)
    ('R01',1,'W_SPD',       '风速',         'm/s','REAL',  0,    40,   0.1, '电网模拟器'),
    ('R01',2,'WT_ACT',      '风机有功出力', 'kW', 'REAL',  0,    100,  0.5, '电网模拟器算'),
    ('R01',3,'WT_PITCH',    '当前桨距角',   'deg','REAL',  0,    90,   0.5, 'A(桨距循C YT2执行,立即到位)'),
    ('R01',4,'WT_AVAIL',    '可用功率',     'kW', 'REAL',  0,    100,  1.0, '电网模拟器算(风功率曲线)'),
    ('R01',5,'WT_P_SET_ACK','功率设定值(回显)','kW','REAL',0,    100,  0.5, 'EMS遥调回显'),
    -- R02 柴油发电机组 (量程按 dg_rated=300kW 设定)
    ('R02',1,'DG_ACT',      '柴发出力',     'kW', 'REAL',  0,    300,  0.5, '电网模拟器算'),
    ('R02',2,'DG_P_SET_ACK','柴发功率设定回显','kW','REAL',  0,    300,  0.5, 'EMS遥调回显'),
    ('R02',3,'DG_LOAD',     '负荷率',       '%',  'REAL',  0,    100,  1.0, '电网模拟器算'),
    -- R03 电网与负荷 (负荷上限按场景最大值 350kW; UNBAL 允许负值; GRID_FREQ 50Hz 工频附近)
    ('R03',1,'LD_ACT',      '负荷用电功率', 'kW', 'REAL',  0,    350, 1.0, '电网模拟器'),
    ('R03',2,'UNBAL',       '系统不平衡功率','kW','REAL',  -50,  50,  1.0, '电网模拟器算(=负荷-风机-柴发)'),
    ('R03',3,'GRID_FREQ',   '电网频率(预留)','Hz','REAL',  49.5, 50.5,0.05,'电网模拟器');

-- 6. 遥信点表 (YX, 状态量)
CREATE TABLE IF NOT EXISTS rtu_yx_info (
    rtu_id     TEXT NOT NULL,
    point_no   INTEGER NOT NULL,
    code       TEXT NOT NULL,
    name       TEXT NOT NULL,
    unit       TEXT,
    data_type  TEXT NOT NULL,
    value_desc TEXT,                   -- 取值说明
    PRIMARY KEY (rtu_id, point_no)
);

INSERT OR IGNORE INTO rtu_yx_info(rtu_id,point_no,code,name,unit,data_type,value_desc) VALUES
    ('R01',1,'WT_RUN',  '风机运行状态',      '-','INTEGER','0=停机,1=运行'),
    ('R01',2,'WT_FAULT','风机故障标志',      '-','INTEGER','0=正常,1=故障'),
    ('R01',3,'WIFI_STA','通信状态',          '-','INTEGER','0=断开,1=连接'),
    ('R02',1,'DG_RUN',  '柴发运行状态',      '-','INTEGER','0=停机,1=运行'),
    ('R02',2,'DG_ALARM','柴发告警',          '-','INTEGER','0=正常,1=告警'),
    ('R03',1,'SIM_RUN', '仿真运行状态',      '-','INTEGER','0=停,1=运,2=暂停'),
    ('R03',2,'COMM_EMS','与EMS连接状态',     '-','INTEGER','0=断开,1=连接'),
    ('R03',3,'COMM_WT', '与风机控制器连接状态','-','INTEGER','0=断开,1=连接');

-- 7. 遥控点表 (YK, 开关指令)
CREATE TABLE IF NOT EXISTS rtu_yk_info (
    rtu_id     TEXT NOT NULL,
    point_no   INTEGER NOT NULL,
    code       TEXT NOT NULL,
    name       TEXT NOT NULL,
    unit       TEXT,
    data_type  TEXT NOT NULL,
    value_desc TEXT,
    PRIMARY KEY (rtu_id, point_no)
);

INSERT OR IGNORE INTO rtu_yk_info(rtu_id,point_no,code,name,unit,data_type,value_desc) VALUES
    ('R01',1,'WT_START','风机启停指令','-','INTEGER','0=停机,1=启动'),
    ('R02',1,'DG_START','柴发启停指令','-','INTEGER','0=停机,1=启动');
    -- R03 暂空 (0 点): 电网侧不接收控制

-- 8. 遥调点表 (YT, 设定值)
CREATE TABLE IF NOT EXISTS rtu_yt_info (
    rtu_id     TEXT NOT NULL,
    point_no   INTEGER NOT NULL,
    code       TEXT NOT NULL,
    name       TEXT NOT NULL,
    unit       TEXT,
    data_type  TEXT NOT NULL,
    range_desc TEXT,                   -- 取值范围说明
    source     TEXT,                   -- 来源/流向
    PRIMARY KEY (rtu_id, point_no)
);

INSERT OR IGNORE INTO rtu_yt_info(rtu_id,point_no,code,name,unit,data_type,range_desc,source) VALUES
    ('R01',1,'WT_P_SET',    '功率设定值',     'kW','REAL','0~额定功率(如100)','EMS→A→风机'),
    ('R01',2,'WT_PITCH_SET','桨距角设定值',   'deg','REAL','0~最大桨距角(如30)','风机控制器→A'),
    ('R02',1,'DG_P_SET_CMD','柴发功率设定指令','kW','REAL','0~dg_rated','EMS→A');

-- ============================================================
-- ③ 实时值 (动态, 变位即更新; B/C 定时拉取)
-- ============================================================

-- 9. 遥测实时 (YC)
CREATE TABLE IF NOT EXISTS yc_realtime (
    rtu_id     TEXT NOT NULL,
    point_no   INTEGER NOT NULL,
    value      REAL NOT NULL,
    quality    INTEGER DEFAULT 0,     -- 0=好 1=坏
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    PRIMARY KEY (rtu_id, point_no)
);

-- 10. 遥信实时 (YX)
CREATE TABLE IF NOT EXISTS yx_realtime (
    rtu_id     TEXT NOT NULL,
    point_no   INTEGER NOT NULL,
    value      INTEGER NOT NULL,      -- 0 / 1 / 2 (SIM_RUN 可 2)
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    PRIMARY KEY (rtu_id, point_no)
);

-- 11. 遥控实时 (YK, 指令队列) -- B/C 写入, A 进程读取后执行
CREATE TABLE IF NOT EXISTS yk_realtime (
    rtu_id     TEXT NOT NULL,
    point_no   INTEGER NOT NULL,
    cmd        INTEGER NOT NULL,      -- 目标值 0/1
    src        TEXT NOT NULL,         -- EMS / WT_CTRL
    state      INTEGER DEFAULT 0,     -- 0=待执行 1=已执行 2=失败
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    executed_at TEXT,
    PRIMARY KEY (rtu_id, point_no)
);

-- 12. 遥调实时 (YT, 设定值) -- 同上
CREATE TABLE IF NOT EXISTS yt_realtime (
    rtu_id     TEXT NOT NULL,
    point_no   INTEGER NOT NULL,
    value      REAL NOT NULL,
    src        TEXT NOT NULL,         -- EMS / WT_CTRL
    state      INTEGER DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    executed_at TEXT,
    PRIMARY KEY (rtu_id, point_no)
);

-- ============================================================
-- ④ 历史 (验收展示/离线分析, 变位或每秒落一条)
-- ============================================================

-- 13. 遥测历史
CREATE TABLE IF NOT EXISTS yc_history (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    rtu_id    TEXT NOT NULL,
    point_no  INTEGER NOT NULL,
    value     REAL NOT NULL,
    sim_time  INTEGER NOT NULL,        -- 仿真时刻
    real_time TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_yc_hist ON yc_history(rtu_id, point_no, sim_time);

-- 14. 遥信历史
CREATE TABLE IF NOT EXISTS yx_history (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    rtu_id    TEXT NOT NULL,
    point_no  INTEGER NOT NULL,
    value     INTEGER NOT NULL,
    sim_time  INTEGER NOT NULL,
    real_time TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_yx_hist ON yx_history(rtu_id, point_no, sim_time);

-- 15. 遥控历史
CREATE TABLE IF NOT EXISTS yk_history (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    rtu_id    TEXT NOT NULL,
    point_no  INTEGER NOT NULL,
    cmd       INTEGER NOT NULL,
    src       TEXT NOT NULL,
    sim_time  INTEGER NOT NULL,
    real_time TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- 16. 遥调历史
CREATE TABLE IF NOT EXISTS yt_history (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    rtu_id    TEXT NOT NULL,
    point_no  INTEGER NOT NULL,
    value     REAL NOT NULL,
    src       TEXT NOT NULL,
    sim_time  INTEGER NOT NULL,
    real_time TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- ============================================================
-- ⑤ 仿真全景与日志
-- ============================================================

-- 17. 仿真历史 (每秒一条全景快照, 验收"看图说话"主数据源)
CREATE TABLE IF NOT EXISTS sim_history (
    sim_time      INTEGER PRIMARY KEY,
    wind_speed    REAL,
    load_kw       REAL,
    wt_act_kw     REAL,
    dg_act_kw     REAL,
    wt_pitch_deg  REAL,
    wt_run        INTEGER,
    dg_run        INTEGER,
    unbalance_kw  REAL,
    real_time     TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- 18. 系统日志
CREATE TABLE IF NOT EXISTS log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    level      TEXT NOT NULL,         -- INFO / WARN / ERROR
    src        TEXT NOT NULL,         -- 进程/模块名
    msg        TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_log_time ON log(created_at DESC);
