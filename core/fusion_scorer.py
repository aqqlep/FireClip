"""
融合评分引擎
将6通道的检测结果进行加权融合，生成最终评分
"""
import numpy as np
from typing import List, Dict, Optional
from dataclasses import dataclass
from core.video_type_preset import get_preset, VideoTypePreset
from utils.logger import logger


@dataclass
class FusionResult:
    """融合评分结果"""
    __slots__ = ('time', 'duration', 'score', 'scene_type', 'action_type', 'description', 'tags', 'channel_scores', 'confidence')
    time: float
    duration: float
    score: float
    scene_type: str
    action_type: str
    description: str
    tags: List[str]
    channel_scores: Dict[str, float]
    confidence: float


class FusionScorer:
    """融合评分引擎"""
    
    def __init__(self, preset_name: str = "auto"):
        """
        初始化融合评分引擎
        
        Args:
            preset_name: 视频类型预设名称
        """
        self.preset = get_preset(preset_name)
        logger.info(f"使用预设: {self.preset.name}")
    
    def fuse(self, 
             scene_data: List[Dict],
             motion_data: List[Dict],
             audio_data: List[Dict],
             color_burst_data: List[Dict],
             brightness_flash_data: List[Dict],
             ai_vision_data: List[Dict],
             video_duration: float,
             vfx_energy_data: Optional[List[Dict]] = None,
             clip_data: Optional[List[Dict]] = None) -> List[FusionResult]:
        """
        融合7通道数据（v2.8增强：CLIP语义评分+光流运动+反差评分）
        
        Args:
            scene_data: 场景切换数据
            motion_data: 运动向量数据
            audio_data: 音频能量数据
            color_burst_data: 色彩突变数据
            brightness_flash_data: 亮度闪烁数据
            ai_vision_data: AI视觉分析数据
            video_duration: 视频总时长
            vfx_energy_data: VFX综合能量数据(v2.6b新增)
        
        Returns:
            融合评分结果列表
        """
        logger.info("开始融合评分（v2.8增强：CLIP语义+光流+反差）...")
        
        # 1. 各通道数据归一化到 0-1 范围
        scene_scores_norm = self._normalize_scores(scene_data, "score")
        motion_scores_norm = self._normalize_scores(motion_data, "magnitude")
        audio_scores_norm = self._normalize_audio(audio_data)  # 音频特殊处理 (dB->0-1)
        color_scores_norm = self._normalize_scores(color_burst_data, "score")
        brightness_scores_norm = self._normalize_scores(brightness_flash_data, "score")
        
        # v2.6b: VFX能量通道归一化（已经是0-1范围）
        vfx_energy_norm = vfx_energy_data if vfx_energy_data else []
        
        # v2.8: CLIP语义通道归一化（CLIP输出已是0-1范围）
        clip_norm = clip_data if clip_data else []
        
        # 2. 时间对齐（优化为 2 秒间隔，减少计算量）
        time_points = self._generate_time_points(video_duration, interval=2.0)
        
        # 3. 为每个时间点计算各通道得分（用窗口平均而不是单点）
        fused_results = []
        
        for time_point in time_points:
            # 获取各通道在该时间点附近的平均得分（2秒窗口）
            scene_score = self._get_window_score(scene_scores_norm, time_point, window=2.0)
            motion_score = self._get_window_score(motion_scores_norm, time_point, window=2.0)
            audio_score = self._get_window_score(audio_scores_norm, time_point, window=2.0)
            color_score = self._get_window_score(color_scores_norm, time_point, window=2.0)
            brightness_score = self._get_window_score(brightness_scores_norm, time_point, window=2.0)
            
            # v2.6b: VFX能量得分
            vfx_energy_score = self._get_window_score(vfx_energy_norm, time_point, window=2.0, score_key="score")
            
            # v2.8: CLIP语义得分（已经是0-1范围，3秒窗口匹配2fps采样）
            clip_score = self._get_window_score(clip_norm, time_point, window=3.0, score_key="score")
            
            # AI视觉得分需要特殊处理（基于时间戳匹配）
            ai_result = self._get_ai_result_at_time(ai_vision_data, time_point)
            ai_score = ai_result.get("confidence", 0.0) if ai_result else 0.0
            
            # 3. 加权融合
            weights = self.preset.weights
            final_score = (
                scene_score * weights["scene_change"] +
                motion_score * weights["motion"] +
                audio_score * weights["audio_energy"] +
                color_score * weights["color_burst"] +
                brightness_score * weights["brightness_flash"] +
                ai_score * weights.get("ai_vision", 0.10) +
                clip_score * weights.get("clip_vision", 0.0)  # v2.8: CLIP语义通道
            )
            
            # 3.5 VFX视觉奇观加成（v2.6b增强：直接使用vfx_energy通道+色彩/亮度组合）
            vfx_bonus = 0.0
            
            # v2.6b: 直接使用VFX能量通道（最综合的特效指标）
            if vfx_energy_score > 0.30:
                vfx_bonus = max(vfx_bonus, vfx_energy_score * 0.40)  # 强特效直接加成
            elif vfx_energy_score > 0.15:
                vfx_bonus = max(vfx_bonus, vfx_energy_score * 0.30)  # 中等特效
            
            # 色彩+亮度组合加成（保留原有逻辑）
            if color_score > 0.25 and brightness_score > 0.20:
                vfx_bonus = max(vfx_bonus, 0.20)
            elif color_score > 0.35:
                vfx_bonus = max(vfx_bonus, 0.15)
            elif brightness_score > 0.35:
                vfx_bonus = max(vfx_bonus, 0.12)
            elif color_score > 0.20 and (brightness_score > 0.15 or motion_score > 0.20):
                vfx_bonus = max(vfx_bonus, 0.08)
            
            final_score = min(1.0, final_score + vfx_bonus)
            
            # 4. 确定场景类型和动作类型
            scene_type = ai_result.get("scene_type", "unknown") if ai_result else "unknown"
            action_type = ai_result.get("action_type", "none") if ai_result else "none"
            description = ai_result.get("description", "") if ai_result else ""
            tags = ai_result.get("tags", []) if ai_result else []
            
            # 如果描述为空或为模拟结果，根据通道得分生成有意义的描述
            if not description or description == "模拟分析结果":
                description = self._generate_description(
                    time_point, scene_score, motion_score, audio_score,
                    color_score, brightness_score, vfx_energy_score
                )
                # 根据通道得分推断场景类型
                if scene_type == "unknown":
                    # v3.1: 从音频通道推断语义标签
                    # 使用该时间点附近音频窗口的中频占比推断是否为语音
                    audio_semantic_label = 'unknown'
                    if audio_score > 0.05:
                        # 从 clip_norm 推断不太可飦，使用频谱比例估算
                        # 简化：如果 audio_score 高但 motion_score 低，可能是语音
                        if audio_score > 0.3 and motion_score < 0.2:
                            audio_semantic_label = 'speech'
                        elif audio_score > 0.3 and motion_score > 0.2:
                            audio_semantic_label = 'action'
                        elif audio_score < 0.1:
                            audio_semantic_label = 'silence'
                    scene_type = self._infer_scene_type(
                        motion_score, audio_score, color_score, brightness_score,
                        scene_score, vfx_energy_score, audio_semantic_label
                    )
            
            # 5. 创建融合结果
            result = FusionResult(
                time=time_point,
                duration=2.0,  # 每个时间点代表2秒
                score=final_score,
                scene_type=scene_type,
                action_type=action_type,
                description=description,
                tags=tags,
                channel_scores={
                    "scene_change": scene_score,
                    "motion": motion_score,
                    "audio_energy": audio_score,
                    "color_burst": color_score,
                    "brightness_flash": brightness_score,
                    "ai_vision": ai_score,
                    "vfx_energy": vfx_energy_score,
                    "clip_vision": clip_score  # v2.8
                },
                confidence=ai_score
            )
            
            fused_results.append(result)
        
        # ====== v2.8: 反差/新颖度评分 ======
        # 原理：对比当前时刻与之前10秒上下文的能量差异
        # "安静→爆发"的反差越大，观感越燃
        fused_results = self._apply_contrast_scoring(fused_results)
        
        # ====== v3.0: 情感强度/名场面检测 ======
        # 不需要新AI模型，通过多信号融合推断
        fused_results = self._apply_emotion_landmark_detection(fused_results)
        
        logger.info(f"融合完成: {len(fused_results)}个时间点")
        return fused_results
    
    def _generate_description(self, time_point: float, scene_score: float, 
                              motion_score: float, audio_score: float,
                              color_score: float, brightness_score: float,
                              vfx_energy_score: float = 0.0) -> str:
        """根据通道得分生成有意义的描述（v2.6b: 含VFX能量）"""
        time_str = f"{int(time_point // 60)}:{int(time_point % 60):02d}"
        
        # v2.6b: VFX能量高时优先考虑特效描述
        if vfx_energy_score > 0.30:
            if motion_score > 0.25:
                return f"@{time_str} 特效动作场面"
            elif vfx_energy_score > 0.50:
                return f"@{time_str} 强烈视觉特效"
            return f"@{time_str} 特效光效场面"
        
        # 找出最突出的特征
        features = {
            "motion": motion_score,
            "audio": audio_score,
            "scene": scene_score,
            "color": color_score,
            "brightness": brightness_score
        }
        
        sorted_features = sorted(features.items(), key=lambda x: x[1], reverse=True)
        top1_name, top1_val = sorted_features[0]
        top2_name, top2_val = sorted_features[1]
        top3_name, top3_val = sorted_features[2]
        
        # 特效视觉奇观（色彩+亮度同时突出）
        if color_score > 0.25 and brightness_score > 0.20:
            if motion_score > 0.25:
                return f"@{time_str} 特效动作场面"
            return f"@{time_str} 视觉特效奇观"
        
        # 组合特征生成更精确的描述
        if top1_name == "motion":
            if top2_name == "audio" and audio_score > 0.3:
                return f"@{time_str} 激烈动作+强音效"
            if top2_name == "color" and color_score > 0.20:
                return f"@{time_str} 动作+特效场面"
            if motion_score > 0.25:
                return f"@{time_str} 激烈动作场面"
        
        if top1_name == "audio":
            if top2_name == "motion" and motion_score > 0.15:
                return f"@{time_str} 高能量音频+动作"
            if audio_score > 0.4:
                return f"@{time_str} 高能量音频"
        
        if top1_name == "color":
            if motion_score > 0.20:  # v2.6: 色彩+运动 = 特效动作
                return f"@{time_str} 动作+特效场面"
            if color_score > 0.30:
                return f"@{time_str} 丰富色彩特效"
            return f"@{time_str} 色彩变化"
        
        if top1_name == "brightness":
            if brightness_score > 0.30:
                return f"@{time_str} 亮度闪烁特效"
            return f"@{time_str} 光影变化"
        
        if top1_name == "scene":
            return f"@{time_str} 场景切换"
        
        # 多特征组合但都不突出
        high_count = sum(1 for _, v in sorted_features[:3] if v > 0.25)
        if high_count >= 2:
            return f"@{time_str} 多维特征精彩片段"
        
        return f"@{time_str} 精彩片段"
    
    def _infer_scene_type(self, motion_score: float, audio_score: float,
                          color_score: float, brightness_score: float,
                          scene_score: float = 0.0,
                          vfx_energy_score: float = 0.0,
                          audio_semantic: str = 'unknown') -> str:
        """根据通道得分推断场景类型
        
        v3.1改进：
        - 直接使用VFX能量通道推断特效场景
        - 使用音频语义标签精确区分语音对话和动作音效
        - 语音主导+低运动 → 强制归为 'dialog'（不再被误判为action/climax）
        - 无声+高VFX+高视觉 → 'vfx_action'（无声打斗特效）
        """
        # v3.1: 音频语义直接判定语音场景
        if audio_semantic == 'speech' and motion_score < 0.25:
            # 语音主导+低运动 → 对话场景（阻止误判）
            return "dialog"
        
        # v3.1: 无声+高VFX+高视觉 → 无声特效打斗
        if audio_semantic in ('silence', 'unknown') and vfx_energy_score > 0.25 and \
           (color_score > 0.20 or brightness_score > 0.15):
            if motion_score > 0.15:
                return "vfx_action"   # 无声特效打斗
            return "vfx_spectacle"  # 无声纯视觉特效
        
        # v2.6b: VFX能量直接推断（最可靠的特效指标）
        if vfx_energy_score > 0.35:
            if motion_score > 0.25:
                return "vfx_action"  # 特效+动作
            return "vfx_spectacle"   # 纯视觉特效
        
        # 1. 视觉奇观：高色彩+高亮度（魔法、能量场、光效、爆炸）
        if color_score > 0.25 and brightness_score > 0.20:
            return "vfx_spectacle"
        if color_score > 0.35:
            return "vfx_spectacle"
        if brightness_score > 0.35:
            return "vfx_spectacle"
        
        # 2. 动作场景：运动+音频（v3.1: 排除语音主导）
        if motion_score > 0.30 and audio_score > 0.30 and audio_semantic != 'speech':
            return "action"
        
        # 3. 高运动场景
        if motion_score > 0.40:
            if color_score > 0.20:
                return "vfx_action"
            return "action"
        
        # 3.5 中等运动+中等色彩 = VFX动作
        if motion_score > 0.25 and color_score > 0.20:
            return "vfx_action"
        
        # 3.7 v2.6b: 中等VFX能量也推断为特效场景
        if vfx_energy_score > 0.15:
            if motion_score > 0.15 or color_score > 0.15:
                return "vfx_action"
            return "vfx_spectacle"
        
        # 4. VFX+音频（v3.1: 排除语音主导）
        if color_score > 0.20 and audio_score > 0.35 and audio_semantic != 'speech':
            return "vfx_action"
        
        # 5. 音频主导（v3.1: 语音主导归为 dialog，非语音归为 climax）
        if audio_score > 0.5:
            if audio_semantic == 'speech':
                return "dialog"  # v3.1: 高能量语音 = 对话/争吵
            if motion_score > 0.15:
                return "climax"
            return "dialog"
        
        # 6. 场景切换密集
        if scene_score > 0.4:
            return "action"
        
        # 7. 色彩丰富
        if color_score > 0.25:
            return "highlight"
        
        # 8. 综合多特征
        high_features = sum(1 for s in [motion_score, audio_score, color_score, brightness_score] if s > 0.18)
        if high_features >= 2:
            return "highlight"
        
        return "highlight"
    
    def extract_highlights(self, 
                          fused_results: List[FusionResult],
                          top_n: int = 10,
                          threshold: Optional[float] = None,
                          scene_cuts: Optional[List[float]] = None,
                          video_duration: Optional[float] = None) -> List[FusionResult]:
        """
        提取高燃片段（v3.3重构：先镜头切割 → 合并短镜头 → 再提取）
        
        Args:
            fused_results: 融合评分结果
            top_n: 提取前N个片段
            threshold: 评分阈值（可选，默认使用预设阈值）
            scene_cuts: 镜头切割点列表（秒），用于确保片段边界在镜头切换处
        
        Returns:
            高燃片段列表
        """
        if not fused_results:
            return []
        
        # 1. 按得分排序
        sorted_results = sorted(fused_results, key=lambda x: x.score, reverse=True)
        
        # 2. 使用阈值过滤
        if threshold is None:
            threshold = self.preset.thresholds["hot"]
        
        filtered_results = [r for r in sorted_results if r.score >= threshold]
        
        # 动态回退：如果阈值过滤后太少，使用百分位数
        if len(filtered_results) < top_n:
            top_percent = max(len(sorted_results) // 3, 1)
            if top_percent > len(filtered_results):
                logger.info(f"阈值 {threshold:.2f} 过滤结果太少，使用前30%高得分 ({top_percent}个)")
                filtered_results = sorted_results[:top_percent]
        
        if len(filtered_results) == 0:
            logger.warning("无片段超过阈值，返回排序后的前N个")
            filtered_results = sorted_results
        
        # 3. v3.3: 基于镜头切割点调整片段边界
        if scene_cuts and len(scene_cuts) > 0:
            filtered_results = self._align_to_scene_cuts(filtered_results, scene_cuts)
        
        # 4. v3.2: 先合并相邻片段（基于镜头连续性）
        merged_results = self._merge_adjacent_segments(filtered_results)
        
        # 5. v3.2: 二次合并 — 确保合并后的片段 >= min_duration
        min_dur = self.preset.min_duration
        merged_results = self._ensure_minimum_duration(merged_results, min_dur)
        
        # 6. 过滤太短的片段
        merged_results = [r for r in merged_results if r.duration >= min_dur]
        
        if not merged_results:
            # 如果过滤后没有结果，放宽条件
            merged_results = self._merge_adjacent_segments(filtered_results, gap_threshold=3.0)
            merged_results = self._ensure_minimum_duration(merged_results, min_dur)
            merged_results = [r for r in merged_results if r.duration >= min_dur]
        
        # 7. v3.4: NMS时间去重（消除重叠/包含片段）
        deduped_results = self._deduplicate_segments(merged_results)
                
        # 8. v3.4: 总时长预算约束（防止提取时长超出原视频）
        if video_duration and video_duration > 0:
            max_total = video_duration * 0.65  # 最多覆盖视频时长的65%
            # 动态限制top_n：至少保证每片段有 min_duration 秒空间
            max_by_duration = max(1, int(max_total / max(self.preset.min_duration, 4.0)))
            effective_top_n = min(top_n, max_by_duration)
            deduped_results = self._apply_duration_budget(deduped_results, max_total, effective_top_n)
                
        # 9. 取前N个（按得分排序）
        merged_sorted = sorted(deduped_results, key=lambda x: x.score, reverse=True)
        top_results = merged_sorted[:top_n]
                
        # 按时间顺序排列，便于后续剪辑
        top_results = sorted(top_results, key=lambda x: x.time)
                
        total_dur = sum(r.duration for r in top_results)
        logger.info(f"提取高燃片段: {len(top_results)}个, 总时长{total_dur:.1f}s "
                   f"(阈值: {threshold:.2f}"
                   f"{', 最低分: ' + f'{top_results[-1].score:.3f}' if top_results else ''})") 
        return top_results
    
    def _align_to_scene_cuts(self, results: List[FusionResult], 
                             scene_cuts: List[float]) -> List[FusionResult]:
        """
        v3.3: 将片段边界对齐到最近的镜头切割点
        
        避免在镜头中间截断，确保每个片段都是完整的镜头
        """
        if not scene_cuts:
            return results
        
        aligned = []
        for r in results:
            original_start = r.time
            original_end = r.time + r.duration
            
            # 找到最近的镜头切割点作为开始和结束
            new_start = self._find_nearest_scene_cut(scene_cuts, original_start, bias='before')
            new_end = self._find_nearest_scene_cut(scene_cuts, original_end, bias='after')
            
            # 确保新时长 >= 原始时长的 80%（避免过度扩展）
            min_duration = r.duration * 0.8
            new_duration = new_end - new_start
            
            if new_duration >= min_duration:
                aligned.append(FusionResult(
                    time=new_start,
                    duration=new_duration,
                    score=r.score,
                    scene_type=r.scene_type,
                    action_type=r.action_type,
                    description=f"{r.description} [镜头对齐]",
                    tags=r.tags,
                    channel_scores=r.channel_scores,
                    confidence=r.confidence,
                ))
            else:
                # 如果对齐后时长不足，保持原样
                aligned.append(r)
        
        logger.info(f"镜头边界对齐: {len(results)} -> {len(aligned)} (有效对齐: {sum(1 for a in aligned if '[镜头对齐]' in a.description)})")
        return aligned
    
    def _find_nearest_scene_cut(self, scene_cuts: List[float], 
                                 target_time: float, 
                                 bias: str = 'before') -> float:
        """
        找到最近的镜头切割点
        
        Args:
            scene_cuts: 镜头切割点列表（秒）
            target_time: 目标时间
            bias: 'before' 找之前的切割点，'after' 找之后的切割点
        """
        if not scene_cuts:
            return target_time
        
        if bias == 'before':
            # 找 target_time 之前最近的切割点
            before_cuts = [t for t in scene_cuts if t <= target_time]
            return max(before_cuts) if before_cuts else (scene_cuts[0] if scene_cuts else target_time)
        else:
            # 找 target_time 之后最近的切割点
            after_cuts = [t for t in scene_cuts if t >= target_time]
            return min(after_cuts) if after_cuts else (scene_cuts[-1] if scene_cuts else target_time)
    
    def _deduplicate_segments(self, results: List[FusionResult],
                               iou_threshold: float = 0.15) -> List[FusionResult]:
        """
        v3.4: 基于时间重叠的非极大值抑制(NMS)去重（强化版）
        
        两轮去重：
        1. 先消除完全包含关系（小片段被大片段完全包含 → 丢弃小的）
        2. 再用IoU阈值过滤重叠（更严格的0.15阈值）
        """
        if not results:
            return results
        
        # === 第一轮：消除完全包含 ===
        # 按时间排序，找出被其他片段完全包含的子片段并丢弃
        sorted_by_time = sorted(results, key=lambda x: x.time)
        non_contained = []
        
        for i, seg in enumerate(sorted_by_time):
            contained = False
            for j, other in enumerate(sorted_by_time):
                if i == j:
                    continue
                # 如果seg完全被other包含
                if other.time <= seg.time and (other.time + other.duration) >= (seg.time + seg.duration):
                    # seg被other完全包含
                    # 只有当other的分数>=seg的分数时，才丢弃seg（保留高分的）
                    # 如果seg分数更高，则丢弃other（在后续处理中）
                    if other.score >= seg.score:
                        contained = True
                        break
                    # 如果seg分数更高但被other包含，other会在后面被丢弃
            
            if not contained:
                non_contained.append(seg)
        
        if len(non_contained) < len(sorted_by_time):
            logger.info(f"包含消除: {len(sorted_by_time)} -> {len(non_contained)}")
        
        # === 第二轮：NMS重叠过滤（更严格的0.15阈值） ===
        sorted_by_score = sorted(non_contained, key=lambda x: x.score, reverse=True)
        selected = []
        
        for candidate in sorted_by_score:
            c_start = candidate.time
            c_end = candidate.time + candidate.duration
            
            overlapped = False
            for sel in selected:
                s_start = sel.time
                s_end = sel.time + sel.duration
                
                # 计算时间重叠
                overlap_start = max(c_start, s_start)
                overlap_end = min(c_end, s_end)
                
                if overlap_end > overlap_start:
                    overlap_dur = overlap_end - overlap_start
                    # IoU = 重叠 / 并集
                    union_dur = max(c_end, s_end) - min(c_start, s_start)
                    iou = overlap_dur / union_dur if union_dur > 0 else 0
                    
                    if iou > iou_threshold:
                        overlapped = True
                        break
                
                # 额外检查：如果两个片段紧邻（间隔<2秒），也合并
                gap = s_start - c_end if s_start > c_end else c_start - s_end
                if gap < 2.0 and gap >= 0:
                    # 间隔很小，视为相邻重叠
                    overlapped = True
                    break
            
            if not overlapped:
                selected.append(candidate)
        
        logger.info(f"NMS去重: {len(results)} -> {len(selected)} "
                   f"(IoU阈值: {iou_threshold:.2f}, 包含消除: {len(sorted_by_time)-len(non_contained)}个)")
        return selected
    
    def _apply_duration_budget(self, results: List[FusionResult],
                               max_total_duration: float,
                               max_count: int = 999) -> List[FusionResult]:
        """
        v3.4: 总时长预算约束（含重叠检查）
        
        按分数排序选择片段，同时确保：
        1. 累计时长不超过 max_total_duration
        2. 片段数不超过 max_count
        3. 新片段与已选片段的重叠不超过片段时长的20%（允许微小重叠）
        """
        if not results:
            return results
        
        sorted_by_score = sorted(results, key=lambda x: x.score, reverse=True)
        selected = []
        total_dur = 0.0
        
        for r in sorted_by_score:
            if len(selected) >= max_count:
                break
            if total_dur + r.duration > max_total_duration:
                continue  # 超时时长预算，跳过
            
            # 检查与已选片段的重叠程度
            r_start = r.time
            r_end = r.time + r.duration
            r_dur = r.duration
            too_much_overlap = False
            for sel in selected:
                s_start = sel.time
                s_end = sel.time + sel.duration
                overlap_start = max(r_start, s_start)
                overlap_end = min(r_end, s_end)
                if overlap_end > overlap_start:
                    overlap_dur = overlap_end - overlap_start
                    # 重叠超过片段时长的50%，则跳过（保留独特内容）
                    if overlap_dur > r_dur * 0.50:
                        too_much_overlap = True
                        break
            
            if not too_much_overlap:
                selected.append(r)
                total_dur += r.duration
        
        logger.info(f"时长预算: {len(results)} -> {len(selected)} "
                   f"(总时长: {total_dur:.1f}s / {max_total_duration:.1f}s, "
                   f"上限: {max_count}个)")
        return selected
    
    def _ensure_minimum_duration(self, results: List[FusionResult], min_dur: float) -> List[FusionResult]:
        """
        v3.2: 确保合并后的片段时长 >= min_dur
        如果片段太短，尝试向前后扩展（吞并相邻低分片段）
        """
        if len(results) <= 1:
            return results
        
        enhanced = []
        for i, r in enumerate(results):
            if r.duration >= min_dur:
                enhanced.append(r)
                continue
            
            # 片段太短，尝试扩展
            extended = self._extend_segment(results, i, min_dur)
            if extended and extended.duration >= min_dur:
                enhanced.append(extended)
            # 如果扩展后仍然太短，丢弃
        
        logger.info(f"短片段扩展: {len(results)} -> {len(enhanced)} (min_dur={min_dur}s)")
        return enhanced
    
    def _extend_segment(self, results: List[FusionResult], idx: int, target_dur: float) -> Optional[FusionResult]:
        """尝试向左右扩展片段以达到目标时长"""
        r = results[idx]
        new_start = r.time
        new_end = r.time + r.duration
        
        # 先向右扩展
        for j in range(idx + 1, len(results)):
            if new_end - new_start >= target_dur:
                break
            nxt = results[j]
            if nxt.time - new_end < 5.0:  # 间隔<5秒才扩展
                new_end = max(new_end, nxt.time + nxt.duration)
        
        # 再向左扩展
        for j in range(idx - 1, -1, -1):
            if new_end - new_start >= target_dur:
                break
            prev = results[j]
            if new_start - (prev.time + prev.duration) < 5.0:
                new_start = min(new_start, prev.time)
        
        new_dur = new_end - new_start
        if new_dur < target_dur:
            return None
        
        return FusionResult(
            time=new_start,
            duration=new_dur,
            score=r.score,
            scene_type=r.scene_type,
            action_type=r.action_type,
            description=f"{r.description} [扩展{new_dur:.0f}s]",
            tags=r.tags,
            channel_scores=r.channel_scores,
            confidence=r.confidence,
        )
    
    def extract_high_moments(self,
                            fused_results: List[FusionResult],
                            top_n: int = 20,
                            threshold: Optional[float] = None,
                            scene_cuts: Optional[List[float]] = None,
                            video_duration: Optional[float] = None) -> List[FusionResult]:
        """
        提取高光时刻（包含打戏、名场面等）
        
        Args:
            fused_results: 融合评分结果
            top_n: 提取前N个片段
            threshold: 评分阈值
            scene_cuts: 镜头切割点列表（秒）
        
        Returns:
            高光时刻列表
        """
        if not fused_results:
            return []
        
        # 使用预设阈值或自定义阈值
        if threshold is None:
            threshold = self.preset.thresholds["highlight"]
        
        # 1. 按得分排序
        sorted_results = sorted(fused_results, key=lambda x: x.score, reverse=True)
        
        # 2. 过滤低于阈值的片段
        filtered_results = [r for r in sorted_results if r.score >= threshold]
        
        # 3. v3.3: 基于镜头切割点调整片段边界
        if scene_cuts and len(scene_cuts) > 0:
            filtered_results = self._align_to_scene_cuts(filtered_results, scene_cuts)
        
        # 4. 合并相邻片段
        merged_results = self._merge_adjacent_segments(filtered_results)
        
        # 5. v3.4: NMS时间去重（消除重叠/包含片段）
        deduped_results = self._deduplicate_segments(merged_results)
        
        # 6. v3.4: 总时长预算约束
        if video_duration and video_duration > 0:
            max_total = video_duration * 0.65
            max_by_duration = max(1, int(max_total / max(self.preset.min_duration, 4.0)))
            effective_top_n = min(top_n, max_by_duration)
            deduped_results = self._apply_duration_budget(deduped_results, max_total, effective_top_n)
        
        # 7. 取前N个
        top_sorted = sorted(deduped_results, key=lambda x: x.score, reverse=True)
        top_results = top_sorted[:top_n]
        top_results = sorted(top_results, key=lambda x: x.time)
        
        total_dur = sum(r.duration for r in top_results)
        logger.info(f"提取高光时刻: {len(top_results)}个, 总时长{total_dur:.1f}s (阈值: {threshold:.2f})")
        return top_results
    
    def _apply_contrast_scoring(self, results: List[FusionResult],
                                 context_window: float = 10.0) -> List[FusionResult]:
        """
        反差/新颖度评分（v2.8新增）
        
        对每个时间点，计算其与前 context_window 秒平均得分的差异。
        反差越大（当前远高于近期上下文），说明“突然燃起来”的感觉越强。
        
        算法：
        1. 计算每个时间点前 context_window 秒的平均基线分数
        2. contrast = current_score - baseline_avg
        3. 如果 contrast > 0，按 contrast 比例加成最终得分
        4. 加成上限为 0.20（避免反差完全压倒其他信号）
        
        Args:
            results: 融合结果列表
            context_window: 上下文窗口（秒），默认10秒
        
        Returns:
            加分后的结果列表
        """
        if not results or len(results) < 3:
            return results
        
        # 提取基线分数（加成前的原始分）
        base_scores = [r.score for r in results]
        times = [r.time for r in results]
        
        contrast_boosts = []
        
        for i, r in enumerate(results):
            # 收集上下文窗口内的分数
            context_scores = []
            for j in range(i):
                if times[i] - times[j] <= context_window:
                    context_scores.append(base_scores[j])
            
            if not context_scores:
                # 没有上下文（视频开头），不加反差分
                contrast_boosts.append(0.0)
                continue
            
            baseline_avg = sum(context_scores) / len(context_scores)
            contrast = r.score - baseline_avg
            
            if contrast > 0.05:  # 至少有 0.05 的反差才加成
                # 反差加成：contrast 的 40%，上限 0.20
                boost = min(contrast * 0.40, 0.20)
                contrast_boosts.append(boost)
            else:
                contrast_boosts.append(0.0)
        
        # 应用加成
        enhanced_results = []
        for i, r in enumerate(results):
            boost = contrast_boosts[i]
            new_score = min(1.0, r.score + boost)
            
            # 更新分数，并在 channel_scores 中记录反差信息
            new_channel_scores = dict(r.channel_scores)
            new_channel_scores["contrast"] = boost
            
            enhanced = FusionResult(
                time=r.time,
                duration=r.duration,
                score=new_score,
                scene_type=r.scene_type,
                action_type=r.action_type,
                description=r.description,
                tags=r.tags,
                channel_scores=new_channel_scores,
                confidence=r.confidence
            )
            enhanced_results.append(enhanced)
        
        # 日志统计
        boosts_applied = sum(1 for b in contrast_boosts if b > 0)
        max_boost = max(contrast_boosts) if contrast_boosts else 0
        logger.info(f"反差评分: {boosts_applied}/{len(results)}个时间点获得加成, "
                   f"最大加成: {max_boost:.3f}")
        
        return enhanced_results
    
    def _generate_time_points(self, duration: float, interval: float = 1.0) -> List[float]:
        """生成时间点列表"""
        return [i * interval for i in range(int(duration / interval))]
    
    def _get_score_at_time(self, data: List[Dict], time: float, score_key: str) -> float:
        """获取指定时间点的得分"""
        if not data:
            return 0.0
        
        # 找到最接近的数据点
        closest = min(data, key=lambda x: abs(x.get("time", 0) - time))
        
        # 如果在时间范围内，返回得分
        if abs(closest.get("time", 0) - time) <= 2.0:  # 2秒容差
            return closest.get(score_key, 0.0)
        
        return 0.0
    
    def _get_ai_result_at_time(self, ai_data: List[Dict], time: float) -> Optional[Dict]:
        """获取指定时间点的AI分析结果"""
        if not ai_data:
            return None
        
        # 找到最接近的AI分析结果
        closest = min(ai_data, key=lambda x: abs(x.get("time", 0) - time))
        
        # 如果在时间范围内，返回结果
        if abs(closest.get("time", 0) - time) <= 3.0:  # 3秒容差
            return closest
        
        return None
    
    def _normalize_scores(self, data: List[Dict], score_key: str) -> List[Dict]:
        """将得分归一化到 0-1 范围（Z-score + 线性映射）"""
        if not data:
            return []
        
        scores = []
        for item in data:
            val = item.get(score_key, 0.0)
            if val is None:
                val = 0.0
            scores.append(float(val))
        
        if not scores:
            return []
        
        scores_arr = np.array(scores)
        
        # 处理全零情况
        if np.all(scores_arr == 0):
            return [dict(item, **{score_key: 0.0}) for item in data]
        
        # 线性归一化到 0-1
        min_val = np.min(scores_arr)
        max_val = np.max(scores_arr)
        
        if max_val - min_val < 1e-10:
            # 所有值相同，设为 0.5
            normalized = np.full_like(scores_arr, 0.5)
        else:
            normalized = (scores_arr - min_val) / (max_val - min_val)
        
        # 构造新数据
        result = []
        for i, item in enumerate(data):
            new_item = dict(item)
            new_item[score_key] = float(normalized[i])
            result.append(new_item)
        
        return result
    
    def _normalize_audio(self, audio_data: List[Dict]) -> List[Dict]:
        """
        音频能量特殊处理：将 dB 值（通常 -60 到 0）映射到 0-1
        
        关键修复：dB 值通常是负数，不能直接参与加权！
        """
        if not audio_data:
            return []
        
        # 提取能量值
        energies = []
        for item in audio_data:
            energy = item.get("energy", -60.0)
            if energy is None:
                energy = -60.0
            energies.append(float(energy))
        
        energies_arr = np.array(energies)
        
        # 将 dB 映射到 0-1: -60dB->0, 0dB->1
        # 使用 sigmoid 使中间部分有更好的区分度
        # 线性映射 + 夹紧
        mapped = np.clip((energies_arr + 60.0) / 60.0, 0.0, 1.0)
        
        # 构造结果
        result = []
        for i, item in enumerate(audio_data):
            new_item = dict(item)
            new_item["energy"] = float(mapped[i])
            result.append(new_item)
        
        logger.debug(f"音频归一化: min={np.min(energies_arr):.1f}dB, max={np.max(energies_arr):.1f}dB")
        return result
    
    def _get_window_score(self, data: List[Dict], time_point: float, 
                         window: float = 2.0, score_key: str = None) -> float:
        """
        获取时间点附近窗口内的平均得分
        
        Args:
            data: 数据列表（已归一化）
            time_point: 目标时间点
            window: 窗口大小（秒）
            score_key: 得分键名，如果为 None 则尝试自动检测
        """
        if not data:
            return 0.0
        
        # 自动检测键名
        if score_key is None:
            for key in ["score", "magnitude", "energy", "value"]:
                if data and key in data[0]:
                    score_key = key
                    break
            if score_key is None:
                return 0.0
        
        # 收集窗口内的所有点
        half_window = window / 2.0
        scores_in_window = []
        
        for item in data:
            t = item.get("time", 0.0)
            if abs(t - time_point) <= half_window:
                val = item.get(score_key, 0.0)
                if val is not None:
                    scores_in_window.append(float(val))
        
        if not scores_in_window:
            return 0.0
        
        # 返回平均值
        return float(np.mean(scores_in_window))
    
    def _merge_adjacent_segments(self, results: List[FusionResult], 
                                 gap_threshold: float = 5.0) -> List[FusionResult]:
        """
        合并相邻片段（v3.2优化：默认5秒间隔，减少1-2秒短片段）
        
        Args:
            results: 片段列表
            gap_threshold: 间隔阈值（秒），小于此值的片段会被合并
        
        Returns:
            合并后的片段列表
        """
        if not results:
            return []
        
        max_dur = self.preset.max_duration
        
        # 按时间排序
        sorted_results = sorted(results, key=lambda x: x.time)
        
        merged = []
        current = sorted_results[0]
        
        for i in range(1, len(sorted_results)):
            next_result = sorted_results[i]
            gap = next_result.time - (current.time + current.duration)
            
            # 如果间隔小于阈值且合并后不会超长，合并
            if gap < gap_threshold and (max(current.time + current.duration, next_result.time + next_result.duration) - current.time) <= max_dur:
                # 合并片段（修复包含关系Bug：取两个结束时间的最大值）
                new_end = max(current.time + current.duration, next_result.time + next_result.duration)
                new_duration = new_end - current.time
                # v2.8: 使用峰值分数而非平均（避免高分片段被低分拖低）
                peak_score = max(current.score, next_result.score)
                avg_score = (current.score + next_result.score) / 2
                # 取峰值和平均的加权（偏向峰值）
                new_score = peak_score * 0.6 + avg_score * 0.4
                
                current = FusionResult(
                    time=current.time,
                    duration=new_duration,
                    score=min(new_score, 1.0),
                    scene_type=current.scene_type,
                    action_type=current.action_type,
                    description=current.description,
                    tags=list(set(current.tags + next_result.tags)),
                    channel_scores={
                        k: max(current.channel_scores.get(k, 0), next_result.channel_scores.get(k, 0))
                        for k in current.channel_scores.keys()
                    },
                    confidence=max(current.confidence, next_result.confidence)
                )
            else:
                # 保存当前片段，开始新片段
                merged.append(current)
                current = next_result
        
        # 添加最后一个片段
        merged.append(current)
        
        return merged
    
    # =========================================================
    # v3.0: 情感强度/名场面检测（多信号融合推断）
    # =========================================================
    def _apply_emotion_landmark_detection(self, results: List[FusionResult]) -> List[FusionResult]:
        """
        情感强度和名场面检测（v3.0新增）
        
        不依赖AI模型，通过多信号融合推断：
        1. 情感爆发点：音频突变+运动突发+场景切换同时发生
        2. 名场面：持续时间适中+高反差+高CLIP语义+视觉特征集中
        3. 叙事高潮：从低能量快速上升到高能量的转折点
        
        检测结果以标签形式附加到FusionResult.tags
        """
        if len(results) < 5:
            return results
        
        # 计算全局基线
        all_scores = [r.score for r in results]
        mean_score = sum(all_scores) / len(all_scores)
        
        for i, result in enumerate(results):
            cs = result.channel_scores
            motion = cs.get('motion', 0)
            audio = cs.get('audio_energy', 0)
            scene = cs.get('scene_change', 0)
            color = cs.get('color_burst', 0)
            vfx = cs.get('vfx_energy', 0)
            clip = cs.get('clip_vision', 0)
            
            # === 1. 情感爆发点检测 ===
            # 多维度同时达到高值 = 情感爆发
            high_dims = sum(1 for v in [motion, audio, scene, vfx] if v > 0.5)
            if high_dims >= 3 and result.score > mean_score * 1.8:
                if '情感爆发' not in result.tags:
                    result.tags.append('情感爆发')
                # 提升评分（情感爆发是极高燃信号）
                result.score = min(1.0, result.score * 1.05)
            
            # === 2. 叙事转折点（从低到高的突变） ===
            if i >= 3:
                # 前3个时间点的平均分数
                prev_avg = sum(results[j].score for j in range(max(0, i-3), i)) / min(i, 3)
                # 当前相对前文的提升幅度
                if prev_avg > 0.01:
                    rise_ratio = result.score / prev_avg
                    if rise_ratio > 2.5 and result.score > 0.4:
                        if '叙事转折' not in result.tags:
                            result.tags.append('叙事转折')
                        result.score = min(1.0, result.score * 1.08)
                    elif rise_ratio > 2.0 and result.score > 0.35:
                        if '能量上升' not in result.tags:
                            result.tags.append('能量上升')
            
            # === 3. 名场面/经典时刻检测 ===
            # 高CLIP语义 + 高反差 + 适中时长 = 名场面
            # CLIP分数高意味着画面内容与"燃"相关
            if clip > 0.3 and result.score > 0.5:
                if '名场面' not in result.tags:
                    result.tags.append('名场面')
            # 场景切换+高音频 = 重要对话/宣言
            elif scene > 0.5 and audio > 0.4 and motion < 0.3:
                if '重要对话' not in result.tags:
                    result.tags.append('重要对话')
            
            # === 4. 视觉震撼检测 ===
            # VFX+色彩+亮度同时高
            if vfx > 0.4 and color > 0.3:
                if '视觉震撼' not in result.tags:
                    result.tags.append('视觉震撼')
            
            # === 5. 持续高潮检测（不只是一瞬间） ===
            if i >= 2 and i < len(results) - 2:
                nearby_scores = [results[j].score for j in range(max(0, i-2), min(len(results), i+3))]
                if min(nearby_scores) > mean_score * 1.5:
                    if '持续高潮' not in result.tags:
                        result.tags.append('持续高潮')
        
        # 统计标签分布
        tag_counts = {}
        for r in results:
            for t in r.tags:
                tag_counts[t] = tag_counts.get(t, 0) + 1
        if tag_counts:
            logger.info(f"情感/名场面检测标签分布: {dict(sorted(tag_counts.items(), key=lambda x: -x[1]))}")
        
        return results


# 测试代码
if __name__ == "__main__":
    # 创建测试数据
    test_scene_data = [
        {"time": 10.0, "score": 0.8},
        {"time": 20.0, "score": 0.6},
        {"time": 30.0, "score": 0.9}
    ]
    
    test_motion_data = [
        {"time": 10.0, "magnitude": 0.7},
        {"time": 20.0, "magnitude": 0.5},
        {"time": 30.0, "magnitude": 0.8}
    ]
    
    test_audio_data = [
        {"time": 10.0, "energy": 0.6},
        {"time": 20.0, "energy": 0.8},
        {"time": 30.0, "energy": 0.7}
    ]
    
    test_color_data = [
        {"time": 10.0, "score": 0.3},
        {"time": 20.0, "score": 0.4},
        {"time": 30.0, "score": 0.9}
    ]
    
    test_brightness_data = [
        {"time": 10.0, "score": 0.2},
        {"time": 20.0, "score": 0.3},
        {"time": 30.0, "score": 0.8}
    ]
    
    test_ai_data = [
        {
            "time": 10.0,
            "scene_type": "action",
            "action_type": "sword_fight",
            "description": "剑斗场景",
            "tags": ["#打戏", "#剑斗"],
            "confidence": 0.85
        },
        {
            "time": 20.0,
            "scene_type": "highlight",
            "action_type": "none",
            "description": "经典对白",
            "tags": ["#名场面", "#经典台词"],
            "confidence": 0.75
        },
        {
            "time": 30.0,
            "scene_type": "vfx_action",
            "action_type": "magic",
            "description": "法术特效",
            "tags": ["#法术特效", "#仙侠"],
            "confidence": 0.90
        }
    ]
    
    # 创建融合评分引擎
    scorer = FusionScorer(preset_name="auto")
    
    # 融合评分
    fused_results = scorer.fuse(
        scene_data=test_scene_data,
        motion_data=test_motion_data,
        audio_data=test_audio_data,
        color_burst_data=test_color_data,
        brightness_flash_data=test_brightness_data,
        ai_vision_data=test_ai_data,
        video_duration=60.0
    )
    
    print(f"融合结果: {len(fused_results)}个时间点")
    for result in fused_results[:5]:
        print(f"  时间: {result.time:.1f}s, 得分: {result.score:.3f}, "
              f"类型: {result.scene_type}, 动作: {result.action_type}")
    
    # 提取高燃片段
    hot_segments = scorer.extract_highlights(fused_results, top_n=3)
    print(f"\n高燃片段: {len(hot_segments)}个")
    for seg in hot_segments:
        print(f"  {seg.time:.1f}s - {seg.time + seg.duration:.1f}s, "
              f"得分: {seg.score:.3f}, 描述: {seg.description}")
    
    # 提取高光时刻
    highlight_segments = scorer.extract_high_moments(fused_results, top_n=5)
    print(f"\n高光时刻: {len(highlight_segments)}个")
    for seg in highlight_segments:
        print(f"  {seg.time:.1f}s - {seg.time + seg.duration:.1f}s, "
              f"得分: {seg.score:.3f}, 描述: {seg.description}")
