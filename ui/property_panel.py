"""
属性面板组件 - v2.7 可折叠下拉设计
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTextEdit,
    QPushButton, QGroupBox, QFormLayout, QScrollArea, QFrame,
    QSizePolicy
)
from PyQt6.QtCore import Qt, QPropertyAnimation, QEasingCurve, pyqtSignal
from PyQt6.QtGui import QFont
from utils.logger import logger
from utils.helpers import format_time


class CollapsibleSection(QWidget):
    """可折叠下拉区块（点击标题栏展开/收起）"""

    def __init__(self, title: str, expanded: bool = True, parent=None):
        super().__init__(parent)
        self._expanded = expanded

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 标题按钮（可点击切换折叠）
        self._header = QPushButton()
        self._header.setCheckable(True)
        self._header.setChecked(expanded)
        self._header.setObjectName("sectionHeader")
        self._header.setStyleSheet("""
            QPushButton#sectionHeader {
                background-color: #16161f;
                color: #9ba0b4;
                border: 1px solid #2a2a3a;
                border-radius: 5px;
                padding: 8px 12px;
                text-align: left;
                font-size: 12px;
                font-weight: bold;
            }
            QPushButton#sectionHeader:hover {
                background-color: #1c1c28;
                color: #d0d4e0;
                border-color: #363650;
            }
            QPushButton#sectionHeader:checked {
                border-bottom-left-radius: 0px;
                border-bottom-right-radius: 0px;
                border-bottom: none;
            }
        """)
        self._update_header_text(title)
        self._header.clicked.connect(self._toggle)
        layout.addWidget(self._header)

        # 内容容器
        self._content = QWidget()
        self._content.setStyleSheet(
            "QWidget { background-color: #131320; border: 1px solid #2a2a3a; "
            "border-top: none; border-bottom-left-radius: 5px; border-bottom-right-radius: 5px; }"
        )
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(10, 8, 10, 10)
        self._content_layout.setSpacing(6)
        layout.addWidget(self._content)

        if not expanded:
            self._content.setVisible(False)

    def _update_header_text(self, title: str):
        arrow = "▼" if self._expanded else "▶"
        self._header.setText(f" {arrow}  {title}")

    def _toggle(self):
        self._expanded = self._header.isChecked()
        self._content.setVisible(self._expanded)
        title = self._header.text().strip()
        # 去掉旧箭头
        if title.startswith("▼") or title.startswith("▶"):
            title = title[1:].strip()
        self._update_header_text(title)

    def content_layout(self):
        return self._content_layout

    def set_expanded(self, expanded: bool):
        self._expanded = expanded
        self._header.setChecked(expanded)
        self._content.setVisible(expanded)
        title = self._header.text().strip()
        if title.startswith("▼") or title.startswith("▶"):
            title = title[1:].strip()
        self._update_header_text(title)


class PropertyPanel(QWidget):
    """属性面板 - 可折叠下拉设计"""
    
    ai_generate_requested = pyqtSignal()   # AI生成按钮点击
    commentary_applied = pyqtSignal(str)   # 应用按钮点击（发送文案文本）

    def __init__(self):
        super().__init__()
        self.current_scene = None
        self.init_ui()
        logger.info("属性面板初始化完成")

    def init_ui(self):
        """初始化UI"""
        # 外层滚动区域
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        outer_layout.addWidget(scroll)

        container = QWidget()
        container.setStyleSheet("background: transparent;")
        scroll.setWidget(container)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # ─── 1. 片段信息 ───
        self.section_info = CollapsibleSection("片段信息", expanded=False)
        info_layout = QFormLayout()
        info_layout.setSpacing(6)
        info_layout.setContentsMargins(0, 0, 0, 0)

        self.duration_label = QLabel("-")
        info_layout.addRow("时长:", self.duration_label)

        self.type_label = QLabel("-")
        info_layout.addRow("类型:", self.type_label)

        self.confidence_label = QLabel("-")
        info_layout.addRow("置信度:", self.confidence_label)

        self.tags_label = QLabel("-")
        info_layout.addRow("标签:", self.tags_label)

        self.section_info.content_layout().addLayout(info_layout)
        layout.addWidget(self.section_info)

        # ─── 2. 通道得分 ───
        self.section_scores = CollapsibleSection("通道得分", expanded=False)
        scores_layout = QVBoxLayout()
        scores_layout.setContentsMargins(0, 0, 0, 0)
        scores_layout.setSpacing(4)

        self.scores_labels = {}
        score_names = [
            ("scene_change", "场景切换"),
            ("motion", "运动向量"),
            ("audio_energy", "音频能量"),
            ("color_burst", "色彩突变"),
            ("brightness_flash", "亮度闪烁"),
            ("ai_vision", "AI视觉"),
            ("vfx_energy", "VFX能量"),
        ]

        for key, name in score_names:
            label = QLabel(f"{name}: -")
            label.setStyleSheet("font-size: 12px;")
            self.scores_labels[key] = label
            scores_layout.addWidget(label)

        self.section_scores.content_layout().addLayout(scores_layout)
        layout.addWidget(self.section_scores)

        # ─── 3. AI分析结果 ───
        self.section_ai = CollapsibleSection("AI分析结果", expanded=True)
        self.description_text = QTextEdit()
        self.description_text.setReadOnly(True)
        self.description_text.setMinimumHeight(80)
        self.description_text.setMaximumHeight(200)
        self.section_ai.content_layout().addWidget(self.description_text)
        layout.addWidget(self.section_ai)

        # ─── 4. 解说文案编辑（最大，便于查看） ───
        self.section_commentary = CollapsibleSection("解说文案编辑", expanded=True)
        self.commentary_text = QTextEdit()
        self.commentary_text.setPlaceholderText("在此输入解说文案...")
        self.commentary_text.setMinimumHeight(200)
        self.commentary_text.setMaximumHeight(400)
        self.section_commentary.content_layout().addWidget(self.commentary_text)

        # 按钮
        btn_layout = QHBoxLayout()
        self.generate_btn = QPushButton("AI生成")
        self.generate_btn.setToolTip("基于当前视频生成解说文案")
        self.generate_btn.clicked.connect(self.ai_generate_requested.emit)
        btn_layout.addWidget(self.generate_btn)
        self.apply_btn = QPushButton("应用")
        self.apply_btn.setToolTip("将当前文案应用到项目")
        self.apply_btn.clicked.connect(lambda: self.commentary_applied.emit(self.commentary_text.toPlainText()))
        btn_layout.addWidget(self.apply_btn)
        self.section_commentary.content_layout().addLayout(btn_layout)
        layout.addWidget(self.section_commentary)

        layout.addStretch()

    def update_scene_info(self, scene_data: dict):
        """更新场景信息"""
        self.current_scene = scene_data

        # 基本信息
        start_time = scene_data.get("start_time", 0)
        end_time = scene_data.get("end_time", 0)
        duration = end_time - start_time
        self.duration_label.setText(format_time(duration))

        scene_type = scene_data.get("scene_type", "unknown")
        type_map = {
            "action": "动作",
            "vfx_action": "特效动作",
            "highlight": "高光",
            "dialog": "对白",
            "emotion": "情感",
            "climax": "高潮"
        }
        self.type_label.setText(type_map.get(scene_type, scene_type))

        confidence = scene_data.get("confidence", 0)
        self.confidence_label.setText(f"{confidence:.1%}")

        tags = scene_data.get("tags", [])
        self.tags_label.setText(" ".join(tags) if tags else "-")

        # AI描述
        description = scene_data.get("description", "")
        self.description_text.setPlainText(description)

        # 通道得分
        channel_scores = scene_data.get("channel_scores", {})
        for key, label in self.scores_labels.items():
            score = channel_scores.get(key, 0)
            score_name = label.text().split(":")[0]
            label.setText(f"{score_name}: {score:.2f}")

        # 自动展开片段信息和通道得分
        self.section_info.set_expanded(True)
        self.section_scores.set_expanded(True)

        logger.info(f"更新场景信息: {scene_data.get('id', 'unknown')}")
    
    def set_commentary_text(self, text: str):
        """设置解说文案内容（并自动展开解说区块）"""
        self.commentary_text.setPlainText(text)
        self.section_commentary.set_expanded(True)
        logger.info(f"解说文案已更新: {len(text)}字")
