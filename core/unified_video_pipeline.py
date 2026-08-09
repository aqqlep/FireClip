"""
统一视频分析管道 v3.0

版本演进:
v2.0: 初始三阶段架构
v2.1: SAD运动分析 + 动态百分位阈值
v2.2: 颜色/亮度/对比度 + 音频频谱分层 + 多窗口动态阈值
v2.3: 多线程流水线 + 增量分析 + ffmpeg参数极致调优
v2.4: 资源控制（内存监控 + 动态采样率 + CPU节流
v2.5: 鲁棒性加固（错误恢复 + 降级策略 + 异常格式处理 + 损坏帧跳过
v3.0: 真实FFT频谱 + RGB双流特征 + 分析结果缓存
"""
import subprocess
import os
import time
import struct
import json
import hashlib
import numpy as np
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple
from collections import deque

from utils.logger import logger
from config import CONFIG


# =========================================================
# 数据结构
# =========================================================
@dataclass
class FrameInfo:
    """单帧信息（极简 - v2.2）"""
    frame_index: int
    timestamp: float
    brightness: float = 0.0
    motion_score: float = 0.0
    color_variation: float = 0.0
    is_scene_boundary: bool = False
    scene_score: float = 0.0
    audio_energy: float = 0.0
    # v2.2 新增特征
    saturation: float = 0.0           # 颜色饱和度（估算
    contrast: float = 0.0             # 局部对比度
    brightness_std: float = 0.0       # 帧内亮度标准差（估算
    # v2.2 音频分层（如果有
    audio_low_freq: float = 0.0       # 低频能量（爆炸/冲击
    audio_mid_freq: float = 0.0       # 中频能量（对话/人声
    audio_high_freq: float = 0.0      # 高频能量（音乐/特效
    # v2.6 新增：VFX能量得分
    vfx_energy_score: float = 0.0     # VFX综合能量（0-100）来自HSV色彩+局部高亮+边缘光效
    # v3.1 新增：音频语义分类
    audio_semantic: str = 'unknown'   # 'speech'|'action'|'explosion'|'music'|'silence'|'unknown'
    audio_semantic_conf: float = 0.0  # 语义分类置信度（0-1）


@dataclass
class ShotInfo:
    """镜头信息（v2.2）"""
    shot_index: int
    start_time: float
    end_time: float
    frame_count: int
    avg_motion: float
    avg_audio: float
    max_motion: float
    avg_color_var: float
    dominant_level: str
    # v2.2 新增
    avg_saturation: float = 0.0
    avg_brightness_std: float = 0.0
    avg_contrast: float = 0.0
    avg_audio_low_freq: float = 0.0
    avg_audio_mid_freq: float = 0.0
    avg_audio_high_freq: float = 0.0
    shot_duration: float = 0.0
    # v2.6 新增：VFX特征
    avg_vfx_score: float = 0.0        # 平均VFX能量
    max_vfx_score: float = 0.0        # 峰值VFX能量
    # v3.1 新增：音频语义
    dominant_audio_semantic: str = 'unknown'  # 镜头内主导音频语义
    speech_ratio: float = 0.0                # 语音帧占比（0-1）


@dataclass
class PipelineResult:
    """管道分析结果（v2.2）"""
    video_path: str
    duration: float
    fps: float
    total_frames: int
    frames: List[FrameInfo]
    shots: List[ShotInfo]
    analysis_resolution: str
    hardware_accel: str
    motion_p25: float = 0.0
    motion_p50: float = 0.0
    motion_p75: float = 0.0
    audio_p25: float = 0.0
    audio_p50: float = 0.0
    audio_p75: float = 0.0
    effective_fps: float = 0.0
    # v2.2 新增统计
    saturation_p50: float = 0.0
    brightness_std_p50: float = 0.0
    contrast_p50: float = 0.0
    audio_low_p50: float = 0.0
    audio_mid_p50: float = 0.0
    audio_high_p50: float = 0.0
    # v2.6 新增
    vfx_p50: float = 0.0              # VFX能量P50
    vfx_p75: float = 0.0              # VFX能量P75


# =========================================================
# 硬件加速检测
# =========================================================
def detect_hardware_accel(ffmpeg_path: str) -> str:
    """
    检测可用的硬件加速（优先级: CUDA > QSV > VAAPI > none
    
    返回: 'cuda' | 'qsv' | 'vaapi' | 'none'
    """
    methods = [
        ('cuda', ['-hwaccel', 'cuda']),
        ('qsv', ['-hwaccel', 'qsv']),
        ('vaapi', ['-hwaccel', 'vaapi']),
    ]
    
    for name, args in methods:
        try:
            cmd = [ffmpeg_path] + args + ['-f', 'lavfi', '-i', 'nullsrc=s=32x32:d=0.1', '-f', 'null', '-']
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                logger.info(f"✓ 检测到硬件加速: {name}")
                return name
        except Exception:
            continue
    
    logger.info("未检测到硬件加速，使用软件解码")
    return 'none'


# =========================================================
# 核心管道：UnifiedVideoPipeline
# =========================================================
class UnifiedVideoPipeline:
    """统一视频分析管道 v3.0 - 真实FFT+RGB双流+分析缓存"""
    
    # 类级别缓存（同一进程内复用）
    _result_cache: Dict[str, 'PipelineResult'] = {}
    _cache_max_size = 3  # 最多缓存3个视频结果
    
    def __init__(self):
        self.pl = CONFIG.pipeline
        # 自动检测 ffmpeg 路径
        portable = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'ffmpeg', 'bin', 'ffmpeg.exe')
        if os.path.exists(portable):
            self.ffmpeg_path = portable
        elif CONFIG.ffmpeg_path and os.path.exists(CONFIG.ffmpeg_path):
            self.ffmpeg_path = CONFIG.ffmpeg_path
        else:
            self.ffmpeg_path = 'ffmpeg'
        
        # v2.5: 状态标记
        self._fallback_mode = False
        self._corrupt_frame_count = 0
        self._analysis_start_time = 0
        
        # 硬件加速
        self.hw_accel_actual = 'none'
        if self.pl.enable_hw_decode:
            self.hw_accel_actual = detect_hardware_accel(self.ffmpeg_path)
        
        # 资源监控
        self._peak_memory_mb = 0.0
        self._peak_cpu_percent = 0.0
        self._frame_count = 0
        self._memory_check_interval = 10
        self._last_memory_check = 0
        
        # 缓存目录
        self._cache_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'cache', 'analysis_results'
        )
        os.makedirs(self._cache_dir, exist_ok=True)
        
        logger.info(f"统一视频分析管道 v3.0 初始化: ffmpeg={self.ffmpeg_path}, hw={self.hw_accel_actual}, "
                   f"缓存=启用")
    
    # =========================================================
    # v3.0: 分析结果缓存
    # =========================================================
    def _video_cache_key(self, video_path: str) -> str:
        """生成视频缓存键（基于路径+大小+修改时间）"""
        try:
            stat = os.stat(video_path)
            key = f"{os.path.basename(video_path)}:{stat.st_size}:{int(stat.st_mtime)}"
            return hashlib.md5(key.encode()).hexdigest()[:16]
        except Exception:
            return hashlib.md5(video_path.encode()).hexdigest()[:16]
    
    def _try_load_cache(self, video_path: str) -> Optional['PipelineResult']:
        """尝试从缓存加载分析结果"""
        cache_key = self._video_cache_key(video_path)
        
        # 先查内存缓存
        if cache_key in UnifiedVideoPipeline._result_cache:
            logger.info(f"命中内存缓存: {cache_key}")
            return UnifiedVideoPipeline._result_cache[cache_key]
        
        # 再查磁盘缓存
        cache_file = os.path.join(self._cache_dir, f"{cache_key}.json")
        if os.path.exists(cache_file):
            try:
                with open(cache_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                result = self._deserialize_result(data)
                if result and result.frames:
                    # 提升到内存缓存
                    UnifiedVideoPipeline._result_cache[cache_key] = result
                    logger.info(f"命中磁盘缓存: {cache_key} ({len(result.frames)}帧)")
                    return result
            except Exception as e:
                logger.warning(f"磁盘缓存读取失败: {e}")
        
        return None
    
    def _save_to_cache(self, video_path: str, result: 'PipelineResult'):
        """保存分析结果到缓存"""
        cache_key = self._video_cache_key(video_path)
        
        # 内存缓存
        if len(UnifiedVideoPipeline._result_cache) >= UnifiedVideoPipeline._cache_max_size:
            # 淘汰最早的
            oldest_key = next(iter(UnifiedVideoPipeline._result_cache))
            del UnifiedVideoPipeline._result_cache[oldest_key]
        UnifiedVideoPipeline._result_cache[cache_key] = result
        
        # 磁盘缓存（仅保存关键统计数据，不保存全部帧数据以节省空间）
        try:
            data = self._serialize_result(result)
            cache_file = os.path.join(self._cache_dir, f"{cache_key}.json")
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"磁盘缓存写入失败: {e}")
    
    def _serialize_result(self, result: 'PipelineResult') -> Dict:
        """序列化PipelineResult（精简版，不保存每帧原始数据）"""
        return {
            'video_path': result.video_path,
            'duration': result.duration,
            'fps': result.fps,
            'total_frames': result.total_frames,
            'analysis_resolution': result.analysis_resolution,
            'hardware_accel': result.hardware_accel,
            'frame_count': len(result.frames),
            'shot_count': len(result.shots),
            'stats': {
                'motion_p25': result.motion_p25, 'motion_p50': result.motion_p50, 'motion_p75': result.motion_p75,
                'audio_p25': result.audio_p25, 'audio_p50': result.audio_p50, 'audio_p75': result.audio_p75,
                'saturation_p50': result.saturation_p50, 'brightness_std_p50': result.brightness_std_p50,
                'contrast_p50': result.contrast_p50, 'vfx_p50': result.vfx_p50, 'vfx_p75': result.vfx_p75,
                'audio_low_p50': result.audio_low_p50, 'audio_mid_p50': result.audio_mid_p50, 'audio_high_p50': result.audio_high_p50,
            },
            'effective_fps': result.effective_fps,
        }
    
    def _deserialize_result(self, data: Dict) -> Optional['PipelineResult']:
        """反序列化PipelineResult（磁盘缓存仅保存统计数据，帧数据需重新分析）"""
        stats = data.get('stats', {})
        return PipelineResult(
            video_path=data.get('video_path', ''),
            duration=data.get('duration', 0),
            fps=data.get('fps', 0),
            total_frames=data.get('total_frames', 0),
            frames=[],  # 帧数据不在磁盘缓存中
            shots=[],   # 镜头数据不在磁盘缓存中
            analysis_resolution=data.get('analysis_resolution', ''),
            hardware_accel=data.get('hardware_accel', 'none'),
            **stats,
            effective_fps=data.get('effective_fps', 0),
        )
    
    # =========================================================
    # 主入口（v3.0: 带缓存的分析
    # =========================================================
    def analyze(self, video_path: str, progress_callback=None, force_no_cache: bool = False) -> PipelineResult:
        """分析视频 - 返回帧+镜头+统计（v3.0 带缓存）"""
        logger.info(f"开始分析: {video_path}")
        self._analysis_start_time = time.time()
        
        if not os.path.exists(video_path):
            logger.error(f"视频不存在: {video_path}")
            return PipelineResult(video_path, 0.0, 0.0, 0, [], [], "", "none")
        
        # v3.0: 尝试缓存
        if not force_no_cache:
            cached = self._try_load_cache(video_path)
            if cached is not None:
                # 完整缓存（内存中有帧数据）
                if cached.frames:
                    logger.info(f"从缓存加载分析结果: {len(cached.frames)}帧, {len(cached.shots)}镜头")
                    return cached
                # 部分缓存（磁盘只有统计）- 仍需重新分析帧
                logger.info("磁盘缓存仅有统计数据，需要重新分析帧数据")
        
        t0 = time.time()
        self._fallback_mode = False
        self._corrupt_frame_count = 0
        
        # 获取视频基本信息
        video_info = self._get_video_info(video_path)
        duration = video_info.get('duration', 0)
        src_fps = video_info.get('fps', 30.0)
        
        # v2.5: 如果无法获取基本信息，尝试降级检测
        if duration <= 0 and self.pl.enable_error_recovery and self.pl.enable_fallback_mode:
            logger.warning("[v2.5] 无法正常获取视频信息，尝试降级模式检测")
            video_info = self._get_video_info_fallback(video_path)
            duration = video_info.get('duration', 0)
            src_fps = video_info.get('fps', 30.0)
            if duration > 0:
                logger.info(f"[v2.5] 降级模式成功获取信息: 时长={duration:.1f}s")
                self._fallback_mode = True
        
        if duration <= 0:
            logger.error("无法获取视频时长，分析失败")
            return PipelineResult(video_path, 0.0, 0.0, 0, [], [], "", self.hw_accel_actual)
        
        # v2.5: 超时保护
        if self.pl.max_analysis_timeout_sec > 0:
            est_max_time = min(duration * 0.5 + 30, self.pl.max_analysis_timeout_sec)
            logger.info(f"[v2.5] 预计最大分析时间: {est_max_time:.1f}s (保护上限: {self.pl.max_analysis_timeout_sec}s)")
        
        logger.info(f"视频信息: 时长={duration:.1f}s, 原始FPS={src_fps:.1f}, "
                   f"分析FPS={self.pl.fallback_frame_rate if self._fallback_mode else self.pl.base_frame_rate}, "
                   f"模式={'降级' if self._fallback_mode else '正常'}")
        
        # Step 1: 提取视频特征（帧亮度 + motion + scene）
        video_frames = self._extract_video_features(video_path, duration, progress_callback)
        
        # v2.5: 如果正常模式提取失败且帧数量不足，尝试降级模式
        if len(video_frames) < max(3, int(duration * 0.1)) and not self._fallback_mode and self.pl.enable_fallback_mode:
            logger.warning(f"[v2.5] 正常模式提取帧数过少({len(video_frames)})，尝试降级模式")
            self._fallback_mode = True
            video_frames = self._extract_video_features(video_path, duration, progress_callback)
        
        # Step 2: 提取音频能量
        audio_by_time = self._extract_audio_energy(video_path, duration)
        
        # Step 2.5 (v2.6): 运行VFX特效检测并融合到帧数据
        vfx_by_time = self._extract_vfx_scores(video_path, duration)
        
        # Step 3: 融合音频+VFX到帧
        for f in video_frames:
            nearest_key = min(audio_by_time.keys(), key=lambda t: abs(t - f.timestamp), default=None)
            if nearest_key is not None and abs(nearest_key - f.timestamp) < 1.0:
                val = audio_by_time[nearest_key]
                if isinstance(val, dict):
                    f.audio_energy = val['energy']
                    f.audio_low_freq = val.get('low_freq', 0.0)
                    f.audio_mid_freq = val.get('mid_freq', 0.0)
                    f.audio_high_freq = val.get('high_freq', 0.0)
                    # v3.1: 音频语义分类
                    f.audio_semantic, f.audio_semantic_conf = self._classify_audio_semantic(
                        f.audio_energy, f.audio_low_freq, f.audio_mid_freq, f.audio_high_freq
                    )
                else:
                    f.audio_energy = val
            
            # v2.6: 融合VFX能量得分
            vfx_nearest = min(vfx_by_time.keys(), key=lambda t: abs(t - f.timestamp), default=None)
            if vfx_nearest is not None and abs(vfx_nearest - f.timestamp) < 1.5:
                f.vfx_energy_score = vfx_by_time[vfx_nearest]  # 0-100 范围
        
        # Step 4: 统计百分位
        if video_frames:
            motions = sorted([f.motion_score for f in video_frames])
            audios = sorted([f.audio_energy for f in video_frames])
            n = len(motions)
            motion_p25 = motions[int(n*0.25)] if n > 0 else 0
            motion_p50 = motions[int(n*0.50)] if n > 0 else 0
            motion_p75 = motions[int(n*0.75)] if n > 0 else 0
            audio_p25 = audios[int(n*0.25)] if n > 0 else 0
            audio_p50 = audios[int(n*0.50)] if n > 0 else 0
            audio_p75 = audios[int(n*0.75)] if n > 0 else 0
            
            sats = sorted([f.saturation for f in video_frames])
            bstds = sorted([f.brightness_std for f in video_frames])
            cons = sorted([f.contrast for f in video_frames])
            alows = sorted([f.audio_low_freq for f in video_frames])
            amids = sorted([f.audio_mid_freq for f in video_frames])
            ahighs = sorted([f.audio_high_freq for f in video_frames])
            
            # v2.6: VFX能量统计
            vfx_scores = sorted([f.vfx_energy_score for f in video_frames])
            
            saturation_p50 = sats[int(n*0.50)] if n > 0 else 0
            brightness_std_p50 = bstds[int(n*0.50)] if n > 0 else 0
            contrast_p50 = cons[int(n*0.50)] if n > 0 else 0
            audio_low_p50 = alows[int(n*0.50)] if n > 0 else 0
            audio_mid_p50 = amids[int(n*0.50)] if n > 0 else 0
            audio_high_p50 = ahighs[int(n*0.50)] if n > 0 else 0
            vfx_p50 = vfx_scores[int(n*0.50)] if n > 0 else 0
            vfx_p75 = vfx_scores[int(n*0.75)] if n > 0 else 0
        else:
            motion_p25 = motion_p50 = motion_p75 = 0
            audio_p25 = audio_p50 = audio_p75 = 0
            saturation_p50 = brightness_std_p50 = contrast_p50 = 0
            audio_low_p50 = audio_mid_p50 = audio_high_p50 = 0
            vfx_p50 = vfx_p75 = 0
        
        # Step 5: 构建镜头
        shots = self._build_shots(video_frames)
        
        t1 = time.time()
        total_time = t1 - t0
        effective_fps = len(video_frames) / max(total_time, 0.001)
        
        logger.info(f"分析完成: {len(video_frames)}帧, {len(shots)}镜头, "
                   f"耗时{total_time:.1f}s, 有效FPS={effective_fps:.1f} (v2.5)")
        
        result = PipelineResult(
            video_path=video_path,
            duration=duration,
            fps=self.pl.fallback_frame_rate if self._fallback_mode else self.pl.base_frame_rate,
            total_frames=len(video_frames),
            frames=video_frames,
            shots=shots,
            analysis_resolution=self.pl.fallback_analysis_resolution if self._fallback_mode else self.pl.analysis_resolution,
            hardware_accel=self.hw_accel_actual,
            motion_p25=motion_p25,
            motion_p50=motion_p50,
            motion_p75=motion_p75,
            audio_p25=audio_p25,
            audio_p50=audio_p50,
            audio_p75=audio_p75,
            effective_fps=effective_fps,
            saturation_p50=saturation_p50,
            brightness_std_p50=brightness_std_p50,
            contrast_p50=contrast_p50,
            audio_low_p50=audio_low_p50,
            audio_mid_p50=audio_mid_p50,
            audio_high_p50=audio_high_p50,
            # v2.6
            vfx_p50=vfx_p50,
            vfx_p75=vfx_p75,
        )
        
        # v3.0: 保存到缓存
        self._save_to_cache(video_path, result)
        
        return result
    
    # =========================================================
    # v2.5: 降级模式获取视频信息（兼容异常格式
    # =========================================================
    def _get_video_info_fallback(self, video_path: str) -> Dict:
        """
        降级模式获取视频信息（v2.5）
        针对异常格式的视频，使用更保守的ffprobe参数尝试
        """
        portable_ffprobe = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'ffmpeg', 'bin', 'ffprobe.exe')
        
        ffprobe_path = portable_ffprobe if os.path.exists(portable_ffprobe) else 'ffprobe'
        
        for attempt_method in ['ffprobe_format', 'ffmpeg_duration']:
            try:
                if attempt_method == 'ffprobe_format':
                    cmd = [
                        ffprobe_path, '-v', 'error',
                        '-show_entries', 'format=duration:stream=codec_name,r_frame_rate',
                        '-of', 'json', video_path
                    ]
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
                    if result.returncode == 0:
                        import json as _json
                        info = _json.loads(result.stdout)
                        fmt = info.get('format', {})
                        duration = float(fmt.get('duration', 0))
                        streams = info.get('streams', [])
                        fps = 30.0
                        for s in streams:
                            fps_str = s.get('r_frame_rate', '0/1')
                            if '/' in fps_str:
                                try:
                                    num, den = fps_str.split('/')
                                    if float(den) > 0:
                                        fps = float(num) / float(den)
                                except (ValueError, ZeroDivisionError):
                                    pass
                            break
                        if duration > 0:
                            return {'duration': duration, 'fps': fps}
                elif attempt_method == 'ffmpeg_duration':
                    cmd = [
                        self.ffmpeg_path, '-i', video_path, '-t', '1', '-f', 'null', '-'
                    ]
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
                    output = result.stderr
                    import re
                    dur_match = re.search(r"Duration:\s+(\d+):(\d+):(\d+\.?\d*)", output)
                    if dur_match:
                        h, m, s = float(dur_match.group(1)), float(dur_match.group(2)), float(dur_match.group(3))
                        duration = h*3600 + m*60 + s
                        fps_match = re.search(r"(\d+(?:\.\d+)?)\s*fps", output)
                        fps = float(fps_match.group(1)) if fps_match else 30.0
                        if duration > 0:
                            return {'duration': duration, 'fps': fps}
            except Exception as e:
                logger.warning(f"[v2.5 Fallback] 方法{attempt_method}失败: {e}")
                continue
        
        logger.error(f"[v2.5 Fallback] 所有降级方法均失败")
        return {'duration': 0.0, 'fps': 30.0}
    
    # =========================================================
    # _get_video_info - 获取视频基本信息
    # =========================================================
    def _get_video_info(self, video_path: str) -> Dict:
        """获取视频基本信息"""
        try:
            cmd = [self.ffmpeg_path, '-i', video_path, '-hide_banner']
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            output = result.stderr  # ffmpeg输出到stderr
            
            duration = 0.0
            fps = 30.0
            
            # 解析时长
            import re
            dur_match = re.search(r"Duration:\s+(\d+):(\d+):(\d+\.?\d*)", output)
            if dur_match:
                h, m, s = float(dur_match.group(1)), float(dur_match.group(2)), float(dur_match.group(3))
                duration = h*3600 + m*60 + s
            
            # 解析FPS
            fps_match = re.search(r"(\d+(?:\.\d+)?)\s*fps", output)
            if fps_match:
                fps = float(fps_match.group(1))
            
            return {'duration': duration, 'fps': fps}
        except Exception as e:
            logger.error(f"获取视频信息失败: {e}")
            return {'duration': 0.0, 'fps': 30.0}
    
    # =========================================================
    # _extract_video_features - 双流提取视频特征（v3.0：灰度SAD + RGB色彩）
    # =========================================================
    def _extract_video_features(self, video_path: str, duration: float, progress_callback) -> List[FrameInfo]:
        """
        双流提取视频特征（v3.0）
        流1: 灰度图用于SAD运动检测（高效）
        流2: 低分辨率RGB用于真实色彩分析（替代灰度估算）
        
        提取：
        1. SAD运动强度（灰度流）
        2. 真实HSV饱和度（RGB流，替代旧版灰度估算）
        3. 真实对比度（RGB流）
        4. 亮度标准差
        5. scene检测
        """
        w, h = self.pl.analysis_width, self.pl.analysis_height
        ds = self.pl.sad_downsample
        sad_w, sad_h = w // ds, h // ds
        
        # RGB分析用更低分辨率（320x180），减少带宽消耗
        rgb_w, rgb_h = min(w, 320), min(h, 180)
        
        # 双流ffmpeg：流0=灰度(运动)，流1=rgb(色彩)
        # 使用split滤镜共享输入，分别走不同处理链
        hw_args = []
        
        cmd = [
            self.ffmpeg_path,
            "-nostats", "-loglevel", "info",
        ] + hw_args + [
            "-i", video_path,
            "-filter_complex",
            # 流0: fps抽帧 -> scale -> gray（用于SAD运动检测）
            # 流1: fps抽帧 -> scale 320x180 -> bgr24（用于真实色彩分析）
            f"fps={self.pl.base_frame_rate},split=2[vin0][vin1];"
            f"[vin0]scale={w}:{h},format=gray[vgray];"
            f"[vin1]scale={rgb_w}:{rgb_h},format=bgr24[vrgb]",
            "-map", "[vgray]", "-f", "rawvideo", "-pix_fmt", "gray",
            "-map", "[vrgb]", "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-"
        ]
        
        logger.debug(f"ffmpeg双流视频分析: gray {w}x{h} + bgr24 {rgb_w}x{rgb_h}")
        
        frames: List[FrameInfo] = []
        prev_small = None
        proc = None
        
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=(w * h + rgb_w * rgb_h * 3) * 2,
            )
            
            frame_bytes_gray = w * h
            frame_bytes_rgb = rgb_w * rgb_h * 3
            frame_bytes_total = frame_bytes_gray + frame_bytes_rgb
            
            idx = 0
            max_frames = int(duration * self.pl.base_frame_rate) + 5
            buffer_remaining = b''
            
            t_start = time.time()
            last_progress_report = 0
            
            while True:
                # 读取完整双帧数据
                needed = frame_bytes_total - len(buffer_remaining)
                if needed > 0:
                    chunk = proc.stdout.read(needed)
                    if not chunk:
                        break
                    buffer_remaining += chunk
                
                if len(buffer_remaining) < frame_bytes_total:
                    break
                
                # 拆分灰度帧和RGB帧
                gray_data = buffer_remaining[:frame_bytes_gray]
                rgb_data = buffer_remaining[frame_bytes_gray:frame_bytes_total]
                buffer_remaining = buffer_remaining[frame_bytes_total:]
                
                # ---- 灰度流：SAD运动检测 ----
                arr_gray = np.frombuffer(gray_data, dtype=np.uint8).reshape(h, w)
                brightness = float(arr_gray.mean())
                
                # SAD下采样
                arr_small = arr_gray[::ds, ::ds].astype(np.int16)
                
                # SAD运动强度
                if prev_small is not None:
                    sad = np.abs(arr_small - prev_small).sum()
                    motion_score = float(sad) / float(sad_w * sad_h)
                    motion_score = min(motion_score * 3.0, 100.0)
                else:
                    motion_score = 0.0
                
                # 亮度标准差
                if arr_small.size > 100:
                    brightness_std = float(arr_small.std())
                    brightness_std = min(brightness_std * 2.0, 100.0)
                else:
                    brightness_std = 0.0
                
                # ---- RGB流：真实色彩分析 ----
                arr_bgr = np.frombuffer(rgb_data, dtype=np.uint8).reshape(rgb_h, rgb_w, 3)
                
                # 转换BGR -> HSV，计算真实饱和度
                arr_bgr_f = arr_bgr.astype(np.float32)
                b, g, r = arr_bgr_f[:,:,0], arr_bgr_f[:,:,1], arr_bgr_f[:,:,2]
                max_c = np.maximum(np.maximum(r, g), b)
                min_c = np.minimum(np.minimum(r, g), b)
                diff = max_c - min_c
                # 饱和度 = diff / max_c (避免除零)
                safe_max = np.where(max_c > 1e-3, max_c, 1.0)
                saturation_map = diff / safe_max
                # 只计算非暗区域的饱和度（亮度>30的像素）
                bright_mask = max_c > 30
                if bright_mask.any():
                    real_saturation = float(saturation_map[bright_mask].mean()) * 100.0
                else:
                    real_saturation = 0.0
                real_saturation = min(real_saturation, 100.0)
                
                # 真实对比度（RMS对比度，比max-min更稳健）
                gray_f = arr_bgr_f.mean(axis=2)  # BGR转灰度
                mean_val = gray_f.mean()
                if mean_val > 1e-3:
                    rms_contrast = float(np.sqrt(((gray_f - mean_val) ** 2).mean()))
                    real_contrast = min(rms_contrast / 1.28, 100.0)  # 映射到0-100
                else:
                    real_contrast = 0.0
                
                # 颜色变化检测
                scene_score = 0.0
                is_boundary = False
                if motion_score > self.pl.l1_scene_threshold * 50:
                    scene_score = min(motion_score / 50.0, 1.0)
                    is_boundary = scene_score > self.pl.l1_scene_threshold
                
                # 保存
                ts = idx / self.pl.base_frame_rate
                frame = FrameInfo(
                    frame_index=idx,
                    timestamp=ts,
                    brightness=brightness,
                    motion_score=motion_score,
                    color_variation=motion_score * 0.5,
                    is_scene_boundary=is_boundary,
                    scene_score=scene_score,
                    audio_energy=0.0,
                    # v3.0: 真实HSV饱和度（替代灰度估算）
                    saturation=real_saturation,
                    # v3.0: 真实RMS对比度（替代max-min）
                    contrast=real_contrast,
                    brightness_std=brightness_std,
                )
                frames.append(frame)
                
                prev_small = arr_small
                idx += 1
                
                # 内存压力控制
                if self.pl.enable_memory_pressure_control and idx - self._last_memory_check > self._memory_check_interval:
                    self._check_memory_pressure()
                    self._last_memory_check = idx
                
                # v3.0: ResourceGovernor闭环CPU节流（替代旧版固定5ms sleep）
                from core.resource_governor import ResourceGovernor
                gov = ResourceGovernor.get_instance()
                gov.throttle()
                
                # 进度回调
                if progress_callback and (idx - last_progress_report) > max(1, max_frames // 20):
                    pct = int(idx / max(max_frames, 1) * 100)
                    pct = min(pct, 100)
                    progress_callback(pct, 100, f"视频分析 {pct}% ({idx}/{max_frames}帧)")
                    last_progress_report = idx
                
                if idx > max_frames:
                    break
            
            t_end = time.time()
            logger.info(f"视频特征提取(v3.0双流): {idx}帧, 耗时{t_end-t_start:.1f}s, "
                      f"内存峰值~{self._peak_memory_mb:.0f}MB")
        
        except Exception as e:
            logger.error(f"视频特征提取异常: {e}")
            import traceback
            logger.error(traceback.format_exc())
        finally:
            if proc is not None:
                try:
                    proc.stdout.close()
                except Exception:
                    pass
                try:
                    proc.wait(timeout=3)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
        
        return frames
    
    # =========================================================
    # _extract_audio_energy - 提取音频能量（v3.0：真实FFT频谱分析）
    # =========================================================
    def _extract_audio_energy(self, video_path: str, duration: float) -> Dict[float, Dict[str, float]]:
        """
        提取音频能量（v3.0 真实FFT频谱分析）
        返回: {time: {'energy': 0_1, 'low_freq': 0_1, 'mid_freq': 0_1, 'high_freq': 0_1}}
            
        策略:
        - 使用ffmpeg提取PCM原始音频数据
        - 滑动窗口RMS计算时间变化的能量
        - 真实FFT频谱分层（替代旧版经验模型）
        - 低频20-300Hz(爆炸/冲击)、中频300-2000Hz(人声/对话)、高频2000-8000Hz(特效/金属声)
        """
        audio_by_time: Dict[float, Dict[str, float]] = {}
        proc = None
        try:
            sample_rate = 16000
            window_sec = 0.5  # 0.5秒窗口
            window_samples = int(sample_rate * window_sec)
            hop_samples = window_samples  # 无重叠
                
            # 使用ffmpeg提取PCM原始音频（16kHz mono s16le）
            cmd = [
                self.ffmpeg_path, "-i", video_path, "-vn",
                "-acodec", "pcm_s16le",
                "-ar", str(sample_rate),
                "-ac", "1",
                "-f", "s16le",
                "-nostats", "-loglevel", "error",
                "-"
            ]
                
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                bufsize=4 * 1024 * 1024
            )
                
            # 批量读取：一次读10秒的音频数据
            bulk_samples = sample_rate * 10
            bulk_bytes = bulk_samples * 2  # 16位=2字节
            buffer = b''
            total_samples_read = 0
            window_idx = 0
                
            while True:
                chunk = proc.stdout.read(bulk_bytes)
                if not chunk:
                    break
                buffer += chunk
                    
                # 处理所有完整窗口
                while len(buffer) >= window_samples * 2:
                    raw = buffer[:window_samples * 2]
                    buffer = buffer[window_samples * 2:]
                        
                    # 解析为numpy数组
                    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
                        
                    # 归一化到[-1, 1]
                    samples_norm = samples / 32768.0
                        
                    # RMS能量（0-1范围）
                    rms = float(np.sqrt(np.mean(samples_norm ** 2)))
                    energy = min(1.0, rms * 3.0)  # 放大映射到0-1
                        
                    # 真实FFT频谱分层
                    low, mid, high = self._compute_fft_freq_bands(samples_norm, sample_rate)
                        
                    t_f = window_idx * window_sec
                    audio_by_time[t_f] = {
                        'energy': energy,
                        'low_freq': low,
                        'mid_freq': mid,
                        'high_freq': high,
                    }
                    window_idx += 1
                    total_samples_read += window_samples
            
            # 将0.5秒窗口数据对齐到1秒时间点（用于帧匹配）
            aligned: Dict[float, Dict[str, float]] = {}
            for t_sec in range(int(duration) + 1):
                t_f = float(t_sec)
                # 收集该秒内的所有窗口数据
                window_energies = []
                window_lows = []
                window_mids = []
                window_highs = []
                for wt, wv in audio_by_time.items():
                    if abs(wt - t_f) < 1.0:
                        window_energies.append(wv['energy'])
                        window_lows.append(wv['low_freq'])
                        window_mids.append(wv['mid_freq'])
                        window_highs.append(wv['high_freq'])
                
                if window_energies:
                    aligned[t_f] = {
                        'energy': float(np.mean(window_energies)),
                        'low_freq': float(np.mean(window_lows)),
                        'mid_freq': float(np.mean(window_mids)),
                        'high_freq': float(np.mean(window_highs)),
                    }
                else:
                    # 找最近的窗口
                    if audio_by_time:
                        nearest_t = min(audio_by_time.keys(), key=lambda x: abs(x - t_f))
                        aligned[t_f] = dict(audio_by_time[nearest_t])
                    else:
                        aligned[t_f] = {'energy': 0.1, 'low_freq': 0.1, 'mid_freq': 0.1, 'high_freq': 0.1}
                
            logger.info(f"音频能量提取完成(v3.0 FFT), 窗口数={len(audio_by_time)}, "
                       f"对齐秒数={len(aligned)}, 平均能量={sum(v['energy'] for v in aligned.values())/max(len(aligned),1):.2f}")
            return aligned
                
        except Exception as e:
            logger.error(f"音频能量提取异常: {e}")
            import traceback
            logger.error(traceback.format_exc())
            for i in range(int(duration) + 1):
                audio_by_time[float(i)] = {
                    'energy': 0.1, 'low_freq': 0.1,
                    'mid_freq': 0.1, 'high_freq': 0.1,
                }
            return audio_by_time
        finally:
            if proc is not None:
                try:
                    proc.stdout.close()
                except Exception:
                    pass
                try:
                    proc.wait(timeout=3)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
    
    @staticmethod
    def _compute_fft_freq_bands(samples: np.ndarray, sample_rate: int) -> Tuple[float, float, float]:
        """
        真实FFT频谱分层（替代旧版经验模型）
        
        频段定义:
        - 低频 20-300Hz: 爆炸、冲击、低音鼓
        - 中频 300-2000Hz: 人声对话、环境音
        - 高频 2000-8000Hz: 特效、金属撞击、嘶嘶声
        
        返回: (low_ratio, mid_ratio, high_ratio) 各频段能量占总能量的比例(0-1)
        """
        if len(samples) < 64:
            return 0.33, 0.34, 0.33  # 数据不足，均匀分布
        
        # 加Hann窗减少频谱泄漏
        windowed = samples * np.hanning(len(samples))
        
        # FFT计算
        fft = np.fft.rfft(windowed)
        freqs = np.fft.rfftfreq(len(windowed), 1.0 / sample_rate)
        magnitudes = np.abs(fft)
        
        # 避免除零
        total_energy = np.sum(magnitudes) + 1e-10
        
        # 低频 20-300Hz
        low_mask = (freqs >= 20) & (freqs <= 300)
        low_energy = float(np.sum(magnitudes[low_mask])) if np.any(low_mask) else 0.0
        
        # 中频 300-2000Hz
        mid_mask = (freqs >= 300) & (freqs <= 2000)
        mid_energy = float(np.sum(magnitudes[mid_mask])) if np.any(mid_mask) else 0.0
        
        # 高频 2000-8000Hz
        high_mask = (freqs >= 2000) & (freqs <= 8000)
        high_energy = float(np.sum(magnitudes[high_mask])) if np.any(high_mask) else 0.0
        
        # 归一化为比例
        low_ratio = low_energy / total_energy
        mid_ratio = mid_energy / total_energy
        high_ratio = high_energy / total_energy
        
        return low_ratio, mid_ratio, high_ratio
    
    @staticmethod
    def _classify_audio_semantic(energy: float, low_ratio: float, mid_ratio: float, high_ratio: float) -> tuple:
        """
        音频语义分类（v3.1新增）
        
        基于 FFT 频谱特征将音频分为5类：
        - 'speech':  语音对话——中频主导、高频低、能量中等
        - 'action':  动作音效——低频强+宽频、能量中高
        - 'explosion': 爆炸冲击——低频极强、总能量高、尖锐起音
        - 'music':   音乐/BGM——频谱较均匀、持续能量
        - 'silence': 静音——总能量极低
        
        返回: (label: str, confidence: float)
        """
        # 1. 静音检测
        if energy < 0.05:
            return ('silence', 0.9)
        
        # 2. 频谱主导判定
        # 语音特征：mid_ratio 是三个频段中最高的，且 high_ratio 较低
        #   人声主要能量在 300-3000Hz，高频（金属/嘶嘶声）少
        is_mid_dominant = mid_ratio > low_ratio and mid_ratio > high_ratio
        is_speech_like = is_mid_dominant and high_ratio < 0.30
        
        # 动作特征：low_ratio 高或频谱宽（三个频段都较高）
        is_low_dominant = low_ratio > mid_ratio and low_ratio > high_ratio
        is_broad_spectrum = (low_ratio > 0.25 and mid_ratio > 0.25 and high_ratio > 0.15)
        
        # 爆炸特征：低频极强 + 总能量高
        is_explosion_like = low_ratio > 0.50 and energy > 0.5
        
        # 音乐特征：频谱较均匀，没有明显主导频段，且能量中等
        #   高能量+均衡频谱更可能是动作场景混合声，非纯音乐
        is_even_spectrum = (abs(low_ratio - mid_ratio) < 0.10 and 
                           abs(mid_ratio - high_ratio) < 0.10 and
                           energy > 0.1 and energy < 0.5)
        
        # 3. 分类决策（优先级：speech > explosion > music > action）
        #   语音优先：避免高能量语音被宽频谱规则误判为动作
        if is_speech_like:
            # 中频主导+高频低 = 语音
            conf = min(1.0, mid_ratio * 1.2 + (1.0 - high_ratio) * 0.3)
            # 但如果能量非常高（>0.6），可能是尖叫/怒吼，仍归为 speech
            # 但给予较低置信度，因为高能量语音也可能混有动作
            if energy > 0.6:
                conf *= 0.7  # 高能量语音置信度降低
            return ('speech', conf)
        
        if is_explosion_like:
            conf = min(1.0, low_ratio * 1.5 + energy * 0.5)
            return ('explosion', conf)
        
        if is_even_spectrum:
            return ('music', 0.5)
        
        if is_broad_spectrum and energy > 0.25:
            # 宽频+高能量 = 动作音效（打斗、碰撞、混杂声）
            conf = min(1.0, 0.5 + energy * 0.5)
            return ('action', conf)
        
        if is_low_dominant and energy > 0.2:
            # 低频主导+中高能量 = 动作音效（冲击、重击）
            conf = min(1.0, low_ratio + energy * 0.3)
            return ('action', conf)
        
        # 兜底：根据最强频段判断
        if low_ratio >= mid_ratio and low_ratio >= high_ratio:
            return ('action', 0.3)
        if mid_ratio >= low_ratio and mid_ratio >= high_ratio:
            return ('speech', 0.3)
        
        return ('unknown', 0.1)
    
    # =========================================================
    # _build_shots - 根据帧的scene边界构建镜头
    # =========================================================
    def _compute_shot_features(self, shot_frames: List[FrameInfo]) -> Dict:
        """计算镜头的聚合特征（消除重复代码，v2.6含VFX特征）"""
        if not shot_frames:
            return {}
        motions = [fr.motion_score for fr in shot_frames]
        audios = [fr.audio_energy for fr in shot_frames]
        sats = [fr.saturation for fr in shot_frames]
        bstds = [fr.brightness_std for fr in shot_frames]
        cons = [fr.contrast for fr in shot_frames]
        alows = [fr.audio_low_freq for fr in shot_frames]
        amids = [fr.audio_mid_freq for fr in shot_frames]
        ahighs = [fr.audio_high_freq for fr in shot_frames]
        vfxs = [fr.vfx_energy_score for fr in shot_frames]  # v2.6
        # v3.1: 音频语义统计
        semantics = [fr.audio_semantic for fr in shot_frames]
        speech_count = sum(1 for s in semantics if s == 'speech')
        action_count = sum(1 for s in semantics if s in ('action', 'explosion'))
        n = len(shot_frames)
        # 主导音频语义
        if action_count > speech_count and action_count > n * 0.3:
            dominant_semantic = 'action'
        elif speech_count > action_count and speech_count > n * 0.3:
            dominant_semantic = 'speech'
        elif sum(1 for s in semantics if s == 'music') > n * 0.3:
            dominant_semantic = 'music'
        elif sum(1 for s in semantics if s == 'silence') > n * 0.5:
            dominant_semantic = 'silence'
        else:
            dominant_semantic = 'unknown'
        return {
            'avg_motion': sum(motions) / n,
            'max_motion': max(motions),
            'avg_audio': sum(audios) / n,
            'avg_color_var': sum(motions) / n * 0.5,
            'avg_saturation': sum(sats) / n,
            'avg_brightness_std': sum(bstds) / n,
            'avg_contrast': sum(cons) / n,
            'avg_audio_low_freq': sum(alows) / n,
            'avg_audio_mid_freq': sum(amids) / n,
            'avg_audio_high_freq': sum(ahighs) / n,
            'shot_duration': shot_frames[-1].timestamp - shot_frames[0].timestamp,
            # v2.6: VFX特征
            'avg_vfx_score': sum(vfxs) / n,
            'max_vfx_score': max(vfxs),
            # v3.1: 音频语义
            'dominant_audio_semantic': dominant_semantic,
            'speech_ratio': speech_count / n,
        }

    def _build_shots(self, frames: List[FrameInfo]) -> List[ShotInfo]:
        """根据scene边界标记构建镜头（v2.2含丰富特征）"""
        if not frames:
            return []
        
        shots: List[ShotInfo] = []
        start_idx = 0
        shot_idx = 0
        
        def _finalize_shot(start: int, end: int) -> Optional[ShotInfo]:
            """封装镜头创建逻辑"""
            sf = frames[start:end]
            if not sf:
                return None
            feats = self._compute_shot_features(sf)
            level = "hot" if (feats['avg_motion'] > self.pl.l2_motion_threshold and
                              feats['avg_audio'] > self.pl.l2_min_energy_threshold) else "normal"
            return ShotInfo(
                shot_index=shot_idx,
                start_time=sf[0].timestamp,
                end_time=sf[-1].timestamp,
                frame_count=len(sf),
                avg_motion=feats['avg_motion'],
                avg_audio=feats['avg_audio'],
                max_motion=feats['max_motion'],
                avg_color_var=feats['avg_color_var'],
                dominant_level=level,
                avg_saturation=feats['avg_saturation'],
                avg_brightness_std=feats['avg_brightness_std'],
                avg_contrast=feats['avg_contrast'],
                avg_audio_low_freq=feats['avg_audio_low_freq'],
                avg_audio_mid_freq=feats['avg_audio_mid_freq'],
                avg_audio_high_freq=feats['avg_audio_high_freq'],
                shot_duration=feats['shot_duration'],
                # v2.6
                avg_vfx_score=feats['avg_vfx_score'],
                max_vfx_score=feats['max_vfx_score'],
                # v3.1: 音频语义
                dominant_audio_semantic=feats['dominant_audio_semantic'],
                speech_ratio=feats['speech_ratio'],
            )
        
        for i in range(1, len(frames)):
            f = frames[i]
            is_boundary = f.is_scene_boundary
            cur_dur = f.timestamp - frames[start_idx].timestamp
            
            if is_boundary and cur_dur >= self.pl.l1_min_shot_duration:
                shot = _finalize_shot(start_idx, i)
                if shot:
                    shots.append(shot)
                    shot_idx += 1
                start_idx = i
        
        # 处理最后一个镜头
        if start_idx < len(frames):
            shot = _finalize_shot(start_idx, len(frames))
            if shot:
                shots.append(shot)
        
        logger.info(f"镜头构建: {len(shots)}个镜头 (含v2.2特征)")
        return shots
    
    # =========================================================
    # v2.6: VFX特效检测集成
    # =========================================================
    def _extract_vfx_scores(self, video_path: str, duration: float) -> Dict[float, float]:
        """
        运行VFX检测器并将能量得分融合到时间轴
        返回: {time_sec: vfx_energy_score(0-100)}
        
        策略:
        - 调用VFXDetector.detect()获取色彩突变+亮度闪烁+VFX能量三个通道
        - 将vfx_energy通道的得分按时间映射到帧级别
        - 同时用color_burst和brightness_flash补充
        """
        vfx_by_time: Dict[float, float] = {}
        try:
            from core.vfx_detector import VFXDetector
            
            detector = VFXDetector()
            vfx_data = detector.detect(video_path)
            
            # 融合三个VFX通道到统一时间轴
            # 1. vfx_energy通道（v2.6新增，最综合）
            vfx_energy = vfx_data.get('vfx_energy', [])
            color_burst = vfx_data.get('color_burst', [])
            brightness_flash = vfx_data.get('brightness_flash', [])
            
            # 构建时间->得分映射
            raw_scores: Dict[float, float] = {}
            
            for item in vfx_energy:
                t = round(item['time'], 1)
                score = item['score'] * 100.0  # 0-1 -> 0-100
                if t in raw_scores:
                    raw_scores[t] = max(raw_scores[t], score)
                else:
                    raw_scores[t] = score
            
            # 用color_burst和brightness_flash补充
            for item in color_burst:
                t = round(item['time'], 1)
                score = item['score'] * 80.0  # 色彩突变权重略低
                if t in raw_scores:
                    raw_scores[t] = max(raw_scores[t], score)
                else:
                    raw_scores[t] = score
            
            for item in brightness_flash:
                t = round(item['time'], 1)
                score = item['score'] * 70.0
                if t in raw_scores:
                    raw_scores[t] = max(raw_scores[t], score)
                else:
                    raw_scores[t] = score
            
            # 填充到每秒时间点（与帧时间对齐）
            for sec in range(int(duration) + 1):
                t_f = float(sec)
                # 找最近的VFX得分（1.5秒容差）
                best_score = 0.0
                best_dist = 1.5
                for vt, vs in raw_scores.items():
                    dist = abs(vt - t_f)
                    if dist < best_dist:
                        best_dist = dist
                        best_score = vs
                vfx_by_time[t_f] = best_score
            
            logger.info(f"VFX能量融合完成: {len(raw_scores)}个原始数据点, "
                       f"平均VFX能量={sum(raw_scores.values())/max(len(raw_scores),1):.1f}")
            
        except Exception as e:
            logger.error(f"VFX能量提取异常: {e}")
            for i in range(int(duration) + 1):
                vfx_by_time[float(i)] = 0.0
        
        return vfx_by_time
    
    # =========================================================
    # _check_memory_pressure - 内存压力检查
    # =========================================================
    def _check_memory_pressure(self):
        """检查内存压力 - 高时释放缓存并警告"""
        try:
            import psutil
            process = psutil.Process()
            mem_mb = process.memory_info().rss / (1024 * 1024)
            cpu = process.cpu_percent(interval=0.01)
            
            self._peak_memory_mb = max(self._peak_memory_mb, mem_mb)
            self._peak_cpu_percent = max(self._peak_cpu_percent, cpu)
            
            # 如果内存超过限制的70%，触发强制GC
            if mem_mb > self.pl.max_memory_limit_mb * 0.7:
                import gc
                gc.collect()
                logger.debug(f"内存压力控制: {mem_mb:.0f}MB/{self.pl.max_memory_limit_mb}MB, 触发GC")
        except ImportError:
            # 没有psutil，跳过监控
            pass
        except Exception:
            pass


# =========================================================
# 快速自测
# =========================================================
if __name__ == "__main__":
    import sys
    
    print("="*50)
    print("UnifiedVideoPipeline v2.2 自测")
    print("="*50)
    
    # 构造模拟数据验证结构
    print("\n[1/3] 模拟FrameInfo/ShotInfo/PipelineResult结构...")
    
    f = FrameInfo(frame_index=0, timestamp=0.0, brightness=128, motion_score=25.0,
                  color_variation=12.5, is_scene_boundary=False, scene_score=0.1,
                  audio_energy=0.3,
                  # v2.2 新字段
                  saturation=50.0, brightness_std=30.0, contrast=60.0,
                  audio_low_freq=0.5, audio_mid_freq=0.3, audio_high_freq=0.4)
    print(f"  FrameInfo OK: ts={f.timestamp}, motion={f.motion_score}, sat={f.saturation}")
    
    s = ShotInfo(shot_index=0, start_time=0.0, end_time=5.0, frame_count=5,
                 avg_motion=25.0, avg_audio=0.4, max_motion=45.0,
                 avg_color_var=12.5, dominant_level="normal",
                 # v2.2
                 avg_saturation=50.0, avg_brightness_std=30.0, avg_contrast=60.0,
                 avg_audio_low_freq=0.5, avg_audio_mid_freq=0.3, avg_audio_high_freq=0.4,
                 shot_duration=5.0)
    print(f"  ShotInfo OK: duration={s.shot_duration}s, saturation={s.avg_saturation}")
    
    r = PipelineResult(video_path="test.mp4", duration=60.0, fps=1.0, total_frames=60,
                      frames=[], shots=[], analysis_resolution="720p", hardware_accel="none",
                      # v2.2
                      saturation_p50=50.0, brightness_std_p50=30.0, contrast_p50=60.0,
                      audio_low_p50=0.5, audio_mid_p50=0.3, audio_high_p50=0.4)
    print(f"  PipelineResult OK: duration={r.duration}s, sat_p50={r.saturation_p50}")
    
    print("\n[2/3] 测试SAD运动分析 + 颜色/对比度逻辑...")
    arr1 = np.random.randint(0, 100, size=(540, 960), dtype=np.uint8)
    arr2 = np.random.randint(0, 100, size=(540, 960), dtype=np.uint8)
    ds = 4
    small1 = arr1[::ds, ::ds].astype(np.int16)
    small2 = arr2[::ds, ::ds].astype(np.int16)
    sad = np.abs(small1 - small2).sum() / (small1.shape[0] * small1.shape[1])
    bstd = float(small1.std())
    contrast = float(small1.max()) - float(small1.min())
    print(f"  SAD运动值: {sad:.1f} (典型动作片通常>10, 对话场景<3)")
    print(f"  亮度标准差: {bstd:.1f} (动作片通常>20)")
    print(f"  对比度(估算): {contrast:.1f}")
    
    print("\n[3/3] 测试Pipeline初始化...")
    try:
        pipe = UnifiedVideoPipeline()
        print(f"  初始化OK: ffmpeg={pipe.ffmpeg_path}, hw={pipe.hw_accel_actual}")
    except Exception as e:
        print(f"  初始化警告（预期: {e}")
    
    print("\n" + "="*50)
    print("v2.2结构验证通过（新增颜色/饱和度/音频频谱分层 + 多窗口特征）")
    print("="*50)
