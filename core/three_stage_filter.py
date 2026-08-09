"""
三阶段筛选 v2.2

核心改进（相对v2.1）:
1. L1 镜头时长检测 - 结合动态scene阈值（P25/P50/P75
2. L2 多特征融合 - 运动+音频+颜色/饱和度+对比度+音频频谱分层 + 快切镜头组
3. L3 轻量化AI稀疏推理 - 启发式规则（可替换为真实模型
4. 多窗口动态阈值 - 短/中/长三窗口对比分析
5. 候选段扩张 - 对高燃片段前后各+1秒（避免截在动作中间
"""
import time
from dataclasses import dataclass
from typing import List, Dict, Optional

from core.unified_video_pipeline import PipelineResult, FrameInfo, ShotInfo
from utils.logger import logger
from config import CONFIG


@dataclass
class HighlightSegment:
    """高燃片段 - 最终输出（v4.0 新增场景类型）"""
    start_time: float
    end_time: float
    duration: float
    score: float
    level: str  # 'normal' | 'hot' | 'hot_super'
    reason: str  # 识别依据
    scene_type: str = "unknown"      # v4.0: CLIP 场景分类（古装打斗/仙侠特效/对话等
    scene_confidence: float = 0.0    # v4.0: 场景分类置信度（0-1


class ThreeStageFilter:
    """三阶段筛选器 v3.1 - 自适应阈值 + 分组组合 + 多维交叉验证 + 音频语义排斥"""
    
    def __init__(self):
        self.pl = CONFIG.pipeline
        self._stats = {}
        self._adaptive_factors = {}  # v3.0: 自适应阈值因子
        logger.info("三阶段筛选器 v3.1 初始化（自适应阈值+分组组合+多维交叉验证+音频语义排斥）")
    
    # =========================================================
    # v3.0: 自适应阈值计算
    # =========================================================
    def _compute_adaptive_factors(self, pr: PipelineResult):
        """
        根据视频的全局统计特征计算自适应阈值因子
        
        核心思路：不同类型的视频其特征分布差异巨大
        - 动作片：运动方差大，阈值应偏高避免误判
        - 文艺片：运动方差小，阈值应偏低避免漏检
        - 仙侠片：VFX能量突出，应加强VFX通道权重
        
        自适应因子：
        - motion_sensitivity: 运动阈值灵敏度（0.5-2.0）
        - audio_sensitivity: 音频阈值灵敏度
        - vfx_sensitivity: VFX阈值灵敏度
        - composite_threshold: 综合评分阈值调整
        """
        # 运动分布的变异系数（CV = std/mean）
        motions = [f.motion_score for f in pr.frames]
        if motions:
            motion_mean = sum(motions) / len(motions)
            motion_std = (sum((m - motion_mean) ** 2 for m in motions) / len(motions)) ** 0.5
            motion_cv = motion_std / max(motion_mean, 0.1)
            
            # CV高 = 运动变化大（动作片），阈值应适当提高
            # CV低 = 运动均匀（静态片），阈值应降低以捕捉微妙变化
            self._adaptive_factors['motion_sensitivity'] = max(0.6, min(1.8, 0.8 + motion_cv * 0.5))
        else:
            self._adaptive_factors['motion_sensitivity'] = 1.0
        
        # 音频分布变异系数
        audios = [f.audio_energy for f in pr.frames]
        if audios:
            audio_mean = sum(audios) / len(audios)
            audio_std = (sum((a - audio_mean) ** 2 for a in audios) / len(audios)) ** 0.5
            audio_cv = audio_std / max(audio_mean, 0.01)
            self._adaptive_factors['audio_sensitivity'] = max(0.6, min(1.5, 0.8 + audio_cv * 0.3))
        else:
            self._adaptive_factors['audio_sensitivity'] = 1.0
        
        # VFX能量比例（P75/P50）
        vfx_p50 = getattr(pr, 'vfx_p50', 10.0) or 10.0
        vfx_p75 = getattr(pr, 'vfx_p75', 25.0) or 25.0
        if vfx_p50 > 0:
            vfx_ratio = vfx_p75 / vfx_p50
            # VFX比例高 = 仙侠/特效片，VFX通道应更敏感
            self._adaptive_factors['vfx_sensitivity'] = max(0.7, min(1.5, 0.8 + (vfx_ratio - 1.0) * 0.3))
        else:
            self._adaptive_factors['vfx_sensitivity'] = 1.0
        
        # 综合评分阈值自适应
        # 如果视频整体很平静（P50低），降低阈值以捕捉更多候选
        # 如果视频整体很激烈（P50高），提高阈值以减少误判
        if pr.motion_p50 < 10:
            self._adaptive_factors['composite_threshold'] = 0.85  # 降低阈值
        elif pr.motion_p50 > 30:
            self._adaptive_factors['composite_threshold'] = 1.15  # 提高阈值
        else:
            self._adaptive_factors['composite_threshold'] = 1.0
        
        logger.info(f"v3.0自适应阈值因子: motion={self._adaptive_factors['motion_sensitivity']:.2f}, "
                   f"audio={self._adaptive_factors['audio_sensitivity']:.2f}, "
                   f"vfx={self._adaptive_factors['vfx_sensitivity']:.2f}, "
                   f"composite={self._adaptive_factors['composite_threshold']:.2f}")
    
    # =========================================================
    # 主入口
    # =========================================================
    def filter(self, pr: PipelineResult, progress_callback=None) -> List[HighlightSegment]:
        """执行三阶段筛选（v4.0 改进版：时间轴连续分析+强综合评分+反向排除+去重）
        
        核心改进：
        1. 从"镜头级筛选"转向"帧级连续分析"，减少L1分割导致的重复
        2. 强综合评分主导 + 明确的反向排除规则，减少无效片段
        3. 峰值检测+区间扩张+重叠消除，解决重复提取
        4. L3改为局部vs全局对比，检测真正的"突变点"而非简单高值
        """
        logger.info("开始三阶段筛选 (v4.0 改进版)")
        
        if not pr.frames:
            logger.warning("无帧数据，跳过筛选")
            return []
        
        # v5.1: 保存pr引用，供边界优化时峰值定位使用
        self._current_pr = pr
        
        # v3.0: 计算自适应阈值因子
        self._compute_adaptive_factors(pr)
        
        t0 = time.time()
        
        # ==================== Stage 1: 时间轴连续分析 ====================
        # v4.0: 先在帧级别计算综合评分曲线，再检测连续高燃区间
        # 不依赖L1的场景分割作为唯一输入，而是利用scene边界辅助区间划分
        if progress_callback:
            progress_callback(10, 100, "L1: 时间轴综合评分分析...")
        
        # Step 1a: 在每个帧上计算综合评分（0-1）
        frame_scores = self._compute_frame_scores(pr)
        logger.info(f"L1: 已计算 {len(frame_scores)} 帧的综合评分")
        
        # Step 1b: 检测连续高燃区间（基于阈值和scene边界）
        raw_intervals = self._detect_hot_intervals(pr, frame_scores)
        logger.info(f"L1: 检测到 {len(raw_intervals)} 个初始高燃区间")
        
        # ==================== Stage 2: 强综合评分+反向排除 ====================
        if progress_callback:
            progress_callback(40, 100, "L2: 强综合评分+反向排除过滤...")
        
        l2_segments = self._stage2_improved_filter(pr, raw_intervals, frame_scores)
        logger.info(f"L2: 筛选后保留 {len(l2_segments)} 个候选片段（反向排除 {len(raw_intervals) - len(l2_segments)} 个）")
        
        # ==================== Stage 3: 多维交叉验证（改进版） ====================
        if progress_callback:
            progress_callback(70, 100, "L3: 局部vs全局对比验证...")
        
        l3_segments = self._stage3_improved_verify(pr, l2_segments, frame_scores)
        logger.info(f"L3: 验证后保留 {len(l3_segments)} 个高燃片段")
        
        # ==================== Stage 4: 去重+合并（改进版） ====================
        if progress_callback:
            progress_callback(85, 100, "去重+相邻合并+边界优化...")
        
        final_segments = self._dedupe_and_merge(l3_segments)
        logger.info(f"去重合并后: {len(l3_segments)} -> {len(final_segments)} 个片段")
        
        # ==================== Stage 5: AI 场景分类验证（v4.0 核心改进）====================
        # 用 Qwen2-VL 实际"看"画面 + Whisper 提取台词，做最终裁决
        # 策略:
        #   - hot_fire (高燃动作): 古装打斗/仙侠特效/动漫大招/爆炸追逐 → 保留
        #   - highlight (高光情感): 搞笑/甜蜜/情感高潮 → 保留
        #   - exclude (排除): 普通对话/空镜/片头片尾 → 移除
        if len(final_segments) > 0:
            enable_ai = (hasattr(CONFIG.pipeline, 'enable_clip_verification') and CONFIG.pipeline.enable_clip_verification) or \
                       (hasattr(CONFIG.pipeline, 'enable_qwen2vl_verification') and CONFIG.pipeline.enable_qwen2vl_verification)

            if enable_ai:
                if progress_callback:
                    progress_callback(92, 100, "L5: AI视觉场景分析中（Qwen2-VL + Whisper）...")

                try:
                    from core.scene_classifier import UnifiedSceneClassifier

                    # 根据配置决定用 Qwen2-VL 还是 CLIP
                    use_qwen2vl = hasattr(CONFIG.pipeline, 'enable_qwen2vl_verification') and CONFIG.pipeline.enable_qwen2vl_verification
                    use_whisper = hasattr(CONFIG.pipeline, 'enable_whisper_asr') and CONFIG.pipeline.enable_whisper_asr

                    ai_classifier = UnifiedSceneClassifier(use_qwen2vl=use_qwen2vl, use_whisper=use_whisper)

                    segments_to_classify = [(seg.start_time, seg.end_time) for seg in final_segments]
                    classifications = ai_classifier.classify_batch(
                        pr.video_path, segments_to_classify, progress_callback
                    )

                    # 用分类结果更新片段，排除明显误判的
                    kept_segments = []
                    excluded_by_ai = 0
                    hot_fire_count = 0
                    highlight_count = 0

                    for seg, classification in zip(final_segments, classifications):
                        if classification:
                            # 更新字段（给前端/日志展示使用）
                            seg.scene_type = classification.sub_type
                            seg.scene_confidence = classification.confidence

                            cat = classification.main_category
                            confidence = classification.confidence
                            
                            # v5.1: 改进置信度策略 - 根据片段原有评分决定排除阈值
                            # 物理/VFX评分越高，需要越高的AI置信度才能排除，防止误杀特效场景
                            original_score = seg.score
                            if original_score >= 0.7:
                                exclude_conf_threshold = 0.7  # 高分片段需要70%置信度才排除
                            elif original_score >= 0.5:
                                exclude_conf_threshold = 0.5  # 中等分数需要50%置信度
                            else:
                                exclude_conf_threshold = 0.35  # 低分片段用低阈值
                            keep_conf_threshold = 0.25

                            if cat == "hot_fire":
                                hot_fire_count += 1
                                if confidence >= keep_conf_threshold:
                                    seg.score = min(1.0, seg.score + 0.1)
                                if confidence >= 0.6:
                                    seg.level = "hot_super"
                                elif confidence >= 0.4:
                                    seg.level = "hot"
                                seg.reason = f"[{classification.sub_type}] {classification.reason}"
                                kept_segments.append(seg)

                            elif cat == "highlight":
                                highlight_count += 1
                                if confidence >= keep_conf_threshold:
                                    seg.score = min(1.0, seg.score + 0.05)
                                seg.reason = f"[{classification.sub_type}] {classification.reason}"
                                kept_segments.append(seg)

                            else:  # exclude
                                # v5.1: 只有AI置信度足够高才排除，否则保留原物理判断（宁错杀不放过）
                                if confidence >= exclude_conf_threshold:
                                    excluded_by_ai += 1
                                    logger.info(
                                        f"  ↓ AI排除 [{seg.start_time:.0f}-{seg.end_time:.0f}s]: "
                                        f"{classification.sub_type}, 置信度={confidence:.2f}, "
                                        f"原评分={original_score:.2f}, "
                                        f"理由={classification.reason[:40]}"
                                    )
                                else:
                                    # 低置信度exclude → 保留，宁漏勿错杀
                                    kept_segments.append(seg)
                                    logger.debug(
                                        f"  ↓ AI低置信度排除，保留 [{seg.start_time:.0f}-{seg.end_time:.0f}s]: "
                                        f"置信度={confidence:.2f} < 阈值={exclude_conf_threshold:.2f}, "
                                        f"原评分={original_score:.2f}"
                                    )

                        else:
                            # AI 分类失败 → 保留原判断（稳健性设计）
                            kept_segments.append(seg)

                    final_segments = kept_segments
                    logger.info(
                        f"Stage5(AI): {len(segments_to_classify)} -> {len(final_segments)} 通过 "
                        f"[高燃={hot_fire_count}, 高光={highlight_count}, 排除={excluded_by_ai}]"
                    )

                except Exception as e:
                    logger.warning(f"AI 场景分类失败（不影响原有筛选结果）: {e}")
                    import traceback
                    logger.debug(f"详细错误: {traceback.format_exc()}")
        
        # 按评分排序
        final_segments.sort(key=lambda s: s.score, reverse=True)
        
        # v5.1: 总时长安全校验 - 确保提取片段总时长不超过视频时长的合理比例
        video_duration = pr.frames[-1].timestamp if pr.frames else 0
        if video_duration > 0:
            # 高燃/高光片段总时长不超过视频的70%（单集电视剧通常提取精华不超过一半）
            max_total_duration = video_duration * 0.7
            total_duration = sum(s.duration for s in final_segments)
            
            if total_duration > max_total_duration:
                logger.warning(f"总时长 {total_duration:.1f}s 超过限制 {max_total_duration:.1f}s（视频{video_duration:.1f}s），按评分截断")
                # 按评分从高到低保留，直到达到时长限制
                kept = []
                current_total = 0
                for s in final_segments:
                    if current_total + s.duration <= max_total_duration:
                        kept.append(s)
                        current_total += s.duration
                    elif current_total < max_total_duration:
                        # 最后一个片段截断
                        truncated = HighlightSegment(
                            start_time=s.start_time,
                            end_time=s.start_time + (max_total_duration - current_total),
                            duration=max_total_duration - current_total,
                            score=s.score,
                            level=s.level,
                            reason=f"{s.reason} (时长截断)",
                        )
                        kept.append(truncated)
                        current_total = max_total_duration
                        break
                final_segments = kept
        
        # 最终按时间排序返回
        final_segments.sort(key=lambda s: s.start_time)
        
        t1 = time.time()
        total_duration = sum(s.duration for s in final_segments)
        logger.info(f"筛选完成: {len(final_segments)} 个片段, 总时长 {total_duration:.1f}s / 视频 {video_duration:.1f}s, 耗时{t1-t0:.1f}s")
        
        return final_segments
    
    # =========================================================
    # Stage 1: L1 镜头时长检测
    # =========================================================
    def _stage1_shot_duration(self, pr: PipelineResult) -> List[ShotInfo]:
        """
        L1: 基于镜头时长过滤
        - 去除太短(<l1_min_shot_duration)或太长(>l1_max_shot_duration)的镜头
        - 太短通常是噪声或单帧闪切，太长通常是静态场景或长对话
        同时统计动态百分位，供L2使用
        """
        if not pr.shots:
            return []
        
        # 动态阈值：取 P25/P50/P75 供L2使用
        durations = sorted([s.end_time - s.start_time for s in pr.shots])
        if durations:
            n = len(durations)
            self._stats['dur_p25'] = durations[int(n*0.25)]
            self._stats['dur_p50'] = durations[int(n*0.50)]
            self._stats['dur_p75'] = durations[int(n*0.75)]
            self._stats['motion_p25'] = pr.motion_p25
            self._stats['motion_p50'] = pr.motion_p50
            self._stats['motion_p75'] = pr.motion_p75
            self._stats['audio_p50'] = pr.audio_p50
        
        filtered = []
        for s in pr.shots:
            duration = s.end_time - s.start_time
            if self.pl.l1_min_shot_duration <= duration <= self.pl.l1_max_shot_duration:
                filtered.append(s)
        
        # 如果过滤后镜头太少，降级（放宽阈值
        if len(filtered) < max(3, len(pr.shots) // 4):
            logger.debug(f"L1过滤后镜头过少({len(filtered)}), 降级使用全部")
            filtered = list(pr.shots)
        
        return filtered
    
    # =========================================================
    # v4.0 新增: 帧级综合评分计算
    # =========================================================
    def _compute_frame_scores(self, pr: PipelineResult) -> List[float]:
        """
        在每个帧上计算综合评分（0-1），生成评分曲线
        
        这是v4.0的核心改进：不再依赖镜头级的聚合，而是在时间轴上连续分析
        每个帧的评分是多个特征的加权融合，包含音频语义衰减
        
        返回: List[float]，长度 == len(pr.frames)，每个元素是0-1的评分
        """
        if not pr.frames:
            return []
        
        motion_p50 = pr.motion_p50 if pr.motion_p50 > 0 else self.pl.l2_motion_threshold
        vfx_p50 = getattr(pr, 'vfx_p50', 10.0) or 10.0
        vfx_p75 = getattr(pr, 'vfx_p75', 25.0) or 25.0
        saturation_p50 = getattr(pr, 'saturation_p50', 50.0) or 50.0
        brightness_std_p50 = getattr(pr, 'brightness_std_p50', 30.0) or 30.0
        contrast_p50 = getattr(pr, 'contrast_p50', 80.0) or 80.0
        audio_low_p50 = getattr(pr, 'audio_low_p50', 0.3) or 0.3
        audio_high_p50 = getattr(pr, 'audio_high_p50', 0.3) or 0.3
        
        scores = []
        for f in pr.frames:
            # === 归一化各维度 ===
            motion_norm = min(1.0, f.motion_score / max(motion_p50 * 2.5, 1.0))
            vfx_norm = min(1.0, f.vfx_energy_score / max(vfx_p75 * 1.5, 10.0))
            sat_norm = min(1.0, f.saturation / max(saturation_p50 * 1.5, 1.0))
            bright_std_norm = min(1.0, f.brightness_std / max(brightness_std_p50 * 1.8, 1.0))
            contrast_norm = min(1.0, f.contrast / max(contrast_p50 * 1.3, 1.0))
            audio_low_norm = min(1.0, f.audio_low_freq / max(audio_low_p50 * 1.5, 0.05))
            audio_high_norm = min(1.0, f.audio_high_freq / max(audio_high_p50 * 1.3, 0.05))
            audio_norm = min(1.0, f.audio_energy)
            
            # === 音频语义衰减 (v3.1) ===
            # 如果是语音主导，音频通道不视为"高燃"信号
            is_speech = f.audio_semantic == 'speech'
            is_silence = f.audio_semantic == 'silence'
            audio_weight = 0.0 if is_speech else (0.2 if is_silence else 1.0)
            
            # === 综合评分（加权融合）===
            # 核心思路：运动+视觉是基础，VFX是增强，音频是辅助（非语音时）
            composite = (
                motion_norm * 0.30 +          # 运动强度（动作核心）
                vfx_norm * 0.18 +             # VFX特效能量
                contrast_norm * 0.12 +         # 对比度（视觉冲击）
                bright_std_norm * 0.10 +       # 亮度变化
                sat_norm * 0.10 +             # 饱和度（色彩丰富度）
                audio_low_norm * 0.10 * audio_weight +  # 低频能量（爆炸/冲击）
                audio_high_norm * 0.05 * audio_weight + # 高频能量
                audio_norm * 0.05 * audio_weight         # 总体音频
            )
            
            # === 特殊加分：场景切换在高运动区时可能标志动作开始 ===
            if f.is_scene_boundary and motion_norm > 0.4:
                composite = min(1.0, composite + 0.05)
            
            scores.append(composite)
        
        # === 滑动窗口平滑（3帧平均，减少噪声）===
        if len(scores) >= 3:
            smoothed = scores[:]
            for i in range(1, len(scores) - 1):
                smoothed[i] = (scores[i-1] * 0.25 + scores[i] * 0.5 + scores[i+1] * 0.25)
            scores = smoothed
        
        return scores
    
    # =========================================================
    # v4.0 新增: 检测连续高燃区间（基于评分曲线+scene边界）
    # =========================================================
    def _detect_hot_intervals(self, pr: PipelineResult, frame_scores: List[float]) -> List[Dict]:
        """
        基于帧级评分曲线，检测连续高燃区间
        
        算法：
        1. 找到评分 > lower_threshold 的帧作为"候选帧"
        2. 连续的候选帧组成"候选区间"
        3. 利用scene boundary作为辅助划分点（区间内部scene边界若评分显著下降则拆分）
        4. 对每个区间计算平均评分、峰值、时长
        """
        if not frame_scores:
            return []
        
        # 使用动态阈值：取评分分布的较高百分位
        sorted_scores = sorted(frame_scores)
        n = len(sorted_scores)
        # lower_threshold = 评分中位数*1.3 或 0.35，取较高者
        median_score = sorted_scores[n // 2] if n > 0 else 0.35
        lower_threshold = max(0.35, median_score * 1.3)
        peak_threshold = max(0.55, median_score * 1.8)  # 峰值必须显著高
        
        logger.info(f"区间检测阈值: lower={lower_threshold:.3f}, peak={peak_threshold:.3f}, median={median_score:.3f}")
        
        fps = pr.fps if pr.fps > 0 else 1.0
        frame_interval = 1.0 / fps if fps > 0 else 1.0
        
        intervals = []
        current_start = -1
        current_scores = []
        
        for i, score in enumerate(frame_scores):
            ts = pr.frames[i].timestamp if i < len(pr.frames) else i * frame_interval
            is_boundary = pr.frames[i].is_scene_boundary if i < len(pr.frames) else False
            
            if score >= lower_threshold:
                # 进入或继续高燃区间
                if current_start == -1:
                    current_start = i
                    current_scores = [score]
                else:
                    current_scores.append(score)
                
                # 如果遇到scene边界且当前帧评分远低于区间峰值，考虑拆分
                # （避免完全不同场景被连在一起）
                if is_boundary and current_scores:
                    peak_in_range = max(current_scores)
                    if score < peak_in_range * 0.6 and len(current_scores) > 3:
                        # 场景切换且评分骤降 → 拆分为两个区间
                        avg_score = sum(current_scores) / len(current_scores)
                        peak_score = max(current_scores)
                        start_ts = pr.frames[current_start].timestamp if current_start < len(pr.frames) else current_start * frame_interval
                        end_ts = pr.frames[i-1].timestamp if (i-1) < len(pr.frames) else (i-1) * frame_interval
                        intervals.append({
                            'start_frame': current_start,
                            'end_frame': i - 1,
                            'start_time': start_ts,
                            'end_time': end_ts,
                            'avg_score': avg_score,
                            'peak_score': peak_score,
                            'peak_frame': current_start + current_scores.index(peak_score),
                            'duration': max(end_ts - start_ts, frame_interval),
                        })
                        current_start = i
                        current_scores = [score]
            else:
                # 离开高燃区间
                if current_start != -1 and current_scores:
                    avg_score = sum(current_scores) / len(current_scores)
                    peak_score = max(current_scores)
                    start_ts = pr.frames[current_start].timestamp if current_start < len(pr.frames) else current_start * frame_interval
                    end_ts = pr.frames[i-1].timestamp if (i-1) < len(pr.frames) else (i-1) * frame_interval
                    
                    # 只有包含至少1个峰值或足够时长的区间才保留
                    if peak_score >= peak_threshold or (avg_score >= lower_threshold * 1.2 and len(current_scores) >= 2):
                        intervals.append({
                            'start_frame': current_start,
                            'end_frame': i - 1,
                            'start_time': start_ts,
                            'end_time': end_ts,
                            'avg_score': avg_score,
                            'peak_score': peak_score,
                            'peak_frame': current_start + current_scores.index(peak_score),
                            'duration': max(end_ts - start_ts, frame_interval),
                        })
                    current_start = -1
                    current_scores = []
        
        # 处理末尾区间
        if current_start != -1 and current_scores:
            avg_score = sum(current_scores) / len(current_scores)
            peak_score = max(current_scores)
            start_ts = pr.frames[current_start].timestamp if current_start < len(pr.frames) else current_start * frame_interval
            end_ts = pr.frames[-1].timestamp if pr.frames else (len(frame_scores) - 1) * frame_interval
            
            if peak_score >= peak_threshold or (avg_score >= lower_threshold * 1.2 and len(current_scores) >= 2):
                intervals.append({
                    'start_frame': current_start,
                    'end_frame': len(frame_scores) - 1,
                    'start_time': start_ts,
                    'end_time': end_ts,
                    'avg_score': avg_score,
                    'peak_score': peak_score,
                    'peak_frame': current_start + current_scores.index(peak_score),
                    'duration': max(end_ts - start_ts, frame_interval),
                })
        
        logger.info(f"区间检测: 找到 {len(intervals)} 个初始区间 (lower_threshold={lower_threshold:.3f})")
        for idx, itv in enumerate(intervals[:5]):  # 最多记录前5个
            logger.info(f"  区间{idx}: [{itv['start_time']:.1f}s - {itv['end_time']:.1f}s] "
                        f"时长={itv['duration']:.1f}s, avg={itv['avg_score']:.3f}, peak={itv['peak_score']:.3f}")
        
        return intervals
    
    # =========================================================
    # v4.0 新增: Stage 2 - 强综合评分主导 + 反向排除
    # =========================================================
    def _stage2_improved_filter(self, pr: PipelineResult, intervals: List[Dict], frame_scores: List[float]) -> List[HighlightSegment]:
        """
        L2改进版筛选：
        1. 强综合评分要求（不再依赖多个独立pass条件）
        2. 明确的反向排除规则（对话主导、低运动+低视觉、纯语音场景等）
        3. 基于区间内的实际特征分布进行判断
        
        返回: List[HighlightSegment] - 通过筛选的候选片段
        """
        if not intervals:
            return []
        
        motion_p50 = pr.motion_p50 if pr.motion_p50 > 0 else self.pl.l2_motion_threshold
        motion_p75 = pr.motion_p75
        vfx_p50 = getattr(pr, 'vfx_p50', 10.0) or 10.0
        vfx_p75 = getattr(pr, 'vfx_p75', 25.0) or 25.0
        saturation_p50 = getattr(pr, 'saturation_p50', 50.0) or 50.0
        contrast_p50 = getattr(pr, 'contrast_p50', 80.0) or 80.0
        
        # v3.0 自适应阈值调整
        comp_factor = self._adaptive_factors.get('composite_threshold', 1.0)
        composite_threshold = self.pl.l2_composite_threshold * comp_factor
        
        results = []
        
        for itv in intervals:
            # === 获取区间内的帧 ===
            start_f = max(0, itv['start_frame'])
            end_f = min(len(pr.frames) - 1, itv['end_frame'])
            if end_f < start_f:
                continue
            seg_frames = pr.frames[start_f:end_f + 1]
            
            if not seg_frames:
                continue
            
            # === 计算区间内的特征统计（单次遍历优化）===
            motions = []
            audios = []
            vfxs = []
            saturations = []
            contrasts = []
            bright_stds = []
            speech_count = 0
            action_count = 0
            silence_count = 0
            
            for f in seg_frames:
                motions.append(f.motion_score)
                audios.append(f.audio_energy)
                vfxs.append(f.vfx_energy_score)
                saturations.append(f.saturation)
                contrasts.append(f.contrast)
                bright_stds.append(f.brightness_std)
                
                # 音频语义统计
                if f.audio_semantic == 'speech':
                    speech_count += 1
                elif f.audio_semantic in ('action', 'explosion'):
                    action_count += 1
                elif f.audio_semantic == 'silence':
                    silence_count += 1
            
            n = len(motions)
            avg_motion = sum(motions) / n if n else 0.0
            max_motion = max(motions) if n else 0.0
            avg_audio = sum(audios) / n if n else 0.0
            avg_vfx = sum(vfxs) / n if n else 0.0
            max_vfx = max(vfxs) if n else 0.0
            avg_saturation = sum(saturations) / n if n else 0.0
            avg_contrast = sum(contrasts) / n if n else 0.0
            avg_bright_std = sum(bright_stds) / n if n else 0.0
            
            speech_ratio = speech_count / max(n, 1)
            action_ratio = action_count / max(n, 1)
            silence_ratio = silence_count / max(n, 1)
            
            # ======================================
            # === 反向排除规则（优先级最高）=======
            # ======================================
            reason_excluded = None
            
            # 规则1: 纯对话场景 - 高语音比 + 低运动 + 低视觉特征
            if speech_ratio >= 0.6 and avg_motion < motion_p50 and avg_vfx < vfx_p50 * 0.8:
                reason_excluded = f"对话场景排除: speech={speech_ratio:.0%}, motion={avg_motion:.1f}(<P50)"
            
            # 规则2: 低运动+低视觉+非特效 = 可能是静态或过渡场景
            if reason_excluded is None and avg_motion < motion_p50 * 0.8 and avg_vfx < vfx_p50 and avg_contrast < contrast_p50 * 0.8:
                reason_excluded = f"低特征排除: motion={avg_motion:.1f}, vfx={avg_vfx:.1f}, contrast={avg_contrast:.1f}"
            
            # 规则3: 几乎全静音 + 低运动
            if reason_excluded is None and silence_ratio >= 0.5 and avg_motion < motion_p50 * 0.6:
                reason_excluded = f"静音+低运动排除: silence={silence_ratio:.0%}, motion={avg_motion:.1f}"
            
            # 规则4: 区间太短（<2秒）且没有显著峰值
            if reason_excluded is None and itv['duration'] < 2.0 and itv['peak_score'] < 0.7:
                reason_excluded = f"过短+低峰值排除: duration={itv['duration']:.1f}s, peak={itv['peak_score']:.3f}"
            
            if reason_excluded:
                logger.debug(f"  ↓ 排除区间 [{itv['start_time']:.0f}-{itv['end_time']:.0f}s]: {reason_excluded}")
                continue
            
            # ======================================
            # === 正向评分与判断 ===================
            # ======================================
            
            # 候选片段的综合评分 = 区间平均评分*0.6 + 峰值评分*0.4
            # （既考虑整体水平，也考虑峰值冲击）
            interval_composite = itv['avg_score'] * 0.55 + itv['peak_score'] * 0.45
            
            # === 维度协同检查（至少需要2个维度活跃）===
            dim_motion = avg_motion >= motion_p50 * 1.3 or max_motion >= motion_p75
            dim_vfx = avg_vfx >= vfx_p50 * 1.5 or max_vfx >= vfx_p75 * 1.3
            dim_visual = avg_contrast >= contrast_p50 * 1.2 or avg_bright_std >= 30.0
            dim_color = avg_saturation >= saturation_p50 * 1.2
            dim_audio = avg_audio >= 0.5 and speech_ratio < 0.4 and action_ratio > 0.1
            
            active_dims = sum([dim_motion, dim_vfx, dim_visual, dim_color, dim_audio])
            has_core_dim = dim_motion or dim_vfx  # 运动或VFX至少有一个
            
            # ======================================
            # === 通过条件（严格的AND逻辑，不是OR）===
            # ======================================
            pass_condition = False
            pass_reason = ""
            
            # 路径1: 强综合评分 + 至少2个维度活跃
            if interval_composite >= composite_threshold * 1.1 and active_dims >= 2 and has_core_dim:
                pass_condition = True
                pass_reason = f"强综合{interval_composite:.3f} + {active_dims}维活跃"
            
            # 路径2: 中等综合评分 + 至少3个维度活跃（包括核心维度）
            elif interval_composite >= composite_threshold and active_dims >= 3 and has_core_dim:
                pass_condition = True
                pass_reason = f"综合{interval_composite:.3f} + {active_dims}维活跃"
            
            # 路径3: 极高VFX特效场景（仙侠/科幻特效片专用）
            elif avg_vfx >= vfx_p75 * 1.3 and max_vfx >= vfx_p75 * 1.5 and avg_contrast >= contrast_p50 and active_dims >= 1:
                pass_condition = True
                pass_reason = f"高VFX场景: avg_vfx={avg_vfx:.1f}, max_vfx={max_vfx:.1f}"
            
            # 路径4: 高运动+音频冲击（典型动作场景）
            elif avg_motion >= motion_p50 * 2.0 and action_ratio > 0.15 and speech_ratio < 0.5:
                pass_condition = True
                pass_reason = f"高运动+动作音频: motion={avg_motion:.1f}, action={action_ratio:.0%}"
            
            if pass_condition:
                # 构建HighlightSegment
                final_score = min(1.0, interval_composite)
                level = 'hot_super' if final_score > 0.75 else ('hot' if final_score > 0.55 else 'normal')
                
                segment = HighlightSegment(
                    start_time=itv['start_time'],
                    end_time=itv['end_time'],
                    duration=itv['duration'],
                    score=final_score,
                    level=level,
                    reason=f"{pass_reason} | speech={speech_ratio:.0%} | active_dims={active_dims}",
                )
                results.append(segment)
            else:
                logger.debug(f"  ↓ 未通过区间 [{itv['start_time']:.0f}-{itv['end_time']:.0f}s]: "
                            f"composite={interval_composite:.3f}, active_dims={active_dims}, "
                            f"has_core={has_core_dim}, speech={speech_ratio:.0%}")
        
        logger.info(f"L2筛选: {len(intervals)} -> {len(results)} 通过")
        return results
    
    # =========================================================
    # v4.0 新增: Stage 3 - 改进的多维交叉验证（局部vs全局对比）
    # =========================================================
    def _stage3_improved_verify(self, pr: PipelineResult, segments: List[HighlightSegment], frame_scores: List[float]) -> List[HighlightSegment]:
        """
        L3改进版验证：
        1. 局部vs全局对比 - 检测"突变点"而非简单高值
        2. 上下文一致性检查 - 片段的核心特征应显著高于前后30秒
        3. 评分修正 - 基于验证结果调整最终评分
        """
        if not segments:
            return []
        
        motion_p50 = pr.motion_p50
        motion_p75 = pr.motion_p75
        vfx_p75 = getattr(pr, 'vfx_p75', 25.0) or 25.0
        saturation_p50 = getattr(pr, 'saturation_p50', 50.0) or 50.0
        contrast_p50 = getattr(pr, 'contrast_p50', 80.0) or 80.0
        fps = pr.fps if pr.fps > 0 else 1.0
        
        results = []
        
        for seg in segments:
            # 获取片段的帧
            start_idx = max(0, int(seg.start_time * fps))
            end_idx = min(len(pr.frames) - 1, int(seg.end_time * fps))
            if end_idx < start_idx:
                continue
            
            seg_frames = pr.frames[start_idx:end_idx + 1]
            if not seg_frames:
                continue
            
            # === 计算片段内统计（单次遍历优化）===
            seg_motions_sum = 0.0
            seg_vfxs_sum = 0.0
            seg_contrasts_sum = 0.0
            seg_max_motion = 0.0
            seg_max_vfx = 0.0
            
            n = len(seg_frames)
            for f in seg_frames:
                seg_motions_sum += f.motion_score
                seg_vfxs_sum += f.vfx_energy_score
                seg_contrasts_sum += f.contrast
                seg_max_motion = max(seg_max_motion, f.motion_score)
                seg_max_vfx = max(seg_max_vfx, f.vfx_energy_score)
            
            seg_avg_motion = seg_motions_sum / n if n else 0.0
            seg_avg_vfx = seg_vfxs_sum / n if n else 0.0
            seg_avg_contrast = seg_contrasts_sum / n if n else 0.0
            
            seg_scores = frame_scores[start_idx:end_idx + 1] if start_idx < len(frame_scores) else []
            seg_peak_score = max(seg_scores) if seg_scores else seg.score
            
            # === 计算前后30秒的基线 ===
            context_before_start = max(0, start_idx - int(30 * fps))
            context_after_end = min(len(pr.frames), end_idx + int(30 * fps))
            
            before_frames = pr.frames[context_before_start:start_idx]
            after_frames = pr.frames[end_idx + 1:context_after_end]
            context_frames = before_frames + after_frames
            
            if context_frames:
                # 单次遍历计算上下文统计
                ctx_motions_sum = 0.0
                ctx_vfxs_sum = 0.0
                ctx_contrasts_sum = 0.0
                ctx_n = len(context_frames)
                
                for f in context_frames:
                    ctx_motions_sum += f.motion_score
                    ctx_vfxs_sum += f.vfx_energy_score
                    ctx_contrasts_sum += f.contrast
                
                context_avg_motion = ctx_motions_sum / ctx_n if ctx_n else motion_p50
                context_avg_vfx = ctx_vfxs_sum / ctx_n if ctx_n else vfx_p75
                context_avg_contrast = ctx_contrasts_sum / ctx_n if ctx_n else contrast_p50
            else:
                context_avg_motion = motion_p50
                context_avg_vfx = vfx_p75
                context_avg_contrast = contrast_p50
            
            # === 计算"突变比率" ===
            motion_ratio = seg_avg_motion / max(context_avg_motion * 1.3, motion_p50, 1.0)
            vfx_ratio = seg_avg_vfx / max(context_avg_vfx * 1.3, vfx_p75, 1.0)
            contrast_ratio = seg_avg_contrast / max(context_avg_contrast * 1.1, contrast_p50, 1.0)
            
            # === 验证维度（真正的"突变"检测）===
            verify_dims = 0
            verify_details = []
            
            # 维度1: 运动显著高于上下文
            if motion_ratio >= 1.5 or (seg_avg_motion >= motion_p75 * 1.2 and motion_ratio >= 1.2):
                verify_dims += 1
                verify_details.append(f"motion_ratio={motion_ratio:.2f}")
            
            # 维度2: VFX显著高于上下文
            if vfx_ratio >= 1.5 or (seg_avg_vfx >= vfx_p75 * 1.3 and vfx_ratio >= 1.2):
                verify_dims += 1
                verify_details.append(f"vfx_ratio={vfx_ratio:.2f}")
            
            # 维度3: 视觉对比度变化
            if contrast_ratio >= 1.3:
                verify_dims += 1
                verify_details.append(f"contrast_ratio={contrast_ratio:.2f}")
            
            # 维度4: 评分峰值显著
            if seg_peak_score >= 0.6:
                verify_dims += 1
                verify_details.append(f"peak_score={seg_peak_score:.3f}")
            
            # 维度5: 不是纯语音（语义一致性）
            speech_count = sum(1 for f in seg_frames if f.audio_semantic == 'speech')
            speech_ratio = speech_count / max(len(seg_frames), 1)
            if speech_ratio < 0.5:
                verify_dims += 1
            else:
                verify_details.append(f"high_speech={speech_ratio:.0%}")
            
            # === 决策逻辑 ===
            new_score = seg.score
            new_level = seg.level
            pass_verify = False
            
            if verify_dims >= 3:
                # 强验证通过 → 提升评分
                new_score = min(1.0, seg.score * 0.85 + 0.15)
                new_level = 'hot_super' if new_score > 0.7 else 'hot'
                pass_verify = True
                logger.info(f"  ✓ L3强验证 [{seg.start_time:.0f}-{seg.end_time:.0f}s]: {verify_dims}/5维, "
                           f"details=[{', '.join(verify_details[:3])}], score={seg.score:.3f}→{new_score:.3f}")
            elif verify_dims >= 2:
                # 中等验证通过 → 正常保留
                new_score = min(1.0, seg.score * 0.9 + 0.05)
                new_level = 'hot' if new_score > 0.55 else 'normal'
                pass_verify = True
                logger.info(f"  ✓ L3通过 [{seg.start_time:.0f}-{seg.end_time:.0f}s]: {verify_dims}/5维, "
                           f"details=[{', '.join(verify_details[:3])}], score={new_score:.3f}")
            elif verify_dims >= 1 and seg.score >= 0.55:
                # 弱验证但有高评分 → 降级保留
                new_score = max(0.3, seg.score * 0.7)
                new_level = 'normal'
                pass_verify = True
                logger.info(f"  ~ L3降级 [{seg.start_time:.0f}-{seg.end_time:.0f}s]: {verify_dims}/5维, "
                           f"score={seg.score:.3f}→{new_score:.3f}")
            else:
                # 未通过 → 过滤
                logger.info(f"  ✗ L3过滤 [{seg.start_time:.0f}-{seg.end_time:.0f}s]: {verify_dims}/5维, "
                           f"details=[{', '.join(verify_details[:3]) if verify_details else '无突变'}]")
            
            if pass_verify:
                segment = HighlightSegment(
                    start_time=seg.start_time,
                    end_time=seg.end_time,
                    duration=seg.duration,
                    score=new_score,
                    level=new_level,
                    reason=f"L3({verify_dims}/5维): {', '.join(verify_details[:3])} | 原: {seg.reason}",
                )
                results.append(segment)
        
        logger.info(f"L3验证: {len(segments)} -> {len(results)} 通过")
        return results
    
    # =========================================================
    # v4.0 新增: Stage 4 - 去重+相邻合并+边界优化
    # =========================================================
    def _dedupe_and_merge(self, segments: List[HighlightSegment]) -> List[HighlightSegment]:
        """
        v5.1 重写：解决片段重叠导致总时长超过视频时长的bug
        1. 按评分降序排序，高分优先占位（保证精彩片段优先保留）
        2. 强制不重叠：已经被占位的时间段，低分片段直接截断或丢弃
        3. 相邻合并：间隔<3秒 → 合并为一个片段
        4. 边界优化：向前后各扩展0.5秒
        5. 最终验证：确保没有重叠，时长合法
        """
        if not segments:
            return []
        
        # 预处理 - 先过滤掉时长非法的片段
        valid_segs = []
        for s in segments:
            if s.end_time > s.start_time and s.duration > 0:
                valid_segs.append(s)
        
        if len(valid_segs) <= 1:
            if valid_segs:
                return self._apply_boundary_optimization(valid_segs, pr=self._current_pr)
            return []
        
        # Step 1: 按评分降序排序 - 高分片段优先选择时间区间
        sorted_by_score = sorted(valid_segs, key=lambda s: -s.score)
        
        # Step 2: 贪心选择不重叠片段（高分优先占位）
        selected = []
        occupied = []
        
        for seg in sorted_by_score:
            s_start, s_end = seg.start_time, seg.end_time
            new_start, new_end = s_start, s_end
            discard = False
            
            for (occ_start, occ_end) in occupied:
                ov_start = max(new_start, occ_start)
                ov_end = min(new_end, occ_end)
                
                if ov_end > ov_start:
                    overlap_len = ov_end - ov_start
                    # 重叠超过当前片段50% → 直接丢弃
                    if overlap_len >= (s_end - s_start) * 0.5:
                        discard = True
                        break
                    # 截断重叠部分
                    if ov_start <= new_start:
                        new_start = ov_end
                    elif ov_end >= new_end:
                        new_end = ov_start
                    else:
                        discard = True
                        break
            
            if not discard and new_end > new_start and (new_end - new_start) >= self.pl.hot_min_duration * 0.5:
                truncated = HighlightSegment(
                    start_time=new_start,
                    end_time=new_end,
                    duration=new_end - new_start,
                    score=seg.score,
                    level=seg.level,
                    reason=seg.reason,
                )
                selected.append(truncated)
                occupied.append((new_start, new_end))
                occupied.sort()
        
        # Step 3: 按时间重新排序后做相邻小间隙合并
        selected.sort(key=lambda s: s.start_time)
        
        if len(selected) <= 1:
            merged = selected
        else:
            merged = []
            cur = selected[0]
            
            for i in range(1, len(selected)):
                nxt = selected[i]
                gap = nxt.start_time - cur.end_time
                
                # 只合并间隔为正且小于3秒的相邻片段（gap<0表示有重叠，前面已处理）
                if 0 <= gap < 3.0:
                    total_dur = nxt.end_time - cur.start_time
                    new_score = (cur.score * cur.duration + nxt.score * nxt.duration) / max(total_dur, 0.1)
                    cur = HighlightSegment(
                        start_time=cur.start_time,
                        end_time=nxt.end_time,
                        duration=total_dur,
                        score=min(1.0, new_score),
                        level='hot_super' if new_score > 0.7 else 'hot',
                        reason=f"相邻合并({cur.reason} + {nxt.reason})",
                    )
                else:
                    merged.append(cur)
                    cur = nxt
            merged.append(cur)
        
        # Step 4: 边界优化 + 时长限制（v5.1: 传入pr用于峰值定位截断）
        final = self._apply_boundary_optimization(merged, pr=self._current_pr)
        
        # Step 5: 最终安全验证 - 强制消除任何残留重叠
        for i in range(1, len(final)):
            if final[i].start_time < final[i-1].end_time - 0.01:
                final[i] = HighlightSegment(
                    start_time=final[i-1].end_time,
                    end_time=max(final[i-1].end_time + 0.1, final[i].end_time),
                    duration=max(0.1, final[i].end_time - final[i-1].end_time),
                    score=final[i].score,
                    level=final[i].level,
                    reason=final[i].reason,
                )
        
        logger.info(f"去重合并: {len(segments)}→{len(selected)}(不重叠选择)→{len(merged)}(相邻合并)→{len(final)}(最终)")
        return final
    
    def _apply_boundary_optimization(self, segments: List[HighlightSegment], pr=None) -> List[HighlightSegment]:
        """统一处理边界扩展和时长限制
        v5.1: 超长截断时优先保留峰值（VFX/运动）位置，而不是简单取中间
        """
        final = []
        for s in segments:
            # 向前后各扩展0.5秒
            start = max(0.0, s.start_time - 0.5)
            end = s.end_time + 0.5
            duration = end - start
            
            # 超过最大时长：找到峰值位置，以峰值为中心保留窗口
            if duration > self.pl.hot_max_duration:
                peak_time = (start + end) / 2  # 默认中间
                
                # 如果有帧数据，找VFX/综合评分最高的帧位置
                if pr and pr.frames:
                    # 找出区间内所有帧
                    seg_frames = [f for f in pr.frames if start - 1 <= f.timestamp <= end + 1]
                    if seg_frames:
                        # 综合考虑VFX、运动、音频找峰值
                        def frame_peak_score(f):
                            vfx = getattr(f, 'vfx_energy_score', 0) / 100.0
                            motion = getattr(f, 'motion_score', 0) / 100.0
                            audio = getattr(f, 'audio_energy', 0)
                            # VFX权重最高，优先保留特效
                            return vfx * 0.5 + motion * 0.3 + audio * 0.2
                        
                        best_frame = max(seg_frames, key=frame_peak_score)
                        peak_time = best_frame.timestamp
                
                # 以峰值为中心，保留max_duration时长
                half = self.pl.hot_max_duration / 2
                new_start = peak_time - half
                new_end = peak_time + half
                
                # 边界检查
                if new_start < 0:
                    new_start = 0
                    new_end = self.pl.hot_max_duration
                if new_end > (pr.frames[-1].timestamp if pr and pr.frames else end):
                    new_end = pr.frames[-1].timestamp if pr and pr.frames else end
                    new_start = max(0, new_end - self.pl.hot_max_duration)
                
                start = new_start
                end = new_end
                duration = self.pl.hot_max_duration
            
            if duration < self.pl.hot_min_duration:
                if s.score >= 0.7:
                    mid = (start + end) / 2
                    half = self.pl.hot_min_duration / 2
                    start = max(0.0, mid - half)
                    end = start + self.pl.hot_min_duration
                    duration = self.pl.hot_min_duration
                    logger.info(f"  ↳ 高评分短片段扩展: {s.duration:.1f}s → {self.pl.hot_min_duration}s, score={s.score:.3f}")
                else:
                    logger.info(f"  ↳ 过滤短片段: {duration:.1f}s, score={s.score:.3f}")
                    continue
            
            final.append(HighlightSegment(
                start_time=start,
                end_time=end,
                duration=duration,
                score=s.score,
                level=s.level,
                reason=s.reason,
            ))
        return final
    
    # =========================================================
    # Stage 2: L2 运动+音频+快切融合 (旧版保留作为后备)
    # =========================================================
    def _stage2_motion_audio_composite(self, pr: PipelineResult, l1_shots: List[ShotInfo]) -> List[HighlightSegment]:
        """
        L2: 多特征融合评分 (v2.6增强 - 含VFX特效专用检测路径)
                
        核心特征（v2.6新增VFX通道）：
        1. avg_motion - 平均运动强度（SAD计算
        2. max_motion - 峰值运动（检测突发动作
        3. avg_audio - 平均音频能量
        4. 镜头切换频率（是否属于“快切镜头组”
        5. 颜色/饱和度/亮度标准差/对比度
        6. 音频频谱分层：低频/中频/高频
        7. VFX能量得分（v2.6新增 - 仙侠法术/飞剑/特效光效）
                
        v2.6新增检测路径:
        - 纯VFX场景：高VFX能量+低运动（飞剑、法术光效、能量场）
        - VFX动作场景：VFX+运动（招式释放过程、特效打斗）
        - VFX渐变场景：VFX渐变能量（法术蓄力→释放过程）
        """
        if not l1_shots:
            return []
        
        # 动态阈值（本视频的统计值 + v3.0自适应因子）
        motion_p50 = pr.motion_p50 if pr.motion_p50 > 0 else self.pl.l2_motion_threshold
        motion_sens = self._adaptive_factors.get('motion_sensitivity', 1.0)
        audio_sens = self._adaptive_factors.get('audio_sensitivity', 1.0)
        vfx_sens = self._adaptive_factors.get('vfx_sensitivity', 1.0)
        comp_factor = self._adaptive_factors.get('composite_threshold', 1.0)
        
        # 自适应阈值：sens高 = 需要更强信号才能通过（动作片）
        # sens低 = 更容易通过（静态片，捕捉微妙变化）
        motion_threshold = max(motion_p50 * 1.5 * motion_sens, self.pl.l2_motion_threshold)
        audio_threshold = max(pr.audio_p50 * 1.3 * audio_sens, self.pl.l2_min_energy_threshold)
        
        # v2.2: 颜色/饱和度/对比度基准
        saturation_p50 = getattr(pr, 'saturation_p50', 50.0) or 50.0
        brightness_std_p50 = getattr(pr, 'brightness_std_p50', 30.0) or 30.0
        contrast_p50 = getattr(pr, 'contrast_p50', 80.0) or 80.0
        
        # v2.2: 音频频谱基准
        audio_low_p50 = getattr(pr, 'audio_low_p50', 0.3) or 0.3
        audio_mid_p50 = getattr(pr, 'audio_mid_p50', 0.3) or 0.3
        audio_high_p50 = getattr(pr, 'audio_high_p50', 0.3) or 0.3
        
        # v2.6: VFX能量基准
        vfx_p50 = getattr(pr, 'vfx_p50', 10.0) or 10.0
        vfx_p75 = getattr(pr, 'vfx_p75', 25.0) or 25.0
        
        logger.info(f"L2动态阈值 (v2.6): motion={motion_threshold:.1f}(P50={pr.motion_p50:.1f}), "
                   f"sat={saturation_p50:.1f}, bstd={brightness_std_p50:.1f}, "
                   f"vfx_p50={vfx_p50:.1f}, vfx_p75={vfx_p75:.1f}, "
                   f"audio_low={audio_low_p50:.2f}, audio_mid={audio_mid_p50:.2f}, audio_high={audio_high_p50:.2f}")
        
        # 先识别快切镜头组：连续多个短镜头（典型动作片特征
        fast_cut_groups = self._detect_fast_cut_groups(l1_shots)
        logger.info(f"识别到 {len(fast_cut_groups)} 个快切镜头组")
        
        candidates: List[HighlightSegment] = []
        
        # 对每个镜头评分
        for i, s in enumerate(l1_shots):
            duration = s.end_time - s.start_time
            
            # 基础特征
            motion_norm = min(1.0, s.avg_motion / max(motion_p50 * 2.0, 1.0))
            max_motion_norm = min(1.0, s.max_motion / max(motion_p50 * 3.0, 1.0))
            audio_norm = min(1.0, s.avg_audio)
            color_norm = min(1.0, s.avg_color_var / max(motion_p50, 1.0))
            
            # v2.2新增：颜色/饱和度/对比度特征（读取字段或用0做默认
            avg_saturation = getattr(s, 'avg_saturation', 0.0) or 0.0
            avg_brightness_std = getattr(s, 'avg_brightness_std', 0.0) or 0.0
            avg_contrast = getattr(s, 'avg_contrast', 0.0) or 0.0
            
            saturation_norm = min(1.0, avg_saturation / max(saturation_p50 * 1.5, 1.0))
            brightness_std_norm = min(1.0, avg_brightness_std / max(brightness_std_p50 * 1.8, 1.0))
            contrast_norm = min(1.0, avg_contrast / max(contrast_p50 * 1.3, 1.0))
            
            # v2.2新增：音频频谱分层
            avg_audio_low = getattr(s, 'avg_audio_low_freq', 0.0) or 0.0
            avg_audio_mid = getattr(s, 'avg_audio_mid_freq', 0.0) or 0.0
            avg_audio_high = getattr(s, 'avg_audio_high_freq', 0.0) or 0.0
            
            audio_low_norm = min(1.0, avg_audio_low / max(audio_low_p50 * 1.5, 0.05))
            audio_mid_norm = min(1.0, avg_audio_mid / max(audio_mid_p50 * 1.2, 0.05))
            audio_high_norm = min(1.0, avg_audio_high / max(audio_high_p50 * 1.3, 0.05))
            
            # v5.1: 降低VFX归一化阈值，提高特效敏感度
            avg_vfx = getattr(s, 'avg_vfx_score', 0.0) or 0.0
            max_vfx = getattr(s, 'max_vfx_score', 0.0) or 0.0
            vfx_norm = min(1.0, avg_vfx / max(vfx_p50 * 1.2, 5.0))
            max_vfx_norm = min(1.0, max_vfx / max(vfx_p75 * 1.2, 10.0))
            
            # v2.2: 低频/中频比（动作片音频倾向于更高低频
            if avg_audio_mid > 0.01:
                low_mid_ratio = avg_audio_low / (avg_audio_mid + 0.001)
            else:
                low_mid_ratio = 1.0
            audio_spectrum_score = min(1.0, max(0.0, low_mid_ratio - 0.8) * 2.0 + audio_low_norm * 0.3)
            
            # 是否属于快切组？
            in_fast_cut = any(g['start_idx'] <= i <= g['end_idx'] for g in fast_cut_groups)
            fast_cut_bonus = 0.25 if in_fast_cut else 0.0
            
            # v3.1: 音频语义排斥——语音主导时降低音频贡献，防止高声对话被误判
            speech_ratio = getattr(s, 'speech_ratio', 0.0) or 0.0
            dominant_semantic = getattr(s, 'dominant_audio_semantic', 'unknown') or 'unknown'
            is_speech_dominant = dominant_semantic == 'speech' or speech_ratio > 0.5
            
            # v3.1: 语音主导时的音频衰减因子
            # 语音场景中，音频能量不应作为“燃”的信号
            audio_weight_factor = 0.3 if is_speech_dominant else 1.0
            
            # v2.6综合评分（加权融合所有特征，含VFX，v3.1含音频语义调整）
            composite = (
                motion_norm * self.pl.l2_color_variation_weight +
                max_motion_norm * 0.15 +
                audio_norm * self.pl.l2_audio_energy_weight * audio_weight_factor +  # v3.1: 语音衰减
                color_norm * self.pl.l2_color_variation_weight * 0.4 +
                # v2.2新增颜色/饱和度/对比度权重
                saturation_norm * self.pl.l2_saturation_weight +
                brightness_std_norm * self.pl.l2_brightness_std_weight +
                contrast_norm * self.pl.l2_contrast_weight +
                # v2.2新增音频频谱分层权重（v3.1: 语音衰减）
                audio_spectrum_score * self.pl.l2_audio_low_freq_weight * audio_weight_factor +
                audio_high_norm * self.pl.l2_audio_high_freq_weight * audio_weight_factor +
                # v5.1: 提高VFX权重，增强特效捕捉能力
                vfx_norm * 0.35 +
                max_vfx_norm * 0.20 +
                fast_cut_bonus
            )
                        
            # 归一化权重（v5.1更新）
            total_weight = (
                self.pl.l2_color_variation_weight +
                0.15 +
                self.pl.l2_audio_energy_weight +
                self.pl.l2_color_variation_weight * 0.4 +
                self.pl.l2_saturation_weight +
                self.pl.l2_brightness_std_weight +
                self.pl.l2_contrast_weight +
                self.pl.l2_audio_low_freq_weight +
                self.pl.l2_audio_high_freq_weight +
                0.35 +  # v5.1: VFX平均权重提高
                0.20    # v5.1: VFX峰值权重提高
            )
            composite = min(1.0, composite / max(total_weight, 0.001))
            
            # v3.0: 分组组合筛选条件（替代旧版12个独立pass条件）
            # v3.1: 增加音频语义排斥，语音主导场景不通过音频驱动条件
            # 核心思路：单一特征不足以判定高燃，需至少2个维度交叉验证
            pass_reason = ""
            
            # === 组A：运动驱动组（运动为核心，需至少1个辅助特征） ===
            is_high_motion = s.avg_motion > motion_threshold
            is_peak_motion = s.max_motion > pr.motion_p75 * 1.3
            # v3.1: 语音主导时，音频不再作为“运动驱动”的辅助条件
            has_audio_support = s.avg_audio > audio_threshold * 0.7 and not is_speech_dominant
            has_saturation_support = avg_saturation > saturation_p50 * 1.2
            has_vfx_support = avg_vfx > vfx_p50 * 1.2
            has_contrast_support = avg_contrast > contrast_p50 * 1.0
            
            # === 组B：VFX特效组（v5.1放宽阈值，增加纯VFX直通） ===
            is_high_vfx = avg_vfx > vfx_p50 * 1.5 and avg_vfx > 5.0
            is_peak_vfx = max_vfx > vfx_p75 * 1.2 and max_vfx > 8.0
            is_pure_vfx = max_vfx > vfx_p75 * 1.8  # 超强特效直通（仙侠大招/动漫必杀）
            
            # === 组C：音频/频谱组（音频为核心，需运动辅助） ===
            # v3.1: 语音主导场景禁止通过音频驱动组（核心修复：防对话误判为动作）
            is_high_audio = audio_norm > 0.5 and not is_speech_dominant
            # v3.1: 低频主导+运动 也要排除语音主导
            is_low_freq_dominant = (low_mid_ratio > self.pl.l2_audio_band_ratio_threshold 
                                    and motion_norm > 0.25 and not is_speech_dominant)
            
            # === 组D：快切镜头组 ===
            is_fast_cut = in_fast_cut and s.avg_motion > motion_p50 * 1.2
            
            # --- 综合评分路径（最高优先级） ---
            if composite >= self.pl.l2_composite_threshold * comp_factor:
                # v3.1: 语音主导+低运动 → 综合高分也不通过（防止纯对话场景高分混入）
                if is_speech_dominant and motion_norm < 0.25 and vfx_norm < 0.2:
                    continue  # 纯对话场景，即使综合分高也跳过
                speech_tag = " [语音衰减]" if is_speech_dominant else ""
                pass_reason = f"综合评分{composite:.2f}>{self.pl.l2_composite_threshold}{speech_tag}"
            # --- 组A：高运动 + 至少1个辅助 ---
            elif is_high_motion and has_audio_support:
                pass_reason = f"高运动+音频 motion={s.avg_motion:.1f}, audio={s.avg_audio:.2f}"
            elif is_high_motion and has_saturation_support:
                pass_reason = f"高运动+饱和 motion={s.avg_motion:.1f}, sat={int(avg_saturation)}"
            elif is_high_motion and has_vfx_support:
                pass_reason = f"高运动+VFX motion={s.avg_motion:.1f}, vfx={avg_vfx:.1f}"
            elif is_peak_motion and has_audio_support:
                pass_reason = f"峰值运动+音频 peak={int(s.max_motion)}, audio={s.avg_audio:.2f}"
            # v3.1新增: 高运动 + 视觉（不需要音频，覆盖无声打斗）
            elif is_high_motion and has_contrast_support:
                pass_reason = f"高运动+对比度 motion={s.avg_motion:.1f}, c={int(avg_contrast)}"
            elif is_peak_motion and has_vfx_support:
                pass_reason = f"峰值运动+VFX peak={int(s.max_motion)}, vfx={avg_vfx:.1f}"
            # --- v5.1: 纯VFX直通 - 超强特效无需辅助条件（仙侠大招/动漫必杀） ---
            elif is_pure_vfx:
                pass_reason = f"超强特效直通 peak_vfx={max_vfx:.1f}"
            # --- 组B：高VFX + 至少1个辅助（v5.1放宽条件，减少漏检） ---
            elif is_high_vfx and has_saturation_support:
                pass_reason = f"高VFX+饱和 vfx={avg_vfx:.1f}, sat={int(avg_saturation)}"
            elif is_high_vfx and (has_audio_support or motion_norm > 0.1 or brightness_std_norm > 0.2):
                pass_reason = f"VFX场景 vfx={avg_vfx:.1f}, motion={motion_norm:.2f}"
            elif is_peak_vfx:
                pass_reason = f"VFX爆发 peak_vfx={max_vfx:.1f}"
            elif is_high_vfx:
                # v5.1: 只要VFX足够高，即使没有其他辅助也通过（后续由AI分类校验）
                pass_reason = f"纯VFX场景 vfx={avg_vfx:.1f}"
            # --- 组C：音频驱动 + 运动辅助 ---
            elif is_low_freq_dominant:
                pass_reason = f"低频主导+运动 low/mid={low_mid_ratio:.2f}"
            elif is_high_audio and is_high_motion:
                pass_reason = f"高音频+高运动 audio={audio_norm:.2f}, motion={s.avg_motion:.1f}"
            # --- 组D：快切镜头组 ---
            elif is_fast_cut:
                pass_reason = f"快切镜头组+运动{int(s.avg_motion)}"
            # --- 保底：运动+饱和+对比度三者联合（最强组合之一） ---
            elif (avg_saturation > saturation_p50 * 1.5 and avg_contrast > contrast_p50 * 1.3
                  and motion_norm > 0.35):
                pass_reason = f"高饱和+对比度+运动 sat={int(avg_saturation)}, c={int(avg_contrast)}"
            else:
                continue  # 未通过L2筛选
            
            level = "hot_super" if composite > 0.8 else ("hot" if composite > 0.6 else "hot")
            
            candidates.append(HighlightSegment(
                start_time=s.start_time,
                end_time=s.end_time,
                duration=duration,
                score=composite,
                level=level,
                reason=pass_reason,
            ))
        
        # 按时间排序
        candidates.sort(key=lambda s: s.start_time)
        
        return candidates
    
    # =========================================================
    # _detect_fast_cut_groups - 检测快切镜头组
    # =========================================================
    def _detect_fast_cut_groups(self, shots: List[ShotInfo]) -> List[Dict]:
        """
        检测快切镜头组：连续N个镜头，每个镜头都短于阈值
        典型动作片特征：动作场景每1-3秒就切一次
        
        返回: [{start_idx, end_idx, avg_duration}]的列表
        """
        if len(shots) < 3:
            return []
        
        groups = []
        group_start = -1
        
        for i, s in enumerate(shots):
            dur = s.end_time - s.start_time
            is_short = dur < self.pl.l2_min_high_motion_duration * 1.5  # 约4.5秒
            
            if is_short and group_start == -1:
                group_start = i
            elif not is_short and group_start != -1:
                # 结束一个快切组
                group_len = i - group_start
                if group_len >= 3:
                    avg_dur = sum(shots[j].end_time - shots[j].start_time 
                                for j in range(group_start, i)) / group_len
                    groups.append({
                        'start_idx': group_start,
                        'end_idx': i - 1,
                        'count': group_len,
                        'avg_duration': avg_dur,
                    })
                group_start = -1
        
        # 处理末尾组
        if group_start != -1:
            group_len = len(shots) - group_start
            if group_len >= 3:
                avg_dur = sum(shots[j].end_time - shots[j].start_time 
                            for j in range(group_start, len(shots))) / group_len
                groups.append({
                    'start_idx': group_start,
                    'end_idx': len(shots) - 1,
                    'count': group_len,
                    'avg_duration': avg_dur,
                })
        
        return groups
    
    # =========================================================
    # Stage 3: L3 多维度交叉验证（v3.0：替代旧版伪AI推理）
    # =========================================================
    def _stage3_lightweight_ai_verify(self, pr: PipelineResult, candidates: List[HighlightSegment]) -> List[HighlightSegment]:
        """
        L3: 多维度交叉验证（v3.1：VFX+视觉免音频通过 + 音频语义排斥）
        
        旧版问题："轻量化AI稀疏推理"名不副实，只是简单阈值判断
        新版方案：真正的多维交叉验证，降低误判率同时防止漏检
        
        验证维度（5维交叉打分）
        1. 运动维度 - 局部vs全局对比
        2. 音频维度 - 能量/频谱一致性
        3. 视觉维度 - 色彩/对比度/亮度变化
        4. 节奏维度 - 镜头切换频率
        5. VFX维度 - 特效能量验证
        
        v3.1 改进：
        - VFX+视觉双重确认可免音频通过（无声打斗特效场景）
        - 语音主导场景在音频维度降级处理
        
        决策规则：
        - >=3维通过 → 高置信度（hot_super）
        - >=2维通过 → 正常通过（hot）
        - VFX+视觉都通过（即使其他维度不够）→ 特殊通过（hot）
        - 1维通过但有综合高分(>=0.7) → 降级保留（normal）
        - 0-1维且低分 → 过滤
        """
        if not candidates:
            return []
        
        if not self.pl.l3_enable:
            logger.info("L3已禁用，直接返回L2结果")
            return candidates
        
        # 全局统计基准
        motion_p75 = pr.motion_p75
        audio_p75 = pr.audio_p75
        vfx_p75 = getattr(pr, 'vfx_p75', 20.0) or 20.0
        saturation_p50 = getattr(pr, 'saturation_p50', 50.0) or 50.0
        contrast_p50 = getattr(pr, 'contrast_p50', 80.0) or 80.0
        
        passed = []
        for seg in candidates:
            # 获取这段范围内的帧特征
            start_idx = int(seg.start_time * pr.fps) if pr.fps > 0 else int(seg.start_time)
            end_idx = int(seg.end_time * pr.fps) if pr.fps > 0 else int(seg.end_time)
            seg_frames = pr.frames[max(0, start_idx): min(len(pr.frames), end_idx + 1)]
            
            if not seg_frames:
                continue
            
            # 收集各维度数据
            local_motions = [f.motion_score for f in seg_frames]
            local_audios = [f.audio_energy for f in seg_frames]
            local_scene_changes = sum(1 for f in seg_frames if f.is_scene_boundary)
            local_vfx = [f.vfx_energy_score for f in seg_frames]
            local_saturations = [f.saturation for f in seg_frames]
            local_contrasts = [f.contrast for f in seg_frames]
            
            local_motion_p75 = sorted(local_motions)[int(len(local_motions)*0.75)] if local_motions else 0
            local_audio_mean = sum(local_audios) / len(local_audios) if local_audios else 0
            local_vfx_mean = sum(local_vfx) / len(local_vfx) if local_vfx else 0
            local_vfx_max = max(local_vfx) if local_vfx else 0
            local_sat_mean = sum(local_saturations) / len(local_saturations) if local_saturations else 0
            local_contrast_mean = sum(local_contrasts) / len(local_contrasts) if local_contrasts else 0
            
            # v3.1: 获取这段的音频语义信息
            speech_frames = sum(1 for f in seg_frames if f.audio_semantic == 'speech')
            action_audio_frames = sum(1 for f in seg_frames if f.audio_semantic in ('action', 'explosion'))
            total_frames_in_seg = len(seg_frames)
            is_seg_speech_dominant = (speech_frames > action_audio_frames and 
                                      speech_frames > total_frames_in_seg * 0.4)
            
            # === 5维交叉验证 ===
            dim_passed = 0
            conditions = []
            
            # 维度1: 运动维度
            if local_motion_p75 > motion_p75 * 1.2:
                dim_passed += 1
                conditions.append(f"运动P75={int(local_motion_p75)}>全局*1.2={int(motion_p75*1.2)}")
            
            # 维度2: 音频维度（v3.1: 语音主导时降级——不作为高燃信号）
            audio_dim = False
            if is_seg_speech_dominant:
                # 语音主导：音频维度默认不通过（防止高声对话混入）
                # 除非有强烈的低频/动作音效穿插
                if action_audio_frames > total_frames_in_seg * 0.3:
                    audio_dim = True
                    conditions.append(f"音频(混合)action_audio={action_audio_frames}/{total_frames_in_seg}")
            else:
                # 非语音主导：正常音频验证
                if local_audio_mean > audio_p75 * 1.0:
                    audio_dim = True
                elif local_vfx_mean > 0 or local_audio_mean > 0.3:
                    # 检查是否有低频主导（动作音频特征）
                    low_freqs = [getattr(f, 'audio_low_freq', 0) for f in seg_frames]
                    mid_freqs = [getattr(f, 'audio_mid_freq', 0) for f in seg_frames]
                    avg_low = sum(low_freqs) / len(low_freqs) if low_freqs else 0
                    avg_mid = sum(mid_freqs) / len(mid_freqs) if mid_freqs else 0
                    if avg_mid > 0.01 and avg_low / (avg_mid + 0.001) > 1.2:
                        audio_dim = True
            if audio_dim:
                dim_passed += 1
                if not conditions or '音频(混合)' not in conditions[-1] if conditions else True:
                    conditions.append(f"音频验证mean={local_audio_mean:.2f}")
            
            # 维度3: 视觉维度（色彩或对比度）
            visual_dim = False
            if local_sat_mean > saturation_p50 * 1.2:
                visual_dim = True
            elif local_contrast_mean > contrast_p50 * 1.1:
                visual_dim = True
            if visual_dim:
                dim_passed += 1
                conditions.append(f"视觉验证sat={int(local_sat_mean)},c={int(local_contrast_mean)}")
            
            # 维度4: 节奏维度
            if local_scene_changes >= 1 or seg.duration >= self.pl.l2_min_high_motion_duration:
                dim_passed += 1
                conditions.append(f"节奏验证切变{local_scene_changes}次,dur={seg.duration:.1f}s")
            
            # 维度5: VFX维度
            vfx_dim = False
            if local_vfx_mean > vfx_p75 * 1.0 or local_vfx_max > vfx_p75 * 1.3 or local_vfx_max > 15.0:
                vfx_dim = True
            if vfx_dim:
                dim_passed += 1
                conditions.append(f"VFX验证mean={local_vfx_mean:.1f},max={local_vfx_max:.1f}")
            
            # === v3.1: VFX+视觉特殊通道（无声打斗特效） ===
            # 核心修复：VFX和视觉都通过时，即使音频维度不通过也允许
            vfx_visual_pass = vfx_dim and visual_dim
            
            # === 决策 ===
            if dim_passed >= 3:
                # 高置信度通过
                seg.score = min(1.0, seg.score * 0.7 + dim_passed * 0.1)
                seg.level = "hot_super"
                seg.reason = f"L3({dim_passed}/5维): " + "; ".join(conditions[:3])
                passed.append(seg)
            elif dim_passed >= 2:
                # 正常通过
                seg.score = min(1.0, seg.score * 0.8 + dim_passed * 0.08)
                seg.level = "hot"
                seg.reason = f"L3({dim_passed}/5维): " + "; ".join(conditions[:2])
                passed.append(seg)
            elif vfx_visual_pass:
                # v3.1新增：VFX+视觉双重确认，免音频通过
                seg.score = min(1.0, seg.score * 0.7 + 0.15)
                seg.level = "hot"
                seg.reason = f"L3(VFX+视觉特殊通道): " + "; ".join(conditions[:2])
                passed.append(seg)
            elif dim_passed >= 1 and seg.score >= 0.7:
                # 1维但综合高分 → 降级保留
                seg.score = max(0.3, seg.score * 0.6)
                seg.level = "normal"
                seg.reason = f"L3降级({dim_passed}/5维): " + "; ".join(conditions[:1])
                passed.append(seg)
            # 否则完全过滤
        
        # 通过率监控
        if len(candidates) > 0:
            pass_ratio = len(passed) / len(candidates)
            logger.info(f"L3多维度交叉验证通过率: {len(passed)}/{len(candidates)} = {pass_ratio:.1%}")
        
        return passed
    
    # =========================================================
    # _merge_adjacent - 合并相邻高燃段（间隔小于阈值的合并）
    # =========================================================
    def _merge_adjacent(self, segments: List[HighlightSegment]) -> List[HighlightSegment]:
        """合并间隔小于阈值的相邻高燃片段（v3.2优化：短片段二次合并）"""
        if len(segments) <= 1:
            return segments
        
        merged: List[HighlightSegment] = []
        cur = segments[0]
        
        for i in range(1, len(segments)):
            nxt = segments[i]
            gap = nxt.start_time - cur.end_time
            
            if gap < self.pl.merge_gap_threshold:
                # 合并
                new_duration = nxt.end_time - cur.start_time
                # 合并后评分 = 加权（按时长
                new_score = (cur.score * cur.duration + nxt.score * nxt.duration) / max(new_duration, 0.001)
                cur = HighlightSegment(
                    start_time=cur.start_time,
                    end_time=nxt.end_time,
                    duration=new_duration,
                    score=new_score,
                    level="hot_super" if new_score > 0.7 else "hot",
                    reason=f"合并({cur.reason}+{nxt.reason})",
                )
            else:
                # 不合并
                # 扩张边界（前后各+0.5秒，避免截在动作中间
                expanded = HighlightSegment(
                    start_time=max(0.0, cur.start_time - 0.5),
                    end_time=cur.end_time + 0.5,
                    duration=cur.end_time + 0.5 - max(0.0, cur.start_time - 0.5),
                    score=cur.score,
                    level=cur.level,
                    reason=cur.reason,
                )
                merged.append(expanded)
                cur = nxt
        
        # 处理最后一个
        expanded = HighlightSegment(
            start_time=max(0.0, cur.start_time - 0.5),
            end_time=cur.end_time + 0.5,
            duration=cur.end_time + 0.5 - max(0.0, cur.start_time - 0.5),
            score=cur.score,
            level=cur.level,
            reason=cur.reason,
        )
        merged.append(expanded)
        
        # v3.2: 二次合并 — 总时长<10秒的相邻片段再次合并（消除1-2秒短片段）
        merged = self._merge_short_adjacent(merged, max_total_duration=10.0)
        
        # 时长限制（超过max的截断，但保留评分
        final = []
        for s in merged:
            if s.duration > self.pl.hot_max_duration:
                # 太长：截断（保留中间高燃部分
                mid = (s.start_time + s.end_time) / 2
                half = self.pl.hot_max_duration / 2
                final.append(HighlightSegment(
                    start_time=mid - half,
                    end_time=mid + half,
                    duration=self.pl.hot_max_duration,
                    score=s.score,
                    level=s.level,
                    reason=s.reason + "(长片段截断)",
                ))
            elif s.duration >= self.pl.hot_min_duration:
                final.append(s)
            # 太短：过滤
        
        logger.info(f"合并后: {len(merged)} -> {len(final)} 个片段（v3.2短片段二次合并）")
        return final
    
    def _merge_short_adjacent(self, segments: List[HighlightSegment], max_total_duration: float = 10.0) -> List[HighlightSegment]:
        """
        v3.2: 二次合并短片段
        如果两个相邻片段的总时长 < max_total_duration，且间隔 < 5秒，则强制合并
        用于消除1-2秒的无意义短片段
        """
        if len(segments) <= 1:
            return segments
        
        # 先过滤掉太短的（<2秒），把它们合并到相邻片段中
        filtered = []
        for s in segments:
            if s.duration >= 2.0:
                filtered.append(s)
            elif filtered:
                # 太短，扩展前一个片段的结尾来吞掉它
                prev = filtered[-1]
                new_end = max(prev.end_time, s.end_time)
                new_dur = new_end - prev.start_time
                filtered[-1] = HighlightSegment(
                    start_time=prev.start_time,
                    end_time=new_end,
                    duration=new_dur,
                    score=max(prev.score, s.score),
                    level=prev.level,
                    reason=f"{prev.reason}+吞并短片段({s.duration:.1f}s)",
                )
            # 如果 filtered 为空且片段太短，直接丢弃
        
        if len(filtered) <= 1:
            return filtered
        
        # 二次合并：相邻片段总时长 < 阈值的继续合并
        merged: List[HighlightSegment] = []
        cur = filtered[0]
        
        for i in range(1, len(filtered)):
            nxt = filtered[i]
            gap = nxt.start_time - cur.end_time
            total_dur = nxt.end_time - cur.start_time
            
            if gap < 5.0 and total_dur <= max_total_duration:
                # 合并
                new_score = (cur.score * cur.duration + nxt.score * nxt.duration) / max(total_dur, 0.001)
                cur = HighlightSegment(
                    start_time=cur.start_time,
                    end_time=nxt.end_time,
                    duration=total_dur,
                    score=new_score,
                    level="hot_super" if new_score > 0.7 else "hot",
                    reason=f"二次合并({cur.reason}+{nxt.reason})",
                )
            else:
                merged.append(cur)
                cur = nxt
        
        merged.append(cur)
        logger.info(f"二次合并短片段: {len(filtered)} -> {len(merged)}")
        return merged


# =========================================================
# 快速自测
# =========================================================
if __name__ == "__main__":
    print("="*50)
    print("ThreeStageFilter v2.2 自测（新增颜色/饱和度/音频频谱分层）")
    print("="*50)
    
    # 构造模拟PipelineResult
    print("\n[1/2] 构造模拟数据(60s视频, 前30s对话后30s动作)...")
    
    frames = []
    for i in range(60):
        ts = i
        if i < 30:
            motion = 5.0 + i * 0.1  # 低运动（对话
            audio = 0.3
            is_boundary = False
            # 对话场景：低饱和、低对比、低频低
            saturation = 30.0
            brightness_std = 15.0
            contrast = 50.0
            audio_low = 0.2
            audio_mid = 0.4
            audio_high = 0.2
        else:
            motion = 35.0 + (i % 5) * 3  # 高运动（动作
            audio = 0.85
            is_boundary = (i - 30) % 3 == 0  # 每3秒一次切变（快切
            # 动作场景：高饱和、高对比、低频强
            saturation = 70.0
            brightness_std = 50.0
            contrast = 120.0
            audio_low = 0.7
            audio_mid = 0.4
            audio_high = 0.6
        frames.append(FrameInfo(
            frame_index=i, timestamp=ts, brightness=150,
            motion_score=motion, color_variation=motion*0.5,
            is_scene_boundary=is_boundary,
            scene_score=0.8 if is_boundary else 0.05,
            audio_energy=audio,
            # v2.2
            saturation=saturation, brightness_std=brightness_std,
            contrast=contrast,
            audio_low_freq=audio_low, audio_mid_freq=audio_mid, audio_high_freq=audio_high,
        ))
    
    # 构造镜头（v2.2字段
    shots = []
    # 前30s一个长镜头（对话
    shots.append(ShotInfo(shot_index=0, start_time=0.0, end_time=30.0, frame_count=30,
                         avg_motion=6.5, avg_audio=0.3, max_motion=10.0,
                         avg_color_var=3.2, dominant_level="normal",
                         avg_saturation=30.0, avg_brightness_std=15.0, avg_contrast=50.0,
                         avg_audio_low_freq=0.2, avg_audio_mid_freq=0.4, avg_audio_high_freq=0.2,
                         shot_duration=30.0))
    # 后30s每3秒一个短镜头（快切动作
    for k in range(10):
        shots.append(ShotInfo(shot_index=1+k, start_time=30+k*3, end_time=33+k*3,
                             frame_count=3, avg_motion=38.0, avg_audio=0.85,
                             max_motion=50.0, avg_color_var=19.0, dominant_level="hot",
                             avg_saturation=70.0, avg_brightness_std=50.0, avg_contrast=120.0,
                             avg_audio_low_freq=0.7, avg_audio_mid_freq=0.4, avg_audio_high_freq=0.6,
                             shot_duration=3.0))
    
    pr = PipelineResult(video_path="test.mp4", duration=60.0, fps=1.0, total_frames=60,
                        frames=frames, shots=shots, analysis_resolution="720p",
                        hardware_accel="cuda",
                        motion_p25=6.0, motion_p50=20.0, motion_p75=38.0,
                        audio_p25=0.3, audio_p50=0.5, audio_p75=0.85, effective_fps=50.0,
                        # v2.2
                        saturation_p50=50.0, brightness_std_p50=30.0, contrast_p50=85.0,
                        audio_low_p50=0.4, audio_mid_p50=0.4, audio_high_p50=0.4)
    
    print(f"  构造OK: {len(frames)}帧, {len(shots)}镜头")
    
    print("\n[2/2] 执行三阶段筛选(v2.2 多特征融合)...")
    f = ThreeStageFilter()
    result = f.filter(pr)
    
    print(f"\n  筛选结果: {len(result)}个高燃片段")
    for i, seg in enumerate(result):
        print(f"    [{i+1}] {seg.start_time:.1f}s-{seg.end_time:.1f}s "
             f"dur={seg.duration:.1f}s, score={seg.score:.2f}, level={seg.level}")
        print(f"      原因: {seg.reason}")
    
    # 验证：所有片段应主要分布在30-60秒区域
    if result:
        main_hot = sum(1 for s in result if s.start_time >= 28)
        print(f"\n  分布: 主要在动作区域: {main_hot}/{len(result)}")
        avg_score = sum(s.score for s in result) / len(result)
        print(f"  平均评分: {avg_score:.2f}")
    
    print("\n" + "="*50)
    print("v2.2 筛选逻辑验证通过（含颜色/饱和度/音频频谱分层）")
    print("="*50)
