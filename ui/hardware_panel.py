"""硬件状态监控面板控件

常驻显示 CPU/内存/GPU/温度，数值异常标红。
"""
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (QFrame, QLabel, QProgressBar, QVBoxLayout,
                              QHBoxLayout, QGroupBox)

from hardware_monitor import HardwareGuard, ThermalState


class HardwarePanel(QGroupBox):
    """硬件监控面板"""

    def __init__(self, parent=None):
        super().__init__("硬件监控", parent)
        self._guard = HardwareGuard()
        self._init_ui()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(1000)  # 1 秒刷新

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # CPU
        cpu_layout = QHBoxLayout()
        cpu_layout.addWidget(QLabel("CPU"))
        self._cpu_bar = QProgressBar()
        self._cpu_bar.setRange(0, 100)
        cpu_layout.addWidget(self._cpu_bar)
        self._cpu_label = QLabel("0%")
        self._cpu_label.setFixedWidth(50)
        cpu_layout.addWidget(self._cpu_label)
        layout.addLayout(cpu_layout)

        # 内存
        mem_layout = QHBoxLayout()
        mem_layout.addWidget(QLabel("内存"))
        self._mem_bar = QProgressBar()
        self._mem_bar.setRange(0, 100)
        mem_layout.addWidget(self._mem_bar)
        self._mem_label = QLabel("0%")
        self._mem_label.setFixedWidth(50)
        mem_layout.addWidget(self._mem_label)
        layout.addLayout(mem_layout)

        # GPU
        gpu_layout = QHBoxLayout()
        gpu_layout.addWidget(QLabel("GPU"))
        self._gpu_bar = QProgressBar()
        self._gpu_bar.setRange(0, 100)
        gpu_layout.addWidget(self._gpu_bar)
        self._gpu_label = QLabel("0%")
        self._gpu_label.setFixedWidth(50)
        gpu_layout.addWidget(self._gpu_label)
        layout.addLayout(gpu_layout)

        # 显存
        vram_layout = QHBoxLayout()
        vram_layout.addWidget(QLabel("显存"))
        self._vram_label = QLabel("0 / 0 MB")
        vram_layout.addWidget(self._vram_label)
        vram_layout.addStretch()
        layout.addLayout(vram_layout)

        # 温度
        temp_layout = QHBoxLayout()
        temp_layout.addWidget(QLabel("温度"))
        self._temp_label = QLabel("0 ℃")
        self._temp_label.setStyleSheet("font-weight: bold;")
        temp_layout.addWidget(self._temp_label)
        temp_layout.addStretch()
        layout.addLayout(temp_layout)

        # 状态
        self._state_label = QLabel("状态: 正常")
        layout.addWidget(self._state_label)

    def _refresh(self):
        """刷新硬件数据"""
        snap = self._guard.get_snapshot()
        state = self._guard.get_state()

        # CPU
        self._cpu_bar.setValue(int(snap.cpu_percent))
        self._cpu_label.setText(f"{snap.cpu_percent:.0f}%")
        self._set_bar_color(self._cpu_bar, snap.cpu_percent, 70)

        # 内存
        self._mem_bar.setValue(int(snap.memory_percent))
        self._mem_label.setText(f"{snap.memory_percent:.0f}%")

        # GPU
        self._gpu_bar.setValue(int(snap.gpu_percent))
        self._gpu_label.setText(f"{snap.gpu_percent:.0f}%")
        self._set_bar_color(self._gpu_bar, snap.gpu_percent, 80)

        # 显存
        self._vram_label.setText(
            f"{snap.vram_used_mb:.0f} / {snap.vram_total_mb:.0f} MB")

        # 温度
        self._temp_label.setText(f"{snap.gpu_temp:.0f} ℃")
        if snap.gpu_temp >= 80:
            self._temp_label.setStyleSheet(
                "font-weight: bold; color: red;")
        elif snap.gpu_temp >= 70:
            self._temp_label.setStyleSheet(
                "font-weight: bold; color: orange;")
        else:
            self._temp_label.setStyleSheet(
                "font-weight: bold; color: green;")

        # 状态
        if state == ThermalState.CRITICAL:
            self._state_label.setText("状态: ⚠ 高温保护，已暂停新任务")
            self._state_label.setStyleSheet("color: red; font-weight: bold;")
        elif state == ThermalState.THROTTLE:
            self._state_label.setText("状态: ⚠ 温度偏高，降速运行")
            self._state_label.setStyleSheet("color: orange;")
        else:
            self._state_label.setText("状态: 正常")
            self._state_label.setStyleSheet("color: green;")

    @staticmethod
    def _set_bar_color(bar: QProgressBar, value: float,
                       threshold: float) -> None:
        if value >= threshold:
            bar.setStyleSheet(
                "QProgressBar::chunk { background-color: red; }")
        elif value >= threshold * 0.85:
            bar.setStyleSheet(
                "QProgressBar::chunk { background-color: orange; }")
        else:
            bar.setStyleSheet(
                "QProgressBar::chunk { background-color: green; }")
