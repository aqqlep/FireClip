"""
通道2: 运动向量分析（v2.8 光流版）
使用 OpenCV Farneback 稠密光流算法分析真实运动强度
替代旧版 FFmpeg scene_score（那只是场景切换检测，不是运动分析）
"""
import subprocess
import os
import cv2
import numpy as np
from pathlib import Path
from typing import List, Dict, Callable, Optional
from utils.logger import logger
from config import CONFIG


class MotionAnalyzer:
    """运动向量分析器（v2.8: 真正的光流分析）"""
    
    def __init__(self, sample_fps: float = 4.0, target_width: int = 320):
        """
        初始化运动分析器
        
        Args:
            sample_fps: 采样帧率，默认4fps（平衡速度和精度）
            target_width: 降采样宽度，默认320px
        """
        self.sample_fps = sample_fps
        self.target_width = target_width
        # v3.0: 使用公共工具获取ffmpeg路径
        from utils.helpers import get_ffmpeg_path
        self.ffmpeg_path = get_ffmpeg_path()
    
    def analyze(self, video_path: str, callback: Optional[Callable] = None,
                shared_frames: Optional[object] = None) -> List[Dict]:
        """
        分析视频运动强度（v3.0: 支持共享帧缓存）
        
        Args:
            video_path: 视频文件路径
            callback: 进度回调函数
            shared_frames: 可选的共享帧缓存 (cache_path, count, width, height, fps)
        """
        logger.info(f"开始运动分析(光流v3.0): {video_path}")
        
        if callback:
            callback(0, "正在分析光流运动...")
        
        try:
            motion_data = []
            prev_gray = None
            prev_magnitude = 0.0
            frame_count = 0
            
            # v3.0: 优先使用共享帧缓存
            if shared_frames is not None:
                cache_path, fcount, fw, fh, ffps = shared_frames
                if cache_path and os.path.exists(cache_path):
                    from core.shared_frame_extractor import SharedFrameCache
                    sfc = SharedFrameCache.get_instance()
                    frames = sfc.load_frames(cache_path, fcount, fw, fh)
                    actual_fps = ffps
                    duration = fcount / ffps if ffps > 0 else 0
                    progress_interval = max(1, int(ffps * 5))
                    
                    for i in range(len(frames)):
                        # BGR转灰度
                        frame = cv2.cvtColor(frames[i], cv2.COLOR_BGR2GRAY)
                        frame_count += 1
                        t = frame_count / actual_fps
                        
                        if prev_gray is not None:
                            flow = cv2.calcOpticalFlowFarneback(
                                prev_gray, frame, None,
                                pyr_scale=0.5, levels=3, winsize=15,
                                iterations=3, poly_n=5, poly_sigma=1.2, flags=0
                            )
                            dx, dy = flow[:, :, 0], flow[:, :, 1]
                            magnitude = np.sqrt(dx ** 2 + dy ** 2)
                            mean_mag = float(np.mean(magnitude))
                            top5_threshold = np.percentile(magnitude, 95)
                            max_flow = float(np.mean(magnitude[magnitude >= top5_threshold]))
                            flow_std = float(np.std(magnitude))
                            angle = np.arctan2(dy, dx)
                            mean_cos = float(np.mean(np.cos(angle)))
                            mean_sin = float(np.mean(np.sin(angle)))
                            direction_consistency = float(np.sqrt(mean_cos**2 + mean_sin**2))
                            main_direction = float(np.degrees(np.arctan2(mean_sin, mean_cos)))
                            acceleration = float(mean_mag - prev_magnitude)
                            norm_mean = min(mean_mag / 8.0, 1.0)
                            norm_max = min(max_flow / 15.0, 1.0)
                            norm_std = min(flow_std / 5.0, 1.0)
                            norm_accel = min(abs(acceleration) / 5.0, 1.0)
                            chaos_bonus = (1.0 - direction_consistency) * 0.15
                            composite_score = (
                                norm_mean * 0.35 + norm_max * 0.25 +
                                norm_std * 0.20 + norm_accel * 0.10 +
                                chaos_bonus * 0.10
                            )
                            composite_score = min(composite_score, 1.0)
                            normalized_mag = composite_score * 100.0
                            motion_data.append({
                                "time": t, "magnitude": normalized_mag,
                                "direction": main_direction, "mean_flow": mean_mag,
                                "max_flow": max_flow, "flow_std": flow_std,
                                "direction_consistency": direction_consistency,
                                "acceleration": acceleration,
                                "composite_score": composite_score
                            })
                            prev_magnitude = mean_mag
                        
                        prev_gray = frame
                        
                        if callback and frame_count % progress_interval == 0 and duration > 0:
                            progress = int(min((t / duration) * 100, 99))
                            callback(progress, f"光流分析(缓存): {frame_count}帧")
                    
                    del frames  # 释放mmap引用
                    if callback:
                        callback(100, f"光流分析完成: {len(motion_data)}帧")
                    logger.info(f"光流分析完成(共享缓存): {len(motion_data)}个数据点")
                    return motion_data
            
            # 回退: 独立解码
            video_info = self._get_video_info(video_path)
            orig_width = video_info.get("width", 1280)
            orig_height = video_info.get("height", 720)
            duration = video_info.get("duration", 0)
            
            # 降采样
            scale_factor = self.target_width / orig_width
            target_height = int(orig_height * scale_factor)
            target_height = target_height if target_height % 2 == 0 else target_height - 1
            
            logger.debug(f"光流降采样: {orig_width}x{orig_height} -> {self.target_width}x{target_height}")
            
            # 用 FFmpeg 管道输出原始灰度帧
            cmd = [
                self.ffmpeg_path,
                "-i", video_path,
                "-vf", f"fps={int(self.sample_fps)},scale={self.target_width}:{target_height}",
                "-f", "rawvideo",
                "-pix_fmt", "gray",
                "-nostats",
                "-loglevel", "error",
                "-"
            ]
            
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=1024 * 1024
            )
            
            frame_size = self.target_width * target_height
            motion_data = []
            
            prev_gray = None
            prev_magnitude = 0.0
            frame_count = 0
            
            progress_interval = max(1, int(self.sample_fps * 5))
            
            try:
                while True:
                    raw_frame = process.stdout.read(frame_size)
                    if not raw_frame or len(raw_frame) < frame_size:
                        break                    
                    frame = np.frombuffer(raw_frame, dtype=np.uint8).reshape((target_height, self.target_width))
                    frame_count += 1
                    time = frame_count / self.sample_fps
                    
                    if prev_gray is not None:
                        # ====== Farneback 稠密光流计算 ======
                        flow = cv2.calcOpticalFlowFarneback(
                            prev_gray, frame,
                            None,
                            pyr_scale=0.5,    # 金字塔缩放
                            levels=3,          # 3层金字塔
                            winsize=15,        # 窗口大小
                            iterations=3,
                            poly_n=5,          # 多项式邻域
                            poly_sigma=1.2,
                            flags=0
                        )
                        
                        # flow shape: (H, W, 2), 最后一维是 (dx, dy)
                        dx = flow[:, :, 0]
                        dy = flow[:, :, 1]
                        
                        # 光流幅度（每个像素的位移量）
                        magnitude = np.sqrt(dx ** 2 + dy ** 2)
                        
                        # ---- 特征提取 ----
                        # 1. 平均光流幅度（整体运动强度）
                        mean_mag = float(np.mean(magnitude))
                        
                        # 2. 最大光流幅度（局部剧烈运动）
                        # 取 top 5% 的平均值，避免噪声尖峰
                        top5_threshold = np.percentile(magnitude, 95)
                        max_flow = float(np.mean(magnitude[magnitude >= top5_threshold]))
                        
                        # 3. 光流标准差（运动不均匀性）
                        flow_std = float(np.std(magnitude))
                        
                        # 4. 方向一致性（整体运动 vs 混乱运动）
                        # 高一致性 = 摄像机平移/整体运动
                        # 低一致性 = 打斗/特效/多物体运动（更可能是精彩场面）
                        angle = np.arctan2(dy, dx)
                        # 使用圆形统计量计算方向一致性
                        mean_cos = float(np.mean(np.cos(angle)))
                        mean_sin = float(np.mean(np.sin(angle)))
                        direction_consistency = float(np.sqrt(mean_cos ** 2 + mean_sin ** 2))
                        
                        # 5. 运动方向（主方向角度）
                        main_direction = float(np.degrees(np.arctan2(mean_sin, mean_cos)))
                        
                        # 6. 运动加速度（幅度变化率）
                        acceleration = float(mean_mag - prev_magnitude)
                        
                        # 7. 综合运动得分（融合多维度）
                        # 平均幅度 + 最大幅度 + 不均匀性 + 加速度突变
                        # 归一化：光流幅度通常在 0-20 像素范围
                        norm_mean = min(mean_mag / 8.0, 1.0)       # 8像素=很强运动
                        norm_max = min(max_flow / 15.0, 1.0)       # 15像素=极剧烈
                        norm_std = min(flow_std / 5.0, 1.0)        # 标准差5=很不均匀
                        norm_accel = min(abs(acceleration) / 5.0, 1.0)  # 加速度突变
                        
                        # 低方向一致性 = 多物体混乱运动 → 加成
                        chaos_bonus = (1.0 - direction_consistency) * 0.15
                        
                        composite_score = (
                            norm_mean * 0.35 +
                            norm_max * 0.25 +
                            norm_std * 0.20 +
                            norm_accel * 0.10 +
                            chaos_bonus * 0.10
                        )
                        composite_score = min(composite_score, 1.0)
                        
                        # 缩放到 0-100 范围（兼容旧的 magnitude 字段）
                        normalized_mag = composite_score * 100.0
                        
                        motion_data.append({
                            "time": time,
                            "magnitude": normalized_mag,
                            "direction": main_direction,
                            "mean_flow": mean_mag,
                            "max_flow": max_flow,
                            "flow_std": flow_std,
                            "direction_consistency": direction_consistency,
                            "acceleration": acceleration,
                            "composite_score": composite_score
                        })
                        
                        prev_magnitude = mean_mag
                    
                    prev_gray = frame
                    
                    # 进度报告
                    if callback and frame_count % progress_interval == 0 and duration > 0:
                        current_time = frame_count / self.sample_fps
                        progress = int(min((current_time / duration) * 100, 99))
                        callback(progress, f"光流分析: {frame_count}帧 ({current_time:.0f}s/{duration:.0f}s)")
                
                process.wait()
            
            finally:
                if prev_gray is not None:
                    del prev_gray
                try:
                    process.stdout.close()
                except Exception:
                    pass
                try:
                    process.wait(timeout=3)
                except Exception:
                    try:
                        process.kill()
                    except Exception:
                        pass
            
            if callback:
                callback(100, f"光流分析完成: {len(motion_data)}帧")
            
            logger.info(f"光流分析完成: {len(motion_data)}个数据点")
            return motion_data
        
        except Exception as e:
            logger.error(f"光流运动分析失败: {e}")
            if callback:
                callback(100, f"分析失败: {str(e)}")
            return []
    
    def analyze_segments(self, video_path: str, segment_duration: float = 5.0,
                        callback: Optional[Callable] = None) -> List[Dict]:
        """
        按时间段分析运动强度
        
        Args:
            video_path: 视频文件路径
            segment_duration: 每段时长（秒）
            callback: 进度回调函数
        
        Returns:
            运动强度列表 [{"start": float, "end": float, "avg_magnitude": float, "max_magnitude": float}, ...]
        """
        motion_data = self.analyze(video_path, callback)
        
        if not motion_data:
            return []
        
        # 获取视频总时长
        total_duration = self._get_video_duration(video_path)
        if total_duration <= 0:
            return []
        
        # 按时间段聚合
        segments = []
        current_start = 0.0
        
        while current_start < total_duration:
            current_end = min(current_start + segment_duration, total_duration)
            
            # 过滤当前时间段的数据
            segment_motion = [
                m for m in motion_data
                if current_start <= m["time"] < current_end
            ]
            
            if segment_motion:
                magnitudes = [m["magnitude"] for m in segment_motion]
                avg_magnitude = sum(magnitudes) / len(magnitudes)
                max_magnitude = max(magnitudes)
                
                segments.append({
                    "start": current_start,
                    "end": current_end,
                    "avg_magnitude": avg_magnitude,
                    "max_magnitude": max_magnitude
                })
            else:
                segments.append({
                    "start": current_start,
                    "end": current_end,
                    "avg_magnitude": 0.0,
                    "max_magnitude": 0.0
                })
            
            current_start = current_end
        
        logger.info(f"生成 {len(segments)} 个运动强度段")
        return segments
    
    def detect_high_motion_segments(self, video_path: str, threshold_percentile: float = 85,
                                   segment_duration: float = 5.0,
                                   callback: Optional[Callable] = None) -> List[Dict]:
        """
        检测高运动强度片段
        
        Args:
            video_path: 视频文件路径
            threshold_percentile: 阈值百分位（0-100）
            segment_duration: 每段时长（秒）
            callback: 进度回调函数
        
        Returns:
            高运动片段列表 [{"start": float, "end": float, "score": float}, ...]
        """
        segments = self.analyze_segments(video_path, segment_duration, callback)
        
        if not segments:
            return []
        
        # 计算阈值
        magnitudes = [s["avg_magnitude"] for s in segments if s["avg_magnitude"] > 0]
        if not magnitudes:
            return []
        
        magnitudes.sort()
        threshold_index = int(len(magnitudes) * threshold_percentile / 100)
        threshold = magnitudes[min(threshold_index, len(magnitudes) - 1)]
        
        # 筛选高运动片段
        high_motion_segments = []
        for segment in segments:
            if segment["avg_magnitude"] >= threshold:
                # 归一化得分
                score = min(segment["avg_magnitude"] / (threshold * 2), 1.0)
                high_motion_segments.append({
                    "start": segment["start"],
                    "end": segment["end"],
                    "score": score
                })
        
        logger.info(f"检测到 {len(high_motion_segments)} 个高运动片段")
        return high_motion_segments
    
    def _get_video_info(self, video_path: str) -> Dict:
        """获取视频信息（宽、高、时长）"""
        try:
            import json as _json
            if self.ffmpeg_path and self.ffmpeg_path != "ffmpeg":
                ffprobe_path = str(Path(self.ffmpeg_path).parent / "ffprobe.exe")
            else:
                ffprobe_path = "ffprobe"
            
            cmd = [
                ffprobe_path,
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height,r_frame_rate",
                "-show_entries", "format=duration",
                "-of", "json",
                video_path
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            
            if result.returncode == 0:
                data = _json.loads(result.stdout)
                width, height, duration = 1280, 720, 0
                
                if "streams" in data and data["streams"]:
                    s = data["streams"][0]
                    width = s.get("width", 1280)
                    height = s.get("height", 720)
                
                if "format" in data:
                    duration = float(data["format"].get("duration", 0))
                
                return {"width": width, "height": height, "duration": duration}
        except Exception as e:
            logger.warning(f"获取视频信息失败: {e}")
        
        return {"width": 1280, "height": 720, "duration": 0}
    
    def _get_video_duration(self, video_path: str) -> float:
        """获取视频时长"""
        info = self._get_video_info(video_path)
        return info.get("duration", 0.0)


# 测试代码
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("用法: python motion_analyzer.py <video_path>")
        sys.exit(1)
    
    video_path = sys.argv[1]
    analyzer = MotionAnalyzer()
    
    def progress_callback(progress, message):
        print(f"[{progress}%] {message}")
    
    # 分析运动向量
    motion_data = analyzer.analyze(video_path, progress_callback)
    print(f"\n分析完成: {len(motion_data)}个数据点")
    
    # 检测高运动片段
    high_motion = analyzer.detect_high_motion_segments(video_path, threshold_percentile=85)
    print(f"\n高运动片段: {len(high_motion)}个")
    for segment in high_motion[:5]:  # 只显示前5个
        print(f"  {segment['start']:.2f}s - {segment['end']:.2f}s, 得分: {segment['score']:.3f}")
