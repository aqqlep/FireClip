"""
轨道编辑器 - 类剪映多轨道时间轴
v2.8: 支持片段拖拽裁剪、分割、原始视频参考轨道
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QFrame, QSizePolicy, QMenu
)
from PyQt6.QtCore import Qt, pyqtSignal, QRectF, QPointF, QTimer
from PyQt6.QtGui import (
    QPainter, QColor, QBrush, QPen, QPolygonF, QFont, QCursor
)
from utils.logger import logger
from utils.helpers import format_time


class TrackEditor(QWidget):
    """轨道编辑器（替代原时间轴）"""

    position_changed = pyqtSignal(float)   # 播放位置变化（秒）
    segment_clicked = pyqtSignal(dict)     # 片段被点击（预览）
    segment_modified = pyqtSignal(dict)    # 片段被修改（拖拽裁剪后）
    segment_split = pyqtSignal(dict, float)  # 片段被分割（片段, 分割时间点）
    playback_toggle = pyqtSignal()         # 请求切换播放/暂停

    def __init__(self):
        super().__init__()
        self.duration = 0.0
        self.current_position = 0.0
        self.segments = []
        self._video_path = ""

        self.init_ui()
        logger.info("轨道编辑器初始化完成")

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(2)

        # ─── 顶部工具栏 ───
        toolbar = QHBoxLayout()

        title = QLabel("轨道编辑器")
        title.setStyleSheet(
            "font-size: 13px; font-weight: bold; color: #7aa2f7; "
            "background: transparent; padding: 2px 0px;"
        )
        toolbar.addWidget(title)
        toolbar.addStretch()

        # 时间显示
        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setStyleSheet(
            "color: #5c6078; font-size: 12px; "
            "font-family: 'Consolas', monospace; background: transparent;"
        )
        toolbar.addWidget(self.time_label)

        # 分割按钮
        split_style = (
            "QPushButton { background: #1c1c28; color: #9ba0b4; border: 1px solid #2a2a3a; "
            "border-radius: 4px; font-size: 12px; padding: 4px 10px; }"
            "QPushButton:hover { background: #2a2a3a; color: #f7768e; border-color: #f7768e; }"
        )
        self.split_btn = QPushButton("✂ 分割")
        self.split_btn.setToolTip("在播放头位置分割选中的片段")
        self.split_btn.setStyleSheet(split_style)
        self.split_btn.clicked.connect(self.do_split)
        toolbar.addWidget(self.split_btn)

        # 删除选中按钮
        self.del_btn = QPushButton("✕ 删除")
        self.del_btn.setToolTip("删除选中的片段")
        self.del_btn.setStyleSheet(split_style.replace("#f7768e", "#f7768e"))
        self.del_btn.clicked.connect(self.do_delete_selected)
        toolbar.addWidget(self.del_btn)

        # 缩放按钮
        zoom_style = (
            "QPushButton { background: #1c1c28; color: #9ba0b4; border: 1px solid #2a2a3a; "
            "border-radius: 4px; font-size: 14px; font-weight: bold; }"
            "QPushButton:hover { background: #2a2a3a; color: #d0d4e0; }"
        )
        zoom_in = QPushButton("+")
        zoom_in.setFixedSize(28, 28)
        zoom_in.setStyleSheet(zoom_style)
        zoom_in.clicked.connect(self.zoom_in)
        toolbar.addWidget(zoom_in)

        zoom_out = QPushButton("−")
        zoom_out.setFixedSize(28, 28)
        zoom_out.setStyleSheet(zoom_style)
        zoom_out.clicked.connect(self.zoom_out)
        toolbar.addWidget(zoom_out)

        layout.addLayout(toolbar)

        # ─── 轨道画布（带滚动） ───
        self.canvas = TrackCanvas()
        self.canvas.setMinimumHeight(180)
        self.canvas.position_clicked.connect(self.on_position_clicked)
        self.canvas.segment_clicked.connect(self.on_segment_clicked)
        self.canvas.segment_modified.connect(self.on_segment_modified)
        self.canvas.split_requested.connect(self.do_split)
        self.canvas.delete_requested.connect(self.do_delete_selected)
        layout.addWidget(self.canvas)

        # 设置焦点以接收键盘事件
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    # ─── 公共接口 ───
    def set_duration(self, duration: float):
        self.duration = duration
        self.canvas.set_duration(duration)
        self.update_time_display()

    def set_position(self, position: float):
        self.current_position = position
        self.canvas.set_position(position)
        self.update_time_display()

    def add_segment(self, segment_data: dict):
        self.segments.append(segment_data)
        self.canvas.set_segments(self.segments)

    def clear_segments(self):
        self.segments.clear()
        self.canvas.set_segments(self.segments)

    def set_video_path(self, path: str):
        self._video_path = path
        self.canvas.set_video_path(path)

    # ─── 事件处理 ───
    def update_time_display(self):
        current = format_time(self.current_position)
        total = format_time(self.duration)
        self.time_label.setText(f"{current} / {total}")

    def on_position_clicked(self, position: float):
        self.position_changed.emit(position)

    def on_segment_clicked(self, segment_data: dict):
        self.segment_clicked.emit(segment_data)

    def on_segment_modified(self, segment_data: dict):
        """片段被拖拽修改后，同步更新内部数据"""
        for i, seg in enumerate(self.segments):
            if seg.get("id") == segment_data.get("id"):
                self.segments[i] = segment_data
                break
        self.segment_modified.emit(segment_data)

    def zoom_in(self):
        self.canvas.zoom(1.3)

    def zoom_out(self):
        self.canvas.zoom(0.7)

    def do_split(self):
        """在播放头位置分割选中的片段"""
        pos = self.current_position
        for seg in self.segments:
            st = seg.get("start_time", 0)
            et = seg.get("end_time", 0)
            if st < pos < et:
                self.segment_split.emit(seg, pos)
                logger.info(f"分割片段: {format_time(st)}-{format_time(et)} @ {format_time(pos)}")
                return
        logger.info("播放头位置没有可分割的片段")

    def do_delete_selected(self):
        """删除选中片段"""
        selected = self.canvas.get_selected_segment()
        if selected:
            self.segments = [s for s in self.segments if s.get("id") != selected.get("id")]
            self.canvas.set_segments(self.segments)
            self.canvas.clear_selection()
            logger.info(f"删除片段: {selected.get('id', '?')}")

    def keyPressEvent(self, event):
        """键盘快捷键"""
        key = event.key()
        if key == Qt.Key.Key_Space:
            self.playback_toggle.emit()
        elif key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.do_delete_selected()
        elif key == Qt.Key.Key_S:
            self.do_split()
        elif key == Qt.Key.Key_Plus or key == Qt.Key.Key_Equal:
            self.zoom_in()
        elif key == Qt.Key.Key_Minus:
            self.zoom_out()
        elif key == Qt.Key.Key_Left:
            # 向左移动播放头
            new_pos = max(0, self.current_position - 1.0)
            self.position_changed.emit(new_pos)
        elif key == Qt.Key.Key_Right:
            # 向右移动播放头
            new_pos = min(self.duration, self.current_position + 1.0)
            self.position_changed.emit(new_pos)
        else:
            super().keyPressEvent(event)


# ═══════════════════════════════════════════════════════
# 轨道画布 - 核心绘制 + 交互
# ═══════════════════════════════════════════════════════

class TrackCanvas(QFrame):
    """轨道画布：绘制标尺、视频轨道、片段轨道"""

    position_clicked = pyqtSignal(float)
    segment_clicked = pyqtSignal(dict)
    segment_modified = pyqtSignal(dict)
    split_requested = pyqtSignal()
    delete_requested = pyqtSignal()

    # 布局常量
    RULER_H = 24          # 标尺高度
    TRACK_LABEL_W = 70    # 轨道标签宽度
    VIDEO_TRACK_H = 50    # 视频参考轨道高度
    CLIP_TRACK_H = 56     # 片段轨道高度
    TRACK_GAP = 4         # 轨道间距
    TRIM_ZONE = 8         # 拖拽裁剪感应区域(px)

    def __init__(self):
        super().__init__()
        self.duration = 0.0
        self.current_position = 0.0
        self.segments = []
        self.zoom_level = 1.0
        self._video_path = ""

        # 交互状态
        self._selected_id = None
        self._drag_mode = None      # None / "move" / "trim_left" / "trim_right"
        self._drag_start_x = 0
        self._drag_original = None  # 拖拽开始时的片段副本
        self._hover_zone = None     # "left" / "right" / "body" / None

        self.setFrameStyle(QFrame.Shape.StyledPanel)
        self.setStyleSheet("background-color: #0f0f17; border: 1px solid #2a2a3a; border-radius: 4px;")
        self.setMouseTracking(True)

    # ─── 公共接口 ───
    def set_duration(self, d: float):
        self.duration = d
        self.update()

    def set_position(self, p: float):
        self.current_position = p
        self.update()

    def set_segments(self, segs: list):
        self.segments = list(segs)
        self.update()

    def set_video_path(self, path: str):
        self._video_path = path
        self.update()

    def get_selected_segment(self) -> dict | None:
        if self._selected_id:
            for s in self.segments:
                if s.get("id") == self._selected_id:
                    return s
        return None

    def clear_selection(self):
        self._selected_id = None
        self.update()

    def zoom(self, factor: float):
        self.zoom_level = max(0.3, min(8.0, self.zoom_level * factor))
        self.update()

    # ─── 坐标工具 ───
    def _content_x(self):
        """内容区域起始x（跳过轨道标签）"""
        return self.TRACK_LABEL_W

    def _content_width(self):
        return max(self.width() - self.TRACK_LABEL_W, 100)

    def _time_to_x(self, t: float) -> int:
        if self.duration <= 0:
            return self._content_x()
        return self._content_x() + int(t / self.duration * self._content_width() * self.zoom_level)

    def _x_to_time(self, x: int) -> float:
        cw = self._content_width() * self.zoom_level
        if cw <= 0:
            return 0
        return max(0, min(self.duration, (x - self._content_x()) / cw * self.duration))

    def _video_track_y(self):
        return self.RULER_H + self.TRACK_GAP

    def _clip_track_y(self):
        return self._video_track_y() + self.VIDEO_TRACK_H + self.TRACK_GAP

    # ─── 绘制 ───
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        # 背景
        painter.fillRect(0, 0, w, h, QColor("#0f0f17"))

        if self.duration <= 0:
            painter.setPen(QColor("#5c6078"))
            painter.drawText(w // 2 - 60, h // 2, "请导入视频")
            painter.end()
            return

        self._draw_ruler(painter, w)
        self._draw_video_track(painter, w)
        self._draw_clip_track(painter, w)
        self._draw_playhead(painter, h)
        painter.end()

    def _draw_ruler(self, painter: QPainter, w: int):
        """绘制时间标尺"""
        y0 = 0
        painter.fillRect(0, y0, w, self.RULER_H, QColor("#16161f"))

        pps = self._content_width() * self.zoom_level / self.duration  # pixels per second
        tick = self._calc_tick_interval(self.duration / self.zoom_level)

        painter.setPen(QPen(QColor("#363650"), 1))
        painter.setFont(QFont("Consolas", 8))

        t = 0.0
        while t <= self.duration:
            x = self._time_to_x(t)
            if x >= self._content_x() and x <= w:
                painter.drawLine(x, y0 + 14, x, y0 + self.RULER_H)
                painter.setPen(QColor("#5c6078"))
                painter.drawText(x + 2, y0 + 12, format_time(t))
                painter.setPen(QPen(QColor("#363650"), 1))
            t += tick

        # 底部分隔线
        painter.setPen(QPen(QColor("#2a2a3a"), 1))
        painter.drawLine(0, self.RULER_H - 1, w, self.RULER_H - 1)

    def _draw_video_track(self, painter: QPainter, w: int):
        """绘制原始视频参考轨道"""
        ty = self._video_track_y()

        # 轨道标签
        painter.setPen(QColor("#5c6078"))
        painter.setFont(QFont("", 9))
        painter.drawText(4, ty + self.VIDEO_TRACK_H // 2 + 4, "原始视频")

        # 视频条（整条，淡色）
        x1 = self._content_x()
        x2 = self._time_to_x(self.duration)
        painter.fillRect(x1, ty, x2 - x1, self.VIDEO_TRACK_H, QColor("#1c1c28"))
        painter.setPen(QPen(QColor("#2a2a3a"), 1))
        painter.drawRect(x1, ty, x2 - x1, self.VIDEO_TRACK_H)

        # 简单波形纹理（模拟视频内容）
        painter.setPen(QPen(QColor("#2a2a3a"), 1))
        mid_y = ty + self.VIDEO_TRACK_H // 2
        import math
        for px in range(x1, x2, 3):
            t = self._x_to_time(px)
            amp = int(8 + 6 * math.sin(t * 0.7) + 4 * math.sin(t * 2.1))
            painter.drawLine(px, mid_y - amp, px, mid_y + amp)

        # 视频文件名
        if self._video_path:
            import os
            video_name = os.path.basename(self._video_path)
            painter.setPen(QColor("#7aa2f7"))
            painter.setFont(QFont("", 9, QFont.Weight.Bold))
            text_x = x1 + 8
            text_y = ty + 16
            # 背景色块
            fm = painter.fontMetrics()
            tw = fm.horizontalAdvance(video_name)
            painter.fillRect(text_x - 4, ty + 4, tw + 8, 16, QColor(28, 28, 40, 200))
            painter.drawText(text_x, text_y, video_name)

    def _draw_clip_track(self, painter: QPainter, w: int):
        """绘制片段轨道"""
        ty = self._clip_track_y()

        # 轨道标签
        painter.setPen(QColor("#5c6078"))
        painter.setFont(QFont("", 9))
        painter.drawText(4, ty + self.CLIP_TRACK_H // 2 + 4, "剪辑轨道")

        # 轨道底色
        x1 = self._content_x()
        x2 = self._time_to_x(self.duration)
        painter.fillRect(x1, ty, x2 - x1, self.CLIP_TRACK_H, QColor("#131320"))
        painter.setPen(QPen(QColor("#1c1c28"), 1))
        painter.drawRect(x1, ty, x2 - x1, self.CLIP_TRACK_H)

        # 绘制片段
        color_map = {
            "action": QColor("#f7768e"),
            "vfx_action": QColor("#ff9e64"),
            "highlight": QColor("#9ece6a"),
            "dialog": QColor("#7aa2f7"),
            "emotion": QColor("#bb9af7"),
            "climax": QColor("#e0af68"),
        }

        for seg in self.segments:
            st = seg.get("start_time", 0)
            et = seg.get("end_time", 0)
            scene_type = seg.get("scene_type", "unknown")
            seg_id = seg.get("id", "")

            sx = self._time_to_x(st)
            ex = self._time_to_x(et)
            sw = ex - sx
            if sw < 2:
                sw = 2
                ex = sx + 2

            if ex < self._content_x() or sx > w:
                continue

            color = color_map.get(scene_type, QColor("#5c6078"))
            is_selected = (seg_id == self._selected_id)

            # 片段主体
            clip_color = QColor(color)
            if is_selected:
                clip_color = clip_color.lighter(120)
            painter.fillRect(sx, ty + 3, sw, self.CLIP_TRACK_H - 6, clip_color)

            # 选中时高亮边框
            if is_selected:
                painter.setPen(QPen(QColor("#ffffff"), 2))
            else:
                painter.setPen(QPen(QColor("#0f0f17"), 1))
            painter.drawRect(sx, ty + 3, sw, self.CLIP_TRACK_H - 6)

            # 裁剪手柄（左右各一个小方块）
            if is_selected:
                handle_size = 6
                handle_color = QColor("#ffffff")
                painter.fillRect(sx - 1, ty + self.CLIP_TRACK_H // 2 - handle_size // 2,
                                 handle_size, handle_size, handle_color)
                painter.fillRect(ex - handle_size + 1, ty + self.CLIP_TRACK_H // 2 - handle_size // 2,
                                 handle_size, handle_size, handle_color)

            # 片段标签
            desc = seg.get("description", seg.get("scene_type", ""))
            if sw > 30:
                painter.setPen(QPen(QColor("#0f0f17")))
                painter.setFont(QFont("", 8))
                label = desc[:max(1, sw // 8)]
                painter.drawText(sx + 8, ty + self.CLIP_TRACK_H // 2 + 4, label)
            
            # 片段时长（底部小字）
            if sw > 50:
                dur = et - st
                painter.setPen(QPen(QColor(15, 15, 23, 180)))
                painter.setFont(QFont("", 7))
                painter.drawText(sx + 8, ty + self.CLIP_TRACK_H - 10, f"{dur:.1f}s")

    def _draw_playhead(self, painter: QPainter, h: int):
        """绘制播放头"""
        if self.current_position < 0:
            return
        px = self._time_to_x(self.current_position)
        cx = self._content_x()
        if px < cx:
            return

        painter.setPen(QPen(QColor("#f7768e"), 2))
        painter.drawLine(px, 0, px, h)

        # 三角头
        painter.setBrush(QBrush(QColor("#f7768e")))
        tri = QPolygonF([
            QPointF(px - 6, 0),
            QPointF(px + 6, 0),
            QPointF(px, 8)
        ])
        painter.drawPolygon(tri)

    # ─── 交互 ───
    def _hit_test(self, x: int, y: int) -> tuple:
        """命中检测: (segment_dict | None, zone: "left"/"right"/"body"/None)"""
        ty = self._clip_track_y()
        if y < ty or y > ty + self.CLIP_TRACK_H:
            return None, None

        for seg in reversed(self.segments):
            st = seg.get("start_time", 0)
            et = seg.get("end_time", 0)
            sx = self._time_to_x(st)
            ex = self._time_to_x(et)
            if sx <= x <= ex:
                if x - sx <= self.TRIM_ZONE:
                    return seg, "left"
                elif ex - x <= self.TRIM_ZONE:
                    return seg, "right"
                else:
                    return seg, "body"
        return None, None

    def mousePressEvent(self, event):
        if self.duration <= 0:
            return
        x, y = int(event.position().x()), int(event.position().y())

        # 右键菜单
        if event.button() == Qt.MouseButton.RightButton:
            self._show_context_menu(event.globalPosition().toPoint(), x, y)
            return

        # 标尺区域 → 移动播放头
        if y < self.RULER_H:
            self.position_clicked.emit(self._x_to_time(x))
            return

        # 片段轨道
        seg, zone = self._hit_test(x, y)
        if seg:
            self._selected_id = seg.get("id")
            if zone == "body" and event.button() == Qt.MouseButton.LeftButton:
                # 点击片段 → 预览
                self.segment_clicked.emit(seg)
                self._drag_mode = "move"
                self._drag_start_x = x
                self._drag_original = dict(seg)
            elif zone in ("left", "right"):
                self._drag_mode = f"trim_{zone}"
                self._drag_start_x = x
                self._drag_original = dict(seg)
            self.update()
        else:
            # 空白区域 → 取消选中 + 移动播放头
            self._selected_id = None
            self._drag_mode = None
            if x >= self._content_x():
                self.position_clicked.emit(self._x_to_time(x))
            self.update()

    def _show_context_menu(self, global_pos, x, y):
        """显示右键菜单"""
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background: #1c1c28; color: #d0d4e0; border: 1px solid #2a2a3a; }
            QMenu::item:selected { background: #2a2a3a; }
            QMenu::separator { height: 1px; background: #2a2a3a; margin: 4px 8px; }
        """)

        seg, zone = self._hit_test(x, y)
        if seg:
            self._selected_id = seg.get("id")
            self.update()
            
            menu.addAction("预览片段", lambda: self.segment_clicked.emit(seg))
            menu.addSeparator()
            menu.addAction("✂ 分割", self.split_requested.emit)
            menu.addAction("✕ 删除", self.delete_requested.emit)
            menu.addSeparator()
            
            # 片段信息
            st = seg.get("start_time", 0)
            et = seg.get("end_time", 0)
            dur = et - st
            info_action = menu.addAction(f"⚐ {format_time(st)} - {format_time(et)} ({dur:.1f}s)")
            info_action.setEnabled(False)
        else:
            menu.addAction("✂ 在播放头分割", self.split_requested.emit)
            
            # 跳转到播放头
            if y < self.RULER_H:
                menu.addAction(f"跳转到 {format_time(self._x_to_time(x))}", 
                             lambda: self.position_clicked.emit(self._x_to_time(x)))

        menu.exec(global_pos)

    def mouseMoveEvent(self, event):
        if self.duration <= 0:
            return
        x, y = int(event.position().x()), int(event.position().y())

        # 拖拽中
        if self._drag_mode and self._drag_original and event.buttons() & Qt.MouseButton.LeftButton:
            dt = self._x_to_time(x) - self._x_to_time(self._drag_start_x)
            seg_id = self._drag_original.get("id")

            for seg in self.segments:
                if seg.get("id") != seg_id:
                    continue
                orig = self._drag_original
                if self._drag_mode == "trim_left":
                    new_st = max(0, orig.get("start_time", 0) + dt)
                    if new_st < orig.get("end_time", 0) - 1:
                        seg["start_time"] = new_st
                        seg["duration"] = seg["end_time"] - seg["start_time"]
                elif self._drag_mode == "trim_right":
                    new_et = min(self.duration, orig.get("end_time", 0) + dt)
                    if new_et > orig.get("start_time", 0) + 1:
                        seg["end_time"] = new_et
                        seg["duration"] = seg["end_time"] - seg["start_time"]
                elif self._drag_mode == "move":
                    dur = orig.get("end_time", 0) - orig.get("start_time", 0)
                    new_st = max(0, min(self.duration - dur, orig.get("start_time", 0) + dt))
                    seg["start_time"] = new_st
                    seg["end_time"] = new_st + dur
                break
            self.update()
            return

        # 非拖拽 → 更新光标 + tooltip
        seg, zone = self._hit_test(x, y)
        if zone in ("left", "right"):
            self.setCursor(QCursor(Qt.CursorShape.SizeHorCursor))
            self.setToolTip(f"拖拽裁剪 ({zone})")
        elif zone == "body":
            self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            if seg:
                st = seg.get("start_time", 0)
                et = seg.get("end_time", 0)
                desc = seg.get("description", seg.get("scene_type", "片段"))
                self.setToolTip(f"{desc}\n{format_time(st)} - {format_time(et)} ({et-st:.1f}s)")
            else:
                self.setToolTip("")
        else:
            self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
            self.setToolTip("")

    def mouseReleaseEvent(self, event):
        if self._drag_mode and self._drag_original:
            # 找到被修改的片段并发射信号
            seg_id = self._drag_original.get("id")
            for seg in self.segments:
                if seg.get("id") == seg_id:
                    if (seg.get("start_time") != self._drag_original.get("start_time") or
                            seg.get("end_time") != self._drag_original.get("end_time")):
                        self.segment_modified.emit(seg)
                    break
            self._drag_mode = None
            self._drag_original = None
            self.update()

    def mouseDoubleClickEvent(self, event):
        """双击片段 → 在视频预览中播放"""
        if self.duration <= 0:
            return
        x, y = int(event.position().x()), int(event.position().y())
        seg, zone = self._hit_test(x, y)
        if seg:
            self.segment_clicked.emit(seg)
            logger.info(f"双击预览片段: {seg.get('id', '?')}")

    # ─── 工具方法 ───
    def _calc_tick_interval(self, visible_duration: float) -> float:
        if visible_duration <= 10:
            return 1
        elif visible_duration <= 30:
            return 5
        elif visible_duration <= 60:
            return 10
        elif visible_duration <= 300:
            return 30
        elif visible_duration <= 600:
            return 60
        elif visible_duration <= 1800:
            return 120
        else:
            return 300
