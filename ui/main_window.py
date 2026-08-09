"""
FireClip 主窗口
"""
import sys
import os
import uuid
import shutil
from pathlib import Path
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QSplitter, QToolBar, QStatusBar, QLabel, QPushButton,
    QFileDialog, QMessageBox, QProgressBar, QFrame,
    QApplication, QSizePolicy, QToolButton, QMenu
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSize, QTimer
from PyQt6.QtGui import QAction, QIcon, QKeySequence

from ui.video_preview import VideoPreviewWidget
from ui.material_panel import MaterialPanel
from ui.property_panel import PropertyPanel
from ui.timeline import TrackEditor
from ui.dialogs.smart_cut_dialog import SmartCutDialog
from ui.dialogs.smart_compose_dialog import SmartComposeDialog
from ui.dialogs.export_dialog import ExportDialog
from ui.dialogs.settings_dialog import SettingsDialog
from ui.dialogs.commentary_dialog import CommentaryDialog
from ui.theme import apply_theme, toggle_theme, apply_window_titlebar, get_theme
from core.analysis_controller import AnalysisController
from core.video_processor import VideoProcessor
from core.database import DatabaseManager
from core.resource_manager import ResourceManager
from utils.logger import logger
from utils.helpers import check_ffmpeg, detect_hardware_capabilities, get_video_info, format_time
from config import CONFIG


class ExportWorker(QThread):
    """导出工作线程"""
    progress = pyqtSignal(int, str)  # 进度百分比, 状态描述
    finished = pyqtSignal(str)  # 输出路径
    error = pyqtSignal(str)  # 错误信息
    
    def __init__(self, video_path: str, export_config: dict):
        super().__init__()
        self.video_path = video_path
        self.export_config = export_config
    
    def run(self):
        """执行导出"""
        try:
            processor = VideoProcessor()
            
            # 获取导出配置
            output_path = self.export_config.get("output_path", "")
            segments = self.export_config.get("segments", [])
            
            if not output_path:
                raise ValueError("未指定输出路径")
            
            if not segments:
                # 如果没有指定片段，导出整个视频
                self.progress.emit(10, "准备导出完整视频...")
                video_info = get_video_info(self.video_path, CONFIG.ffmpeg_path)
                duration = video_info.get("duration", 0)
                
                success = processor.extract_segment(
                    self.video_path,
                    0,
                    duration,
                    output_path
                )
                
                if success:
                    self.progress.emit(100, "导出完成")
                    self.finished.emit(output_path)
                else:
                    raise Exception("视频导出失败")
            else:
                # 导出选中的片段
                import tempfile
                tmp_dir = tempfile.mkdtemp(prefix="fireclip_export_")
                total_segments = len(segments)
                temp_files = []
                
                for i, segment in enumerate(segments):
                    progress = int(5 + (i / total_segments) * 75)
                    self.progress.emit(progress, f"提取片段 {i+1}/{total_segments}...")
                    
                    temp_path = os.path.join(tmp_dir, f"seg_{i:03d}_{uuid.uuid4().hex[:8]}.mp4")
                    temp_files.append(temp_path)
                    
                    start_time = float(segment.get("start_time", 0))
                    end_time = float(segment.get("end_time", 0))
                    duration = max(end_time - start_time, 1.0)
                    
                    success = processor.extract_segment(
                        self.video_path,
                        start_time,
                        duration,
                        temp_path
                    )
                    
                    if not success:
                        raise Exception(f"片段 {i+1} 提取失败")
                
                # 合并片段
                self.progress.emit(85, "合并片段...")
                success = processor.merge_segments(temp_files, output_path)
                
                # 清理临时目录
                try:
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                except:
                    pass
                
                if success:
                    self.progress.emit(100, "导出完成")
                    self.finished.emit(output_path)
                else:
                    raise Exception("片段合并失败")
                    
        except Exception as e:
            logger.error(f"导出失败: {e}")
            self.error.emit(str(e))


class AnalysisWorker(QThread):
    """分析工作线程"""
    progress = pyqtSignal(int, str)  # 进度百分比, 状态描述
    finished = pyqtSignal(list)  # 分析结果
    commentary_finished = pyqtSignal(dict)  # 解说文案结果
    error = pyqtSignal(str)  # 错误信息
    
    def __init__(self, video_path: str, analysis_type: str = "hot", preset: str = "auto"):
        super().__init__()
        self.video_path = video_path
        self.analysis_type = analysis_type  # hot / highlight / commentary
        self.preset = preset
    
    def run(self):
        """执行分析"""
        try:
            # 创建分析控制器
            controller = AnalysisController(preset_name=self.preset, enable_ai=CONFIG.enable_ai_vision_channel)
            
            # 定义进度回调
            def progress_callback(channel: int, progress: int, message: str):
                self.progress.emit(progress, message)
            
            # 根据视频时长动态计算片段数
            # 原则：每分钟约2个片段，最少10个，最多100个
            video_info = get_video_info(self.video_path, CONFIG.ffmpeg_path)
            duration = video_info.get("duration", 0)
            duration_min = max(duration / 60.0, 1.0)
            dynamic_top_n = max(10, min(int(duration_min * 2), 100))
            logger.info(f"视频时长 {duration:.0f}s ({duration_min:.1f}分钟) → 动态top_n={dynamic_top_n}")
            
            # 根据分析类型执行不同操作
            if self.analysis_type == "hot":
                segments = controller.extract_hot_segments(
                    self.video_path, 
                    top_n=dynamic_top_n,
                    progress_callback=progress_callback
                )
                # 转换为字典列表，开始时间向下取整、结束时间向上取整
                import math
                results = [
                    {
                        "id": str(uuid.uuid4()),
                        "start_time": int(seg.time),              # 向下取整到秒
                        "end_time": math.ceil(seg.time + seg.duration),  # 向上取整到秒
                        "duration": math.ceil(seg.time + seg.duration) - int(seg.time),
                        "scene_type": seg.scene_type,
                        "confidence": seg.score,
                        "channel_scores": seg.channel_scores,
                        "description": seg.description,
                        "tags": seg.tags,
                        "action_type": seg.action_type
                    }
                    for seg in segments
                ]
                self.finished.emit(results)
            
            elif self.analysis_type == "highlight":
                segments = controller.extract_highlight_segments(
                    self.video_path,
                    top_n=dynamic_top_n * 2,  # 高光片段取更多
                    progress_callback=progress_callback
                )
                import math
                results = [
                    {
                        "id": str(uuid.uuid4()),
                        "start_time": int(seg.time),              # 向下取整到秒
                        "end_time": math.ceil(seg.time + seg.duration),  # 向上取整到秒
                        "duration": math.ceil(seg.time + seg.duration) - int(seg.time),
                        "scene_type": seg.scene_type,
                        "confidence": seg.score,
                        "channel_scores": seg.channel_scores,
                        "description": seg.description,
                        "tags": seg.tags,
                        "action_type": seg.action_type
                    }
                    for seg in segments
                ]
                self.finished.emit(results)
            
            elif self.analysis_type == "commentary":
                commentary = controller.generate_commentary(
                    self.video_path,
                    progress_callback=progress_callback
                )
                # 解说文案走独立信号
                self.commentary_finished.emit(commentary)
        
        except Exception as e:
            logger.error(f"分析失败: {e}")
            self.error.emit(str(e))


class BatchExportWorker(QThread):
    """批量导出片段工作线程"""
    progress = pyqtSignal(int, str)    # 进度百分比, 状态描述
    finished = pyqtSignal(int, int)    # 成功数, 总数
    error = pyqtSignal(str)            # 错误信息
    
    def __init__(self, video_path: str, output_dir: str, grouped_scenes: dict):
        super().__init__()
        self.video_path = video_path
        self.output_dir = output_dir
        self.grouped_scenes = grouped_scenes  # {folder_name: [scenes]}
    
    def run(self):
        try:
            processor = VideoProcessor()
            video_stem = Path(self.video_path).stem
            
            # 计算总数
            total = sum(len(scenes) for scenes in self.grouped_scenes.values())
            done = 0
            
            for folder_name, folder_scenes in self.grouped_scenes.items():
                # 固定2个文件夹，不加序号
                sub_dir = os.path.join(self.output_dir, folder_name)
                os.makedirs(sub_dir, exist_ok=True)
                
                for i, scene in enumerate(folder_scenes):
                    start_time = scene.get("start_time", 0)
                    end_time = scene.get("end_time", 0)
                    duration = max(end_time - start_time, 1.0)
                    
                    # 简化文件名：类型_序号_开始时间-结束时间.mp4
                    filename = f"{folder_name}_{i+1:02d}_{int(start_time)}s-{int(end_time)}s.mp4"
                    output_path = os.path.join(sub_dir, filename)
                    
                    success = processor.extract_segment(self.video_path, start_time, duration, output_path)
                    if success:
                        done += 1
                    
                    progress = int(done / total * 100) if total > 0 else 0
                    self.progress.emit(progress, f"导出中: {done}/{total}...")
            
            self.finished.emit(done, total)
        
        except Exception as e:
            logger.error(f"批量导出失败: {e}")
            self.error.emit(str(e))


class MainWindow(QMainWindow):
    """主窗口"""
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("FireClip - 影视高燃剪辑")
        self.setMinimumSize(1400, 900)
        
        # 设置窗口图标（左上角标题栏）
        icon_path = Path(__file__).parent.parent / "assets" / "clip.png"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        
        # 应用统一主题
        apply_theme(self)
        
        # 当前视频路径
        self.current_video_path = None
        self.current_project_id = None
        self.analysis_worker = None
        self.export_worker = None
        
        # 初始化核心组件
        self.db_manager = DatabaseManager()
        self.resource_manager = ResourceManager()
        
        # v3.0: ResourceGovernor初始化 + 前台检测回调
        from core.resource_governor import ResourceGovernor
        self._governor = ResourceGovernor.get_instance()
        self._governor.set_foreground_check(self._is_foreground)
        
        # 初始化UI
        self.init_ui()
        self.init_toolbar()
        self.init_statusbar()
        
        # 检查环境
        self.check_environment()
        
        logger.info("FireClip 启动成功 (应用主题)")
    
    def init_ui(self):
        """初始化UI"""
        # 中心部件
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # 主布局
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # 创建分割器
        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        
        # 左侧：素材面板
        self.material_panel = MaterialPanel()
        self.material_panel.setMinimumWidth(250)
        self.main_splitter.addWidget(self.material_panel)
        
        # 中间：视频预览区
        self.preview_widget = VideoPreviewWidget()
        self.main_splitter.addWidget(self.preview_widget)
        
        # 右侧：属性面板
        self.property_panel = PropertyPanel()
        self.property_panel.setMinimumWidth(300)
        self.main_splitter.addWidget(self.property_panel)
        
        # 设置分割比例
        self.main_splitter.setSizes([250, 800, 300])
        
        # 垂直分割器（上方内容 + 下方时间轴）
        self.vertical_splitter = QSplitter(Qt.Orientation.Vertical)
        self.vertical_splitter.addWidget(self.main_splitter)
        
        # 时间轴
        self.timeline = TrackEditor()
        self.timeline.setMinimumHeight(180)
        self.vertical_splitter.addWidget(self.timeline)
        
        # 设置垂直分割比例
        self.vertical_splitter.setSizes([700, 200])
        
        main_layout.addWidget(self.vertical_splitter)
        
        # 连接信号
        self.material_panel.video_selected.connect(self.on_video_selected)
        self.material_panel.scene_selected.connect(self.on_scene_selected)
        self.material_panel.export_scenes_requested.connect(self.on_export_scenes_requested)
        self.material_panel.batch_analyze_requested.connect(self.on_batch_analyze_requested)
        self.material_panel.batch_extract_requested.connect(self.on_batch_extract_requested)
        self.timeline.position_changed.connect(self.on_timeline_position_changed)
        self.timeline.segment_clicked.connect(self.on_timeline_segment_clicked)
        self.timeline.segment_modified.connect(self.on_timeline_segment_modified)
        self.timeline.segment_split.connect(self.on_timeline_segment_split)
        self.timeline.playback_toggle.connect(self.preview_widget.toggle_play)
        self.preview_widget.position_changed.connect(self.on_preview_position_changed)
        self.preview_widget.duration_changed.connect(self.timeline.set_duration)
        
        # 属性面板信号
        self.property_panel.ai_generate_requested.connect(self.on_ai_generate_requested)
        self.property_panel.commentary_applied.connect(self.on_commentary_applied)
    
    def init_toolbar(self):
        """初始化工具栏"""
        toolbar = QToolBar()
        toolbar.setIconSize(QSize(22, 22))
        toolbar.setMovable(False)
        toolbar.setObjectName("mainToolbar")
        self.addToolBar(toolbar)
        
        # 导入按钮（带下拉菜单）
        import_btn = QToolButton()
        import_btn.setText("导入")
        import_btn.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        import_menu = QMenu(import_btn)
        import_menu.addAction("导入文件", self.import_video).setShortcut(QKeySequence("Ctrl+I"))
        import_menu.addAction("导入文件夹", self.import_folder)
        import_btn.setMenu(import_menu)
        import_btn.clicked.connect(self.import_video)
        toolbar.addWidget(import_btn)
        
        toolbar.addSeparator()
        
        # 提取高燃片段
        hot_action = QAction("提取高燃片段", self)
        hot_action.triggered.connect(lambda: self.start_analysis("hot"))
        toolbar.addAction(hot_action)
        
        # 提取高光片段
        highlight_action = QAction("提取高光片段", self)
        highlight_action.triggered.connect(lambda: self.start_analysis("highlight"))
        toolbar.addAction(highlight_action)
        
        # 剪辑解说
        commentary_action = QAction("剪辑解说", self)
        commentary_action.triggered.connect(self._open_commentary_dialog)
        toolbar.addAction(commentary_action)
        
        # 智能成片
        smart_action = QAction("智能成片", self)
        smart_action.triggered.connect(self.smart_compose)
        toolbar.addAction(smart_action)
        
        toolbar.addSeparator()
        
        # 导出
        export_action = QAction("导出", self)
        export_action.setShortcut(QKeySequence("Ctrl+E"))
        export_action.triggered.connect(self.export_video)
        toolbar.addAction(export_action)
        
        # 弹性空间 → 把设置按钮推到右侧
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)
        
        # 设置按钮（右侧）
        settings_action = QAction("设置", self)
        settings_action.triggered.connect(self.open_settings)
        toolbar.addAction(settings_action)
        
        # 主题切换按钮
        self.theme_btn = QToolButton()
        self.theme_btn.setText("浅色")
        self.theme_btn.setToolTip("切换浅色/深色主题")
        self.theme_btn.clicked.connect(self._toggle_theme)
        toolbar.addWidget(self.theme_btn)
        self._update_theme_btn_text()
    
    def init_statusbar(self):
        """初始化状态栏"""
        self.statusbar = QStatusBar()
        self.setStatusBar(self.statusbar)
        
        # 状态标签
        self.status_label = QLabel("就绪")
        self.statusbar.addWidget(self.status_label)
        
        # 进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximumWidth(200)
        self.progress_bar.setVisible(False)
        self.statusbar.addPermanentWidget(self.progress_bar)
        
        # 视频信息标签
        self.video_info_label = QLabel("")
        self.statusbar.addPermanentWidget(self.video_info_label)
        
        # v3.0: 资源状态标签
        self.resource_label = QLabel("")
        self.resource_label.setStyleSheet("color: #a6adc8; font-size: 11px; padding: 0 8px;")
        self.statusbar.addPermanentWidget(self.resource_label)
        
        # v3.0: 资源监控定时器（每3秒更新）
        self._resource_timer = QTimer(self)
        self._resource_timer.timeout.connect(self._update_resource_status)
        self._resource_timer.start(3000)
    
    def _is_foreground(self) -> bool:
        """检测窗口是否在前台"""
        return self.isActiveWindow()
    
    def _update_resource_status(self):
        """更新状态栏资源占用显示"""
        try:
            status = self._governor.get_status()
            cpu = status['cpu_smooth']
            mode = status['mode']
            ram_pct = status['ram']['percent']
            throttled = status['is_throttled']
            
            # 颜色指示
            if cpu > status['cpu_max']:
                color = "#f38ba8"  # 红色 - 超限
            elif throttled:
                color = "#f9e2af"  # 黄色 - 节流中
            else:
                color = "#a6e3a1"  # 绿色 - 正常
            
            mode_text = {"economy": "省电", "balanced": "均衡", "performance": "性能"}.get(mode, mode)
            self.resource_label.setText(f"CPU:{cpu:.0f}% | RAM:{ram_pct:.0f}% | {mode_text}")
            self.resource_label.setStyleSheet(f"color: {color}; font-size: 11px; padding: 0 8px;")
        except Exception:
            pass
    
    def check_environment(self):
        """检查运行环境"""
        # 检查FFmpeg
        if not check_ffmpeg(CONFIG.ffmpeg_path):
            QMessageBox.warning(
                self, "警告",
                "未检测到FFmpeg，部分功能可能无法正常使用。\n"
                "请确保FFmpeg已安装并添加到系统PATH。"
            )
        
        # 检测硬件能力
        hw_caps = detect_hardware_capabilities()
        if hw_caps["gpu_available"]:
            gpu_info = f"GPU: {hw_caps['gpu_name']} ({hw_caps['gpu_memory_mb']}MB)"
            logger.info(gpu_info)
            self.statusbar.showMessage(gpu_info, 5000)
        else:
            logger.warning("未检测到GPU，将使用CPU模式")
    
    def import_video(self):
        """导入视频（支持批量）"""
        file_paths, _ = QFileDialog.getOpenFileNames(
            self,
            "选择视频文件（可多选）",
            "",
            "视频文件 (*.mp4 *.mkv *.avi *.mov *.ts *.flv);;所有文件 (*)"
        )
        
        if file_paths:
            # 批量导入：第一个加载预览，其余快速添加（不阻塞UI）
            for i, file_path in enumerate(file_paths):
                if i == 0:
                    self.load_video(file_path)
                else:
                    self.material_panel.add_video(file_path, skip_info=True)
            
            # 异步加载其余视频的信息（后台线程）
            if len(file_paths) > 1:
                remaining_paths = file_paths[1:]
                self.material_panel.load_video_info_async(remaining_paths)
                self.status_label.setText(f"已导入 {len(file_paths)} 个视频（信息加载中...）")
                logger.info(f"批量导入 {len(file_paths)} 个视频")
    
    def import_folder(self):
        """导入整个文件夹的视频"""
        folder = QFileDialog.getExistingDirectory(self, "选择视频文件夹", "")
        if not folder:
            return
        
        # 支持的视频格式
        video_exts = {".mp4", ".mkv", ".avi", ".mov", ".ts", ".flv", ".wmv", ".webm"}
        file_paths = []
        for f in sorted(Path(folder).iterdir()):
            if f.is_file() and f.suffix.lower() in video_exts:
                file_paths.append(str(f))
        
        if not file_paths:
            QMessageBox.information(self, "提示", "该文件夹中没有找到视频文件")
            return
        
        # 第一个加载预览，其余快速添加
        for i, file_path in enumerate(file_paths):
            if i == 0:
                self.load_video(file_path)
            else:
                self.material_panel.add_video(file_path, skip_info=True)
        
        # 异步加载其余视频的信息
        if len(file_paths) > 1:
            remaining_paths = file_paths[1:]
            self.material_panel.load_video_info_async(remaining_paths)
        
        self.status_label.setText(f"已从文件夹导入 {len(file_paths)} 个视频")
        logger.info(f"文件夹导入: {folder} → {len(file_paths)} 个视频")
    
    def load_video(self, video_path: str):
        """加载视频"""
        try:
            self.current_video_path = video_path
            self.preview_widget.load_video(video_path)
            
            # 更新状态
            filename = Path(video_path).name
            self.status_label.setText(f"已加载: {filename}")
            
            # 添加到素材库
            self.material_panel.add_video(video_path)
            
            # 创建项目
            project_id = str(uuid.uuid4())
            video_info = self.preview_widget.video_info
            self.db_manager.create_project(
                project_id=project_id,
                name=filename,
                source_path=video_path,
                video_info=video_info
            )
            self.current_project_id = project_id
            
            # 设置轨道编辑器的视频路径
            self.timeline.set_video_path(video_path)
            
            logger.info(f"加载视频: {video_path}, 项目ID: {project_id}")
        except Exception as e:
            logger.error(f"加载视频失败: {e}")
            QMessageBox.critical(self, "错误", f"加载视频失败:\n{str(e)}")
    
    def on_video_selected(self, video_path: str):
        """视频被选中 → 加载并自动播放"""
        try:
            if video_path and os.path.exists(video_path):
                self.preview_widget.load_video(video_path)
                self.current_video_path = video_path
                self.timeline.set_video_path(video_path)
                # 自动播放
                QTimer.singleShot(300, self.preview_widget.play)
        except Exception as e:
            logger.error(f"视频选中处理异常: {e}")
    
    def on_scene_selected(self, scene_data: dict):
        """场景被选中 - 提取独立片段文件播放，音视频天然同步"""
        try:
            if scene_data:
                scene_video = scene_data.get("video_path", "")
                if not scene_video or not os.path.exists(scene_video):
                    scene_video = self.current_video_path
                
                if scene_video and os.path.exists(scene_video):
                    start_time = scene_data.get("start_time", 0)
                    end_time = scene_data.get("end_time", start_time + 10)
                    
                    # 更新当前视频路径
                    if scene_video != self.current_video_path:
                        self.current_video_path = scene_video
                    
                    # 提取片段并加载（核心改动：独立片段文件播放）
                    self.preview_widget.load_scene(scene_video, start_time, end_time)
                
                self.property_panel.update_scene_info(scene_data)
        except Exception as e:
            logger.error(f"场景选中处理异常: {e}")
    
    def on_export_scenes_requested(self, scenes: list):
        """处理素材面板的导出请求（单个或批量分段导出）"""
        if not scenes:
            return
        
        # 获取源视频路径（从片段数据中取）
        video_path = scenes[0].get("video_path", self.current_video_path)
        if not video_path or not os.path.exists(video_path):
            QMessageBox.warning(self, "警告", "找不到源视频文件")
            return
        
        if len(scenes) == 1:
            # 单个片段导出
            scene = scenes[0]
            start_time = scene.get("start_time", 0)
            end_time = scene.get("end_time", 0)
            duration = end_time - start_time
            
            # 默认文件名
            default_name = f"{Path(video_path).stem}_{format_time(start_time).replace(':', '')}-{format_time(end_time).replace(':', '')}.mp4"
            default_dir = str(Path(video_path).parent)
            
            output_path, _ = QFileDialog.getSaveFileName(
                self, "导出片段",
                os.path.join(default_dir, default_name),
                "视频文件 (*.mp4 *.mkv);;所有文件 (*)"
            )
            
            if not output_path:
                return
            
            # 直接提取
            processor = VideoProcessor()
            success = processor.extract_segment(video_path, start_time, max(duration, 1.0), output_path)
            
            if success:
                QMessageBox.information(self, "导出成功", f"片段已导出:\n{output_path}")
            else:
                QMessageBox.critical(self, "导出失败", "片段提取失败")
        else:
            # 批量分段导出 - 按场景类型分子文件夹
            output_dir = QFileDialog.getExistingDirectory(
                self, "选择导出目录",
                str(Path(video_path).parent)
            )
            
            if not output_dir:
                return
            
            # 只分2个文件夹：高燃片段 vs 高光片段
            # 按场景类型分组
            type_folder_names = {
                "action": "高燃片段", "vfx_action": "高燃片段", "vfx_spectacle": "高燃片段",
                "highlight": "高光片段", "dialog": "高光片段",
                "emotion": "高光片段", "climax": "高光片段",
                "unknown": "高光片段"
            }
            
            grouped = {}
            for scene in scenes:
                st = scene.get("scene_type", "unknown")
                folder_name = type_folder_names.get(st, "高光片段")
                grouped.setdefault(folder_name, []).append(scene)
            
            total = len(scenes)
            folder_info = "\n".join([f"  {k}: {len(v)}个片段" for k, v in grouped.items()])
            
            reply = QMessageBox.question(
                self, "确认导出",
                f"将导出 {total} 个片段，按类型分文件夹:\n\n{folder_info}\n\n导出到: {output_dir}\n确定继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            
            if reply != QMessageBox.StandardButton.Yes:
                return
            
            # 使用工作线程执行导出（不阻塞UI）
            self._batch_export_worker = BatchExportWorker(video_path, output_dir, grouped)
            self._batch_export_worker.progress.connect(self._on_batch_export_progress)
            self._batch_export_worker.finished.connect(self._on_batch_export_finished)
            self._batch_export_worker.error.connect(self._on_batch_export_error)
            self._batch_export_output_dir = output_dir
            self._batch_export_grouped = grouped
            self._batch_export_worker.start()
            
            self.status_label.setText("导出已开始...")
            logger.info(f"批量导出任务已启动: {total}个片段")
    
    def _on_batch_export_progress(self, progress: int, status: str):
        """批量导出进度更新"""
        self.status_label.setText(status)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(progress)
    
    def _on_batch_export_finished(self, success_count: int, total: int):
        """批量导出完成"""
        self.progress_bar.setVisible(False)
        output_dir = getattr(self, '_batch_export_output_dir', '')
        grouped = getattr(self, '_batch_export_grouped', {})
        
        QMessageBox.information(
            self, "导出完成",
            f"成功导出 {success_count}/{total} 个片段\n按类型分 {len(grouped)} 个文件夹\n目录: {output_dir}"
        )
        self.status_label.setText(f"导出完成: {success_count}/{total}")
        logger.info(f"批量导出完成: {success_count}/{total}，分为 {len(grouped)} 个子文件夹")
        self._batch_export_worker = None
    
    def _on_batch_export_error(self, error_msg: str):
        """批量导出失败"""
        self.progress_bar.setVisible(False)
        QMessageBox.warning(self, "导出失败", f"批量导出失败:\n{error_msg}")
        self.status_label.setText("导出失败")
        logger.error(f"批量导出失败: {error_msg}")
        self._batch_export_worker = None
    
    def on_timeline_position_changed(self, position: float):
        """时间轴位置被拖动"""
        self.preview_widget.seek(position)
        self.timeline.set_position(position)
    
    def on_timeline_segment_clicked(self, segment_data: dict):
        """轨道上的片段被点击"""
        start_time = segment_data.get("start_time", 0)
        end_time = segment_data.get("end_time", 0)
        video_path = segment_data.get("video_path", self.current_video_path)
        
        # 在视频预览中加载片段
        if video_path and end_time > start_time:
            self.preview_widget.load_scene(video_path, start_time, end_time)
        else:
            self.preview_widget.seek(start_time)
        
        self.property_panel.update_scene_info(segment_data)
    
    def on_timeline_segment_modified(self, segment_data: dict):
        """轨道上的片段被拖拽修改（裁剪/移动）"""
        seg_id = segment_data.get("id")
        st = segment_data.get("start_time", 0)
        et = segment_data.get("end_time", 0)
        logger.info(f"片段已修改: {format_time(st)}-{format_time(et)}")
        # 更新属性面板
        self.property_panel.update_scene_info(segment_data)
        # 更新素材面板中的对应场景
        if seg_id and seg_id in self.material_panel.scenes:
            self.material_panel.scenes[seg_id].update(segment_data)
    
    def on_timeline_segment_split(self, segment_data: dict, split_time: float):
        """轨道上的片段被分割"""
        import uuid
        seg_id = segment_data.get("id")
        st = segment_data.get("start_time", 0)
        et = segment_data.get("end_time", 0)
        
        # 创建右半部分新片段
        new_seg = dict(segment_data)
        new_seg["id"] = str(uuid.uuid4())
        new_seg["start_time"] = split_time
        new_seg["duration"] = et - split_time
        
        # 修改原片段为左半部分
        for seg in self.timeline.segments:
            if seg.get("id") == seg_id:
                seg["end_time"] = split_time
                seg["duration"] = split_time - st
                break
        
        # 添加新片段到轨道和素材面板
        self.timeline.segments.append(new_seg)
        self.timeline.canvas.set_segments(self.timeline.segments)
        self.material_panel.add_scenes([new_seg])
        
        logger.info(f"片段已分割 @ {format_time(split_time)}: "
                    f"左={format_time(st)}-{format_time(split_time)}, "
                    f"右={format_time(split_time)}-{format_time(et)}")
    
    def on_preview_position_changed(self, position: float):
        """预览播放位置变化 → 同步更新时间轴"""
        self.timeline.set_position(position)
    
    def start_analysis(self, analysis_type: str):
        """开始分析 —— 如有勾选视频则批量处理，否则处理当前视频"""
        # 检查原始素材中是否有勾选的视频
        checked_paths = self.material_panel.get_checked_video_paths()
        
        if checked_paths:
            # 批量模式：对勾选的视频逐一处理
            if len(checked_paths) > 1:
                reply = QMessageBox.question(
                    self, "确认批量提取",
                    f"已勾选 {len(checked_paths)} 个视频，将按顺序逐一执行分析。\n\n确定继续？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
            self._start_batch_extract(checked_paths, analysis_type)
            return
        
        # 单个视频模式
        if not self.current_video_path:
            QMessageBox.warning(self, "警告", "请先导入视频文件，或在原始素材中勾选要处理的视频")
            return
        
        # 创建分析工作线程
        self.analysis_worker = AnalysisWorker(self.current_video_path, analysis_type)
        self.analysis_worker.progress.connect(self.on_analysis_progress)
        self.analysis_worker.finished.connect(self.on_analysis_finished)
        self.analysis_worker.commentary_finished.connect(self.on_commentary_finished)
        self.analysis_worker.error.connect(self.on_analysis_error)
        
        # 显示进度条
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.status_label.setText("分析中...")
        
        # 启动分析
        self.analysis_worker.start()
        logger.info(f"开始分析: {analysis_type}")
    
    def on_batch_analyze_requested(self, video_paths: list):
        """批量分析请求 - 对多个视频逐一执行高燃片段分析"""
        if not video_paths:
            return
        self._start_batch_extract(video_paths, "hot")
    
    def on_batch_extract_requested(self, video_paths: list, analysis_type: str):
        """批量提取请求 - 从素材面板右键菜单发起"""
        if not video_paths:
            return
        self._start_batch_extract(video_paths, analysis_type)
    
    def _start_batch_extract(self, video_paths: list, analysis_type: str):
        """启动批量提取队列"""
        self._batch_queue = list(video_paths)
        self._batch_total = len(self._batch_queue)
        self._batch_done = 0
        self._batch_analysis_type = analysis_type
        
        type_names = {"hot": "高燃片段", "highlight": "高光片段", "commentary": "解说"}
        type_label = type_names.get(analysis_type, analysis_type)
        
        logger.info(f"批量提取启动: {self._batch_total} 个视频, 类型: {type_label}")
        self._process_next_batch()
    
    def _process_next_batch(self):
        """处理批量队列中的下一个视频"""
        if not self._batch_queue:
            type_names = {"hot": "高燃片段", "highlight": "高光片段", "commentary": "解说"}
            type_label = type_names.get(getattr(self, '_batch_analysis_type', 'hot'), '分析')
            self.status_label.setText(f"批量{type_label}完成: {self._batch_done}/{self._batch_total} 个视频")
            QMessageBox.information(
                self, f"批量{type_label}完成",
                f"已成功处理 {self._batch_done}/{self._batch_total} 个视频"
            )
            logger.info(f"批量分析完成: {self._batch_done}/{self._batch_total}")
            return
        
        video_path = self._batch_queue.pop(0)
        self._batch_done += 1
        filename = Path(video_path).name
        analysis_type = getattr(self, '_batch_analysis_type', 'hot')
        
        self.status_label.setText(f"[{self._batch_done}/{self._batch_total}] 正在分析: {filename}")
        logger.info(f"批量分析 [{self._batch_done}/{self._batch_total}]: {filename}")
        
        # 加载视频并启动分析
        self.current_video_path = video_path
        self.preview_widget.load_video(video_path)
        self.material_panel.add_video(video_path)
        
        # 创建分析工作线程
        self.analysis_worker = AnalysisWorker(video_path, analysis_type)
        self.analysis_worker.progress.connect(self.on_analysis_progress)
        self.analysis_worker.finished.connect(self._on_batch_analysis_finished)
        self.analysis_worker.commentary_finished.connect(self.on_commentary_finished)
        self.analysis_worker.error.connect(self._on_batch_analysis_error)
        
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.analysis_worker.start()
    
    def _on_batch_analysis_finished(self, results: list):
        """批量分析中单个视频完成"""
        # 复用原有的 on_analysis_finished 逻辑
        self.on_analysis_finished(results)
        # 继续下一个
        self._process_next_batch()
    
    def _on_batch_analysis_error(self, error_msg: str):
        """批量分析中单个视频出错"""
        logger.warning(f"批量分析某个视频失败: {error_msg}")
        self.status_label.setText(f"分析失败，继续下一个...")
        # 继续下一个
        self._process_next_batch()
    
    def on_analysis_progress(self, progress: int, status: str):
        """分析进度更新"""
        self.progress_bar.setValue(progress)
        self.status_label.setText(status)
    
    def _postprocess_segments(self, segments: list, video_duration: float = 0) -> list:
        """片段后处理：合并短片段 + 补齐最短3秒
        
        规则：
        1. 时长 < 3秒的片段，尝试与相邻片段合并（如果间隔 < 5秒）
        2. 无法合并的短片段，前后补齐到至少3秒
        3. 补齐时不超出视频时长范围
        """
        if not segments:
            return segments
        
        MIN_DURATION = 3.0   # 最短片段时长（秒）
        MERGE_GAP = 5.0      # 可合并的最大间隔（秒）
        
        # 按起始时间排序
        sorted_segs = sorted(segments, key=lambda s: s.get("start_time", 0))
        
        # 第一轮：合并相邻短片段
        merged = []
        i = 0
        while i < len(sorted_segs):
            seg = dict(sorted_segs[i])  # 复制一份
            seg_dur = seg.get("end_time", 0) - seg.get("start_time", 0)
            
            if seg_dur < MIN_DURATION and i + 1 < len(sorted_segs):
                # 短片段，尝试与下一个合并
                next_seg = sorted_segs[i + 1]
                gap = next_seg.get("start_time", 0) - seg.get("end_time", 0)
                
                if gap < MERGE_GAP:
                    # 合并：取两者的起止时间
                    seg["end_time"] = next_seg.get("end_time", seg["end_time"])
                    # 合并后取更高的置信度
                    seg["confidence"] = max(
                        seg.get("confidence", 0), 
                        next_seg.get("confidence", 0)
                    )
                    # 合并描述
                    next_dur = next_seg.get("end_time", 0) - next_seg.get("start_time", 0)
                    if next_dur < MIN_DURATION and i + 2 < len(sorted_segs):
                        # 下一个也是短片段，继续往后看
                        pass
                    i += 2
                    merged.append(seg)
                    continue
            
            merged.append(seg)
            i += 1
        
        # 第二轮：补齐到最少3秒
        result = []
        for seg in merged:
            start = seg.get("start_time", 0)
            end = seg.get("end_time", 0)
            dur = end - start
            
            if dur < MIN_DURATION:
                # 计算需要补齐的时间
                deficit = MIN_DURATION - dur
                pad_start = deficit / 2
                pad_end = deficit / 2
                
                # 前补齐
                new_start = max(0, start - pad_start)
                remaining = MIN_DURATION - (end - new_start)
                if remaining > 0:
                    pad_end = remaining
                
                # 后补齐
                new_end = end + pad_end
                if video_duration > 0 and new_end > video_duration:
                    new_end = video_duration
                    # 重新分配前面的补齐
                    new_start = max(0, new_end - MIN_DURATION)
                
                seg["start_time"] = round(new_start, 2)
                seg["end_time"] = round(new_end, 2)
                seg["duration"] = round(new_end - new_start, 2)
            else:
                seg["duration"] = round(dur, 2)
            
            result.append(seg)
        
        if len(result) < len(segments):
            logger.info(f"片段后处理: {len(segments)}个 → {len(result)}个（合并了{len(segments)-len(result)}个短片段）")
        
        return result

    def on_analysis_finished(self, results: list):
        """分析完成"""
        self.progress_bar.setVisible(False)
        self.status_label.setText("分析完成")
        
        if results:
            # 给每个结果标注来源视频路径
            for seg in results:
                seg["video_path"] = self.current_video_path
            
            # 片段后处理：合并短片段 + 补齐3秒
            video_duration = 0
            if self.current_video_path and os.path.exists(self.current_video_path):
                vinfo = get_video_info(self.current_video_path, CONFIG.ffmpeg_path)
                video_duration = vinfo.get("duration", 0)
            
            original_count = len(results)
            results = self._postprocess_segments(results, video_duration)
            
            # 清空旧数据（仅非批量模式时清空，批量模式累积添加）
            if not getattr(self, '_batch_queue', None):
                self.timeline.clear_segments()
            
            # 更新素材库
            self.material_panel.add_scenes(results)
            
            # 填充时间轴分段
            for seg in results:
                self.timeline.add_segment(seg)
            
            # 保存到数据库
            if self.current_project_id:
                self.db_manager.save_scenes(self.current_project_id, results)
                logger.info(f"场景已保存到数据库: {len(results)}个")
            
            merged_info = ""
            if len(results) < original_count:
                merged_info = f"（已合并{original_count - len(results)}个短片段）"
            logger.info(f"分析完成，发现 {len(results)} 个片段{merged_info}")
        
        # 非阻塞提示
        self.status_label.setText(f"分析完成 - 共 {len(results)} 个精彩片段")
        self.progress_bar.setValue(100)
        self.progress_bar.setVisible(False)
    
    def on_analysis_error(self, error_msg: str):
        """分析出错"""
        self.progress_bar.setVisible(False)
        self.status_label.setText("分析失败")
        QMessageBox.critical(self, "错误", f"分析失败:\n{error_msg}")
        logger.error(f"分析失败: {error_msg}")
    
    def on_commentary_finished(self, commentary: dict):
        """解说文案生成完成"""
        self.progress_bar.setVisible(False)
        
        full_text = commentary.get("full_text", "")
        segments = commentary.get("segments", [])
        
        if full_text:
            # 将文案填入解说文案编辑器
            self.property_panel.set_commentary_text(full_text)
            self.status_label.setText(f"解说文案生成完成 - {len(segments)}段")
            logger.info(f"解说文案已填入编辑器: {len(segments)}段, {len(full_text)}字")
        else:
            self.status_label.setText("解说文案生成完成（无内容）")
            QMessageBox.information(self, "提示", "未生成解说文案，可能是视频未识别到高光时刻")
    
    def on_ai_generate_requested(self):
        """属性面板 AI生成按钮 → 触发解说文案分析"""
        if not self.current_video_path:
            QMessageBox.warning(self, "警告", "请先导入视频文件")
            return
        self.start_analysis("commentary")
    
    def on_commentary_applied(self, text: str):
        """属性面板应用按钮 → 保存解说文案到数据库"""
        if not self.current_project_id:
            QMessageBox.information(self, "提示", "请先导入视频")
            return
        if text.strip():
            self.db_manager.save_commentary_text(self.current_project_id, text)
            self.status_label.setText(f"解说文案已保存 ({len(text)}字)")
            logger.info(f"解说文案已保存到项目 {self.current_project_id}: {len(text)}字")
        else:
            QMessageBox.information(self, "提示", "文案为空，请先输入内容")
    
    def smart_compose(self):
        """智能成片"""
        if not self.current_video_path:
            QMessageBox.warning(self, "警告", "请先导入视频文件")
            return
        
        checked_segments = []
        if hasattr(self, 'material_panel') and self.material_panel:
            checked_segments = self.material_panel.get_checked_scenes()
        
        dialog = SmartComposeDialog(self, self.current_video_path, checked_segments)
        if hasattr(self, 'material_panel') and self.material_panel:
            dialog.compose_completed.connect(self.material_panel.add_smart_compose_result)
        dialog.exec()
    
    def export_video(self):
        """导出视频"""
        if not self.current_video_path:
            QMessageBox.warning(self, "警告", "请先导入视频文件")
            return
        
        # 获取场景数据
        scenes = []
        if self.current_project_id:
            scenes = self.db_manager.get_scenes(self.current_project_id)
        
        # 打开导出对话框
        dialog = ExportDialog(self, self.current_video_path, scenes)
        if dialog.exec():
            config = dialog.get_export_config()
            logger.info(f"导出配置: {config}")
            
            # 启动导出工作线程
            self.export_worker = ExportWorker(self.current_video_path, config)
            self.export_worker.progress.connect(self.on_export_progress)
            self.export_worker.finished.connect(self.on_export_finished)
            self.export_worker.error.connect(self.on_export_error)
            
            # 显示进度条
            self.progress_bar.setVisible(True)
            self.progress_bar.setValue(0)
            self.status_label.setText("导出中...")
            
            # 启动导出
            self.export_worker.start()
            logger.info("开始导出视频")
    
    def on_export_progress(self, progress: int, status: str):
        """导出进度更新"""
        self.progress_bar.setValue(progress)
        self.status_label.setText(status)
    
    def on_export_finished(self, output_path: str):
        """导出完成"""
        self.progress_bar.setVisible(False)
        self.status_label.setText("导出完成")
        
        QMessageBox.information(
            self, "成功",
            f"视频导出成功！\n\n输出路径: {output_path}"
        )
        logger.info(f"导出完成: {output_path}")
    
    def on_export_error(self, error_msg: str):
        """导出出错"""
        self.progress_bar.setVisible(False)
        self.status_label.setText("导出失败")
        QMessageBox.critical(self, "错误", f"导出失败:\n{error_msg}")
        logger.error(f"导出失败: {error_msg}")
    
    def open_settings(self):
        """打开设置"""
        dialog = SettingsDialog(self)
        if dialog.exec():
            logger.info("设置已更新")
            # 重新加载配置
            CONFIG.load()
    
    def _toggle_theme(self):
        """切换深色/浅色主题"""
        new_theme = toggle_theme()
        # 应用到整个应用
        app = QApplication.instance()
        if app:
            apply_theme(app)
        apply_theme(self)
        # 更新标题栏颜色
        apply_window_titlebar(self)
        # 保存到配置
        CONFIG.theme = new_theme
        CONFIG.save()
        # 更新按钮文字
        self._update_theme_btn_text()
        logger.info(f"主题已切换为: {new_theme}")
    
    def _update_theme_btn_text(self):
        """更新主题切换按钮的文字"""
        current = get_theme()
        if current == "dark":
            self.theme_btn.setText("浅色")
            self.theme_btn.setToolTip("切换到浅色主题")
        else:
            self.theme_btn.setText("深色")
            self.theme_btn.setToolTip("切换到深色主题")

    def _open_commentary_dialog(self):
        """打开剪辑解说对话框"""
        if not self.current_video_path:
            QMessageBox.warning(self, "提示", "请先导入视频文件")
            return

        # 从素材面板获取已有片段
        scenes = []
        if hasattr(self.material_panel, 'scenes'):
            scenes = list(self.material_panel.scenes.values()) if isinstance(self.material_panel.scenes, dict) else list(self.material_panel.scenes)

        if not scenes:
            reply = QMessageBox.question(
                self, "提示",
                "当前没有提取过片段。\n\n是否先提取高燃/高光片段？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.start_analysis("highlight")
            return

        dialog = CommentaryDialog(self, self.current_video_path, scenes)
        dialog.exec()
    
    def closeEvent(self, event):
        """关闭事件"""
        # 停止分析线程
        if self.analysis_worker and self.analysis_worker.isRunning():
            self.analysis_worker.quit()
            self.analysis_worker.wait()
        
        # 停止导出线程
        if self.export_worker and self.export_worker.isRunning():
            self.export_worker.quit()
            self.export_worker.wait()
        
        event.accept()
