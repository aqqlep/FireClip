"""配置加载与热更新模块

负责读取 config.yaml，提供全局配置访问，支持热加载通知。
"""
import threading
from pathlib import Path
from typing import Any, Optional

import yaml

from logger import get_logger

log = get_logger("task")


class ConfigLoader:
    """全局配置管理器（单例）"""

    _instance: Optional["ConfigLoader"] = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self, config_path: str = "config.yaml"):
        if self._initialized:
            return
        self._config_path = Path(config_path)
        self._config: dict = {}
        self._listeners: list = []
        self._file_lock = threading.Lock()
        self._load()
        self._initialized = True
        log.info(f"配置加载完成: {self._config_path}")

    def _load(self) -> None:
        """从文件加载配置"""
        with self._file_lock:
            if not self._config_path.exists():
                log.warning(f"配置文件不存在，使用默认配置: {self._config_path}")
                self._config = self._default_config()
                return
            try:
                with open(self._config_path, "r", encoding="utf-8") as f:
                    self._config = yaml.safe_load(f) or {}
            except Exception as e:
                log.error(f"配置加载失败: {e}，使用默认配置")
                self._config = self._default_config()

    def _default_config(self) -> dict:
        """默认配置兜底"""
        return {
            "hardware": {
                "cpu_threshold": 70, "gpu_threshold": 80,
                "temp_warning": 70, "temp_critical": 80,
                "low_vram_limit": 4096, "monitor_interval": 1.0,
            },
            "model": {"precision": "int4", "device": "auto", "batch_size": 1,
                      "vl_sample_ratio": 0.1},
            "video": {"segment_duration": 1.5, "merge_gap": 15,
                      "min_clip_duration_fight": 3,
                      "min_clip_duration_highlight": 4,
                      "max_resolution": 1080, "fps_sample": 10},
            "cache": {"path": "./temp_cache", "auto_clean": True,
                      "max_size_gb": 10, "stale_hours": 24},
            "tts": {"voice": "zh-CN-XiaoxiaoNeural", "rate": "+0%",
                    "retry_times": 3, "timeout": 30},
        }

    def get(self, *keys: str, default: Any = None) -> Any:
        """按层级获取配置项，例如 get('hardware', 'cpu_threshold')"""
        node = self._config
        for k in keys:
            if not isinstance(node, dict) or k not in node:
                return default
            node = node[k]
        return node

    def set(self, *keys_and_value: Any) -> None:
        """按层级设置配置项，最后一个参数为值"""
        if len(keys_and_value) < 2:
            return
        *keys, value = keys_and_value
        node = self._config
        for k in keys[:-1]:
            node = node.setdefault(k, {})
        node[keys[-1]] = value

    def reload(self) -> None:
        """重新加载配置文件并通知监听器"""
        old = self._config.copy()
        self._load()
        log.info("配置热加载完成")
        for listener in self._listeners:
            try:
                listener(old, self._config)
            except Exception as e:
                log.error(f"配置变更通知失败: {e}")

    def save(self) -> None:
        """持久化配置到文件"""
        with self._file_lock:
            with open(self._config_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(self._config, f, allow_unicode=True,
                               default_flow_style=False, sort_keys=False)
        log.info("配置已保存")

    def add_listener(self, callback) -> None:
        """注册配置变更监听器 callback(old_config, new_config)"""
        self._listeners.append(callback)

    @property
    def raw(self) -> dict:
        return self._config


def get_config(config_path: str = "config.yaml") -> ConfigLoader:
    """获取全局配置实例"""
    return ConfigLoader(config_path)
