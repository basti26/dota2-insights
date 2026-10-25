"""Pure hero catalog extracted from the helper, without capture code."""
import re

def hero_id_for_name(name: str) -> str:
    """Return the stable Dota hero identifier for a canonical English name."""
    value = str(name).strip()
    if value in {"Spirit Bear", "熊灵", "npc_dota_lone_druid_bear"}:
        return "npc_dota_lone_druid_bear"
    if value.startswith("npc_dota_hero_"):
        return value
    chinese_names = globals().get("DEFAULT_HERO_NAMES_ZH", ())
    english_names = globals().get("DEFAULT_HERO_NAMES", ())
    if value in chinese_names:
        value = english_names[chinese_names.index(value)]
    alias = globals().get("DEFAULT_HERO_ALIASES", {}).get(value)
    if alias in english_names:
        value = alias
    elif alias in chinese_names:
        value = english_names[chinese_names.index(alias)]
    special = {
        "Anti-Mage": "antimage",
        "Arc Warden": "arc_warden",
        "Centaur Warrunner": "centaur",
        "Clockwerk": "rattletrap",
        "Doom": "doom_bringer",
        "Io": "wisp",
        "Lifestealer": "life_stealer",
        "Magnus": "magnataur",
        "Necrophos": "necrolyte",
        "Outworld Destroyer": "obsidian_destroyer",
        "Nature's Prophet": "furion",
        "Queen of Pain": "queenofpain",
        "Shadow Fiend": "nevermore",
        "Treant Protector": "treant",
        "Vengeful Spirit": "vengefulspirit",
        "Wraith King": "skeleton_king",
        "Windranger": "windrunner",
        "Timbersaw": "shredder",
        "Underlord": "abyssal_underlord",
        "Techies": "techies",
        "Zeus": "zuus",
    }
    slug = {name.casefold(): slug for name, slug in special.items()}.get(value.casefold())
    if slug is None:
        slug = re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
    return f"npc_dota_hero_{slug}"


DEFAULT_HERO_NAMES = (
    "Abaddon", "Ancient Apparition", "Anti-Mage", "Arc Warden", "Axe",
    "Bane", "Batrider", "Beastmaster", "Bloodseeker", "Bounty Hunter",
    "Brewmaster", "Bristleback", "Broodmother", "Centaur Warrunner",
    "Chaos Knight", "Chen", "Clinkz", "Clockwerk", "Crystal Maiden",
    "Dark Seer", "Dark Willow", "Dawnbreaker", "Dazzle", "Death Prophet",
    "Disruptor", "Doom", "Dragon Knight", "Drow Ranger", "Earth Spirit",
    "Earthshaker", "Elder Titan", "Ember Spirit", "Enchantress", "Enigma",
    "Faceless Void", "Grimstroke", "Gyrocopter", "Hoodwink", "Huskar",
    "Invoker", "Io", "Jakiro", "Juggernaut", "Keeper of the Light",
    "Kunkka", "Legion Commander", "Leshrac", "Lich", "Lifestealer", "Lina",
    "Lion", "Lone Druid", "Luna", "Lycan", "Magnus", "Marci", "Mars",
    "Medusa", "Meepo", "Mirana", "Monkey King", "Morphling", "Muerta",
    "Naga Siren", "Nature's Prophet", "Necrophos", "Night Stalker",
    "Nyx Assassin", "Ogre Magi", "Omniknight", "Oracle", "Outworld Destroyer",
    "Pangolier", "Phantom Assassin", "Phantom Lancer", "Phoenix", "Primal Beast",
    "Puck", "Pudge", "Pugna", "Queen of Pain", "Razor", "Riki", "Rubick",
    "Sand King", "Shadow Demon", "Shadow Fiend", "Shadow Shaman", "Silencer",
    "Skywrath Mage", "Slardar", "Slark", "Snapfire", "Sniper", "Spectre",
    "Spirit Breaker", "Storm Spirit", "Sven", "Techies", "Templar Assassin",
    "Terrorblade", "Tidehunter", "Timbersaw", "Tinker", "Tiny", "Treant Protector",
    "Troll Warlord", "Tusk", "Underlord", "Undying", "Ursa", "Vengeful Spirit",
    "Venomancer", "Viper", "Visage", "Void Spirit", "Warlock", "Weaver",
    "Windranger", "Winter Wyvern", "Witch Doctor", "Wraith King", "Zeus",
    "Kez", "Ringmaster", "Alchemist", "Largo",
)


DEFAULT_HERO_NAMES_ZH = (
    "亚巴顿", "远古冰魄", "敌法师", "天穹守望者", "斧王", "祸乱之源",
    "蝙蝠骑士", "兽王", "嗜血狂魔", "赏金猎人", "酒仙", "刚背兽",
    "育母蜘蛛", "半人马战行者", "混沌骑士", "陈", "克林克兹", "发条技师",
    "水晶室女", "黑暗贤者", "邪影芳灵", "破晓辰星", "戴泽", "死亡先知",
    "干扰者", "末日使者", "龙骑士", "卓尔游侠", "大地之灵", "撼地者",
    "上古巨神", "灰烬之灵", "魅惑魔女", "谜团", "虚空假面", "天涯墨客",
    "矮人直升机", "森海飞霞", "哈斯卡", "祈求者", "艾欧", "杰奇洛",
    "主宰", "光之守卫", "昆卡", "军团指挥官", "拉席克", "巫妖", "噬魂鬼",
    "莉娜", "莱恩", "德鲁伊", "露娜", "狼人", "马格纳斯", "玛西", "马尔斯",
    "美杜莎", "米波", "米拉娜", "齐天大圣", "变体精灵", "琼英碧灵", "娜迦海妖",
    "先知", "死灵法师", "夜魔", "尼克斯刺客", "食人魔魔法师", "全能骑士",
    "神谕者", "殁境神蚀者", "石鳞剑士", "幻影刺客", "幻影长矛手", "凤凰",
    "原始兽", "帕克", "帕吉", "帕格纳", "痛苦女王", "剃刀", "力丸", "拉比克",
    "沙王", "暗影恶魔", "影魔", "暗影萨满", "沉默术士", "天怒法师", "斯拉达",
    "斯拉克", "电炎绝手", "狙击手", "幽鬼", "裂魂人", "风暴之灵", "斯温",
    "工程师", "圣堂刺客", "恐怖利刃", "潮汐猎人", "伐木机", "修补匠", "小小",
    "树精卫士", "巨魔战将", "巨牙海民", "孽主", "不朽尸王", "熊战士", "复仇之魂",
    "剧毒术士", "冥界亚龙", "维萨吉", "虚无之灵", "术士", "编织者", "风行者",
    "寒冬飞龙", "巫医", "骷髅王", "宙斯", "凯兹", "百戏大王", "炼金术士", "朗戈",
)


DEFAULT_HERO_ALIASES = {
    **dict(zip(DEFAULT_HERO_NAMES, DEFAULT_HERO_NAMES_ZH)),
    # Official installed Simplified Chinese localization, reviewed 2026-10-04.
    # Display nicknames and HUD labels must resolve to the same internal ID.
    "血魔": "Bloodseeker",
    "钢背兽": "Bristleback",
    "独行德鲁伊": "Lone Druid",
    "玛尔斯": "Mars",
    "自然先知": "Nature's Prophet",
    "暗夜魔王": "Night Stalker",
    "司夜刺客": "Nyx Assassin",
    "獸": "Primal Beast",
    "冥魂大帝": "Wraith King",
    # RapidOCR read the verified HUD label “莱恩” as “菜恩” in the 1176×664
    # windowed capture from 2026-09-12.  No other Dota hero uses this label.
    "菜恩": "Lion",
    # The current HUD renders Kez as the short Chinese label “凯”; the full
    # roster table uses “凯兹”, so both forms must resolve to one hero ID.
    "凯": "Kez",
    # The current HUD may render Necrophos as “瘟疫法师”.  RapidOCR read the
    # verified 1600×900 frame from 2026-09-14 as “盒 疫法师”; keep that
    # reviewed OCR variant explicit so it cannot win against Anti-Mage merely
    # because both names share the suffix “法师”.
    "瘟疫法师": "Necrophos",
    "盒疫法师": "Necrophos",
    # RapidOCR read the verified 1920x1080 HDMI HUD label “孽主” as “草主”
    # in the 2026-09-23 live capture.  Keep the full two-character variant;
    # the truncated single character “主” is intentionally not aliased because
    # it is also compatible with “主宰” (Juggernaut).
    "草主": "Underlord",
    # Saved 2560x1440 HUDs from 2026-10-10 visibly say “孽主”, while
    # RapidOCR outputs these complete two-character forms. Keep exact aliases;
    # neither the shared “主” nor arbitrary characters before it identify him.
    "艺主": "Underlord",
    "梦主": "Underlord",
    # The live Chinese HUD labels Razor as “雷泽”, while the roster's longer
    # translation is “剃刀”. Without this alias it can fuzzy-match “戴泽”.
    "雷泽": "Razor",
}
