"""模块三：短剧解说页面"""
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QComboBox, QFileDialog, QGroupBox, QHBoxLayout,
                              QLabel, QLineEdit, QMessageBox, QPushButton,
                              QTextEdit, QVBoxLayout, QWidget)

from task_scheduler import Task, TaskScheduler
from .widgets import FileListWidget, TaskProgressBar
from .video_player import VideoPlayer
from module_c_shortdrama.tts_edge import EdgeTTS


class ShortDramaPage(QWidget):
    """短剧解说页面"""

    def __init__(self, scheduler: TaskScheduler, parent=None):
        super().__init__(parent)
        self._scheduler = scheduler
        self._current_task: Task = None
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        title = QLabel("模块三：短剧自动解说")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)

        desc = QLabel("输入剧情简介 → AI 润色文案 → Edge-TTS 配音 → "
                      "画面比例适配 → 合成短剧解说成片。")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        # 视频文件
        file_group = QGroupBox("视频文件")
        file_layout = QVBoxLayout(file_group)
        self._file_list = FileListWidget()
        file_layout.addWidget(self._file_list)
        layout.addWidget(file_group)

        # 参数配置
        param_group = QGroupBox("参数配置")
        param_layout = QVBoxLayout(param_group)

        # 剧情简介
        param_layout.addWidget(QLabel("剧情简介（必填）:"))
        self._plot_edit = QTextEdit()
        self._plot_edit.setPlaceholderText(
            "请输入剧情简介，AI 将基于此生成解说文案...\n"
            "例如：男主林凡被未婚妻陷害，意外获得神秘传承，"
            "从此开启逆袭之路，最终让仇人付出代价。")
        self._plot_edit.setMaximumHeight(120)
        param_layout.addWidget(self._plot_edit)

        # 比例选择
        ratio_layout = QHBoxLayout()
        ratio_layout.addWidget(QLabel("画面比例:"))
        self._ratio_combo = QComboBox()
        self._ratio_combo.addItems(["9:16", "1:1", "4:3", "16:9"])
        ratio_layout.addWidget(self._ratio_combo)
        ratio_layout.addStretch()
        param_layout.addLayout(ratio_layout)

        # 音色选择
        voice_layout = QHBoxLayout()
        voice_layout.addWidget(QLabel("配音音色:"))
        self._voice_combo = QComboBox()
        voices = EdgeTTS.list_voices()
        for name, vid in voices.items():
            self._voice_combo.addItem(name, vid)
        voice_layout.addWidget(self._voice_combo)
        voice_layout.addStretch()
        param_layout.addLayout(voice_layout)

        # 语速
        rate_layout = QHBoxLayout()
        rate_layout.addWidget(QLabel("语速:"))
        self._rate_combo = QComboBox()
        self._rate_combo.addItems(["-20%", "-10%", "+0%", "+10%", "+20%"])
        rate_layout.addWidget(self._rate_combo)
        rate_layout.addStretch()
        param_layout.addLayout(rate_layout)

        layout.addWidget(param_group)

        # 操作按钮
        btn_layout = QHBoxLayout()
        self._btn_start = QPushButton("开始生成")
        self._btn_start.clicked.connect(self._start)
        btn_layout.addWidget(self._btn_start)

        self._btn_cancel = QPushButton("取消")
        self._btn_cancel.clicked.connect(self._cancel)
        self._btn_cancel.setEnabled(False)
        btn_layout.addWidget(self._btn_cancel)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        # 进度
        self._progress = TaskProgressBar()
        layout.addWidget(self._progress)

        # 输出区
        out_group = QGroupBox("输出")
        out_layout = QVBoxLayout(out_group)
        self._result_label = QLabel("等待生成...")
        self._result_label.setWordWrap(True)
        out_layout.addWidget(self._result_label)
        layout.addWidget(out_group)

    def _start(self):
        files = self._file_list.get_files()
        if not files:
            QMessageBox.warning(self, "提示", "请先添加视频文件")
            return
        plot = self._plot_edit.toPlainText().strip()
        if not plot:
            QMessageBox.warning(self, "提示", "请输入剧情简介")
            return

        out_dir = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if not out_dir:
            return

        ratio = self._ratio_combo.currentText()
        voice = self._voice_combo.currentData()
        rate = self._rate_combo.currentText()

        from module_c_shortdrama import ShortDramaPipeline
        pipeline = ShortDramaPipeline()
        task = Task(
            module="shortdrama",
            name=f"短剧解说-{Path(files[0]).stem}",
            input_files=files,
            config={
                "plot": plot,
                "ratio": ratio,
                "voice": voice,
                "rate": rate,
                "output_dir": out_dir,
            },
            executor=lambda ctx: pipeline.execute(ctx),
            on_progress=self._on_progress,
        )
        self._current_task = task
        self._scheduler.submit(task)
        self._set_running_ui(True)
        self._result_label.setText("生成中...")

    def _cancel(self):
        if self._current_task:
            self._scheduler.cancel(self._current_task.task_id)
            self._set_running_ui(False)

    def _on_progress(self, task: Task):
        self._progress.update_task(task)
        if task.status.value in ("done", "error", "canceled"):
            self._set_running_ui(False)
            if task.status.value == "done":
                result = task.result
                self._result_label.setText(
                    f"✓ 生成完成\n"
                    f"成片: {result.get('final_video')}\n"
                    f"文案: {result.get('script_path')}\n"
                    f"配音: {result.get('audio_path')}\n"
                    f"时长: {result.get('duration', 0):.1f}s\n"
                    f"比例: {result.get('ratio')}")
                QMessageBox.information(self, "完成", "短剧解说成片已生成")
            elif task.status.value == "error":
                self._result_label.setText(f"错误: {task.error}")
                QMessageBox.critical(self, "错误", task.error)

    def _set_running_ui(self, running: bool):
        self._btn_start.setEnabled(not running)
        self._btn_cancel.setEnabled(running)
