"""comm - A(电网模拟器)的 TCP 通讯层。

实现 interface.md v0.3 的 JSON Lines 协议:
A 监听 0.0.0.0:9000, 同时接两条连接:
  - B (EMS 主站)     -> role=EMS
  - C (风机控制器)   -> role=WT_CTRL
"""
