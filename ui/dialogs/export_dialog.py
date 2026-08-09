"""
导出对话框
提供视频导出配置界面
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QComboBox, QLineEdit, QPushButton, QFileDialog,
    QGroupBox, QRadioButton, QCheckBox, QSpinBox, QProgressBar,
    QTextEdit, QMessageBox
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from pathlib import Path
import os, shutil
from utils.logger import logger
from utils.helpers import get_video_info
from config import CONFIG


class ExportWorker(QThread):
    """导出工作线程"""
    progress = pyqtSignal(int, str)  # 进度百分比, 状态描述
    finished = pyqtSignal(bool, str)  # 是否成功, 输出路径
    error = pyqtSignal(str)  # 错误信息
    
    def __init__(self, export_config: dict):
        super().__init__()
        self.export_config = export_config
    
    def run(self):
        """执行导出"""
        try:
            from core.video_processor import VideoProcessor
            
            video_path = self.export_config.get("video_path", "")
            output_path = self.export_config.get("output_path", "")
            segments = self.export_config.get("segments", [])
            per_segment = self.export_config.get("per_segment", False)
            
            processor = VideoProcessor()
            
            # 如果没有片段，导出整个视频
            if not segments:
                self.progress.emit(10, "准备导出完整视频...")
                info = get_video_info(video_path, CONFIG.ffmpeg_path)
                duration = info.get('duration', 0)
                
                success = processor.extract_segment(
                    video_path, 0, duration, output_path
                )
                
                if success:
                    self.progress.emit(100, "导出完成")
                    self.finished.emit(True, output_path)
                else:
                    self.finished.emit(False, "导出失败")
                return
            
            # 按场景类型分文件夹导出
            self.progress.emit(5, "开始导出...")
            output_dir = str(Path(output_path).parent)
            video_stem = Path(video_path).stem
            total = len(segments)
            success_count = 0
            
            # 只分2个文件夹：高燃片段 vs 高光片段
            # 按场景类型分组
            type_folder_names = {
                "action": "高燃片段", "vfx_action": "高燃片段", "vfx_spectacle": "高燃片段",
                "highlight": "高光片段", "dialog": "高光片段",
                "emotion": "高光片段", "climax": "高光片段",
                "unknown": "高光片段"
            }
            grouped = {}
            for seg in segments:
                st = seg.get("scene_type", "unknown")
                folder_name = type_folder_names.get(st, "高光片段")
                grouped.setdefault(folder_name, []).append(seg)
            
            # 创建子文件夹并导出（不加序号，固定2个文件夹）
            for folder_name, folder_segs in grouped.items():
                sub_dir = os.path.join(output_dir, folder_name)
                os.makedirs(sub_dir, exist_ok=True)
                
                for i, segment in enumerate(folder_segs):
                    progress = int(5 + ((success_count + i) / total) * 90)
                    self.progress.emit(progress, f"导出 {folder_name} {i+1}/{len(folder_segs)}...")
                    
                    start_time = float(segment.get("start_time", 0))
                    end_time = float(segment.get("end_time", 0))
                    duration = max(end_time - start_time, 1.0)
                    
                    # 简化文件名：类型_序号_开始时间-结束时间.mp4
                    seg_filename = f"{folder_name}_{i+1:02d}_{int(start_time)}s-{int(end_time)}s.mp4"
                    seg_path = os.path.join(sub_dir, seg_filename)
                    
                    success = processor.extract_segment(
                        video_path, start_time, duration, seg_path
                    )
                    
                    if success:
                        success_count += 1
                    else:
                        logger.error(f"片段导出失败: {seg_filename}")
            
            if success_count > 0:
                self.progress.emit(100, f"导出完成: {success_count}/{total}")
                folder_count = len(grouped)
                self.finished.emit(True, f"{output_dir} (成功{success_count}/{total}个片段, {folder_count}个文件夹)")
            else:
                self.finished.emit(False, "没有成功导出的片段")
            return
                
        except Exception as e:
            logger.error(f"导出失败: {e}")
            import traceback
            traceback.print_exc()
            self.error.emit(str(e))
            self.finished.emit(False, str(e))


class ExportDialog(QDialog):
    """导出对话框"""
    
    def __init__(self, parent=None, video_path: str = "", segments: list = None):
        super().__init__(parent)
        self.video_path = video_path
        self.segments = segments or []
        self.export_worker = None
        
        self.setWindowTitle("导出视频")
        self.setMinimumWidth(600)
        self.setMinimumHeight(500)
        
        self.init_ui()
    
    def init_ui(self):
        """初始化UI"""
        layout = QVBoxLayout(self)
        
        # 片段信息提示
        seg_count = len(self.segments)
        if seg_count > 0:
            info_label = QLabel(f"当前共有 {seg_count} 个分析片段")
            info_label.setStyleSheet("color: #89b4fa; font-size: 13px; font-weight: bold; padding: 5px;")
            layout.addWidget(info_label)
        
        # 输出设置组
        output_group = QGroupBox("输出设置")
        output_layout = QGridLayout()
        
        # 输出路径
        output_layout.addWidget(QLabel("输出路径:"), 0, 0)
        self.output_path_edit = QLineEdit()
        self.output_path_edit.setPlaceholderText("选择输出文件路径...")
        output_layout.addWidget(self.output_path_edit, 0, 1)
        
        browse_btn = QPushButton("浏览...")
        browse_btn.clicked.connect(self.browse_output)
        output_layout.addWidget(browse_btn, 0, 2)
        
        # 输出格式
        output_layout.addWidget(QLabel("输出格式:"), 1, 0)
        self.format_combo = QComboBox()
        self.format_combo.addItems(["MP4", "MKV", "AVI", "MOV"])
        self.format_combo.setCurrentText("MP4")
        output_layout.addWidget(self.format_combo, 1, 1)
        
        output_group.setLayout(output_layout)
        layout.addWidget(output_group)
        
        # 视频设置组
        video_group = QGroupBox("视频设置")
        video_layout = QGridLayout()
        
        # 分辨率
        video_layout.addWidget(QLabel("分辨率:"), 0, 0)
        self.resolution_combo = QComboBox()
        self.resolution_combo.addItems(["原始分辨率", "1080p (1920x1080)", "720p (1280x720)", "480p (854x480)"])
        self.resolution_combo.setCurrentIndex(0)
        video_layout.addWidget(self.resolution_combo, 0, 1)
        
        # 编码格式
        video_layout.addWidget(QLabel("编码格式:"), 1, 0)
        self.codec_combo = QComboBox()
        self.codec_combo.addItems(["H.264 (推荐)", "H.265/HEVC", "VP9"])
        self.codec_combo.setCurrentIndex(0)
        video_layout.addWidget(self.codec_combo, 1, 1)
        
        # 质量
        video_layout.addWidget(QLabel("质量:"), 2, 0)
        self.quality_combo = QComboBox()
        self.quality_combo.addItems(["高 (推荐)", "中", "低", "自定义"])
        self.quality_combo.setCurrentIndex(0)
        video_layout.addWidget(self.quality_combo, 2, 1)
        
        # 帧率
        video_layout.addWidget(QLabel("帧率:"), 3, 0)
        self.fps_combo = QComboBox()
        self.fps_combo.addItems(["原始帧率", "60 fps", "30 fps", "24 fps"])
        self.fps_combo.setCurrentIndex(0)
        video_layout.addWidget(self.fps_combo, 3, 1)
        
        video_group.setLayout(video_layout)
        layout.addWidget(video_group)
        
        # 音频设置组
        audio_group = QGroupBox("音频设置")
        audio_layout = QGridLayout()
        
        # 音频编码
        audio_layout.addWidget(QLabel("音频编码:"), 0, 0)
        self.audio_codec_combo = QComboBox()
        self.audio_codec_combo.addItems(["AAC (推荐)", "MP3", "Opus"])
        self.audio_codec_combo.setCurrentIndex(0)
        audio_layout.addWidget(self.audio_codec_combo, 0, 1)
        
        # 音频质量
        audio_layout.addWidget(QLabel("音频质量:"), 1, 0)
        self.audio_quality_combo = QComboBox()
        self.audio_quality_combo.addItems(["高 (320kbps)", "中 (192kbps)", "低 (128kbps)"])
        self.audio_quality_combo.setCurrentIndex(0)
        audio_layout.addWidget(self.audio_quality_combo, 1, 1)
        
        audio_group.setLayout(audio_layout)
        layout.addWidget(audio_group)
        
        # 导出选项组
        options_group = QGroupBox("导出选项")
        options_layout = QVBoxLayout()
        
        # 导出范围
        range_layout = QHBoxLayout()
        range_layout.addWidget(QLabel("导出范围:"))
        
        self.range_all_radio = QRadioButton("全部片段")
        self.range_all_radio.setChecked(False)
        range_layout.addWidget(self.range_all_radio)
        
        self.range_selected_radio = QRadioButton("仅选中片段")
        self.range_selected_radio.setChecked(True)
        range_layout.addWidget(self.range_selected_radio)
        
        range_layout.addStretch()
        options_layout.addLayout(range_layout)
        
        # 导出说明
        hint_label = QLabel("💡 按场景类型自动分文件夹导出（高燃动作、高光片段等）")
        hint_label.setStyleSheet("color: #a6adc8; font-size: 12px;")
        options_layout.addWidget(hint_label)
        
        # 其他选项
        self.add_subtitles_check = QCheckBox("添加字幕")
        self.add_subtitles_check.setChecked(False)  # 不默认勾选
        options_layout.addWidget(self.add_subtitles_check)
        
        self.add_bgm_check = QCheckBox("添加背景音乐")
        options_layout.addWidget(self.add_bgm_check)
        
        self.hw_accel_check = QCheckBox("使用硬件加速 (需要NVIDIA显卡)")
        self.hw_accel_check.setChecked(True)
        options_layout.addWidget(self.hw_accel_check)
        
        options_group.setLayout(options_layout)
        layout.addWidget(options_group)
        
        # 进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)
        
        # 状态标签
        self.status_label = QLabel("")
        self.status_label.setVisible(False)
        layout.addWidget(self.status_label)
        
        # 按钮
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        self.export_btn = QPushButton("开始导出")
        self.export_btn.setStyleSheet("""
            QPushButton {
                background-color: #f38ba8;
                color: #1e1e2e;
                font-weight: bold;
                padding: 8px 24px;
                border-radius: 6px;
            }
            QPushButton:hover {
                background-color: #f5a0b8;
            }
            QPushButton:disabled {
                background-color: #6c7086;
            }
        """)
        self.export_btn.clicked.connect(self.start_export)
        button_layout.addWidget(self.export_btn)
        
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(self.cancel_btn)
        
        layout.addLayout(button_layout)
        
        # 设置默认输出路径
        if self.video_path:
            p = Path(self.video_path)
            # 分段导出模式：默认输出到子目录
            default_output = str(p.parent / f"{p.stem}_导出片段")
            self.output_path_edit.setText(default_output)
            self.output_path_edit.setPlaceholderText("分段导出时，此为输出目录...")
    
    def browse_output(self):
        """浏览输出路径 - 固定选择目录"""
        default_dir = str(Path(self.video_path).parent) if self.video_path else ""
        dir_path = QFileDialog.getExistingDirectory(
            self, "选择导出目录", default_dir
        )
        if dir_path:
            self.output_path_edit.setText(dir_path)
    
    def get_export_config(self) -> dict:
        """获取导出配置"""
        # 解析分辨率
        resolution_map = {
            "原始分辨率": "original",
            "1080p (1920x1080)": "1080p",
            "720p (1280x720)": "720p",
            "480p (854x480)": "480p"
        }
        resolution = resolution_map.get(self.resolution_combo.currentText(), "original")
        
        # 解析编码格式
        codec_map = {
            "H.264 (推荐)": "h264",
            "H.265/HEVC": "h265",
            "VP9": "vp9"
        }
        codec = codec_map.get(self.codec_combo.currentText(), "h264")
        
        # 解析质量
        quality_map = {
            "高 (推荐)": "high",
            "中": "medium",
            "低": "low"
        }
        quality = quality_map.get(self.quality_combo.currentText(), "high")
        
        # 解析帧率
        fps_map = {
            "原始帧率": 0,
            "60 fps": 60,
            "30 fps": 30,
            "24 fps": 24
        }
        fps = fps_map.get(self.fps_combo.currentText(), 0)
        
        return {
            "output_path": self.output_path_edit.text(),
            "format": self.format_combo.currentText().lower(),
            "resolution": resolution,
            "codec": codec,
            "quality": quality,
            "fps": fps,
            "audio_codec": self.audio_codec_combo.currentText().split()[0].lower(),
            "audio_quality": self.audio_quality_combo.currentIndex(),
            "add_subtitles": self.add_subtitles_check.isChecked(),
            "add_bgm": self.add_bgm_check.isChecked(),
            "hw_accel": self.hw_accel_check.isChecked(),
            "export_range": "all" if self.range_all_radio.isChecked() else "selected",
            "per_segment": True,  # 固定分文件夹导出
            "video_path": self.video_path,
            "segments": self.segments
        }
    
    def start_export(self):
        """开始导出"""
        # 验证输出路径
        output_path = self.output_path_edit.text()
        if not output_path:
            QMessageBox.warning(self, "警告", "请选择输出路径")
            return
        
        # 分段导出时，确保输出目录存在
        if self.per_segment_check.isChecked():
            os.makedirs(output_path, exist_ok=True)
            # 分段导出时输出路径是目录，生成一个dummy文件名给worker
            dummy_file = os.path.join(output_path, "export.mp4")
            export_config = self.get_export_config()
            export_config["output_path"] = dummy_file
        else:
            export_config = self.get_export_config()
        
        # 创建导出工作线程
        self.export_worker = ExportWorker(export_config)
        self.export_worker.progress.connect(self.on_export_progress)
        self.export_worker.finished.connect(self.on_export_finished)
        self.export_worker.error.connect(self.on_export_error)
        
        # 更新UI
        self.export_btn.setEnabled(False)
        self.cancel_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.status_label.setVisible(True)
        self.status_label.setText("准备导出...")
        
        # 启动导出
        self.export_worker.start()
        logger.info("开始导出视频")
    
    def on_export_progress(self, progress: int, status: str):
        """导出进度更新"""
        self.progress_bar.setValue(progress)
        self.status_label.setText(status)
    
    def on_export_finished(self, success: bool, output_path: str):
        """导出完成"""
        self.export_btn.setEnabled(True)
        self.cancel_btn.setEnabled(True)
        
        if success:
            self.status_label.setText(f"导出成功: {output_path}")
            QMessageBox.information(
                self, "导出成功",
                f"视频导出成功！\n\n输出路径: {output_path}"
            )
            self.accept()
        else:
            self.status_label.setText(f"导出失败: {output_path}")
            QMessageBox.critical(self, "错误", f"导出失败\n\n{output_path}")
    
    def on_export_error(self, error_msg: str):
        """导出出错"""
        self.export_btn.setEnabled(True)
        self.cancel_btn.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.status_label.setText(f"导出失败: {error_msg}")
        QMessageBox.critical(self, "错误", f"导出失败:\n{error_msg}")
    
    def closeEvent(self, event):
        """关闭事件"""
        if self.export_worker and self.export_worker.isRunning():
            reply = QMessageBox.question(
                self, "确认",
                "导出正在进行中，确定要取消吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            
            if reply == QMessageBox.StandardButton.No:
                event.ignore()
                return
            
            self.export_worker.quit()
            self.export_worker.wait()
        
        event.accept()


# 测试代码
if __name__ == "__main__":
    import sys
    from PyQt6.QtWidgets import QApplication
    
    app = QApplication(sys.argv)
    
    dialog = ExportDialog(video_path="test.mp4")
    if dialog.exec() == QDialog.DialogCode.Accepted:
        config = dialog.get_export_config()
        print("导出配置:", config)
