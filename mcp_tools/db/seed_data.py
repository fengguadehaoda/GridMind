"""种子数据——设备、遥测（含异常样本）、巡检记录、安规条款、知识库片段、图谱实体/关系。

为 demo 注入人为构造的异常样本，确保异常检测与混合 RAG 演示效果可见。
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta

from loguru import logger

from mcp_tools.db.database import get_connection

# 固定随机种子保证可复现
RNG = random.Random(42)

DEVICES: list[tuple[str, str, str, str, str, str, float, float, float]] = [
    # device_id, name, type, location, install_date, status, rated_current(A), short_impedance(%), rated_voltage(kV)
    ("TR-001", "一号主变",         "transformer", "A区变电站", "2022-03-15", "normal",   120.0, 8.5,  220.0),
    ("TR-002", "二号主变",         "transformer", "B区变电站", "2022-06-20", "normal",   100.0, 10.0, 110.0),
    ("BR-001", "进线断路器",        "breaker",     "A区变电站", "2023-01-10", "normal",   630.0, 12.0, 10.0),
    ("BR-002", "出线断路器",        "breaker",     "B区变电站", "2023-02-14", "warning",  630.0, 12.0, 10.0),
    ("CB-001", "高压电缆-A线",     "cable",       "A区-架空线", "2021-11-01", "normal",  300.0,  0.5,  10.0),
    ("CB-002", "高压电缆-B线",     "cable",       "B区-地埋段", "2021-12-15", "normal",  300.0,  0.5,  10.0),
    ("BB-001", "10kV母线",         "busbar",      "A区变电站", "2022-05-01", "normal",  2000.0,  0.1,  10.0),
    ("BB-002", "35kV母线",         "busbar",      "B区变电站", "2022-08-01", "critical", 1500.0, 0.15, 35.0),
]

SAFETY_RULES: list[tuple[str, str, str, str]] = [
    ("DL/T-572-2010-1", "操作票",   "倒闸操作必须持有有效操作票，严禁无票操作", "mandatory"),
    ("DL/T-572-2010-2", "操作票",   "操作票须经值班长审核签字后方可执行", "mandatory"),
    ("DL/T-572-2010-3", "验电",     "停电设备必须三相验电确认无电压后方可装设接地线", "mandatory"),
    ("Q/GDW-1799-1",    "安全距离", "10kV设备不停电时安全距离不小于0.7m", "mandatory"),
    ("Q/GDW-1799-2",    "安全距离", "35kV设备不停电时安全距离不小于1.0m", "mandatory"),
    ("Q/GDW-1799-3",    "安全距离", "110kV设备不停电时安全距离不小于1.5m", "mandatory"),
    ("DL/T-572-2010-4", "工作票",   "检修工作必须办理工作票，工作票有效期不超过7天", "mandatory"),
    ("DL/T-572-2010-5", "接地",     "装设接地线必须先接接地端，后接导体端", "mandatory"),
    ("Q/GDW-1799-4",    "防火",     "电缆沟道内不得堆放易燃物品，电缆孔洞必须封堵", "mandatory"),
    ("DL/T-572-2010-6", "送电",     "送电前必须确认所有工作票已终结、接地线已拆除、人员已撤离", "mandatory"),
]

KNOWLEDGE_CHUNKS: list[tuple[str, str, str, str]] = [
    ("doc-001", "变压器过载运行规程",
     "变压器正常运行时，负载电流不应超过额定值的1.3倍。"
     "若超过1.3倍但小于1.5倍，允许运行时间不超过60分钟；"
     "超过1.5倍但小于2.0倍，允许运行时间不超过20分钟。"
     "超过2.0倍应立即减载或切除。",
     "GB/T 1094.7-2016"),
    ("doc-002", "变压器油温异常处置",
     "顶层油温超过85℃时发出告警，超过95℃时应申请减载或停运。"
     "油温异常原因包括：过负荷、散热器堵塞、油泵故障、内部故障。"
     "若油温骤升10℃/h以上应紧急停运。",
     "DL/T 572-2010"),
    ("doc-003", "断路器SF6压力监控",
     "SF6断路器气体压力低于0.45MPa（20℃折算值）时补气，"
     "低于0.40MPa时闭锁操作并报警。年漏气率应小于0.5%。"
     "补气时必须使用专用SF6气体回收装置。",
     "GB 1984-2014"),
    ("doc-004", "电缆局部放电在线监测",
     "电缆局部放电量超过100pC时进入预警状态，"
     "超过200pC时应安排停电检修。"
     "局放趋势增速超过50%/月视为危急缺陷。"
     "推荐采用高频电流互感器（HFCT）法在线监测。",
     "DL/T 1578-2016"),
    ("doc-005", "母线差动保护动作处置",
     "母线差动保护动作后，应先检查母线及连接设备外观有无明显故障痕迹，"
     "测量绝缘电阻，确认无故障后方可试送。"
     "若试送失败，应隔离故障母线，转移负荷。"
     '严禁用"拉路法"强行试送疑似故障母线。',
     "Q/GDW 1164-2014"),
    ("doc-006", "电力设备健康评估导则",
     "设备健康状态分为四级：正常（80-100分）、注意（60-79分）、"
     "异常（40-59分）、严重（<40分）。综合评估参数包括电气量、"
     "非电气量、历史故障、运行年限、家族缺陷等。"
     "评估周期：正常设备每年一次，异常设备每季度一次。",
     "Q/GDW 1168-2013"),
    ("doc-007", "避雷器泄漏电流监测",
     "氧化锌避雷器全电流超过初始值1.2倍或阻性电流超过初始值1.5倍时告警。"
     "雷雨季节前应进行一次预防性试验。"
     "动作计数器读数大于铭牌额定值时更换。",
     "DL/T 596-2005"),
    ("doc-008", "接地电阻测量规范",
     "变电站接地网接地电阻应不大于0.5Ω。"
     "杆塔接地电阻：有地线线路杆塔应不大于10Ω，"
     "无地线线路杆塔应不大于30Ω。"
     "测量采用三极法，应在干燥季节进行。",
     "DL/T 621-1997"),
]


def _make_anomalous_readings(
    base: float, count: int, anomaly_idx: int, factor: float
) -> list[float]:
    """生成以 base 为中心的正常数据，在 anomaly_idx 处插入异常值。"""
    values = []
    for i in range(count):
        noise = RNG.gauss(0, base * 0.02)
        if i == anomaly_idx:
            values.append(base * factor + noise)   # 异常尖峰
        else:
            values.append(base + noise)
    return values


def seed_all(full_reset: bool = False) -> None:
    """写入所有种子数据（B3 修复：默认**幂等 upsert**，不再清空知识库）。

    Args:
        full_reset: 是否恢复 V1.6 之前的"全表清空"行为。
            - ``False``（默认）：**不清空** ``knowledge_chunks``，保护运营经
              reload 热更新的自定义分片（V1.6 热更新设计）；8 条种子
              doc-001..008 按 doc_id 先删后插（等价 ``INSERT OR REPLACE``——
              doc_id 无 UNIQUE 约束，直接 OR REPLACE 无法去重）。其余 6 张
              demo 表（devices/telemetry/inspections/safety_rules/
              graph_entities/graph_relations）保留原有清空逻辑。
            - ``True``：恢复旧行为，7 张表全部清空后重写（仅显式人工触发）。

    Returns:
        None
    """
    conn = get_connection()
    try:
        if full_reset:
            # 显式全量重置：7 表全清空（反向依赖顺序，避免 FOREIGN KEY 冲突）
            tables = [
                "graph_relations", "graph_entities",
                "knowledge_chunks", "safety_rules",
                "inspections", "telemetry", "devices",
            ]
        else:
            # 默认：只清空 6 张 demo 表，**不碰** knowledge_chunks
            tables = [
                "graph_relations", "graph_entities",
                "safety_rules",
                "inspections", "telemetry", "devices",
            ]
        for t in tables:
            conn.execute(f"DELETE FROM {t}")

        # 知识库片段：幂等 upsert（B3）
        # 仅按 8 条种子 doc_id 先删后插——等价 INSERT OR REPLACE 语义，
        # 同时保留运营热更新写入的自定义分片（如 feature-intro:*）。
        seed_doc_ids = [chunk[0] for chunk in KNOWLEDGE_CHUNKS]
        conn.executemany(
            "DELETE FROM knowledge_chunks WHERE doc_id = ?",
            [(doc_id,) for doc_id in seed_doc_ids],
        )

        # 1. 设备（含 P0 铭牌字段：rated_current / short_impedance / rated_voltage）
        conn.executemany(
            "INSERT INTO devices VALUES (?,?,?,?,?,?,?,?,?)",
            DEVICES,
        )

        # 2. 遥测（每设备 60 条，间隔 1 小时，覆盖最近 60 小时）
        now = datetime.now()
        telem_rows: list[tuple[str, str, float, float, float, float, float]] = []
        for dev_id, dev_name, dtype, loc, *_ in DEVICES:
            base_temp = {"transformer": 55.0, "breaker": 35.0, "cable": 40.0, "busbar": 45.0}.get(dtype, 40.0)
            base_volt = {"transformer": 10.5, "breaker": 10.0, "cable": 10.0, "busbar": 10.0}.get(dtype, 10.0)
            base_load = {"transformer": 60.0, "breaker": 50.0, "cable": 55.0, "busbar": 45.0}.get(dtype, 50.0)
            base_hum = 60.0
            base_pres = {"transformer": 0.6, "breaker": 0.5, "cable": 0.4, "busbar": 0.5}.get(dtype, 0.5)

            # 在某些设备中注入异常
            anomaly_idx = -1
            anomaly_factor = 1.0
            if dev_id == "TR-001":
                anomaly_idx, anomaly_factor = 45, 1.6   # 过载
            elif dev_id == "BB-002":
                anomaly_idx, anomaly_factor = 50, 2.2   # 严重过载

            temps = _make_anomalous_readings(base_temp, 60, anomaly_idx, anomaly_factor * 1.3)
            volts = _make_anomalous_readings(base_volt, 60, anomaly_idx, 1.15)
            loads = _make_anomalous_readings(base_load, 60, anomaly_idx, anomaly_factor)

            for i in range(60):
                ts = (now - timedelta(hours=59 - i)).strftime("%Y-%m-%d %H:00:00")
                telem_rows.append((
                    dev_id, ts,
                    round(temps[i], 1),
                    round(volts[i], 2),
                    round(loads[i], 1),
                    round(base_hum + RNG.gauss(0, 3), 1),
                    round(base_pres + RNG.gauss(0, 0.02), 3),
                ))

        conn.executemany(
            "INSERT INTO telemetry(device_id,timestamp,temperature,voltage,current_load,humidity,pressure) "
            "VALUES (?,?,?,?,?,?,?)",
            telem_rows,
        )

        # 3. 巡检记录（每设备 4 条）
        insp_rows: list[tuple[str, str, str, str]] = []
        for dev_id, *_ in DEVICES:
            for m in range(4):
                ts = (now - timedelta(days=30 * (3 - m), hours=RNG.randint(0, 8))).strftime("%Y-%m-%d %H:%M")
                result = "abnormal" if (dev_id == "BB-002" and m == 3) else "normal"
                notes = "35kV母线温升偏高，建议安排检修" if (dev_id == "BB-002" and m == 3) else "正常"
                insp_rows.append((dev_id, "巡检机器人-01", ts, result, notes))

        conn.executemany(
            "INSERT INTO inspections(device_id,inspector,inspect_time,result,notes) VALUES (?,?,?,?,?)",
            insp_rows,
        )

        # 4. 安规条款
        conn.executemany(
            "INSERT INTO safety_rules(rule_code,category,content,severity) VALUES (?,?,?,?)",
            SAFETY_RULES,
        )

        # 5. 知识库片段
        conn.executemany(
            "INSERT INTO knowledge_chunks(doc_id,title,content,source) VALUES (?,?,?,?)",
            KNOWLEDGE_CHUNKS,
        )

        # 6. 图谱实体
        entities: list[tuple[str, str, str, str]] = [
            ("e-transformer", "变压器", "设备类别", "{}"),
            ("e-breaker",     "断路器",  "设备类别", "{}"),
            ("e-cable",       "电缆",    "设备类别", "{}"),
            ("e-busbar",      "母线",    "设备类别", "{}"),
            ("e-overload",    "过载",    "故障类型", '{"severity":"high"}'),
            ("e-overtemp",    "油温异常", "故障类型", '{"severity":"high"}'),
            ("e-sf6leak",     "SF6泄漏", "故障类型", '{"severity":"medium"}'),
            ("e-partial-discharge", "局部放电", "故障类型", '{"severity":"medium"}'),
            ("e-ground-fault", "接地故障", "故障类型", '{"severity":"high"}'),
            ("e-derating",    "减载",    "处置措施", "{}"),
            ("e-shutdown",    "停运",    "处置措施", "{}"),
            ("e-repair",      "检修",    "处置措施", "{}"),
            ("e-replace",     "更换",    "处置措施", "{}"),
            ("e-monitor",     "加强监测", "处置措施", "{}"),
            ("e-DL572",       "DL/T 572-2010", "规程", "{}"),
            ("e-QGDW1799",    "Q/GDW 1799",    "规程", "{}"),
            ("e-GB1094",      "GB/T 1094.7-2016", "规程", "{}"),
            ("e-DL1578",      "DL/T 1578-2016",  "规程", "{}"),
            ("e-TR001",       "一号主变",      "设备实例", '{"device_id":"TR-001"}'),
            ("e-TR002",       "二号主变",      "设备实例", '{"device_id":"TR-002"}'),
            ("e-BB002",       "35kV母线",      "设备实例", '{"device_id":"BB-002"}'),
        ]
        conn.executemany(
            "INSERT OR IGNORE INTO graph_entities(entity_id,name,type,properties) VALUES (?,?,?,?)",
            entities,
        )

        # 7. 图谱关系
        relations: list[tuple[str, str, str]] = [
            # 设备类别 → 故障类型
            ("e-transformer", "e-overload",        "可能发生"),
            ("e-transformer", "e-overtemp",        "可能发生"),
            ("e-breaker",     "e-sf6leak",         "可能发生"),
            ("e-cable",       "e-partial-discharge","可能发生"),
            ("e-busbar",      "e-ground-fault",    "可能发生"),
            # 故障 → 处置
            ("e-overload",    "e-derating",        "处置"),
            ("e-overload",    "e-shutdown",        "严重时处置"),
            ("e-overtemp",    "e-derating",        "处置"),
            ("e-overtemp",    "e-shutdown",        "严重时处置"),
            ("e-sf6leak",     "e-repair",          "处置"),
            ("e-sf6leak",     "e-replace",         "严重时处置"),
            ("e-partial-discharge", "e-monitor",   "处置"),
            ("e-partial-discharge", "e-repair",    "严重时处置"),
            ("e-ground-fault", "e-repair",         "处置"),
            # 规程 → 设备/故障
            ("e-DL572",  "e-transformer", "适用于"),
            ("e-DL572",  "e-overtemp",    "关联"),
            ("e-GB1094", "e-transformer", "适用于"),
            ("e-DL1578", "e-cable",       "适用于"),
            ("e-DL1578", "e-partial-discharge", "关联"),
            # 设备实例 → 设备类别
            ("e-TR001", "e-transformer", "属于"),
            ("e-TR002", "e-transformer", "属于"),
            ("e-BB002", "e-busbar",      "属于"),
            # 跨跳关联（用于演示多跳图谱扩展）
            ("e-TR001", "e-overload",    "已发生"),   # 一号主变发生过载
            ("e-BB002", "e-ground-fault","已发生"),   # 35kV母线接地故障
        ]
        conn.executemany(
            "INSERT OR IGNORE INTO graph_relations(source_id,target_id,relation_type) VALUES (?,?,?)",
            relations,
        )

        conn.commit()
        logger.info("Seed data written: {} devices, {} telemetry, {} rules, {} chunks, {} entities, {} relations",
                     len(DEVICES), len(telem_rows), len(SAFETY_RULES), len(KNOWLEDGE_CHUNKS),
                     len(entities), len(relations))
    finally:
        conn.close()
