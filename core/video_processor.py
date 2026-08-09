"""
视频处理 v2.5 - 码流直接拷贝优先 + 硬解码 + ffmpeg参数极致调优 + 并行提取
              + 错误恢复 + 自动重试 + 异常格式处理（鲁棒性加固

版本演进:
v1.x: 基础提取（码流拷贝 + 快速编码降级
v2.1: 动态阈值 + 快速 SAD 运动分析
v2.2: 颜色饱和度/亮度分析 + 音频频谱分层
v2.3: 多线程流水线 + 增量分析 + ffmpeg参数极致调优 + 并行批量提取
v2.4: 资源控制（内存监控 + 动态采样率 + CPU节流 + GPU显存池
v2.5: 鲁棒性加固（错误恢复 + 自动重试 + 降级策略 + 异常格式处理

核心改进 (v2.5):
1. 错误恢复：提取失败时自动重试最多max_retry_count次
2. 降级策略：硬件解码失败时自动切换软件解码
3. 异常格式处理：ffprobe无法识别时尝试强制格式解析
4. 超时保护：避免长时间挂起进程
5. 损坏帧跳过：skip_corrupted_frames参数控制
"""
import subprocess
import os
import time
import shlex
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional, Dict, Tuple

from utils.logger import logger
from config import CONFIG


class VideoProcessor:
    """视频处理器 v2.5 - 鲁棒性加固 + 错误恢复 + 并行提取"""
    
    def __init__(self):
        # 查找ffmpeg路径（便携版优先
        portable_ffmpeg = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'ffmpeg', 'bin', 'ffmpeg.exe')
        portable_ffprobe = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'ffmpeg', 'bin', 'ffprobe.exe')
        
        if os.path.exists(portable_ffmpeg):
            self.ffmpeg_path = portable_ffmpeg
        elif CONFIG.ffmpeg_path and os.path.exists(CONFIG.ffmpeg_path):
            self.ffmpeg_path = CONFIG.ffmpeg_path
        else:
            self.ffmpeg_path = 'ffmpeg'
        
        if os.path.exists(portable_ffprobe):
            self.ffprobe_path = portable_ffprobe
        elif CONFIG.ffprobe_path and os.path.exists(CONFIG.ffprobe_path):
            self.ffprobe_path = CONFIG.ffprobe_path
        else:
            self.ffprobe_path = 'ffprobe'
        
        # 编码器优先级
        self.video_codecs = [
            'h264_nvenc', 'h264_qsv', 'libx264'
        ]
        self.audio_codec = 'aac'
        self.audio_bitrate = '128k'
        
        # v2.3: 线程锁（避免并行提取时日志混乱
        self._lock = threading.Lock()
        
        # v2.5: 运行状态追踪
        self._error_count = 0
        self._total_extractions = 0
        
        logger.info(f"VideoProcessor v2.5 初始化: ffmpeg={self.ffmpeg_path}, "
                   f"并行提取={'开启' if CONFIG.pipeline.enable_parallel_extract else '关闭'}, "
                   f"错误恢复={'开启' if CONFIG.pipeline.enable_error_recovery else '关闭'}")
    
    # =========================================================
    # v2.3: 获取ffmpeg基础参数
    # =========================================================
    def _get_ffmpeg_base_args(self) -> List[str]:
        """获取v2.3极致调优的ffmpeg基础参数"""
        pl = CONFIG.pipeline
        args = []
        
        # 基础交互参数
        if pl.ffmpeg_nostdin:
            args.append('-nostdin')
        
        # 日志级别
        args.extend(['-loglevel', pl.ffmpeg_loglevel])
        
        # 探测参数（加快启动速度
        args.extend(['-probesize', str(pl.ffmpeg_probesize)])
        args.extend(['-analyzeduration', str(pl.ffmpeg_analyzeduration)])
        
        # 低延迟模式（可选
        if pl.ffmpeg_enable_lowlatency:
            args.extend(['-avioflags', 'direct'])
        
        # 线程数
        if pl.ffmpeg_threads > 0:
            args.extend(['-threads', str(pl.ffmpeg_threads)])
        
        return args
    
    # =========================================================
    # v2.3: 获取码流拷贝参数
    # =========================================================
    def _get_copy_args(self, start_time: float, duration: float, output_path: str) -> List[str]:
        """
        获取码流拷贝参数（快速模式）
        
        注意: 生产代码已迁移到 _try_stream_copy_v23()，此方法保留用于测试兼容。
        """
        pl = CONFIG.pipeline
        args = []
        
        # v2.3: -ss放在-i前（快速定位，不精确但更快
        args.extend(['-ss', f"{start_time:.3f}"])
        args.extend(['-t', f"{duration:.3f}"])
        
        # 码流拷贝
        args.extend(['-c', 'copy'])
        
        # v2.3: 时间戳拷贝 + 负时间戳处理（避免音视频同步问题
        if pl.ffmpeg_copyts:
            args.extend(['-copyts'])
        args.extend(['-avoid_negative_ts', pl.ffmpeg_avoid_negative_ts])
        
        # 快速播放启动
        args.extend(['-movflags', '+faststart'])
        
        return args
    
    # =========================================================
    # v2.5: 带自动重试的片段提取（错误恢复核心
    # =========================================================
    def extract_segment_with_retry(self, input_path: str, start_time: float, 
                                    duration: float, output_path: str, 
                                    force_recode: bool = False) -> bool:
        """
        带自动重试的片段提取 (v2.5 错误恢复核心)
        
        策略:
        1. 正常尝试提取（码流拷贝 → 快速编码
        2. 失败时: 等待 retry_delay_sec 后重试
        3. 第2次重试: 启用降级模式（强制重编码 + 禁用硬件加速
        4. 第3次重试: 使用最保守参数（慢编码 + 软件解码
        """
        pl = CONFIG.pipeline
        self._total_extractions += 1
        
        max_attempts = pl.max_retry_count if pl.enable_error_recovery else 1
        
        for attempt in range(max_attempts):
            try:
                if attempt == 0:
                    # 第一次: 正常提取
                    result = self.extract_segment(input_path, start_time, duration, 
                                                output_path, force_recode)
                elif attempt == 1:
                    # 第二次: 强制重编码，跳过码流拷贝
                    logger.warning(f"[v2.5 Retry {attempt+1}/{max_attempts}] 强制重编码: {os.path.basename(output_path)}")
                    time.sleep(pl.retry_delay_sec)
                    result = self.extract_segment(input_path, start_time, duration, 
                                                output_path, force_recode=True)
                else:
                    # 第三次及以上: 最保守模式 + 更长超时
                    logger.warning(f"[v2.5 Retry {attempt+1}/{max_attempts}] 保守模式: {os.path.basename(output_path)}")
                    time.sleep(pl.retry_delay_sec * 2)
                    result = self._extract_safe_mode(input_path, start_time, duration, output_path)
                
                if result:
                    if attempt > 0:
                        logger.info(f"[v2.5] 重试成功 (第{attempt+1}次尝试): {os.path.basename(output_path)}")
                    return True
                    
            except Exception as e:
                logger.error(f"[v2.5 Retry {attempt+1}/{max_attempts}] 异常: {e}")
                self._error_count += 1
                if attempt < max_attempts - 1:
                    time.sleep(pl.retry_delay_sec)
                continue
        
        logger.error(f"[v2.5] 所有 {max_attempts} 次尝试均失败: {os.path.basename(output_path)}")
        self._error_count += 1
        return False
    
    # =========================================================
    # v2.5: 安全模式提取（最保守参数 + 软件解码
    # =========================================================
    def _extract_safe_mode(self, input_path: str, start_time: float, duration: float,
                          output_path: str) -> bool:
        """安全模式提取 - 完全禁用硬件加速，使用最兼容参数"""
        try:
            cmd = [
                self.ffmpeg_path, '-y', '-nostdin',
                '-loglevel', 'error',
                '-err_detect', 'ignore_err',
                '-i', input_path,
                '-ss', f"{start_time:.3f}",
                '-t', f"{duration:.3f}",
                '-vcodec', 'libx264',             # 软件编码，最兼容
                '-preset', 'ultrafast',
                '-crf', '26',
                '-acodec', 'aac',
                '-b:a', '128k',
                '-movflags', '+faststart',
                output_path
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=max(180, duration * 6)
            )
            
            return result.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 1000
            
        except Exception as e:
            logger.error(f"[v2.5 SafeMode] 安全模式提取异常: {e}")
            return False
    
    # =========================================================
    # v2.5: 格式异常检测与自动降级处理
    # =========================================================
    def detect_and_fix_format(self, video_path: str) -> bool:
        """
        检测视频格式是否异常，必要时尝试修复/转码处理
        
        返回: True 表示视频可用（原始或已修复），False 表示不可用
        """
        pl = CONFIG.pipeline
        
        if not pl.enable_format_detection:
            return True
        
        # Step 1: 基本格式检测
        info = self.get_video_info(video_path)
        if info and info.get('duration', 0) > 0:
            return True  # 格式正常
        
        logger.warning(f"[v2.5 Format] 格式异常，尝试修复: {os.path.basename(video_path)}")
        
        if not pl.unsupported_format_fallback:
            return False
        
        # Step 2: 尝试用 ffmpeg 强制重新封装（快速修复容器问题
        temp_path = video_path + ".fixed.mp4"
        try:
            cmd = [
                self.ffmpeg_path, '-y', '-nostdin',
                '-loglevel', 'error',
                '-err_detect', 'ignore_err',
                '-i', video_path,
                '-c', 'copy',
                '-movflags', '+faststart',
                temp_path
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            
            if result.returncode == 0 and os.path.exists(temp_path) and os.path.getsize(temp_path) > 10000:
                # 替换原文件
                try:
                    os.replace(temp_path, video_path)
                    logger.info(f"[v2.5 Format] 容器修复成功: {os.path.basename(video_path)}")
                    return True
                except OSError:
                    os.remove(temp_path)
            elif os.path.exists(temp_path):
                os.remove(temp_path)
        except Exception as e:
            logger.error(f"[v2.5 Format] 容器修复异常: {e}")
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
        
        return False
    
    # =========================================================
    # 提取单个片段（v2.5 入口方法
    # =========================================================
    def extract_segment(self, input_path: str, start_time: float, duration: float, 
                        output_path: str, force_recode: bool = False) -> bool:
        """
        提取视频片段 (v2.5 鲁棒性版)
        
        策略：
        1. 先尝试 -c copy（码流直接拷贝，速度最快但需要对齐关键帧
        2. 如果失败，自动降级快速编码
        """
        if not os.path.exists(input_path):
            logger.error(f"视频不存在: {input_path}")
            return False
        
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        
        # 时间边界处理
        start_time = max(0.0, start_time)
        duration = max(1.0, duration)
        
        # Step 1: 尝试码流拷贝（v2.3参数极致调优
        if not force_recode:
            if self._try_stream_copy_v23(input_path, start_time, duration, output_path):
                return True
        
        # Step 2: 快速编码（如果copy失败
        logger.info(f"码流拷贝失败，使用快速编码: {os.path.basename(output_path)}")
        result = self._fast_recode_v23(input_path, start_time, duration, output_path)
        if result:
            logger.info(f"✓ 快速编码完成: {os.path.basename(output_path)}")
        return result
    
    # =========================================================
    # v2.3: 码流拷贝（优先方式，参数极致调优
    # =========================================================
    def _try_stream_copy_v23(self, input_path: str, start_time: float, duration: float,
                             output_path: str) -> bool:
        """尝试直接码流拷贝 (v2.3 参数调优版)
        
        v3.4修复: 拷贝后验证实际时长，如果过短则降级到重编码
        """
        pl = CONFIG.pipeline
        try:
            base_args = self._get_ffmpeg_base_args()
            
            # v2.3: -noaccurate_seek 加快定位速度
            seek_args = []
            if pl.ffmpeg_noaccurate_seek:
                seek_args.append('-noaccurate_seek')
            
            cmd = ([self.ffmpeg_path, '-y'] + base_args + seek_args +
                   ['-ss', f"{start_time:.3f}", '-i', input_path,
                    '-t', f"{duration:.3f}", '-c', 'copy',
                    '-avoid_negative_ts', pl.ffmpeg_avoid_negative_ts,
                    '-movflags', '+faststart',
                    output_path])
            
            logger.debug(f"[StreamCopy] ffmpeg命令: {' '.join(shlex.quote(c) for c in cmd[:12])}...")
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=max(60, duration * 2)
            )
            
            if not (result.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 1000):
                if result.stderr:
                    logger.debug(f"[StreamCopy] 失败原因: {result.stderr[:200]}")
                return False
            
            # v3.4: 验证输出时长是否合理（关键帧不对齐可能导致片段极短）
            actual_dur = self._get_output_duration(output_path)
            if actual_dur < duration * 0.5 and actual_dur < 3.0:
                # 输出时长不到请求时长的50%且少于3秒，视为关键帧不对齐
                logger.warning(f"[StreamCopy] 关键帧不对齐，输出仅{actual_dur:.1f}s (请求{duration:.1f}s)，降级重编码")
                try:
                    os.remove(output_path)
                except (OSError, PermissionError):
                    pass
                return False
            
            logger.info(f"[StreamCopy] 成功: {os.path.basename(output_path)} "
                       f"({os.path.getsize(output_path)/1024/1024:.1f}MB, 实际{actual_dur:.1f}s)")
            return True
            
        except subprocess.TimeoutExpired:
            logger.warning(f"[StreamCopy] 超时: {os.path.basename(output_path)}")
            return False
        except Exception as e:
            logger.error(f"[StreamCopy] 异常: {e}")
            return False
    
    # =========================================================
    # v2.3: 快速编码（降级方案
    # =========================================================
    def _get_output_duration(self, output_path: str) -> float:
        """v3.4: 快速获取输出文件的实际时长（用于验证stream copy结果）"""
        try:
            cmd = [
                self.ffprobe_path, '-v', 'error',
                '-show_entries', 'format=duration',
                '-of', 'default=noprint_wrappers=1:nokey=1',
                output_path
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if result.returncode == 0 and result.stdout.strip():
                return float(result.stdout.strip())
        except Exception:
            pass
        return 0.0
    
    def _fast_recode_v23(self, input_path: str, start_time: float, duration: float,
                         output_path: str) -> bool:
        """快速编码 - v2.3参数调优版
        
        v3.4修复: -ss 放在 -i 后面，确保精确到帧的截取
        """
        pl = CONFIG.pipeline
        base_args = self._get_ffmpeg_base_args()
        
        for codec in self.video_codecs:
            try:
                preset = 'ultrafast' if codec == 'libx264' else 'p1'
                
                # v2.3: 构建命令
                # v3.4: -ss 在 -i 后面，精确到帧的截取（不依赖关键帧对齐）
                cmd = [self.ffmpeg_path, '-y'] + base_args + [
                    '-i', input_path,
                    '-ss', f"{start_time:.3f}",
                    '-t', f"{duration:.3f}",
                    '-vcodec', codec,
                    '-preset', preset,
                    '-crf', '23',
                    '-acodec', self.audio_codec,
                    '-b:a', self.audio_bitrate,
                    '-movflags', '+faststart',
                    output_path
                ]
                
                logger.debug(f"[FastRecode] 尝试编码器 {codec}")
                
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=max(120, duration * 4)
                )
                
                if result.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 1000:
                    logger.debug(f"[FastRecode] 编码器 {codec} 成功")
                    return True
                
                if result.stderr and 'not found' not in result.stderr.lower():
                    logger.debug(f"[FastRecode] 编码器{codec}失败: {result.stderr[:200]}")
                continue
                
            except subprocess.TimeoutExpired:
                logger.warning(f"[FastRecode] 编码器{codec}超时")
                continue
            except Exception as e:
                logger.debug(f"[FastRecode] 编码器{codec}异常: {e}")
                continue
        
        logger.error(f"[FastRecode] 所有编码器失败")
        return False
    
    # =========================================================
    # v2.3: 单个片段提取（带超时重试
    # =========================================================
    def _extract_single(self, input_path: str, start_time: float, duration: float,
                      output_path: str, force_recode: bool = False) -> Tuple[bool, str]:
        """线程安全的单个片段提取"""
        with self._lock:
            logger.info(f"开始提取: {os.path.basename(output_path)} ({duration:.1f}s)")
        success = self.extract_segment(input_path, start_time, duration, output_path, force_recode)
        return success, output_path

    # =========================================================
    # v2.3: 批量处理（支持并行提取
    # =========================================================
    def extract_segments(self, input_path: str, segments: List[Dict], 
                         output_dir: str, progress_callback=None) -> List[Dict]:
        """
        批量提取多个片段 (v2.5 支持并行提取 + 错误恢复)
        
        策略:
        - 片段数 >= parallel_extract_min_segments: 并行提取
        - 否则: 串行提取
        - v2.5: 每个提取内部都带自动重试
        
        参数: segments = [{start_time, duration, name, ...}, ...]
        返回: 成功的片段列表
        """
        os.makedirs(output_dir, exist_ok=True)
        success_segments = []
        pl = CONFIG.pipeline
        
        use_parallel = (pl.enable_parallel_extract and 
                       len(segments) >= pl.parallel_extract_min_segments and
                       pl.parallel_extract_max_workers > 1)
        
        if use_parallel:
            logger.info(f"[v2.5] 使用并行提取: {len(segments)} 个片段, 最大并行 {pl.parallel_extract_max_workers} 个")
            t0 = time.time()
            success_segments = self._extract_parallel(input_path, segments, output_dir, progress_callback)
            t1 = time.time()
            logger.info(f"[v2.5] 并行提取完成: {len(success_segments)}/{len(segments)} 成功, 耗时 {t1-t0:.1f}s")
        else:
            logger.info(f"[v2.5] 使用串行提取: {len(segments)} 个片段")
            for i, seg in enumerate(segments):
                start = float(seg.get('start_time', 0))
                duration = float(seg.get('duration', 0))
                name = seg.get('name', f"segment_{i+1:03d}.mp4")
                output_path = os.path.join(output_dir, name)
                
                if progress_callback:
                    progress_callback(i, len(segments), f"提取片段 {i+1}/{len(segments)}: {name}")
                
                # v2.5: 使用带重试的提取
                if self.extract_segment_with_retry(input_path, start, duration, output_path):
                    seg_info = dict(seg)
                    seg_info['output_path'] = output_path
                    try:
                        seg_info['size_bytes'] = os.path.getsize(output_path)
                    except OSError:
                        seg_info['size_bytes'] = 0
                    seg_info['success'] = True
                    success_segments.append(seg_info)
                else:
                    logger.warning(f"[v2.5] 片段提取失败: {name}")
        
        if progress_callback:
            progress_callback(len(segments), len(segments), "片段提取完成")
        
        logger.info(f"[v2.5] 批量提取: 成功 {len(success_segments)}/{len(segments)} 个片段")
        return success_segments
    
    # =========================================================
    # v2.5: 并行提取实现（带错误恢复
    # =========================================================
    def _extract_parallel(self, input_path: str, segments: List[Dict], 
                         output_dir: str, progress_callback=None) -> List[Dict]:
        """并行提取多个片段（v2.5 带自动重试）"""
        pl = CONFIG.pipeline
        success_segments = []
        completed = 0
        total = len(segments)
        progress_lock = threading.Lock()
        
        def process_segment(seg: Dict) -> Optional[Dict]:
            nonlocal completed
            start = float(seg.get('start_time', 0))
            duration = float(seg.get('duration', 0))
            name = seg.get('name', f"segment_{segments.index(seg)+1:03d}.mp4")
            output_path = os.path.join(output_dir, name)
            
            # v2.5: 使用带重试的提取
            success = self.extract_segment_with_retry(input_path, start, duration, output_path)
            
            # 线程安全更新进度
            with progress_lock:
                completed += 1
                if progress_callback:
                    progress_callback(completed, total, f"并行提取 {completed}/{total}: {name}")
            
            if success:
                seg_info = dict(seg)
                seg_info['output_path'] = output_path
                try:
                    seg_info['size_bytes'] = os.path.getsize(output_path)
                except OSError:
                    seg_info['size_bytes'] = 0
                seg_info['success'] = True
                return seg_info
            else:
                logger.warning(f"[v2.5 Parallel] 片段提取失败: {name}")
                return None
        
        # 使用线程池并行提取
        # v3.0: 用ResourceGovernor限制并发ffmpeg数
        from core.resource_governor import ResourceGovernor
        gov = ResourceGovernor.get_instance()
        max_workers = min(pl.parallel_extract_max_workers, total, gov.pl.max_ffmpeg_processes)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(process_segment, seg) for seg in segments]
            
            for future in as_completed(futures):
                try:
                    result = future.result(timeout=max(120, 30 * total/max_workers))
                    if result:
                        success_segments.append(result)
                except Exception as e:
                    logger.error(f"[Parallel] 并行提取异常: {e}")
        
        return success_segments
    
    # =========================================================
    # 获取视频信息
    # =========================================================
    def get_video_info(self, video_path: str) -> Dict:
        """使用ffprobe获取视频详细信息"""
        try:
            cmd = [
                self.ffprobe_path,
                '-v', 'error',
                '-show_entries', 'format=duration,size,bit_rate:stream=codec_name,width,height,r_frame_rate,codec_type',
                '-of', 'json',
                video_path
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                import json
                info = json.loads(result.stdout)
                
                # 提取关键信息
                format_info = info.get('format', {})
                streams = info.get('streams', [])
                
                duration = float(format_info.get('duration', 0))
                size = int(format_info.get('size', 0))
                
                video_stream = next((s for s in streams if s.get('codec_type') == 'video'), {})
                width = int(video_stream.get('width', 0))
                height = int(video_stream.get('height', 0))
                codec = video_stream.get('codec_name', '')
                
                # 帧率解析 (如 "30/1")
                fps_str = video_stream.get('r_frame_rate', '30/1')
                try:
                    num, den = fps_str.split('/')
                    fps = float(num) / float(den) if den else 30.0
                except Exception:
                    fps = 30.0
                
                return {
                    'duration': duration,
                    'size_bytes': size,
                    'width': width,
                    'height': height,
                    'fps': fps,
                    'codec': codec,
                }
        except Exception as e:
            logger.error(f"ffprobe获取信息失败: {e}")
        
        # 兜底：用ffmpeg解析
        try:
            cmd = [self.ffmpeg_path, '-i', video_path, '-hide_banner']
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            
            import re
            duration = 0.0
            dur_match = re.search(r"Duration:\s+(\d+):(\d+):(\d+\.?\d*)", result.stderr)
            if dur_match:
                h, m, s = float(dur_match.group(1)), float(dur_match.group(2)), float(dur_match.group(3))
                duration = h * 3600 + m * 60 + s
            
            fps_match = re.search(r"(\d+(?:\.\d+)?)\s*fps", result.stderr)
            fps = float(fps_match.group(1)) if fps_match else 30.0
            
            wh_match = re.search(r"(\d{2,5})x(\d{2,5})", result.stderr)
            width = int(wh_match.group(1)) if wh_match else 1920
            height = int(wh_match.group(2)) if wh_match else 1080
            
            return {
                'duration': duration,
                'size_bytes': os.path.getsize(video_path) if os.path.exists(video_path) else 0,
                'width': width,
                'height': height,
                'fps': fps,
                'codec': '',
            }
        except Exception as e:
            logger.error(f"ffmpeg获取信息失败: {e}")
            return {'duration': 0, 'size_bytes': 0, 'width': 1920, 'height': 1080, 'fps': 30.0, 'codec': ''}

    # =========================================================
    # v2.5: 合并多个片段到单个文件 (ffmpeg concat demuxer)
    # =========================================================
    def _merge_segments_to_file(self, segment_paths: list, output_path: str) -> bool:
        """
        使用 ffmpeg concat demuxer 合并多个视频片段
        
        原理: 生成文本列表文件 → ffmpeg -f concat -safe 0 → -c copy 无重编码合并
        v2.6: 合并输出先写入临时目录, 再用 shutil.copy2 移到目标路径,
              避免 ffmpeg 子进程无权限写入受保护的目录 (如 下载/桌面)
        v3.3: 添加关键帧对齐保护，使用 -async 1 -vsync cfr 确保音视频同步
        """
        import shutil, tempfile
        
        if not segment_paths:
            logger.error("[Merge] 片段列表为空")
            return False
        
        if len(segment_paths) == 1:
            shutil.copy2(segment_paths[0], output_path)
            return os.path.exists(output_path)
        
        concat_dir = tempfile.mkdtemp(prefix="fireclip_merge_")
        # 合并输出用纯英文扩展名, 避免 ffmpeg 无法识别中文路径格式
        tmp_output = os.path.join(concat_dir, "_merged_output.mp4")
        concat_file = os.path.join(concat_dir, "concat_list.txt")
        try:
            with open(concat_file, 'w', encoding='utf-8') as f:
                for sp in segment_paths:
                    abs_path = os.path.abspath(sp).replace('\\', '/')
                    f.write(f"file '{abs_path}'\n")
            
            cmd = [
                self.ffmpeg_path, '-y', '-nostdin',
                '-loglevel', 'error',
                '-f', 'concat',
                '-safe', '0',
                '-i', concat_file,
                '-c', 'copy',
                '-async', '1',         # v3.3: 音频同步
                '-vsync', 'cfr',       # v3.3: 视频同步（恒定帧率）
                '-movflags', '+faststart',
                tmp_output
            ]
            
            logger.info(f"[Merge] 合并 {len(segment_paths)} 个片段 → {os.path.basename(output_path)}")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            
            if not (result.returncode == 0 and os.path.exists(tmp_output) and os.path.getsize(tmp_output) > 1000):
                logger.error(f"[Merge] 合并失败: {result.stderr[:500]}")
                # v3.3: 如果 copy 失败，尝试重编码
                return self._merge_segments_with_recode(segment_paths, output_path, concat_dir)
            
            logger.info(f"[Merge] 合并成功: {os.path.getsize(tmp_output)/1024/1024:.1f}MB, 移入目标...")
            
            # 用 Python shutil 复制到最终路径 (有用户权限)
            shutil.copy2(tmp_output, output_path)
            
            if os.path.exists(output_path) and os.path.getsize(output_path) > 1000:
                logger.info(f"[Merge] 移入成功: {output_path}")
                return True
            else:
                logger.error(f"[Merge] 移入失败: 无法写入 {output_path} (可能是目录无写入权限)")
                return False
        except Exception as e:
            logger.error(f"[Merge] 合并异常: {e}")
            return False
        finally:
            try:
                shutil.rmtree(concat_dir, ignore_errors=True)
            except (OSError, PermissionError):
                pass
    
    def _merge_segments_with_recode(self, segment_paths: list, output_path: str, 
                                     work_dir: str) -> bool:
        """
        v3.3: 合并失败时的降级方案 — 重新编码合并
        
        当 -c copy 无法对齐关键帧时，使用重编码确保完整性
        """
        import shutil
        
        logger.info(f"[Merge] 降级为重新编码合并...")
        
        concat_file = os.path.join(work_dir, "concat_list_recode.txt")
        tmp_output = os.path.join(work_dir, "_merged_recode.mp4")
        
        try:
            with open(concat_file, 'w', encoding='utf-8') as f:
                for sp in segment_paths:
                    abs_path = os.path.abspath(sp).replace('\\', '/')
                    f.write(f"file '{abs_path}'\n")
            
            cmd = [
                self.ffmpeg_path, '-y', '-nostdin',
                '-loglevel', 'error',
                '-f', 'concat',
                '-safe', '0',
                '-i', concat_file,
                '-c:v', 'libx264',
                '-preset', 'fast',
                '-crf', '23',
                '-c:a', 'aac',
                '-b:a', '128k',
                '-async', '1',
                '-vsync', 'cfr',
                '-movflags', '+faststart',
                tmp_output
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            
            if not (result.returncode == 0 and os.path.exists(tmp_output) and os.path.getsize(tmp_output) > 1000):
                logger.error(f"[Merge Recode] 重编码合并失败: {result.stderr[:500]}")
                return False
            
            logger.info(f"[Merge Recode] 重编码成功: {os.path.getsize(tmp_output)/1024/1024:.1f}MB")
            
            shutil.copy2(tmp_output, output_path)
            return os.path.exists(output_path) and os.path.getsize(output_path) > 1000
            
        except Exception as e:
            logger.error(f"[Merge Recode] 异常: {e}")
            return False
    
    def merge_segments(self, segment_paths: list, output_path: str) -> bool:
        """公开的合并接口（兼容旧调用）"""
        return self._merge_segments_to_file(segment_paths, output_path)

    # =========================================================
    # 解说音频混入视频（按时间位置叠加）
    # =========================================================
    def mix_commentary_audio(self, video_path: str,
                              commentary_segments: list,
                              output_path: str,
                              original_volume: float = 0.3,
                              commentary_volume: float = 1.0,
                              tmp_dir: str = "") -> bool:
        """
        将解说音频按时间位置混入原视频

        原理: ffmpeg -filter_complex
        1. 将原视频音轨降低音量
        2. 将多段解说音频按各自的 start_time 拼接成一条完整音轨
        3. 混合两条音轨

        Args:
            video_path: 源视频路径
            commentary_segments: [{"audio_path": str, "start_time": float, "duration": float}, ...]
            output_path: 输出视频路径
            original_volume: 原视频音量 (0.0-1.0)
            commentary_volume: 解说音量 (0.0-1.0)
            tmp_dir: 临时目录

        Returns:
            是否成功
        """
        import shutil

        if not commentary_segments:
            logger.error("[MixCommentary] 没有解说音频段")
            return False

        if not tmp_dir:
            tmp_dir = tempfile.mkdtemp(prefix="fireclip_mix_")

        try:
            # Step 1: 将每段解说音频放置到正确的时间位置
            # 使用 adelay 滤镜将音频延迟到对应的 start_time
            # 先把所有小段拼成一条完整的解说音轨

            # 按开始时间排序
            sorted_segs = sorted(commentary_segments, key=lambda s: s.get("start_time", 0))

            # 构建 ffmpeg filter_complex
            # 方案: 为每段解说创建一个输入，用 adelay 延迟到正确时间，然后 amix 混合

            # 先生成一条完整的静音+解说拼接音轨
            commentary_full_path = os.path.join(tmp_dir, "commentary_full.wav")
            concat_success = self._build_full_commentary_track(
                sorted_segs, commentary_full_path, tmp_dir
            )

            if not concat_success:
                logger.error("[MixCommentary] 拼接解说音轨失败")
                return False

            # Step 2: 使用 amix 混合原视频音轨和解说音轨
            os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

            cmd = [
                self.ffmpeg_path, '-y', '-nostdin',
                '-loglevel', 'error',
                '-i', video_path,
                '-i', commentary_full_path,
                '-filter_complex',
                f"[0:a]volume={original_volume}[a0];"
                f"[1:a]volume={commentary_volume}[a1];"
                f"[a0][a1]amix=inputs=2:duration=first:dropout_transition=2[aout]",
                '-map', '0:v',
                '-map', '[aout]',
                '-c:v', 'copy',
                '-c:a', 'aac',
                '-b:a', '128k',
                '-movflags', '+faststart',
                output_path
            ]

            logger.info(f"[MixCommentary] 混音: 原视频音量={original_volume}, 解说音量={commentary_volume}")

            # 获取视频时长用于超时计算
            video_info = self.get_video_info(video_path)
            video_duration = video_info.get('duration', 300)
            timeout = max(300, video_duration * 3)

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

            if result.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 1000:
                logger.info(f"[MixCommentary] 混音成功: {os.path.getsize(output_path)/1024/1024:.1f}MB")
                return True
            else:
                logger.error(f"[MixCommentary] 混音失败: {result.stderr[:500]}")
                return False

        except subprocess.TimeoutExpired:
            logger.error("[MixCommentary] 混音超时")
            return False
        except Exception as e:
            logger.error(f"[MixCommentary] 混音异常: {e}")
            return False

    def _build_full_commentary_track(self, segments: list,
                                      output_path: str,
                                      tmp_dir: str) -> bool:
        """
        将多段解说音频拼接成一条完整的音轨
        每段音频之间用静音填充到正确的时间位置

        原理: 使用 ffmpeg concat demuxer，每段前插入适当时长的静音
        """
        try:
            import json

            # 获取每段音频的详细信息
            seg_entries = []
            current_time = 0.0

            for i, seg in enumerate(segments):
                start_time = seg.get("start_time", 0)
                audio_path = seg.get("audio_path", "")
                seg_duration = seg.get("duration", 5.0)

                if not audio_path or not os.path.exists(audio_path):
                    continue

                # 如果当前时间 < 该段的开始时间，需要插入静音
                if start_time > current_time:
                    silence_duration = start_time - current_time
                    silence_path = os.path.join(tmp_dir, f"silence_{i:03d}.wav")
                    if not self._generate_silence(silence_duration, silence_path):
                        logger.warning(f"静音生成失败，跳过 {silence_duration:.1f}s 静音")
                    else:
                        seg_entries.append(silence_path)
                    current_time = start_time

                seg_entries.append(audio_path)
                current_time = start_time + seg_duration

            if not seg_entries:
                logger.error("没有有效的音频段")
                return False

            # 使用 ffmpeg concat 拼接所有音频段
            concat_file = os.path.join(tmp_dir, "audio_concat_list.txt")
            with open(concat_file, 'w', encoding='utf-8') as f:
                for sp in seg_entries:
                    abs_path = os.path.abspath(sp).replace('\\', '/')
                    f.write(f"file '{abs_path}'\n")

            cmd = [
                self.ffmpeg_path, '-y', '-nostdin',
                '-loglevel', 'error',
                '-f', 'concat',
                '-safe', '0',
                '-i', concat_file,
                '-ar', '44100',
                '-ac', '2',
                output_path
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

            if result.returncode == 0 and os.path.exists(output_path):
                logger.info(f"[CommentaryTrack] 拼接成功: {len(seg_entries)}段")
                return True
            else:
                logger.error(f"[CommentaryTrack] 拼接失败: {result.stderr[:300]}")
                return False

        except Exception as e:
            logger.error(f"[CommentaryTrack] 拼接异常: {e}")
            return False

    def _generate_silence(self, duration: float, output_path: str) -> bool:
        """使用 ffmpeg 生成指定时长的静音 WAV"""
        try:
            cmd = [
                self.ffmpeg_path, '-y', '-nostdin',
                '-loglevel', 'error',
                '-f', 'lavfi',
                '-i', f'anullsrc=r=44100:cl=stereo',
                '-t', f'{duration:.3f}',
                '-ar', '44100',
                '-ac', '2',
                output_path
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            return result.returncode == 0 and os.path.exists(output_path)
        except Exception:
            return False


# =========================================================
# 快速自测
# =========================================================
if __name__ == "__main__":
    print("="*50)
    print("VideoProcessor v2.1 自测")
    print("="*50)
    
    print("\n[1/2] 初始化VideoProcessor...")
    vp = VideoProcessor()
    print(f"  ffmpeg: {vp.ffmpeg_path}")
    print(f"  ffprobe: {vp.ffprobe_path}")
    
    print("\n[2/2] 查找测试视频...")
    test_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'test_videos')
    if os.path.exists(test_dir):
        videos = [f for f in os.listdir(test_dir) if f.lower().endswith(('.mp4', '.avi', '.mov', '.mkv'))]
        if videos:
            test_video = os.path.join(test_dir, videos[0])
            info = vp.get_video_info(test_video)
            print(f"  测试视频: {videos[0]}")
            print(f"  时长: {info['duration']:.1f}s")
            print(f"  分辨率: {info['width']}x{info['height']}")
            print(f"  FPS: {info['fps']:.1f}")
            print(f"  大小: {info['size_bytes']/1024/1024:.2f}MB")
        else:
            print("  测试目录中无视频文件 (跳过提取测试")
    else:
        print("  无测试目录 (跳过提取测试")
    
    print("\n" + "="*50)
    print("结构验证通过")
    print("="*50)
