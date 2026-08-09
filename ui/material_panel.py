"""
素材面板组件
"""
import os
import math
from pathlib import Path
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTreeWidget, QTreeWidgetItem,
    QPushButton, QLabel, QMenu, QMessageBox, QFileDialog,
    QAbstractItemView, QFrame, QCheckBox
)
from PyQt6.QtCore import Qt, pyqtSignal, QThread
from PyQt6.QtGui import QIcon, QFont

from utils.logger import logger
from utils.helpers import format_time, format_time_short, format_file_size, get_video_info
from config import CONFIG


class VideoInfoWorker(QThread):
    """后台加载视频信息的工作线程"""
    info_ready = pyqtSignal(str, float)  # video_path, duration
    all_done = pyqtSignal()
    
    def __init__(self, video_paths: list):
        super().__init__()
        self.video_paths = video_paths
    
    def run(self):
        for path in self.video_paths:
            try:
                info = get_video_info(path, CONFIG.ffmpeg_path)
                duration = info.get("duration", 0)
                self.info_ready.emit(path, duration)
            except Exception as e:
                logger.error(f"获取视频信息失败: {Path(path).name} - {e}")
                self.info_ready.emit(path, 0)
        self.all_done.emit()


class MaterialPanel(QWidget):
    """素材面板"""
    
    video_selected = pyqtSignal(str)  # 视频被选中
    scene_selected = pyqtSignal(dict)  # 场景被选中
    export_scenes_requested = pyqtSignal(list)  # 批量导出请求
    batch_analyze_requested = pyqtSignal(list)  # 批量分析请求（视频路径列表）
    batch_extract_requested = pyqtSignal(list, str)  # 批量提取请求（视频路径列表, 分析类型）
    
    def __init__(self):
        super().__init__()
        self.videos = {}  # video_id -> video_path
        self.scenes = {}  # scene_id -> scene_data
        self._info_worker = None  # 异步视频信息加载工作线程
        
        self.init_ui()
        logger.info("素材面板初始化完成")
    
    def init_ui(self):
        """初始化UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        
        # 标题
        title_label = QLabel("素材库")
        title_label.setStyleSheet(
            "font-size: 13px; font-weight: bold; color: #7aa2f7; "
            "padding: 2px 0px; background: transparent;"
        )
        layout.addWidget(title_label)
        
        # 素材树
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.show_context_menu)
        self.tree.itemClicked.connect(self.on_item_clicked)
        self.tree.itemDoubleClicked.connect(self.on_item_double_clicked)
        
        # 复选框样式：简单对勾风格，SVG图标
        _assets = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'assets').replace('\\', '/')
        self.tree.setStyleSheet(f"""
            QTreeWidget::indicator {{
                width: 13px;
                height: 13px;
            }}
            QTreeWidget::indicator:unchecked {{
                image: url({_assets}/checkbox_unchecked.svg);
            }}
            QTreeWidget::indicator:checked {{
                image: url({_assets}/checkbox_checked.svg);
            }}
            QTreeWidget::indicator:indeterminate {{
                image: url({_assets}/checkbox_partial.svg);
            }}
        """)
        # 禁用双击展开/折叠（只用箭头控制）
        self.tree.setExpandsOnDoubleClick(False)
        
        # 创建根节点（分组节点带三态复选框）
        self.original_root = QTreeWidgetItem(self.tree, ["原始素材"])
        self.original_root.setFlags(
            self.original_root.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsAutoTristate
        )
        self.original_root.setCheckState(0, Qt.CheckState.Unchecked)
        self.original_root.setExpanded(False)  # 默认折叠，点箭头展开
        # 设置分组字体稍大
        font = self.original_root.font(0)
        font.setBold(True)
        self.original_root.setFont(0, font)
        
        self.hot_root = self._create_group_root("高燃片段 (0)", expanded=False)
        self.highlight_root = self._create_group_root("高光片段 (0)", expanded=False)
        self.smart_compose_root = self._create_group_root("🎬 智能成片 (0)", expanded=True)
        self.commentary_root = self._create_group_root("解说成品 (0)", expanded=False)
        
        # 所有根节点创建完毕后再连接复选框变化信号
        self.tree.itemChanged.connect(self.on_item_check_changed)
        
        layout.addWidget(self.tree)
        
        # 批量操作栏
        batch_frame = QFrame()
        batch_frame.setStyleSheet(
            "QFrame { background-color: #16161f; border: 1px solid #2a2a3a; border-radius: 5px; }"
        )
        batch_frame.setFixedHeight(36)
        batch_layout = QHBoxLayout(batch_frame)
        batch_layout.setContentsMargins(10, 0, 10, 0)
        batch_layout.setSpacing(8)
        
        self.selection_label = QLabel("【0个文件】")
        self.selection_label.setStyleSheet("color: #5c6078; font-size: 12px; background: transparent; border: none;")
        batch_layout.addWidget(self.selection_label)
        
        batch_layout.addStretch()
        
        btn_style = (
            "QPushButton { border: none; border-radius: 4px; font-size: 11px; "
            "font-weight: bold; padding: 4px 10px; min-width: 44px; min-height: 16px; }"
        )
        
        self.batch_export_btn = QPushButton("导出")
        self.batch_export_btn.setFixedSize(50, 24)
        self.batch_export_btn.setStyleSheet(
            btn_style + "QPushButton { background: #9ece6a; color: #0f0f17; } "
            "QPushButton:hover { background: #b4e080; } "
            "QPushButton:disabled { background: #2a2a3a; color: #5c6078; }"
        )
        self.batch_export_btn.setEnabled(False)
        self.batch_export_btn.clicked.connect(self.export_checked_scenes)
        batch_layout.addWidget(self.batch_export_btn)
        
        self.batch_delete_btn = QPushButton("删除")
        self.batch_delete_btn.setFixedSize(50, 24)
        self.batch_delete_btn.setStyleSheet(
            btn_style + "QPushButton { background: #f7768e; color: #0f0f17; } "
            "QPushButton:hover { background: #ff8da3; } "
            "QPushButton:disabled { background: #2a2a3a; color: #5c6078; }"
        )
        self.batch_delete_btn.setEnabled(False)
        self.batch_delete_btn.clicked.connect(self.delete_checked_items)
        batch_layout.addWidget(self.batch_delete_btn)
        
        layout.addWidget(batch_frame)
        
        # 底部按钮
        button_layout = QHBoxLayout()
        
        self.add_btn = QPushButton("添加")
        self.add_btn.setToolTip("支持多选批量导入")
        self.add_btn.clicked.connect(self.add_video_dialog)
        button_layout.addWidget(self.add_btn)
        
        self.batch_analyze_btn = QPushButton("批量分析")
        self.batch_analyze_btn.setToolTip("对素材库中所有视频逐一执行高燃片段分析")
        self.batch_analyze_btn.clicked.connect(self.batch_analyze_all)
        button_layout.addWidget(self.batch_analyze_btn)
        
        self.clear_btn = QPushButton("清空")
        self.clear_btn.clicked.connect(self.clear_all_confirm)
        button_layout.addWidget(self.clear_btn)
        
        layout.addLayout(button_layout)
        
        # 初始化计数显示
        self._update_check_info()
    
    def _create_group_root(self, text: str, expanded: bool) -> QTreeWidgetItem:
        """创建带三态复选框的分组根节点"""
        item = QTreeWidgetItem(self.tree, [text])
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsAutoTristate)
        item.setCheckState(0, Qt.CheckState.Unchecked)
        item.setExpanded(expanded)
        # 设置分组字体稍大
        font = item.font(0)
        font.setBold(True)
        item.setFont(0, font)
        return item
    
    def add_video(self, video_path: str, skip_info: bool = False):
        """添加视频到素材库
        
        Args:
            video_path: 视频文件路径
            skip_info: 跳过 ffprobe 信息获取（批量导入时用，避免阻塞UI）
        """
        try:
            if not os.path.exists(video_path):
                return
            
            video_id = str(hash(video_path))
            if video_id in self.videos:
                return  # 已存在
            
            filename = Path(video_path).name
            
            if skip_info:
                # 批量模式：先快速添加到树，不执行 ffprobe
                item_text = f"{filename} (加载中...)"
                item = QTreeWidgetItem(self.original_root, [item_text])
                item.setFlags(
                    item.flags() | Qt.ItemFlag.ItemIsUserCheckable
                )
                item.setCheckState(0, Qt.CheckState.Unchecked)
                item.setData(0, Qt.ItemDataRole.UserRole, {
                    "type": "video", "path": video_path, "id": video_id, "pending_info": True
                })
                self.videos[video_id] = video_path
                logger.info(f"快速添加视频: {filename}")
            else:
                # 单个模式：获取完整信息
                info = get_video_info(video_path, CONFIG.ffmpeg_path)
                duration = info.get("duration", 0)
                
                item_text = f"{filename} ({format_time(duration)})"
                item = QTreeWidgetItem(self.original_root, [item_text])
                item.setFlags(
                    item.flags() | Qt.ItemFlag.ItemIsUserCheckable
                )
                item.setCheckState(0, Qt.CheckState.Unchecked)
                item.setData(0, Qt.ItemDataRole.UserRole, {
                    "type": "video", "path": video_path, "id": video_id
                })
                self.videos[video_id] = video_path
                logger.info(f"添加视频: {filename}")
            
            # 更新原始素材计数
            self.update_root_count(self.original_root)
        except Exception as e:
            logger.error(f"添加视频失败: {e}")
    
    def load_video_info_async(self, video_paths: list):
        """异步加载视频信息（后台线程，不阻塞UI）"""
        if not video_paths:
            return
        self._info_worker = VideoInfoWorker(video_paths)
        self._info_worker.info_ready.connect(self._on_video_info_ready)
        self._info_worker.all_done.connect(self._on_all_info_done)
        self._info_worker.start()
        logger.info(f"启动异步视频信息加载: {len(video_paths)}个视频")
    
    def _on_video_info_ready(self, video_path: str, duration: float):
        """单个视频信息加载完成"""
        # 找到对应的树节点并更新文本
        for i in range(self.original_root.childCount()):
            child = self.original_root.child(i)
            data = child.data(0, Qt.ItemDataRole.UserRole)
            if data and data.get("path") == video_path and data.get("pending_info"):
                filename = Path(video_path).name
                child.setText(0, f"{filename} ({format_time(duration)})")
                data["pending_info"] = False
                break
    
    def _on_all_info_done(self):
        """所有视频信息加载完成"""
        logger.info("批量视频信息加载完成")
        self._info_worker = None
    
    def add_video_dialog(self):
        """通过对话框批量添加视频"""
        file_paths, _ = QFileDialog.getOpenFileNames(
            self,
            "选择视频文件（可多选）",
            "",
            "视频文件 (*.mp4 *.mkv *.avi *.mov *.ts *.flv);;所有文件 (*)"
        )
        
        if file_paths:
            for file_path in file_paths:
                self.add_video(file_path)
            if len(file_paths) > 1:
                logger.info(f"批量添加 {len(file_paths)} 个视频")
    
    def add_scenes(self, scenes: list):
        """添加场景/片段"""
        for scene in scenes:
            scene_id = scene.get("id", str(hash(str(scene))))
            self.scenes[scene_id] = scene
            
            scene_type = scene.get("scene_type", "unknown")
            start_time = scene.get("start_time", 0)
            end_time = scene.get("end_time", 0)
            duration = end_time - start_time
            confidence = scene.get("confidence", 0)
            
            # 生成有意义的显示文本
            video_path = scene.get("video_path", "")
            video_name = Path(video_path).stem if video_path else ""
            
            # 用场景类型+时间生成描述
            type_names = {
                "action": "动作", "vfx_action": "特效动作",
                "highlight": "高光", "dialog": "对话",
                "emotion": "情感", "climax": "高潮",
                "unknown": "精彩"
            }
            type_label = type_names.get(scene_type, "精彩")
            
            # 显示文本：类型 + 时长 + 视频名（如：特效动作 00:30 [第十集]）
            duration_str = format_time_short(duration)  # 如 00:30
            if video_name:
                item_text = f"{type_label} {duration_str} [{video_name}]"
            else:
                item_text = f"{type_label} {duration_str}"
            
            # 根据类型添加到不同分组
            if scene_type in ["action", "vfx_action"]:
                root = self.hot_root
                icon = ""
            elif scene_type in ["highlight", "dialog", "emotion", "climax"]:
                root = self.highlight_root
                icon = ""
            else:
                root = self.commentary_root
                icon = ""
            
            # 创建树节点（带复选框）
            item = QTreeWidgetItem(root, [f"{icon}{item_text}"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Unchecked)
            item.setData(0, Qt.ItemDataRole.UserRole, {"type": "scene", "data": scene, "id": scene_id})
            
            # 更新计数
            self.update_root_count(root)
        
        logger.info(f"添加 {len(scenes)} 个场景")
    
    def add_smart_compose_result(self, video_path: str, duration: float = 0, template_name: str = ""):
        """添加智能成片结果到素材库
        
        Args:
            video_path: 成片视频路径
            duration: 视频时长（秒），0则自动获取
            template_name: 使用的模板名称
        """
        try:
            if not os.path.exists(video_path):
                logger.warning(f"智能成片文件不存在，无法添加到素材库: {video_path}")
                return None
            
            video_id = f"sc_{hash(video_path)}"
            
            filename = Path(video_path).name
            if duration <= 0:
                try:
                    info = get_video_info(video_path, CONFIG.ffmpeg_path)
                    duration = info.get("duration", 0)
                except:
                    duration = 0
            
            dur_str = format_time(duration) if duration > 0 else ""
            if template_name:
                item_text = f"🎬 {template_name} {dur_str} [{filename}]"
            else:
                item_text = f"🎬 智能成片 {dur_str} [{filename}]"
            
            item = QTreeWidgetItem(self.smart_compose_root, [item_text])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Unchecked)
            item.setData(0, Qt.ItemDataRole.UserRole, {
                "type": "smart_compose",
                "path": video_path,
                "id": video_id,
                "duration": duration,
                "template": template_name,
            })
            
            self.smart_compose_root.setExpanded(True)
            self.update_root_count(self.smart_compose_root)
            
            logger.info(f"智能成片结果已添加到素材库: {filename}, 时长={dur_str}")
            return item
        except Exception as e:
            logger.error(f"添加智能成片结果到素材库失败: {e}")
            return None
    
    def update_root_count(self, root: QTreeWidgetItem):
        """更新根节点计数"""
        count = root.childCount()
        text = root.text(0)
        # 移除旧的计数
        if "(" in text:
            text = text[:text.rfind("(")]
        # 添加新计数
        root.setText(0, f"{text}({count})")
    
    def on_item_clicked(self, item: QTreeWidgetItem, column: int):
        """项目被点击（点击文本预览，点击复选框切换选中）"""
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            # 分组节点点击：不触发展开/折叠，只用箭头控制
            return
        
        item_type = data.get("type")
        if item_type in ("video", "smart_compose"):
            video_path = data.get("path")
            self.video_selected.emit(video_path)
        elif item_type == "scene":
            scene_data = data.get("data")
            self.scene_selected.emit(scene_data)
    
    def on_item_double_clicked(self, item: QTreeWidgetItem, column: int):
        """项目被双击"""
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            # 分组节点双击：不触发展开/折叠，只用箭头控制
            return
        
        item_type = data.get("type")
        if item_type in ("video", "smart_compose"):
            video_path = data.get("path")
            if video_path:
                self.video_selected.emit(video_path)
        elif item_type == "scene":
            scene_data = data.get("data")
            self.scene_selected.emit(scene_data)
    
    def show_context_menu(self, position):
        """显示右键菜单"""
        item = self.tree.itemAt(position)
        
        menu = QMenu(self)
        
        if not item:
            return
        
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data and data.get("type") in ("video", "smart_compose"):
            menu.addAction("播放", lambda: self.play_video(item))
            if data.get("type") == "smart_compose":
                menu.addSeparator()
                menu.addAction("在资源管理器中打开", lambda: self._open_in_explorer(item))
            menu.addAction("删除", lambda: self.remove_item(item))
        
        # 单个场景操作
        if data and data.get("type") == "scene":
            menu.addAction("预览", lambda: self.preview_scene(item))
            menu.addAction("导出此片段", lambda: self.export_single_scene(item))
            menu.addSeparator()
            # 复选框操作
            checked = item.checkState(0) == Qt.CheckState.Checked
            if checked:
                menu.addAction("取消勾选", lambda: item.setCheckState(0, Qt.CheckState.Unchecked))
            else:
                menu.addAction("勾选", lambda: item.setCheckState(0, Qt.CheckState.Checked))
            menu.addAction("删除", lambda: self.remove_item(item))
        
        # 分组节点的右键菜单
        all_roots = [self.original_root, self.hot_root, self.highlight_root, self.smart_compose_root, self.commentary_root]
        if item in all_roots:
            child_count = item.childCount()
            if child_count > 0:
                if item == self.original_root:
                    # 原始素材分组：全选/取消全选
                    checked_count = self._count_checked_in_group(item)
                    menu.addAction(f"全选 ({child_count}个)", lambda: self._check_group(item, Qt.CheckState.Checked))
                    menu.addAction("取消全选", lambda: self._check_group(item, Qt.CheckState.Unchecked))
                    if checked_count > 0:
                        menu.addSeparator()
                        menu.addAction(f"提取已勾选的 {checked_count} 个视频", lambda: self._batch_extract_checked())
                elif item == self.smart_compose_root:
                    checked_count = self._count_checked_in_group(item)
                    menu.addAction(f"勾选全部 ({child_count}个)", lambda: self._check_group(item, Qt.CheckState.Checked))
                    menu.addAction("取消全部勾选", lambda: self._check_group(item, Qt.CheckState.Unchecked))
                    menu.addSeparator()
                    menu.addAction(f"删除此分组全部 ({child_count}个)", lambda: self.delete_group_all(item))
                else:
                    checked_count = self._count_checked_in_group(item)
                    menu.addAction(f"勾选全部 ({child_count}个)", lambda: self._check_group(item, Qt.CheckState.Checked))
                    menu.addAction("取消全部勾选", lambda: self._check_group(item, Qt.CheckState.Unchecked))
                    menu.addSeparator()
                    menu.addAction(f"导出全部 {child_count} 个片段", lambda: self.export_all_children(item))
                    if checked_count > 0:
                        menu.addAction(f"导出已勾选 {checked_count} 个片段", lambda: self._export_checked_in_group(item))
                    menu.addSeparator()
                    menu.addAction(f"删除此分组全部 ({child_count}个)", lambda: self.delete_group_all(item))
        
        menu.exec(self.tree.mapToGlobal(position))
    
    def _open_in_explorer(self, item: QTreeWidgetItem):
        """在资源管理器中打开文件所在位置"""
        import subprocess
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data:
            path = data.get("path")
            if path and os.path.exists(path):
                try:
                    subprocess.run(["explorer", "/select,", os.path.normpath(path)], check=False)
                except Exception as e:
                    logger.error(f"打开资源管理器失败: {e}")
    
    def play_video(self, item: QTreeWidgetItem):
        """播放视频"""
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data:
            video_path = data.get("path")
            self.video_selected.emit(video_path)
    
    def preview_scene(self, item: QTreeWidgetItem):
        """预览场景"""
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data:
            scene_data = data.get("data")
            self.scene_selected.emit(scene_data)
    
    def export_single_scene(self, item: QTreeWidgetItem):
        """导出单个场景片段"""
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data or data.get("type") != "scene":
            return
        
        scene = data.get("data", {})
        self.export_scenes_requested.emit([scene])
    
    def export_all_children(self, root_item: QTreeWidgetItem):
        """导出分组下所有片段（分段导出）"""
        scenes = []
        for i in range(root_item.childCount()):
            child = root_item.child(i)
            data = child.data(0, Qt.ItemDataRole.UserRole)
            if data and data.get("type") == "scene":
                scenes.append(data.get("data", {}))
        
        if scenes:
            self.export_scenes_requested.emit(scenes)
    
    def remove_item(self, item: QTreeWidgetItem):
        """删除单个项目"""
        reply = QMessageBox.question(
            self, "确认删除",
            "确定要删除这个项目吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._do_remove_items([item])
    
    # ─── 复选框相关方法 ───
    
    def on_item_check_changed(self, item: QTreeWidgetItem):
        """复选框状态变化时更新计数和按钮"""
        self._update_check_info()
    
    def _count_checked_scenes(self) -> int:
        """统计所有已勾选的片段数（不包含智能成片成品，成品是完整视频直接预览/导出）"""
        count = 0
        for root in [self.hot_root, self.highlight_root, self.commentary_root]:
            for i in range(root.childCount()):
                if root.child(i).checkState(0) == Qt.CheckState.Checked:
                    count += 1
        return count
    
    def _count_checked_all(self) -> int:
        """统计所有已勾选的项目数（包括智能成片成品）"""
        count = self._count_checked_scenes()
        for i in range(self.smart_compose_root.childCount()):
            if self.smart_compose_root.child(i).checkState(0) == Qt.CheckState.Checked:
                count += 1
        return count
    
    def _count_checked_in_group(self, group_item: QTreeWidgetItem) -> int:
        """统计分组内已勾选的片段数"""
        count = 0
        for i in range(group_item.childCount()):
            if group_item.child(i).checkState(0) == Qt.CheckState.Checked:
                count += 1
        return count
    
    def _get_checked_scene_items(self) -> list:
        """获取所有已勾选的片段项"""
        items = []
        for root in [self.hot_root, self.highlight_root, self.commentary_root]:
            for i in range(root.childCount()):
                child = root.child(i)
                if child.checkState(0) == Qt.CheckState.Checked:
                    items.append(child)
        return items
    
    def get_checked_scenes(self) -> list:
        """获取所有已勾选的片段数据列表（公开接口，供智能成片使用）"""
        scenes = []
        items = self._get_checked_scene_items()
        for item in items:
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data and data.get("type") == "scene":
                scenes.append(data.get("data", {}))
        return scenes
    
    def _check_group(self, group_item: QTreeWidgetItem, state: Qt.CheckState):
        """勾选/取消勾选分组内所有片段"""
        self.tree.blockSignals(True)  # 暂时屏蔽信号避免重复触发
        for i in range(group_item.childCount()):
            group_item.child(i).setCheckState(0, state)
        self.tree.blockSignals(False)
        # 手动更新三态和计数
        self._update_group_tristate(group_item)
        self._update_check_info()
    
    def _update_group_tristate(self, group_item: QTreeWidgetItem):
        """根据子项状态更新分组节点的三态复选框"""
        total = group_item.childCount()
        if total == 0:
            group_item.setCheckState(0, Qt.CheckState.Unchecked)
            return
        checked = sum(1 for i in range(total) 
                     if group_item.child(i).checkState(0) == Qt.CheckState.Checked)
        if checked == 0:
            group_item.setCheckState(0, Qt.CheckState.Unchecked)
        elif checked == total:
            group_item.setCheckState(0, Qt.CheckState.Checked)
        else:
            group_item.setCheckState(0, Qt.CheckState.PartiallyChecked)
    
    def _update_check_info(self):
        """更新已选计数和按钮状态"""
        count = self._count_checked_scenes()
        if count > 0:
            self.selection_label.setText(f"【{count}个文件】")
            self.selection_label.setStyleSheet("color: #7aa2f7; font-size: 12px; font-weight: bold; background: transparent; border: none;")
            self.batch_delete_btn.setEnabled(True)
            self.batch_export_btn.setEnabled(True)
        else:
            self.selection_label.setText("【0个文件】")
            self.selection_label.setStyleSheet("color: #a6adc8; font-size: 12px; background: transparent; border: none;")
            self.batch_delete_btn.setEnabled(False)
            self.batch_export_btn.setEnabled(False)
    
    def export_checked_scenes(self):
        """批量导出所有已勾选的片段"""
        checked_items = self._get_checked_scene_items()
        scenes = []
        for item in checked_items:
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data and data.get("type") == "scene":
                scenes.append(data.get("data", {}))
        if scenes:
            self.export_scenes_requested.emit(scenes)
    
    def _export_checked_in_group(self, group_item: QTreeWidgetItem):
        """导出分组内已勾选的片段"""
        scenes = []
        for i in range(group_item.childCount()):
            child = group_item.child(i)
            if child.checkState(0) == Qt.CheckState.Checked:
                data = child.data(0, Qt.ItemDataRole.UserRole)
                if data and data.get("type") == "scene":
                    scenes.append(data.get("data", {}))
        if scenes:
            self.export_scenes_requested.emit(scenes)
    
    def delete_checked_items(self):
        """批量删除所有已勾选的片段"""
        checked_items = self._get_checked_scene_items()
        if not checked_items:
            return
        
        count = len(checked_items)
        reply = QMessageBox.question(
            self, "确认批量删除",
            f"确定要删除已勾选的 {count} 个片段吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        
        self._do_remove_items(checked_items)
    
    def _do_remove_items(self, items: list):
        """执行删除项目并更新计数"""
        parent_ids = set()
        parents_list = []
        for item in items:
            parent = item.parent()
            if parent:
                data = item.data(0, Qt.ItemDataRole.UserRole)
                if data:
                    scene_id = data.get("id", "")
                    self.scenes.pop(scene_id, None)
                parent.removeChild(item)
                pid = id(parent)
                if pid not in parent_ids:
                    parent_ids.add(pid)
                    parents_list.append(parent)
        
        for parent in parents_list:
            self.update_root_count(parent)
            self._update_group_tristate(parent)
        
        self._update_check_info()
        logger.info(f"批量删除 {len(items)} 个片段")
    
    def delete_group_all(self, group_item: QTreeWidgetItem):
        """删除某个分组下的所有片段"""
        child_count = group_item.childCount()
        if child_count == 0:
            return
        
        group_name = group_item.text(0)
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除 {group_name} 下的全部 {child_count} 个片段吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply != QMessageBox.StandardButton.Yes:
            return
        
        # 清理 scenes 字典
        for i in range(group_item.childCount()):
            child = group_item.child(i)
            data = child.data(0, Qt.ItemDataRole.UserRole)
            if data:
                self.scenes.pop(data.get("id", ""), None)
        
        # 清空子项
        group_item.takeChildren()
        self.update_root_count(group_item)
        self._update_check_info()
        logger.info(f"删除分组 {group_name} 全部 {child_count} 个片段")
    
    def clear_all_confirm(self):
        """确认清空所有素材"""
        total = (self.original_root.childCount() + self.hot_root.childCount() 
                + self.highlight_root.childCount() + self.commentary_root.childCount())
        if total == 0:
            return
        
        reply = QMessageBox.question(
            self, "确认清空",
            f"确定要清空所有 {total} 个素材项吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            self.clear_all()
    
    def batch_analyze_all(self):
        """批量分析素材库中所有原始视频"""
        video_paths = []
        for video_id, video_path in self.videos.items():
            if os.path.exists(video_path):
                video_paths.append(video_path)
        
        if not video_paths:
            QMessageBox.information(self, "提示", "素材库中没有视频，请先添加")
            return
        
        reply = QMessageBox.question(
            self, "确认批量分析",
            f"将对 {len(video_paths)} 个视频逐一执行高燃片段分析，\n"
            f"分析期间请勿关闭窗口。\n\n确定继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            self.batch_analyze_requested.emit(video_paths)
    
    def _batch_extract_checked(self):
        """对原始素材中已勾选的视频发起批量提取"""
        paths = self.get_checked_video_paths()
        if not paths:
            QMessageBox.information(self, "提示", "请先勾选要提取的视频")
            return
        reply = QMessageBox.question(
            self, "确认批量提取",
            f"将对 {len(paths)} 个视频逐一执行高燃片段提取，\n"
            f"期间请勿关闭窗口。\n\n确定继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.batch_extract_requested.emit(paths, "hot")
    
    def get_checked_video_paths(self) -> list:
        """获取原始素材中已勾选的视频路径列表（按显示顺序）"""
        paths = []
        for i in range(self.original_root.childCount()):
            child = self.original_root.child(i)
            if child.checkState(0) == Qt.CheckState.Checked:
                data = child.data(0, Qt.ItemDataRole.UserRole)
                if data and data.get("type") == "video":
                    video_path = data.get("path", "")
                    if video_path and os.path.exists(video_path):
                        paths.append(video_path)
        return paths
    
    def clear_all(self):
        """清空所有素材"""
        self.original_root.takeChildren()
        self.hot_root.takeChildren()
        self.highlight_root.takeChildren()
        self.smart_compose_root.takeChildren()
        self.commentary_root.takeChildren()
        
        self.videos.clear()
        self.scenes.clear()
        
        for root in [self.original_root, self.hot_root, self.highlight_root, self.smart_compose_root, self.commentary_root]:
            self.update_root_count(root)
            root.setCheckState(0, Qt.CheckState.Unchecked)
        
        self._update_check_info()
    
    def clear_smart_compose_results(self):
        """清空智能成片结果（保留原始素材和提取片段）"""
        self.smart_compose_root.takeChildren()
        self.update_root_count(self.smart_compose_root)
        self.smart_compose_root.setCheckState(0, Qt.CheckState.Unchecked)
        self._update_check_info()
