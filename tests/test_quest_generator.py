"""
游戏内任务生成器与程序化任务系统 - 验收测试
运行方式：python -m pytest tests/test_quest_generator.py -q
共 14 个测试用例
"""
import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from quest.generator import QuestGenerator, QUEST_TYPES


class TestQuestGenerator:
    def setup_method(self):
        self.system = QuestGenerator()

    # ---- 任务生成：随机性 ----
    def test_case_01(self):
        quest = self.system.generate_quest()
        for key in ("id", "name", "type", "objectives", "rewards", "difficulty", "npc", "location"):
            assert key in quest, f"任务缺少字段: {key}"
        assert quest["type"] in QUEST_TYPES
        assert quest["objectives"], "任务应至少有一个目标"

    def test_case_02(self):
        gen = QuestGenerator()
        signatures = {
            (q["type"], q["objectives"][0]["target"], q["npc"], q["location"])
            for q in (gen.generate_quest() for _ in range(10))
        }
        assert len(signatures) > 1, "连续生成的任务完全相同，没有随机性"

    def test_case_03(self):
        q1 = self.system.generate_quest(seed=42)
        q2 = self.system.generate_quest(seed=42)
        strip = lambda q: (q["type"], q["name"],
                           [{k: v for k, v in o.items() if k not in ("id", "prerequisite")}
                            for o in q["objectives"]],
                           q["rewards"])
        assert strip(q1) == strip(q2), "相同 seed 应生成相同任务"''

    # ---- 任务生成：多样性 ----
    def test_case_04(self):
        gen = QuestGenerator()
        types = {gen.generate_quest()["type"] for _ in range(60)}
        assert len(types) >= 3, f"任务类型缺乏多样性: {types}"

    # ---- 模板引擎：变量 ----
    def test_case_05(self):
        text = self.system.template_engine(
            "去{location}找{npc}领取{item}",
            {"location": "新手村", "npc": "艾拉", "item": "治疗草药"},
        )
        assert text == "去新手村找艾拉领取治疗草药"

    # ---- 模板引擎：条件 ----
    def test_case_06(self):
        template = {
            "text": "猎杀野狼",
            "conditions": [
                {"if": {"key": "level", "op": ">=", "value": 10}, "then": "（精英）", "else": "（普通）"}
            ],
        }
        assert self.system.template_engine(template, {"level": 20}) == "猎杀野狼（精英）"
        assert self.system.template_engine(template, {"level": 3}) == "猎杀野狼（普通）"

    # ---- 模板引擎：组合 ----
    def test_case_07(self):
        template = {
            "compose": [
                {"text": "第一章：{intro}"},
                {"text": "第二章：{boss}"},
            ],
            "separator": " -> ",
        }
        text = self.system.template_engine(template, {"intro": "收集草药", "boss": "讨伐巨魔"})
        assert text == "第一章：收集草药 -> 第二章：讨伐巨魔"

    # ---- 任务构建：从目标生成完整任务 ----
    def test_case_08(self):
        quest = self.system.quest_builder([{"type": "kill", "target": "野狼", "count": 5}])
        assert quest["id"]
        assert quest["objectives"][0]["target"] == "野狼"
        assert quest["rewards"]["gold"] > 0 and quest["rewards"]["exp"] > 0

    # ---- 任务构建：目标链 ----
    def test_case_09(self):
        objectives = [
            {"type": "collect", "target": "铁矿石", "count": 3},
            {"type": "kill", "target": "骷髅兵", "count": 5},
            {"type": "talk", "target": "艾拉", "count": 1},
        ]
        quest = self.system.quest_builder(objectives)
        chain = quest["objectives"]
        assert len(chain) == 3
        assert [s["step"] for s in chain] == [1, 2, 3]
        assert chain[0]["prerequisite"] is None
        assert chain[1]["prerequisite"] == chain[0]["id"]
        assert chain[2]["prerequisite"] == chain[1]["id"]

    # ---- 任务构建：奖励 ----
    def test_case_10(self):
        easy = self.system.quest_builder(
            [{"type": "kill", "target": "野狼", "count": 5}],
            difficulty=self.system.dynamic_difficulty({"level": 1}),
        )
        hard = self.system.quest_builder(
            [{"type": "kill", "target": "野狼", "count": 5}],
            difficulty=self.system.dynamic_difficulty({"level": 40}),
        )
        assert hard["rewards"]["gold"] > easy["rewards"]["gold"]
        assert hard["rewards"]["exp"] > easy["rewards"]["exp"]

    # ---- 动态难度：玩家评估 ----
    def test_case_11(self):
        diff = self.system.dynamic_difficulty({"level": 15, "gear_score": 100, "win_rate": 0.8})
        assert diff["player_power"] > 0
        assert 1 <= diff["tier"] <= 5
        weak = self.system.dynamic_difficulty({"level": 1, "gear_score": 0, "win_rate": 0.2})
        assert weak["tier"] < diff["tier"]

    # ---- 动态难度：难度调整 ----
    def test_case_12(self):
        base = self.system.dynamic_difficulty({"level": 10})
        losing = self.system.dynamic_difficulty({"level": 10, "streak": -5})
        winning = self.system.dynamic_difficulty({"level": 10, "streak": 5})
        assert losing["tier"] == max(1, base["tier"] - 1)
        assert winning["tier"] == min(5, base["tier"] + 1)

    # ---- 任务验证：可达性 ----
    def test_case_13(self):
        quest = self.system.quest_builder(
            [{"type": "explore", "target": "幽暗森林", "count": 1, "location": "幽暗森林"}]
        )
        assert self.system.quest_validation(quest)["valid"]
        quest["objectives"][0]["location"] = "虚空裂隙"
        result = self.system.quest_validation(quest)
        assert not result["valid"]
        assert any("不可达" in e for e in result["errors"])

    # ---- 任务验证：一致性与可完成性 ----
    def test_case_14(self):
        bad = self.system.quest_builder([{"type": "kill", "target": "不存在的怪", "count": 5}])
        bad["rewards"] = {"gold": 0, "exp": 0}
        result = self.system.quest_validation(bad)
        assert not result["valid"]
        assert any("无法完成" in e for e in result["errors"])
        assert any("奖励" in e for e in result["errors"])

        gen = QuestGenerator()
        for _ in range(20):
            assert gen.quest_validation(gen.generate_quest())["valid"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
