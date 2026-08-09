"""
智能成片模块 v1.2 - 音画同步稳定性优化版
模块化独立架构，与高燃/高光提取功能完全解耦

支持两种使用模式:
1. compose_from_video(): 直接导入原始视频，一键成片
2. compose_from_segments(): 基于已提取/勾选的片段，合成成片

v1.2 更新:
- 重构音视频处理流程：截取时保留原声音频，同步应用setpts+atempo变速
- 转场支持音频acrossfade交叉淡化，实现音视频平滑过渡
- 大幅简化最终混音流程，避免原声二次拼接导致的音画不同步
- 修复片段合并时metadata丢失问题
- 添加多层降级容错机制：转场失败降级硬切，变速失败降级1x，混音失败降级原声
- 严格限制变速范围(0.5x-2.0x)，兼容atempo滤镜限制
"""
from core.smart_compose.smart_compose_engine import SmartComposeEngine, SmartComposeConfig, SmartComposeResult
from core.smart_compose.templates import (
    ComposeTemplate,
    ALL_TEMPLATES,
    TEMPLATE_HOT_FIGHT,
    TEMPLATE_HIGHLIGHT_FUNNY,
    TEMPLATE_XIANXIA_EFFECT,
    TEMPLATE_BEAT_CUT,
    TEMPLATE_CINEMATIC_MIX,
    TEMPLATE_EMOTION_HIGHLIGHT,
    TEMPLATE_PASSIONATE_COMMENTARY,
    TEMPLATE_EMOTIONAL_COMMENTARY,
    TEMPLATE_FUNNY_COMMENTARY,
    TEMPLATE_FAMOUS_SCENE_COMMENTARY,
    get_template_by_name,
    get_templates_by_category,
    get_all_categories,
)

__all__ = [
    'SmartComposeEngine',
    'SmartComposeConfig', 
    'SmartComposeResult',
    'ComposeTemplate',
    'ALL_TEMPLATES',
    'TEMPLATE_HOT_FIGHT',
    'TEMPLATE_HIGHLIGHT_FUNNY',
    'TEMPLATE_XIANXIA_EFFECT',
    'TEMPLATE_BEAT_CUT',
    'TEMPLATE_CINEMATIC_MIX',
    'TEMPLATE_EMOTION_HIGHLIGHT',
    'TEMPLATE_PASSIONATE_COMMENTARY',
    'TEMPLATE_EMOTIONAL_COMMENTARY',
    'TEMPLATE_FUNNY_COMMENTARY',
    'TEMPLATE_FAMOUS_SCENE_COMMENTARY',
    'get_template_by_name',
    'get_templates_by_category',
    'get_all_categories',
]

__version__ = '1.2.0'
