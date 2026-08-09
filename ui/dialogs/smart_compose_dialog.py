"""
智能成片对话框
支持两种模式:
1. 一键成片: 直接从原始视频自动提取高燃/高光片段，自动拼接导出
2. 使用已选片段: 如果已经提取了高燃/高光片段，勾选后直接合成
"""
import os
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGroupBox, QRadioButton,
    QButtonGroup, QComboBox, QCheckBox, QSpinBox, QDoubleSpinBox,
    QPushButton, QLabel, QProgressBar, QFileDialog, QListWidget,
    QListWidgetItem, QMessageBox, QLineEdit, QWidget
)
from PyQt6.QtCore import QThread, pyqtSignal, Qt

from core.smart_compose import (
    SmartComposeEngine,
    SmartComposeConfig,
)
from core.smart_compose.templates import (
    ALL_TEMPLATES,
    get_template_by_name,
    ComposeTemplate,
    get_all_categories,
    get_templates_by_category,
)


class SmartComposeWorker(QThread):
    """智能成片工作线程"""
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(bool, str, str, float, int, str)
    error = pyqtSignal(str)
    
    def __init__(self, engine: SmartComposeEngine, video_path: str, segments=None, extract_hot=True, extract_highlight=True, template_name=""):
        super().__init__()
        self.engine = engine
        self.video_path = video_path
        self.segments = segments
        self.extract_hot = extract_hot
        self.extract_highlight = extract_highlight
        self.template_name = template_name
    
    def run(self):
        try:
            def progress_cb(p: int, msg: str):
                self.progress.emit(p, msg)
            
            if self.segments is not None:
                result = self.engine.compose_from_segments(
                    self.video_path,
                    self.segments,
                    callback=progress_cb,
                    use_all_segments=True,
                )
            else:
                result = self.engine.compose_from_video(
                    self.video_path,
                    callback=progress_cb,
                    extract_hot=self.extract_hot,
                    extract_highlight=self.extract_highlight
                )
            
            self.finished.emit(
                result.success,
                result.output_path,
                result.error_message,
                result.total_duration,
                result.clip_count,
                self.template_name
            )
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.error.emit(str(e))


class SmartComposeDialog(QDialog):
    """智能成片对话框"""
    
    compose_completed = pyqtSignal(str, float, str)
    
    def __init__(self, parent=None, video_path: str = "", checked_segments=None):
        super().__init__(parent)
        self.video_path = video_path
        self.checked_segments = checked_segments or []
        self.worker = None
        self.engine = None
        
        self.setWindowTitle("智能成片 - 支持多风格解说/混剪")
        self.setMinimumSize(620, 600)
        self.init_ui()
        
        self.update_mode_availability()
    
    def init_ui(self):
        layout = QVBoxLayout(self)
        
        mode_group = QGroupBox("成片模式")
        mode_layout = QVBoxLayout()
        self.mode_group = QButtonGroup()
        
        self.mode_one_click = QRadioButton("一键成片: 自动提取高燃/高光片段 → 智能排序 → 合成导出")
        self.mode_one_click.setChecked(True)
        self.mode_group.addButton(self.mode_one_click, 0)
        mode_layout.addWidget(self.mode_one_click)
        
        self.mode_use_selected = QRadioButton("使用已选片段: 使用已提取/勾选的片段直接合成")
        self.mode_group.addButton(self.mode_use_selected, 1)
        mode_layout.addWidget(self.mode_use_selected)
        
        self.selected_info_label = QLabel("  （当前没有已勾选片段，此模式不可用）")
        self.selected_info_label.setStyleSheet("color: #666; font-size: 11px;")
        mode_layout.addWidget(self.selected_info_label)
        
        mode_group.setLayout(mode_layout)
        layout.addWidget(mode_group)
        
        template_group = QGroupBox("剪辑模板")
        template_layout = QVBoxLayout()
        
        cat_row = QHBoxLayout()
        cat_row.addWidget(QLabel("成片类型:"))
        self.category_combo = QComboBox()
        categories = get_all_categories()
        for cat_key, cat_name in categories:
            self.category_combo.addItem(cat_name, cat_key)
        cat_row.addWidget(self.category_combo)
        cat_row.addStretch()
        template_layout.addLayout(cat_row)
        
        tpl_row = QHBoxLayout()
        tpl_row.addWidget(QLabel("选择风格:"))
        self.template_combo = QComboBox()
        tpl_row.addWidget(self.template_combo)
        tpl_row.addStretch()
        template_layout.addLayout(tpl_row)
        
        self.template_desc_label = QLabel("")
        self.template_desc_label.setStyleSheet("color: #666; font-size: 11px; padding-left: 20px;")
        self.template_desc_label.setWordWrap(True)
        template_layout.addWidget(self.template_desc_label)
        
        template_group.setLayout(template_layout)
        layout.addWidget(template_group)
        
        self.category_combo.currentIndexChanged.connect(self.update_template_list)
        self.template_combo.currentIndexChanged.connect(self.update_template_desc)
        self.update_template_list()
        
        options_group = QGroupBox("成片选项")
        options_layout = QVBoxLayout()
        
        dur_layout = QHBoxLayout()
        dur_layout.addWidget(QLabel("目标成片时长(秒):"))
        self.duration_spin = QSpinBox()
        self.duration_spin.setRange(15, 300)
        self.duration_spin.setValue(45)
        self.duration_spin.setSpecialValueText("自动")
        dur_layout.addWidget(self.duration_spin)
        self.duration_auto_check = QCheckBox("自动决定时长")
        self.duration_auto_check.setChecked(True)
        self.duration_auto_check.toggled.connect(lambda checked: self.duration_spin.setEnabled(not checked))
        self.duration_spin.setEnabled(False)
        dur_layout.addWidget(self.duration_auto_check)
        dur_layout.addStretch()
        options_layout.addLayout(dur_layout)
        
        self.quality_check = QCheckBox("启用质量过滤（自动过滤黑屏/模糊/抖动片段）")
        self.quality_check.setChecked(True)
        options_layout.addWidget(self.quality_check)
        
        self.beat_align_check = QCheckBox("启用节拍对齐（片段切点自动对齐BGM节拍）")
        self.beat_align_check.setChecked(True)
        options_layout.addWidget(self.beat_align_check)
        
        self.keep_audio_check = QCheckBox("保留视频原声音频")
        self.keep_audio_check.setChecked(True)
        options_layout.addWidget(self.keep_audio_check)
        
        bgm_layout = QHBoxLayout()
        self.add_bgm_check = QCheckBox("添加背景音乐（不指定则自动匹配）")
        self.add_bgm_check.setChecked(True)
        bgm_layout.addWidget(self.add_bgm_check)
        self.bgm_path_edit = QLineEdit()
        self.bgm_path_edit.setPlaceholderText("选择BGM音频文件...")
        self.bgm_path_edit.setEnabled(False)
        bgm_layout.addWidget(self.bgm_path_edit)
        self.bgm_browse_btn = QPushButton("浏览...")
        self.bgm_browse_btn.setEnabled(False)
        self.bgm_browse_btn.clicked.connect(self.browse_bgm)
        bgm_layout.addWidget(self.bgm_browse_btn)
        self.add_bgm_check.toggled.connect(lambda checked: (
            self.bgm_path_edit.setEnabled(checked),
            self.bgm_browse_btn.setEnabled(checked)
        ))
        options_layout.addLayout(bgm_layout)
        
        self.extract_hot_check = QCheckBox("提取高燃(打斗/特效)片段")
        self.extract_hot_check.setChecked(True)
        options_layout.addWidget(self.extract_hot_check)
        
        self.extract_highlight_check = QCheckBox("提取高光(搞笑/情感)片段")
        self.extract_highlight_check.setChecked(True)
        options_layout.addWidget(self.extract_highlight_check)
        
        options_group.setLayout(options_layout)
        layout.addWidget(options_group)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)
        
        self.status_label = QLabel("准备就绪")
        self.status_label.setStyleSheet("color: #666;")
        layout.addWidget(self.status_label)
        
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self.cancel_btn)
        self.start_btn = QPushButton("开始成片")
        self.start_btn.clicked.connect(self.start_compose)
        self.start_btn.setDefault(True)
        btn_layout.addWidget(self.start_btn)
        layout.addLayout(btn_layout)
        
        self.mode_one_click.toggled.connect(self.update_mode_options)
    
    def update_mode_availability(self):
        """根据是否有已选片段更新模式可用性"""
        has_segments = len(self.checked_segments) > 0
        self.mode_use_selected.setEnabled(has_segments)
        if has_segments:
            self.selected_info_label.setText(f"  （当前有 {len(self.checked_segments)} 个已勾选片段）")
            self.selected_info_label.setStyleSheet("color: #2e7d32; font-size: 11px;")
        else:
            self.selected_info_label.setText("  （当前没有已勾选片段，请先提取并勾选片段后使用此模式）")
            self.selected_info_label.setStyleSheet("color: #d32f2f; font-size: 11px;")
            if self.mode_use_selected.isChecked():
                self.mode_one_click.setChecked(True)
    
    def update_mode_options(self):
        """根据选择的模式更新选项可用性"""
        is_one_click = self.mode_one_click.isChecked()
        self.extract_hot_check.setVisible(is_one_click)
        self.extract_highlight_check.setVisible(is_one_click)
    
    def update_template_list(self):
        """根据选择的分类更新模板下拉列表"""
        cat_key = self.category_combo.currentData()
        templates = get_templates_by_category(cat_key)
        
        self.template_combo.clear()
        for t in templates:
            self.template_combo.addItem(t.display_name, t.name)
        
        self.update_template_desc()
    
    def update_template_desc(self):
        """更新模板描述文字"""
        template_name = self.template_combo.currentData()
        if not template_name:
            self.template_desc_label.setText("")
            return
        
        t = get_template_by_name(template_name)
        desc_parts = []
        
        if t.category == "commentary":
            desc_parts.append("【解说类】自动生成解说文案 + TTS配音 + 自动字幕")
            style_names = {
                "passionate": "激情高燃",
                "emotional": "情感走心",
                "humorous": "幽默搞笑",
                "calm": "平静讲述"
            }
            desc_parts.append(f"解说风格: {style_names.get(t.commentary_style, t.commentary_style)}")
        else:
            desc_parts.append("【纯混剪类】无解说，纯画面剪辑+BGM")
        
        feat_parts = []
        if t.beat_cut:
            feat_parts.append("卡点切换")
        if t.fast_cut:
            feat_parts.append("快切节奏")
        if t.color_grading != "none":
            color_names = {"warm": "暖色调", "cool": "冷色调", "high_contrast": "高对比度电影感", "vintage": "复古调色"}
            feat_parts.append(color_names.get(t.color_grading, t.color_grading))
        if t.transition_type == "flash_white":
            feat_parts.append("闪白转场")
        elif t.transition_type == "fade":
            feat_parts.append("淡入淡出")
        
        if feat_parts:
            desc_parts.append("特效: " + ", ".join(feat_parts))
        
        self.template_desc_label.setText(" | ".join(desc_parts))
    
    def browse_bgm(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择BGM文件", "",
            "音频文件 (*.mp3 *.wav *.aac *.m4a *.flac);;所有文件 (*.*)"
        )
        if path:
            self.bgm_path_edit.setText(path)
    
    def start_compose(self):
        """开始成片"""
        template_name = self.template_combo.currentData()
        template = get_template_by_name(template_name)
        
        target_dur = None if self.duration_auto_check.isChecked() else self.duration_spin.value()
        bgm_path = self.bgm_path_edit.text() if self.add_bgm_check.isChecked() else None
        if bgm_path and not os.path.exists(bgm_path):
            QMessageBox.warning(self, "警告", f"BGM文件不存在: {bgm_path}")
            return
        
        from config import CONFIG
        add_bgm = self.add_bgm_check.isChecked()
        config = SmartComposeConfig(
            template=template,
            enable_quality_filter=self.quality_check.isChecked(),
            enable_beat_align=self.beat_align_check.isChecked(),
            keep_original_audio=self.keep_audio_check.isChecked(),
            bgm_path=bgm_path if (bgm_path and os.path.exists(bgm_path)) else None,
            add_bgm=add_bgm,
            target_total_duration=target_dur,
            ffmpeg_path=CONFIG.ffmpeg_path,
            ffprobe_path=CONFIG.ffprobe_path,
        )
        
        self.engine = SmartComposeEngine(config)
        
        use_selected = self.mode_use_selected.isChecked() and len(self.checked_segments) > 0
        segments = self.checked_segments if use_selected else None
        
        template_display_name = self.template_combo.currentText()
        self.worker = SmartComposeWorker(
            self.engine, 
            self.video_path, 
            segments,
            extract_hot=self.extract_hot_check.isChecked(),
            extract_highlight=self.extract_highlight_check.isChecked(),
            template_name=template_display_name
        )
        self.worker.progress.connect(self.on_progress)
        self.worker.finished.connect(self.on_finished)
        self.worker.error.connect(self.on_error)
        
        self.start_btn.setEnabled(False)
        self.cancel_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.status_label.setText("启动中...")
        
        self.worker.start()
    
    def on_progress(self, progress: int, msg: str):
        self.progress_bar.setValue(progress)
        self.status_label.setText(msg)
    
    def on_finished(self, success: bool, output_path: str, error_msg: str, duration: float, clip_count: int, template_name: str):
        self.progress_bar.setVisible(False)
        self.start_btn.setEnabled(True)
        self.cancel_btn.setEnabled(True)
        
        if success:
            self.status_label.setText("成片完成!")
            self.compose_completed.emit(output_path, duration, template_name)
            QMessageBox.information(
                self, "成功",
                f"智能成片完成！已自动添加到素材库【智能成片】文件夹\n\n"
                f"模板: {template_name}\n"
                f"片段数: {clip_count}\n"
                f"总时长: {duration:.1f}秒\n\n"
                f"输出文件: {output_path}"
            )
            self.accept()
        else:
            self.status_label.setText("合成失败")
            QMessageBox.critical(
                self, "失败",
                f"智能成片失败:\n{error_msg}"
            )
    
    def on_error(self, error_msg: str):
        self.progress_bar.setVisible(False)
        self.start_btn.setEnabled(True)
        self.cancel_btn.setEnabled(True)
        self.status_label.setText("出错了")
        QMessageBox.critical(self, "错误", f"智能成片出错:\n{error_msg}")
