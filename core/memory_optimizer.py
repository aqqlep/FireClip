"""
内存与显存优化模块
提供智能内存管理、缓存清理和显存控制
"""

import gc
import sys
import threading
import time
from dataclasses import dataclass
from typing import Optional, Callable, Dict, Any
from utils.logger import logger


@dataclass
class MemorySnapshot:
    """内存快照"""
    timestamp: float
    memory_mb: float
    gpu_memory_mb: Optional[float] = None


class MemoryOptimizer:
    """
    内存管理器
    
    功能：
    - 监控和管理应用程序的内存使用情况
    - 智能清理缓存和临时数据
    - GPU显存管理（如PyTorch模型加载时使用）
    - 防止内存泄漏检测
    """
    
    def __init__(self, max_memory_percent: float = 80.0, max_gpu_memory_percent: float = 75.0):
        self.max_memory_percent = max_memory_percent
        self.max_gpu_memory_percent = max_gpu_memory_percent
        self._lock = threading.Lock()
        self._snapshots: list[MemorySnapshot] = []
        self._cache_cleanup_callbacks: list[Callable] = []
        
    def register_cache_cleanup(self, callback: Callable[[], None]) -> None:
        """注册缓存清理回调"""
        with self._lock:
            self._cache_cleanup_callbacks.append(callback)
            logger.info(f"已注册缓存清理回调: {callback.__name__}")
    
    def get_memory_usage_mb(self) -> float:
        """获取当前进程内存使用 (MB)"""
        try:
            import psutil
            import os
            process = psutil.Process(os.getpid())
            return process.memory_info().rss / (1024 * 1024)
        except Exception as e:
            logger.warning(f"获取内存使用失败: {e}")
            return 0.0
    
    def get_gpu_memory_usage_mb(self) -> Optional[float]:
        """获取当前GPU显存使用 (MB)"""
        try:
            import torch
            if torch.cuda.is_available():
                return torch.cuda.memory_allocated() / (1024 * 1024)
            return None
        except Exception:
            return None
    
    def get_system_memory_percent(self) -> float:
        """获取系统内存使用率 (%)"""
        try:
            import psutil
            return psutil.virtual_memory().percent
        except Exception:
            return 0.0
    
    def force_gc(self, aggressive: bool = False) -> float:
        """
        强制执行垃圾回收
        
        Args:
            aggressive: 是否激进清理，将清理所有可收集对象
            
        Returns:
            回收内存 (MB)
        """
        with self._lock:
            before = self.get_memory_usage_mb()
            
            # 清理Python对象
            gc.collect()
            
            # 清理所有缓存清理回调
            for callback in self._cache_cleanup_callbacks:
                try:
                    callback()
                except Exception as e:
                    logger.warning(f"缓存清理回调失败: {e}")
            
            # 清理GPU显存
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                    if aggressive:
                        torch.cuda.synchronize()
            except Exception:
                pass
            
            after = self.get_memory_usage_mb()
            freed = before - after
            
            logger.info(f"垃圾回收: {freed:.2f} MB (激进: {aggressive})")
            return max(0.0, freed)
    
    def check_memory_threshold(self) -> bool:
        """检查是否超过内存阈值"""
        system_percent = self.get_system_memory_percent()
        if system_percent > self.max_memory_percent:
            logger.warning(f"系统内存使用率: {system_percent:.1f}% 超过阈值: {self.max_memory_percent}%")
            return True
        return False
    
    def optimize_memory_if_needed(self) -> bool:
        """如果需要，则优化内存使用
        
        Returns:
            是否执行了优化
        """
        if self.check_memory_threshold():
            logger.info("触发内存优化")
            self.force_gc(aggressive=True)
            return True
        return False
    
    def snapshot(self) -> MemorySnapshot:
        """记录内存快照"""
        snap = MemorySnapshot(
            timestamp=time.time(),
            memory_mb=self.get_memory_usage_mb(),
            gpu_memory_mb=self.get_gpu_memory_usage_mb()
        )
        with self._lock:
            self._snapshots.append(snap)
            # 仅保留最近20个快照
            if len(self._snapshots) > 20:
                self._snapshots = self._snapshots[-20:]
        return snap
    
    def get_memory_trend(self) -> Dict[str, Any]:
        """获取内存趋势分析"""
        if len(self._snapshots) < 2:
            return {"status": "insufficient_data"}
        
        first = self._snapshots[0]
        last = self._snapshots[-1]
        
        diff = last.memory_mb - first.memory_mb
        time_diff = last.timestamp - first.timestamp
        
        return {
            "start_mb": first.memory_mb,
            "current_mb": last.memory_mb,
            "diff_mb": diff,
            "time_seconds": time_diff,
            "trend": "increasing" if diff > 0 else "decreasing",
            "rate_mb_per_sec": diff / time_diff if time_diff > 0 else 0
        }
    
    def cleanup_before_heavy_operation(self, operation_name: str = "") -> None:
        """执行重操作前的内存清理"""
        logger.info(f"执行重操作前清理内存: {operation_name}")
        self.force_gc(aggressive=True)
        self.snapshot()
    
    def cleanup_after_heavy_operation(self, operation_name: str = "") -> None:
        """执行重操作后的内存清理"""
        self.force_gc(aggressive=True)
        freed = self.get_memory_usage_mb()
        logger.info(f"重操作后内存: {freed:.2f} MB: {operation_name}")
        self.snapshot()

    def get_memory_info(self) -> Dict[str, Any]:
        """获取完整内存信息（统一API）"""
        info = {
            "process_memory_mb": self.get_memory_usage_mb(),
            "system_memory_percent": self.get_system_memory_percent(),
            "gpu_memory_mb": self.get_gpu_memory_usage_mb(),
            "max_memory_percent": self.max_memory_percent,
            "max_gpu_memory_percent": self.max_gpu_memory_percent,
            "snapshot_count": len(self._snapshots),
        }
        return info


# 全局内存优化器单例
_global_memory_optimizer: Optional[MemoryOptimizer] = None


def get_memory_optimizer() -> MemoryOptimizer:
    """获取全局内存优化器实例"""
    global _global_memory_optimizer
    if _global_memory_optimizer is None:
        _global_memory_optimizer = MemoryOptimizer()
    return _global_memory_optimizer


def auto_gc_decorator(func):
    """装饰器：在函数执行前后自动进行垃圾回收"""
    def wrapper(*args, **kwargs):
        optimizer = get_memory_optimizer()
        optimizer.cleanup_before_heavy_operation(func.__name__)
        try:
            result = func(*args, **kwargs)
            return result
        finally:
            optimizer.cleanup_after_heavy_operation(func.__name__)
    return wrapper
