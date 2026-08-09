"""
智能成片对话框
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QSpinBox, QCheckBox, QGroupBox, QRadioButton,
    QButtonGroup, QProgressBar, QTextEdit, QFileDialog, QMessageBox
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from utils.logger import logger


class SmartCutWorker(QThread):
    """智能成片工作线程"""
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(bool, str)
    
    def __init__(self, mode: str, video_path: str, output_path: str, config: dict):
        super().__init__()
        self.mode = mode
        self.video_path = video_path
        self.output_path = output_path
        self.config = config
    
    def run(self):
        try:
            from core.smart_cut import SmartClip
            import os, time, tempfile, shutil
            
            clip = SmartClip()
            
            # 适配进度回调: clip.progress(cur, total, msg) -> dialog.progress(percent, msg)
            def progress_callback(cur, total, message):
                percent = int((cur / max(total, 1)) * 100)
                self.progress.emit(percent, message)
            
            if self.mode == "hot":
                top_n = self.config.get("top_n", 10)
                video_name = os.path.basename(self.video_path).rsplit('.', 1)[0]
                output_dir = os.path.join(tempfile.gettempdir(), f"fireclip_{video_name}")
                
                result = clip.extract_hot_clips(
                    self.video_path, output_dir,
                    top_n=top_n,
                    progress_callback=progress_callback
                )
                
                if result.segments:
                    seg_paths = [s['output_path'] for s in result.segments]
                    if len(seg_paths) >= 2:
                        self.progress.emit(95, "合并片段到输出文件...")
                        clip.extractor._merge_segments_to_file(seg_paths, self.output_path)
                    elif len(seg_paths) == 1:
                        shutil.copy2(seg_paths[0], self.output_path)
                    
                    self.progress.emit(100, "高燃片段提取完成")
                    self.finished.emit(True, self.output_path)
                else:
                    self.finished.emit(False, "未识别到高燃片段")
            
            elif self.mode == "commentary":
                import json
                top_n = self.config.get("top_n", 10)
                video_name = os.path.basename(self.video_path).rsplit('.', 1)[0]
                output_dir = os.path.join(tempfile.gettempdir(), f"fireclip_commentary_{int(time.time())}")
                os.makedirs(output_dir, exist_ok=True)
                
                # Step 1: 提取高燃片段
                self.progress.emit(10, "步骤 1/4: 提取高燃片段...")
                result = clip.extract_hot_clips(
                    self.video_path, output_dir,
                    top_n=top_n,
                    progress_callback=progress_callback
                )
                
                if not result.segments:
                    self.finished.emit(False, "未找到可用的高光时刻")
                    return
                
                # Step 2: 生成解说文案
                self.progress.emit(50, "步骤 2/4: 生成解说文案...")
                from core.analysis_controller import AnalysisController
                controller = AnalysisController(preset_name="auto", enable_ai=False)
                commentary = controller.generate_commentary(self.video_path)
                
                seg_texts = []
                if commentary and commentary.get("segments"):
                    for cs in commentary["segments"][:len(result.segments)]:
                        seg_texts.append(cs.get("text", ""))
                else:
                    # 备用：为每个片段生成简单描述
                    for s in result.segments:
                        seg_texts.append(f"精彩片段 {s.get('reason', '')}")
                
                # Step 3: TTS合成配音
                self.progress.emit(65, "步骤 3/4: 合成语音配音...")
                from core.tts_engine import TTSEngine
                tts = TTSEngine()
                voice = self.config.get("voice", "male")
                speed = self.config.get("speed", 1.0)
                
                audio_paths = []
                for i, text in enumerate(seg_texts):
                    if not text.strip():
                        continue
                    audio_out = os.path.join(output_dir, f"tts_{i:03d}.mp3")
                    ok = tts.synthesize(text, audio_out, voice=voice, speed=speed)
                    if ok and os.path.exists(audio_out):
                        audio_paths.append((audio_out, text))
                
                # Step 4: 合并视频 + 配音 + 字幕
                self.progress.emit(85, "步骤 4/4: 合并视频、配音、字幕...")
                
                seg_paths = [s['output_path'] for s in result.segments]
                
                if len(seg_paths) >= 2:
                    # 先合并视频片段
                    merged_video = os.path.join(output_dir, "merged_noaudio.mp4")
                    clip.extractor._merge_segments_to_file(seg_paths, merged_video)
                elif len(seg_paths) == 1:
                    merged_video = seg_paths[0]
                
                # 如果TTS音频可用，合并音视频
                if audio_paths and os.path.exists(merged_video):
                    # 生成SRT字幕
                    from core.subtitle import SubtitleProcessor
                    sub_proc = SubtitleProcessor()
                    srt_path = os.path.join(output_dir, "commentary.srt")
                    
                    # 计算每段字幕的时间轴（简单均匀分配）
                    sub_segments = []
                    # 获取合并视频时长
                    from utils.helpers import get_video_info
                    from config import CONFIG
                    vinfo = get_video_info(merged_video, CONFIG.ffmpeg_path)
                    total_dur = vinfo.get("duration", 0)
                    
                    if total_dur > 0 and len(seg_texts) > 0:
                        seg_dur = total_dur / len(seg_texts)
                        for i, text in enumerate(seg_texts):
                            sub_segments.append({
                                "start": i * seg_dur,
                                "end": (i + 1) * seg_dur,
                                "text": text
                            })
                        sub_proc.generate_srt(sub_segments, srt_path)
                    
                    # 合并所有TTS音频为一个完整音轨
                    ffmpeg = CONFIG.ffmpeg_path or "ffmpeg"
                    import subprocess
                    
                    if len(audio_paths) == 1:
                        # 单个音频直接使用
                        merged_audio = audio_paths[0][0]
                    else:
                        # 多个音频拼接：创建concat列表
                        concat_list = os.path.join(output_dir, "audio_concat.txt")
                        with open(concat_list, 'w', encoding='utf-8') as f:
                            for audio_path, _ in audio_paths:
                                f.write(f"file '{audio_path}'\n")
                        
                        merged_audio = os.path.join(output_dir, "merged_tts.mp3")
                        concat_cmd = [
                            ffmpeg, "-y", "-f", "concat", "-safe", "0",
                            "-i", concat_list, "-c", "copy", merged_audio
                        ]
                        subprocess.run(concat_cmd, capture_output=True, timeout=120)
                    
                    # 合并视频+音频
                    if os.path.exists(merged_audio):
                        cmd = [
                            ffmpeg, "-y", "-i", merged_video, "-i", merged_audio,
                            "-c:v", "copy", "-c:a", "aac",
                            "-shortest", self.output_path
                        ]
                        subprocess.run(cmd, capture_output=True, timeout=300)
                    
                    if not os.path.exists(self.output_path):
                        # 合并失败，复制无音频版本
                        shutil.copy2(merged_video, self.output_path)
                else:
                    if os.path.exists(merged_video):
                        shutil.copy2(merged_video, self.output_path)
                    else:
                        self.finished.emit(False, "视频合并失败")
                        return
                
                self.progress.emit(100, "解说视频生成完成")
                self.finished.emit(True, self.output_path)
            else:
                self.finished.emit(False, "未知模式")
        
        except Exception as e:
            import traceback
            logger.error(f"智能成片失败: {e}\n{traceback.format_exc()}")
            self.finished.emit(False, str(e))


class SmartCutDialog(QDialog):
    """智能成片对话框"""
    
    def __init__(self, parent=None, video_path: str = ""):
        super().__init__(parent)
        self.video_path = video_path
        self.output_path = ""
        self.worker = None
        
        self.setWindowTitle("智能成片")
        self.setMinimumSize(500, 450)
        self.init_ui()
    
    def init_ui(self):
        layout = QVBoxLayout(self)
        
        # 模式选择
        mode_group = QGroupBox("成片模式")
        mode_layout = QVBoxLayout()
        
        self.mode_group = QButtonGroup()
        
        self.hot_radio = QRadioButton("高燃视频 - 自动提取精彩片段合并")
        self.hot_radio.setChecked(True)
        self.hot_radio.toggled.connect(self.on_mode_changed)
        self.mode_group.addButton(self.hot_radio, 0)
        mode_layout.addWidget(self.hot_radio)
        
        self.commentary_radio = QRadioButton("解说视频 - 自动生成文案、配音、字幕")
        self.commentary_radio.toggled.connect(self.on_mode_changed)
        self.mode_group.addButton(self.commentary_radio, 1)
        mode_layout.addWidget(self.commentary_radio)
        
        mode_group.setLayout(mode_layout)
        layout.addWidget(mode_group)
        
        # 高燃视频选项
        self.hot_options = QGroupBox("高燃视频选项")
        hot_layout = QVBoxLayout()
        
        top_n_layout = QHBoxLayout()
        top_n_layout.addWidget(QLabel("提取片段数:"))
        self.top_n_spin = QSpinBox()
        self.top_n_spin.setRange(3, 30)
        self.top_n_spin.setValue(10)
        top_n_layout.addWidget(self.top_n_spin)
        top_n_layout.addStretch()
        hot_layout.addLayout(top_n_layout)
        
        self.hot_options.setLayout(hot_layout)
        layout.addWidget(self.hot_options)
        
        # 解说视频选项
        self.commentary_options = QGroupBox("解说视频选项")
        commentary_layout = QVBoxLayout()
        
        voice_layout = QHBoxLayout()
        voice_layout.addWidget(QLabel("配音声音:"))
        self.voice_combo = QComboBox()
        self.voice_combo.addItems(["男声", "女声"])
        voice_layout.addWidget(self.voice_combo)
        voice_layout.addStretch()
        commentary_layout.addLayout(voice_layout)
        
        speed_layout = QHBoxLayout()
        speed_layout.addWidget(QLabel("语速:"))
        self.speed_combo = QComboBox()
        self.speed_combo.addItems(["0.8x 慢速", "1.0x 正常", "1.2x 快速", "1.5x 极速"])
        self.speed_combo.setCurrentIndex(1)
        speed_layout.addWidget(self.speed_combo)
        speed_layout.addStretch()
        commentary_layout.addLayout(speed_layout)
        
        self.commentary_options.setLayout(commentary_layout)
        self.commentary_options.setVisible(False)
        layout.addWidget(self.commentary_options)
        
        # 通用选项
        common_group = QGroupBox("输出选项")
        common_layout = QVBoxLayout()
        
        res_layout = QHBoxLayout()
        res_layout.addWidget(QLabel("分辨率:"))
        self.resolution_combo = QComboBox()
        self.resolution_combo.addItems(["原始分辨率", "1080p", "720p", "480p"])
        res_layout.addWidget(self.resolution_combo)
        res_layout.addStretch()
        common_layout.addLayout(res_layout)
        
        common_group.setLayout(common_layout)
        layout.addWidget(common_group)
        
        # 输出路径
        output_layout = QHBoxLayout()
        self.output_label = QLabel("输出路径: 未选择")
        output_layout.addWidget(self.output_label)
        self.browse_btn = QPushButton("浏览...")
        self.browse_btn.clicked.connect(self.browse_output)
        output_layout.addWidget(self.browse_btn)
        layout.addLayout(output_layout)
        
        # 进度
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)
        
        self.progress_label = QLabel("")
        self.progress_label.setVisible(False)
        layout.addWidget(self.progress_label)
        
        # 按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        
        self.start_btn = QPushButton("开始成片")
        self.start_btn.setMinimumHeight(40)
        self.start_btn.clicked.connect(self.start_smart_cut)
        btn_layout.addWidget(self.start_btn)
        
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self.cancel_btn)
        
        layout.addLayout(btn_layout)
    
    def on_mode_changed(self):
        is_hot = self.hot_radio.isChecked()
        self.hot_options.setVisible(is_hot)
        self.commentary_options.setVisible(not is_hot)
    
    def browse_output(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "选择输出路径", "", "MP4视频 (*.mp4)"
        )
        if path:
            self.output_path = path
            self.output_label.setText(f"输出路径: {path}")
    
    def start_smart_cut(self):
        if not self.video_path:
            QMessageBox.warning(self, "警告", "请先导入视频文件")
            return
        
        if not self.output_path:
            QMessageBox.warning(self, "警告", "请选择输出路径")
            return
        
        mode = "hot" if self.hot_radio.isChecked() else "commentary"
        
        res_map = {"原始分辨率": "original", "1080p": "1080p", "720p": "720p", "480p": "480p"}
        resolution = res_map.get(self.resolution_combo.currentText(), "original")
        
        config = {"resolution": resolution}
        
        if mode == "hot":
            config["top_n"] = self.top_n_spin.value()
        else:
            voice = "male" if self.voice_combo.currentIndex() == 0 else "female"
            speed_map = {0: 0.8, 1: 1.0, 2: 1.2, 3: 1.5}
            speed = speed_map.get(self.speed_combo.currentIndex(), 1.0)
            config["voice"] = voice
            config["speed"] = speed
        
        self.start_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_label.setVisible(True)
        self.progress_label.setText("正在初始化...")
        
        self.worker = SmartCutWorker(mode, self.video_path, self.output_path, config)
        self.worker.progress.connect(self.on_progress)
        self.worker.finished.connect(self.on_finished)
        self.worker.start()
    
    def on_progress(self, progress: int, message: str):
        self.progress_bar.setValue(progress)
        self.progress_label.setText(message)
    
    def on_finished(self, success: bool, message: str):
        self.start_btn.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.progress_label.setVisible(False)
        
        if success:
            QMessageBox.information(self, "成功", f"智能成片完成！\n\n输出路径: {message}")
            self.accept()
        else:
            QMessageBox.critical(self, "失败", f"智能成片失败:\n{message}")
