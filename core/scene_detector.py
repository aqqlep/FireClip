"""
通道1: 场景切换检测（优化版）
使用FFmpeg的场景检测滤镜，优化输出和性能
"""
import subprocess
import re
from pathlib import Path
from typing import List, Dict, Callable, Optional
from utils.logger import logger
from config import CONFIG

# 预编译正则表达式
_PTS_PATTERN = re.compile(r"pts_time:([0-9.]+)")
_SCORE_PATTERN = re.compile(r"score:([0-9.]+)")


class SceneDetector:
    """场景切换检测器"""
    
    def __init__(self, threshold: float = 0.3):
        """
        初始化场景检测器
        
        Args:
            threshold: 场景切换阈值 (0.0-1.0)，默认0.3
        """
        self.threshold = threshold
        self.ffmpeg_path = CONFIG.ffmpeg_path
    
    def detect(self, video_path: str, callback: Optional[Callable] = None) -> List[Dict]:
        """
        检测视频中的场景切换点（优化版）
        
        Args:
            video_path: 视频文件路径
            callback: 进度回调函数 callback(progress: int, message: str)
        
        Returns:
            场景切换点列表 [{"time": float, "score": float}, ...]
        """
        logger.info(f"开始场景切换检测(优化版): {video_path}")
        
        if callback:
            callback(0, "正在分析场景切换...")
        
        process = None
        try:
            # 使用FFmpeg的场景检测滤镜（优化参数：仅输出必要信息）
            cmd = [
                self.ffmpeg_path,
                "-i", video_path,
                "-vf", f"select='gt(scene,{self.threshold})',showinfo",
                "-f", "null",
                "-nostats",
                "-loglevel", "info",
                "-"
            ]
            
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                encoding='utf-8',
                errors='replace',
                bufsize=1  # 行缓冲
            )
            
            scenes = []
            total_duration = self._get_video_duration(video_path)
            
            # 解析FFmpeg输出（使用预编译正则）
            while True:
                line = process.stderr.readline()
                if not line and process.poll() is not None:
                    break
                
                if "showinfo" in line and "pts_time:" in line:
                    try:
                        pts_match = _PTS_PATTERN.search(line)
                        if not pts_match:
                            continue
                        scene_time = float(pts_match.group(1))
                        
                        score_match = _SCORE_PATTERN.search(line)
                        if score_match:
                            score = float(score_match.group(1))
                        else:
                            score = self.threshold + 0.1
                        
                        scenes.append({
                            "time": scene_time,
                            "score": score
                        })
                        
                        # 每 5 个场景点报告一次进度（避免过度回调）
                        if callback and len(scenes) % 5 == 0 and total_duration > 0:
                            progress = int(min((scene_time / total_duration) * 100, 99))
                            callback(progress, f"检测到场景切换: {len(scenes)}个")
                    
                    except (ValueError, IndexError) as e:
                        logger.warning(f"解析场景信息失败: {e}")
                        continue
            
            process.wait()
            
            if callback:
                callback(100, f"场景切换检测完成: {len(scenes)}个")
            
            logger.info(f"场景切换检测完成: {len(scenes)}个切换点")
            return scenes
        
        except Exception as e:
            logger.error(f"场景切换检测失败: {e}")
            if callback:
                callback(100, f"检测失败: {str(e)}")
            return []
        finally:
            if process is not None:
                try:
                    process.stderr.close()
                except Exception:
                    pass
                try:
                    process.wait(timeout=3)
                except Exception:
                    try:
                        process.kill()
                    except Exception:
                        pass
    
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
    
    def detect_with_segments(self, video_path: str, min_duration: float = 3.0,
                            callback: Optional[Callable] = None) -> List[Dict]:
        """
        检测场景切换并生成片段
        
        Args:
            video_path: 视频文件路径
            min_duration: 最小时长（秒）
            callback: 进度回调函数
        
        Returns:
            场景片段列表 [{"start": float, "end": float, "score": float}, ...]
        """
        scenes = self.detect(video_path, callback)
        
        if not scenes:
            return []
        
        # 获取视频总时长
        total_duration = self._get_video_duration(video_path)
        if total_duration <= 0:
            return []
        
        # 生成片段
        segments = []
        prev_time = 0.0
        
        for scene in scenes:
            scene_time = scene["time"]
            duration = scene_time - prev_time
            
            if duration >= min_duration:
                segments.append({
                    "start": prev_time,
                    "end": scene_time,
                    "score": scene["score"]
                })
            
            prev_time = scene_time
        
        # 最后一个片段
        if total_duration - prev_time >= min_duration:
            segments.append({
                "start": prev_time,
                "end": total_duration,
                "score": 0.5  # 默认得分
            })
        
        logger.info(f"生成 {len(segments)} 个场景片段")
        return segments


# 测试代码
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("用法: python scene_detector.py <video_path>")
        sys.exit(1)
    
    video_path = sys.argv[1]
    detector = SceneDetector(threshold=0.3)
    
    def progress_callback(progress, message):
        print(f"[{progress}%] {message}")
    
    scenes = detector.detect(video_path, progress_callback)
    print(f"\n检测到 {len(scenes)} 个场景切换点:")
    for scene in scenes:
        print(f"  时间: {scene['time']:.2f}s, 得分: {scene['score']:.3f}")
