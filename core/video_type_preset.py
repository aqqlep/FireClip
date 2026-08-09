"""
视频类型预设
定义不同视频类型的检测权重和阈值
"""
from dataclasses import dataclass
from typing import Dict


@dataclass
class VideoTypePreset:
    """视频类型预设"""
    name: str
    description: str
    
    # 7通道权重（总和为1.0）v2.8新增 clip_vision
    weights: Dict[str, float]
    
    # 检测阈值
    thresholds: Dict[str, float]
    
    # 最小片段时长（秒）
    min_duration: float
    
    # 最大片段时长（秒）
    max_duration: float


# 预设配置
PRESETS = {
    "auto": VideoTypePreset(
        name="自动检测",
        description="自动分析视频类型并应用最佳配置",
        weights={
            "scene_change": 0.07,
            "motion": 0.22,              # 光流运动
            "audio_energy": 0.13,
            "color_burst": 0.20,         # 色彩突变
            "brightness_flash": 0.11,    # 亮度光效
            "ai_vision": 0.07,           # AI视觉（Qwen2-VL）
            "clip_vision": 0.20          # v2.8: CLIP语义评分（内容理解）
        },
        thresholds={
            "hot": 0.30,      # v2.8: 降低阈值（反差加成会自动突出真正燃点）
            "highlight": 0.22 # v2.8: 降低高光阈值
        },
        min_duration=5.0,    # v3.2: 最小5秒（v2.8是3秒，过滤掉1-2秒无意义片段）
        max_duration=30.0
    ),
    
    "real_action": VideoTypePreset(
        name="真人动作",
        description="真人电影/电视剧的动作场景（打戏、枪战、追逐等）",
        weights={
            "scene_change": 0.08,
            "motion": 0.28,              # 光流运动（动作核心）
            "audio_energy": 0.20,
            "color_burst": 0.04,
            "brightness_flash": 0.04,
            "ai_vision": 0.16,
            "clip_vision": 0.20          # CLIP语义（识别打斗/枪战）
        },
        thresholds={
            "hot": 0.5,
            "highlight": 0.4
        },
        min_duration=5.0,
        max_duration=30.0
    ),
    
    "xianxia_fantasy": VideoTypePreset(
        name="仙侠奇幻",
        description="仙侠/奇幻类短剧（法术、特效、能量波、飞剑、招式特效等）",
        weights={
            "scene_change": 0.05,
            "motion": 0.15,
            "audio_energy": 0.10,
            "color_burst": 0.28,         # 仙侠核心特征
            "brightness_flash": 0.17,    # 法术光效
            "ai_vision": 0.10,
            "clip_vision": 0.15          # CLIP语义（识别法术/特效）
        },
        thresholds={
            "hot": 0.28,        # v2.8: 降低阈值
            "highlight": 0.20
        },
        min_duration=5.0,
        max_duration=25.0
    ),
    
    "anime_cartoon": VideoTypePreset(
        name="漫剧动画",
        description="动漫/动画作品（打斗、特效、热血场景）",
        weights={
            "scene_change": 0.12,
            "motion": 0.25,              # 光流运动
            "audio_energy": 0.17,
            "color_burst": 0.12,
            "brightness_flash": 0.08,
            "ai_vision": 0.08,
            "clip_vision": 0.18          # CLIP语义（识别热血场景）
        },
        thresholds={
            "hot": 0.5,
            "highlight": 0.4
        },
        min_duration=5.0,
        max_duration=25.0
    ),
    
    "modern_urban": VideoTypePreset(
        name="现代都市",
        description="现代都市题材（枪战、追车、爆炸等）",
        weights={
            "scene_change": 0.08,
            "motion": 0.25,              # 光流运动
            "audio_energy": 0.25,
            "color_burst": 0.08,
            "brightness_flash": 0.08,
            "ai_vision": 0.08,
            "clip_vision": 0.18          # CLIP语义（识别爆炸/追车）
        },
        thresholds={
            "hot": 0.55,
            "highlight": 0.45
        },
        min_duration=5.0,
        max_duration=30.0
    )
}


def get_preset(preset_name: str) -> VideoTypePreset:
    """获取预设配置"""
    return PRESETS.get(preset_name, PRESETS["auto"])


def list_presets() -> Dict[str, str]:
    """列出所有预设"""
    return {name: preset.description for name, preset in PRESETS.items()}


def get_all_presets() -> Dict[str, VideoTypePreset]:
    """获取所有预设配置"""
    return PRESETS

