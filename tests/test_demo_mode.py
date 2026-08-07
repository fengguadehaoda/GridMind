"""演示模式剧本范围 + P1-3 Key 占位符回归测试（纯单测）。

覆盖：
- P1-1: 高危动作意图（派发/检修/停运/高危/工单/跳闸/隔离/合闸等）优先级高于
  设备/主题白名单 → 无设备标识的随机高危词在演示模式下判定剧本外
  （不触发高危演示、不走 RAG mock）
- P1-1 补充（演示高危话术例外）: 高危词 **同时含演示设备标识**
  （TR-001/主变/变压器，如“建议对#1主变压器进行停机检修”）→ 落白名单
  → 剧本内 → 触发 HITL 审批演示
- P1-2: 白名单匹配前归一化空白 → 无空格变体「请介绍5个核心视图」也能命中
- P1-3: has_key_for(DEEPSEEK) 排除 sk-placeholder/空值（与 DASHSCOPE 一致）

运行：
    python3 -m pytest tests/test_demo_mode.py -q
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from api.agents.agent_factory import _high_risk_mock_reply, _is_demo_script_match


def test_demo_script_match() -> None:
    """P1-1 / P1-2：剧本范围判定回归用例。"""
    cases = [
        # 演示高危话术（高危词 + 设备标识 → 落白名单 → 剧本内 → HITL）
        ("建议对#1主变压器进行停机检修", True),   # 高危操作快捷卡片 → 触发 HITL
        ("派发检修工单给TR-001", True),           # 含设备标识 → 落白名单（剧本内）
        ("变压器停运", True),                     # 含「变压器」设备标识 → 剧本内
        # 无设备标识的随机高危词 → 剧本外（防御保留：不误弹高危审批）
        ("随便检修一下", False),                  # 检修但无设备标识 → 剧本外
        ("跳闸了怎么办", False),                  # 跳闸 → 剧本外
        ("合闸操作", False),                      # 合闸 → 剧本外
        # 剧本内（监控/诊断/功能介绍/设备列表）
        ("变压器运行状态", True),                 # 监控类
        ("所有设备状态", True),                   # 设备列表剧本
        ("变压器故障诊断", True),                 # 诊断类仍在剧本内
        # P1-2：空白归一化变体
        ("请介绍5个核心视图", True),              # 无空格变体 → 剧本内（功能介绍）
        ("5 个核心视图", True),                   # 带空格变体
        # 剧本外
        ("风电场如何管理", False),
        ("", False),                              # 空输入
    ]
    for q, expect in cases:
        got = _is_demo_script_match(q)
        assert got == expect, f"{q!r}: got {got}, expect {expect}"


def test_demo_high_risk_phrase_triggers_hitl() -> None:
    """演示高危话术（高危词 + 设备标识）→ 剧本内 + 命中高危兜底（HITL 演示）。"""
    # 演示模式「高危操作」快捷卡片消息：剧本内（触发 HITL 审批）
    q = "建议对#1主变压器进行停机检修"
    assert _is_demo_script_match(q) is True
    # 且命中高危兜底（suggest_shutdown），演示分支会走工具执行路径触发 interrupt
    assert _high_risk_mock_reply(q) is not None

    # 反例：无设备标识的随机高危词 → 剧本外（防御不放松），也不触发高危兜底路径
    q2 = "随便检修一下"
    assert _is_demo_script_match(q2) is False
    # 注意：_high_risk_mock_reply 对 q2 仍会命中（检修），但演示分支因剧本外
    # 已提前 return 固定提示，不会走到高危兜底路径——由 test_demo_script_match
    # 的剧本外断言保证。


class _FakeSettings:
    """api.config.settings 的轻量替身（真实 Settings 为 frozen，不可改属性）。"""

    deepseek_api_key: str = ""
    dashscope_api_key: str = ""


def test_has_key_for_deepseek_placeholder(monkeypatch) -> None:
    """P1-3：DEEPSEEK 分支排除 sk-placeholder/空值（与 DASHSCOPE 一致）。"""
    from core.llm_client import ModelProvider, has_key_for

    fake = _FakeSettings()

    # 占位符 Key → False（Supervisor 应回退 mock，避免 /chat 空响应）
    fake.deepseek_api_key = "sk-placeholder"
    monkeypatch.setattr("api.config.settings", fake)
    assert has_key_for(ModelProvider.DEEPSEEK) is False

    # 空 Key → False
    fake.deepseek_api_key = ""
    assert has_key_for(ModelProvider.DEEPSEEK) is False

    # 真实 Key → True
    fake.deepseek_api_key = "sk-real-deepseek-key"
    assert has_key_for(ModelProvider.DEEPSEEK) is True


def test_has_key_for_dashscope_placeholder(monkeypatch) -> None:
    """回归护栏：DASHSCOPE 分支同样排除 sk-placeholder（保持既有行为）。"""
    from core.llm_client import ModelProvider, has_key_for

    fake = _FakeSettings()

    fake.dashscope_api_key = "sk-placeholder"
    monkeypatch.setattr("api.config.settings", fake)
    assert has_key_for(ModelProvider.DASHSCOPE) is False

    fake.dashscope_api_key = "sk-real-dashscope-key"
    assert has_key_for(ModelProvider.DASHSCOPE) is True
