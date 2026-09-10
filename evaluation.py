from database import SessionLocal

from models import (
    DispatchHistory,
    SystemLog,
    YCInfo
)



# =====================================================
# A 通信稳定性评价
# =====================================================
def evaluate_A(session):

    try:

        logs = session.query(
            SystemLog
        ).order_by(
            SystemLog.id.desc()
        ).limit(100).all()


        if len(logs) == 0:
            return 100


        error_num = 0
        warn_num = 0


        for log in logs:

            if log.level == "ERROR":
                error_num += 1

            elif log.level == "WARN":
                warn_num += 1



        # ERROR严重扣分
        # WARN轻微扣分

        score = 100 - error_num * 5 - warn_num * 3

        if score < 0:
            score = 0


        return round(score,2)


    except Exception as e:

        print("A评价错误:",e)

        return 0

# =====================================================
# B 调度执行评价
# =====================================================
def evaluate_B(
        session,
        load_power=None,
        wind_set=None,
        diesel_set=None
):

    try:

        history = session.query(
            DispatchHistory
        ).order_by(
            DispatchHistory.id.desc()
        ).limit(50).all()


        errors=[]


        if len(history)>0:


            for h in history:

                total_power = (
                    h.wind_set
                    +
                    h.diesel_set
                )


                if h.load_power != 0:

                    error = abs(
                        total_power -
                        h.load_power
                    ) / h.load_power


                    errors.append(error)


        else:


            if (
                load_power is None
                or wind_set is None
                or diesel_set is None
            ):

                return 0


            error = abs(
                wind_set +
                diesel_set -
                load_power
            ) / load_power


            errors.append(error)



        avg_error = sum(errors)/len(errors)


        score = (
            1-avg_error
        )*100


        if score < 0:
            score = 0


        if score > 100:
            score = 100


        return round(score,2)


    except Exception as e:

        print("B评价错误:",e)

        return 0
# =====================================================
# C 数据稳定性评价
# =====================================================

def evaluate_C(session):

    try:


        data=session.query(
            YCInfo
        ).all()



        if len(data)==0:

            return 0



        abnormal=0



        for d in data:


            code=d.code.upper()


            value=d.value



            # 频率

            if "FREQ" in code:

                if value <49 or value>51:

                    abnormal+=1



            # 电压

            elif "VOLT" in code:

                if value<180 or value>260:

                    abnormal+=1



            # 负值

            if value <0:

                abnormal+=1




        abnormal_rate=(

            abnormal /
            len(data)

        )



        score=(

            100 -
            abnormal_rate*100

        )


        if score<0:

            score=0


        return round(score,2)



    except Exception as e:

        print("C评价错误:",e)

        return 0


# =====================================================
# main.py 使用
# =====================================================

def get_evaluation():


    session=SessionLocal()


    try:


        A=evaluate_A(session)

        B=evaluate_B(session)

        C=evaluate_C(session)



        return [

            {
                "对象":"A",
                "评价指标":"通信稳定性",
                "得分":A,
                "说明":"根据通信成功率计算"
            },


            {
                "对象":"B",
                "评价指标":"调度执行",
                "得分":B,
                "说明":"根据发电功率跟踪误差计算"
            },


            {
                "对象":"C",
                "评价指标":"数据稳定性",
                "得分":C,
                "说明":"根据遥测数据异常率计算"
            }

        ]


    finally:

        session.close()




# =====================================================
# operator_core.py 使用
# =====================================================

def evaluate_ems(
        session,
        load_power,
        wind_set,
        diesel_set,
        dg_min,
        dg_max,
        control_mode=None
):


    A=evaluate_A(session)



    B=evaluate_B(
        session,
        load_power,
        wind_set,
        diesel_set
    )



    C=evaluate_C(session)



    return [

        {
            "对象":"A",
            "评价指标":"通信稳定性",
            "得分":A,
            "说明":"通信成功率"
        },


        {
            "对象":"B",
            "评价指标":"调度执行",
            "得分":B,
            "说明":"功率跟踪误差"
        },


        {
            "对象":"C",
            "评价指标":"数据稳定性",
            "得分":C,
            "说明":"遥测异常率"
        }

    ]




# =====================================================
# 单独测试
# =====================================================

if __name__=="__main__":


    session=SessionLocal()



    result=evaluate_ems(

        session,

        load_power=280,

        wind_set=57.87,

        diesel_set=222.13,

        dg_min=30,

        dg_max=300

    )



    print("================")

    print("真实ABC评价结果")

    print("================")



    for r in result:

        print(r)



    session.close()