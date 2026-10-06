"""
游戏内任务生成器与程序化任务系统 - 核心模块

功能：
- generate_quest: 带随机性与多样性的程序化任务生成
- template_engine: 支持变量替换与条件分支的任务模板引擎
- quest_builder: 从目标构建完整任务（目标链 + 奖励）
- dynamic_difficulty: 基于玩家评估的动态难度调整
- quest_validation: 任务可完成性 / 可达性 / 一致性验证
"""
from dataclasses import dataclass, field
import random
import re
from typing import Dict, List, Optional, Tuple


@dataclass
class Config:
    pass


# ---------------------------------------------------------------------------
# 内容池：程序化生成所用的素材
# ---------------------------------------------------------------------------

QUEST_TYPES = ("kill", "collect", "explore", "escort", "talk")

NPCS = ("艾拉", "布隆", "卡德加", "莉娜", "老汤姆", "塞西莉亚")
LOCATIONS = ("新手村", "幽暗森林", "风鸣峡谷", "熔岩洞窟", "遗忘遗迹", "月影湖畔")
ITEMS = ("治疗草药", "铁矿石", "月光水晶", "古代卷轴", "狼牙", "魔法粉尘")
ENEMIES = ("野狼", "骷髅兵", "森林巨魔", "熔岩傀儡", "暗影刺客", "腐化树人")

# 默认世界图：地点之间的可达关系（无向）
DEFAULT_WORLD = {
    "新手村": {"幽暗森林", "月影湖畔"},
    "幽暗森林": {"新手村", "风鸣峡谷", "遗忘遗迹"},
    "风鸣峡谷": {"幽暗森林", "熔岩洞窟"},
    "熔岩洞窟": {"风鸣峡谷", "遗忘遗迹"},
    "遗忘遗迹": {"幽暗森林", "熔岩洞窟"},
    "月影湖畔": {"新手村"},
}

_TYPE_TARGETS = {
    "kill": ENEMIES,
    "collect": ITEMS,
    "explore": LOCATIONS,
    "escort": NPCS,
    "talk": NPCS,
}

_TYPE_VERB = {
    "kill": "击杀",
    "collect": "收集",
    "explore": "探索",
    "escort": "护送",
    "talk": "对话",
}

_OPS = {
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    "in": lambda a, b: a in b,
}

# 难度档位：tier -> (推荐等级, 目标数量基数, 奖励倍率)
DIFFICULTY_TIERS = {
    1: (1, 3, 1.0),
    2: (5, 5, 1.5),
    3: (10, 8, 2.2),
    4: (20, 12, 3.0),
    5: (35, 16, 4.0),
}


class QuestGenerator:
    def __init__(self, config: Optional[Dict] = None):
        self.config = config or {}
        self._state = {}
        self._history = []
        self._rng = random.Random(self.config.get("seed"))
        self._world = self.config.get("world", DEFAULT_WORLD)
        self._quest_counter = 0

    def update(self, dt: float):
        pass

    def reset(self):
        self._state = {}
        self._history = []

    # ------------------------------------------------------------------
    # 1. 任务生成：随机性 + 多样性
    # ------------------------------------------------------------------
    def generate_quest(self, player: Optional[Dict] = None, seed: Optional[int] = None) -> Dict:
        """生成一个完整任务。

        - 随机性：每次生成从任务类型 / NPC / 地点 / 物品池中随机抽取；
          传入相同 seed 可复现，否则由内部 RNG 驱动，连续生成结果不同。
        - 多样性：任务类型覆盖 kill / collect / explore / escort / talk，
          并避免与上一次生成的任务类型重复。
        """
        rng = random.Random(seed) if seed is not None else self._rng

        last_type = self._history[-1]["type"] if (self._history and seed is None) else None
        candidates = [t for t in QUEST_TYPES if t != last_type] or list(QUEST_TYPES)
        quest_type = rng.choice(candidates)

        difficulty = self.dynamic_difficulty(player)
        tier = difficulty["tier"]
        _, base_count, _ = DIFFICULTY_TIERS[tier]

        npc = rng.choice(NPCS)
        location = rng.choice(LOCATIONS)
        target = rng.choice(_TYPE_TARGETS[quest_type])
        count = max(1, int(base_count * rng.uniform(0.8, 1.2))) if quest_type in ("kill", "collect") else 1

        objectives = [{
            "type": quest_type,
            "target": target,
            "count": count,
            "location": location,
        }]
        quest = self.quest_builder(objectives, difficulty=difficulty, rng=rng)
        quest.update({
            "npc": npc,
            "location": location,
            "type": quest_type,
        })
        quest["name"] = self.template_engine(
            "{npc}的委托：{verb}{target}", {"npc": npc, "verb": _TYPE_VERB[quest_type], "target": target}
        )

        self._history.append(quest)
        return quest

    # ------------------------------------------------------------------
    # 2. 模板引擎：变量替换 + 条件分支 + 模板组合
    # ------------------------------------------------------------------
    def template_engine(self, template, context: Optional[Dict] = None):
        """渲染任务模板。

        - template 为 str：替换其中的 {变量}。
        - template 为 dict：
            "text": 含 {变量} 的文本；
            "conditions": [{"if": {"key","op","value"}, "then": str, "else": str(可选)}]
              根据 context 中的玩家状态选择分支文本；
            "compose": [子模板, ...] 将多个模板渲染结果拼接，组合生成新任务文本。
        - context 提供变量值与玩家状态。
        """
        context = context or {}

        if isinstance(template, str):
            return self._render_text(template, context)

        if not isinstance(template, dict):
            raise TypeError("template 必须是 str 或 dict")

        if "compose" in template:
            parts = [self.template_engine(sub, context) for sub in template["compose"]]
            return template.get("separator", "").join(parts)

        text = template.get("text", "")
        for cond in template.get("conditions", []):
            branch = cond.get("then", "") if self._eval_condition(cond.get("if", {}), context) else cond.get("else", "")
            text += branch
        return self._render_text(text, context)

    def _render_text(self, text: str, context: Dict) -> str:
        def repl(match):
            key = match.group(1)
            if key not in context:
                raise KeyError(f"模板变量未提供: {key}")
            return str(context[key])

        return re.sub(r"\{(\w+)\}", repl, text)

    def _eval_condition(self, cond: Dict, context: Dict) -> bool:
        if not cond:
            return True
        key, op, value = cond.get("key"), cond.get("op", "=="), cond.get("value")
        if key not in context:
            return False
        fn = _OPS.get(op)
        if fn is None:
            raise ValueError(f"不支持的比较操作: {op}")
        return fn(context[key], value)

    # ------------------------------------------------------------------
    # 3. 任务构建：目标链 + 奖励
    # ------------------------------------------------------------------
    def quest_builder(self, objectives: List[Dict], difficulty: Optional[Dict] = None,
                      rng: Optional[random.Random] = None) -> Dict:
        """从目标列表构建完整任务。

        - 目标链：多个目标按顺序串联为多步骤任务，每个步骤带 step 序号，
          前一步骤完成后解锁下一步（prerequisite 指向前一步 id）。
        - 奖励：根据目标数量、类型与难度倍率计算金币 / 经验 / 物品奖励。
        """
        if not objectives:
            raise ValueError("objectives 不能为空")

        rng = rng or self._rng
        difficulty = difficulty or self.dynamic_difficulty(None)
        tier = difficulty["tier"]
        _, _, reward_mult = DIFFICULTY_TIERS[tier]

        self._quest_counter += 1
        quest_id = f"quest_{self._quest_counter}"

        chain = []
        for i, obj in enumerate(objectives):
            step = dict(obj)
            step["step"] = i + 1
            step["id"] = f"{quest_id}_step{i + 1}"
            step["prerequisite"] = chain[-1]["id"] if chain else None
            chain.append(step)

        total_count = sum(o.get("count", 1) for o in objectives)
        rewards = {
            "gold": int((20 * len(chain) + 5 * total_count) * reward_mult),
            "exp": int((30 * len(chain) + 8 * total_count) * reward_mult),
            "items": [rng.choice(ITEMS)] if tier >= 3 else [],
        }

        return {
            "id": quest_id,
            "name": "",
            "type": objectives[0].get("type", "talk"),
            "objectives": chain,
            "rewards": rewards,
            "difficulty": difficulty,
            "steps": len(chain),
        }

    # ------------------------------------------------------------------
    # 4. 动态难度：玩家评估 + 难度调整
    # ------------------------------------------------------------------
    def dynamic_difficulty(self, player: Optional[Dict] = None) -> Dict:
        """评估玩家实力并据此调整难度。

        - 玩家评估：综合等级、装备评分、历史胜率估算实力分。
        - 难度调整：实力分映射到 1-5 难度档；玩家近期连败则下调一档，
          连胜则上调一档，保证任务不会太简单或太难。
        """
        player = player or {}
        level = player.get("level", 1)
        gear = player.get("gear_score", 0)
        win_rate = player.get("win_rate", 0.5)

        power = level * 10 + gear * 0.5 + (win_rate - 0.5) * 40

        tier = 1
        for t, (rec_level, _, _) in sorted(DIFFICULTY_TIERS.items()):
            if power >= rec_level * 10:
                tier = t

        streak = player.get("streak", 0)  # 正为连胜，负为连败
        if streak <= -3:
            tier -= 1
        elif streak >= 3:
            tier += 1
        tier = max(1, min(5, tier))

        rec_level, base_count, reward_mult = DIFFICULTY_TIERS[tier]
        return {
            "tier": tier,
            "player_power": power,
            "recommended_level": rec_level,
            "count_multiplier": base_count / DIFFICULTY_TIERS[1][1],
            "reward_multiplier": reward_mult,
        }

    # ------------------------------------------------------------------
    # 5. 任务验证：可完成性 + 可达性 + 一致性
    # ------------------------------------------------------------------
    def quest_validation(self, quest: Dict, world: Optional[Dict] = None) -> Dict:
        """验证任务是否合法。

        - 可完成性：目标类型 / 目标对象必须存在于内容池中，数量必须为正。
        - 可达性：任务地点必须在世界图中从“新手村”可达。
        - 一致性：奖励必须非空且与目标规模、难度档位匹配。
        """
        world = world or self._world
        errors = []

        objectives = quest.get("objectives") or []
        if not objectives:
            errors.append("任务没有目标")

        for obj in objectives:
            otype = obj.get("type")
            if otype not in _TYPE_TARGETS:
                errors.append(f"未知目标类型: {otype}")
                continue
            if obj.get("target") not in _TYPE_TARGETS[otype]:
                errors.append(f"目标不存在，无法完成: {obj.get('target')}")
            if obj.get("count", 1) < 1:
                errors.append(f"目标数量非法: {obj.get('count')}")

            location = obj.get("location")
            if location is not None and not self._reachable(location, world):
                errors.append(f"地点不可达: {location}")

        rewards = quest.get("rewards") or {}
        if not rewards or (rewards.get("gold", 0) <= 0 and rewards.get("exp", 0) <= 0):
            errors.append("任务没有奖励")
        else:
            total_count = sum(o.get("count", 1) for o in objectives)
            tier = (quest.get("difficulty") or {}).get("tier", 1)
            min_exp = 10 * len(objectives) * tier
            if rewards.get("exp", 0) < min_exp:
                errors.append("奖励与目标规模/难度不匹配")
            if total_count > 0 and rewards.get("gold", 0) < 5 * total_count:
                errors.append("金币奖励与目标数量不匹配")

        return {"valid": not errors, "errors": errors}

    def _reachable(self, location: str, world: Dict, start: str = "新手村") -> bool:
        if location not in world:
            return False
        visited, stack = set(), [start]
        while stack:
            node = stack.pop()
            if node == location:
                return True
            if node in visited:
                continue
            visited.add(node)
            stack.extend(world.get(node, ()))
        return False
