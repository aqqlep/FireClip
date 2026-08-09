"""
资源管理器
负责磁盘空间、内存、缓存的管理
"""
import os
import shutil
import psutil
from pathlib import Path
from typing import Optional, Dict
from utils.logger import logger
from config import CONFIG


class ResourceManager:
    """资源管理器 v3.0 - GPU内存池 + 硬约束"""
    
    # 三级资源阈值
    LEVEL_GRACEFUL = 0.7   # 优雅降级
    LEVEL_WARNING = 0.85   # 警告模式
    LEVEL_CRITICAL = 0.95  # 紧急模式
    
    def __init__(self):
        """初始化资源管理器"""
        self.cache_dir = Path(CONFIG.cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        # v3.0: GPU内存池管理
        self._gpu_pool_initialized = False
        self._gpu_pool_size_mb = 0
        self._gpu_models_loaded = {}  # {model_name: model_instance}
        
        logger.info("资源管理器 v3.0 初始化完成（GPU内存池+硬约束）")
    
    def check_disk_space(self, required_gb: float = 5.0) -> bool:
        """
        检查磁盘空间是否充足
        
        Args:
            required_gb: 需要的空间（GB）
        
        Returns:
            是否充足
        """
        try:
            stat = shutil.disk_usage(self.cache_dir)
            free_gb = stat.free / (1024 ** 3)
            
            if free_gb < required_gb:
                logger.warning(f"磁盘空间不足: 需要 {required_gb:.1f}GB, 可用 {free_gb:.1f}GB")
                return False
            
            logger.info(f"磁盘空间充足: {free_gb:.1f}GB 可用")
            return True
        
        except Exception as e:
            logger.error(f"检查磁盘空间失败: {e}")
            return True  # 检查失败时默认允许
    
    def get_cache_size(self) -> float:
        """
        获取缓存目录大小
        
        Returns:
            缓存大小（GB）
        """
        try:
            total_size = 0
            for f in self.cache_dir.rglob("*"):
                if f.is_file():
                    total_size += f.stat().st_size
            
            return total_size / (1024 ** 3)
        
        except Exception as e:
            logger.error(f"获取缓存大小失败: {e}")
            return 0.0
    
    def auto_clean_cache(self, max_size_gb: Optional[float] = None) -> bool:
        """
        自动清理缓存
        
        Args:
            max_size_gb: 最大缓存大小（GB），默认使用配置值
        
        Returns:
            是否成功
        """
        try:
            if max_size_gb is None:
                max_size_gb = CONFIG.max_cache_size_gb
            
            current_size = self.get_cache_size()
            
            if current_size <= max_size_gb:
                logger.info(f"缓存大小正常: {current_size:.2f}GB / {max_size_gb:.2f}GB")
                return True
            
            logger.info(f"开始清理缓存: {current_size:.2f}GB > {max_size_gb:.2f}GB")
            
            # 清理代理文件
            proxy_dir = self.cache_dir / "proxies"
            if proxy_dir.exists():
                shutil.rmtree(proxy_dir)
                proxy_dir.mkdir(parents=True, exist_ok=True)
                logger.info("已清理代理文件")
            
            # 清理缩略图
            thumb_dir = self.cache_dir / "thumbnails"
            if thumb_dir.exists():
                shutil.rmtree(thumb_dir)
                thumb_dir.mkdir(parents=True, exist_ok=True)
                logger.info("已清理缩略图")
            
            # 清理临时文件
            temp_dir = self.cache_dir / "temp"
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
                temp_dir.mkdir(parents=True, exist_ok=True)
                logger.info("已清理临时文件")
            
            new_size = self.get_cache_size()
            logger.info(f"缓存清理完成: {current_size:.2f}GB -> {new_size:.2f}GB")
            
            return True
        
        except Exception as e:
            logger.error(f"清理缓存失败: {e}")
            return False
    
    def get_memory_usage(self) -> dict:
        """
        获取内存使用情况
        
        Returns:
            内存使用信息字典
        """
        try:
            process = psutil.Process()
            memory_info = process.memory_info()
            
            return {
                "rss_mb": memory_info.rss / (1024 ** 2),  # 物理内存
                "vms_mb": memory_info.vms / (1024 ** 2),  # 虚拟内存
                "percent": process.memory_percent()
            }
        
        except Exception as e:
            logger.error(f"获取内存使用失败: {e}")
            return {"rss_mb": 0, "vms_mb": 0, "percent": 0}
    
    def check_memory_limit(self) -> bool:
        """
        检查是否超过内存限制
        
        Returns:
            是否超过限制
        """
        try:
            memory_usage = self.get_memory_usage()
            current_mb = memory_usage["rss_mb"]
            limit_mb = CONFIG.memory_limit_mb
            
            if current_mb > limit_mb:
                logger.warning(f"内存使用超过限制: {current_mb:.0f}MB > {limit_mb}MB")
                return True
            
            return False
        
        except Exception as e:
            logger.error(f"检查内存限制失败: {e}")
            return False
    
    def memory_guard(self):
        """内存保护机制（v3.0: 三级硬约束）"""
        try:
            mem = psutil.virtual_memory()
            mem_ratio = mem.percent / 100.0
            
            if mem_ratio >= self.LEVEL_CRITICAL:
                # 紧急模式：强制释放GPU模型+清理所有缓存+激进GC
                logger.critical(f"内存紧急({mem.percent:.1f}%)，强制释放所有资源")
                self._emergency_release()
                import gc; gc.collect()
            elif mem_ratio >= self.LEVEL_WARNING:
                # 警告模式：释放非核心GPU模型+清理缓存
                logger.warning(f"内存警告({mem.percent:.1f}%)，释放非核心资源")
                self._release_non_critical_models()
                self.auto_clean_cache()
                import gc; gc.collect()
            elif mem_ratio >= self.LEVEL_GRACEFUL:
                # 优雅模式：清理缓存
                logger.info(f"内存偏高({mem.percent:.1f}%)，清理缓存")
                self.auto_clean_cache()
        except Exception as e:
            logger.error(f"内存保护失败: {e}")
    
    # =========================================================
    # v3.0: GPU内存池管理
    # =========================================================
    def init_gpu_pool(self, max_reserve_mb: int = 2048) -> bool:
        """
        初始化GPU内存池（v3.0）
        
        将一部分GPU显存预留给模型常驻，避免频繁加载/卸载
        
        Args:
            max_reserve_mb: 最大预留显存(MB)，默认2GB
        """
        try:
            import torch
            if not torch.cuda.is_available():
                logger.info("CUDA不可用，跳过GPU内存池初始化")
                return False
            
            total_vram = torch.cuda.get_device_properties(0).total_mem / (1024**2)
            # 预留不超过总VRAM的40%
            reserve_mb = min(max_reserve_mb, int(total_vram * 0.4))
            
            self._gpu_pool_size_mb = reserve_mb
            self._gpu_pool_initialized = True
            
            logger.info(f"GPU内存池初始化: VRAM={total_vram:.0f}MB, 预留={reserve_mb}MB")
            return True
        except ImportError:
            logger.info("PyTorch未安装，跳过GPU内存池初始化")
            return False
        except Exception as e:
            logger.warning(f"GPU内存池初始化失败: {e}")
            return False
    
    def get_gpu_memory_info(self) -> Dict:
        """获取GPU内存使用情况"""
        try:
            import torch
            if not torch.cuda.is_available():
                return {"available": False}
            
            allocated = torch.cuda.memory_allocated() / (1024**2)
            reserved = torch.cuda.memory_reserved() / (1024**2)
            total = torch.cuda.get_device_properties(0).total_mem / (1024**2)
            
            return {
                "available": True,
                "total_mb": total,
                "allocated_mb": allocated,
                "reserved_mb": reserved,
                "free_mb": total - allocated,
                "pool_mb": self._gpu_pool_size_mb,
            }
        except Exception:
            return {"available": False}
    
    def can_load_model(self, estimated_size_mb: int) -> bool:
        """检查是否有足够GPU内存加载模型"""
        gpu_info = self.get_gpu_memory_info()
        if not gpu_info.get("available", False):
            return False
        free_mb = gpu_info.get("free_mb", 0)
        return free_mb > estimated_size_mb * 1.2  # 留20%余量
    
    def _emergency_release(self):
        """紧急释放所有GPU模型"""
        released = []
        for name, model in list(self._gpu_models_loaded.items()):
            try:
                del model
                released.append(name)
            except Exception:
                pass
        self._gpu_models_loaded.clear()
        
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass
        
        if released:
            logger.warning(f"紧急释放GPU模型: {', '.join(released)}")
    
    def _release_non_critical_models(self):
        """释放非核心GPU模型（保留CLIP，释放其他）"""
        critical = {'clip_scorer'}
        released = []
        for name in list(self._gpu_models_loaded.keys()):
            if name not in critical:
                try:
                    del self._gpu_models_loaded[name]
                    released.append(name)
                except Exception:
                    pass
        
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass
        
        if released:
            logger.info(f"释放非核心GPU模型: {', '.join(released)}")
    
    def get_system_info(self) -> dict:
        """
        获取系统信息
        
        Returns:
            系统信息字典
        """
        try:
            # CPU信息
            cpu_percent = psutil.cpu_percent(interval=0.1)
            cpu_count = psutil.cpu_count()
            
            # 内存信息
            memory = psutil.virtual_memory()
            memory_total_gb = memory.total / (1024 ** 3)
            memory_available_gb = memory.available / (1024 ** 3)
            memory_percent = memory.percent
            
            # 磁盘信息
            disk = psutil.disk_usage(str(self.cache_dir))
            disk_total_gb = disk.total / (1024 ** 3)
            disk_free_gb = disk.free / (1024 ** 3)
            disk_percent = disk.percent
            
            return {
                "cpu_percent": cpu_percent,
                "cpu_count": cpu_count,
                "memory_total_gb": memory_total_gb,
                "memory_available_gb": memory_available_gb,
                "memory_percent": memory_percent,
                "disk_total_gb": disk_total_gb,
                "disk_free_gb": disk_free_gb,
                "disk_percent": disk_percent
            }
        
        except Exception as e:
            logger.error(f"获取系统信息失败: {e}")
            return {}


# 测试代码
if __name__ == "__main__":
    manager = ResourceManager()
    
    # 检查磁盘空间
    has_space = manager.check_disk_space(5.0)
    print(f"磁盘空间充足: {has_space}")
    
    # 获取缓存大小
    cache_size = manager.get_cache_size()
    print(f"缓存大小: {cache_size:.2f}GB")
    
    # 获取内存使用
    memory_usage = manager.get_memory_usage()
    print(f"内存使用: {memory_usage}")
    
    # 检查内存限制
    over_limit = manager.check_memory_limit()
    print(f"超过内存限制: {over_limit}")
    
    # 获取系统信息
    system_info = manager.get_system_info()
    print(f"系统信息: {system_info}")
