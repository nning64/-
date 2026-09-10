# EMS SCADA监控系统

## 项目简介

本项目为能源管理系统（EMS，Energy Management System）原型系统，实现了对风机、柴油发电机以及负荷系统的数据采集、实时监控、调度控制等功能。

系统采用 Python 开发，使用 Qt6 实现图形化监控界面，通过 TCP + JSON 数据通信方式与仿真端进行数据交互，实现能源系统运行状态监测与控制。


## 主要功能

### 1. 实时监控

实现能源系统运行状态实时显示，包括：

- 风机运行数据
- 柴油发电机运行数据
- 负荷数据
- 电网频率
- 功率不平衡状态


### 2. 遥测（YC）

支持实时采集设备运行数据：

- 风机速度
- 风机实际功率
- 风机可用功率
- 柴油机实际功率
- 柴油机负荷率
- 负荷功率
- 电网频率


### 3. 遥信（YX）

支持设备状态监测：

- 风机运行状态
- 风机故障状态
- 风机通信状态
- 柴油机运行状态
- 柴油机报警状态
- EMS通信状态
- 系统运行状态


### 4. 遥控（YK）

支持设备控制：

- 风机启动控制
- 柴油机启动控制
- 设备运行控制

控制指令通过通信模块发送至仿真端。


### 5. 遥调（YT）

支持设备参数调整：

- 风机功率设定
- 风机桨距角设定
- 柴油机功率设定

EMS根据当前负荷需求和设备运行状态计算调度结果，并生成遥调指令。


### 6. EMS调度控制

实现自动调度功能：

- 根据系统负荷需求进行功率分配
- 优先利用风电资源
- 根据柴油机运行约束进行补偿
- 保持系统功率平衡


### 7. 历史曲线

支持历史运行数据查看：

- 风机功率变化曲线
- 柴油机功率变化曲线
- 负荷变化曲线


### 8. 数据库管理

使用 SQLAlchemy 实现数据库管理。

保存数据包括：

- YC遥测数据
- YX遥信数据
- YK遥控记录
- YT遥调记录
- 系统参数信息


## 项目结构


EMS

├── main.py                 主程序及Qt界面入口

├── main.ui                 Qt界面文件

├── operator_core.py        EMS调度核心算法

├── operator_io.py           通信模块

├── models.py               数据库模型

├── database.py             数据库配置

├── point_mapping.py        点表映射

├── curve_plot_widgets/     曲线显示组件

├── mock_data.py            模拟数据生成

├── init_db.py              数据库初始化

├── evaluation.py            运行评价模块

├── fix_dg_min.py            柴油机约束处理

├── set_closed_loop.py       闭环控制

├── check_xxx.py             功能检测脚本

├── test_xxx.py              测试程序

└── README.md


## 环境要求

Python版本：

Python 3.10及以上


主要依赖：

PyQt6

SQLAlchemy


安装依赖：

pip install PyQt6 SQLAlchemy


## 数据库初始化

第一次运行前执行：

python init_db.py


系统会自动创建数据库和相关数据表。


## 系统运行

启动EMS监控系统：

python main.py


## 通信说明

通信方式：

TCP Socket


数据格式：

JSON Lines


主要通信内容：

REG        注册

PULL_ALL   全量数据请求

RESP_ALL   全量数据响应

RESP_DELTA 增量数据响应

YK         遥控指令

YT         遥调指令


通信结构：

EMS <----TCP----> 仿真端


通信地址：

IP：
10.18.134.231


Port：
9000


## 测试功能

提供以下测试程序：

python check_yt.py

python check_yk.py

python check_dg.py

python check_history.py


用于测试：

- 遥调功能
- 遥控功能
- 柴油机控制
- 历史数据


## 开发环境

开发工具：

PyCharm

Qt Designer

Git / GitHub


## 项目说明

本系统完成了能源管理系统EMS基本功能，实现了数据采集、设备状态监测、调度计算、控制指令发送以及图形化显示，为微电网能源管理系统提供了基础框架。