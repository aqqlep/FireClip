"""
设置对话框
提供软件配置界面
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QComboBox, QLineEdit, QPushButton, QFileDialog,
    QGroupBox, QCheckBox, QSpinBox, QDoubleSpinBox, QMessageBox,
    QTabWidget, QWidget, QSlider, QScrollArea, QSizePolicy, QFrame
)
from PyQt6.QtCore import Qt
from utils.logger import logger
from config import CONFIG


def _make_slider_row(label_text: str, slider: QSlider, value_label: QLabel, suffix: str = "%"):
    """创建一行: 标签 | 滑块 | 数值显示"""
    row = QHBoxLayout()
    row.setSpacing(10)
    lbl = QLabel(label_text)
    lbl.setMinimumWidth(120)
    lbl.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred)
    row.addWidget(lbl)
    row.addWidget(slider, 1)
    value_label.setMinimumWidth(48)
    value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    row.addWidget(value_label)
    return row


def _link_slider_spin(slider: QSlider, value_label: QLabel, suffix: str = "%"):
    """将滑块与数值标签联动"""
    def _update(val):
        value_label.setText(f"{val}{suffix}")
    slider.valueChanged.connect(_update)


class SettingsDialog(QDialog):
    """设置对话框"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setMinimumWidth(700)
        self.setMinimumHeight(600)
        
        self.init_ui()
        self.load_settings()
    
    def init_ui(self):
        """初始化UI"""
        layout = QVBoxLayout(self)
        
        # 创建标签页
        tab_widget = QTabWidget()
        
        # 通用设置页
        general_tab = self.create_general_tab()
        tab_widget.addTab(general_tab, "通用")
        
        # AI模型设置页
        ai_tab = self.create_ai_tab()
        tab_widget.addTab(ai_tab, "AI模型")
        
        # 检测设置页
        detection_tab = self.create_detection_tab()
        tab_widget.addTab(detection_tab, "检测参数")
        
        # 资源管理页
        resource_tab = self.create_resource_tab()
        tab_widget.addTab(resource_tab, "资源管理")
        
        layout.addWidget(tab_widget)
        
        # 按钮
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        save_btn = QPushButton("保存")
        save_btn.setStyleSheet("""
            QPushButton {
                background-color: #89b4fa;
                color: #1e1e2e;
                font-weight: bold;
                padding: 8px 24px;
                border-radius: 6px;
            }
            QPushButton:hover {
                background-color: #a6c8fc;
            }
        """)
        save_btn.clicked.connect(self.save_settings)
        button_layout.addWidget(save_btn)
        
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)
        
        layout.addLayout(button_layout)
    
    def create_general_tab(self) -> QWidget:
        """创建通用设置页"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        
        # FFmpeg设置组
        ffmpeg_group = QGroupBox("FFmpeg设置")
        ffmpeg_layout = QGridLayout()
        
        ffmpeg_layout.addWidget(QLabel("FFmpeg路径:"), 0, 0)
        self.ffmpeg_path_edit = QLineEdit()
        self.ffmpeg_path_edit.setPlaceholderText("ffmpeg (自动检测)")
        ffmpeg_layout.addWidget(self.ffmpeg_path_edit, 0, 1)
        
        browse_ffmpeg_btn = QPushButton("浏览...")
        browse_ffmpeg_btn.clicked.connect(self.browse_ffmpeg)
        ffmpeg_layout.addWidget(browse_ffmpeg_btn, 0, 2)
        
        ffmpeg_layout.addWidget(QLabel("硬件加速:"), 1, 0)
        self.hw_accel_combo = QComboBox()
        self.hw_accel_combo.addItems(["自动", "CUDA (NVIDIA)", "无"])
        ffmpeg_layout.addWidget(self.hw_accel_combo, 1, 1)
        
        ffmpeg_group.setLayout(ffmpeg_layout)
        layout.addWidget(ffmpeg_group)
        
        # 输出设置组
        output_group = QGroupBox("输出设置")
        output_layout = QGridLayout()
        
        output_layout.addWidget(QLabel("默认输出目录:"), 0, 0)
        self.output_dir_edit = QLineEdit()
        self.output_dir_edit.setPlaceholderText("./output")
        output_layout.addWidget(self.output_dir_edit, 0, 1)
        
        browse_output_btn = QPushButton("浏览...")
        browse_output_btn.clicked.connect(self.browse_output_dir)
        output_layout.addWidget(browse_output_btn, 0, 2)
        
        output_layout.addWidget(QLabel("默认格式:"), 1, 0)
        self.default_format_combo = QComboBox()
        self.default_format_combo.addItems(["MP4", "MKV", "AVI"])
        output_layout.addWidget(self.default_format_combo, 1, 1)
        
        output_layout.addWidget(QLabel("默认分辨率:"), 2, 0)
        self.default_resolution_combo = QComboBox()
        self.default_resolution_combo.addItems(["原始", "1080p", "720p", "480p"])
        output_layout.addWidget(self.default_resolution_combo, 2, 1)
        
        output_group.setLayout(output_layout)
        layout.addWidget(output_group)
        
        layout.addStretch()
        return widget
    
    def create_ai_tab(self) -> QWidget:
        """创建AI模型设置页"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        
        # AI提供商设置组
        provider_group = QGroupBox("AI提供商")
        provider_layout = QGridLayout()
        
        provider_layout.addWidget(QLabel("提供商:"), 0, 0)
        self.ai_provider_combo = QComboBox()
        self.ai_provider_combo.addItems(["本地模型 (推荐)", "OpenAI API", "Claude API"])
        provider_layout.addWidget(self.ai_provider_combo, 0, 1)
        
        provider_group.setLayout(provider_layout)
        layout.addWidget(provider_group)
        
        # 本地模型设置组
        local_model_group = QGroupBox("本地模型设置")
        local_model_layout = QGridLayout()
        
        local_model_layout.addWidget(QLabel("视觉模型:"), 0, 0)
        self.vision_model_combo = QComboBox()
        self.vision_model_combo.addItems([
            "Qwen/Qwen2-VL-7B-Instruct",
            "OpenGVLab/InternVL2-8B"
        ])
        local_model_layout.addWidget(self.vision_model_combo, 0, 1)
        
        local_model_layout.addWidget(QLabel("文本模型:"), 1, 0)
        self.text_model_combo = QComboBox()
        self.text_model_combo.addItems([
            "Qwen/Qwen2.5-7B-Instruct",
            "THUDM/chatglm3-6b"
        ])
        local_model_layout.addWidget(self.text_model_combo, 1, 1)
        
        local_model_layout.addWidget(QLabel("Whisper模型:"), 2, 0)
        self.whisper_model_combo = QComboBox()
        self.whisper_model_combo.addItems(["large-v3", "medium", "small", "base"])
        local_model_layout.addWidget(self.whisper_model_combo, 2, 1)
        
        local_model_layout.addWidget(QLabel("TTS引擎:"), 3, 0)
        self.tts_engine_combo = QComboBox()
        self.tts_engine_combo.addItems(["ChatTTS", "edge-tts", "CosyVoice"])
        local_model_layout.addWidget(self.tts_engine_combo, 3, 1)
        
        local_model_group.setLayout(local_model_layout)
        layout.addWidget(local_model_group)
        
        # API设置组
        api_group = QGroupBox("API设置 (备选)")
        api_layout = QGridLayout()
        
        api_layout.addWidget(QLabel("OpenAI API Key:"), 0, 0)
        self.openai_key_edit = QLineEdit()
        self.openai_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.openai_key_edit.setPlaceholderText("sk-...")
        api_layout.addWidget(self.openai_key_edit, 0, 1)
        
        api_layout.addWidget(QLabel("Claude API Key:"), 1, 0)
        self.claude_key_edit = QLineEdit()
        self.claude_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.claude_key_edit.setPlaceholderText("sk-ant-...")
        api_layout.addWidget(self.claude_key_edit, 1, 1)
        
        api_group.setLayout(api_layout)
        layout.addWidget(api_group)
        
        layout.addStretch()
        return widget
    
    def create_detection_tab(self) -> QWidget:
        """创建检测设置页"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        
        # 检测参数组
        params_group = QGroupBox("检测参数")
        params_layout = QGridLayout()
        
        params_layout.addWidget(QLabel("场景切换阈值:"), 0, 0)
        self.scene_threshold_spin = QDoubleSpinBox()
        self.scene_threshold_spin.setRange(0.1, 0.9)
        self.scene_threshold_spin.setSingleStep(0.05)
        self.scene_threshold_spin.setDecimals(2)
        params_layout.addWidget(self.scene_threshold_spin, 0, 1)
        
        params_layout.addWidget(QLabel("音频能量阈值百分位:"), 1, 0)
        self.energy_percentile_spin = QSpinBox()
        self.energy_percentile_spin.setRange(50, 99)
        self.energy_percentile_spin.setSingleStep(1)
        params_layout.addWidget(self.energy_percentile_spin, 1, 1)
        
        params_layout.addWidget(QLabel("提取片段数量:"), 2, 0)
        self.top_n_spin = QSpinBox()
        self.top_n_spin.setRange(1, 50)
        self.top_n_spin.setSingleStep(1)
        params_layout.addWidget(self.top_n_spin, 2, 1)
        
        params_layout.addWidget(QLabel("最小片段时长(秒):"), 3, 0)
        self.min_duration_spin = QDoubleSpinBox()
        self.min_duration_spin.setRange(1.0, 30.0)
        self.min_duration_spin.setSingleStep(0.5)
        self.min_duration_spin.setDecimals(1)
        params_layout.addWidget(self.min_duration_spin, 3, 1)
        
        params_layout.addWidget(QLabel("最大片段时长(秒):"), 4, 0)
        self.max_duration_spin = QDoubleSpinBox()
        self.max_duration_spin.setRange(5.0, 120.0)
        self.max_duration_spin.setSingleStep(1.0)
        self.max_duration_spin.setDecimals(1)
        params_layout.addWidget(self.max_duration_spin, 4, 1)
        
        params_group.setLayout(params_layout)
        layout.addWidget(params_group)
        
        # AI视觉分析组
        ai_vision_group = QGroupBox("AI视觉分析")
        ai_vision_layout = QVBoxLayout()
        
        self.enable_ai_vision_check = QCheckBox("启用AI视觉分析通道 (耗时较长但更准确)")
        ai_vision_layout.addWidget(self.enable_ai_vision_check)
        
        ai_interval_layout = QHBoxLayout()
        ai_interval_layout.addWidget(QLabel("AI分析截帧间隔(秒):"))
        self.ai_interval_spin = QDoubleSpinBox()
        self.ai_interval_spin.setRange(0.5, 10.0)
        self.ai_interval_spin.setSingleStep(0.5)
        self.ai_interval_spin.setDecimals(1)
        ai_interval_layout.addWidget(self.ai_interval_spin)
        ai_interval_layout.addStretch()
        ai_vision_layout.addLayout(ai_interval_layout)
        
        ai_vision_group.setLayout(ai_vision_layout)
        layout.addWidget(ai_vision_group)
        
        # 渐进式分析组
        progressive_group = QGroupBox("渐进式分析")
        progressive_layout = QVBoxLayout()
        
        self.progressive_analysis_check = QCheckBox("启用渐进式分析 (先粗扫再精分析)")
        progressive_layout.addWidget(self.progressive_analysis_check)
        
        progressive_group.setLayout(progressive_layout)
        layout.addWidget(progressive_group)
        
        layout.addStretch()
        return widget
    
    def create_resource_tab(self) -> QWidget:
        """创建资源管理设置页（v3.1: 滑块式 + 可滚动 + 自适应布局）"""
        # 外层用 ScrollArea 包裹，小窗口时可滚动
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSpacing(12)
        
        # === 资源模式预设 ===
        mode_group = QGroupBox("资源模式")
        mode_layout = QVBoxLayout()
        
        mode_hint = QLabel("选择适合你使用场景的模式，切换后自动填充下方参数")
        mode_hint.setWordWrap(True)
        mode_hint.setStyleSheet("color: #a6adc8; font-size: 11px;")
        mode_layout.addWidget(mode_hint)
        
        mode_btn_layout = QHBoxLayout()
        mode_btn_layout.setSpacing(8)
        
        self.mode_economy_btn = QPushButton("省电")
        self.mode_economy_btn.setCheckable(True)
        self.mode_economy_btn.setToolTip("CPU上限15%~40%，低并行\n适合边用剪映/打游戏，不影响其他软件")
        self.mode_economy_btn.setStyleSheet("""
            QPushButton { padding: 8px 0; border-radius: 6px; font-size: 13px;
                          background-color: #313244; color: #cdd6f4; border: 2px solid #45475a; }
            QPushButton:checked { background-color: #a6e3a1; color: #1e1e2e; border-color: #a6e3a1; font-weight: bold; }
            QPushButton:hover { border-color: #a6e3a1; }
        """)
        self.mode_economy_btn.clicked.connect(lambda: self._select_mode("economy"))
        mode_btn_layout.addWidget(self.mode_economy_btn)
        
        self.mode_balanced_btn = QPushButton("均衡")
        self.mode_balanced_btn.setCheckable(True)
        self.mode_balanced_btn.setToolTip("CPU上限30%~70%，中等并行\n适合日常使用，兼顾速度与资源")
        self.mode_balanced_btn.setStyleSheet("""
            QPushButton { padding: 8px 0; border-radius: 6px; font-size: 13px;
                          background-color: #313244; color: #cdd6f4; border: 2px solid #45475a; }
            QPushButton:checked { background-color: #89b4fa; color: #1e1e2e; border-color: #89b4fa; font-weight: bold; }
            QPushButton:hover { border-color: #89b4fa; }
        """)
        self.mode_balanced_btn.clicked.connect(lambda: self._select_mode("balanced"))
        mode_btn_layout.addWidget(self.mode_balanced_btn)
        
        self.mode_performance_btn = QPushButton("性能")
        self.mode_performance_btn.setCheckable(True)
        self.mode_performance_btn.setToolTip("CPU上限50%~90%，高并行\n最快速度处理，仅专注分析时推荐")
        self.mode_performance_btn.setStyleSheet("""
            QPushButton { padding: 8px 0; border-radius: 6px; font-size: 13px;
                          background-color: #313244; color: #cdd6f4; border: 2px solid #45475a; }
            QPushButton:checked { background-color: #f38ba8; color: #1e1e2e; border-color: #f38ba8; font-weight: bold; }
            QPushButton:hover { border-color: #f38ba8; }
        """)
        self.mode_performance_btn.clicked.connect(lambda: self._select_mode("performance"))
        mode_btn_layout.addWidget(self.mode_performance_btn)
        
        mode_layout.addLayout(mode_btn_layout)
        mode_group.setLayout(mode_layout)
        layout.addWidget(mode_group)
        
        # === CPU 占用控制（滑块） ===
        cpu_group = QGroupBox("CPU 占用")
        cpu_layout = QVBoxLayout()
        cpu_layout.setSpacing(8)
        
        # CPU上限
        self.cpu_max_slider = QSlider(Qt.Orientation.Horizontal)
        self.cpu_max_slider.setRange(10, 100)
        self.cpu_max_slider.setSingleStep(5)
        self.cpu_max_slider.setPageStep(10)
        self.cpu_max_value = QLabel("50%")
        _link_slider_spin(self.cpu_max_slider, self.cpu_max_value)
        cpu_layout.addLayout(_make_slider_row("占用上限", self.cpu_max_slider, self.cpu_max_value))
        
        # CPU目标
        self.cpu_target_slider = QSlider(Qt.Orientation.Horizontal)
        self.cpu_target_slider.setRange(5, 90)
        self.cpu_target_slider.setSingleStep(5)
        self.cpu_target_slider.setPageStep(10)
        self.cpu_target_value = QLabel("30%")
        _link_slider_spin(self.cpu_target_slider, self.cpu_target_value)
        cpu_layout.addLayout(_make_slider_row("目标占用", self.cpu_target_slider, self.cpu_target_value))
        
        # 检查间隔
        self.cpu_interval_slider = QSlider(Qt.Orientation.Horizontal)
        self.cpu_interval_slider.setRange(100, 3000)
        self.cpu_interval_slider.setSingleStep(100)
        self.cpu_interval_slider.setPageStep(500)
        self.cpu_interval_value = QLabel("500ms")
        _link_slider_spin(self.cpu_interval_slider, self.cpu_interval_value, "ms")
        cpu_layout.addLayout(_make_slider_row("检查间隔", self.cpu_interval_slider, self.cpu_interval_value, "ms"))
        
        self.cpu_aggressive_check = QCheckBox("激进节流（更平稳但稍慢）")
        cpu_layout.addWidget(self.cpu_aggressive_check)
        
        cpu_group.setLayout(cpu_layout)
        layout.addWidget(cpu_group)
        
        # === 内存 & GPU 合并一组 ===
        mem_gpu_group = QGroupBox("内存 & 显存")
        mem_gpu_layout = QVBoxLayout()
        mem_gpu_layout.setSpacing(8)
        
        # RAM上限
        self.ram_max_slider = QSlider(Qt.Orientation.Horizontal)
        self.ram_max_slider.setRange(30, 90)
        self.ram_max_slider.setSingleStep(5)
        self.ram_max_slider.setPageStep(10)
        self.ram_max_value = QLabel("60%")
        _link_slider_spin(self.ram_max_slider, self.ram_max_value)
        mem_gpu_layout.addLayout(_make_slider_row("内存上限", self.ram_max_slider, self.ram_max_value))
        
        # RAM目标
        self.ram_target_slider = QSlider(Qt.Orientation.Horizontal)
        self.ram_target_slider.setRange(20, 80)
        self.ram_target_slider.setSingleStep(5)
        self.ram_target_slider.setPageStep(10)
        self.ram_target_value = QLabel("40%")
        _link_slider_spin(self.ram_target_slider, self.ram_target_value)
        mem_gpu_layout.addLayout(_make_slider_row("内存目标", self.ram_target_slider, self.ram_target_value))
        
        # GPU显存上限
        self.gpu_vram_max_slider = QSlider(Qt.Orientation.Horizontal)
        self.gpu_vram_max_slider.setRange(20, 90)
        self.gpu_vram_max_slider.setSingleStep(5)
        self.gpu_vram_max_slider.setPageStep(10)
        self.gpu_vram_max_value = QLabel("50%")
        _link_slider_spin(self.gpu_vram_max_slider, self.gpu_vram_max_value)
        mem_gpu_layout.addLayout(_make_slider_row("显存上限", self.gpu_vram_max_slider, self.gpu_vram_max_value))
        
        mem_gpu_group.setLayout(mem_gpu_layout)
        layout.addWidget(mem_gpu_group)
        
        # === 并行度 & 后台 合并一组 ===
        adv_group = QGroupBox("高级选项")
        adv_layout = QVBoxLayout()
        adv_layout.setSpacing(8)
        
        # FFmpeg进程数
        self.max_ffmpeg_slider = QSlider(Qt.Orientation.Horizontal)
        self.max_ffmpeg_slider.setRange(1, 8)
        self.max_ffmpeg_slider.setSingleStep(1)
        self.max_ffmpeg_slider.setPageStep(1)
        self.max_ffmpeg_value = QLabel("2")
        _link_slider_spin(self.max_ffmpeg_slider, self.max_ffmpeg_value, "")
        adv_layout.addLayout(_make_slider_row("FFmpeg进程", self.max_ffmpeg_slider, self.max_ffmpeg_value, ""))
        
        # 分析线程数
        self.max_threads_slider = QSlider(Qt.Orientation.Horizontal)
        self.max_threads_slider.setRange(1, 8)
        self.max_threads_slider.setSingleStep(1)
        self.max_threads_slider.setPageStep(1)
        self.max_threads_value = QLabel("2")
        _link_slider_spin(self.max_threads_slider, self.max_threads_value, "")
        adv_layout.addLayout(_make_slider_row("分析线程", self.max_threads_slider, self.max_threads_value, ""))
        
        # 分割线
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #45475a;")
        adv_layout.addWidget(sep)
        
        # 后台自动降速
        self.auto_reduce_check = QCheckBox("窗口失焦时自动降低CPU")
        self.auto_reduce_check.setToolTip("切到其他软件时自动降速")
        adv_layout.addWidget(self.auto_reduce_check)
        
        # 后台CPU目标
        self.idle_cpu_slider = QSlider(Qt.Orientation.Horizontal)
        self.idle_cpu_slider.setRange(5, 50)
        self.idle_cpu_slider.setSingleStep(5)
        self.idle_cpu_slider.setPageStep(10)
        self.idle_cpu_value = QLabel("15%")
        _link_slider_spin(self.idle_cpu_slider, self.idle_cpu_value)
        adv_layout.addLayout(_make_slider_row("后台CPU目标", self.idle_cpu_slider, self.idle_cpu_value))
        
        adv_group.setLayout(adv_layout)
        layout.addWidget(adv_group)
        
        # === 代理文件/缓存 ===
        cache_group = QGroupBox("代理文件与缓存")
        cache_layout = QGridLayout()
        cache_layout.setHorizontalSpacing(10)
        cache_layout.setVerticalSpacing(6)
        cache_layout.setColumnMinimumWidth(0, 100)
        cache_layout.setColumnStretch(1, 1)
        
        self.proxy_enabled_check = QCheckBox("启用代理文件 (大视频预览)")
        cache_layout.addWidget(self.proxy_enabled_check, 0, 0, 1, 3)
        
        cache_layout.addWidget(QLabel("代理分辨率:"), 1, 0)
        self.proxy_resolution_combo = QComboBox()
        self.proxy_resolution_combo.addItems(["480p", "720p", "360p"])
        cache_layout.addWidget(self.proxy_resolution_combo, 1, 1, 1, 2)
        
        cache_layout.addWidget(QLabel("缓存目录:"), 2, 0)
        self.cache_dir_edit = QLineEdit()
        self.cache_dir_edit.setPlaceholderText("./cache")
        cache_layout.addWidget(self.cache_dir_edit, 2, 1)
        browse_cache_btn = QPushButton("...")
        browse_cache_btn.setFixedWidth(36)
        browse_cache_btn.clicked.connect(self.browse_cache_dir)
        cache_layout.addWidget(browse_cache_btn, 2, 2)
        
        cache_layout.addWidget(QLabel("最大缓存:"), 3, 0)
        cache_size_layout = QHBoxLayout()
        self.max_cache_spin = QDoubleSpinBox()
        self.max_cache_spin.setRange(1.0, 500.0)
        self.max_cache_spin.setSingleStep(5.0)
        self.max_cache_spin.setDecimals(1)
        self.max_cache_spin.setSuffix(" GB")
        cache_size_layout.addWidget(self.max_cache_spin)
        cache_size_layout.addStretch()
        cache_layout.addLayout(cache_size_layout, 3, 1, 1, 2)
        
        cache_layout.addWidget(QLabel("内存限制:"), 4, 0)
        mem_limit_layout = QHBoxLayout()
        self.memory_limit_spin = QSpinBox()
        self.memory_limit_spin.setRange(500, 32000)
        self.memory_limit_spin.setSingleStep(500)
        self.memory_limit_spin.setSuffix(" MB")
        mem_limit_layout.addWidget(self.memory_limit_spin)
        mem_limit_layout.addStretch()
        cache_layout.addLayout(mem_limit_layout, 4, 1, 1, 2)
        
        clear_cache_btn = QPushButton("清理缓存")
        clear_cache_btn.clicked.connect(self.clear_cache)
        cache_layout.addWidget(clear_cache_btn, 5, 1)
        
        cache_group.setLayout(cache_layout)
        layout.addWidget(cache_group)
        
        layout.addStretch()
        
        scroll.setWidget(content)
        return scroll
    
    # 模式对应的滑块范围: (cpu_max_min, cpu_max_max, cpu_target_min, cpu_target_max,
    #                      ram_max_min, ram_max_max, idle_min, idle_max, ffmpeg_max, threads_max)
    _MODE_RANGES = {
        "economy":     (15, 40,  5, 25,  40, 55,  5, 20,  2, 2),
        "balanced":    (30, 70, 10, 50,  50, 70,  5, 30,  3, 4),
        "performance": (50, 90, 20, 70,  60, 85, 10, 40,  4, 6),
    }
    # 模式对应的预设值: (cpu_max, cpu_target, ffmpeg, threads, idle, ram_max)
    _MODE_PRESETS = {
        "economy":     (30, 15, 1, 1,  8, 50),
        "balanced":    (50, 30, 2, 2, 15, 60),
        "performance": (80, 50, 3, 4, 30, 75),
    }

    def _select_mode(self, mode: str):
        """选择资源模式，同步更新滑块范围+填充预设值"""
        self.mode_economy_btn.setChecked(mode == "economy")
        self.mode_balanced_btn.setChecked(mode == "balanced")
        self.mode_performance_btn.setChecked(mode == "performance")
        
        if mode not in self._MODE_RANGES:
            return
        
        (cpu_max_lo, cpu_max_hi, cpu_tgt_lo, cpu_tgt_hi,
         ram_max_lo, ram_max_hi, idle_lo, idle_hi,
         ffmpeg_hi, threads_hi) = self._MODE_RANGES[mode]
        
        # 动态更新滑块范围（拉不到超限值）
        self.cpu_max_slider.setRange(cpu_max_lo, cpu_max_hi)
        self.cpu_target_slider.setRange(cpu_tgt_lo, cpu_tgt_hi)
        self.ram_max_slider.setRange(ram_max_lo, ram_max_hi)
        self.ram_target_slider.setRange(ram_max_lo - 10, ram_max_hi - 10)
        self.idle_cpu_slider.setRange(idle_lo, idle_hi)
        self.max_ffmpeg_slider.setRange(1, ffmpeg_hi)
        self.max_threads_slider.setRange(1, threads_hi)
        # GPU显存：性能模式最高80%，省电最高60%
        gpu_hi = 60 if mode == "economy" else (80 if mode == "performance" else 70)
        self.gpu_vram_max_slider.setRange(20, gpu_hi)
        
        # 填充预设值
        cpu_max, cpu_target, ffmpeg, threads, idle, ram_max = self._MODE_PRESETS[mode]
        self.cpu_max_slider.setValue(cpu_max)
        self.cpu_target_slider.setValue(cpu_target)
        self.max_ffmpeg_slider.setValue(ffmpeg)
        self.max_threads_slider.setValue(threads)
        self.idle_cpu_slider.setValue(idle)
        self.ram_max_slider.setValue(ram_max)
    
    def load_settings(self):
        """加载设置"""
        # 通用设置
        self.ffmpeg_path_edit.setText(CONFIG.ffmpeg_path)
        
        hw_accel_map = {"auto": 0, "cuda": 1, "none": 2}
        self.hw_accel_combo.setCurrentIndex(hw_accel_map.get(CONFIG.hw_accel, 0))
        
        self.output_dir_edit.setText(CONFIG.output_dir)
        
        format_map = {"mp4": 0, "mkv": 1, "avi": 2}
        self.default_format_combo.setCurrentIndex(format_map.get(CONFIG.default_format, 0))
        
        resolution_map = {"original": 0, "1080p": 1, "720p": 2, "480p": 3}
        self.default_resolution_combo.setCurrentIndex(resolution_map.get(CONFIG.default_resolution, 0))
        
        # AI设置
        provider_map = {"local": 0, "openai": 1, "claude": 2}
        self.ai_provider_combo.setCurrentIndex(provider_map.get(CONFIG.ai_provider, 0))
        
        self.openai_key_edit.setText(CONFIG.openai_api_key)
        self.claude_key_edit.setText(CONFIG.claude_api_key)
        
        # 检测设置
        self.scene_threshold_spin.setValue(CONFIG.scene_threshold)
        self.energy_percentile_spin.setValue(int(CONFIG.energy_threshold_percentile))
        self.top_n_spin.setValue(CONFIG.detection_top_n)
        self.min_duration_spin.setValue(CONFIG.detection_min_duration)
        self.max_duration_spin.setValue(CONFIG.detection_max_duration)
        self.enable_ai_vision_check.setChecked(CONFIG.enable_ai_vision_channel)
        self.ai_interval_spin.setValue(CONFIG.ai_vision_interval)
        self.progressive_analysis_check.setChecked(CONFIG.progressive_analysis)
        
        # 资源管理设置
        pl = CONFIG.pipeline
        # 资源模式
        self._select_mode(pl.resource_mode)
        # CPU
        self.cpu_max_slider.setValue(pl.cpu_max_percent)
        self.cpu_target_slider.setValue(pl.cpu_target_percent)
        self.cpu_interval_slider.setValue(pl.cpu_check_interval_ms)
        self.cpu_aggressive_check.setChecked(pl.cpu_throttle_aggressive)
        # RAM
        self.ram_max_slider.setValue(pl.ram_max_percent)
        self.ram_target_slider.setValue(pl.ram_target_percent)
        # GPU
        self.gpu_vram_max_slider.setValue(pl.gpu_vram_max_percent)
        # 并行度
        self.max_ffmpeg_slider.setValue(pl.max_ffmpeg_processes)
        self.max_threads_slider.setValue(pl.max_analysis_threads)
        # 后台
        self.auto_reduce_check.setChecked(pl.auto_reduce_when_idle)
        self.idle_cpu_slider.setValue(pl.idle_cpu_target_percent)
        # 代理/缓存
        self.proxy_enabled_check.setChecked(CONFIG.proxy_enabled)
        proxy_resolution_map = {"480p": 0, "720p": 1, "360p": 2}
        self.proxy_resolution_combo.setCurrentIndex(proxy_resolution_map.get(CONFIG.proxy_resolution, 0))
        self.cache_dir_edit.setText(CONFIG.cache_dir)
        self.max_cache_spin.setValue(CONFIG.max_cache_size_gb)
        self.memory_limit_spin.setValue(CONFIG.memory_limit_mb)
    
    def save_settings(self):
        """保存设置"""
        try:
            # 通用设置
            CONFIG.ffmpeg_path = self.ffmpeg_path_edit.text() or "ffmpeg"
            
            hw_accel_map = {0: "auto", 1: "cuda", 2: "none"}
            CONFIG.hw_accel = hw_accel_map.get(self.hw_accel_combo.currentIndex(), "auto")
            
            CONFIG.output_dir = self.output_dir_edit.text() or "./output"
            
            format_map = {0: "mp4", 1: "mkv", 2: "avi"}
            CONFIG.default_format = format_map.get(self.default_format_combo.currentIndex(), "mp4")
            
            resolution_map = {0: "original", 1: "1080p", 2: "720p", 3: "480p"}
            CONFIG.default_resolution = resolution_map.get(self.default_resolution_combo.currentIndex(), "original")
            
            # AI设置
            provider_map = {0: "local", 1: "openai", 2: "claude"}
            CONFIG.ai_provider = provider_map.get(self.ai_provider_combo.currentIndex(), "local")
            
            CONFIG.openai_api_key = self.openai_key_edit.text()
            CONFIG.claude_api_key = self.claude_key_edit.text()
            
            # 检测设置
            CONFIG.scene_threshold = self.scene_threshold_spin.value()
            CONFIG.energy_threshold_percentile = float(self.energy_percentile_spin.value())
            CONFIG.detection_top_n = self.top_n_spin.value()
            CONFIG.detection_min_duration = self.min_duration_spin.value()
            CONFIG.detection_max_duration = self.max_duration_spin.value()
            CONFIG.enable_ai_vision_channel = self.enable_ai_vision_check.isChecked()
            CONFIG.ai_vision_interval = self.ai_interval_spin.value()
            CONFIG.progressive_analysis = self.progressive_analysis_check.isChecked()
            
            # 资源管理设置
            CONFIG.proxy_enabled = self.proxy_enabled_check.isChecked()
            
            proxy_resolution_map = {0: "480p", 1: "720p", 2: "360p"}
            CONFIG.proxy_resolution = proxy_resolution_map.get(self.proxy_resolution_combo.currentIndex(), "480p")
            
            CONFIG.cache_dir = self.cache_dir_edit.text() or "./cache"
            CONFIG.max_cache_size_gb = self.max_cache_spin.value()
            CONFIG.memory_limit_mb = self.memory_limit_spin.value()
            
            # v3.0: 资源调控参数
            pl = CONFIG.pipeline
            # 确定资源模式
            if self.mode_economy_btn.isChecked():
                pl.resource_mode = "economy"
            elif self.mode_performance_btn.isChecked():
                pl.resource_mode = "performance"
            else:
                pl.resource_mode = "balanced"
            pl.cpu_max_percent = self.cpu_max_slider.value()
            pl.cpu_target_percent = self.cpu_target_slider.value()
            pl.cpu_check_interval_ms = self.cpu_interval_slider.value()
            pl.cpu_throttle_aggressive = self.cpu_aggressive_check.isChecked()
            pl.ram_max_percent = self.ram_max_slider.value()
            pl.ram_target_percent = self.ram_target_slider.value()
            pl.gpu_vram_max_percent = self.gpu_vram_max_slider.value()
            pl.max_ffmpeg_processes = self.max_ffmpeg_slider.value()
            pl.max_analysis_threads = self.max_threads_slider.value()
            pl.auto_reduce_when_idle = self.auto_reduce_check.isChecked()
            pl.idle_cpu_target_percent = self.idle_cpu_slider.value()
            
            # 同步应用预设（更新关联参数）
            from core.resource_governor import ResourceGovernor
            ResourceGovernor.apply_preset(pl.resource_mode)
            # 再覆盖用户精细调节的值
            pl.cpu_max_percent = self.cpu_max_slider.value()
            pl.cpu_target_percent = self.cpu_target_slider.value()
            pl.ram_max_percent = self.ram_max_slider.value()
            pl.ram_target_percent = self.ram_target_slider.value()
            pl.gpu_vram_max_percent = self.gpu_vram_max_slider.value()
            pl.max_ffmpeg_processes = self.max_ffmpeg_slider.value()
            pl.max_analysis_threads = self.max_threads_slider.value()
            pl.idle_cpu_target_percent = self.idle_cpu_slider.value()
            pl.auto_reduce_when_idle = self.auto_reduce_check.isChecked()
            
            # 保存配置文件
            CONFIG.save()
            
            QMessageBox.information(self, "成功", "设置已保存")
            self.accept()
        
        except Exception as e:
            logger.error(f"保存设置失败: {e}")
            QMessageBox.critical(self, "错误", f"保存设置失败:\n{str(e)}")
    
    def browse_ffmpeg(self):
        """浏览FFmpeg路径"""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择FFmpeg可执行文件",
            "",
            "可执行文件 (*.exe);;所有文件 (*)"
        )
        
        if file_path:
            self.ffmpeg_path_edit.setText(file_path)
    
    def browse_output_dir(self):
        """浏览输出目录"""
        dir_path = QFileDialog.getExistingDirectory(
            self,
            "选择输出目录"
        )
        
        if dir_path:
            self.output_dir_edit.setText(dir_path)
    
    def browse_cache_dir(self):
        """浏览缓存目录"""
        dir_path = QFileDialog.getExistingDirectory(
            self,
            "选择缓存目录"
        )
        
        if dir_path:
            self.cache_dir_edit.setText(dir_path)
    
    def clear_cache(self):
        """清理缓存"""
        reply = QMessageBox.question(
            self,
            "确认",
            "确定要清理所有缓存文件吗？\n这将删除代理文件、缩略图等临时文件。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            try:
                import shutil
                from pathlib import Path
                
                cache_path = Path(self.cache_dir_edit.text())
                if cache_path.exists():
                    shutil.rmtree(cache_path)
                    cache_path.mkdir(parents=True, exist_ok=True)
                    QMessageBox.information(self, "成功", "缓存已清理")
                else:
                    QMessageBox.information(self, "提示", "缓存目录不存在")
            
            except Exception as e:
                logger.error(f"清理缓存失败: {e}")
                QMessageBox.critical(self, "错误", f"清理缓存失败:\n{str(e)}")


# 测试代码
if __name__ == "__main__":
    import sys
    from PyQt6.QtWidgets import QApplication
    
    app = QApplication(sys.argv)
    
    dialog = SettingsDialog()
    if dialog.exec() == QDialog.DialogCode.Accepted:
        print("设置已保存")
