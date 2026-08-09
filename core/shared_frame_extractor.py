"""
共享帧提取器 v3.0
单次ffmpeg解码，为多个分析通道提供帧数据缓存
避免同一视频被MotionAnalyzer、VFXDetector等重复解码

核心思路：
- 一次解码出低分辨率BGR帧序列（4fps, 320px宽）
- 帧数据存为临时numpy数组文件（mmap映射，零拷贝）
- 各分析通道从缓存读取，不再各自调用ffmpeg
"""
import os
import subprocess
import tempfile
import hashlib
import numpy as np
from typing import Optional, Tuple
from utils.logger import logger
from config import CONFIG


class SharedFrameCache:
    """共享帧缓存 - 单次解码多通道复用"""
    
    _instance = None
    _cache_dir = None
    
    def __init__(self):
        self._frames_cache = {}  # {video_hash: {'path': str, 'count': int, 'width': int, 'height': int, 'fps': float}}
    
    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = SharedFrameCache()
        return cls._instance
    
    @staticmethod
    def _video_hash(video_path: str) -> str:
        """基于文件路径+大小+修改时间的哈希（快速，无需读整个文件）"""
        stat = os.stat(video_path)
        key = f"{video_path}:{stat.st_size}:{stat.st_mtime}"
        return hashlib.md5(key.encode()).hexdigest()[:12]
    
    @staticmethod
    def _get_cache_dir() -> str:
        """获取缓存目录"""
        if SharedFrameCache._cache_dir is None:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            cache_dir = os.path.join(project_root, "cache", "shared_frames")
            os.makedirs(cache_dir, exist_ok=True)
            SharedFrameCache._cache_dir = cache_dir
        return SharedFrameCache._cache_dir
    
    def extract_frames(self, video_path: str, 
                       target_fps: float = 4.0,
                       target_width: int = 320) -> Tuple[str, int, int, int, float]:
        """
        提取视频帧到缓存文件（如果已存在则直接复用）
        
        Returns:
            (cache_path, frame_count, width, height, actual_fps)
        """
        vid_hash = self._video_hash(video_path)
        cache_key = f"{vid_hash}_{target_fps}fps_{target_width}w"
        
        # 检查是否已有缓存
        if cache_key in self._frames_cache:
            info = self._frames_cache[cache_key]
            cache_path = info['path']
            if os.path.exists(cache_path):
                logger.info(f"复用帧缓存: {cache_path} ({info['count']}帧)")
                return cache_path, info['count'], info['width'], info['height'], info['fps']
        
        # 检查磁盘缓存
        cache_dir = self._get_cache_dir()
        cache_path = os.path.join(cache_dir, f"{cache_key}.bin")
        meta_path = os.path.join(cache_dir, f"{cache_key}.meta")
        
        if os.path.exists(cache_path) and os.path.exists(meta_path):
            try:
                with open(meta_path, 'r') as f:
                    lines = f.read().strip().split('\n')
                    count = int(lines[0])
                    w = int(lines[1])
                    h = int(lines[2])
                    fps = float(lines[3])
                self._frames_cache[cache_key] = {
                    'path': cache_path, 'count': count,
                    'width': w, 'height': h, 'fps': fps
                }
                logger.info(f"从磁盘加载帧缓存: {cache_path} ({count}帧)")
                return cache_path, count, w, h, fps
            except Exception:
                pass
        
        # 需要重新提取
        logger.info(f"提取视频帧到缓存: {video_path} ({target_fps}fps, {target_width}px宽)")
        
        # 获取视频信息
        duration = 0
        try:
            cmd = [CONFIG.ffmpeg_path.replace('ffmpeg.exe', 'ffprobe.exe')
                   if CONFIG.ffmpeg_path.endswith('ffmpeg.exe') else 'ffprobe',
                   "-v", "quiet", "-print_format", "json",
                   "-show_format", "-show_streams", video_path]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            import json
            info = json.loads(result.stdout)
            duration = float(info.get('format', {}).get('duration', 0))
            for stream in info.get('streams', []):
                if stream.get('codec_type') == 'video':
                    orig_w = int(stream.get('width', 1280))
                    orig_h = int(stream.get('height', 720))
                    break
        except Exception:
            duration = 0
            orig_w, orig_h = 1280, 720
        
        # 计算目标高度（保持宽高比）
        target_height = int(target_width * orig_h / orig_w)
        # 确保高度是偶数
        target_height = target_height + (target_height % 2)
        
        # ffmpeg提取BGR24帧
        cmd = [
            CONFIG.ffmpeg_path,
            "-nostats", "-loglevel", "error",
            "-i", video_path,
            "-vf", f"fps={target_fps},scale={target_width}:{target_height}",
            "-f", "rawvideo",
            "-pix_fmt", "bgr24",
            "-"
        ]
        
        frame_size = target_width * target_height * 3
        frame_count = 0
        proc = None
        
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                bufsize=frame_size * 5
            )
            
            # 写入缓存文件
            with open(cache_path, 'wb') as f:
                while True:
                    chunk = proc.stdout.read(frame_size)
                    if len(chunk) < frame_size:
                        break
                    f.write(chunk)
                    frame_count += 1
            
            # 写入元数据
            with open(meta_path, 'w') as f:
                f.write(f"{frame_count}\n{target_width}\n{target_height}\n{target_fps}\n")
            
            self._frames_cache[cache_key] = {
                'path': cache_path, 'count': frame_count,
                'width': target_width, 'height': target_height,
                'fps': target_fps
            }
            
            file_size_mb = os.path.getsize(cache_path) / (1024 * 1024)
            logger.info(f"帧缓存创建完成: {frame_count}帧, {target_width}x{target_height}, "
                       f"大小={file_size_mb:.1f}MB")
            
            return cache_path, frame_count, target_width, target_height, target_fps
            
        except Exception as e:
            logger.error(f"帧缓存提取失败: {e}")
            return "", 0, 0, 0, 0.0
        finally:
            if proc is not None:
                try:
                    proc.stdout.close()
                except Exception:
                    pass
                try:
                    proc.wait(timeout=5)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
    
    def load_frames(self, cache_path: str, frame_count: int, 
                    width: int, height: int) -> np.ndarray:
        """
        从缓存加载帧数据（使用mmap零拷贝）
        
        Returns:
            np.ndarray shape=(frame_count, height, width, 3) dtype=uint8
        """
        if not os.path.exists(cache_path):
            raise FileNotFoundError(f"帧缓存不存在: {cache_path}")
        
        # 使用numpy mmap读取（零拷贝，不占用额外内存）
        data = np.memmap(cache_path, dtype=np.uint8, mode='r')
        expected_size = frame_count * width * height * 3
        if len(data) >= expected_size:
            frames = data[:expected_size].reshape(frame_count, height, width, 3)
        else:
            # 缓存不完整，返回可用部分
            actual_count = len(data) // (width * height * 3)
            frames = data[:actual_count * width * height * 3].reshape(
                actual_count, height, width, 3)
            logger.warning(f"帧缓存不完整: 期望{frame_count}帧, 实际{actual_count}帧")
        
        return frames
    
    def clear_cache(self):
        """清理所有帧缓存"""
        cache_dir = self._get_cache_dir()
        cleared = 0
        try:
            for f in os.listdir(cache_dir):
                if f.endswith('.bin') or f.endswith('.meta'):
                    os.remove(os.path.join(cache_dir, f))
                    cleared += 1
        except Exception as e:
            logger.warning(f"清理帧缓存失败: {e}")
        self._frames_cache.clear()
        logger.info(f"帧缓存已清理: 删除{cleared}个文件")
