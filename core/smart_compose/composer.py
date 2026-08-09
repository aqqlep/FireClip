"""
视频合成模块 v1.1
- ffmpeg片段截取+拼接（音视频同步）
- 转场效果（硬切/淡入淡出/闪白）+ 音频交叉淡化
- 智能变速（音视频同步变速）
- BGM混音+人声闪避
- 导出最终MP4
"""
import os
import time
import random
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from typing import List, Optional, Callable, Tuple
from pathlib import Path

from utils.logger import logger
from core.smart_compose.clip_selector import SelectedClip
from core.smart_compose.templates import ComposeTemplate


@dataclass
class ComposeOutput:
    """合成输出结果"""
    output_path: str
    total_duration: float
    clip_count: int
    success: bool = False
    error_message: str = ""
    temp_files: List[str] = None
    
    def __post_init__(self):
        if self.temp_files is None:
            self.temp_files = []


class VideoComposer:
    """视频合成器"""
    
    def __init__(self, ffmpeg_path: str = "ffmpeg", ffprobe_path: str = "ffprobe"):
        self.ffmpeg_path = ffmpeg_path
        self.ffprobe_path = ffprobe_path
    
    def compose(
        self,
        video_path: str,
        clips: List[SelectedClip],
        template: ComposeTemplate,
        output_path: str,
        bgm_path: Optional[str] = None,
        keep_original_audio: bool = True,
        commentary_tracks: Optional[List[Tuple[float, str]]] = None,
        subtitle_path: Optional[str] = None,
        callback: Optional[Callable[[int, str], None]] = None,
    ) -> ComposeOutput:
        """
        合成最终视频
        
        Args:
            video_path: 原始视频路径
            clips: 选中的片段列表
            template: 剪辑模板
            output_path: 输出文件路径
            bgm_path: BGM文件路径（None则不添加BGM）
            keep_original_audio: 是否保留原声音频
            commentary_tracks: 解说音频列表 [(延迟秒数, 音频文件路径), ...]
            subtitle_path: SRT字幕文件路径（None则不烧录字幕）
            callback: 进度回调
        """
        compose_start = time.time()
        logger.info(f"[Composer] ===== 开始视频合成 =====")
        logger.info(f"[Composer] 原始视频: {video_path}")
        logger.info(f"[Composer] 片段数量: {len(clips)}")
        logger.info(f"[Composer] 模板: {template.name} ({template.category}), 保留原声: {keep_original_audio}, BGM: {bgm_path}")
        logger.info(f"[Composer] 解说段数: {len(commentary_tracks) if commentary_tracks else 0}, 字幕: {subtitle_path}")
        logger.info(f"[Composer] 输出路径: {output_path}")
        
        if not clips:
            error_msg = "没有选中的片段"
            logger.error(f"[Composer] {error_msg}")
            return ComposeOutput(output_path, 0.0, 0, False, error_msg)
        
        temp_dir = tempfile.mkdtemp(prefix="compose_")
        logger.debug(f"[Composer] 创建临时目录: {temp_dir}")
        output = ComposeOutput(output_path=output_path, total_duration=0.0, clip_count=len(clips))
        output.temp_files.append(temp_dir)
        
        try:
            valid_video = video_path
            need_cleanup_valid_video = False
            
            if not os.path.exists(video_path):
                raise RuntimeError(f"视频文件不存在: {video_path}")
            
            file_size = os.path.getsize(video_path)
            if file_size < 1024 * 100:
                logger.warning(f"[Composer] 源视频文件过小({file_size/1024:.1f}KB)，可能损坏，尝试转码修复...")
                need_transcode = True
            else:
                need_transcode = False
                try:
                    logger.debug(f"[Composer] 校验源视频有效性...")
                    probe_cmd = [
                        self.ffprobe_path, "-v", "error",
                        "-select_streams", "v:0",
                        "-show_entries", "stream=codec_name,width,height,duration",
                        "-of", "default=noprint_wrappers=1:nokey=1",
                        video_path
                    ]
                    result = subprocess.run(
                        probe_cmd,
                        capture_output=True,
                        text=True,
                        errors="ignore",
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                        timeout=15
                    )
                    if result.returncode != 0 or not result.stdout.strip():
                        logger.warning(f"[Composer] ffprobe校验失败，尝试转码修复... stderr: {result.stderr[-300:] if result.stderr else '无'}")
                        need_transcode = True
                    else:
                        logger.debug(f"[Composer] 源视频校验通过，视频信息: {result.stdout.strip()}")
                except subprocess.TimeoutExpired:
                    logger.warning(f"[Composer] ffprobe超时，尝试直接转码...")
                    need_transcode = True
                except Exception as e:
                    logger.warning(f"[Composer] ffprobe校验异常: {str(e)[:200]}，尝试直接转码...")
                    need_transcode = True
            
            if need_transcode:
                logger.info(f"[Composer] 开始转码修复源视频...")
                valid_video = os.path.join(temp_dir, "source_fixed.mp4")
                transcode_cmd = [
                    self.ffmpeg_path, "-y",
                    "-fflags", "+genpts+discardcorrupt",
                    "-err_detect", "ignore_err",
                    "-i", video_path,
                    "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                    "-pix_fmt", "yuv420p",
                    "-profile:v", "high", "-level", "4.1",
                    "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
                    "-movflags", "+faststart",
                    "-vsync", "cfr",
                    valid_video
                ]
                try:
                    transcode_result = subprocess.run(
                        transcode_cmd,
                        capture_output=True,
                        text=True,
                        errors="ignore",
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                        timeout=600
                    )
                    if transcode_result.returncode != 0 or not os.path.exists(valid_video) or os.path.getsize(valid_video) < 1024*100:
                        logger.error(f"[Composer] 源视频转码失败: {transcode_result.stderr[-500:]}")
                        raise RuntimeError(f"源视频无法读取且转码失败")
                    logger.info(f"[Composer] 源视频转码修复成功: {valid_video}")
                    need_cleanup_valid_video = True
                except subprocess.TimeoutExpired:
                    logger.error(f"[Composer] 源视频转码超时")
                    raise RuntimeError("源视频转码超时，文件可能已损坏")
                except Exception as e:
                    logger.error(f"[Composer] 源视频转码异常: {e}")
                    raise RuntimeError(f"源视频无效: {str(e)}")
            
            source_duration = None
            try:
                dur_probe_cmd = [
                    self.ffprobe_path, "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    valid_video
                ]
                dur_result = subprocess.run(
                    dur_probe_cmd,
                    capture_output=True,
                    text=True,
                    errors="ignore",
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                    timeout=15
                )
                if dur_result.returncode == 0 and dur_result.stdout.strip():
                    source_duration = float(dur_result.stdout.strip())
                    logger.info(f"[Composer] 获取源视频总时长: {source_duration:.2f}s")
            except Exception as e:
                logger.warning(f"[Composer] 获取源视频时长失败，跳过边界检查: {e}")
            
            if source_duration and source_duration > 1:
                valid_clips = []
                for clip in clips:
                    start = max(0.0, min(clip.start_time, source_duration - 0.5))
                    end = max(start + 0.5, min(clip.end_time, source_duration))
                    if end - start >= 0.3:
                        clip.start_time = start
                        clip.end_time = end
                        valid_clips.append(clip)
                    else:
                        logger.warning(f"[Composer] 片段超出视频范围，跳过: {clip.start_time:.1f}-{clip.end_time:.1f}s")
                clips = valid_clips
                if not clips:
                    logger.warning(f"[Composer] 所有片段都超出视频范围，自动生成均匀分片")
                    from .clip_selector import SelectedClip
                    num_clips = min(10, max(3, int(source_duration / 3)))
                    step = source_duration / num_clips
                    clips = []
                    for j in range(num_clips):
                        s = j * step
                        e = min(s + 2.5, source_duration)
                        if e - s > 0.5:
                            clips.append(SelectedClip(start_time=s, end_time=e, score=0.5, clip_type="fallback"))
            
            clip_files = []
            clip_play_durations = []
            actual_output_durations = []
            output.success = True
            
            transition_type = template.transition_type
            transition_dur = template.transition_duration if transition_type != "hard" else 0.0
            need_overlap = transition_dur > 0
            overlap = transition_dur if need_overlap else 0.0
            
            cut_start = time.time()
            logger.info(f"[Composer] 开始截取 {len(clips)} 个片段... 转场={transition_type}, 转场时长={transition_dur}s")
            
            success_clips = []
            
            for i, clip in enumerate(clips):
                if callback:
                    progress = 10 + int((i / len(clips)) * 40)
                    callback(progress, f"截取片段 {i+1}/{len(clips)} ({clip.start_time:.1f}s - {clip.end_time:.1f}s)")
                
                speed = 1.0
                
                extra_head = 0.0
                extra_tail = 0.0
                overlap = 0.0
                
                src_cut_dur = clip.duration
                
                logger.debug(f"[Composer] 截取片段 {i+1}: {clip.start_time:.3f}s - {clip.end_time:.3f}s, 时长={clip.duration:.3f}s")
                clip_file = os.path.join(temp_dir, f"clip_{i:04d}.mp4")
                
                cut_ok = False
                try:
                    self._cut_clip(
                        valid_video, clip.start_time, clip.end_time, clip_file, template,
                        speed=1.0, extra_head=extra_head, extra_tail=extra_tail, keep_audio=True
                    )
                    if os.path.exists(clip_file) and os.path.getsize(clip_file) > 2048:
                        cut_ok = True
                except Exception as e:
                    logger.warning(f"[Composer] 截取片段{i+1}失败: {e}")
                    if os.path.exists(clip_file):
                        try:
                            os.unlink(clip_file)
                        except:
                            pass
                
                if cut_ok:
                    file_play_dur = src_cut_dur
                    actual_output_durations.append(file_play_dur)
                    clip_play_durations.append(clip.duration)
                    file_size = os.path.getsize(clip_file) / (1024*1024)
                    logger.debug(f"[Composer] 截取完成: {clip_file}, 大小={file_size:.2f}MB, 播放时长={file_play_dur:.3f}s")
                    clip_files.append(clip_file)
                    success_clips.append(clip)
                else:
                    logger.error(f"[Composer] 片段{i+1}截取失败，跳过")
            
            if not clip_files:
                logger.warning(f"[Composer] 所有选中片段截取失败，启动保底机制：自动生成均匀分片...")
                
                from .clip_selector import SelectedClip
                
                video_duration = None
                try:
                    probe_cmd = [
                        self.ffprobe_path, "-v", "error",
                        "-show_entries", "format=duration",
                        "-of", "default=noprint_wrappers=1:nokey=1",
                        valid_video
                    ]
                    probe_result = subprocess.run(
                        probe_cmd,
                        capture_output=True,
                        text=True,
                        errors="ignore",
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                        timeout=10
                    )
                    if probe_result.returncode == 0 and probe_result.stdout.strip():
                        video_duration = float(probe_result.stdout.strip())
                        logger.info(f"[Composer] 保底机制：获取视频总时长={video_duration:.2f}s")
                except Exception as e:
                    logger.warning(f"[Composer] 获取视频时长失败: {e}")
                
                if video_duration and video_duration > 5:
                    clip_duration = 2.0
                    num_clips = min(15, max(5, int(video_duration / clip_duration)))
                    step = video_duration / num_clips
                    
                    logger.info(f"[Composer] 保底机制：均匀切分 {num_clips} 个片段，每个≈{clip_duration}s")
                    
                    fallback_clips = []
                    for j in range(num_clips):
                        start = j * step
                        end = min(start + clip_duration, video_duration)
                        if end - start < 0.5:
                            continue
                        fallback_clips.append(SelectedClip(
                            start_time=start,
                            end_time=end,
                            score=0.5,
                            clip_type="fallback",
                        ))
                    
                    for j, fb_clip in enumerate(fallback_clips):
                        if callback:
                            progress = 50 + int((j / len(fallback_clips)) * 10)
                            callback(progress, f"保底截取片段 {j+1}/{len(fallback_clips)}...")
                        
                        fb_clip_file = os.path.join(temp_dir, f"fallback_clip_{j:04d}.mp4")
                        fb_cut_ok = False
                        try:
                            self._cut_clip(
                                valid_video, fb_clip.start_time, fb_clip.end_time, fb_clip_file, template,
                                speed=1.0, extra_head=0, extra_tail=0, keep_audio=True
                            )
                            if os.path.exists(fb_clip_file) and os.path.getsize(fb_clip_file) > 2048:
                                fb_cut_ok = True
                        except Exception as e:
                            logger.warning(f"[Composer] 保底片段{j+1}截取失败: {str(e)[:200]}")
                        
                        if fb_cut_ok:
                            file_play_dur = fb_clip.duration
                            actual_output_durations.append(file_play_dur)
                            clip_play_durations.append(fb_clip.duration)
                            file_size = os.path.getsize(fb_clip_file) / (1024*1024)
                            logger.debug(f"[Composer] 保底截取完成: {fb_clip_file}, 大小={file_size:.2f}MB, 时长={fb_clip.duration:.3f}s")
                            clip_files.append(fb_clip_file)
                            success_clips.append(fb_clip)
            
            if not clip_files:
                logger.warning(f"[Composer] 所有分片截取失败，启动终极保底：直接转码整个视频...")
                try:
                    direct_transcode_cmd = [
                        self.ffmpeg_path, "-y",
                        "-fflags", "+genpts+discardcorrupt",
                        "-err_detect", "ignore_err",
                        "-i", valid_video,
                        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                        "-pix_fmt", "yuv420p",
                        "-profile:v", "high", "-level", "4.1",
                        "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
                        "-movflags", "+faststart",
                        output_path
                    ]
                    subprocess.run(
                        direct_transcode_cmd,
                        check=True,
                        capture_output=True,
                        text=True,
                        errors="ignore",
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                        timeout=600
                    )
                    if os.path.exists(output_path) and os.path.getsize(output_path) > 1024*100:
                        logger.info(f"[Composer] 终极保底转码成功，直接输出完整视频")
                        output.success = True
                        output.output_path = output_path
                        try:
                            probe_dur_cmd = [
                                self.ffprobe_path, "-v", "error",
                                "-show_entries", "format=duration",
                                "-of", "default=noprint_wrappers=1:nokey=1",
                                output_path
                            ]
                            dur_result = subprocess.run(
                                probe_dur_cmd,
                                capture_output=True,
                                text=True,
                                errors="ignore",
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                                timeout=10
                            )
                            if dur_result.returncode == 0 and dur_result.stdout.strip():
                                output.total_duration = float(dur_result.stdout.strip())
                                output.clip_count = 1
                        except:
                            output.total_duration = 0
                            output.clip_count = 1
                        self.cleanup(output)
                        return output
                except Exception as e:
                    logger.error(f"[Composer] 终极保底转码也失败: {e}")
            
            if not clip_files:
                error_msg = "所有片段截取失败，视频可能已损坏"
                logger.error(f"[Composer] {error_msg}")
                output.success = False
                output.error_message = error_msg
                self.cleanup(output)
                return output
            
            clips = success_clips
            
            if transition_type not in ("hard", "cut", ""):
                total_dur = sum(actual_output_durations) - transition_dur * max(0, len(clips) - 1)
            else:
                total_dur = sum(clip_play_durations)
            output.total_duration = total_dur
            output.clip_count = len(clips)
            logger.info(f"[Composer] 所有片段截取完成, 转场后总时长≈{total_dur:.2f}s, 耗时={time.time()-cut_start:.2f}s")
            
            if callback:
                callback(55, "拼接片段中...")
            
            video_only = os.path.join(temp_dir, "video_only.mp4")
            concat_start = time.time()
            logger.info(f"[Composer] 开始视频拼接 (转场类型: {transition_type})...")
            
            concat_success = False
            if transition_type not in ("hard", "cut", ""):
                try:
                    self._concat_with_transitions(clip_files, actual_output_durations, video_only, transition_type, transition_dur)
                    concat_success = True
                except Exception as e:
                    logger.warning(f"[Composer] 转场拼接失败，降级为硬切: {e}")
                    transition_type = "hard"
            
            if not concat_success:
                concat_file = os.path.join(temp_dir, "concat.txt")
                self._write_concat_file(clip_files, concat_file)
                logger.debug(f"[Composer] 写入拼接列表文件(硬切): {concat_file}, 包含 {len(clip_files)} 个片段")
                self._concat_clips(concat_file, video_only, with_audio=True)
            
            concat_size = os.path.getsize(video_only) / (1024*1024) if os.path.exists(video_only) else 0
            logger.info(f"[Composer] 视频拼接完成: {video_only}, 大小={concat_size:.2f}MB, 耗时={time.time()-concat_start:.2f}s")
            
            if callback:
                callback(70, "处理音频与混音...")
            
            mix_start = time.time()
            logger.info(f"[Composer] 开始音频处理与混音...")
            
            mix_ok = False
            try:
                self._mix_audio(
                    video_only,
                    video_path,
                    clips,
                    template,
                    bgm_path,
                    keep_original_audio,
                    output_path,
                    temp_dir,
                    callback,
                    commentary_tracks,
                    subtitle_path,
                )
                mix_ok = os.path.exists(output_path) and os.path.getsize(output_path) > 1024
            except Exception as e:
                logger.warning(f"[Composer] 复杂混音失败: {e}")
            
            if not mix_ok:
                logger.info(f"[Composer] 降级为基础音频模式（仅复制原视频音轨）")
                if os.path.exists(output_path):
                    try:
                        os.unlink(output_path)
                    except:
                        pass
                try:
                    if subtitle_path and os.path.exists(subtitle_path):
                        sub_escaped = subtitle_path.replace('\\', '/').replace(':', '\\:')
                        vf = f"subtitles='{sub_escaped}'"
                        cmd = [self.ffmpeg_path, "-y", "-i", video_only,
                               "-vf", vf,
                               "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                               "-pix_fmt", "yuv420p",
                               "-profile:v", "high", "-level", "4.1",
                               "-c:a", "copy",
                               "-movflags", "+faststart",
                               output_path]
                        subprocess.run(cmd, check=True, capture_output=True,
                                       text=True, errors="ignore",
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                        mix_ok = os.path.exists(output_path) and os.path.getsize(output_path) > 1024
                    else:
                        shutil.copy2(video_only, output_path)
                        mix_ok = os.path.exists(output_path) and os.path.getsize(output_path) > 1024
                except Exception as e2:
                    logger.error(f"[Composer] 基础模式也失败: {e2}")
                    try:
                        shutil.copy2(video_only, output_path)
                        mix_ok = os.path.exists(output_path) and os.path.getsize(output_path) > 1024
                    except Exception as e3:
                        logger.error(f"[Composer] 复制视频也失败: {e3}")
            
            if os.path.exists(output_path):
                final_size = os.path.getsize(output_path) / (1024*1024)
                logger.info(f"[Composer] 混音完成: {output_path}, 大小={final_size:.2f}MB, 耗时={time.time()-mix_start:.2f}s")
            else:
                logger.error(f"[Composer] 混音失败: 输出文件不存在 {output_path}")
            
            if callback:
                callback(95, "合成完成，清理临时文件...")
            
            output.success = os.path.exists(output_path) and os.path.getsize(output_path) > 1024
            if output.success:
                logger.info(f"[Composer] ===== 视频合成成功! 总耗时={time.time()-compose_start:.2f}s =====")
            else:
                output.error_message = "输出文件生成失败"
                logger.error(f"[Composer] ===== 视频合成失败: 输出文件无效 =====")
            
            self.cleanup(output)
            return output
            
        except subprocess.CalledProcessError as e:
            error_msg = f"FFmpeg执行错误 (返回码={e.returncode})"
            logger.error(f"[Composer] {error_msg}")
            logger.error(f"[Composer] 命令: {' '.join(e.cmd)}")
            if e.stderr:
                stderr_text = e.stderr if isinstance(e.stderr, str) else e.stderr.decode('utf-8', errors='ignore')
                logger.error(f"[Composer] FFmpeg stderr:\n{stderr_text[:2000]}")
                error_msg += f": {stderr_text[-300:]}"
            output.success = False
            output.error_message = error_msg
            self.cleanup(output)
            return output
        except Exception as e:
            error_msg = str(e)
            logger.error(f"[Composer] 合成异常: {error_msg}", exc_info=True)
            output.success = False
            output.error_message = error_msg
            self.cleanup(output)
            return output
    
    def _cut_clip(
        self,
        video_path: str,
        start: float,
        end: float,
        output: str,
        template: Optional[ComposeTemplate] = None,
        speed: float = 1.0,
        extra_head: float = 0.0,
        extra_tail: float = 0.0,
        keep_audio: bool = True,
    ):
        """
        可靠的片段截取：多级重试机制保证成功率
        1. 快速seek(-ss在-i前) + 保留原音 (速度快)
        2. 精确seek(-ss在-i后) + 保留原音 (兼容性好，解决WinError 87)
        3. 快速seek + 静音音轨兜底
        4. 精确seek + 静音音轨兜底
        """
        actual_start = max(0.0, start - extra_head)
        actual_end = end + extra_tail
        duration = actual_end - actual_start
        
        if duration < 0.2:
            duration = 0.2
            actual_end = actual_start + duration
        
        cut_ok = False
        
        cut_modes = []
        if keep_audio:
            cut_modes.append(("fast_seek_audio", True, True))
            cut_modes.append(("accurate_seek_audio", True, False))
        cut_modes.append(("fast_seek_silent", False, True))
        cut_modes.append(("accurate_seek_silent", False, False))
        
        for mode_idx, (mode_name, use_audio, fast_seek) in enumerate(cut_modes):
            try:
                cmd = [self.ffmpeg_path, "-y"]
                
                if fast_seek:
                    cmd.extend(["-ss", f"{actual_start:.3f}"])
                    cmd.extend(["-fflags", "+genpts+discardcorrupt"])
                    cmd.extend(["-err_detect", "ignore_err"])
                    cmd.extend(["-i", video_path])
                    cmd.extend(["-t", f"{duration:.3f}"])
                else:
                    cmd.extend(["-fflags", "+genpts+discardcorrupt"])
                    cmd.extend(["-err_detect", "ignore_err"])
                    cmd.extend(["-i", video_path])
                    cmd.extend(["-ss", f"{actual_start:.3f}"])
                    cmd.extend(["-t", f"{duration:.3f}"])
                
                if use_audio:
                    cmd.extend([
                        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                        "-pix_fmt", "yuv420p",
                        "-profile:v", "high", "-level", "4.1",
                        "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
                        "-avoid_negative_ts", "make_zero",
                        "-movflags", "+faststart",
                    ])
                    logger.debug(f"[FFmpeg] 截取片段({mode_name}) 尝试{mode_idx+1}")
                else:
                    cmd.extend([
                        "-f", "lavfi", "-t", f"{duration:.3f}",
                        "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                        "-map", "0:v:0", "-map", "1:a:0",
                        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                        "-pix_fmt", "yuv420p",
                        "-profile:v", "high", "-level", "4.1",
                        "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
                        "-avoid_negative_ts", "make_zero", "-shortest",
                        "-movflags", "+faststart",
                    ])
                    logger.debug(f"[FFmpeg] 截取片段({mode_name}) 尝试{mode_idx+1}")
                
                cmd.append(output)
                
                result = subprocess.run(
                    cmd,
                    check=True,
                    capture_output=True,
                    text=True,
                    errors="ignore",
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                    timeout=120
                )
                
                if os.path.exists(output) and os.path.getsize(output) > 2048:
                    cut_ok = True
                    logger.debug(f"[FFmpeg] 截取成功，模式={mode_name}")
                    break
            except subprocess.CalledProcessError as e:
                err_msg = e.stderr[-500:] if e.stderr else str(e)
                logger.warning(f"[FFmpeg] 截取模式{mode_name}失败(返回码={e.returncode}): {err_msg}")
                if os.path.exists(output):
                    try:
                        os.unlink(output)
                    except:
                        pass
                continue
            except Exception as e:
                logger.warning(f"[FFmpeg] 截取模式{mode_name}异常: {str(e)[:200]}")
                if os.path.exists(output):
                    try:
                        os.unlink(output)
                    except:
                        pass
                continue
        
        if not cut_ok:
            raise RuntimeError(f"所有截取方式都失败")
    
    def _concat_with_transitions(
        self,
        clip_files: List[str],
        clip_durations: List[float],
        output: str,
        transition_type: str = "hard",
        transition_duration: float = 0.3,
    ) -> str:
        """使用xfade+acrossfade滤镜拼接带转场效果的音视频
        transition_type: hard/cut(硬切), fade(淡入淡出黑场), fadewhite(闪白)
        """
        n_clips = len(clip_files)
        if n_clips == 0:
            return output
        if n_clips == 1 or transition_type in ("hard", "cut", ""):
            concat_file = os.path.join(os.path.dirname(output), "concat_trans.txt")
            self._write_concat_file(clip_files, concat_file)
            return self._concat_clips(concat_file, output, with_audio=True)
        
        logger.info(f"[Composer] 使用转场拼接: type={transition_type}, duration={transition_duration:.3f}s, clips={n_clips}")
        
        xfade_transition = "fade" if transition_type == "fade" else "fadewhite" if transition_type == "fadewhite" else "fade"
        crossfade_dur = min(transition_duration, 0.3)
        
        cmd = [self.ffmpeg_path, "-y"]
        for cf in clip_files:
            cmd.extend(["-i", cf])
        
        filter_parts = []
        
        for i in range(n_clips):
            filter_parts.append(f"[{i}:v]settb=AVTB,fps=30000/1001[v{i}]")
            filter_parts.append(f"[{i}:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}]")
        
        v_current = "[v0]"
        a_current = "[a0]"
        v_cumulative_offset = clip_durations[0]
        a_cumulative_offset = clip_durations[0]
        
        for i in range(1, n_clips):
            v_offset = v_cumulative_offset - transition_duration
            a_offset = a_cumulative_offset - crossfade_dur
            
            v_next = f"[vt{i}]"
            a_next = f"[at{i}]"
            
            filter_parts.append(
                f"{v_current}[v{i}]xfade=transition={xfade_transition}:duration={transition_duration:.3f}:offset={v_offset:.3f}{v_next}"
            )
            filter_parts.append(
                f"{a_current}[a{i}]acrossfade=d={crossfade_dur:.3f}:c1=tri:c2=tri{a_next}"
            )
            
            v_current = v_next
            a_current = a_next
            v_cumulative_offset += clip_durations[i] - transition_duration
            a_cumulative_offset += clip_durations[i] - crossfade_dur
        
        filter_parts.append(f"{v_current}fps=30000/1001[vout]")
        
        filter_complex = ";".join(filter_parts)
        cmd.extend([
            "-filter_complex", filter_complex,
            "-map", "[vout]",
            "-map", a_current,
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "aac", "-b:a", "160k",
            output
        ])
        
        logger.debug(f"[FFmpeg] 转场拼接命令(含音频)")
        
        result = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
            errors="ignore",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
        )
        
        if result.stderr:
            stderr_lower = result.stderr.lower()
            if "error" in stderr_lower:
                logger.error(f"[FFmpeg] 转场拼接错误: {result.stderr[-1500:]}")
            elif "warning" in stderr_lower:
                logger.debug(f"[FFmpeg] 转场拼接警告: {result.stderr[-500:]}")
        
        total_dur_after = cumulative_offset
        logger.info(f"[Composer] 转场拼接完成: {output}, 转场后总时长≈{total_dur_after:.2f}s")
        return output
    
    def _write_concat_file(self, clip_files: List[str], concat_file: str):
        """写ffmpeg concat拼接文件，正确处理Windows路径特殊字符"""
        with open(concat_file, 'w', encoding='utf-8') as f:
            for cf in clip_files:
                cf_abs = os.path.abspath(cf).replace('\\', '/').replace("'", "'\\''")
                f.write(f"file '{cf_abs}'\n")
    
    def _concat_clips(self, concat_file: str, output: str, with_audio: bool = True) -> str:
        """拼接视频片段：优先尝试流复制零损失，失败再回退到高质量重编码"""
        # 先尝试流复制（所有片段编码参数一致时可用，完全无质量损失）
        logger.debug(f"[FFmpeg] 尝试流复制拼接(零质量损失)...")
        cmd_copy = [
            self.ffmpeg_path, "-y",
            "-f", "concat", "-safe", "0",
            "-fflags", "+genpts+discardcorrupt",
            "-err_detect", "ignore_err",
            "-i", concat_file,
            "-c", "copy",
            "-movflags", "+faststart",
            output
        ]
        if not with_audio:
            cmd_copy.insert(-1, "-an")
        
        try:
            logger.debug(f"[FFmpeg] 流复制拼接命令: {' '.join(cmd_copy)}")
            result = subprocess.run(
                cmd_copy,
                check=True,
                capture_output=True,
                text=True,
                errors="ignore",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                timeout=300
            )
            if os.path.exists(output) and os.path.getsize(output) > 1024*100:
                logger.info(f"[Composer] 流复制拼接成功，零质量损失")
                return output
        except Exception as e:
            logger.debug(f"[FFmpeg] 流复制拼接失败，回退到高质量重编码: {str(e)[:200]}")
            if os.path.exists(output):
                try:
                    os.unlink(output)
                except:
                    pass
        
        # 流复制失败，使用高质量重编码
        cmd = [
            self.ffmpeg_path, "-y",
            "-f", "concat", "-safe", "0",
            "-fflags", "+genpts+discardcorrupt",
            "-err_detect", "ignore_err",
            "-i", concat_file,
            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-pix_fmt", "yuv420p",
            "-profile:v", "high", "-level", "4.1",
            "-avoid_negative_ts", "make_zero",
            "-movflags", "+faststart",
        ]
        if with_audio:
            cmd.extend(["-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2"])
        else:
            cmd.append("-an")
        cmd.append(output)
        
        logger.debug(f"[FFmpeg] 重编码拼接命令: {' '.join(cmd)}")
        
        result = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
            errors="ignore",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            timeout=600
        )
        
        if result.stderr:
            stderr_lower = result.stderr.lower()
            if "error" in stderr_lower:
                logger.error(f"[FFmpeg] 拼接错误stderr:\n{result.stderr[-1500:]}")
            elif "warning" in stderr_lower:
                logger.debug(f"[FFmpeg] 拼接警告: {result.stderr[-500:]}")
        
        return output
    
    def _mix_audio(
        self,
        video_path: str,
        original_video: str,
        clips: List[SelectedClip],
        template: ComposeTemplate,
        bgm_path: Optional[str],
        keep_original_audio: bool,
        output_path: str,
        temp_dir: str,
        callback: Optional[Callable[[int, str], None]],
        commentary_tracks: Optional[List[Tuple[float, str]]] = None,
        subtitle_path: Optional[str] = None,
    ) -> str:
        """最终混音：输入视频已包含同步好的原声
        - 原声已经同步变速+转场交叉淡化
        - 叠加BGM（可循环）
        - 叠加多段解说（带延迟）
        - 智能闪避（解说时降低BGM和原声）
        - 可选烧录字幕
        """
        logger.info(f"[MixAudio] ===== 开始最终混音 =====")
        logger.info(f"[MixAudio] 输入视频(已含同步原声): {video_path}")
        logger.info(f"[MixAudio] 保留原声: {keep_original_audio}, BGM: {bgm_path}")
        logger.info(f"[MixAudio] 解说段数: {len(commentary_tracks) if commentary_tracks else 0}")
        
        cmd = [self.ffmpeg_path, "-y", "-i", video_path]
        input_idx = 1
        bgm_idx = -1
        voice_inputs = []
        
        orig_volume = template.original_audio_volume if (template.enable_tts and commentary_tracks) else 1.0
        if not keep_original_audio:
            orig_volume = 0.0
        
        if bgm_path and os.path.exists(bgm_path):
            cmd.extend(["-i", bgm_path])
            bgm_idx = input_idx
            input_idx += 1
            logger.debug(f"[MixAudio] BGM输入索引: {bgm_idx}, 音量: {template.bgm_volume:.2f}")
        
        valid_commentary = []
        if commentary_tracks:
            for delay, track_path in commentary_tracks:
                if os.path.exists(track_path):
                    cmd.extend(["-i", track_path])
                    valid_commentary.append((delay, input_idx))
                    input_idx += 1
            logger.info(f"[MixAudio] 添加 {len(valid_commentary)} 段有效解说")
        
        filter_parts = []
        
        filter_parts.append(f"[0:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo,volume={orig_volume:.2f}[a_base]")
        audio_inputs = ["[a_base]"]
        audio_input_count = 1
        
        if bgm_idx >= 0:
            filter_parts.append(
                f"[{bgm_idx}:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                f"volume={template.bgm_volume:.2f},aloop=loop=-1:size=2e9,asetpts=N/SR/TB[a_bgm]"
            )
            audio_inputs.append("[a_bgm]")
            audio_input_count += 1
        
        if valid_commentary:
            voice_labels = []
            for i, (delay, in_idx) in enumerate(valid_commentary):
                delay_ms = max(0, int(delay * 1000))
                filter_parts.append(
                    f"[{in_idx}:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                    f"adelay={delay_ms}|{delay_ms},apad=whole_dur=200[av{i}]"
                )
                voice_labels.append(f"[av{i}]")
            
            if len(voice_labels) == 1:
                filter_parts.append(f"{voice_labels[0]}anull[a_voice]")
            else:
                labels_str = "".join(voice_labels)
                filter_parts.append(f"{labels_str}amix=inputs={len(voice_labels)}:duration=longest:dropout_transition=0:normalize=0[a_voice]")
            
            if template.audio_ducking and bgm_idx >= 0:
                ratio = max(2.0, template.ducking_db / 6.0)
                filter_parts.append(
                    f"[a_voice][a_bgm]sidechaincompress=threshold=0.05:ratio={ratio:.1f}:attack=10:release=200[a_bgm_ducked]"
                )
                audio_inputs[-1] = "[a_bgm_ducked]"
            
            audio_inputs.append("[a_voice]")
            audio_input_count += 1
        
        labels_str = "".join(audio_inputs)
        filter_parts.append(
            f"{labels_str}amix=inputs={audio_input_count}:duration=first:dropout_transition=0:normalize=0[aout]"
        )
        
        video_filter_parts = []
        if subtitle_path and os.path.exists(subtitle_path):
            logger.info(f"[MixAudio] 添加字幕烧录: {subtitle_path}")
            sub_path_escaped = subtitle_path.replace('\\', '/').replace(':', '\\:')
            video_filter_parts.append(f"subtitles='{sub_path_escaped}':force_style='FontSize=22,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=1,MarginV=40'")
        
        if video_filter_parts:
            vf = ",".join(video_filter_parts)
            filter_parts.append(f"[0:v]{vf}[vout]")
            cmd.extend(["-map", "[vout]"])
            cmd.extend([
                "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                "-pix_fmt", "yuv420p",
                "-profile:v", "high", "-level", "4.1",
                "-movflags", "+faststart",
            ])
        else:
            cmd.extend(["-map", "0:v"])
            cmd.extend(["-c:v", "copy"])
        
        cmd.extend(["-map", "[aout]", "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2"])
        
        filter_complex = ";".join(filter_parts)
        cmd.extend(["-filter_complex", filter_complex])
        
        cmd.extend(["-shortest", output_path])
        
        logger.debug(f"[FFmpeg] 混音命令:\n{' '.join(cmd)}")
        
        if callback:
            callback(85, "音频混音中...")
        
        result = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
            errors="ignore",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
        )
        
        if result.stderr:
            stderr_lower = result.stderr.lower()
            if "error" in stderr_lower:
                logger.error(f"[FFmpeg] 混音错误stderr:\n{result.stderr[:3000]}")
            elif "warning" in stderr_lower:
                logger.debug(f"[FFmpeg] 混音警告: {result.stderr[-800:]}")
            else:
                logger.debug(f"[FFmpeg] 混音完成, 最后输出: {result.stderr[-200:]}")
        
        logger.info(f"[MixAudio] 音频处理完成: {output_path}")
        return output_path
    
    def cleanup(self, output: ComposeOutput):
        """清理临时文件"""
        logger.info(f"[Composer] 清理临时文件, 共 {len(output.temp_files)} 个项")
        for f in output.temp_files:
            try:
                if os.path.isdir(f):
                    logger.debug(f"[Composer] 删除临时目录: {f}")
                    shutil.rmtree(f, ignore_errors=True)
                elif os.path.exists(f):
                    logger.debug(f"[Composer] 删除临时文件: {f}")
                    os.unlink(f)
            except Exception as e:
                logger.warning(f"[Composer] 清理临时文件失败 {f}: {e}")
