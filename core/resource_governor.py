"""
资源调控器 v3.0 (ResourceGovernor)
真正的CPU/RAM/GPU闭环控制系统

旧版问题：
- CPU节流只做了固定5ms sleep，没有实际测量CPU占用率
- 阈值写死在代码里，用户无法调节
- 不区分前台/后台场景

新版方案：
- 实时测量本进程CPU占用率，PID闭环控制
- 超过上限时动态增大sleep间隔
- 低于目标值时恢复处理速度
- 支持三档预设 + 用户精细调节
- 后台自动降速
"""
import time
import os
import threading
from typing import Optional, Callable
from utils.logger import logger
from config import CONFIG


class ResourceGovernor:
    """资源调控器 - 闭环CPU/RAM占用控制"""
    
    _instance = None
    
    @classmethod
    def get_instance(cls) -> 'ResourceGovernor':
        if cls._instance is None:
            cls._instance = ResourceGovernor()
        return cls._instance
    
    def __init__(self):
        self.pl = CONFIG.pipeline
        self._process = None
        self._pid = os.getpid()
        
        # CPU测量
        self._last_cpu_time = 0.0
        self._last_wall_time = 0.0
        self._current_cpu_percent = 0.0
        self._cpu_history = []  # 最近N次的CPU测量值
        self._cpu_history_max = 10
        
        # 节流状态
        self._sleep_ms = 0           # 当前sleep时长(ms)
        self._base_sleep_ms = 1      # 基础sleep(ms)
        self._max_sleep_ms = 200     # 最大sleep(ms)
        self._is_throttled = False
        
        # 后台检测
        self._is_foreground = True
        self._foreground_check_cb = None  # 外部注入的前台检测回调
        
        # 监控线程
        self._monitor_running = False
        self._monitor_thread = None
        
        # ffmpeg进程计数
        self._ffmpeg_count = 0
        self._ffmpeg_lock = threading.Lock()
        
        # 初始化进程对象
        try:
            import psutil
            self._process = psutil.Process(self._pid)
        except ImportError:
            logger.warning("psutil未安装，资源调控将使用简化模式")
        
        logger.info(f"资源调控器初始化: CPU上限={self.pl.cpu_max_percent}%, "
                   f"目标={self.pl.cpu_target_percent}%, 模式={self.pl.resource_mode}")
    
    def set_foreground_check(self, cb: Callable[[], bool]):
        """注入前台检测回调（由MainWindow设置）"""
        self._foreground_check_cb = cb
    
    # =========================================================
    # CPU 占用测量
    # =========================================================
    def measure_cpu(self) -> float:
        """测量本进程当前CPU占用率(%)"""
        if self._process is None:
            return 0.0
        try:
            return self._process.cpu_percent(interval=None)
        except Exception:
            return 0.0
    
    def get_smoothed_cpu(self) -> float:
        """获取平滑后的CPU占用率（取最近N次的均值）"""
        if not self._cpu_history:
            return self.measure_cpu()
        return sum(self._cpu_history) / len(self._cpu_history)
    
    # =========================================================
    # 核心：节流控制（每个分析循环调用一次）
    # =========================================================
    def throttle(self):
        """
        节流控制 - 在分析循环的每次迭代中调用
        
        工作原理：
        1. 测量当前CPU占用
        2. 如果超过上限 → 增大sleep间隔
        3. 如果低于目标 → 减小sleep间隔
        4. 后台模式时使用更低的CPU目标
        """
        # 测量CPU
        cpu_now = self.measure_cpu()
        self._cpu_history.append(cpu_now)
        if len(self._cpu_history) > self._cpu_history_max:
            self._cpu_history.pop(0)
        
        cpu_smooth = self.get_smoothed_cpu()
        
        # 确定目标CPU（前台 vs 后台）
        is_fg = True
        if self._foreground_check_cb:
            try:
                is_fg = self._foreground_check_cb()
            except Exception:
                pass
        
        if self.pl.auto_reduce_when_idle and not is_fg:
            target = self.pl.idle_cpu_target_percent
            upper = self.pl.idle_cpu_target_percent + 10
        else:
            target = self.pl.cpu_target_percent
            upper = self.pl.cpu_max_percent
        
        # PID-like 调节
        if cpu_smooth > upper:
            # 超过上限 → 加大sleep
            overshoot = (cpu_smooth - upper) / max(upper, 1)
            delta_ms = int(overshoot * 50)  # 每超1%加50ms
            self._sleep_ms = min(self._sleep_ms + delta_ms, self._max_sleep_ms)
            self._is_throttled = True
        elif cpu_smooth > target:
            # 在目标和上限之间 → 微调
            ratio = (cpu_smooth - target) / max(upper - target, 1)
            self._sleep_ms = int(self._base_sleep_ms + ratio * 20)
            self._is_throttled = ratio > 0.5
        else:
            # 低于目标 → 恢复
            if self._sleep_ms > 0:
                self._sleep_ms = max(0, self._sleep_ms - 5)
            self._is_throttled = False
        
        # 执行sleep
        if self._sleep_ms > 0:
            time.sleep(self._sleep_ms / 1000.0)
    
    @property
    def is_throttled(self) -> bool:
        return self._is_throttled
    
    @property
    def current_cpu(self) -> float:
        return self.get_smoothed_cpu()
    
    @property
    def current_sleep_ms(self) -> int:
        return self._sleep_ms
    
    # =========================================================
    # ffmpeg 进程数控制
    # =========================================================
    def can_start_ffmpeg(self) -> bool:
        """检查是否可以启动新的ffmpeg进程"""
        with self._ffmpeg_lock:
            return self._ffmpeg_count < self.pl.max_ffmpeg_processes
    
    def register_ffmpeg(self):
        """注册一个ffmpeg进程"""
        with self._ffmpeg_lock:
            self._ffmpeg_count += 1
    
    def unregister_ffmpeg(self):
        """注销一个ffmpeg进程"""
        with self._ffmpeg_lock:
            self._ffmpeg_count = max(0, self._ffmpeg_count - 1)
    
    # =========================================================
    # 内存检查
    # =========================================================
    def check_ram(self) -> str:
        """
        检查系统内存状态
        Returns: 'ok' | 'warning' | 'critical'
        """
        try:
            import psutil
            mem = psutil.virtual_memory()
            if mem.percent >= self.pl.ram_max_percent:
                return 'critical'
            elif mem.percent >= self.pl.ram_target_percent:
                return 'warning'
            return 'ok'
        except Exception:
            return 'ok'
    
    # =========================================================
    # GPU检查
    # =========================================================
    def check_gpu_vram(self) -> str:
        """
        检查GPU显存状态
        Returns: 'ok' | 'warning' | 'critical'
        """
        try:
            import torch
            if not torch.cuda.is_available():
                return 'ok'
            total = torch.cuda.get_device_properties(0).total_mem
            allocated = torch.cuda.memory_allocated()
            used_percent = (allocated / total) * 100
            if used_percent >= self.pl.gpu_vram_max_percent:
                return 'critical'
            elif used_percent >= self.pl.gpu_vram_max_percent * 0.7:
                return 'warning'
            return 'ok'
        except Exception:
            return 'ok'
    
    # =========================================================
    # 资源模式预设
    # =========================================================
    @staticmethod
    def apply_preset(mode: str):
        """应用三档预设"""
        pl = CONFIG.pipeline
        pl.resource_mode = mode
        
        if mode == "economy":
            # 省电模式：低CPU、低并行、后台强降速
            pl.cpu_max_percent = 30
            pl.cpu_target_percent = 15
            pl.max_ffmpeg_processes = 1
            pl.max_analysis_threads = 1
            pl.idle_cpu_target_percent = 8
            pl.auto_reduce_when_idle = True
            pl.ram_max_percent = 50
        elif mode == "performance":
            # 性能模式：高CPU、高并行
            pl.cpu_max_percent = 80
            pl.cpu_target_percent = 50
            pl.max_ffmpeg_processes = 3
            pl.max_analysis_threads = 4
            pl.idle_cpu_target_percent = 30
            pl.auto_reduce_when_idle = False
            pl.ram_max_percent = 75
        else:
            # 均衡模式（默认）
            pl.cpu_max_percent = 50
            pl.cpu_target_percent = 30
            pl.max_ffmpeg_processes = 2
            pl.max_analysis_threads = 2
            pl.idle_cpu_target_percent = 15
            pl.auto_reduce_when_idle = True
            pl.ram_max_percent = 60
        
        logger.info(f"资源模式已切换: {mode} (CPU上限={pl.cpu_max_percent}%, "
                   f"目标={pl.cpu_target_percent}%, ffmpeg进程={pl.max_ffmpeg_processes})")
    
    # =========================================================
    # 资源状态摘要（给UI用）
    # =========================================================
    def get_status(self) -> dict:
        """获取当前资源状态摘要"""
        try:
            import psutil
            mem = psutil.virtual_memory()
            mem_info = {
                "total_gb": round(mem.total / (1024**3), 1),
                "used_gb": round(mem.used / (1024**3), 1),
                "percent": mem.percent,
            }
        except Exception:
            mem_info = {"total_gb": 0, "used_gb": 0, "percent": 0}
        
        gpu_info = {"available": False}
        try:
            import torch
            if torch.cuda.is_available():
                total = torch.cuda.get_device_properties(0).total_mem / (1024**2)
                allocated = torch.cuda.memory_allocated() / (1024**2)
                gpu_info = {
                    "available": True,
                    "total_mb": round(total),
                    "used_mb": round(allocated),
                    "percent": round(allocated / total * 100, 1) if total > 0 else 0,
                }
        except Exception:
            pass
        
        return {
            "cpu_smooth": round(self.get_smoothed_cpu(), 1),
            "cpu_sleep_ms": self._sleep_ms,
            "is_throttled": self._is_throttled,
            "ram": mem_info,
            "gpu": gpu_info,
            "ffmpeg_processes": self._ffmpeg_count,
            "mode": self.pl.resource_mode,
            "cpu_max": self.pl.cpu_max_percent,
            "cpu_target": self.pl.cpu_target_percent,
        }
    
    # =========================================================
    # 后台监控线程（可选，给状态栏用）
    # =========================================================
    def start_monitor(self, interval_sec: float = 2.0, callback: Optional[Callable] = None):
        """启动后台资源监控"""
        if self._monitor_running:
            return
        self._monitor_running = True
        self._monitor_callback = callback
        
        def _monitor_loop():
            while self._monitor_running:
                cpu = self.measure_cpu()
                self._cpu_history.append(cpu)
                if len(self._cpu_history) > self._cpu_history_max:
                    self._cpu_history.pop(0)
                if self._monitor_callback:
                    try:
                        self._monitor_callback(self.get_status())
                    except Exception:
                        pass
                time.sleep(interval_sec)
        
        self._monitor_thread = threading.Thread(target=_monitor_loop, daemon=True)
        self._monitor_thread.start()
        logger.info("资源监控线程已启动")
    
    def stop_monitor(self):
        """停止后台监控"""
        self._monitor_running = False
        if self._monitor_thread:
            self._monitor_thread.join(timeout=3)
        logger.info("资源监控线程已停止")