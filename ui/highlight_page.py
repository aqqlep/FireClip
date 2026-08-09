"""模块二：高光提取页面"""
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QFileDialog, QGroupBox, QHBoxLayout, QLabel,
                              QMessageBox, QPushButton, QVBoxLayout, QWidget)

from task_scheduler import Task, TaskScheduler
from .widgets import ClipGridWidget, FileListWidget, TaskProgressBar


class HighlightPage(QWidget):
    """高光提取页面"""

    def __init__(self, scheduler: TaskScheduler, parent=None):
        super().__init__(parent)
        self._scheduler = scheduler
        self._current_task: Task = None
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        title = QLabel("模块二：高光提取")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)

        desc = QLabel("基于画面规则与音频特征双维度提取高光片段，"
                      "支持镜头切换、人脸特写、音量峰值检测。")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        # 文件列表
        file_group = QGroupBox("视频文件")
        file_layout = QVBoxLayout(file_group)
        self._file_list = FileListWidget()
        file_layout.addWidget(self._file_list)
        layout.addWidget(file_group)

        # 操作按钮
        btn_layout = QHBoxLayout()
        self._btn_start = QPushButton("开始提取")
        self._btn_start.clicked.connect(self._start)
        btn_layout.addWidget(self._btn_start)

        self._btn_pause = QPushButton("暂停")
        self._btn_pause.clicked.connect(self._pause)
        self._btn_pause.setEnabled(False)
        btn_layout.addWidget(self._btn_pause)

        self._btn_cancel = QPushButton("取消")
        self._btn_cancel.clicked.connect(self._cancel)
        self._btn_cancel.setEnabled(False)
        btn_layout.addWidget(self._btn_cancel)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        # 进度
        self._progress = TaskProgressBar()
        layout.addWidget(self._progress)

        # 结果
        result_group = QGroupBox("提取结果")
        result_layout = QVBoxLayout(result_group)
        self._clip_grid = ClipGridWidget()
        result_layout.addWidget(self._clip_grid)

        export_layout = QHBoxLayout()
        btn_export_local = QPushButton("导出选中片段")
        btn_export_local.clicked.connect(self._export_local)
        export_layout.addWidget(btn_export_local)

        btn_export_jy = QPushButton("生成剪映草稿")
        btn_export_jy.clicked.connect(self._export_jianying)
        export_layout.addWidget(btn_export_jy)
        export_layout.addStretch()
        result_layout.addLayout(export_layout)
        layout.addWidget(result_group, stretch=1)

    def _start(self):
        files = self._file_list.get_files()
        if not files:
            QMessageBox.warning(self, "提示", "请先添加视频文件")
            return
        from module_b_highlight import HighlightPipeline
        pipeline = HighlightPipeline()
        task = Task(
            module="highlight",
            name=f"高光提取-{Path(files[0]).stem}",
            input_files=files,
            executor=lambda ctx: pipeline.execute(ctx),
            on_progress=self._on_progress,
        )
        self._current_task = task
        self._scheduler.submit(task)
        self._set_running_ui(True)

    def _pause(self):
        if self._current_task:
            if self._current_task.status.value == "paused":
                self._scheduler.resume(self._current_task.task_id)
                self._btn_pause.setText("暂停")
            else:
                self._scheduler.pause(self._current_task.task_id)
                self._btn_pause.setText("继续")

    def _cancel(self):
        if self._current_task:
            self._scheduler.cancel(self._current_task.task_id)
            self._set_running_ui(False)

    def _on_progress(self, task: Task):
        self._progress.update_task(task)
        if task.status.value in ("done", "error", "canceled"):
            self._set_running_ui(False)
            if task.status.value == "done":
                self._clip_grid.set_clips(task.result.get("clips", []))
                QMessageBox.information(
                    self, "完成",
                    f"提取完成，共 {task.result.get('total', 0)} 个片段")
            elif task.status.value == "error":
                QMessageBox.critical(self, "错误", task.error)

    def _export_local(self):
        clips = self._clip_grid.get_selected_clips()
        if not clips:
            QMessageBox.warning(self, "提示", "无选中片段")
            return
        out_dir = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if not out_dir:
            return
        from module_b_highlight import HighlightPipeline
        pipeline = HighlightPipeline()
        result = pipeline.export_local(clips, out_dir)
        QMessageBox.information(
            self, "导出完成",
            f"已导出 {result['count']} 个片段到:\n{out_dir}")

    def _export_jianying(self):
        clips = self._clip_grid.get_selected_clips()
        if not clips:
            QMessageBox.warning(self, "提示", "无选中片段")
            return
        from module_b_highlight import HighlightPipeline
        pipeline = HighlightPipeline()
        result = pipeline.export_jianying(clips, "高光片段")
        if result.get("success"):
            QMessageBox.information(
                self, "成功",
                f"剪映草稿已生成:\n{result.get('draft_dir')}")
        else:
            QMessageBox.critical(self, "失败", result.get("error", "未知错误"))

    def _set_running_ui(self, running: bool):
        self._btn_start.setEnabled(not running)
        self._btn_pause.setEnabled(running)
        self._btn_cancel.setEnabled(running)
