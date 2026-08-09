"""
辅助工具函数
"""
import subprocess
import json
import os
import shutil
from pathlib import Path
from typing import Optional, Dict


def format_time(seconds: float) -> str:
    """将秒数格式化为 HH:MM:SS.mmm"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:06.3f}"


def format_time_short(seconds: float) -> str:
    """将秒数格式化为 MM:SS"""
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes:02d}:{secs:02d}"


def format_file_size(size_bytes: int) -> str:
    """格式化文件大小"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 ** 2:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 ** 3:
        return f"{size_bytes / (1024 ** 2):.1f} MB"
    else:
        return f"{size_bytes / (1024 ** 3):.2f} GB"


def get_video_info(video_path: str, ffmpeg_path: str = "ffmpeg") -> Dict:
    """获取视频信息"""
    try:
        # 构建 ffprobe 路径
        if ffmpeg_path and ffmpeg_path != "ffmpeg":
            # ffmpeg_path 是完整路径，提取目录并构建 ffprobe 路径
            ffmpeg_dir = Path(ffmpeg_path).parent
            ffprobe_path = str(ffmpeg_dir / "ffprobe.exe")
        else:
            ffprobe_path = "ffprobe"
        
        cmd = [
            ffprobe_path, "-v", "quiet",
            "-print_format", "json",
            "-show_format", "-show_streams",
            video_path
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=30
        )
        if result.returncode != 0:
            return {}
        
        data = json.loads(result.stdout)
        info = {
            "duration": float(data.get("format", {}).get("duration", 0)),
            "size": int(data.get("format", {}).get("size", 0)),
            "format": data.get("format", {}).get("format_name", ""),
            "bitrate": int(data.get("format", {}).get("bit_rate", 0)),
        }
        
        for stream in data.get("streams", []):
            if stream.get("codec_type") == "video":
                info["width"] = stream.get("width", 0)
                info["height"] = stream.get("height", 0)
                info["codec"] = stream.get("codec_name", "")
                fps_str = stream.get("r_frame_rate", "30/1")
                try:
                    num, den = fps_str.split("/")
                    info["fps"] = float(num) / float(den) if float(den) != 0 else 30.0
                except:
                    info["fps"] = 30.0
            elif stream.get("codec_type") == "audio":
                info["audio_codec"] = stream.get("codec_name", "")
                info["audio_sample_rate"] = stream.get("sample_rate", "")
                info["audio_channels"] = stream.get("channels", 0)
        
        return info
    except Exception as e:
        from utils.logger import logger
        logger.error(f"获取视频信息失败: {e}")
        return {}


def check_ffmpeg(ffmpeg_path: str = "ffmpeg") -> bool:
    """检查FFmpeg是否可用"""
    try:
        result = subprocess.run(
            [ffmpeg_path, "-version"],
            capture_output=True, text=True, timeout=5
        )
        return result.returncode == 0
    except:
        return False


def check_ffprobe() -> bool:
    """检查FFprobe是否可用"""
    try:
        result = subprocess.run(
            ["ffprobe", "-version"],
            capture_output=True, text=True, timeout=5
        )
        return result.returncode == 0
    except:
        return False


def detect_hardware_capabilities() -> dict:
    """检测硬件能力"""
    capabilities = {
        "gpu_available": False,
        "gpu_name": None,
        "gpu_memory_mb": 0,
        "nvenc_available": False,
        "cuda_version": None,
        "recommended_preset": "medium",
        "max_batch_size": 4,
    }
    
    # 检测NVIDIA GPU
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            gpu_info = result.stdout.strip().split(",")
            capabilities["gpu_available"] = True
            capabilities["gpu_name"] = gpu_info[0].strip()
            mem_str = gpu_info[1].strip()
            capabilities["gpu_memory_mb"] = int(mem_str.split()[0])
            
            if capabilities["gpu_memory_mb"] >= 12000:
                capabilities["recommended_preset"] = "high"
                capabilities["max_batch_size"] = 8
            elif capabilities["gpu_memory_mb"] >= 6000:
                capabilities["recommended_preset"] = "medium"
                capabilities["max_batch_size"] = 4
            else:
                capabilities["recommended_preset"] = "low"
                capabilities["max_batch_size"] = 2
    except:
        pass
    
    # 检测FFmpeg NVENC支持
    try:
        result = subprocess.run(
            ["ffmpeg", "-encoders"],
            capture_output=True, text=True, timeout=5
        )
        if "h264_nvenc" in result.stdout:
            capabilities["nvenc_available"] = True
    except:
        pass
    
    # 检测CUDA
    try:
        import torch
        if torch.cuda.is_available():
            capabilities["cuda_version"] = torch.version.cuda
    except:
        pass
    
    return capabilities


def check_disk_space(path: str, required_gb: float = 5.0) -> bool:
    """检查磁盘空间"""
    try:
        stat = shutil.disk_usage(path)
        free_gb = stat.free / (1024 ** 3)
        return free_gb >= required_gb
    except:
        return True


def get_cache_size(cache_dir: str) -> float:
    """获取缓存目录大小（GB）"""
    total = 0
    cache_path = Path(cache_dir)
    if cache_path.exists():
        for f in cache_path.rglob("*"):
            if f.is_file():
                total += f.stat().st_size
    return total / (1024 ** 3)


def safe_filename(name: str) -> str:
    """清理文件名，移除非法字符"""
    invalid = '<>:"/\\|?*'
    for ch in invalid:
        name = name.replace(ch, '_')
    return name.strip()


def format_size(size_bytes: int) -> str:
    """format_file_size的别名，统一API"""
    return format_file_size(size_bytes)


def get_ffmpeg_path() -> str:
    """
    获取ffmpeg可执行路径（v3.0公共工具）
    优先级: 便携版 > CONFIG配置 > 系统 PATH
    
    多个模块重复此逻辑：MotionAnalyzer, VFXDetector, SmartClip, 
    UnifiedVideoPipeline 等。统一到此函数。
    """
    # 1. 便携版
    project_root = Path(__file__).parent.parent
    portable = project_root / 'ffmpeg' / 'bin' / 'ffmpeg.exe'
    if portable.exists():
        return str(portable)
    
    # 2. CONFIG 配置
    try:
        from config import CONFIG
        if CONFIG.ffmpeg_path and Path(CONFIG.ffmpeg_path).exists():
            return CONFIG.ffmpeg_path
    except Exception:
        pass
    
    # 3. 系统 PATH
    return 'ffmpeg'


def get_ffprobe_path() -> str:
    """获取ffprobe可执行路径（v3.0公共工具）"""
    ffmpeg = get_ffmpeg_path()
    if ffmpeg and ffmpeg != 'ffmpeg':
        return str(Path(ffmpeg).parent / 'ffprobe.exe')
    return 'ffprobe'


def get_video_info_cached(video_path: str) -> Dict:
    """
    带缓存的视频信息获取（v3.0公共工具）
    同一视频不重复调用ffprobe
    """
    if not hasattr(get_video_info_cached, '_cache'):
        get_video_info_cached._cache = {}
    
    # 基于路径+大小+修改时间的缓存键
    try:
        stat = os.stat(video_path)
        cache_key = f"{video_path}:{stat.st_size}:{int(stat.st_mtime)}"
    except Exception:
        cache_key = video_path
    
    if cache_key in get_video_info_cached._cache:
        return get_video_info_cached._cache[cache_key]
    
    result = get_video_info(video_path, get_ffmpeg_path())
    get_video_info_cached._cache[cache_key] = result
    
    # 限制缓存大小
    if len(get_video_info_cached._cache) > 10:
        oldest_key = next(iter(get_video_info_cached._cache))
        del get_video_info_cached._cache[oldest_key]
    
    return result

