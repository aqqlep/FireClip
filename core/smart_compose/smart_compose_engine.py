"""
智能成片主引擎 v1.0
对外统一入口，整合所有子模块

支持两种使用方式:
1. compose_from_video(video_path): 直接导入原始视频一键成片（自动提取+筛选+合成）
2. compose_from_segments(video_path, segments): 基于已提取/勾选的片段合成成片
"""
import os
import random
import glob
import time
from dataclasses import dataclass, field
from typing import List, Optional, Callable, Any

import tempfile
from utils.logger import logger
from core.smart_compose.quality_filter import QualityFilter, QualityFilterConfig, QualityResult
from core.smart_compose.audio_separator import AudioSeparator, AudioSeparatorConfig
from core.smart_compose.beat_detector import BeatDetector, BeatDetectorConfig, BeatAnalysisResult
from core.smart_compose.clip_selector import ClipSelector, SelectedClip
from core.smart_compose.composer import VideoComposer, ComposeOutput
from core.smart_compose.commentary_generator import CommentaryGenerator
from core.tts_engine import TTSEngine
from core.smart_compose.templates import (
    ComposeTemplate,
    TEMPLATE_HOT_FIGHT,
    get_template_by_name,
)


@dataclass
class SmartComposeConfig:
    """智能成片配置"""
    template: ComposeTemplate = field(default_factory=lambda: TEMPLATE_HOT_FIGHT)
    
    enable_quality_filter: bool = False
    enable_beat_align: bool = True
    enable_audio_separation: bool = False
    add_bgm: bool = False
    bgm_path: Optional[str] = None
    keep_original_audio: bool = True
    
    target_total_duration: Optional[float] = None
    output_dir: Optional[str] = None
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"
    
    use_existing_hot_segments: bool = True
    use_existing_highlight_segments: bool = True


@dataclass
class SmartComposeResult:
    """智能成片结果"""
    success: bool = False
    output_path: str = ""
    total_duration: float = 0.0
    clip_count: int = 0
    bpm: float = 0.0
    quality_results: Optional[List[QualityResult]] = None
    beat_result: Optional[BeatAnalysisResult] = None
    selected_clips: List[SelectedClip] = field(default_factory=list)
    error_message: str = ""
    intermediate_output: ComposeOutput = None


class SmartComposeEngine:
    """智能成片主引擎"""
    
    def __init__(self, config: Optional[SmartComposeConfig] = None):
        self.config = config or SmartComposeConfig()
        logger.info(f"[SmartCompose] 初始化智能成片引擎, ffmpeg={self.config.ffmpeg_path}")
        logger.debug(f"[SmartCompose] 配置: 质量过滤={self.config.enable_quality_filter}, 节拍对齐={self.config.enable_beat_align}, "
                    f"保留原声={self.config.keep_original_audio}, 添加BGM={self.config.add_bgm}, "
                    f"BGM路径={self.config.bgm_path}, 目标时长={self.config.target_total_duration}")
        
        self.quality_filter = QualityFilter(QualityFilterConfig())
        self.audio_separator = AudioSeparator(AudioSeparatorConfig(), ffmpeg_path=self.config.ffmpeg_path)
        self.beat_detector = BeatDetector(BeatDetectorConfig(), ffmpeg_path=self.config.ffmpeg_path)
        self.clip_selector = ClipSelector()
        self.composer = VideoComposer(
            ffmpeg_path=self.config.ffmpeg_path,
            ffprobe_path=self.config.ffprobe_path,
        )
        self.commentary_generator = CommentaryGenerator()
        try:
            self.tts_engine = TTSEngine()
            logger.info("[SmartCompose] TTS引擎初始化成功")
        except Exception as e:
            logger.warning(f"[SmartCompose] TTS引擎初始化失败（解说功能将不可用）: {e}")
            self.tts_engine = None
        
        self._bgm_dir = self._find_bgm_dir()
        logger.info(f"[SmartCompose] 内置BGM目录: {self._bgm_dir}")
        
        logger.info("[SmartCompose] 引擎初始化完成")
    
    def _find_bgm_dir(self) -> str:
        """查找内置BGM目录位置"""
        candidates = [
            os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "assets", "bgm"),
            os.path.join(os.getcwd(), "assets", "bgm"),
        ]
        for d in candidates:
            if os.path.isdir(d):
                return d
        return ""
    
    def _find_default_bgm(self, bgm_genre: str) -> Optional[str]:
        """根据风格在assets/bgm目录中自动查找匹配的BGM文件"""
        if not self._bgm_dir or not os.path.isdir(self._bgm_dir):
            return None
        
        genre_dir = os.path.join(self._bgm_dir, bgm_genre)
        search_dirs = []
        if os.path.isdir(genre_dir):
            search_dirs.append(genre_dir)
        search_dirs.append(self._bgm_dir)
        
        extensions = ["*.mp3", "*.wav", "*.aac", "*.m4a", "*.flac", "*.ogg"]
        candidates = []
        for d in search_dirs:
            for ext in extensions:
                candidates.extend(glob.glob(os.path.join(d, ext)))
                candidates.extend(glob.glob(os.path.join(d, "**", ext), recursive=True))
        
        candidates = [c for c in candidates if os.path.isfile(c)]
        if candidates:
            chosen = random.choice(candidates)
            logger.info(f"[SmartCompose] 自动匹配到BGM: {chosen} (genre={bgm_genre}, 可选{len(candidates)}个)")
            return chosen
        
        logger.debug(f"[SmartCompose] BGM目录中未找到{bgm_genre}分类音乐文件")
        return None
    
    def compose_from_video(
        self,
        video_path: str,
        callback: Optional[Callable[[int, str], None]] = None,
        extract_hot: bool = True,
        extract_highlight: bool = True,
    ) -> SmartComposeResult:
        """
        从原始视频直接一键成片
        内部自动调用高燃/高光提取
        
        Args:
            video_path: 输入视频路径
            callback: 进度回调 callback(progress: 0-100, message: str)
            extract_hot: 是否提取高燃片段
            extract_highlight: 是否提取高光片段
        """
        start_time = time.time()
        result = SmartComposeResult()
        logger.info(f"[SmartCompose] ===== 开始一键成片 =====")
        logger.info(f"[SmartCompose] 输入视频: {video_path}")
        logger.info(f"[SmartCompose] 提取选项: 高燃={extract_hot}, 高光={extract_highlight}")
        
        def progress(p: int, msg: str):
            logger.debug(f"[SmartCompose] 进度 {p}%: {msg}")
            if callback:
                callback(p, msg)
        
        try:
            if not os.path.exists(video_path):
                error_msg = f"视频文件不存在: {video_path}"
                logger.error(f"[SmartCompose] {error_msg}")
                result.error_message = error_msg
                return result
            
            progress(5, "初始化分析管道...")
            
            segments = []
            try:
                segments = self._extract_segments(video_path, extract_hot, extract_highlight, progress)
            except Exception as e:
                logger.error(f"[SmartCompose] 片段提取异常: {e}", exc_info=True)
            
            if not segments:
                logger.warning(f"[SmartCompose] 自动提取未返回片段，启用保底均匀分片")
                progress(50, "自动提取失败，使用均匀分片模式...")
                try:
                    segments = self._fallback_uniform_split(video_path)
                except Exception as e:
                    logger.error(f"[SmartCompose] 保底分片也失败，使用最简单单片段: {e}")
                    segments = [SelectedClip(
                        start_time=0,
                        end_time=60,
                        score=0.5,
                        clip_type="full",
                        metadata={"content_type": "action", "reason": "单片段保底"},
                    )]
            
            logger.info(f"[SmartCompose] 片段提取完成, 共 {len(segments)} 个候选片段")
            
            if not segments:
                error_msg = "未能提取到有效片段，使用完整视频合成"
                logger.warning(f"[SmartCompose] {error_msg}")
                segments = [SelectedClip(
                    start_time=0,
                    end_time=60,
                    score=0.5,
                    clip_type="full",
                    metadata={"content_type": "action", "reason": "完整视频"},
                )]
            
            logger.info(f"[SmartCompose] 进入片段合成流程, 耗时: {time.time()-start_time:.2f}s")
            return self.compose_from_segments(video_path, segments, callback)
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"[SmartCompose] 一键成片失败: {error_msg}", exc_info=True)
            result.error_message = error_msg
            return result
    
    def compose_from_segments(
        self,
        video_path: str,
        segments: List[Any],
        callback: Optional[Callable[[int, str], None]] = None,
        use_all_segments: bool = False,
    ) -> SmartComposeResult:
        """
        从已提取/勾选的片段合成成片
        
        Args:
            video_path: 原始视频路径
            segments: 片段列表，可以是HighlightSegment对象，也可以是(start, end, score)元组
            callback: 进度回调
            use_all_segments: 是否保留所有片段不做筛选（用户手动勾选时为True）
        """
        start_time = time.time()
        result = SmartComposeResult()
        logger.info(f"[SmartCompose] ===== 开始片段合成 =====")
        logger.info(f"[SmartCompose] 输入片段数: {len(segments)}")
        
        def progress(p: int, msg: str):
            logger.debug(f"[SmartCompose] 进度 {p}%: {msg}")
            if callback:
                callback(p, msg)
        
        composer_output = None
        
        try:
            if not os.path.exists(video_path):
                error_msg = f"视频文件不存在: {video_path}"
                logger.error(f"[SmartCompose] {error_msg}")
                result.error_message = error_msg
                return result
            
            if not segments:
                logger.warning(f"[SmartCompose] 没有提供片段，启用保底均匀分片")
                segments = self._fallback_uniform_split(video_path)
            
            logger.debug(f"[SmartCompose] 转换片段格式...")
            selected_clips = self._convert_to_selected_clips(segments)
            logger.info(f"[SmartCompose] 格式转换完成, 有效片段: {len(selected_clips)} 个")
            for i, clip in enumerate(selected_clips[:10]):
                logger.debug(f"  片段{i}: [{clip.start_time:.2f}s-{clip.end_time:.2f}s], 时长={clip.duration:.2f}s, 分数={clip.score:.3f}, 类型={clip.clip_type}")
            if len(selected_clips) > 10:
                logger.debug(f"  ... 还有 {len(selected_clips)-10} 个片段")
            
            if not selected_clips:
                logger.warning(f"[SmartCompose] 片段转换后无有效片段，启用保底均匀分片")
                selected_clips = self._fallback_uniform_split(video_path)
            
            if self.config.enable_quality_filter:
                try:
                    progress(10, "质量过滤中...")
                    logger.info(f"[SmartCompose] 开始质量过滤...")
                    quality_results = self.quality_filter.analyze_video(video_path, callback=lambda p, m: progress(10 + int(p*0.2), m))
                    result.quality_results = quality_results
                    logger.debug(f"[SmartCompose] 质量分析完成, 分析帧数: {len(quality_results)}")
                    
                    if quality_results:
                        clip_tuples = [(c.start_time, c.end_time) for c in selected_clips]
                        filtered_tuples, removed_count = self.quality_filter.filter_segments(clip_tuples, quality_results)
                        
                        filtered_starts = {(s, e) for s, e in filtered_tuples}
                        original_count = len(selected_clips)
                        selected_clips = [c for c in selected_clips if (c.start_time, c.end_time) in filtered_starts]
                        logger.info(f"[SmartCompose] 质量过滤完成: 移除 {removed_count} 个低质量片段, 剩余 {len(selected_clips)} 个")
                        if not selected_clips:
                            logger.warning(f"[SmartCompose] 质量过滤移除了所有片段，恢复使用原始片段")
                            selected_clips = self._convert_to_selected_clips(segments)
                            if not selected_clips:
                                selected_clips = self._fallback_uniform_split(video_path)
                    else:
                        logger.info(f"[SmartCompose] 质量分析返回空结果，跳过质量过滤")
                except Exception as e:
                    logger.warning(f"[SmartCompose] 质量过滤失败，跳过: {str(e)[:200]}")
            
            progress(30, "节拍检测中...")
            beat_result = BeatAnalysisResult(bpm=120.0, success=False, method="skipped")
            try:
                beat_start = time.time()
                logger.info(f"[SmartCompose] 开始节拍检测...")
                beat_result = self.beat_detector.analyze(video_path, is_video=True, callback=lambda p, m: progress(30 + int(p*0.15), m))
                result.beat_result = beat_result
                result.bpm = beat_result.bpm
                if beat_result.success:
                    logger.info(f"[SmartCompose] 节拍检测完成: BPM={beat_result.bpm:.1f}, 节拍点数={len(beat_result.beat_times)}, 耗时={time.time()-beat_start:.2f}s")
                else:
                    logger.info(f"[SmartCompose] 节拍检测未成功，使用默认BPM=120，不进行节拍对齐")
            except Exception as e:
                logger.warning(f"[SmartCompose] 节拍检测异常，跳过: {str(e)[:200]}")
                result.bpm = 120.0
            
            progress(50, "智能选择与排序片段...")
            select_start = time.time()
            logger.info(f"[SmartCompose] 开始片段选择与排序, 模板={self.config.template.name}, 目标时长={self.config.target_total_duration}s, 节拍对齐={self.config.enable_beat_align}, 保留所有片段={use_all_segments}")
            
            final_clips = []
            try:
                final_clips = self.clip_selector.select_and_order(
                    selected_clips,
                    self.config.template,
                    self.config.target_total_duration,
                    beat_result if self.config.enable_beat_align else None,
                    use_all_segments=use_all_segments,
                )
            except Exception as e:
                logger.error(f"[SmartCompose] 片段选择失败: {e}，直接使用所有输入片段")
                final_clips = selected_clips
            
            result.selected_clips = final_clips
            logger.info(f"[SmartCompose] 片段选择完成: 最终保留 {len(final_clips)} 个片段, 总时长={sum(c.duration for c in final_clips):.2f}s, 耗时={time.time()-select_start:.2f}s")
            for i, clip in enumerate(final_clips[:10]):
                logger.debug(f"  最终片段{i}: [{clip.start_time:.2f}s-{clip.end_time:.2f}s], 时长={clip.duration:.2f}s, 分数={clip.score:.3f}")
            if len(final_clips) > 10:
                logger.debug(f"  ... 还有 {len(final_clips)-10} 个片段")
            
            if not final_clips:
                logger.warning(f"[SmartCompose] 筛选后无有效片段，使用保底均匀分片")
                try:
                    final_clips = self._fallback_uniform_split(video_path)
                except Exception as e:
                    logger.error(f"[SmartCompose] 保底分片失败，使用整个视频")
                    final_clips = [SelectedClip(
                        start_time=0,
                        end_time=60,
                        score=0.5,
                        clip_type="full",
                        metadata={"content_type": "action"},
                    )]
            
            progress(60, f"开始合成 {len(final_clips)} 个片段...")
            
            if self.config.output_dir:
                output_dir = self.config.output_dir
            else:
                project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
                output_dir = os.path.join(project_root, "output", "smart_compose")
            os.makedirs(output_dir, exist_ok=True)
            base_name = os.path.splitext(os.path.basename(video_path))[0]
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            template_name = self.config.template.name.replace(" ", "_")
            output_path = os.path.join(output_dir, f"{base_name}_{template_name}_{timestamp}.mp4")
            logger.info(f"[SmartCompose] 输出路径: {output_path}")
            
            bgm_path = self.config.bgm_path
            if bgm_path and not os.path.exists(bgm_path):
                logger.warning(f"[SmartCompose] 指定BGM文件不存在: {bgm_path}, 尝试自动匹配内置BGM")
                bgm_path = None
            
            if not bgm_path and self.config.add_bgm:
                bgm_path = self._find_default_bgm(self.config.template.bgm_genre)
            
            if bgm_path:
                logger.info(f"[SmartCompose] 使用BGM: {bgm_path}, 音量={self.config.template.bgm_volume:.2f}, 闪避={self.config.template.audio_ducking}")
            else:
                logger.info(f"[SmartCompose] 未找到BGM，将不添加背景音乐")
            
            commentary_tracks = []
            subtitle_path = None
            temp_compose_dir = None
            
            if self.config.template.enable_tts and self.tts_engine is not None:
                try:
                    logger.info(f"[SmartCompose] 检测到解说模板，开始生成解说文案和配音...")
                    progress(58, "生成解说文案...")
                    
                    temp_compose_dir = tempfile.mkdtemp(prefix="compose_commentary_")
                    
                    commentary_segments = self.commentary_generator.generate(
                        clips=final_clips,
                        style=self.config.template.commentary_style,
                        total_duration=sum(c.duration for c in final_clips),
                    )
                    
                    if commentary_segments:
                        logger.info(f"[SmartCompose] 解说文案生成完成，共{len(commentary_segments)}段")
                        
                        if self.config.template.subtitle_enabled:
                            try:
                                subtitle_path = os.path.join(temp_compose_dir, "subtitle.srt")
                                self.commentary_generator.segments_to_srt(commentary_segments, subtitle_path)
                                logger.info(f"[SmartCompose] 字幕已生成: {subtitle_path}")
                            except Exception as e:
                                logger.warning(f"[SmartCompose] 字幕生成失败: {str(e)[:100]}")
                                subtitle_path = None
                        
                        tts_voice = "激昂热血(男)" if self.config.template.commentary_style == "passionate" else \
                                    "温暖知性(女)" if self.config.template.commentary_style == "emotional" else \
                                    "阳光活泼(男)" if self.config.template.commentary_style == "humorous" else \
                                    "专业沉稳(男)"
                        tts_speed = 1.1 if self.config.template.commentary_style == "passionate" else \
                                    0.9 if self.config.template.commentary_style == "emotional" else 1.0
                        
                        logger.info(f"[SmartCompose] 开始分段TTS合成, 语音={tts_voice}, 语速={tts_speed}")
                        progress(60, "TTS分段语音合成中...")
                        
                        tts_success_count = 0
                        for i, seg in enumerate(commentary_segments):
                            seg_audio_path = os.path.join(temp_compose_dir, f"voice_{i:03d}.mp3")
                            try:
                                ok = self.tts_engine.synthesize(
                                    text=seg.text,
                                    output_path=seg_audio_path,
                                    voice=tts_voice,
                                    speed=tts_speed,
                                )
                                if ok and os.path.exists(seg_audio_path):
                                    commentary_tracks.append((seg.start_time, seg_audio_path))
                                    tts_success_count += 1
                                    logger.debug(f"[SmartCompose] TTS分段{i}合成成功: 延迟={seg.start_time:.2f}s, 文件={seg_audio_path}")
                                else:
                                    logger.warning(f"[SmartCompose] TTS分段{i}合成失败")
                            except Exception as e:
                                logger.error(f"[SmartCompose] TTS分段{i}合成异常: {e}")
                        
                        logger.info(f"[SmartCompose] TTS分段合成完成, 成功{tts_success_count}/{len(commentary_segments)}段")
                    else:
                        logger.warning(f"[SmartCompose] 解说文案生成失败，跳过解说")
                except Exception as e:
                    logger.warning(f"[SmartCompose] 解说生成整体失败，跳过解说: {str(e)[:200]}")
                    commentary_tracks = []
                    subtitle_path = None
            
            compose_start = time.time()
            try:
                composer_output = self.composer.compose(
                    video_path=video_path,
                    clips=final_clips,
                    template=self.config.template,
                    output_path=output_path,
                    bgm_path=bgm_path,
                    keep_original_audio=self.config.keep_original_audio,
                    commentary_tracks=commentary_tracks if commentary_tracks else None,
                    subtitle_path=subtitle_path,
                    callback=lambda p, m: progress(60 + int(p*0.35), m),
                )
            except Exception as e:
                logger.error(f"[SmartCompose] 视频合成异常: {str(e)}", exc_info=True)
                result.success = False
                result.error_message = f"视频合成失败: {str(e)}"
                if temp_compose_dir and os.path.exists(temp_compose_dir):
                    import shutil
                    try:
                        shutil.rmtree(temp_compose_dir, ignore_errors=True)
                    except:
                        pass
                self.cleanup(result)
                return result
            
            if temp_compose_dir and os.path.exists(temp_compose_dir):
                import shutil
                try:
                    shutil.rmtree(temp_compose_dir, ignore_errors=True)
                    logger.debug(f"[SmartCompose] 清理解说临时目录: {temp_compose_dir}")
                except:
                    pass
            
            result.success = composer_output.success
            result.output_path = output_path if composer_output.success else ""
            result.total_duration = composer_output.total_duration
            result.clip_count = composer_output.clip_count
            result.error_message = composer_output.error_message
            result.intermediate_output = composer_output
            
            if result.success:
                logger.info(f"[SmartCompose] ===== 成片合成成功! =====")
                logger.info(f"[SmartCompose] 输出文件: {result.output_path}")
                logger.info(f"[SmartCompose] 总时长: {result.total_duration:.2f}s, 片段数: {result.clip_count}")
                logger.info(f"[SmartCompose] 总耗时: {time.time()-start_time:.2f}s, 合成耗时: {time.time()-compose_start:.2f}s")
                progress(100, "成片合成完成!")
            else:
                logger.error(f"[SmartCompose] ===== 成片合成失败 =====")
                logger.error(f"[SmartCompose] 错误信息: {result.error_message}")
                progress(100, f"合成失败: {result.error_message}")
            
            return result
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"[SmartCompose] 片段合成异常: {error_msg}", exc_info=True)
            result.error_message = error_msg
            return result
        finally:
            if composer_output:
                pass
    
    def _extract_segments(
        self,
        video_path: str,
        extract_hot: bool,
        extract_highlight: bool,
        progress: Callable[[int, str], None],
    ) -> List[SelectedClip]:
        """提取高燃/高光片段（复用现有分析管道）"""
        from core.unified_video_pipeline import UnifiedVideoPipeline
        from core.three_stage_filter import ThreeStageFilter
        
        extract_start = time.time()
        all_segments = []
        logger.info(f"[Extract] ===== 开始提取高燃/高光片段 =====")
        logger.info(f"[Extract] 视频路径: {video_path}")
        
        try:
            progress(5, "分析视频特征中...")
            logger.info(f"[Extract] 启动UnifiedVideoPipeline分析视频...")
            
            pipeline = UnifiedVideoPipeline()
            
            def pipe_cb(p, msg):
                logger.debug(f"[Extract] 视频分析进度 {p}%: {msg}")
                progress(5 + int(p * 0.5), msg)
            
            analysis_result = pipeline.analyze(video_path, progress_callback=pipe_cb)
            logger.info(f"[Extract] 视频分析完成: 时长={analysis_result.duration:.2f}s, FPS={analysis_result.fps:.1f}, "
                       f"镜头数={len(analysis_result.shots)}, 帧数={len(analysis_result.frames)}")
            
            if extract_hot:
                progress(55, "筛选高燃片段中...")
                hot_start = time.time()
                logger.info(f"[Extract] 开始筛选高燃片段...")
                try:
                    filter_engine = ThreeStageFilter()
                    
                    def filter_cb(stage_p, msg):
                        logger.debug(f"[Extract] 高燃筛选进度 {stage_p}%: {msg}")
                        progress(55 + int(stage_p * 0.35), msg)
                    
                    hot_segments = filter_engine.filter(analysis_result, progress_callback=filter_cb)
                    logger.info(f"[Extract] 高燃片段筛选完成: 找到 {len(hot_segments)} 个, 耗时={time.time()-hot_start:.2f}s")
                    
                    for i, seg in enumerate(hot_segments):
                        vfx_score = 0.0
                        motion_score = 0.0
                        audio_score = 0.0
                        
                        start_f = int(seg.start_time * analysis_result.fps)
                        end_f = int(seg.end_time * analysis_result.fps)
                        frame_slice = [f for f in analysis_result.frames if start_f <= f.frame_index <= end_f]
                        if frame_slice:
                            motion_score = sum(f.motion_score for f in frame_slice) / len(frame_slice)
                            vfx_score = sum(f.vfx_score for f in frame_slice) / len(frame_slice)
                        
                        logger.debug(f"  高燃片段{i}: [{seg.start_time:.2f}s-{seg.end_time:.2f}s], 分数={seg.score:.3f}, 等级={seg.level}, 原因={seg.reason}")
                        all_segments.append(SelectedClip(
                            start_time=seg.start_time,
                            end_time=seg.end_time,
                            score=seg.score,
                            clip_type="hot_fire",
                            vfx_score=vfx_score,
                            motion_score=motion_score,
                            audio_score=audio_score,
                            metadata={"level": seg.level, "reason": seg.reason, "scene_type": seg.scene_type},
                        ))
                except Exception as e:
                    logger.error(f"[Extract] 提取高燃片段出错: {e}", exc_info=True)
            
            if extract_highlight:
                progress(80, "筛选高光片段中...")
                hl_start = time.time()
                logger.info(f"[Extract] 开始筛选高光片段...")
                try:
                    from core.analysis_controller import AnalysisController
                    controller = AnalysisController()
                    
                    def hl_cb(p, msg):
                        logger.debug(f"[Extract] 高光筛选进度 {p}%: {msg}")
                        progress(80 + int(p * 0.15), msg)
                    
                    highlight_segments = controller.extract_highlight_segments(
                        video_path, top_n=20, progress_callback=lambda c, t, m: hl_cb(int(c/t*100) if t>0 else 0, m)
                    )
                    logger.info(f"[Extract] 高光片段筛选完成: 找到 {len(highlight_segments)} 个, 耗时={time.time()-hl_start:.2f}s")
                    
                    for i, seg in enumerate(highlight_segments):
                        start = getattr(seg, 'start_time', 0.0)
                        end = getattr(seg, 'end_time', 0.0)
                        score = getattr(seg, 'score', 0.7)
                        if start < end:
                            logger.debug(f"  高光片段{i}: [{start:.2f}s-{end:.2f}s], 分数={score:.3f}")
                            all_segments.append(SelectedClip(
                                start_time=start,
                                end_time=end,
                                score=score * 0.85,
                                clip_type="highlight",
                                metadata={"level": "normal", "reason": "高光片段"},
                            ))
                except Exception as e:
                    logger.error(f"[Extract] 提取高光片段出错（跳过）: {e}", exc_info=True)
                    
        except Exception as e:
            logger.error(f"[Extract] 视频分析出错: {e}", exc_info=True)
        
        if not all_segments:
            logger.warning(f"[Extract] 自动提取未找到片段，启用保底均匀分片模式")
            progress(90, "自动提取未找到片段，使用均匀分片...")
            all_segments = self._fallback_uniform_split(video_path)
        
        logger.info(f"[Extract] ===== 片段提取完成, 共找到 {len(all_segments)} 个候选片段, 总耗时: {time.time()-extract_start:.2f}s =====")
        progress(95, f"提取完成，共找到 {len(all_segments)} 个候选片段")
        return all_segments
    
    def _fallback_uniform_split(self, video_path: str) -> List[SelectedClip]:
        """保底方案：均匀切分视频为多个片段，多重容错保证一定返回有效片段"""
        import subprocess as sp
        
        clips = []
        duration = 60.0
        
        try:
            cmd = [
                self.config.ffprobe_path, "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                video_path
            ]
            result = sp.run(
                cmd,
                capture_output=True,
                text=True,
                errors="ignore",
                creationflags=sp.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                timeout=15
            )
            if result.returncode == 0 and result.stdout.strip():
                duration = float(result.stdout.strip())
                logger.info(f"[Extract] 获取视频时长成功: {duration:.2f}s")
            else:
                logger.warning(f"[Extract] ffprobe获取时长失败，使用默认时长60s")
        except Exception as e:
            logger.warning(f"[Extract] 获取视频时长异常: {e}，使用默认时长60s")
        
        if duration < 5:
            duration = max(5.0, duration)
        
        if duration <= 30:
            clip_duration = 2.0
            num_clips = max(3, int(duration / clip_duration))
        elif duration <= 120:
            clip_duration = 3.0
            num_clips = min(20, max(5, int(duration / clip_duration)))
        else:
            clip_duration = 4.0
            num_clips = min(25, max(8, int(duration / clip_duration)))
        
        actual_clip_dur = duration / num_clips
        logger.info(f"[Extract] 保底均匀分片: 总时长={duration:.2f}s, 切分为{num_clips}段, 每段≈{actual_clip_dur:.2f}s")
        
        for i in range(num_clips):
            start = i * actual_clip_dur
            end = min((i + 1) * actual_clip_dur, duration)
            if end - start < 0.5:
                continue
            base_score = 0.5 + random.random() * 0.3
            clips.append(SelectedClip(
                start_time=start,
                end_time=end,
                score=base_score,
                clip_type="uniform",
                vfx_score=40 + random.random() * 30,
                motion_score=40 + random.random() * 30,
                audio_score=40 + random.random() * 30,
                metadata={"level": "normal", "reason": "均匀分片(保底)", "content_type": "action"},
            ))
        
        if not clips:
            logger.warning(f"[Extract] 均匀分片无有效片段，返回整个视频作为单个片段")
            clips.append(SelectedClip(
                start_time=0,
                end_time=duration,
                score=0.5,
                clip_type="full",
                metadata={"level": "normal", "reason": "完整视频(终极保底)", "content_type": "action"},
            ))
        
        return clips
    
    def _convert_to_selected_clips(self, segments: List[Any]) -> List[SelectedClip]:
        """将各种格式的片段统一转换为SelectedClip"""
        clips = []
        
        for seg in segments:
            if isinstance(seg, SelectedClip):
                if not seg.metadata:
                    seg.metadata = {"content_type": "action"}
                elif "content_type" not in seg.metadata:
                    seg.metadata["content_type"] = "action"
                clips.append(seg)
                continue
            
            if isinstance(seg, dict):
                start = seg.get("start_time") or seg.get("start") or 0.0
                end = seg.get("end_time") or seg.get("end") or 0.0
                if not end or end <= start:
                    continue
                start = float(start)
                end = float(end)
                score = float(seg.get("score", 0.8))
                vfx = float(seg.get("vfx_score", 50.0) or 50.0)
                motion = float(seg.get("motion_score", 50.0) or 50.0)
                audio = float(seg.get("audio_score", 50.0) or 50.0)
                clip_type = seg.get("clip_type") or seg.get("scene_type") or seg.get("type") or "scene"
                
                content_type = "action"
                if clip_type in ("dialog", "emotion", "highlight"):
                    content_type = clip_type if clip_type != "highlight" else "famous_scene"
                
                metadata = seg.get("metadata", {}) or {}
                metadata.setdefault("content_type", content_type)
                metadata.setdefault("reason", seg.get("reason", "用户勾选片段"))
                
                clips.append(SelectedClip(
                    start_time=start,
                    end_time=end,
                    score=score,
                    clip_type=str(clip_type),
                    vfx_score=vfx,
                    motion_score=motion,
                    audio_score=audio,
                    metadata=metadata,
                ))
                continue
            
            if hasattr(seg, 'start_time') and hasattr(seg, 'end_time'):
                vfx = getattr(seg, 'vfx_score', 0.0) or 0.0
                motion = getattr(seg, 'motion_score', 0.0) or 0.0
                audio = getattr(seg, 'audio_score', 0.0) or 0.0
                scene_type = getattr(seg, 'scene_type', 'hot_fire') or 'hot_fire'
                
                content_type = "action"
                if scene_type in ("dialog", "emotion", "highlight"):
                    content_type = scene_type if scene_type != "highlight" else "famous_scene"
                
                metadata = {
                    "level": getattr(seg, 'level', ''),
                    "reason": getattr(seg, 'reason', ''),
                    "content_type": content_type
                }
                
                clips.append(SelectedClip(
                    start_time=seg.start_time,
                    end_time=seg.end_time,
                    score=getattr(seg, 'score', 0.8),
                    clip_type=scene_type,
                    vfx_score=vfx if isinstance(vfx, (int, float)) else 0.0,
                    motion_score=motion if isinstance(motion, (int, float)) else 0.0,
                    audio_score=audio if isinstance(audio, (int, float)) else 0.0,
                    metadata=metadata,
                ))
                continue
            
            if isinstance(seg, (tuple, list)) and len(seg) >= 2:
                start = float(seg[0])
                end = float(seg[1])
                score = float(seg[2]) if len(seg) >= 3 else 0.8
                
                clips.append(SelectedClip(
                    start_time=start,
                    end_time=end,
                    score=score,
                    clip_type="hot_fire",
                    metadata={"content_type": "action"},
                ))
                continue
        
        logger.info(f"[SmartCompose] 转换完成: 输入{len(segments)}个片段，输出{len(clips)}个有效SelectedClip")
        return clips
    
    def cleanup(self, result: SmartComposeResult):
        """清理临时文件"""
        if result.intermediate_output:
            self.composer.cleanup(result.intermediate_output)
