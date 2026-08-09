"""
视频预览组件
QMediaPlayer + QVideoWidget 原生播放，音视频天然同步，零漂移
FFmpeg 仅用于片段提取（stream copy，不重编码）
"""
import os
import subprocess
import tempfile
from pathlib import Path
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QSlider, QStyle
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QUrl, QThread
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput, QMediaDevices
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtGui import QIcon, QPainter, QColor, QPen, QPainterPath

from utils.logger import logger
from utils.helpers import format_time, get_video_info
from config import CONFIG


class ClipExtractWorker(QThread):
    """后台片段提取工作线程（可取消，不卡UI）"""
    finished = pyqtSignal(str, str, float, float)
    error = pyqtSignal(str)

    def __init__(self, video_path, start_time, duration):
        super().__init__()
        self.video_path = video_path
        self.start_time = start_time
        self.duration = duration
        self._process = None
        self._cancelled = False

    def cancel(self):
        self._cancelled = True
        if self._process:
            try:
                self._process.kill()
                self._process.wait(timeout=2)
            except Exception:
                pass

    def run(self):
        ffmpeg_path = CONFIG.ffmpeg_path
        tmp_path = None
        try:
            tmp = tempfile.NamedTemporaryFile(
                suffix='.mp4', prefix='fireclip_clip_', delete=False
            )
            tmp_path = tmp.name
            tmp.close()

            clip_duration = self.duration + 2.0

            # 先尝试 stream copy（最快，<1秒）
            cmd = [
                ffmpeg_path, "-y",
                "-ss", f"{self.start_time:.3f}",
                "-i", self.video_path,
                "-t", f"{clip_duration:.3f}",
                "-c", "copy",
                "-avoid_negative_ts", "make_zero",
                "-movflags", "+faststart",
                tmp_path
            ]
            self._process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            try:
                self._process.communicate(timeout=15)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.communicate()
                raise TimeoutError("stream copy超时")

            if self._cancelled:
                try: os.unlink(tmp_path)
                except Exception: pass
                return

            if (self._process.returncode == 0
                    and os.path.exists(tmp_path)
                    and os.path.getsize(tmp_path) > 1000):
                logger.info(f"[后台] 提取片段(stream copy): {os.path.getsize(tmp_path)/1024:.0f}KB")
                self.finished.emit(tmp_path, self.video_path, self.start_time, self.duration)
                return

            # stream copy 失败 → 重编码
            if self._cancelled:
                return

            cmd2 = [
                ffmpeg_path, "-y",
                "-ss", f"{self.start_time:.3f}",
                "-i", self.video_path,
                "-t", f"{clip_duration:.3f}",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart",
                tmp_path
            ]
            self._process = subprocess.Popen(
                cmd2, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            try:
                self._process.communicate(timeout=30)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.communicate()
                raise TimeoutError("重编码超时")

            if self._cancelled:
                try: os.unlink(tmp_path)
                except Exception: pass
                return

            if (self._process.returncode == 0
                    and os.path.exists(tmp_path)
                    and os.path.getsize(tmp_path) > 1000):
                logger.info(f"[后台] 提取片段(重编码): {os.path.getsize(tmp_path)/1024:.0f}KB")
                self.finished.emit(tmp_path, self.video_path, self.start_time, self.duration)
                return

            try: os.unlink(tmp_path)
            except Exception: pass
            if not self._cancelled:
                self.error.emit("片段提取失败")

        except Exception as e:
            if tmp_path:
                try: os.unlink(tmp_path)
                except Exception: pass
            if not self._cancelled:
                self.error.emit(str(e))


class VideoPreviewWidget(QWidget):
    """视频预览组件 - QMediaPlayer + QVideoWidget 原生播放

    原理：QMediaPlayer 同时处理音频和视频渲染，
    不存在双引擎漂移问题，音视频天然同步。
    FFmpeg 仅用于提取片段文件（stream copy，<1秒），不参与播放。
    """

    position_changed = pyqtSignal(float)
    duration_changed = pyqtSignal(float)

    def __init__(self):
        super().__init__()
        self.video_path = None
        self.video_info = {}

        # 播放状态
        self.is_playing = False
        self._current_duration_ms = 0  # 当前媒体时长(ms)

        # UI 状态
        self._updating_slider = False
        self._user_dragging = False

        # 片段预览模式
        self._clip_path = None         # 当前提取的临时片段MP4文件
        self._clip_source_path = None  # 片段对应的原始视频路径
        self._clip_start_time = 0.0    # 片段在原视频中的起始时间
        self._scene_duration = 0.0     # 当前场景时长

        # 异步工作线程
        self._clip_worker = None

        # 播放器（QMediaPlayer 同时处理音频+视频渲染，天然同步）
        self.audio_player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.audio_player.setAudioOutput(self.audio_output)
        self.audio_output.setVolume(0.7)
        
        # v3.0: 监听系统音频设备切换，自动跟随默认输出设备
        self._audio_devices = QMediaDevices()
        self._audio_devices.audioOutputsChanged.connect(self._on_audio_device_changed)
        self._last_audio_device_id = None  # 跟踪上一次的默认设备ID，避免重复重建
        
        # 记录初始默认音频设备
        try:
            initial_default = QMediaDevices.defaultAudioOutput()
            if initial_default:
                self._last_audio_device_id = initial_default.id()
        except Exception:
            pass

        self.init_ui()

        # 关键：将播放器连接到视频渲染组件
        self.audio_player.setVideoOutput(self.video_widget)

        # 连接播放器信号
        self.audio_player.playbackStateChanged.connect(self._on_playback_state_changed)
        self.audio_player.positionChanged.connect(self._on_position_changed)
        self.audio_player.durationChanged.connect(self._on_duration_changed)
        self.audio_player.errorOccurred.connect(self._on_media_error)

        logger.info("视频预览组件初始化完成（QMediaPlayer + QVideoWidget 原生播放）")

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # QVideoWidget: Qt 原生视频渲染，自动缩放、保持宽高比
        self.video_widget = QVideoWidget()
        self.video_widget.setMinimumHeight(400)
        layout.addWidget(self.video_widget, 1)

        # 控制区域
        control_widget = QWidget()
        control_widget.setObjectName("videoControlBar")
        control_widget.setMinimumHeight(90)
        control_layout = QVBoxLayout(control_widget)
        control_layout.setContentsMargins(16, 8, 16, 12)
        control_layout.setSpacing(8)

        # 进度条
        self.progress_slider = QSlider(Qt.Orientation.Horizontal)
        self.progress_slider.setMinimumHeight(22)
        self.progress_slider.setObjectName("progressSlider")
        self.progress_slider.sliderPressed.connect(self._on_slider_pressed)
        self.progress_slider.sliderReleased.connect(self._on_slider_released)
        control_layout.addWidget(self.progress_slider)

        # 按钮行
        button_layout = QHBoxLayout()
        button_layout.setSpacing(12)

        # 播放按钮（带图标风格）
        self.play_btn = QPushButton("▶ 播放")
        self.play_btn.setObjectName("playButton")
        self.play_btn.setFixedSize(100, 38)
        self.play_btn.clicked.connect(self.toggle_play)
        button_layout.addWidget(self.play_btn)

        # 停止按钮
        self.stop_btn = QPushButton("■ 停止")
        self.stop_btn.setObjectName("stopButton")
        self.stop_btn.setFixedSize(100, 38)
        self.stop_btn.clicked.connect(self.stop)
        button_layout.addWidget(self.stop_btn)

        # 时间标签
        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setObjectName("timeLabel")
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.time_label.setMinimumWidth(140)
        button_layout.addWidget(self.time_label)

        button_layout.addStretch()

        # 音量图标（线性喇叭，仿Windows风格）
        self.volume_icon = VolumeIconLabel()
        self.volume_icon.setFixedSize(24, 24)
        self.volume_icon.setCursor(Qt.CursorShape.PointingHandCursor)
        self.volume_icon.clicked.connect(self.toggle_mute)
        button_layout.addWidget(self.volume_icon)

        # 音量滑块
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setObjectName("volumeSlider")
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(70)
        self.volume_slider.setFixedWidth(110)
        self.volume_slider.setMinimumHeight(22)
        self.volume_slider.valueChanged.connect(self._on_volume_changed)
        button_layout.addWidget(self.volume_slider)

        control_layout.addLayout(button_layout)
        layout.addWidget(control_widget)

    # ─── 临时文件管理 ───────────────────────────────────────────

    def _cleanup_temp_files(self):
        """清理所有临时文件"""
        if self._clip_path and os.path.exists(self._clip_path):
            try:
                os.unlink(self._clip_path)
            except Exception:
                pass
        self._clip_path = None

    def _cancel_workers(self):
        """取消正在运行的工作线程"""
        if self._clip_worker:
            self._clip_worker.cancel()
            try:
                self._clip_worker.finished.disconnect()
                self._clip_worker.error.disconnect()
            except TypeError:
                pass
            if self._clip_worker.isRunning():
                self._clip_worker.wait(2000)
            self._clip_worker = None

    # ─── 加载视频/片段 ──────────────────────────────────────────

    def load_video(self, video_path: str):
        """加载完整视频（原始素材预览）"""
        try:
            if not os.path.exists(video_path):
                raise FileNotFoundError(f"视频文件不存在: {video_path}")

            self.stop()
            self._cleanup_temp_files()
            self._cancel_workers()

            self.video_path = video_path
            self.video_info = get_video_info(video_path, CONFIG.ffmpeg_path)
            self._clip_start_time = 0.0
            self._scene_duration = 0.0
            self._clip_path = None
            self._clip_source_path = None

            self.duration = self.video_info.get("duration", 0.0)

            # 设置媒体源 → QMediaPlayer 自动处理音视频
            self.audio_player.setSource(QUrl.fromLocalFile(os.path.abspath(video_path)))

            logger.info(f"加载视频: {video_path}, 时长: {self.duration:.1f}s")

        except Exception as e:
            logger.error(f"加载视频失败: {e}")
            raise

    def load_scene(self, video_path: str, start_time: float, end_time: float):
        """加载场景片段（异步提取MP4 → QMediaPlayer原生播放）

        流程：
        1. 后台用 FFmpeg stream copy 提取片段 MP4（<1秒）
        2. 将片段 MP4 设为 QMediaPlayer 媒体源
        3. QMediaPlayer 同时播放音频+视频 → 天然同步
        """
        try:
            # 同一场景已加载 → 直接重头播放，不重复提取
            if (self._clip_source_path == video_path
                    and abs(self._clip_start_time - start_time) < 0.5
                    and self._clip_path and os.path.exists(self._clip_path)):
                self.stop()
                self.audio_player.setPosition(0)
                self.update_time_display()
                return

            self.stop()
            self._cleanup_temp_files()
            self._cancel_workers()

            self._clip_source_path = video_path
            self._clip_start_time = start_time
            self._scene_duration = end_time - start_time

            # 更新视频信息
            if self.video_path != video_path:
                self.video_path = video_path
                self.video_info = get_video_info(video_path, CONFIG.ffmpeg_path)
                self.duration = self.video_info.get("duration", 0.0)

            self.time_label.setText("加载中...")

            # 后台提取片段（不阻塞UI）
            self._clip_worker = ClipExtractWorker(
                video_path, start_time, self._scene_duration
            )
            self._clip_worker.finished.connect(self._on_clip_extracted)
            self._clip_worker.error.connect(self._on_clip_error)
            self._clip_worker.start()

            logger.info(f"后台提取片段: {start_time:.1f}s-{end_time:.1f}s")

        except Exception as e:
            logger.error(f"加载场景失败: {e}")

    def _on_clip_extracted(self, clip_path, video_path, start_time, duration):
        """片段提取完成回调 → 设置为媒体源"""
        # 安全检查：确保回调对应当前请求的场景
        if (self._clip_source_path != video_path
                or abs(self._clip_start_time - start_time) > 0.5):
            try:
                os.unlink(clip_path)
            except Exception:
                pass
            return

        self._clip_path = clip_path

        # 设置片段为媒体源 → QMediaPlayer 同时播放音视频
        self.audio_player.setSource(QUrl.fromLocalFile(os.path.abspath(clip_path)))
        self.audio_player.setPosition(0)

        self.update_time_display()

        # 自动播放片段
        QTimer.singleShot(200, self.play)

        logger.info(f"片段加载完成: {clip_path}")

    def _on_clip_error(self, error_msg):
        """片段提取失败 → 回退到原始视频"""
        logger.warning(f"片段提取失败: {error_msg}，回退到原始视频")
        if self._clip_source_path and os.path.exists(self._clip_source_path):
            self.load_video(self._clip_source_path)
            self.seek(self._clip_start_time)

    # ─── 播放控制 ──────────────────────────────────────────────

    def toggle_play(self):
        if self.is_playing:
            self.pause()
        else:
            self.play()

    def play(self):
        if not self.video_path:
            return
        self.is_playing = True
        self.play_btn.setText("❚❚ 暂停")
        self.audio_player.play()

    def pause(self):
        self.is_playing = False
        self.play_btn.setText("▶ 播放")
        self.audio_player.pause()

    def stop(self):
        self.is_playing = False
        self.play_btn.setText("▶ 播放")
        self.audio_player.stop()
        self.audio_player.setPosition(0)
        self._updating_slider = True
        self.progress_slider.setValue(0)
        self._updating_slider = False
        self.update_time_display()

    def seek(self, position_sec: float):
        """跳转到指定时间位置（秒）

        片段模式：position_sec 是原视频中的绝对时间，
        自动转换为片段内相对时间。
        """
        if self._clip_path:
            # 片段模式：转换为片段内时间
            clip_pos = max(0, position_sec - self._clip_start_time)
            pos_ms = int(clip_pos * 1000)
            if self._current_duration_ms > 0:
                pos_ms = min(pos_ms, self._current_duration_ms)
            self.audio_player.setPosition(pos_ms)
        else:
            # 原始视频模式
            pos_ms = int(position_sec * 1000)
            self.audio_player.setPosition(pos_ms)

    # ─── 播放器信号处理 ─────────────────────────────────────────

    def _on_playback_state_changed(self, state):
        """播放状态变化"""
        if state != QMediaPlayer.PlaybackState.PlayingState and self.is_playing:
            self.is_playing = False
            self.play_btn.setText("▶ 播放")

    def _on_media_error(self, error, error_string):
        """媒体播放错误"""
        logger.warning(f"播放错误: {error_string} (error={error})")
        if error != QMediaPlayer.Error.NoError:
            self.time_label.setText(f"错误: {error_string}")

    def _on_position_changed(self, position_ms):
        """播放位置变化 → 更新进度条和时间显示"""
        if self._user_dragging:
            return
        self._updating_slider = True
        if self._current_duration_ms > 0:
            self.progress_slider.setValue(position_ms)
        self._updating_slider = False
        self.update_time_display()

        # 发送绝对时间位置信号（供时间轴同步）
        pos_sec = position_ms / 1000.0
        if self._clip_path:
            self.position_changed.emit(pos_sec + self._clip_start_time)
        else:
            self.position_changed.emit(pos_sec)

    def _on_duration_changed(self, duration_ms):
        """媒体时长变化（源加载完成时触发）"""
        self._current_duration_ms = max(duration_ms, 1)
        self.progress_slider.setRange(0, self._current_duration_ms)
        # 仅在完整视频模式下通知外部（片段模式不改变 timeline 时长）
        if not self._clip_path:
            self.duration_changed.emit(duration_ms / 1000.0)
        logger.debug(f"媒体时长: {duration_ms/1000:.1f}s (clip={bool(self._clip_path)})")

    # ─── 进度条拖动 ────────────────────────────────────────────

    def _on_slider_pressed(self):
        self._user_dragging = True

    def _on_slider_released(self):
        self._user_dragging = False
        position_ms = self.progress_slider.value()
        self.audio_player.setPosition(position_ms)
        if self.is_playing:
            self.audio_player.play()

    # ─── 辅助方法 ──────────────────────────────────────────────

    def set_volume(self, value: int):
        self.audio_output.setVolume(value / 100.0)
        self.volume_icon.set_volume_level(value)

    def _on_volume_changed(self, value: int):
        """音量滑块变化 → 设置音量+更新图标"""
        self.set_volume(value)

    def toggle_mute(self):
        """点击音量图标切换静音"""
        if self.volume_slider.value() > 0:
            self._saved_volume = self.volume_slider.value()
            self.volume_slider.setValue(0)
        else:
            self.volume_slider.setValue(getattr(self, '_saved_volume', 70))

    def update_time_display(self):
        pos_ms = self.audio_player.position()
        dur_ms = self._current_duration_ms
        current_sec = max(pos_ms / 1000.0, 0)
        dur_sec = max(dur_ms / 1000.0, 0)

        if self._clip_path:
            # 片段模式：显示原视频中的绝对时间
            display_time = current_sec + self._clip_start_time
            self.time_label.setText(
                f"{format_time(display_time)} / {format_time(self._scene_duration)}"
            )
        else:
            self.time_label.setText(f"{format_time(current_sec)} / {format_time(dur_sec)}")

    def get_current_position(self) -> float:
        return self.audio_player.position() / 1000.0

    def get_duration(self) -> float:
        return self._current_duration_ms / 1000.0

    def closeEvent(self, event):
        self.stop()
        self._cancel_workers()
        self._cleanup_temp_files()
        super().closeEvent(event)
    
    # ─── 音频设备自动跟随（v3.0） ─────────────────────────────────
    
    def _on_audio_device_changed(self):
        """系统音频输出设备变化时自动切换到新的默认设备
        
        问题：QAudioOutput在创建时绑定当前默认设备，
        当用户从耳机切换到扬声器/蓝牙时，音频仍从旧设备输出。
        
        方案：监听QMediaDevices.audioOutputsChanged信号，
        检测默认设备是否变化，如果变了就重建QAudioOutput。
        """
        try:
            new_default = QMediaDevices.defaultAudioOutput()
            new_id = new_default.id() if new_default else ""
            
            # 设备没变（可能是其他设备的热插拔），忽略
            if new_id == self._last_audio_device_id:
                return
            
            old_name = "(未知)"
            if self._last_audio_device_id is not None:
                # 查找旧设备名称（可能在列表中已不存在）
                for dev in QMediaDevices.audioOutputs():
                    if dev.id() == self._last_audio_device_id:
                        old_name = dev.description()
                        break
            
            new_name = new_default.description() if new_default else "(未知)"
            logger.info(f"音频设备切换: {old_name} → {new_name}")
            
            # 保存当前播放状态
            was_playing = self.is_playing
            current_pos = self.audio_player.position()
            current_source = self.audio_player.source()
            current_volume = self.audio_output.volume()
            
            # 重建 QAudioOutput 绑定到新默认设备
            self.audio_player.setAudioOutput(None)  # 先断开
            self.audio_output = QAudioOutput(new_default)  # 绑定新设备
            self.audio_output.setVolume(current_volume)
            self.audio_player.setAudioOutput(self.audio_output)  # 重新连接
            
            # 恢复播放状态
            if not current_source.isEmpty():
                self.audio_player.setSource(current_source)
                self.audio_player.setPosition(current_pos)
                if was_playing:
                    # 短暂延迟让设备就绪后再播放
                    QTimer.singleShot(100, self.audio_player.play)
            
            self._last_audio_device_id = new_id
            logger.info(f"音频设备已切换到: {new_name}")
            
        except Exception as e:
            logger.warning(f"音频设备切换处理异常: {e}")


class VolumeIconLabel(QLabel):
    """自绘线性喇叭音量图标（仿 Windows 风格）"""
    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._volume = 70
        self.setMinimumSize(24, 24)

    def set_volume_level(self, level: int):
        """level: 0-100"""
        self._volume = max(0, min(100, level))
        self.update()

    def mousePressEvent(self, event):
        self.clicked.emit()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy = w // 2, h // 2
        color = QColor("#a6adc8") if self._volume > 0 else QColor("#6c7086")
        pen = QPen(color, 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)

        # 喇叭主体：小梯形 + 大口
        lx = cx - 6
        p.drawLine(lx, cy - 3, lx, cy + 3)            # 左侧竖线
        p.drawLine(lx, cy - 3, lx + 4, cy - 6)        # 上斜线
        p.drawLine(lx, cy + 3, lx + 4, cy + 6)        # 下斜线
        p.drawLine(lx + 4, cy - 6, lx + 4, cy + 6)    # 右侧竖线

        # 声波线
        wave_base_x = lx + 7
        if self._volume > 0:
            p.drawArc(wave_base_x - 3, cy - 5, 6, 10, 270 * 16, 180 * 16)
        if self._volume > 40:
            p.drawArc(wave_base_x - 1, cy - 8, 8, 16, 270 * 16, 180 * 16)
        if self._volume > 70:
            p.drawArc(wave_base_x + 1, cy - 11, 10, 22, 270 * 16, 180 * 16)

        # 静音时画 X
        if self._volume == 0:
            mx = wave_base_x + 2
            p.drawLine(mx, cy - 4, mx + 6, cy + 4)
            p.drawLine(mx, cy + 4, mx + 6, cy - 4)

        p.end()
