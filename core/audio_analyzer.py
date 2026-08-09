"""
通道3: 音频能量分析
使用FFmpeg提取音频并分析能量分布
"""
import subprocess
import json
import numpy as np
from pathlib import Path
from typing import List, Dict, Callable, Optional
from utils.logger import logger
from config import CONFIG


class AudioAnalyzer:
    """音频能量分析器"""
    
    def __init__(self, sample_rate: int = 16000, window_size: float = 0.5):
        """
        初始化音频分析器（优化版：降低采样率+增大窗口，提升性能）
        
        Args:
            sample_rate: 采样率，默认16000Hz（能量分析不需要高采样率）
            window_size: 分析窗口大小（秒），默认0.5秒
        """
        self.sample_rate = sample_rate
        self.window_size = window_size
        self.ffmpeg_path = CONFIG.ffmpeg_path
    
    def analyze(self, video_path: str, callback: Optional[Callable] = None) -> List[Dict]:
        """
        分析视频音频能量（优化版：批量处理+大缓冲区）
        
        Args:
            video_path: 视频文件路径
            callback: 进度回调函数 callback(progress: int, message: str)
        
        Returns:
            音频能量数据列表 [{"time": float, "energy": float, "rms": float}, ...]
        """
        logger.info(f"开始音频能量分析(优化版): {video_path}")
        
        if callback:
            callback(0, "正在提取音频...")
        
        process = None
        try:
            # 使用FFmpeg提取原始音频数据（低采样率=更快）
            cmd = [
                self.ffmpeg_path,
                "-i", video_path,
                "-vn",
                "-acodec", "pcm_s16le",
                "-ar", str(self.sample_rate),
                "-ac", "1",
                "-f", "s16le",
                "-nostats",
                "-loglevel", "error",
                "-"
            ]
            
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=4 * 1024 * 1024  # 4MB 缓冲区
            )
            
            if callback:
                callback(30, "正在分析音频能量...")
            
            # 计算窗口大小
            window_samples = int(self.window_size * self.sample_rate)
            chunk_bytes = window_samples * 2  # 16位 = 2字节
            
            energy_data = []
            current_time = 0.0
            
            # 批量读取：一次读取 20 个窗口（10 秒视频）
            bulk_bytes = chunk_bytes * 20
            buffer = b''
            
            while True:
                data = process.stdout.read(bulk_bytes)
                if not data:
                    break
                buffer += data
                
                # 处理所有完整窗口
                while len(buffer) >= chunk_bytes:
                    chunk = buffer[:chunk_bytes]
                    buffer = buffer[chunk_bytes:]
                    
                    # 向量化计算
                    samples = np.frombuffer(chunk, dtype=np.int16)
                    
                    # RMS 计算（浮点精度足够）
                    rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2))) / 32768.0
                    
                    # 计算能量（dB）
                    energy = 20 * np.log10(rms + 1e-10)
                    
                    energy_data.append({
                        "time": float(current_time),
                        "energy": float(energy),
                        "rms": float(rms)
                    })
                    
                    current_time += self.window_size
                
                # 每 20 个窗口报告进度
                if callback and len(energy_data) % 20 == 0:
                    callback(30 + min(60, int(len(energy_data) * self.window_size / 60)),
                            f"分析音频: {len(energy_data)}窗口")
            
            if callback:
                callback(100, f"音频能量分析完成: {len(energy_data)}个数据点")
            
            logger.info(f"音频能量分析完成: {len(energy_data)}个数据点, 总时长: {current_time:.0f}秒")
            return energy_data
        
        except Exception as e:
            logger.error(f"音频能量分析失败: {e}")
            if callback:
                callback(100, f"分析失败: {str(e)}")
            return []
        finally:
            if process is not None:
                try:
                    process.stdout.close()
                except Exception:
                    pass
                try:
                    process.wait(timeout=5)
                except Exception:
                    try:
                        process.kill()
                    except Exception:
                        pass
    
    def analyze_segments(self, video_path: str, segment_duration: float = 5.0,
                        callback: Optional[Callable] = None) -> List[Dict]:
        """
        按时间段分析音频能量
        
        Args:
            video_path: 视频文件路径
            segment_duration: 每段时长（秒）
            callback: 进度回调函数
        
        Returns:
            音频能量段列表 [{"start": float, "end": float, "avg_energy": float, "max_energy": float}, ...]
        """
        energy_data = self.analyze(video_path, callback)
        
        if not energy_data:
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
            segment_energy = [
                e for e in energy_data
                if current_start <= e["time"] < current_end
            ]
            
            if segment_energy:
                energies = [e["energy"] for e in segment_energy]
                avg_energy = sum(energies) / len(energies)
                max_energy = max(energies)
                
                segments.append({
                    "start": current_start,
                    "end": current_end,
                    "avg_energy": avg_energy,
                    "max_energy": max_energy
                })
            else:
                segments.append({
                    "start": current_start,
                    "end": current_end,
                    "avg_energy": -100.0,  # 静音
                    "max_energy": -100.0
                })
            
            current_start = current_end
        
        logger.info(f"生成 {len(segments)} 个音频能量段")
        return segments
    
    def detect_high_energy_segments(self, video_path: str, threshold_percentile: float = 85,
                                   segment_duration: float = 5.0,
                                   callback: Optional[Callable] = None) -> List[Dict]:
        """
        检测高能量音频片段
        
        Args:
            video_path: 视频文件路径
            threshold_percentile: 阈值百分位（0-100）
            segment_duration: 每段时长（秒）
            callback: 进度回调函数
        
        Returns:
            高能量片段列表 [{"start": float, "end": float, "score": float}, ...]
        """
        segments = self.analyze_segments(video_path, segment_duration, callback)
        
        if not segments:
            return []
        
        # 计算阈值
        energies = [s["avg_energy"] for s in segments if s["avg_energy"] > -100]
        if not energies:
            return []
        
        energies.sort()
        threshold_index = int(len(energies) * threshold_percentile / 100)
        threshold = energies[min(threshold_index, len(energies) - 1)]
        
        # 筛选高能量片段
        high_energy_segments = []
        for segment in segments:
            if segment["avg_energy"] >= threshold:
                # 归一化得分
                max_possible = 0.0  # 最大可能能量（0dB）
                score = min((segment["avg_energy"] - threshold) / (max_possible - threshold + 1e-10), 1.0)
                score = max(0.0, score)
                
                high_energy_segments.append({
                    "start": segment["start"],
                    "end": segment["end"],
                    "score": score
                })
        
        logger.info(f"检测到 {len(high_energy_segments)} 个高能量片段")
        return high_energy_segments
    
    def _get_video_duration(self, video_path: str) -> float:
        """获取视频时长"""
        try:
            # 构建 ffprobe 路径
            if self.ffmpeg_path and self.ffmpeg_path != "ffmpeg":
                ffprobe_path = str(Path(self.ffmpeg_path).parent / "ffprobe.exe")
            else:
                ffprobe_path = "ffprobe"
            
            cmd = [
                ffprobe_path,
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                video_path
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode == 0:
                return float(result.stdout.strip())
        
        except Exception as e:
            logger.warning(f"获取视频时长失败: {e}")
        
        return 0.0


# 测试代码
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("用法: python audio_analyzer.py <video_path>")
        sys.exit(1)
    
    video_path = sys.argv[1]
    analyzer = AudioAnalyzer()
    
    def progress_callback(progress, message):
        print(f"[{progress}%] {message}")
    
    # 分析音频能量
    energy_data = analyzer.analyze(video_path, progress_callback)
    print(f"\n分析完成: {len(energy_data)}个数据点")
    
    # 检测高能量片段
    high_energy = analyzer.detect_high_energy_segments(video_path, threshold_percentile=85)
    print(f"\n高能量片段: {len(high_energy)}个")
    for segment in high_energy[:5]:  # 只显示前5个
        print(f"  {segment['start']:.2f}s - {segment['end']:.2f}s, 得分: {segment['score']:.3f}")
