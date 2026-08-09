"""
片段智能选择与排序模块 v1.0
- top-N筛选
- 情绪曲线排序（平缓开场→逐步上升→高潮爆发→收尾）
- 时长自动调整
- 节拍对齐
"""
import random
from dataclasses import dataclass
from typing import List, Tuple, Optional, Dict
from core.smart_compose.templates import ComposeTemplate
from core.smart_compose.beat_detector import BeatAnalysisResult


@dataclass
class SelectedClip:
    """选中的片段"""
    start_time: float
    end_time: float
    score: float
    clip_type: str = "hot_fire"
    vfx_score: float = 0.0
    motion_score: float = 0.0
    audio_score: float = 0.0
    metadata: Dict = None
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}
    
    @property
    def duration(self) -> float:
        return max(0.1, self.end_time - self.start_time)


class ClipSelector:
    """片段智能选择器"""
    
    def __init__(self, random_seed: int = 42):
        random.seed(random_seed)
    
    def select_and_order(
        self,
        segments: List[SelectedClip],
        template: ComposeTemplate,
        target_total_duration: Optional[float] = None,
        beat_result: Optional[BeatAnalysisResult] = None,
        use_all_segments: bool = False,
    ) -> List[SelectedClip]:
        """
        选择并排序片段
        
        Args:
            segments: 候选片段列表（已通过质量过滤）
            template: 使用的剪辑模板
            target_total_duration: 目标总时长，None则自动决定
            beat_result: 节拍分析结果（用于对齐）
            use_all_segments: 是否使用所有片段（用户手动勾选时为True，不做top-N筛选）
        
        Returns:
            排序好的最终片段列表
        """
        if not segments:
            return []
        
        if use_all_segments:
            logger_info = f"用户手动选择模式，保留所有 {len(segments)} 个片段"
            try:
                from utils.logger import logger
                logger.info(f"[ClipSelector] {logger_info}")
            except:
                pass
            ordered = self._arrange_by_emotion_curve(segments, template)
            final_clips = self._adjust_durations(ordered, template, beat_result)
            return final_clips
        
        scored = self._filter_by_template(segments, template)
        
        if not scored:
            scored = segments
        
        if target_total_duration is None:
            target_total_duration = min(
                60.0,
                sum(s.duration for s in scored) * template.top_n_ratio
            )
        
        selected = self._select_top_n(scored, template, target_total_duration)
        
        ordered = self._arrange_by_emotion_curve(selected, template)
        
        final_clips = self._adjust_durations(ordered, template, beat_result)
        
        return final_clips
    
    def _get_content_type(self, seg: SelectedClip) -> str:
        """根据片段元数据推断内容类型"""
        clip_type = seg.clip_type.lower()
        reason = str(seg.metadata.get("reason", "")).lower()
        scene_type = str(seg.metadata.get("scene_type", "")).lower()
        
        if "funny" in clip_type or "搞笑" in reason or "笑" in reason:
            return "funny"
        if "dialogue" in clip_type or "对话" in reason or "台词" in reason or "剧情" in reason:
            return "dialogue"
        if "emotion" in clip_type or "情感" in reason or "哭" in reason or "悲伤" in reason or "感动" in reason:
            return "emotion"
        if "famous" in clip_type or "名场面" in reason or "经典" in reason:
            return "famous_scene"
        if seg.vfx_score > 60 or "特效" in reason or "vfx" in scene_type:
            return "effect"
        if seg.motion_score > 70 or "fight" in clip_type or "战斗" in reason or "打斗" in reason or "action" in scene_type:
            return "action"
        if seg.motion_score < 20 and seg.audio_score < 30:
            return "landscape"
        
        return "action"
    
    def _calculate_content_weighted_score(self, seg: SelectedClip, template: ComposeTemplate) -> float:
        """计算结合内容偏好的加权得分"""
        base_score = seg.score
        content_type = self._get_content_type(seg)
        preference_weight = template.content_preference.get(content_type, 0.3)
        
        weighted_score = base_score * (0.4 + preference_weight * 0.6)
        return weighted_score
    
    def _filter_by_template(
        self,
        segments: List[SelectedClip],
        template: ComposeTemplate,
    ) -> List[SelectedClip]:
        """按模板要求预过滤片段，并根据内容偏好重新加权评分"""
        filtered = []
        
        for seg in segments:
            if template.min_vfx_score > 0 and seg.vfx_score < template.min_vfx_score:
                continue
            if template.min_motion_score > 0 and seg.motion_score < template.min_motion_score:
                continue
            
            if seg.duration < template.min_clip_duration * 0.5:
                continue
            
            content_type = self._get_content_type(seg)
            preference_weight = template.content_preference.get(content_type, 0.3)
            
            if preference_weight < 0.1 and len(segments) > 20:
                continue
            
            weighted_score = self._calculate_content_weighted_score(seg, template)
            seg.score = weighted_score
            seg.metadata["content_type"] = content_type
            
            filtered.append(seg)
        
        return filtered
    
    def _select_top_n(
        self,
        segments: List[SelectedClip],
        template: ComposeTemplate,
        target_total_duration: float,
    ) -> List[SelectedClip]:
        """选择top-N片段，直到凑够目标时长"""
        sorted_segs = sorted(segments, key=lambda x: x.score, reverse=True)
        
        selected = []
        total_dur = 0.0
        
        for seg in sorted_segs:
            if total_dur >= target_total_duration * 1.2:
                break
            
            seg_duration = min(seg.duration, template.max_clip_duration * 1.5)
            if total_dur + seg_duration > target_total_duration * 1.5:
                continue
            
            selected.append(seg)
            total_dur += seg_duration
        
        if not selected and sorted_segs:
            selected = [sorted_segs[0]]
        
        return selected
    
    def _arrange_by_emotion_curve(
        self,
        clips: List[SelectedClip],
        template: ComposeTemplate,
    ) -> List[SelectedClip]:
        """
        按情绪曲线排列片段：
        - 开头：中等分数片段，吸引注意力但不直接放最高潮
        - 中段：分数逐步提升，高低搭配避免单调
        - 结尾：最高潮片段放在最后
        """
        if len(clips) <= 2 or not template.climax_buildup:
            return sorted(clips, key=lambda x: x.start_time)
        
        sorted_by_score = sorted(clips, key=lambda x: x.score, reverse=True)
        
        climax_clips = sorted_by_score[:max(1, len(sorted_by_score)//5)]
        remaining = sorted_by_score[max(1, len(sorted_by_score)//5):]
        
        opening_candidates = [c for c in remaining if c.motion_score < 70 or c.vfx_score < 70]
        if not opening_candidates:
            opening_candidates = remaining[:2]
        
        opening = opening_candidates[:max(1, len(opening_candidates)//4)]
        middle = [c for c in remaining if c not in opening]
        
        random.shuffle(middle)
        
        ordered = []
        ordered.extend(sorted(opening, key=lambda x: x.score))
        
        for i in range(0, len(middle), 2):
            group = middle[i:i+2]
            ordered.extend(sorted(group, key=lambda x: x.score))
        
        ordered.extend(sorted(climax_clips, key=lambda x: x.score))
        
        return ordered
    
    def _adjust_durations(
        self,
        clips: List[SelectedClip],
        template: ComposeTemplate,
        beat_result: Optional[BeatAnalysisResult] = None,
    ) -> List[SelectedClip]:
        """简化：严格限制每个片段时长在[min_clip, max_clip]之间，不做节拍对齐"""
        adjusted = []
        
        for clip in clips:
            new_start = clip.start_time
            new_end = clip.end_time
            clip_dur = clip.duration
            
            max_dur = min(template.max_clip_duration, 5.0)
            min_dur = max(template.min_clip_duration, 2.0)
            target_dur = min(max(template.target_avg_clip_duration, min_dur), max_dur)
            
            if clip_dur > max_dur:
                center = (clip.start_time + clip.end_time) / 2
                half_dur = target_dur / 2
                new_start = center - half_dur
                new_end = center + half_dur
            elif clip_dur < min_dur:
                center = (clip.start_time + clip.end_time) / 2
                half_dur = min_dur / 2
                new_start = center - half_dur
                new_end = center + half_dur
            
            new_start = max(0.0, new_start)
            new_end = max(new_start + min_dur, new_end)
            
            adjusted.append(SelectedClip(
                start_time=new_start,
                end_time=new_end,
                score=clip.score,
                clip_type=clip.clip_type,
                vfx_score=clip.vfx_score,
                motion_score=clip.motion_score,
                audio_score=clip.audio_score,
                metadata=clip.metadata,
            ))
        
        return adjusted
    
    def _find_peak_time(self, clip: SelectedClip) -> float:
        """找到片段内的峰值位置（用于超长片段截取中心）"""
        peak_score = clip.vfx_score * 0.5 + clip.motion_score * 0.3 + clip.audio_score * 0.2
        center = (clip.start_time + clip.end_time) / 2
        
        if abs(peak_score - 0.5) < 0.2:
            rand_offset = random.uniform(-0.5, 0.5)
            return center + rand_offset
        
        return center
    
    def _merge_adjacent_if_needed(self, clips: List[SelectedClip]) -> List[SelectedClip]:
        """合并真正重叠的相邻片段（间隔小于0说明重叠，仅合并重叠部分，不合并首尾相接的片段）"""
        if len(clips) < 2:
            return clips
        
        merged = [clips[0]]
        
        for clip in clips[1:]:
            last = merged[-1]
            
            if clip.start_time < last.end_time - 0.05:
                new_start = min(last.start_time, clip.start_time)
                new_end = max(last.end_time, clip.end_time)
                merged_metadata = last.metadata.copy() if last.metadata else {}
                if clip.metadata:
                    merged_metadata.update(clip.metadata)
                merged[-1] = SelectedClip(
                    start_time=new_start,
                    end_time=new_end,
                    score=max(last.score, clip.score),
                    clip_type=last.clip_type,
                    vfx_score=max(last.vfx_score, clip.vfx_score),
                    motion_score=max(last.motion_score, clip.motion_score),
                    audio_score=max(last.audio_score, clip.audio_score),
                    metadata=merged_metadata,
                )
            else:
                merged.append(clip)
        
        return merged
