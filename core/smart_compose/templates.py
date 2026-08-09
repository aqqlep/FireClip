"""
剪辑模板配置
不同类型视频采用不同剪辑节奏/转场/BGM风格
支持：解说类视频（带配音）、纯混剪类视频（卡点/快切）
"""
from dataclasses import dataclass, field
from typing import List, Tuple, Dict, Optional


@dataclass
class ComposeTemplate:
    """剪辑模板配置"""
    name: str
    display_name: str
    category: str = "mix"  # mix=纯混剪, commentary=解说视频
    
    # 解说配置（仅commentary类型生效）
    enable_tts: bool = False
    commentary_style: str = ""  # passionate=激情解说, emotional=情感解说, humorous=搞笑解说, calm=平静解说
    subtitle_enabled: bool = False
    original_audio_volume: float = 0.3  # 解说时原声音量
    
    # 片段内容偏好配置 (权重0-1，越高越优先选择)
    content_preference: Dict[str, float] = field(default_factory=lambda: {
        "action": 0.5,      # 打斗/动作
        "dialogue": 0.3,    # 对话/剧情
        "emotion": 0.3,     # 情感表达
        "funny": 0.2,       # 搞笑
        "famous_scene": 0.4, # 名场面
        "effect": 0.4,      # 特效画面
        "landscape": 0.2,   # 风景/空镜
    })
    
    # 片段时长配置
    min_clip_duration: float = 1.5
    max_clip_duration: float = 4.0
    target_avg_clip_duration: float = 2.5
    
    # 节奏配置
    beat_align: bool = True
    beat_align_offset_ms: int = 0
    speed_variation: bool = False  # 是否启用变速（快慢镜）
    fast_cut: bool = False  # 是否启用快切模式（更短片段）
    beat_cut: bool = False  # 严格按节拍点切换（卡点视频）
    
    # 视频特效配置
    enable_effects: bool = False
    flash_white_frequency: float = 0.0  # 闪白频率 0-1
    zoom_pulse: bool = False  # 心跳缩放效果
    color_grading: str = "none"  # none, warm, cool, high_contrast, vintage
    
    # 转场配置
    transition_type: str = "hard"  # hard/cut, fade(黑场淡入淡出), fadewhite(闪白)
    transition_duration_ms: int = 0
    
    @property
    def transition_duration(self) -> float:
        """转场时长（秒）"""
        if self.transition_type in ("hard", "cut", ""):
            return 0.0
        return max(0.1, self.transition_duration_ms / 1000.0)
    
    # BGM配置
    bgm_genre: str = "electronic"
    bgm_volume: float = 0.3
    audio_ducking: bool = True
    ducking_db: int = 18
    
    # 片段选择配置
    top_n_ratio: float = 0.3
    min_vfx_score: float = 0.0
    min_motion_score: float = 0.0
    require_audio_peak: bool = False
    avoid_consecutive_similar: bool = True  # 避免连续相似场景
    
    # 情绪曲线配置
    opening_duration_sec: float = 3.0
    climax_buildup: bool = True
    ending_duration_sec: float = 2.0


# ========== 纯混剪类模板 ==========

TEMPLATE_HOT_FIGHT = ComposeTemplate(
    name="hot_fight",
    display_name="高燃打斗集锦",
    category="mix",
    min_clip_duration=1.0,
    max_clip_duration=3.0,
    target_avg_clip_duration=2.0,
    beat_align=True,
    beat_cut=True,
    speed_variation=True,
    transition_type="hard",
    transition_duration_ms=0,
    bgm_genre="rock",
    bgm_volume=0.4,
    audio_ducking=False,
    ducking_db=18,
    top_n_ratio=0.4,
    min_motion_score=40.0,
    content_preference={"action": 1.0, "effect": 0.8, "famous_scene": 0.5, "dialogue": 0.1, "emotion": 0.1, "funny": 0.0},
    opening_duration_sec=2.0,
    climax_buildup=True,
    ending_duration_sec=2.0,
)

TEMPLATE_XIANXIA_EFFECT = ComposeTemplate(
    name="xianxia_effect",
    display_name="仙侠特效集锦",
    category="mix",
    min_clip_duration=2.0,
    max_clip_duration=5.0,
    target_avg_clip_duration=3.0,
    beat_align=True,
    transition_type="hard",
    transition_duration_ms=0,
    bgm_genre="epic",
    bgm_volume=0.35,
    audio_ducking=True,
    enable_effects=True,
    color_grading="cool",
    speed_variation=True,
    top_n_ratio=0.35,
    min_vfx_score=50.0,
    content_preference={"effect": 1.0, "action": 0.7, "famous_scene": 0.6, "dialogue": 0.2, "emotion": 0.3, "landscape": 0.4},
    opening_duration_sec=3.0,
    climax_buildup=True,
    ending_duration_sec=3.0,
)

TEMPLATE_BEAT_CUT = ComposeTemplate(
    name="beat_cut",
    display_name="卡点快剪",
    category="mix",
    min_clip_duration=0.4,
    max_clip_duration=1.2,
    target_avg_clip_duration=0.6,
    beat_align=True,
    beat_cut=True,
    fast_cut=True,
    transition_type="hard",
    transition_duration_ms=0,
    bgm_genre="electronic",
    bgm_volume=0.45,
    audio_ducking=False,
    top_n_ratio=0.6,
    min_motion_score=20.0,
    avoid_consecutive_similar=False,
    content_preference={"action": 0.9, "effect": 0.8, "famous_scene": 0.7, "funny": 0.4, "dialogue": 0.1, "emotion": 0.1},
    opening_duration_sec=0.5,
    climax_buildup=False,
    ending_duration_sec=1.0,
)

TEMPLATE_CINEMATIC_MIX = ComposeTemplate(
    name="cinematic_mix",
    display_name="电影感混剪",
    category="mix",
    min_clip_duration=2.0,
    max_clip_duration=6.0,
    target_avg_clip_duration=3.5,
    beat_align=True,
    transition_type="hard",
    transition_duration_ms=0,
    bgm_genre="epic",
    bgm_volume=0.3,
    audio_ducking=True,
    ducking_db=15,
    enable_effects=True,
    color_grading="high_contrast",
    speed_variation=True,
    top_n_ratio=0.3,
    content_preference={"famous_scene": 1.0, "emotion": 0.8, "action": 0.6, "effect": 0.5, "landscape": 0.7, "dialogue": 0.5},
    opening_duration_sec=4.0,
    climax_buildup=True,
    ending_duration_sec=4.0,
)

# ========== 解说类视频模板 ==========

TEMPLATE_PASSIONATE_COMMENTARY = ComposeTemplate(
    name="passionate_commentary",
    display_name="高燃解说",
    category="commentary",
    enable_tts=True,
    commentary_style="passionate",
    subtitle_enabled=True,
    original_audio_volume=0.2,
    min_clip_duration=2.0,
    max_clip_duration=5.0,
    target_avg_clip_duration=3.0,
    beat_align=True,
    speed_variation=True,
    transition_type="hard",
    transition_duration_ms=0,
    bgm_genre="rock",
    bgm_volume=0.18,
    audio_ducking=True,
    ducking_db=20,
    top_n_ratio=0.4,
    min_motion_score=20.0,
    content_preference={"action": 1.0, "famous_scene": 0.8, "effect": 0.7, "dialogue": 0.4, "emotion": 0.3, "funny": 0.1},
    opening_duration_sec=3.0,
    climax_buildup=True,
    ending_duration_sec=2.0,
)

TEMPLATE_EMOTIONAL_COMMENTARY = ComposeTemplate(
    name="emotional_commentary",
    display_name="情感解说",
    category="commentary",
    enable_tts=True,
    commentary_style="emotional",
    subtitle_enabled=True,
    original_audio_volume=0.45,
    min_clip_duration=3.0,
    max_clip_duration=8.0,
    target_avg_clip_duration=5.0,
    beat_align=False,
    speed_variation=True,
    transition_type="hard",
    transition_duration_ms=0,
    bgm_genre="emotional",
    bgm_volume=0.2,
    audio_ducking=True,
    ducking_db=12,
    top_n_ratio=0.25,
    content_preference={"dialogue": 1.0, "emotion": 1.0, "famous_scene": 0.7, "landscape": 0.6, "action": 0.1, "effect": 0.1},
    opening_duration_sec=3.0,
    climax_buildup=False,
    ending_duration_sec=4.0,
)

TEMPLATE_FUNNY_COMMENTARY = ComposeTemplate(
    name="funny_commentary",
    display_name="搞笑解说",
    category="commentary",
    enable_tts=True,
    commentary_style="humorous",
    subtitle_enabled=True,
    original_audio_volume=0.5,
    min_clip_duration=1.5,
    max_clip_duration=5.0,
    target_avg_clip_duration=3.0,
    beat_align=False,
    transition_type="hard",
    transition_duration_ms=0,
    bgm_genre="funny",
    bgm_volume=0.15,
    audio_ducking=True,
    ducking_db=12,
    top_n_ratio=0.35,
    content_preference={"funny": 1.0, "dialogue": 0.8, "famous_scene": 0.5, "action": 0.3, "emotion": 0.2, "effect": 0.1},
    opening_duration_sec=2.0,
    climax_buildup=False,
    ending_duration_sec=2.0,
)

TEMPLATE_FAMOUS_SCENE_COMMENTARY = ComposeTemplate(
    name="famous_scene_commentary",
    display_name="名场面解说",
    category="commentary",
    enable_tts=True,
    commentary_style="calm",
    subtitle_enabled=True,
    original_audio_volume=0.35,
    min_clip_duration=2.5,
    max_clip_duration=7.0,
    target_avg_clip_duration=4.0,
    beat_align=False,
    speed_variation=True,
    transition_type="hard",
    transition_duration_ms=0,
    bgm_genre="calm",
    bgm_volume=0.2,
    audio_ducking=True,
    ducking_db=15,
    top_n_ratio=0.3,
    content_preference={"famous_scene": 1.0, "dialogue": 0.7, "emotion": 0.6, "effect": 0.5, "action": 0.4, "landscape": 0.4},
    opening_duration_sec=3.0,
    climax_buildup=True,
    ending_duration_sec=3.0,
)

# ========== 旧版模板兼容保留 ==========
TEMPLATE_HIGHLIGHT_FUNNY = ComposeTemplate(
    name="highlight_funny",
    display_name="搞笑高光集锦",
    category="mix",
    min_clip_duration=2.0,
    max_clip_duration=6.0,
    target_avg_clip_duration=3.5,
    beat_align=False,
    transition_type="hard",
    transition_duration_ms=0,
    bgm_genre="funny",
    bgm_volume=0.25,
    audio_ducking=True,
    ducking_db=12,
    top_n_ratio=0.3,
    content_preference={"funny": 1.0, "dialogue": 0.5, "action": 0.2},
    opening_duration_sec=2.0,
    climax_buildup=False,
    ending_duration_sec=2.0,
)

TEMPLATE_EMOTION_HIGHLIGHT = ComposeTemplate(
    name="emotion_highlight",
    display_name="情感高光集锦",
    category="mix",
    min_clip_duration=2.5,
    max_clip_duration=7.0,
    target_avg_clip_duration=4.0,
    beat_align=False,
    transition_type="hard",
    transition_duration_ms=0,
    bgm_genre="emotional",
    bgm_volume=0.25,
    audio_ducking=True,
    ducking_db=10,
    speed_variation=True,
    top_n_ratio=0.25,
    content_preference={"emotion": 1.0, "dialogue": 0.8, "landscape": 0.5},
    opening_duration_sec=3.0,
    climax_buildup=False,
    ending_duration_sec=4.0,
)


ALL_TEMPLATES = [
    # 解说类
    TEMPLATE_PASSIONATE_COMMENTARY,
    TEMPLATE_EMOTIONAL_COMMENTARY,
    TEMPLATE_FUNNY_COMMENTARY,
    TEMPLATE_FAMOUS_SCENE_COMMENTARY,
    # 混剪类
    TEMPLATE_HOT_FIGHT,
    TEMPLATE_BEAT_CUT,
    TEMPLATE_CINEMATIC_MIX,
    TEMPLATE_XIANXIA_EFFECT,
    TEMPLATE_HIGHLIGHT_FUNNY,
    TEMPLATE_EMOTION_HIGHLIGHT,
]


def get_templates_by_category(category: str) -> List[ComposeTemplate]:
    """按分类获取模板列表"""
    return [t for t in ALL_TEMPLATES if t.category == category]


def get_all_categories() -> List[Tuple[str, str]]:
    """获取所有分类 [(key, display_name)]"""
    return [
        ("commentary", "解说视频"),
        ("mix", "纯混剪"),
    ]


def get_template_by_name(name: str) -> ComposeTemplate:
    for t in ALL_TEMPLATES:
        if t.name == name:
            return t
    return TEMPLATE_HOT_FIGHT
