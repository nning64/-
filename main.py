import sys
from curve_plot_widgets import (
    HistoryPlot,
    EditableCurvePlot
)
from curve_plot_widgets.editable_curve_plot import EditableCurvePlot

from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QPushButton,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QLineEdit
)


from PyQt6.QtCore import QTimer


from database import SessionLocal


from models import (
    YCInfo,
    YKInfo,
    YTInfo,
    DeviceParam,
    DispatchHistory,
    SystemLog,
    Evaluation
)
from evaluation import get_evaluation

from curve_plot_widgets import (
    HistoryPlot,
    EditableCurvePlot
)

from models import (
    YCInfo,
    YXInfo,
    YKInfo,
    YTInfo,
    DeviceParam,
    DispatchHistory,
    SystemLog
)

class MainWindow(QMainWindow):

    def __init__(self):

        super().__init__()


        self.setWindowTitle(
            "EMS SCADA监控系统"
        )


        self.resize(
            1200,
            800
        )


        self.tabs = QTabWidget()


        self.setCentralWidget(
            self.tabs
        )


        # 新增
        self.init_monitor()


        # 原四遥
        self.init_yc()

        self.init_yx()

        self.init_yk()

        self.init_yt()
        self.init_device_param()

        self.init_evaluation()

        # 新增
        self.init_history()

        self.init_dispatch()
        self.init_evaluation()


        self.timer = QTimer()


        self.timer.timeout.connect(
            self.refresh_data
        )


        self.timer.timeout.connect(
            self.refresh_curve
        )


        self.timer.start(
            1000
        )



    # =========================
    # 实时监控曲线
    # =========================

    def init_monitor(self):


        page = QWidget()


        layout = QVBoxLayout()



        self.real_plot = HistoryPlot()



        layout.addWidget(
            self.real_plot
        )



        page.setLayout(
            layout
        )


        self.tabs.addTab(
            page,
            "实时监控"
        )



    # =========================
    # YC
    # =========================

    def init_yc(self):

        page = QWidget()


        layout = QVBoxLayout()



        self.yc_table = QTableWidget()


        self.yc_table.setColumnCount(
            5
        )


        self.yc_table.setHorizontalHeaderLabels(
            [
                "编号",
                "名称",
                "数值",
                "单位",
                "时间"
            ]
        )


        layout.addWidget(
            self.yc_table
        )


        page.setLayout(
            layout
        )


        self.tabs.addTab(
            page,
            "遥测YC"
        )



    # =========================
    # YX
    # =========================

    def init_yx(self):


        page = QWidget()


        layout = QVBoxLayout()


        self.yx_table = QTableWidget()


        self.yx_table.setColumnCount(
            4
        )


        self.yx_table.setHorizontalHeaderLabels(
            [
                "编号",
                "名称",
                "状态",
                "时间"
            ]
        )


        layout.addWidget(
            self.yx_table
        )


        page.setLayout(
            layout
        )


        self.tabs.addTab(
            page,
            "遥信YX"
        )



    # =========================
    # YK
    # =========================

    def init_yk(self):


        page = QWidget()


        layout = QVBoxLayout()



        label = QLabel(
            "设备控制"
        )


        layout.addWidget(
            label
        )



        btn1 = QPushButton(
            "柴发启动"
        )


        btn1.clicked.connect(
            lambda:
            self.add_yk(
                "DG_START",
                1
            )
        )



        btn2 = QPushButton(
            "柴发停止"
        )


        btn2.clicked.connect(
            lambda:
            self.add_yk(
                "DG_STOP",
                0
            )
        )



        btn3 = QPushButton(
            "风机启动"
        )


        btn3.clicked.connect(
            lambda:
            self.add_yk(
                "WT_START",
                1
            )
        )



        btn4 = QPushButton(
            "风机停止"
        )


        btn4.clicked.connect(
            lambda:
            self.add_yk(
                "WT_STOP",
                0
            )
        )



        layout.addWidget(btn1)

        layout.addWidget(btn2)

        layout.addWidget(btn3)

        layout.addWidget(btn4)



        self.yk_table = QTableWidget()


        self.yk_table.setColumnCount(
            4
        )


        self.yk_table.setHorizontalHeaderLabels(
            [
                "命令",
                "名称",
                "值",
                "状态"
            ]
        )


        layout.addWidget(
            self.yk_table
        )



        page.setLayout(
            layout
        )


        self.tabs.addTab(
            page,
            "遥控YK"
        )



    # =========================
    # YT
    # =========================

    def init_yt(self):


        page = QWidget()


        layout = QVBoxLayout()



        self.yt_value = QLineEdit()


        self.yt_value.setPlaceholderText(
            "输入柴油机功率"
        )



        btn = QPushButton(
            "设置柴发功率"
        )


        btn.clicked.connect(
            self.send_yt
        )



        layout.addWidget(
            self.yt_value
        )


        layout.addWidget(
            btn
        )



        page.setLayout(
            layout
        )


        self.tabs.addTab(
            page,
            "遥调YT"
        )



    # =========================
    # 历史曲线
    # =========================

    def init_history(self):


        page = QWidget()


        layout = QVBoxLayout()


        self.history_plot = HistoryPlot()



        self.history_plot.set_curves(
            {
                "风电功率":[20,30,40,50],
                "柴发功率":[100,120,130,150],
                "负荷功率":[150,160,170,180]
            }
        )



        layout.addWidget(
            self.history_plot
        )



        page.setLayout(
            layout
        )


        self.tabs.addTab(
            page,
            "历史曲线"
        )



    # =========================
    # 调度计划
    # =========================

    def init_dispatch(self):


        page = QWidget()


        layout = QVBoxLayout()


        self.dispatch_plot = EditableCurvePlot()



        self.dispatch_plot.set_curves(
            {
                "风电计划":[20,30,40,50],
                "柴发计划":[100,120,130,150]
            }
        )



        self.dispatch_plot.curvePointChanged.connect(
            self.change_dispatch
        )



        layout.addWidget(
            self.dispatch_plot
        )



        page.setLayout(
            layout
        )


        self.tabs.addTab(
            page,
            "调度计划"
        )



    # =========================
    # 修改调度
    # =========================

    def change_dispatch(
            self,
            name,
            index,
            value
    ):


        print(
            "修改调度:",
            name,
            index,
            value
        )



    # =========================
    # YK写数据库
    # =========================

    def add_yk(
            self,
            code,
            value
    ):


        session = SessionLocal()



        item = session.query(
            YKInfo
        ).filter(
            YKInfo.code == code
        ).first()



        if item:

            item.value=value

            item.state="NEW"



        session.commit()


        session.close()



    # =========================
    # YT写数据库
    # =========================

    def send_yt(self):


        value=float(
            self.yt_value.text()
        )


        session=SessionLocal()



        item=session.query(
            YTInfo
        ).filter(
            YTInfo.code=="DG_POWER"
        ).first()



        if item:

            item.value=value

            item.state="NEW"



        session.commit()


        session.close()



    # =========================
    # 刷新数据库
    # =========================

    def refresh_data(self):


        session=SessionLocal()



        yc=session.query(
            YCInfo
        ).all()



        self.yc_table.setRowCount(
            len(yc)
        )



        for i,x in enumerate(yc):


            self.yc_table.setItem(
                i,
                0,
                QTableWidgetItem(
                    x.code
                )
            )


            self.yc_table.setItem(
                i,
                1,
                QTableWidgetItem(
                    x.name
                )
            )


            self.yc_table.setItem(
                i,
                2,
                QTableWidgetItem(
                    str(x.value)
                )
            )


            self.yc_table.setItem(
                i,
                3,
                QTableWidgetItem(
                    x.unit or ""
                )
            )


            self.yc_table.setItem(
                i,
                4,
                QTableWidgetItem(
                    str(x.refresh_time)
                )
            )



        session.close()



    # =========================
    # 刷新曲线
    # =========================

    def refresh_curve(self):


        session=SessionLocal()



        yc=session.query(
            YCInfo
        ).all()



        wind=[]

        dg=[]

        load=[]



        for x in yc:

            if "风" in x.name:

                wind.append(
                    x.value
                )

            elif "柴" in x.name:

                dg.append(
                    x.value
                )

            elif "负荷" in x.name:

                load.append(
                    x.value
                )



        session.close()



        if wind or dg or load:


            self.real_plot.set_curves(
                {
                    "风电功率":wind,
                    "柴发功率":dg,
                    "负荷功率":load

                }
            )

    def init_device_param(self):

        page = QWidget()

        layout = QVBoxLayout()

        self.dg_min = QLineEdit()
        self.dg_max = QLineEdit()

        self.dg_min.setPlaceholderText(
            "柴发最小功率"
        )

        self.dg_max.setPlaceholderText(
            "柴发最大功率"
        )

        btn = QPushButton(
            "保存柴发上下限"
        )

        btn.clicked.connect(
            self.save_dg_limit
        )

        layout.addWidget(
            QLabel("柴油机参数设置")
        )

        layout.addWidget(
            self.dg_min
        )

        layout.addWidget(
            self.dg_max
        )

        layout.addWidget(
            btn
        )

        self.device_table = QTableWidget()

        self.device_table.setColumnCount(3)

        self.device_table.setHorizontalHeaderLabels(
            [
                "设备",
                "参数",
                "数值"
            ]
        )

        layout.addWidget(
            self.device_table
        )

        page.setLayout(layout)

        self.tabs.addTab(
            page,
            "设备参数"
        )

    def save_dg_limit(self):

        min_text = self.dg_min.text().strip()
        max_text = self.dg_max.text().strip()

        if min_text == "" or max_text == "":
            print("请输入柴发最小功率和最大功率")
            return

        try:
            dg_min = float(min_text)
            dg_max = float(max_text)
        except ValueError:
            print("柴发上下限必须是数字")
            return

        if dg_min < 0 or dg_max < 0:
            print("柴发上下限不能小于0")
            return

        if dg_min >= dg_max:
            print("柴发最小功率必须小于最大功率")
            return

        session = SessionLocal()

        try:

            min_item = session.query(
                DeviceParam
            ).filter(
                DeviceParam.key == "DG_P_MIN"
            ).first()

            max_item = session.query(
                DeviceParam
            ).filter(
                DeviceParam.key == "DG_P_MAX"
            ).first()

            if min_item is None:

                min_item = DeviceParam(
                    device="DG",
                    key="DG_P_MIN",
                    value=dg_min,
                    unit="kW",
                    source="EMS"
                )

                session.add(min_item)

            else:

                min_item.value = dg_min

            if max_item is None:

                max_item = DeviceParam(
                    device="DG",
                    key="DG_P_MAX",
                    value=dg_max,
                    unit="kW",
                    source="EMS"
                )

                session.add(max_item)

            else:

                max_item.value = dg_max

            session.commit()

            print(
                f"柴发上下限修改完成："
                f"最小={dg_min:.2f} kW，"
                f"最大={dg_max:.2f} kW"
            )

            self.refresh_device_param()

        except Exception as e:

            session.rollback()

            print(
                "柴发上下限修改失败：",
                e
            )

        finally:

            session.close()
    def init_evaluation(self):

        page = QWidget()

        layout = QVBoxLayout()

        self.score_table = QTableWidget()

        self.score_table.setColumnCount(4)

        self.score_table.setHorizontalHeaderLabels(
            [
                "对象",
                "评价指标",
                "得分",
                "说明"
            ]
        )

        result = get_evaluation()

        data = []

        for item in result:
            data.append([
                item["对象"],
                item["评价指标"],
                item["得分"],
                item["说明"]
            ])

        self.score_table.setRowCount(
            len(data)
        )

        for i, row in enumerate(data):

            for j, value in enumerate(row):
                self.score_table.setItem(
                    i,
                    j,
                    QTableWidgetItem(
                        str(value)
                    )
                )

        layout.addWidget(
            self.score_table
        )

        page.setLayout(
            layout
        )

        self.tabs.addTab(
            page,
            "运行评价"
        )

    def refresh_device_param(self):

        session = SessionLocal()

        try:

            data = session.query(
                DeviceParam
            ).filter(
                DeviceParam.device == "DG"
            ).all()

            self.device_table.setRowCount(
                len(data)
            )

            for i, item in enumerate(data):
                self.device_table.setItem(
                    i,
                    0,
                    QTableWidgetItem(
                        item.device
                    )
                )

                self.device_table.setItem(
                    i,
                    1,
                    QTableWidgetItem(
                        item.key
                    )
                )

                self.device_table.setItem(
                    i,
                    2,
                    QTableWidgetItem(
                        str(item.value)
                    )
                )

        finally:

            session.close()



if __name__=="__main__":


    app=QApplication(
        sys.argv
    )


    win=MainWindow()


    win.show()



    sys.exit(
        app.exec()
    )