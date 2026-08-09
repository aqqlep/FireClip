"""
剪辑解说对话框 — 独立 UI，消费已有片段数据
流程: 选片段 → 生成/编辑文案 → TTS设置 → 一键生成解说视频
"""
import os
from pathlib import Path
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QSplitter,
    QGroupBox, QLabel, QTextEdit, QComboBox, QSlider,
    QPushButton, QProgressBar, QCheckBox, QListWidget,
    QListWidgetItem, QFileDialog, QMessageBox, QSpinBox,
    QWidget, QStatusBar
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QUrl
from PyQt6.QtGui import QFont
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput

from core.commentary_producer import CommentaryProducer
from core.commentary_text_gen import STYLE_NAMES
from core.tts_engine import TTSEngine
from ui.theme import apply_theme
from utils.logger import logger
from config import CONFIG


class CommentaryWorker(QThread):
    """解说视频生成后台线程"""
    progress = pyqtSignal(str, int)  # (step_name, percent)
    finished = pyqtSignal(str)       # output_path
    error = pyqtSignal(str)          # error_msg

    def __init__(self, producer, video_path, segments, output_path,
                 style, mode, voice, speed, original_vol, commentary_vol,
                 burn_subtitles=True, bgm_path="", bgm_volume=0.2,
                 transition="\u65e0", output_format="MP4 (H.264)"):
        super().__init__()
        self.producer = producer
        self.video_path = video_path
        self.segments = segments
        self.output_path = output_path
        self.style = style
        self.mode = mode
        self.voice = voice
        self.speed = speed
        self.original_vol = original_vol
        self.commentary_vol = commentary_vol
        self.burn_subtitles = burn_subtitles
        self.bgm_path = bgm_path
        self.bgm_volume = bgm_volume
        self.transition = transition
        self.output_format = output_format

    def run(self):
        try:
            result = self.producer.produce(
                video_path=self.video_path,
                segments=self.segments,
                output_path=self.output_path,
                style=self.style,
                mode=self.mode,
                voice=self.voice,
                speed=self.speed,
                original_volume=self.original_vol,
                commentary_volume=self.commentary_vol,
                burn_subtitles=self.burn_subtitles,
                bgm_path=self.bgm_path,
                bgm_volume=self.bgm_volume,
                transition=self.transition,
                output_format=self.output_format,
                progress_callback=lambda name, pct: self.progress.emit(name, pct)
            )
            if result:
                self.finished.emit(result)
            elif self.producer._cancel:
                self.error.emit("用户已取消生成")
            else:
                self.error.emit("解说视频生成失败，请查看日志")
        except Exception as e:
            self.error.emit(str(e))


class TextGenWorker(QThread):
    """文案生成后台线程"""
    progress = pyqtSignal(str, int)
    finished = pyqtSignal(dict)  # commentary dict
    error = pyqtSignal(str)

    def __init__(self, producer, segments, style, mode):
        super().__init__()
        self.producer = producer
        self.segments = segments
        self.style = style
        self.mode = mode

    def run(self):
        try:
            result = self.producer.generate_text_only(
                segments=self.segments,
                style=self.style,
                mode=self.mode,
                progress_callback=lambda name, pct: self.progress.emit(name, pct)
            )
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))


class CommentaryDialog(QDialog):
    """剪辑解说对话框"""

    def __init__(self, parent, video_path: str, scenes: list):
        super().__init__(parent)
        self.setWindowTitle("剪辑解说")
        self.setMinimumSize(900, 650)
        self.resize(1050, 700)

        self.video_path = video_path
        self.scenes = scenes  # 来自 material_panel 的已有片段
        self.producer = CommentaryProducer()
        self._worker = None
        self._text_gen_worker = None
        self._commentary_data = None  # 当前文案数据

        self._init_ui()
        self._populate_segments()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # ====== 顶部: 源视频信息 ======
        info_layout = QHBoxLayout()
        self.video_label = QLabel(f"源视频: {Path(self.video_path).name}" if self.video_path else "未加载视频")
        self.video_label.setStyleSheet("font-weight: bold; font-size: 13px;")
        info_layout.addWidget(self.video_label)
        info_layout.addStretch()
        self.seg_count_label = QLabel(f"可用片段: {len(self.scenes)}个")
        info_layout.addWidget(self.seg_count_label)
        layout.addLayout(info_layout)

        # ====== 主区域: 三栏布局 ======
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # --- 左侧: 片段选择 ---
        seg_widget = QWidget()
        seg_layout = QVBoxLayout(seg_widget)
        seg_layout.setContentsMargins(0, 0, 0, 0)

        seg_header = QHBoxLayout()
        seg_header.addWidget(QLabel("选择片段"))
        select_all_btn = QPushButton("全选")
        select_all_btn.setFixedWidth(50)
        select_all_btn.clicked.connect(self._select_all_segments)
        seg_header.addWidget(select_all_btn)
        deselect_all_btn = QPushButton("全不选")
        deselect_all_btn.setFixedWidth(50)
        deselect_all_btn.clicked.connect(self._deselect_all_segments)
        seg_header.addWidget(deselect_all_btn)
        seg_layout.addLayout(seg_header)

        self.segment_list = QListWidget()
        self.segment_list.setMaximumWidth(280)
        seg_layout.addWidget(self.segment_list)

        splitter.addWidget(seg_widget)

        # --- 中间: 文案编辑 + TTS设置 ---
        center_widget = QWidget()
        center_layout = QVBoxLayout(center_widget)
        center_layout.setContentsMargins(4, 0, 4, 0)

        # 文案编辑区
        text_group = QGroupBox("解说文案")
        text_layout = QVBoxLayout(text_group)

        # 生成控制行
        gen_layout = QHBoxLayout()
        gen_layout.addWidget(QLabel("风格:"))
        self.style_combo = QComboBox()
        self.style_combo.addItems(STYLE_NAMES)
        gen_layout.addWidget(self.style_combo)

        gen_layout.addWidget(QLabel("模式:"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["模板生成", "AI生成"])
        self.mode_combo.setToolTip("AI生成需要Qwen2.5模型，较慢但更自然")
        gen_layout.addWidget(self.mode_combo)

        self.gen_text_btn = QPushButton("生成文案")
        self.gen_text_btn.clicked.connect(self._generate_text)
        gen_layout.addWidget(self.gen_text_btn)
        gen_layout.addStretch()
        text_layout.addLayout(gen_layout)

        self.text_edit = QTextEdit()
        self.text_edit.setPlaceholderText("点击\"生成文案\"自动生成，或在此手动输入解说文案...\n\n支持按空行分段，每段对应一个片段。")
        self.text_edit.setMinimumHeight(200)
        text_layout.addWidget(self.text_edit)

        center_layout.addWidget(text_group, stretch=3)

        # TTS 设置
        tts_group = QGroupBox("语音设置")
        tts_layout = QVBoxLayout(tts_group)

        # 声音选择
        voice_layout = QHBoxLayout()
        voice_layout.addWidget(QLabel("\u58f0\u97f3:"))
        self.voice_combo = QComboBox()
        self.voice_combo.addItems(TTSEngine.VOICE_NAMES)
        self.voice_combo.setCurrentIndex(1)  # \u9ed8\u8ba4\u9633\u5149\u6d3b\u6cfc(\u7537)
        voice_layout.addWidget(self.voice_combo)
        
        # \u8bd5\u542c\u6309\u94ae
        self.preview_btn = QPushButton("\u266a \u8bd5\u542c")
        self.preview_btn.setFixedWidth(60)
        self.preview_btn.setToolTip("\u8bd5\u542c\u5f53\u524d\u9009\u62e9\u7684\u58f0\u97f3")
        self.preview_btn.clicked.connect(self._preview_voice)
        voice_layout.addWidget(self.preview_btn)

        voice_layout.addWidget(QLabel("语速:"))
        self.speed_slider = QSlider(Qt.Orientation.Horizontal)
        self.speed_slider.setRange(50, 200)
        self.speed_slider.setValue(100)
        self.speed_slider.setFixedWidth(120)
        self.speed_slider.setToolTip("语速调节 (50%-200%)")
        voice_layout.addWidget(self.speed_slider)
        self.speed_label = QLabel("1.0x")
        self.speed_label.setFixedWidth(40)
        self.speed_slider.valueChanged.connect(
            lambda v: self.speed_label.setText(f"{v/100:.1f}x")
        )
        voice_layout.addWidget(self.speed_label)
        voice_layout.addStretch()
        tts_layout.addLayout(voice_layout)

        # 音量设置
        vol_layout = QHBoxLayout()
        vol_layout.addWidget(QLabel("原声音量:"))
        self.orig_vol_slider = QSlider(Qt.Orientation.Horizontal)
        self.orig_vol_slider.setRange(0, 100)
        self.orig_vol_slider.setValue(30)
        self.orig_vol_slider.setFixedWidth(100)
        vol_layout.addWidget(self.orig_vol_slider)
        self.orig_vol_label = QLabel("30%")
        self.orig_vol_label.setFixedWidth(35)
        self.orig_vol_slider.valueChanged.connect(
            lambda v: self.orig_vol_label.setText(f"{v}%")
        )
        vol_layout.addWidget(self.orig_vol_label)

        vol_layout.addWidget(QLabel("解说音量:"))
        self.comm_vol_slider = QSlider(Qt.Orientation.Horizontal)
        self.comm_vol_slider.setRange(0, 200)
        self.comm_vol_slider.setValue(100)
        self.comm_vol_slider.setFixedWidth(100)
        vol_layout.addWidget(self.comm_vol_slider)
        self.comm_vol_label = QLabel("100%")
        self.comm_vol_label.setFixedWidth(40)
        self.comm_vol_slider.valueChanged.connect(
            lambda v: self.comm_vol_label.setText(f"{v}%")
        )
        vol_layout.addWidget(self.comm_vol_label)
        vol_layout.addStretch()
        tts_layout.addLayout(vol_layout)

        center_layout.addWidget(tts_group, stretch=1)

        splitter.addWidget(center_widget)

        # --- 右侧: 输出设置 ---
        output_widget = QWidget()
        output_layout = QVBoxLayout(output_widget)
        output_layout.setContentsMargins(4, 0, 4, 0)

        out_group = QGroupBox("输出设置")
        out_layout = QVBoxLayout(out_group)

        out_layout.addWidget(QLabel("输出路径:"))
        out_path_layout = QHBoxLayout()
        self.output_edit = QLabel()
        self.output_edit.setWordWrap(True)
        self._set_default_output()
        out_path_layout.addWidget(self.output_edit, stretch=1)
        browse_btn = QPushButton("浏览")
        browse_btn.clicked.connect(self._browse_output)
        out_path_layout.addWidget(browse_btn)
        out_layout.addLayout(out_path_layout)

        # V3: 字幕烧录
        self.burn_subtitles_cb = QCheckBox("烧录字幕")
        self.burn_subtitles_cb.setToolTip("将解说字幕烧录到视频中，无需单独字幕文件")
        self.burn_subtitles_cb.setChecked(True)
        out_layout.addWidget(self.burn_subtitles_cb)

        # V3: BGM 支持
        bgm_layout = QHBoxLayout()
        self.bgm_cb = QCheckBox("添加背景音乐:")
        self.bgm_cb.setToolTip("为解说视频添加背景音乐")
        bgm_layout.addWidget(self.bgm_cb)
        self.bgm_path_edit = QLabel("")
        self.bgm_path_edit.setWordWrap(True)
        self.bgm_path_edit.setStyleSheet("color: #888;")
        bgm_layout.addWidget(self.bgm_path_edit, stretch=1)
        bgm_browse_btn = QPushButton("选择")
        bgm_browse_btn.setFixedWidth(50)
        bgm_browse_btn.clicked.connect(self._browse_bgm)
        bgm_layout.addWidget(bgm_browse_btn)
        out_layout.addLayout(bgm_layout)

        # BGM 音量
        bgm_vol_layout = QHBoxLayout()
        bgm_vol_layout.addWidget(QLabel("BGM音量:"))
        self.bgm_vol_slider = QSlider(Qt.Orientation.Horizontal)
        self.bgm_vol_slider.setRange(0, 100)
        self.bgm_vol_slider.setValue(20)
        self.bgm_vol_slider.setFixedWidth(80)
        bgm_vol_layout.addWidget(self.bgm_vol_slider)
        self.bgm_vol_label = QLabel("20%")
        self.bgm_vol_label.setFixedWidth(35)
        self.bgm_vol_slider.valueChanged.connect(
            lambda v: self.bgm_vol_label.setText(f"{v}%")
        )
        bgm_vol_layout.addWidget(self.bgm_vol_label)
        bgm_vol_layout.addStretch()
        out_layout.addLayout(bgm_vol_layout)

        # V3: 片段转场
        trans_layout = QHBoxLayout()
        trans_layout.addWidget(QLabel("片段转场:"))
        self.transition_combo = QComboBox()
        self.transition_combo.addItems(["无", "淡入淡出", "黑场过渡"])
        self.transition_combo.setFixedWidth(120)
        trans_layout.addWidget(self.transition_combo)
        trans_layout.addStretch()
        out_layout.addLayout(trans_layout)

        # V3: 输出格式
        fmt_layout = QHBoxLayout()
        fmt_layout.addWidget(QLabel("输出格式:"))
        self.format_combo = QComboBox()
        self.format_combo.addItems(["MP4 (H.264)", "MP4 (H.265/HEVC)", "MOV (ProRes)", "WEBM (VP9)"])
        self.format_combo.setFixedWidth(160)
        fmt_layout.addWidget(self.format_combo)
        fmt_layout.addStretch()
        out_layout.addLayout(fmt_layout)

        out_layout.addSpacing(16)

        # 进度
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)
        out_layout.addWidget(self.progress_bar)

        self.status_label = QLabel("就绪")
        out_layout.addWidget(self.status_label)

        out_layout.addSpacing(16)

        # 生成/取消按钮
        btn_layout = QHBoxLayout()
        self.produce_btn = QPushButton("生成解说视频")
        self.produce_btn.setMinimumHeight(44)
        self.produce_btn.setStyleSheet(
            "QPushButton { font-size: 15px; font-weight: bold; padding: 10px; }"
        )
        self.produce_btn.clicked.connect(self._produce_video)
        btn_layout.addWidget(self.produce_btn)

        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setMinimumHeight(44)
        self.cancel_btn.setStyleSheet(
            "QPushButton { font-size: 14px; padding: 10px; }"
        )
        self.cancel_btn.clicked.connect(self._cancel_production)
        self.cancel_btn.setVisible(False)  # 默认隐藏，生成时才显示
        btn_layout.addWidget(self.cancel_btn)

        out_layout.addLayout(btn_layout)

        out_layout.addStretch()

        output_layout.addWidget(out_group)

        splitter.addWidget(output_widget)

        # 设置分割比例
        splitter.setSizes([250, 500, 250])
        layout.addWidget(splitter, stretch=1)

    # ================================================================
    # 片段列表
    # ================================================================
    def _populate_segments(self):
        """填充片段列表"""
        self.segment_list.clear()
        for i, scene in enumerate(self.scenes):
            start = scene.get("start_time", 0)
            end = scene.get("end_time", 0)
            duration = end - start
            scene_type = scene.get("scene_type", "unknown")
            desc = scene.get("description", "")[:25]
            text = f"[{scene_type}] {start:.0f}s-{end:.0f}s ({duration:.0f}s) {desc}"
            item = QListWidgetItem(text)
            item.setCheckState(Qt.CheckState.Checked)
            item.setData(Qt.ItemDataRole.UserRole, scene)
            self.segment_list.addItem(item)
        self._update_duration_estimate()

    def _select_all_segments(self):
        for i in range(self.segment_list.count()):
            self.segment_list.item(i).setCheckState(Qt.CheckState.Checked)
        self._update_duration_estimate()

    def _deselect_all_segments(self):
        for i in range(self.segment_list.count()):
            self.segment_list.item(i).setCheckState(Qt.CheckState.Unchecked)
        self._update_duration_estimate()

    def _get_selected_segments(self) -> list:
        """获取勾选的片段"""
        selected = []
        for i in range(self.segment_list.count()):
            item = self.segment_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                selected.append(item.data(Qt.ItemDataRole.UserRole))
        return selected

    def _update_duration_estimate(self):
        """更新预估输出时长"""
        selected = self._get_selected_segments()
        total_sec = sum(s.get("end_time", 0) - s.get("start_time", 0) for s in selected)
        m, s = divmod(int(total_sec), 60)
        self.seg_count_label.setText(f"已选 {len(selected)}/{len(self.scenes)} 段, 预计约 {m}分{s}秒")

    # ================================================================
    # 文案生成
    # ================================================================
    def _generate_text(self):
        """生成解说文案"""
        segments = self._get_selected_segments()
        if not segments:
            QMessageBox.warning(self, "提示", "请至少选择一个片段")
            return

        style = self.style_combo.currentText()
        mode = "template" if self.mode_combo.currentIndex() == 0 else "ai"

        self.gen_text_btn.setEnabled(False)
        self.gen_text_btn.setText("生成中...")
        self.status_label.setText("正在生成文案...")

        self._text_gen_worker = TextGenWorker(
            self.producer, segments, style, mode
        )
        self._text_gen_worker.progress.connect(self._on_text_gen_progress)
        self._text_gen_worker.finished.connect(self._on_text_gen_finished)
        self._text_gen_worker.error.connect(self._on_text_gen_error)
        self._text_gen_worker.start()

    def _on_text_gen_progress(self, msg, pct):
        self.status_label.setText(f"{msg} ({pct}%)")

    def _on_text_gen_finished(self, commentary: dict):
        self.gen_text_btn.setEnabled(True)
        self.gen_text_btn.setText("生成文案")
        self._commentary_data = commentary
        self.text_edit.setPlainText(commentary.get("full_text", ""))
        seg_count = len(commentary.get("segments", []))
        self.status_label.setText(f"文案生成完成: {seg_count}段, {len(commentary.get('full_text', ''))}字")

    def _on_text_gen_error(self, msg):
        self.gen_text_btn.setEnabled(True)
        self.gen_text_btn.setText("生成文案")
        self.status_label.setText(f"文案生成失败: {msg}")
        QMessageBox.warning(self, "生成失败", f"文案生成失败:\n{msg}")

    # ================================================================
    # 生成解说视频
    # ================================================================
    def _produce_video(self):
        """一键生成解说视频"""
        segments = self._get_selected_segments()
        if not segments:
            QMessageBox.warning(self, "提示", "请至少选择一个片段")
            return

        # 确保有文案
        text = self.text_edit.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "提示", "请先生成或输入解说文案")
            return

        output_path = self.output_edit.text()
        if not output_path:
            QMessageBox.warning(self, "提示", "请设置输出路径")
            return

        # 如果文案被手动编辑过，重新解析
        style = self.style_combo.currentText()
        mode = "template" if self.mode_combo.currentIndex() == 0 else "ai"

        if self._commentary_data is None or text != self._commentary_data.get("full_text", ""):
            # 文案被手动编辑，需要重新分段
            from core.commentary_text_gen import CommentaryTextGenerator
            self._commentary_data = CommentaryTextGenerator.split_manual_text(text, segments)

        voice = self.voice_combo.currentText()  # \u76f4\u63a5\u4f20\u663e\u793a\u540d\uff0cTTSEngine \u5185\u90e8\u89e3\u6790
        speed = self.speed_slider.value() / 100.0
        orig_vol = self.orig_vol_slider.value() / 100.0
        comm_vol = self.comm_vol_slider.value() / 100.0
        
        # V3 \u53c2\u6570
        burn_subtitles = self.burn_subtitles_cb.isChecked()
        bgm_path = self.bgm_path_edit.text() if self.bgm_cb.isChecked() else ""
        bgm_volume = self.bgm_vol_slider.value() / 100.0
        transition = self.transition_combo.currentText()
        output_format = self.format_combo.currentText()
        
        # UI\u72b6\u6001
        self.produce_btn.setEnabled(False)
        self.produce_btn.setText("\u751f\u6210\u4e2d...")
        self.cancel_btn.setVisible(True)  # \u663e\u793a\u53d6\u6d88\u6309\u94ae
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        
        self._worker = CommentaryWorker(
            self.producer, self.video_path, segments,
            output_path, style, mode, voice, speed,
            orig_vol, comm_vol,
            burn_subtitles=burn_subtitles,
            bgm_path=bgm_path,
            bgm_volume=bgm_volume,
            transition=transition,
            output_format=output_format
        )
        self._worker.progress.connect(self._on_produce_progress)
        self._worker.finished.connect(self._on_produce_finished)
        self._worker.error.connect(self._on_produce_error)

        # 传入文案数据
        self._worker.segments = self._commentary_data.get("segments", segments)
        self._worker.start()

    def _cancel_production(self):
        """取消当前生成"""
        if self._worker and self._worker.isRunning():
            self.producer.cancel()  # 设置取消标志
            self.cancel_btn.setEnabled(False)
            self.cancel_btn.setText("正在取消...")
            self.status_label.setText("正在取消...")
            logger.info("用户取消了解说视频生成")

    def _on_produce_progress(self, step, pct):
        self.progress_bar.setValue(pct)
        self.status_label.setText(f"{step} ({pct}%)")

    def _on_produce_finished(self, output_path):
        self.produce_btn.setEnabled(True)
        self.produce_btn.setText("生成解说视频")
        self.cancel_btn.setVisible(False)  # 隐藏取消按钮
        self.cancel_btn.setEnabled(True)
        self.cancel_btn.setText("取消")
        self.progress_bar.setValue(100)
        self.status_label.setText("解说视频生成完成！")

        QMessageBox.information(
            self, "\u751f\u6210\u5b8c\u6210",
            f"\u89e3\u8bf4\u89c6\u9891\u5df2\u751f\u6210\uff01\n\n\u8f93\u51fa\u8def\u5f84: {output_path}\n\n"
            f"\u539f\u58f0\u97f3\u91cf: {self.orig_vol_slider.value()}%\n"
            f"\u89e3\u8bf4\u97f3\u91cf: {self.comm_vol_slider.value()}%"
        )
        
        # \u63d0\u4f9b\u6253\u5f00\u6587\u4ef6\u5939\u6309\u94ae
        open_btn = QMessageBox.information(
            self, "\u6253\u5f00\u6587\u4ef6\u5939",
            f"\u89e3\u8bf4\u89c6\u9891\u5df2\u751f\u6210\uff01\n\u8def\u5f84: {output_path}\n\n\u662f\u5426\u6253\u5f00\u8f93\u51fa\u76ee\u5f55\uff1f",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if open_btn == QMessageBox.StandardButton.Yes:
            os.startfile(os.path.dirname(output_path))

    def _on_produce_error(self, msg):
        self.produce_btn.setEnabled(True)
        self.produce_btn.setText("生成解说视频")
        self.cancel_btn.setVisible(False)  # 隐藏取消按钮
        self.cancel_btn.setEnabled(True)
        self.cancel_btn.setText("取消")
        self.progress_bar.setVisible(False)

        if "取消" in msg:
            # 用户取消，不需要错误弹窗
            self.status_label.setText("已取消")
            logger.info("解说视频生成已取消")
        else:
            self.status_label.setText(f"生成失败: {msg}")
            QMessageBox.critical(self, "生成失败", f"解说视频生成失败:\n{msg}")

    # ================================================================
    # 输出路径
    # ================================================================
    def _set_default_output(self):
        if self.video_path:
            stem = Path(self.video_path).stem
            default_dir = str(Path(self.video_path).parent)
            default_path = os.path.join(default_dir, f"{stem}_解说.mp4")
            self.output_edit.setText(default_path)

    def _browse_output(self):
        current = self.output_edit.text()
        start_dir = str(Path(current).parent) if current else ""
        path, _ = QFileDialog.getSaveFileName(
            self, "选择输出路径", start_dir or "",
            "视频文件 (*.mp4 *.mk4);;所有文件 (*)"
        )
        if path:
            self.output_edit.setText(path)

    def _browse_bgm(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择背景音乐", "",
            "音频文件 (*.mp3 *.wav *.ogg *.flac *.m4a);;所有文件 (*)"
        )
        if path:
            self.bgm_path_edit.setText(path)
            self.bgm_path_edit.setStyleSheet("color: inherit;")
            self.bgm_cb.setChecked(True)

    # ================================================================
    # \u8bd5\u542c\u58f0\u97f3
    # ================================================================
    def _preview_voice(self):
        """\u8bd5\u542c\u5f53\u524d\u9009\u62e9\u7684\u58f0\u97f3"""
        voice_name = self.voice_combo.currentText()
        self.preview_btn.setEnabled(False)
        self.preview_btn.setText("\u266a \u5408\u6210\u4e2d...")
        self.status_label.setText(f"\u6b63\u5728\u8bd5\u542c {voice_name}...")

        # \u5728\u540e\u53f0\u7ebf\u7a0b\u5408\u6210\u8bd5\u542c\u97f3\u9891
        import tempfile
        self._preview_path = os.path.join(tempfile.gettempdir(), "fireclip_voice_preview.mp3")

        # \u66f4\u65b0\u5f53\u524d\u58f0\u97f3\u914d\u7f6e\uff0c\u4e34\u65f6\u5207\u6362
        old_engine = self.producer.tts.engine
        self.producer.tts.engine = "edge-tts"

        sample_text = "\u6b22\u8fce\u89c2\u770b\u89e3\u8bf4\u89c6\u9891\uff0c\u8fd9\u662f\u58f0\u97f3\u8bd5\u542c\u6837\u4f8b\u3002"
        success = self.producer.tts.synthesize(sample_text, self._preview_path, voice=voice_name, speed=1.0)

        self.producer.tts.engine = old_engine

        if success and os.path.exists(self._preview_path):
            # \u64ad\u653e\u8bd5\u542c\u97f3\u9891
            try:
                if not hasattr(self, '_preview_player'):
                    self._preview_player = QMediaPlayer()
                    self._preview_audio_output = QAudioOutput()
                    self._preview_player.setAudioOutput(self._preview_audio_output)
                self._preview_player.setSource(QUrl.fromLocalFile(self._preview_path))
                self._preview_player.play()
                self.status_label.setText(f"\u8bd5\u542c\u4e2d: {voice_name}")
            except Exception as e:
                self.status_label.setText(f"\u8bd5\u542c\u64ad\u653e\u5931\u8d25: {e}")
        else:
            self.status_label.setText("\u8bd5\u542c\u5408\u6210\u5931\u8d25\uff0c\u8bf7\u68c0\u67e5\u7f51\u7edc\u8fde\u63a5")

        self.preview_btn.setEnabled(True)
        self.preview_btn.setText("\u266a \u8bd5\u542c")

    def closeEvent(self, event):
        """关闭时清理"""
        if self._worker and self._worker.isRunning():
            self.producer.cancel()  # 先发取消信号
            self._worker.quit()
            self._worker.wait(3000)
        if self._text_gen_worker and self._text_gen_worker.isRunning():
            self._text_gen_worker.quit()
            self._text_gen_worker.wait(3000)
        # 卸载模型释放显存
        self.producer.unload_models()
        event.accept()