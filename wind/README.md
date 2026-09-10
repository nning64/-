# wind/

> 学生 C · 南极考察站微电网 · **风电子站上位机 + STM32 固件**

本目录包含风电子站（C 模块）的全部代码：

| 子目录 | 角色 | 技术栈 |
|---|---|---|
| `scada/`   | 上位机 (HMI)：串口接收单片机遥测、写库、画曲线、人机交互 | Python 3.11, PyQt6, pyqtgraph, SQLite |
| `stm32_firmware/` | 单片机固件：遥测采集、可用功率计算、目标桨距角计算、与上位机串口通信 | C, STM32CubeIDE, HAL |

---

## scada/ 上位机

### 主要文件
- `wind_gui.py` — GUI 主程序（PyQt6，4 个 tab：实时曲线 / 加载历史 / 参数下发 / 通信日志）
- `wind_db.py` — SQLite 封装（`db/wind.db`，表 `history_control` / `device_params` / `control_log`）
- `wind_data_receiver.py` — 串口解析（点表映射）
- `Ui_wind_show.py` + `wind_show.ui` — Qt Designer 生成的 UI
- `send_config.py` — 参数下发给单片机（YK 风速上下限、YT 功率 / 桨距角设定）
- `curve_widget.py` / `curve_data.py` — 曲线渲染与状态缓存
- `demo.py` — pyqtgraph 信号槽示例（鼠标悬浮 crosshair）
- `temp_*.py` — 历史调试脚本，保留作参考
- `使用说明.md` — 上位机使用手册

### 怎么跑
```powershell
cd wind/scada
python wind_gui.py
```
**前置**：Python 3.10+，已装 `PyQt6` `pyqtgraph` `pyserial`。

### 数据模型
- **仿真秒**（不是 Unix 时间戳）：`history_control.timestamp` 来自 A 端 PUSH 的 `ts/1000`
- 核心字段：`timestamp / wind_speed / avail_power / output_power / pitch_angle / power_setpoint / run_status`
- GUI 运行时库被占用，外部脚本必须只读打开：
  ```python
  sqlite3.connect('file:wind.db?mode=ro', uri=True)
  ```

---

## stm32_firmware/ 单片机固件

### 工程入口
- `WindController.ioc` — STM32CubeMX 配置（STM32G431RBTX）
- `WindController Debug.launch` — CubeIDE 调试启动器
- `Core/Src/main.c` — 主循环（仿真时钟 / 协议解析 / 链路监督 / 串口上报）
- `Core/Src/wind_control.c` — **学生 C 模块 29/30** 实现：
  ```c
  avail = P_rated × min(1, v / v_rated)³   // 欠额定 v³, 额定封顶
  β    = max_pitch × (1 − set / avail)     // 桨距角反向联动设定值
  P    = avail × (1 − β / 30)             // 等价于 min(set, avail)
  ```
- `Core/Src/cJson.c` + `Core/Inc/cJSON.h` — JSON 编解码（第三方）
- `Drivers/` — **ST 官方 CMSIS / HAL 库**（CubeMX 自动生成，~70 个文件，可重新生成）

### 怎么编译
STM32CubeIDE 里 Open 这个文件夹 → Build（Ctrl+B）→ 用 ST-Link 烧录。

### 默认参数（设备参数表里可改）
`cut_in=3 m/s, rated_wind=12 m/s, cut_out=25 m/s, rated_power=100 kW, max_pitch=30°`

---

## 与其他模块的关系

| 接口 | 方向 | 协议 | 物理通道 |
|---|---|---|---|
| A (电网模拟器) → C | 接收 | TCP PUSH  (YC/YT) | 网线 |
| A (电网模拟器) → C | 接收 | TCP 心跳 / 设置响应 | 网线 |
| C → 单片机 | 发送 | 串口（cJSON 文本帧）| USB-TTL |
| C → 上位机 | 显示 | SQLite + pyqtgraph | — |

详细协议见仓库根 `docs/interface.md`（A 模块维护，三方共读）。

---

## 当前 P0 完成情况（C 端）

- ✅ 上位机：串口接收 / SQLite 持久化 / 4 tab GUI（实时 / 历史 / 参数 / 日志）/ 曲线渲染 + 鼠标 hover
- ✅ 单片机：仿真时钟 / 协议解析 / C 模块 29/30（avail + pitch）/ 串口回传
- ✅ 参数下发：上位机 → 单片机（含 YK 启停 / YT 功率 / YT 桨距角）
- ✅ 一致性：上位机改参数 → 下发 → 单片机生效

待办（验收前）：
- ❓ 单片机与 A 的心跳 / 断联恢复（当前偶发断联）
- ❓ C 模块 35「一致性验证」按钮（参数改完回读校验）
