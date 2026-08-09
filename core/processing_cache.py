# -*- coding: utf-8 -*-
"""
FFmpeg处理优化模块
提供智能FFmpeg参数选择、硬件加速检测和缓存复用机制
"""

import os
import json
import hashlib
import threading
import time
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from utils.logger import logger


@dataclass
class ProcessingCacheEntry:
    """处理缓存条目"""
    key: str
    output_path: str
    timestamp: float
    size_bytes: int
    params: Dict[str, Any] = field(default_factory=dict)
    access_count: int = 0
    last_access: float = 0.0


class ProcessingCache:
    """
    处理结果缓存
    
    功能：
    - 缓存FFmpeg处理结果
    - 避免重复处理相同的视频片段
    - LRU缓存清理策略
    - 跨会话持久化缓存
    """
    
    def __init__(self, cache_dir: str = "cache", max_size_mb: int = 2048):
        self.cache_dir = cache_dir
        self.max_size_bytes = max_size_mb * 1024 * 1024
        self._entries: Dict[str, ProcessingCacheEntry] = {}
        self._lock = threading.Lock()
        self._index_file = os.path.join(cache_dir, "cache_index.json")
        
        os.makedirs(cache_dir, exist_ok=True)
        self._load_index()
    
    @staticmethod
    def generate_key(video_path: str, start_time: float, end_time: float, params: Dict[str, Any]) -> str:
        """生成缓存键"""
        try:
            stat = os.stat(video_path)
            file_id = f"{stat.st_size}_{stat.st_mtime}"
        except Exception:
            file_id = video_path
        
        key_string = f"{file_id}_{start_time}_{end_time}_{json.dumps(params, sort_keys=True)}"
        return hashlib.md5(key_string.encode()).hexdigest()
    
    def get(self, key: str) -> Optional[str]:
        """从缓存获取输出路径"""
        with self._lock:
            if key in self._entries:
                entry = self._entries[key]
                if os.path.exists(entry.output_path):
                    entry.access_count += 1
                    entry.last_access = time.time()
                    logger.info(f"缓存命中: {key[:16]}...")
                    return entry.output_path
                else:
                    del self._entries[key]
                    self._save_index()
            return None
    
    def put(self, key: str, output_path: str, params: Optional[Dict[str, Any]] = None) -> None:
        """存入缓存"""
        with self._lock:
            try:
                size = os.path.getsize(output_path)
            except Exception:
                size = 0
            
            entry = ProcessingCacheEntry(
                key=key,
                output_path=output_path,
                timestamp=time.time(),
                size_bytes=size,
                params=params or {},
                access_count=1,
                last_access=time.time()
            )
            
            self._entries[key] = entry
            self._save_index()
            self._check_cache_size()
            logger.info(f"缓存已添加: {key[:16]}... ({size / 1024 / 1024:.2f} MB)")
    
    def _check_cache_size(self) -> None:
        """检查并清理缓存大小"""
        total_size = sum(e.size_bytes for e in self._entries.values())
        
        if total_size > self.max_size_bytes:
            sorted_entries = sorted(
                self._entries.values(),
                key=lambda e: e.last_access
            )
            
            for entry in sorted_entries:
                if total_size <= self.max_size_bytes * 0.7:
                    break
                
                try:
                    if os.path.exists(entry.output_path):
                        os.remove(entry.output_path)
                    total_size -= entry.size_bytes
                    del self._entries[entry.key]
                    logger.info(f"缓存清理: {entry.key[:16]}...")
                except Exception as e:
                    logger.warning(f"缓存清理失败: {e}")
            
            self._save_index()
    
    def clear(self) -> None:
        """清空所有缓存"""
        with self._lock:
            for entry in self._entries.values():
                try:
                    if os.path.exists(entry.output_path):
                        os.remove(entry.output_path)
                except Exception:
                    pass
            self._entries.clear()
            self._save_index()
            logger.info("所有缓存已清空")
    
    def _load_index(self) -> None:
        """加载缓存索引"""
        if os.path.exists(self._index_file):
            try:
                with open(self._index_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for key, entry_data in data.items():
                        if os.path.exists(entry_data.get("output_path", "")):
                            self._entries[key] = ProcessingCacheEntry(**entry_data)
                logger.info(f"加载缓存索引: {len(self._entries)} 个条目")
            except Exception as e:
                logger.warning(f"加载缓存索引失败: {e}")
    
    def _save_index(self) -> None:
        """保存缓存索引"""
        try:
            data = {
                key: {
                    "key": e.key,
                    "output_path": e.output_path,
                    "timestamp": e.timestamp,
                    "size_bytes": e.size_bytes,
                    "params": e.params,
                    "access_count": e.access_count,
                    "last_access": e.last_access,
                }
                for key, e in self._entries.items()
            }
            with open(self._index_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"保存缓存索引失败: {e}")
    
    def get_cache_info(self) -> Dict[str, Any]:
        """获取缓存统计信息"""
        with self._lock:
            total_size = sum(e.size_bytes for e in self._entries.values())
            return {
                "count": len(self._entries),
                "total_size_mb": total_size / (1024 * 1024),
                "total_hits": sum(e.access_count for e in self._entries.values()),
            }


class FFmpegOptimizer:
    """
    FFmpeg参数优化器
    
    功能：
    - 检测可用的硬件编码器
    - 根据视频参数智能选择编码参数
    - 生成最优的FFmpeg命令
    """
    
    def __init__(self):
        self._hardware_info = None
        self._lock = threading.Lock()
    
    def detect_hardware(self, ffmpeg_path: str = "ffmpeg") -> Dict[str, Any]:
        """检测硬件加速能力"""
        with self._lock:
            if self._hardware_info is not None:
                return self._hardware_info
            
            info = {
                "ffmpeg_path": ffmpeg_path,
                "encoders": [],
                "hwaccels": [],
                "has_nvenc": False,
                "has_qsv": False,
                "has_amf": False,
                "has_videotoolbox": False,
            }
            
            try:
                import subprocess
                
                try:
                    result = subprocess.run(
                        [ffmpeg_path, "-hide_banner", "-hwaccels"],
                        capture_output=True,
                        text=True,
                        timeout=10
                    )
                    if result.returncode == 0:
                        hwaccels = [
                            line.strip()
                            for line in result.stdout.strip().split("\n")[1:]
                            if line.strip() and not line.startswith("Hardware")
                        ]
                        info["hwaccels"] = hwaccels
                        info["has_nvenc"] = any(
                            "cuda" in hw.lower() or "nvenc" in hw.lower()
                            for hw in hwaccels
                        )
                        info["has_qsv"] = any("qsv" in hw.lower() for hw in hwaccels)
                except Exception as e:
                    logger.warning(f"硬件加速检测失败: {e}")
                
                try:
                    result = subprocess.run(
                        [ffmpeg_path, "-hide_banner", "-encoders"],
                        capture_output=True,
                        text=True,
                        timeout=10
                    )
                    if result.returncode == 0:
                        for line in result.stdout.split("\n"):
                            if "h264" in line.lower() or "hevc" in line.lower():
                                if "_nvenc" in line.lower():
                                    info["has_nvenc"] = True
                                if "_qsv" in line.lower():
                                    info["has_qsv"] = True
                                if "_amf" in line.lower():
                                    info["has_amf"] = True
                                info["encoders"].append(line.strip())
                except Exception:
                    pass
                
                logger.info(
                    f"硬件检测: NVENC={info['has_nvenc']}, "
                    f"QSV={info['has_qsv']}, AMF={info['has_amf']}"
                )
                
            except Exception as e:
                logger.warning(f"硬件检测失败: {e}")
            
            self._hardware_info = info
            return info
    
    def get_optimized_encode_params(self, quality: str = "balanced", target_resolution: Optional[str] = None) -> List[str]:
        """获取优化的编码参数
        
        Args:
            quality: 质量级别 ("fast", "balanced", "high")
            target_resolution: 目标分辨率 (如 "1920x1080")
            
        Returns:
            FFmpeg参数列表
        """
        hw = self.detect_hardware()
        params = []
        
        quality_presets = {
            "fast": {"preset": "ultrafast", "crf": "28", "tune": "zerolatency"},
            "balanced": {"preset": "medium", "crf": "23", "tune": "film"},
            "high": {"preset": "slow", "crf": "20", "tune": "film"},
        }
        preset = quality_presets.get(quality, quality_presets["balanced"])
        
        if hw["has_nvenc"]:
            params.extend(["-c:v", "h264_nvenc"])
            params.extend(["-preset", preset["preset"]])
            params.extend(["-rc", "vbr"])
            params.extend(["-cq", preset["crf"]])
        elif hw["has_qsv"]:
            params.extend(["-c:v", "h264_qsv"])
            params.extend(["-preset", preset["preset"]])
            params.extend(["-global_quality", preset["crf"]])
        else:
            params.extend(["-c:v", "libx264"])
            params.extend(["-preset", preset["preset"]])
            params.extend(["-crf", preset["crf"]])
            params.extend(["-tune", preset["tune"]])
        
        params.extend(["-pix_fmt", "yuv420p"])
        params.extend(["-c:a", "aac"])
        params.extend(["-b:a", "128k"])
        params.extend(["-movflags", "+faststart"])
        params.extend(["-threads", "0"])
        
        return params
    
    def get_optimized_extract_params(self) -> List[str]:
        """获取优化的片段提取参数（快速，无重新编码）"""
        return [
            "-c:v", "copy",
            "-c:a", "copy",
            "-avoid_negative_ts", "make_zero",
        ]


_global_processing_cache: Optional[ProcessingCache] = None
_global_ffmpeg_optimizer: Optional[FFmpegOptimizer] = None


def get_processing_cache() -> ProcessingCache:
    """获取全局处理缓存"""
    global _global_processing_cache
    if _global_processing_cache is None:
        _global_processing_cache = ProcessingCache()
    return _global_processing_cache


def get_ffmpeg_optimizer() -> FFmpegOptimizer:
    """获取全局FFmpeg优化器"""
    global _global_ffmpeg_optimizer
    if _global_ffmpeg_optimizer is None:
        _global_ffmpeg_optimizer = FFmpegOptimizer()
    return _global_ffmpeg_optimizer
