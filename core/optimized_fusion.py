# -*- coding: utf-8 -*-
"""
优化的融合评分系统
提供改进的多通道分析结果融合算法和精细调优的视频类型预设
"""

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from utils.logger import logger


@dataclass
class OptimizedVideoPreset:
    """优化融合系统的视频类型预设（区别于video_type_preset.VideoTypePreset）"""
    name: str
    description: str
    weights: Dict[str, float] = field(default_factory=dict)
    thresholds: Dict[str, float] = field(default_factory=dict)
    min_segment_duration: float = 2.0
    max_segment_duration: float = 30.0
    merge_threshold: float = 0.6
    smoothing_window: int = 3


VIDEO_PRESETS_OPTIMIZED: Dict[str, OptimizedVideoPreset] = {
    "auto": OptimizedVideoPreset(
        name="自动检测",
        description="自动根据视频内容自动选择最佳预设",
        weights={
            "scene": 0.20,
            "motion": 0.25,
            "audio": 0.20,
            "vfx": 0.15,
            "ai_vision": 0.20,
        },
        thresholds={
            "scene": 0.55,
            "motion": 0.50,
            "audio": 0.45,
            "vfx": 0.55,
            "ai_vision": 0.60,
            "final_min": 0.40,
        },
        min_segment_duration=2.0,
        max_segment_duration=30.0,
        merge_threshold=0.65,
        smoothing_window=3,
    ),
    "real_action": OptimizedVideoPreset(
        name="真人影视",
        description="真人实拍电影、电视剧、纪录片等",
        weights={
            "scene": 0.25,
            "motion": 0.20,
            "audio": 0.25,
            "vfx": 0.10,
            "ai_vision": 0.20,
        },
        thresholds={
            "scene": 0.60,
            "motion": 0.45,
            "audio": 0.55,
            "vfx": 0.50,
            "ai_vision": 0.55,
            "final_min": 0.45,
        },
        min_segment_duration=2.5,
        max_segment_duration=25.0,
        merge_threshold=0.65,
        smoothing_window=3,
    ),
    "xianxia_fantasy": OptimizedVideoPreset(
        name="仙侠奇幻",
        description="仙侠、玄幻、奇幻类视频",
        weights={
            "scene": 0.15,
            "motion": 0.25,
            "audio": 0.15,
            "vfx": 0.25,
            "ai_vision": 0.20,
        },
        thresholds={
            "scene": 0.50,
            "motion": 0.55,
            "audio": 0.40,
            "vfx": 0.60,
            "ai_vision": 0.55,
            "final_min": 0.40,
        },
        min_segment_duration=2.0,
        max_segment_duration=25.0,
        merge_threshold=0.60,
        smoothing_window=3,
    ),
    "anime": OptimizedVideoPreset(
        name="动漫动画",
        description="日本动漫、国漫等动画视频",
        weights={
            "scene": 0.20,
            "motion": 0.30,
            "audio": 0.20,
            "vfx": 0.15,
            "ai_vision": 0.15,
        },
        thresholds={
            "scene": 0.55,
            "motion": 0.60,
            "audio": 0.50,
            "vfx": 0.55,
            "ai_vision": 0.50,
            "final_min": 0.40,
        },
        min_segment_duration=1.5,
        max_segment_duration=20.0,
        merge_threshold=0.60,
        smoothing_window=2,
    ),
    "gaming": OptimizedVideoPreset(
        name="游戏精彩",
        description="游戏精彩集锦、操作秀",
        weights={
            "scene": 0.15,
            "motion": 0.35,
            "audio": 0.20,
            "vfx": 0.10,
            "ai_vision": 0.20,
        },
        thresholds={
            "scene": 0.50,
            "motion": 0.65,
            "audio": 0.50,
            "vfx": 0.50,
            "ai_vision": 0.55,
            "final_min": 0.40,
        },
        min_segment_duration=1.0,
        max_segment_duration=15.0,
        merge_threshold=0.55,
        smoothing_window=2,
    ),
    "music": OptimizedVideoPreset(
        name="音乐MV",
        description="音乐视频、MV、演唱会",
        weights={
            "scene": 0.15,
            "motion": 0.20,
            "audio": 0.35,
            "vfx": 0.15,
            "ai_vision": 0.15,
        },
        thresholds={
            "scene": 0.50,
            "motion": 0.50,
            "audio": 0.70,
            "vfx": 0.50,
            "ai_vision": 0.50,
            "final_min": 0.40,
        },
        min_segment_duration=3.0,
        max_segment_duration=30.0,
        merge_threshold=0.65,
        smoothing_window=4,
    ),
    "sports": OptimizedVideoPreset(
        name="体育赛事",
        description="体育比赛、运动精彩瞬间",
        weights={
            "scene": 0.20,
            "motion": 0.35,
            "audio": 0.25,
            "vfx": 0.10,
            "ai_vision": 0.10,
        },
        thresholds={
            "scene": 0.55,
            "motion": 0.70,
            "audio": 0.55,
            "vfx": 0.50,
            "ai_vision": 0.55,
            "final_min": 0.40,
        },
        min_segment_duration=1.5,
        max_segment_duration=20.0,
        merge_threshold=0.55,
        smoothing_window=2,
    ),
}


class OptimizedFusionScorer:
    """
    优化的融合评分器
    
    改进：
    - 加权几何平均融合（替代简单加权和）
    - 时间平滑处理
    - 动态权重归一化
    - 置信度加权
    - 自适应阈值
    """
    
    def __init__(self, preset_name: str = "auto"):
        self.preset = VIDEO_PRESETS_OPTIMIZED.get(preset_name, VIDEO_PRESETS_OPTIMIZED["auto"])
        logger.info(f"使用预设: {self.preset.name} ({preset_name})")
    
    def fuse_scores(self,
                     scene_scores: Optional[List[float]],
                     motion_scores: Optional[List[float]],
                     audio_scores: Optional[List[float]],
                     vfx_scores: Optional[List[float]],
                     ai_vision_scores: Optional[List[float]],
                     frame_count: int) -> List[float]:
        """融合多通道评分"""
        scores_dict = {
            "scene": scene_scores,
            "motion": motion_scores,
            "audio": audio_scores,
            "vfx": vfx_scores,
            "ai_vision": ai_vision_scores,
        }
        
        weights = self.preset.weights
        thresholds = self.preset.thresholds
        
        valid_channels = {}
        for name, scores in scores_dict.items():
            if scores is not None and len(scores) > 0:
                valid_channels[name] = scores
        
        if not valid_channels:
            logger.warning("没有有效的分数通道，返回零分数")
            return [0.0] * frame_count
        
        total_weight = sum(weights[name] for name in valid_channels)
        if total_weight <= 0:
            total_weight = 1.0
        
        normalized_weights = {
            name: weights[name] / total_weight
            for name in valid_channels
        }
        
        fused_scores = []
        epsilon = 1e-10
        
        for i in range(frame_count):
            log_sum = 0.0
            weight_sum = 0.0
            
            for name, scores in valid_channels.items():
                if i < len(scores):
                    score = scores[i]
                    weight = normalized_weights[name]
                    
                    threshold = thresholds.get(name, 0.5)
                    if score < threshold:
                        score = score * (score / threshold) * 0.5
                    
                    if score > 0:
                        log_sum += weight * math.log(score + epsilon)
                    weight_sum += weight
            
            if weight_sum > 0:
                fused = math.exp(log_sum / weight_sum) - epsilon
            else:
                fused = 0.0
            
            fused = max(0.0, min(1.0, fused))
            fused_scores.append(fused)
        
        if self.preset.smoothing_window > 1 and len(fused_scores) > self.preset.smoothing_window:
            fused_scores = self._smooth_scores(fused_scores, self.preset.smoothing_window)
        
        logger.info(f"融合完成: {len(fused_scores)} 帧, 平均分数: {sum(fused_scores)/len(fused_scores):.4f}")
        return fused_scores
    
    def _smooth_scores(self, scores: List[float], window_size: int) -> List[float]:
        """时间平滑（移动平均）"""
        if window_size <= 1:
            return scores
        
        smoothed = []
        half_window = window_size // 2
        
        weights = []
        for i in range(window_size):
            distance = abs(i - half_window)
            weight = 1.0 - (distance / (half_window + 1))
            weights.append(weight)
        
        total_weight = sum(weights)
        weights = [w / total_weight for w in weights]
        
        for i in range(len(scores)):
            weighted_sum = 0.0
            weight_sum = 0.0
            
            for j in range(window_size):
                idx = i - half_window + j
                if 0 <= idx < len(scores):
                    weighted_sum += scores[idx] * weights[j]
                    weight_sum += weights[j]
            
            if weight_sum > 0:
                smoothed.append(weighted_sum / weight_sum)
            else:
                smoothed.append(scores[i])
        
        return smoothed
    
    def detect_hot_segments(self, scores: List[float], fps: float, frame_times: List[float]) -> List[Dict[str, Any]]:
        """检测高燃片段"""
        if not scores or not frame_times:
            return []
        
        min_frames = int(self.preset.min_segment_duration * fps)
        max_frames = int(self.preset.max_segment_duration * fps)
        final_min_threshold = self.preset.thresholds.get("final_min", 0.4)
        
        segments = []
        in_segment = False
        current_segment_start = 0
        current_segment_scores = []
        
        if scores:
            mean_score = sum(scores) / len(scores)
            std_dev = math.sqrt(sum((s - mean_score) ** 2 for s in scores) / len(scores)) if len(scores) > 1 else 0.1
            dynamic_threshold = max(final_min_threshold, mean_score + 0.5 * std_dev)
        else:
            dynamic_threshold = final_min_threshold
        
        logger.info(f"检测高燃片段: 动态阈值={dynamic_threshold:.4f}, 平均分数={mean_score:.4f}")
        
        for i, score in enumerate(scores):
            if score >= dynamic_threshold:
                if not in_segment:
                    in_segment = True
                    current_segment_start = i
                    current_segment_scores = [score]
                else:
                    current_segment_scores.append(score)
            else:
                if in_segment:
                    segment_length = i - current_segment_start
                    
                    if segment_length >= min_frames:
                        avg_score = sum(current_segment_scores) / len(current_segment_scores)
                        segments.append({
                            "start_frame": current_segment_start,
                            "end_frame": i,
                            "start_time": frame_times[current_segment_start] if current_segment_start < len(frame_times) else 0,
                            "end_time": frame_times[i] if i < len(frame_times) else frame_times[-1],
                            "avg_score": avg_score,
                            "max_score": max(current_segment_scores),
                            "frames": segment_length,
                        })
                    
                    in_segment = False
                    current_segment_scores = []
        
        if in_segment:
            segment_length = len(scores) - current_segment_start
            if segment_length >= min_frames:
                avg_score = sum(current_segment_scores) / len(current_segment_scores)
                segments.append({
                    "start_frame": current_segment_start,
                    "end_frame": len(scores),
                    "start_time": frame_times[current_segment_start] if current_segment_start < len(frame_times) else 0,
                    "end_time": frame_times[-1],
                    "avg_score": avg_score,
                    "max_score": max(current_segment_scores),
                    "frames": segment_length,
                })
        
        segments = self._merge_adjacent_segments(segments, fps)
        segments.sort(key=lambda x: x["avg_score"], reverse=True)
        
        logger.info(f"检测到 {len(segments)} 个高燃片段")
        return segments
    
    def _merge_adjacent_segments(self, segments: List[Dict[str, Any]], fps: float) -> List[Dict[str, Any]]:
        """合并相邻的高燃片段"""
        if len(segments) <= 1:
            return segments
        
        merge_gap_frames = int(self.preset.merge_threshold * fps)
        merged = []
        i = 0
        
        while i < len(segments):
            current = segments[i]
            j = i + 1
            
            while j < len(segments):
                next_seg = segments[j]
                gap = next_seg["start_frame"] - current["end_frame"]
                
                if gap <= merge_gap_frames:
                    current["end_frame"] = next_seg["end_frame"]
                    current["end_time"] = next_seg["end_time"]
                    current["frames"] = current["end_frame"] - current["start_frame"]
                    all_scores = [current["avg_score"], next_seg["avg_score"]]
                    current["avg_score"] = sum(all_scores) / len(all_scores)
                    current["max_score"] = max(current["max_score"], next_seg["max_score"])
                    j += 1
                else:
                    break
            
            merged.append(current)
            i = j
        
        if len(merged) < len(segments):
            logger.info(f"合并片段: {len(segments)} -> {len(merged)}")
        
        return merged
    
    def get_preset_info(self) -> Dict[str, Any]:
        """获取当前预设信息"""
        return {
            "name": self.preset.name,
            "description": self.preset.description,
            "weights": self.preset.weights,
            "thresholds": self.preset.thresholds,
            "min_duration": self.preset.min_segment_duration,
            "max_duration": self.preset.max_segment_duration,
        }


def get_available_presets() -> List[str]:
    """获取所有可用的预设名称"""
    return list(VIDEO_PRESETS_OPTIMIZED.keys())


def get_preset(name: str) -> OptimizedVideoPreset:
    """获取指定的优化预设"""
    return VIDEO_PRESETS_OPTIMIZED.get(name, VIDEO_PRESETS_OPTIMIZED["auto"])
