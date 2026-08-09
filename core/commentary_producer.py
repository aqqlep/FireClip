"""
解说成片制作管线 — 完整编排: 文案生成 → TTS语音 → ffmpeg混音 → 输出视频
独立于 AnalysisController，仅消费片段数据
"""
import os
import tempfile
import shutil
from pathlib import Path
from typing import List, Dict, Optional, Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

from core.commentary_text_gen import CommentaryTextGenerator, STYLE_NAMES
from core.tts_engine import TTSEngine
from core.video_processor import VideoProcessor
from core.subtitle import SubtitleProcessor
from utils.logger import logger
from config import CONFIG


class CommentaryProducer:
    """解说成片制作管线 — 一键从片段数据到带解说配音的视频"""

    def __init__(self):
        self.text_gen = CommentaryTextGenerator()
        self.tts = TTSEngine()
        self.video_proc = VideoProcessor()
        self.subtitle_proc = SubtitleProcessor()
        self._cancel = False  # 取消标志

    def cancel(self):
        """取消当前制作流程"""
        self._cancel = True
        logger.info("收到取消请求，正在停止解说视频制作...")

    # ================================================================
    # 主入口: 一键生成解说视频
    # ================================================================
    def produce(self, video_path: str,
                segments: List[Dict],
                output_path: str,
                style: str = "\u4e13\u4e1a\u89e3\u8bf4",
                mode: str = "template",
                voice: str = "male",
                speed: float = 1.0,
                original_volume: float = 0.3,
                commentary_volume: float = 1.0,
                burn_subtitles: bool = True,
                bgm_path: str = "",
                bgm_volume: float = 0.2,
                transition: str = "\u65e0",
                output_format: str = "MP4 (H.264)",
                progress_callback: Optional[Callable] = None) -> Optional[str]:
        """
        \u5b8c\u6574\u89e3\u8bf4\u89c6\u9891\u5236\u4f5c\u6d41\u7a0b
    
        Args:
            video_path: \u6e90\u89c6\u9891\u8def\u5f84
            segments: \u7247\u6bb5\u5217\u8868
            output_path: \u8f93\u51fa\u89c6\u9891\u8def\u5f84
            style: \u89e3\u8bf4\u98ce\u683c
            mode: \u751f\u6210\u6a21\u5f0f ("template"/"ai")
            voice: TTS\u58f0\u97f3
            speed: TTS\u8bed\u901f (0.5-2.0)
            original_volume: \u539f\u89c6\u9891\u97f3\u91cf (0.0-1.0)
            commentary_volume: \u89e3\u8bf4\u97f3\u91cf (0.0-1.0)
            burn_subtitles: \u662f\u5426\u70e7\u5f55\u5b57\u5e55
            bgm_path: \u80cc\u666f\u97f3\u4e50\u8def\u5f84
            bgm_volume: \u80cc\u666f\u97f3\u4e50\u97f3\u91cf
            transition: \u8f6c\u573a\u7c7b\u578b ("\u65e0"/"\u6de1\u5165\u6de1\u51fa"/"\u9ed1\u573a\u8fc7\u6e21")
            output_format: \u8f93\u51fa\u683c\u5f0f
            progress_callback: \u8fdb\u5ea6\u56de\u8c03 (step_name, percent)
    
        Returns:
            \u8f93\u51fa\u89c6\u9891\u8def\u5f84\uff0c\u5931\u8d25\u8fd4\u56de None
        """
        tmp_dir = tempfile.mkdtemp(prefix="fireclip_commentary_")
        self._cancel = False  # 重置取消标志

        try:
            # ---- Step 0: 提取高光片段并拼接成短片 (0-15%) ----
            if self._cancel:
                logger.info("解说视频制作已取消")
                return None

            if progress_callback:
                progress_callback("提取高光片段...", 0)

            clips_dir = os.path.join(tmp_dir, "clips")
            extracted = self._extract_and_concat_clips(
                video_path, segments, clips_dir, tmp_dir, progress_callback
            )

            if not extracted:
                logger.error("高光片段提取失败")
                return None

            concat_video = extracted["concat_video"]
            time_map = extracted["time_map"]  # 原始时间 → 拼接时间映射
            logger.info(f"高光短片拼接完成: {concat_video}")

            if progress_callback:
                progress_callback("片段提取完成", 15)

            if self._cancel:
                logger.info("解说视频制作已取消(片段提取完成)")
                return None

            # ---- Step 1: 生成解说文案 (15-30%) ----
            if progress_callback:
                progress_callback("生成解说文案...", 15)

            if mode == "manual":
                # 手动模式：文案已经由用户提供，跳过生成
                commentary = CommentaryTextGenerator.split_manual_text(
                    "", segments  # 文案在调用前已设置
                )
            else:
                commentary = self.text_gen.generate(
                    segments, style=style, mode=mode,
                    progress_callback=lambda p, msg: progress_callback(msg, 15 + int(p * 0.15)) if progress_callback else None
                )

            if not commentary.get("segments"):
                logger.error("解说文案生成失败或为空")
                return None

            logger.info(f"解说文案生成完成: {len(commentary['segments'])}段, {len(commentary['full_text'])}字")

            if progress_callback:
                progress_callback("文案生成完成", 30)

            if self._cancel:
                logger.info("解说视频制作已取消(文案完成)")
                return None

            # ---- Step 2: TTS 语音合成 (30-70%) ----
            audio_segments = self._synthesize_all(
                commentary["segments"], tmp_dir, voice, speed,
                progress_callback, pct_start=30, pct_range=40
            )

            # TTS 容错：部分失败时仍继续（只要有成功的）
            if not audio_segments:
                logger.error("TTS语音合成全部失败")
                return None

            if len(audio_segments) < len(commentary["segments"]):
                failed = len(commentary["segments"]) - len(audio_segments)
                logger.warning(f"TTS部分失败: {failed}/{len(commentary['segments'])}段失败，继续合成")

            if progress_callback:
                progress_callback("语音合成完成", 70)

            if self._cancel:
                logger.info("解说视频制作已取消(TTS完成)")
                return None

            # ---- Step 3: 重映射时间线 — 原始时间 → 拼接时间 (70-75%) ----
            remapped_audio = self._remap_audio_timeline(audio_segments, time_map)

            if progress_callback:
                progress_callback("时间线重映射完成", 75)

            # ---- Step 4: 生成SRT字幕文件 (75-80%) ----
            srt_path = os.path.join(tmp_dir, "commentary.srt")
            subtitle_segments = []
            for seg, audio in zip(commentary["segments"], remapped_audio):
                subtitle_segments.append({
                    "start": audio["start_time"],
                    "end": audio["start_time"] + audio.get("duration", seg.get("duration", 5)),
                    "text": seg["text"]
                })
            self.subtitle_proc.generate_srt(subtitle_segments, srt_path)
            logger.info(f"SRT字幕已生成: {srt_path}")

            if progress_callback:
                progress_callback("字幕生成完成", 80)

            # ---- Step 5: \u6df7\u97f3\u5408\u6210\u89c6\u9891 (80-90%) ----
            # \u5982\u679c\u6709\u8f6c\u573a\u6548\u679c\uff0c\u5148\u5728\u62fc\u63a5\u89c6\u9891\u4e0a\u52a0\u8f6c\u573a
            mixed_video = concat_video
            
            if transition != "\u65e0" and len(segments) > 1:
                if progress_callback:
                    progress_callback("\u6dfb\u52a0\u8f6c\u573a\u6548\u679c...", 80)
                transition_video = os.path.join(tmp_dir, "transition_reel.mp4")
                if self._apply_transitions(concat_video, transition_video, transition, time_map):
                    mixed_video = transition_video
                    logger.info(f"\u8f6c\u573a\u6548\u679c\u5e94\u7528\u6210\u529f: {transition}")
                else:
                    logger.warning("\u8f6c\u573a\u6548\u679c\u5e94\u7528\u5931\u8d25\uff0c\u4f7f\u7528\u539f\u89c6\u9891")
            
            # \u6df7\u97f3\uff1a\u539f\u58f0 + \u89e3\u8bf4
            mixed_output = os.path.join(tmp_dir, "mixed_output.mp4")
            result = self.video_proc.mix_commentary_audio(
                video_path=mixed_video,  # \u7528\u62fc\u63a5/\u8f6c\u573a\u540e\u7684\u89c6\u9891
                commentary_segments=remapped_audio,
                output_path=mixed_output,
                original_volume=original_volume,
                commentary_volume=commentary_volume,
                tmp_dir=tmp_dir
            )
            
            if progress_callback:
                progress_callback("\u6df7\u97f3\u5408\u6210\u5b8c\u6210", 88)
            
            if not result:
                logger.error("\u6df7\u97f3\u5408\u6210\u5931\u8d25")
                return None
            
            # ---- Step 5b: \u6dfb\u52a0BGM (88-92%) ----
            current_video = mixed_output
            
            if bgm_path and os.path.exists(bgm_path):
                if progress_callback:
                    progress_callback("\u6dfb\u52a0\u80cc\u666f\u97f3\u4e50...", 88)
                bgm_output = os.path.join(tmp_dir, "with_bgm.mp4")
                if self._add_bgm(mixed_output, bgm_path, bgm_output, bgm_volume):
                    current_video = bgm_output
                    logger.info("BGM\u6dfb\u52a0\u6210\u529f")
                else:
                    logger.warning("BGM\u6dfb\u52a0\u5931\u8d25\uff0c\u8df3\u8fc7")
            
            if progress_callback:
                progress_callback("\u97f3\u9891\u5904\u7406\u5b8c\u6210", 92)
            
            # ---- Step 5c: \u5b57\u5e55\u70e7\u5f55 (92-96%) ----
            final_video = current_video
            
            if burn_subtitles:
                if progress_callback:
                    progress_callback("\u70e7\u5f55\u5b57\u5e55...", 92)
                burn_output = os.path.join(tmp_dir, "with_subtitles.mp4")
                codec_args = self._get_codec_args(output_format)
                if self._burn_subtitles(current_video, srt_path, burn_output, codec_args):
                    final_video = burn_output
                    logger.info("\u5b57\u5e55\u70e7\u5f55\u6210\u529f")
                else:
                    logger.warning("\u5b57\u5e55\u70e7\u5f55\u5931\u8d25\uff0c\u4fdd\u7559\u65e0\u5b57\u5e55\u7248\u672c")
            
            # ---- Step 6: \u8f93\u51fa\u683c\u5f0f\u8f6c\u6362 + \u590d\u5236\u5230\u6700\u7ec8\u8def\u5f84 (96-100%) ----
            if final_video != output_path:
                if progress_callback:
                    progress_callback("\u8f93\u51fa\u6700\u7ec8\u89c6\u9891...", 96)
                import shutil
                shutil.copy2(final_video, output_path)
            
            if progress_callback:
                progress_callback("\u89e3\u8bf4\u89c6\u9891\u751f\u6210\u5b8c\u6210\uff01", 100)
            
            logger.info(f"\u89e3\u8bf4\u89c6\u9891\u751f\u6210\u6210\u529f: {output_path}")
            return output_path

        except Exception as e:
            logger.error(f"解说视频制作失败: {e}")
            return None
        finally:
            # 清理临时目录
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception:
                pass

    # ================================================================
    # 提取高光片段并拼接成短片
    # ================================================================
    def _extract_and_concat_clips(self, video_path: str,
                                   segments: List[Dict],
                                   clips_dir: str,
                                   tmp_dir: str,
                                   progress_callback=None) -> Optional[Dict]:
        """
        从原片提取高光片段，拼接成一条短片，并返回时间映射

        Returns:
            {"concat_video": str, "time_map": [(orig_start, orig_end, concat_start), ...]}
            失败返回 None
        """
        import time as _time

        # 按 start_time 排序，确保拼接顺序正确
        sorted_segs = sorted(segments, key=lambda s: s.get("start_time", 0))

        # 1. 逐段提取
        clip_paths = []
        time_map = []  # [(orig_start, orig_end, concat_start), ...]
        concat_start = 0.0

        for i, seg in enumerate(sorted_segs):
            if self._cancel:
                return None

            start = float(seg.get("start_time", 0))
            duration = float(seg.get("duration", 5))
            clip_path = os.path.join(clips_dir, f"clip_{i:03d}.mp4")

            if progress_callback:
                pct = int(i / max(len(sorted_segs), 1) * 12)  # 0-12%
                progress_callback(f"提取片段 {i+1}/{len(sorted_segs)}...", pct)

            success = self.video_proc.extract_segment_with_retry(
                video_path, start, duration, clip_path
            )

            if success and os.path.exists(clip_path):
                clip_paths.append(clip_path)
                time_map.append((start, start + duration, concat_start))
                concat_start += duration
                logger.info(f"片段 {i+1}/{len(sorted_segs)} 提取成功")
            else:
                logger.warning(f"片段 {i+1}/{len(sorted_segs)} 提取失败，跳过")

        if not clip_paths:
            logger.error("所有片段提取失败")
            return None

        # 2. 拼接成短片
        if progress_callback:
            progress_callback("拼接高光短片...", 13)

        concat_video = os.path.join(tmp_dir, "highlight_reel.mp4")

        if len(clip_paths) == 1:
            # 单个片段直接复制
            import shutil
            shutil.copy2(clip_paths[0], concat_video)
        else:
            success = self.video_proc._merge_segments_to_file(clip_paths, concat_video)
            if not success:
                logger.error("高光短片拼接失败")
                return None

        return {"concat_video": concat_video, "time_map": time_map}

    # ================================================================
    # 重映射音频时间线：原始时间 → 拼接时间
    # ================================================================
    def _remap_audio_timeline(self, audio_segments: List[Dict],
                               time_map: list) -> List[Dict]:
        """
        将解说音频的 start_time 从原始视频时间线映射到拼接短片时间线

        time_map: [(orig_start, orig_end, concat_start), ...]
        audio_segments: [{"start_time": float, ...}, ...]

        逻辑: 找到每个音频段属于哪个原始片段，然后用拼接起始时间 + 偏移量
        """
        remapped = []

        for seg in audio_segments:
            orig_start = seg.get("start_time", 0)
            new_seg = dict(seg)

            # 在 time_map 中查找所属片段
            # 音频的 orig_start 可能不完全等于片段的 orig_start（文案分段和片段分段可能有微小差异）
            # 所以用区间匹配：找到 orig_start 落在哪个 (orig_start_seg, orig_end_seg) 区间内
            mapped = False
            for o_start, o_end, c_start in time_map:
                if o_start - 0.5 <= orig_start <= o_end + 0.5:
                    # 在此区间内，映射到拼接时间线
                    offset = orig_start - o_start
                    new_seg["start_time"] = max(0, c_start + offset)
                    mapped = True
                    break

            if not mapped:
                # 没找到对应区间，使用最近的片段
                best_idx = 0
                best_dist = float('inf')
                for idx, (o_start, o_end, c_start) in enumerate(time_map):
                    dist = abs(orig_start - o_start)
                    if dist < best_dist:
                        best_dist = dist
                        best_idx = idx
                o_start, o_end, c_start = time_map[best_idx]
                offset = max(0, orig_start - o_start)
                new_seg["start_time"] = c_start + offset
                logger.debug(f"音频段 {orig_start:.1f}s 未精确匹配，使用最近片段偏移")

            remapped.append(new_seg)

        return remapped

    # ================================================================
    # 仅生成文案（不进入TTS/混音流程）
    # ================================================================
    def generate_text_only(self, segments: List[Dict],
                           style: str = "专业解说",
                           mode: str = "template",
                           progress_callback: Optional[Callable] = None) -> Dict:
        """仅生成解说文案，不执行TTS和混音"""
        return self.text_gen.generate(segments, style=style, mode=mode,
                                      progress_callback=progress_callback)

    # ================================================================
    # TTS 批量合成
    # ================================================================
    def _synthesize_all(self, segments: List[Dict], output_dir: str,
                        voice: str, speed: float,
                        progress_callback: Optional[Callable] = None,
                        pct_start: int = 20, pct_range: int = 40) -> List[Dict]:
        """
        批量TTS合成

        Args:
            pct_start: 进度起始百分比
            pct_range: 进度范围

        Returns:
            [{"audio_path": str, "start_time": float, "duration": float, "text": str}, ...]
        """
        os.makedirs(output_dir, exist_ok=True)
        results = []
        total = len(segments)
        max_retries = 2  # 每段最多重试2次

        for i, seg in enumerate(segments):
            # 检查取消
            if self._cancel:
                logger.info(f"TTS合成已取消，已完成 {len(results)}/{total}段")
                break

            text = seg.get("text", "")
            if not text.strip():
                continue

            audio_path = os.path.join(output_dir, f"commentary_{i:03d}.mp3")
            pct = pct_start + int((i / max(total, 1)) * pct_range)

            if progress_callback:
                progress_callback(f"TTS合成 {i+1}/{total}...", pct)

            # 带重试的 TTS 合成
            success = False
            for attempt in range(1, max_retries + 1):
                success = self.tts.synthesize(text, audio_path, voice=voice, speed=speed)
                if success and os.path.exists(audio_path):
                    break
                if attempt < max_retries:
                    import time
                    time.sleep(1.0 * attempt)  # 递增延迟，避免频繁请求限流
                    logger.warning(f"TTS片段 {i+1}/{total} 第{attempt}次重试...")

            if success and os.path.exists(audio_path):
                # 获取音频时长
                duration = self._get_audio_duration(audio_path)
                seg_duration = float(seg.get("duration", 5))

                # TTS 时长适配：如果音频超出片段时长，用 ffmpeg 加速
                if duration > seg_duration * 1.1:
                    speed_factor = duration / seg_duration
                    speed_factor = min(speed_factor, 1.8)  # 最大加速 1.8x
                    adjusted_path = audio_path.replace(".mp3", "_fast.mp3")
                    if self._adjust_audio_speed(audio_path, adjusted_path, speed_factor):
                        audio_path = adjusted_path
                        duration = self._get_audio_duration(adjusted_path)
                        logger.info(f"TTS片段 {i+1} 加速 {speed_factor:.2f}x 适配片段时长 ({duration:.1f}s/{seg_duration:.1f}s)")

                results.append({
                    "audio_path": audio_path,
                    "start_time": seg.get("start_time", 0),
                    "duration": duration,
                    "text": text
                })
                logger.info(f"TTS片段 {i+1}/{total} 合成成功 ({duration:.1f}s)")
            else:
                logger.warning(f"TTS片段 {i+1}/{total} 合成失败")

        return results

    def _get_audio_duration(self, audio_path: str) -> float:
        """使用 ffprobe 获取音频时长"""
        try:
            import subprocess
            cmd = [
                self.video_proc.ffprobe_path,
                '-v', 'error',
                '-show_entries', 'format=duration',
                '-of', 'json',
                audio_path
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                import json
                info = json.loads(result.stdout)
                return float(info.get('format', {}).get('duration', 5.0))
        except Exception:
            pass
        return 5.0  # 默认5秒

    def _adjust_audio_speed(self, input_path: str, output_path: str, speed: float) -> bool:
        """使用 ffmpeg 调整音频速度 (speed>1 加速, speed<1 减速)"""
        try:
            import subprocess
            cmd = [
                self.video_proc.ffmpeg_path,
                '-y', '-i', input_path,
                '-filter:a', f'atempo={speed}',
                '-vn', output_path
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            return result.returncode == 0 and os.path.exists(output_path)
        except Exception as e:
            logger.warning(f"\u97f3\u9891\u52a0\u901f\u5931\u8d25: {e}")
            return False
    
    # ================================================================
    # V3: \u8f6c\u573a\u6548\u679c
    # ================================================================
    def _apply_transitions(self, input_video: str, output_video: str,
                           transition_type: str, time_map: list) -> bool:
        """\u5e94\u7528\u7247\u6bb5\u95f4\u8f6c\u573a\u6548\u679c\n
        \u7531\u4e8e\u62fc\u63a5\u89c6\u9891\u5df2\u7ecf\u662f\u8fde\u7eed\u7684\uff0c\u8f6c\u573a\u6548\u679c\u9700\u8981\u5728\u7247\u6bb5\u5207\u6362\u70b9\u5e94\u7528\u6ee4\u955c\n        \u7528 xfade \u6ee4\u955c\u5b9e\u73b0\u6de1\u5165\u6de1\u51fa\uff0c\u7528 \u7f29\u653e\u5b9e\u73b0\u9ed1\u573a\u8fc7\u6e21
        """
        try:
            import subprocess
            # \u83b7\u53d6\u89c6\u9891\u65f6\u957f
            duration = self._get_video_duration(input_video)
            if duration <= 0:
                return False
    
            # \u8ba1\u7b97\u8f6c\u573a\u70b9\uff08\u7247\u6bb5\u5207\u6362\u4f4d\u7f6e\uff09
            # time_map: [(orig_start, orig_end, concat_start), ...]
            # \u7247\u6bb5\u8fb9\u754c = \u5404\u7247\u6bb5\u7684 concat_start + duration
            transition_pts = []
            for i in range(len(time_map) - 1):
                # \u5f53\u524d\u7247\u6bb5\u7ed3\u675f\u65f6\u95f4 = \u4e0b\u4e00\u7247\u6bb5\u7684 concat_start
                pt = time_map[i + 1][2]  # \u4e0b\u4e00\u7247\u6bb5\u5728\u62fc\u63a5\u89c6\u9891\u4e2d\u7684\u8d77\u59cb\u65f6\u95f4
                transition_pts.append(pt)
    
            if not transition_pts:
                return False
    
            # \u8f6c\u573a\u65f6\u957f\uff08\u79d2\uff09
            trans_dur = 0.5
    
            if transition_type == "\u6de1\u5165\u6de1\u51fa":
                # \u4f7f\u7528 xfade \u6ee4\u955c\u94fe\n                # \u6784\u5efa\u590d\u6742\u7684 xfade \u6ee4\u955c\u56fe\uff0c\u4f46\u7531\u4e8e\u89c6\u9891\u5df2\u7ecf\u62fc\u63a5\uff0c
                # \u66f4\u7b80\u5355\u7684\u65b9\u6848\u662f\u5728\u8f6c\u573a\u70b9\u653e\u7f6e\u7f29\u653e\u6548\u679c
                filter_parts = []
                for pt in transition_pts:
                    filter_parts.append(
                        f"fade=t=in:st={pt}:d={trans_dur},"
                        f"fade=t=out:st={pt - trans_dur}:d={trans_dur}"
                    )
                vf = ",".join(filter_parts)
            elif transition_type == "\u9ed1\u573a\u8fc7\u6e21":
                # \u5728\u8f6c\u573a\u70b9\u524d\u540e\u5404\u52a0\u4e00\u6bb5\u9ed1\u573a\u6de1\u5165\u6de1\u51fa
                filter_parts = []
                for pt in transition_pts:
                    filter_parts.append(
                        f"fade=t=out:st={pt - trans_dur}:d={trans_dur},"
                        f"fade=t=in:st={pt}:d={trans_dur}"
                    )
                vf = ",".join(filter_parts)
            else:
                return False
    
            cmd = [
                self.video_proc.ffmpeg_path,
                '-y', '-i', input_video,
                '-vf', vf,
                '-c:a', 'copy',
                output_video
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            return result.returncode == 0 and os.path.exists(output_video)
        except Exception as e:
            logger.warning(f"\u8f6c\u573a\u6548\u679c\u5e94\u7528\u5931\u8d25: {e}")
            return False
    
    def _get_video_duration(self, video_path: str) -> float:
        """\u83b7\u53d6\u89c6\u9891\u65f6\u957f"""
        try:
            import subprocess, json
            cmd = [
                self.video_proc.ffprobe_path,
                '-v', 'error',
                '-show_entries', 'format=duration',
                '-of', 'json',
                video_path
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                info = json.loads(result.stdout)
                return float(info.get('format', {}).get('duration', 0))
        except Exception:
            pass
        return 0
    
    # ================================================================
    # V3: BGM \u6dfb\u52a0
    # ================================================================
    def _add_bgm(self, input_video: str, bgm_path: str,
                 output_video: str, bgm_volume: float) -> bool:
        """\u4e3a\u89c6\u9891\u6dfb\u52a0\u80cc\u666f\u97f3\u4e50\n        BGM\u4f1a\u5faa\u73af\u64ad\u653e\u4ee5\u8986\u76d6\u89c6\u9891\u65f6\u957f"""
        try:
            import subprocess
            cmd = [
                self.video_proc.ffmpeg_path,
                '-y',
                '-i', input_video,
                '-stream_loop', '-1',  # BGM\u5faa\u73af\u64ad\u653e
                '-i', bgm_path,
                '-filter_complex',
                f'[1:a]volume={bgm_volume}[bgm];'
                f'[0:a][bgm]amix=inputs=2:duration=first:dropout_transition=2[aout]',
                '-map', '0:v',
                '-map', '[aout]',
                '-c:v', 'copy',
                '-c:a', 'aac',
                '-shortest',
                output_video
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            return result.returncode == 0 and os.path.exists(output_video)
        except Exception as e:
            logger.warning(f"BGM\u6dfb\u52a0\u5931\u8d25: {e}")
            return False
    
    # ================================================================
    # V3: \u5b57\u5e55\u70e7\u5f55
    # ================================================================
    def _burn_subtitles(self, input_video: str, srt_path: str,
                        output_video: str, codec_args: list) -> bool:
        """\u4f7f\u7528 ffmpeg \u5c06 SRT \u5b57\u5e55\u70e7\u5f55\u5230\u89c6\u9891"""
        try:
            import subprocess
            srt_escaped = srt_path.replace('\\', '/').replace(':', '\\:').replace(chr(39), chr(92)*2+chr(39))
    
            cmd = [
                self.video_proc.ffmpeg_path,
                '-y', '-i', input_video,
                '-vf', f"subtitles='{srt_escaped}'",
                *codec_args,
                output_video
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if result.returncode != 0:
                logger.warning(f"\u5b57\u5e55\u70e7\u5f55 stderr: {result.stderr[:500]}")
            return result.returncode == 0 and os.path.exists(output_video)
        except Exception as e:
            logger.warning(f"\u5b57\u5e55\u70e7\u5f55\u5931\u8d25: {e}")
            return False
    
    # ================================================================
    # V3: \u8f93\u51fa\u683c\u5f0f\u7f16\u7801\u53c2\u6570
    # ================================================================
    @staticmethod
    def _get_codec_args(output_format: str) -> list:
        """\u6839\u636e\u8f93\u51fa\u683c\u5f0f\u8fd4\u56de ffmpeg \u7f16\u7801\u53c2\u6570"""
        format_map = {
            "MP4 (H.264)":        ['-c:v', 'libx264', '-preset', 'medium', '-crf', '23'],
            "MP4 (H.265/HEVC)":   ['-c:v', 'libx265', '-preset', 'medium', '-crf', '28'],
            "MOV (ProRes)":       ['-c:v', 'prores_ks', '-profile:v', '3'],
            "WEBM (VP9)":         ['-c:v', 'libvpx-vp9', '-crf', '30', '-b:v', '0'],
        }
        return format_map.get(output_format, format_map["MP4 (H.264)"])
    
    # ================================================================
    # \u8d44\u6e90\u6e05\u7406
    # ================================================================
    def unload_models(self):
        """卸载所有AI模型释放显存"""
        self.text_gen.unload_model()
