"""硬件监控、温控、算力限流核心模块

实时监控 CPU/内存/GPU/温度，分级温控保护，算力动态限流。
"""
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

import psutil

from logger import get_logger

log = get_logger("hardware")


class ThermalState(Enum):
    """温控状态"""
    NORMAL = "normal"       # < warning 温度
    THROTTLE = "throttle"   # warning ~ critical
    CRITICAL = "critical"   # >= critical


class HardwareGuard:
    """硬件限流温控保护核心（单例）"""

    _instance: Optional["HardwareGuard"] = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, cpu_threshold: float = 70, gpu_threshold: float = 80,
                 temp_warning: float = 70, temp_critical: float = 80,
                 low_vram_limit: int = 4096, monitor_interval: float = 1.0):
        if self._initialized:
            return
        self._cpu_threshold = cpu_threshold
        self._gpu_threshold = gpu_threshold
        self._temp_warning = temp_warning
        self._temp_critical = temp_critical
        self._low_vram_limit = low_vram_limit
        self._monitor_interval = monitor_interval

        self._state = ThermalState.NORMAL
        self._snapshot = HardwareSnapshot()
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._listeners: list[Callable[[HardwareSnapshot], None]] = []
        self._throttle_callbacks: list[Callable[[ThermalState], None]] = []
        self._nvml_ok = False
        self._nvml_handle = None
        self._init_nvml()
        self._initialized = True
        log.info("硬件保护模块初始化完成")

    def _init_nvml(self) -> None:
        """初始化 NVIDIA 显卡监控"""
        try:
            import pynvml
            pynvml.nvmlInit()
            self._nvml_handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            self._nvml_ok = True
            log.info("NVIDIA 显卡监控已启用")
        except Exception as e:
            self._nvml_ok = False
            log.warning(f"NVIDIA 显卡监控不可用（无 NVIDIA 显卡或驱动）: {e}")

    def start(self) -> None:
        """启动监控线程"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._monitor_loop,
                                        daemon=True, name="HardwareMonitor")
        self._thread.start()
        log.info("硬件监控线程已启动")

    def stop(self) -> None:
        """停止监控"""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        if self._nvml_ok:
            try:
                import pynvml
                pynvml.nvmlShutdown()
            except Exception:
                pass
        log.info("硬件监控已停止")

    def _monitor_loop(self) -> None:
        """监控主循环"""
        while self._running:
            try:
                self._update_snapshot()
                self._check_thermal()
                self._notify_listeners()
            except Exception as e:
                log.error(f"硬件监控异常: {e}", extra={"category": "error"})
            time.sleep(self._monitor_interval)

    def _update_snapshot(self) -> None:
        """更新硬件快照"""
        with self._lock:
            self._snapshot.cpu_percent = psutil.cpu_percent(interval=0.5)
            self._snapshot.memory_percent = psutil.virtual_memory().percent
            self._snapshot.memory_used_gb = psutil.virtual_memory().used / 1024**3

            if self._nvml_ok:
                try:
                    import pynvml
                    util = pynvml.nvmlDeviceGetUtilizationRates(self._nvml_handle)
                    self._snapshot.gpu_percent = util.gpu
                    mem = pynvml.nvmlDeviceGetMemoryInfo(self._nvml_handle)
                    self._snapshot.vram_used_mb = mem.used / 1024 / 1024
                    self._snapshot.vram_total_mb = mem.total / 1024 / 1024
                    self._snapshot.gpu_temp = pynvml.nvmlDeviceGetTemperature(
                        self._nvml_handle, pynvml.NVML_TEMPERATURE_GPU)
                except Exception as e:
                    log.warning(f"GPU 数据读取失败: {e}")

    def _check_thermal(self) -> None:
        """温控状态判定与切换"""
        temp = self._snapshot.gpu_temp
        old_state = self._state

        if temp >= self._temp_critical:
            new_state = ThermalState.CRITICAL
        elif temp >= self._temp_warning:
            new_state = ThermalState.THROTTLE
        else:
            new_state = ThermalState.NORMAL

        if new_state != old_state:
            self._state = new_state
            log.warning(
                f"温控状态切换: {old_state.value} → {new_state.value} "
                f"(当前 {temp}℃)", extra={"category": "hardware"})
            for cb in self._throttle_callbacks:
                try:
                    cb(new_state)
                except Exception as e:
                    log.error(f"温控回调异常: {e}")

    def _notify_listeners(self) -> None:
        """通知监控数据监听器"""
        for listener in self._listeners:
            try:
                listener(self._snapshot)
            except Exception:
                pass

    def add_listener(self, callback: Callable[[HardwareSnapshot], None]) -> None:
        """注册硬件数据监听器"""
        self._listeners.append(callback)

    def add_thermal_listener(
            self, callback: Callable[[ThermalState], None]) -> None:
        """注册温控状态变更监听器"""
        self._throttle_callbacks.append(callback)

    def get_snapshot(self) -> "HardwareSnapshot":
        """获取当前硬件快照"""
        with self._lock:
            return self._snapshot

    def get_state(self) -> ThermalState:
        """获取当前温控状态"""
        return self._state

    def can_run_new_task(self) -> bool:
        """判断是否允许启动新任务（基于温控与算力占用）"""
        snap = self._snapshot
        if self._state == ThermalState.CRITICAL:
            return False
        if snap.cpu_percent > self._cpu_threshold:
            return False
        if self._nvml_ok and snap.gpu_percent > self._gpu_threshold:
            return False
        return True

    def should_throttle(self) -> bool:
        """当前是否需要降速（温控或算力超限）"""
        if self._state in (ThermalState.THROTTLE, ThermalState.CRITICAL):
            return True
        snap = self._snapshot
        if snap.cpu_percent > self._cpu_threshold:
            return True
        if self._nvml_ok and snap.gpu_percent > self._gpu_threshold:
            return True
        return False

    def is_low_vram(self) -> bool:
        """是否为低显存设备"""
        if not self._nvml_ok:
            return True
        return self._snapshot.vram_total_mb < self._low_vram_limit

    def update_thresholds(self, cpu_threshold: float = None,
                          gpu_threshold: float = None,
                          temp_warning: float = None,
                          temp_critical: float = None) -> None:
        """动态更新阈值"""
        if cpu_threshold is not None:
            self._cpu_threshold = cpu_threshold
        if gpu_threshold is not None:
            self._gpu_threshold = gpu_threshold
        if temp_warning is not None:
            self._temp_warning = temp_warning
        if temp_critical is not None:
            self._temp_critical = temp_critical
        log.info(f"阈值已更新: CPU<{self._cpu_threshold}%> "
                 f"GPU<{self._gpu_threshold}%> "
                 f"温度<{self._temp_warning}~{self._temp_critical}℃>")


@dataclass
class HardwareSnapshot:
    """硬件状态快照"""
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    memory_used_gb: float = 0.0
    gpu_percent: float = 0.0
    vram_used_mb: float = 0.0
    vram_total_mb: float = 0.0
    gpu_temp: float = 0.0
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "cpu_percent": round(self.cpu_percent, 1),
            "memory_percent": round(self.memory_percent, 1),
            "memory_used_gb": round(self.memory_used_gb, 2),
            "gpu_percent": round(self.gpu_percent, 1),
            "vram_used_mb": round(self.vram_used_mb, 0),
            "vram_total_mb": round(self.vram_total_mb, 0),
            "gpu_temp": round(self.gpu_temp, 1),
        }
