"""
通道4&5: 特效检测（色彩突变+亮度闪烁+渐变能量+局部高亮）
检测仙侠/奇幻/漫剧中的特效打斗场面
v2.6 增强：HSV精确色彩分析 + 4fps采样 + 渐变色检测 + 局部高亮区域检测
"""
import subprocess
import json
import os
import cv2
import numpy as np
from pathlib import Path
from typing import List, Dict, Callable, Optional
from utils.logger import logger
from config import CONFIG


class VFXDetector:
    """特效检测器"""
    
    def __init__(self, color_threshold: float = 40.0, brightness_threshold: float = 80.0):
        """
        初始化特效检测器 (v2.6 增强版)
        
        Args:
            color_threshold: 色彩突变阈值（0-255），默认40（降低灵敏度）
            brightness_threshold: 亮度闪烁阈值（0-255），默认80（降低灵敏度）
        """
        self.color_threshold = color_threshold
        self.brightness_threshold = brightness_threshold
        self.ffmpeg_path = CONFIG.ffmpeg_path
    
    def detect(self, video_path: str, callback: Optional[Callable] = None,
                shared_frames: Optional[object] = None) -> Dict[str, List[Dict]]:
        """
        检测视频中的特效场景（v3.0: 支持共享帧缓存）
        
        Args:
            video_path: 视频文件路径
            callback: 进度回调函数
            shared_frames: 可选的共享帧缓存 (cache_path, count, width, height, fps)
        
        Returns:
            {"color_burst": [...], "brightness_flash": [...], "vfx_energy": [...]}
        """
        logger.info(f"开始特效检测(v3.0): {video_path}")
        
        if callback:
            callback(0, "正在提取视频帧(HSV分析)...")
        
        try:
            # v3.0: 优先使用共享帧缓存
            if shared_frames is not None:
                cache_path, fcount, fw, fh, ffps = shared_frames
                if cache_path and os.path.exists(cache_path):
                    return self._detect_from_cache(cache_path, fcount, fw, fh, ffps, callback)
            
            # 回退: 独立解码
            # 获取视频信息
            video_info = self._get_video_info(video_path)
            orig_width = video_info.get("width", 1280)
            orig_height = video_info.get("height", 720)
            fps = video_info.get("fps", 30)
            duration = video_info.get("duration", 0)
            
            # 降采样到 320 宽度
            target_width = 320
            scale_factor = target_width / orig_width
            target_height = int(orig_height * scale_factor)
            target_height = target_height if target_height % 2 == 0 else target_height - 1
            
            logger.debug(f"VFX降采样: {orig_width}x{orig_height} -> {target_width}x{target_height}")
            
            # v2.6: 提升到 4fps（从2fps），捕捉更快的特效过程
            vfx_fps = 4.0
            
            cmd = [
                self.ffmpeg_path,
                "-i", video_path,
                "-vf", f"fps={int(vfx_fps)},scale={target_width}:{target_height}",
                "-f", "rawvideo",
                "-pix_fmt", "bgr24",
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
            
            frame_size = target_width * target_height * 3
            
            # 流式处理
            color_burst_data = []
            brightness_flash_data = []
            vfx_energy_data = []  # v2.6新增：渐变能量数据
            
            prev_frame = None
            prev_hsv = None
            frame_count = 0
            
            # v2.6: 渐变检测 - 追踪连续帧的色彩变化累积
            color_change_history = []  # 最近N帧的色彩变化量
            HISTORY_SIZE = 8  # 追踪2秒(4fps*0.5s)的渐变窗口
            
            progress_interval = max(1, int(vfx_fps * 5))
            
            try:
                while True:
                    raw_frame = process.stdout.read(frame_size)
                    if not raw_frame:
                        break
                    
                    if len(raw_frame) < frame_size:
                        break
                    
                    frame = np.frombuffer(raw_frame, dtype=np.uint8).reshape((target_height, target_width, 3))
                    frame_count += 1
                    time = frame_count / vfx_fps
                    
                    # v2.6: 真正的HSV转换（精确色彩分析）
                    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
                    h_channel = hsv[:, :, 0]  # 色相
                    s_channel = hsv[:, :, 1]  # 饱和度
                    v_channel = hsv[:, :, 2]  # 亮度/明度
                    
                    # v2.6: 局部高亮区域检测（飞剑、法术光球、能量团）
                    # 检测画面中亮度>160且饱和度>80的区域比例（v2.6b: 降低阈值捕捉远处小光效）
                    bright_saturated_mask = (v_channel > 160) & (s_channel > 80)
                    bright_saturated_ratio = float(np.sum(bright_saturated_mask)) / bright_saturated_mask.size
                    
                    # v2.6b: 局部极高亮区域（飞剑核心、法术爆发中心）
                    ultra_bright_mask = (v_channel > 210) & (s_channel > 120)
                    ultra_bright_ratio = float(np.sum(ultra_bright_mask)) / ultra_bright_mask.size
                    
                    # v2.6: 边缘光效检测（高频亮度模式 - 能量波纹、光晕）
                    # 使用Sobel算子检测亮度梯度
                    v_float = v_channel.astype(np.float32)
                    sobel_x = cv2.Sobel(v_float, cv2.CV_32F, 1, 0, ksize=3)
                    sobel_y = cv2.Sobel(v_float, cv2.CV_32F, 0, 1, ksize=3)
                    edge_intensity = float(np.mean(np.sqrt(sobel_x**2 + sobel_y**2)))
                    # 归一化到0-1
                    edge_norm = min(edge_intensity / 80.0, 1.0)
                    
                    # v2.6: 全局饱和度统计
                    avg_saturation = float(np.mean(s_channel)) / 255.0
                    high_saturation = float(np.sum(s_channel > 150)) / s_channel.size
                    
                    if prev_frame is not None and prev_hsv is not None:
                        # ============ 色彩突变检测（HSV空间） ============
                        # 色相差异（环形距离，考虑H的环绕特性）
                        h_diff = np.minimum(
                            np.abs(hsv[:, :, 0].astype(np.int16) - prev_hsv[:, :, 0].astype(np.int16)),
                            180 - np.abs(hsv[:, :, 0].astype(np.int16) - prev_hsv[:, :, 0].astype(np.int16))
                        )
                        avg_h_diff = float(np.mean(h_diff)) / 180.0  # 归一化到0-1
                        
                        # 饱和度差异
                        s_diff = np.abs(s_channel.astype(np.int16) - prev_hsv[:, :, 1].astype(np.int16))
                        avg_s_diff = float(np.mean(s_diff)) / 255.0
                        
                        # 亮度差异
                        v_diff = np.abs(v_channel.astype(np.int16) - prev_hsv[:, :, 2].astype(np.int16))
                        avg_v_diff = float(np.mean(v_diff)) / 255.0
                        
                        # 综合色彩突变评分（HSV加权）
                        color_score = avg_h_diff * 0.35 + avg_s_diff * 0.35 + avg_v_diff * 0.30
                        
                        # v2.6: 渐变检测 - 追踪色彩变化累积
                        color_change_history.append(color_score)
                        if len(color_change_history) > HISTORY_SIZE:
                            color_change_history.pop(0)
                        
                        # 渐变得分：窗口内持续中等以上变化的帧比例
                        # 法术蓄力过程：连续多帧有中等的色彩变化
                        if len(color_change_history) >= 3:
                            sustained_changes = sum(1 for c in color_change_history if c > 0.08)
                            gradual_ratio = sustained_changes / len(color_change_history)
                            # 渐变能量评分
                            gradual_score = gradual_ratio * min(sum(color_change_history) / len(color_change_history) * 5.0, 1.0)
                        else:
                            gradual_score = 0.0
                        
                        # ============ 色彩突变判定 ============
                        # v2.6: 降低阈值+多维度判定
                        if color_score > 0.12 or (avg_h_diff > 0.15 and high_saturation > 0.05):
                            color_burst_data.append({
                                "time": time,
                                "score": float(min(color_score * 3.0, 1.0)),
                                "avg_diff": float(avg_h_diff + avg_s_diff + avg_v_diff),
                                "high_saturation_ratio": float(high_saturation),
                                "hue_change": float(avg_h_diff),
                                "edge_intensity": float(edge_norm)
                            })
                        
                        # ============ 亮度闪烁检测 ============
                        gray = v_channel  # HSV的V通道就是亮度
                        prev_gray = prev_hsv[:, :, 2]
                        brightness_diff = float(np.mean(np.abs(gray.astype(np.int16) - prev_gray.astype(np.int16)))) / 255.0
                        high_brightness = float(np.mean(v_channel > 200))
                        
                        brightness_score = brightness_diff * 0.35 + high_brightness * 0.35 + bright_saturated_ratio * 0.30
                        
                        if brightness_score > 0.10 or bright_saturated_ratio > 0.02:
                            brightness_flash_data.append({
                                "time": time,
                                "score": float(min(brightness_score * 3.0, 1.0)),
                                "avg_diff": float(brightness_diff * 255),
                                "high_brightness_ratio": float(high_brightness),
                                "bright_saturated_ratio": float(bright_saturated_ratio)
                            })
                        
                        # ============ v2.6新增: VFX综合能量评分 ============
                        # 融合多维度：色彩突变+局部高亮+边缘光效+渐变能量+极高亮
                        vfx_energy = (
                            color_score * 0.20 +
                            bright_saturated_ratio * 3.0 * 0.20 +  # 局部高亮放大
                            ultra_bright_ratio * 5.0 * 0.15 +       # v2.6b: 极高亮区域放大
                            edge_norm * 0.15 +
                            gradual_score * 0.15 +
                            high_saturation * 0.15
                        )
                        vfx_energy = min(vfx_energy, 1.0)
                        
                        if vfx_energy > 0.12:  # v2.6b: 降低阈值捕捉更微妙的特效
                            vfx_energy_data.append({
                                "time": time,
                                "score": float(vfx_energy),
                                "color_score": float(color_score),
                                "bright_saturated": float(bright_saturated_ratio),
                                "ultra_bright": float(ultra_bright_ratio),
                                "edge_intensity": float(edge_norm),
                                "gradual_score": float(gradual_score),
                                "saturation": float(high_saturation)
                            })
                    else:
                        # 第一帧：初始化渐变历史
                        color_change_history = [0.0]
                    
                    # 更新前一帧
                    prev_frame = frame
                    prev_hsv = hsv
                    
                    # 进度报告
                    if callback and frame_count % progress_interval == 0 and duration > 0:
                        current_time = frame_count / vfx_fps
                        progress = int(min((current_time / duration) * 100, 100))
                        callback(progress, f"VFX分析: {frame_count}帧 ({current_time:.0f}s/{duration:.0f}s)")
                
                process.wait()
                
            finally:
                if prev_frame is not None:
                    del prev_frame
                if prev_hsv is not None:
                    del prev_hsv
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
                callback(100, f"VFX检测完成: {len(color_burst_data)}色彩突变, "
                         f"{len(brightness_flash_data)}亮度闪烁, {len(vfx_energy_data)}VFX能量")
            
            logger.info(f"VFX检测完成(v2.6): {len(color_burst_data)}色彩突变, "
                       f"{len(brightness_flash_data)}亮度闪烁, {len(vfx_energy_data)}VFX能量, "
                       f"共处理{frame_count}帧")
            
            return {
                "color_burst": color_burst_data,
                "brightness_flash": brightness_flash_data,
                "vfx_energy": vfx_energy_data  # v2.6新增通道
            }
        
        except Exception as e:
            logger.error(f"特效检测失败: {e}")
            if callback:
                callback(100, f"检测失败: {str(e)}")
            return {"color_burst": [], "brightness_flash": [], "vfx_energy": []}
    
    def _analyze_color_burst(self, frames: List[np.ndarray], fps: int,
                            callback: Optional[Callable] = None) -> List[Dict]:
        """
        分析色彩突变（法术光效、能量波等）
        
        Args:
            frames: 帧列表
            fps: 帧率
            callback: 进度回调
        
        Returns:
            色彩突变数据列表
        """
        color_burst_data = []
        
        for i in range(1, len(frames)):
            prev_frame = frames[i - 1]
            curr_frame = frames[i]
            
            # 转换为HSV空间
            prev_hsv = cv2.cvtColor(prev_frame, cv2.COLOR_BGR2HSV)
            curr_hsv = cv2.cvtColor(curr_frame, cv2.COLOR_BGR2HSV)
            
            # 计算色彩差异
            diff = cv2.absdiff(curr_hsv, prev_hsv)
            
            # 计算平均色彩变化
            avg_diff = np.mean(diff)
            
            # 检测高饱和度区域（特效通常有高饱和度）
            saturation = curr_hsv[:, :, 1]
            high_saturation = np.sum(saturation > 150) / saturation.size
            
            # 综合评分
            score = (avg_diff / 255.0) * 0.6 + high_saturation * 0.4
            
            if score > self.color_threshold / 255.0:
                time = i / fps
                color_burst_data.append({
                    "time": time,
                    "score": float(score),
                    "avg_diff": float(avg_diff),
                    "high_saturation_ratio": float(high_saturation)
                })
            
            # 更新进度
            if callback and i % 100 == 0:
                progress = 50 + int((i / len(frames)) * 25)  # 50-75%
                callback(progress, f"分析色彩突变: {i}/{len(frames)}")
        
        return color_burst_data
    
    def _analyze_brightness_flash(self, frames: List[np.ndarray], fps: int,
                                 callback: Optional[Callable] = None) -> List[Dict]:
        """
        分析亮度闪烁（爆炸、能量释放、闪光等）
        
        Args:
            frames: 帧列表
            fps: 帧率
            callback: 进度回调
        
        Returns:
            亮度闪烁数据列表
        """
        brightness_flash_data = []
        
        for i in range(1, len(frames)):
            prev_frame = frames[i - 1]
            curr_frame = frames[i]
            
            # 转换为灰度
            prev_gray = cv2.cvtColor(prev_frame, cv2.COLOR_BGR2GRAY)
            curr_gray = cv2.cvtColor(curr_frame, cv2.COLOR_BGR2GRAY)
            
            # 计算亮度差异
            diff = cv2.absdiff(curr_gray, prev_gray)
            
            # 计算平均亮度变化
            avg_diff = np.mean(diff)
            
            # 检测高亮度区域（爆炸、闪光）
            high_brightness = np.sum(curr_gray > 200) / curr_gray.size
            
            # 综合评分
            score = (avg_diff / 255.0) * 0.5 + high_brightness * 0.5
            
            if score > self.brightness_threshold / 255.0:
                time = i / fps
                brightness_flash_data.append({
                    "time": time,
                    "score": float(score),
                    "avg_diff": float(avg_diff),
                    "high_brightness_ratio": float(high_brightness)
                })
            
            # 更新进度
            if callback and i % 100 == 0:
                progress = 75 + int((i / len(frames)) * 25)  # 75-100%
                callback(progress, f"分析亮度闪烁: {i}/{len(frames)}")
        
        return brightness_flash_data
    
    def detect_vfx_segments(self, video_path: str, min_duration: float = 2.0,
                           callback: Optional[Callable] = None) -> List[Dict]:
        """
        检测特效片段并合并相邻帧
        
        Args:
            video_path: 视频文件路径
            min_duration: 最小片段时长（秒）
            callback: 进度回调
        
        Returns:
            特效片段列表
        """
        vfx_data = self.detect(video_path, callback)
        
        color_burst = vfx_data.get("color_burst", [])
        brightness_flash = vfx_data.get("brightness_flash", [])
        
        # 合并两种特效
        all_vfx = color_burst + brightness_flash
        all_vfx.sort(key=lambda x: x["time"])
        
        if not all_vfx:
            return []
        
        # 合并相邻帧（间隔小于0.5秒）
        segments = []
        current_segment = {
            "start": all_vfx[0]["time"],
            "end": all_vfx[0]["time"],
            "score": all_vfx[0]["score"]
        }
        
        for i in range(1, len(all_vfx)):
            vfx = all_vfx[i]
            
            if vfx["time"] - current_segment["end"] < 0.5:
                # 合并
                current_segment["end"] = vfx["time"]
                current_segment["score"] = max(current_segment["score"], vfx["score"])
            else:
                # 保存当前片段
                if current_segment["end"] - current_segment["start"] >= min_duration:
                    segments.append(current_segment)
                
                # 开始新片段
                current_segment = {
                    "start": vfx["time"],
                    "end": vfx["time"],
                    "score": vfx["score"]
                }
        
        # 保存最后一个片段
        if current_segment["end"] - current_segment["start"] >= min_duration:
            segments.append(current_segment)
        
        logger.info(f"检测到 {len(segments)} 个特效片段")
        return segments
    
    def _get_video_info(self, video_path: str) -> Dict:
        """获取视频信息"""
        try:
            # 构建 ffprobe 路径
            if self.ffmpeg_path and self.ffmpeg_path != "ffmpeg":
                ffprobe_path = str(Path(self.ffmpeg_path).parent / "ffprobe.exe")
            else:
                ffprobe_path = "ffprobe"
            
            cmd = [
                ffprobe_path,
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height,duration,r_frame_rate",
                "-show_entries", "format=duration",
                "-of", "json",
                video_path
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode == 0:
                data = json.loads(result.stdout)
                
                width = 1280
                height = 720
                fps = 30
                duration = 0
                
                if "streams" in data and data["streams"]:
                    stream = data["streams"][0]
                    width = stream.get("width", 1280)
                    height = stream.get("height", 720)
                    
                    fps_str = stream.get("r_frame_rate", "30/1")
                    if "/" in fps_str:
                        num, den = fps_str.split("/")
                        fps = float(num) / float(den) if float(den) != 0 else 30
                
                if "format" in data:
                    duration = float(data["format"].get("duration", 0))
                
                return {
                    "width": width,
                    "height": height,
                    "fps": fps,
                    "duration": duration
                }
        
        except Exception as e:
            logger.warning(f"获取视频信息失败: {e}")
        
        return {"width": 1280, "height": 720, "fps": 30, "duration": 0}
    
    def _detect_from_cache(self, cache_path: str, fcount: int, 
                           fw: int, fh: int, ffps: float,
                           callback: Optional[Callable] = None) -> Dict[str, List[Dict]]:
        """从共享帧缓存检测VFX（v3.0新增，避免重复解码）"""
        from core.shared_frame_extractor import SharedFrameCache
        sfc = SharedFrameCache.get_instance()
        frames = sfc.load_frames(cache_path, fcount, fw, fh)
        
        color_burst_data = []
        brightness_flash_data = []
        vfx_energy_data = []
        prev_frame = None
        prev_hsv = None
        color_change_history = []
        HISTORY_SIZE = 8
        
        for i in range(len(frames)):
            frame = frames[i]
            t = (i + 1) / ffps
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
            h_ch = hsv[:, :, 0]
            s_ch = hsv[:, :, 1]
            v_ch = hsv[:, :, 2]
            
            bright_saturated_mask = (v_ch > 160) & (s_ch > 80)
            bright_saturated_ratio = float(np.sum(bright_saturated_mask)) / bright_saturated_mask.size
            
            if prev_hsv is not None:
                # 色彩突变
                h_diff = np.abs(h_ch.astype(float) - prev_hsv[:, :, 0].astype(float))
                h_diff = np.minimum(h_diff, 180 - h_diff)  # 色相环形处理
                s_diff = np.abs(s_ch.astype(float) - prev_hsv[:, :, 1].astype(float))
                color_change = float(np.mean(h_diff) + np.mean(s_diff) * 0.5)
                
                if color_change > self.color_threshold:
                    color_burst_data.append({
                        "time": t, "color_change": color_change,
                        "hue_change": float(np.mean(h_diff)),
                        "sat_change": float(np.mean(s_diff)),
                        "bright_saturated_ratio": bright_saturated_ratio
                    })
                
                # 亮度闪烁
                v_diff = np.abs(v_ch.astype(float) - prev_hsv[:, :, 2].astype(float))
                brightness_change = float(np.mean(v_diff))
                
                if brightness_change > self.brightness_threshold:
                    brightness_flash_data.append({
                        "time": t, "brightness_change": brightness_change,
                        "bright_saturated_ratio": bright_saturated_ratio
                    })
                
                # VFX能量
                color_change_history.append(color_change)
                if len(color_change_history) > HISTORY_SIZE:
                    color_change_history.pop(0)
                
                gradual_energy = sum(color_change_history) / len(color_change_history) if color_change_history else 0
                vfx_energy = max(color_change * 0.6, gradual_energy * 0.4) + bright_saturated_ratio * 30
                vfx_energy_data.append({
                    "time": t, "vfx_energy": vfx_energy,
                    "color_burst": color_change, "brightness_flash": brightness_change,
                    "gradual_energy": gradual_energy,
                    "bright_saturated_ratio": bright_saturated_ratio
                })
            
            prev_frame = frame
            prev_hsv = hsv
            
            if callback and (i + 1) % 20 == 0:
                progress = int(min((i + 1) / len(frames) * 100, 99))
                callback(progress, f"VFX分析(缓存): {i+1}帧")
        
        del frames  # 释放mmap引用
        
        if callback:
            callback(100, f"VFX分析完成(缓存)")
        logger.info(f"VFX检测完成(共享缓存): color={len(color_burst_data)}, "
                   f"flash={len(brightness_flash_data)}, energy={len(vfx_energy_data)}")
        
        return {
            "color_burst": color_burst_data,
            "brightness_flash": brightness_flash_data,
            "vfx_energy": vfx_energy_data
        }


# 测试代码
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("用法: python vfx_detector.py <video_path>")
        sys.exit(1)
    
    video_path = sys.argv[1]
    detector = VFXDetector()
    
    def progress_callback(progress, message):
        print(f"[{progress}%] {message}")
    
    # 检测特效
    vfx_data = detector.detect(video_path, progress_callback)
    
    color_burst = vfx_data.get("color_burst", [])
    brightness_flash = vfx_data.get("brightness_flash", [])
    
    print(f"\n色彩突变: {len(color_burst)}个")
    print(f"亮度闪烁: {len(brightness_flash)}个")
    
    # 检测特效片段
    vfx_segments = detector.detect_vfx_segments(video_path, min_duration=2.0)
    print(f"\n特效片段: {len(vfx_segments)}个")
    for segment in vfx_segments[:5]:
        print(f"  {segment['start']:.2f}s - {segment['end']:.2f}s, 得分: {segment['score']:.3f}")
