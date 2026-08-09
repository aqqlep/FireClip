"""
FireClip 统一主题样式 v3.0
深色主题 + 浅色主题，运行时切换
配色参考: Catppuccin Mocha (深色) / Catppuccin Latte (浅色)
"""

# ============ 深色主题配色 (Catppuccin Mocha 风格) ============
DARK = {
    "bg_base": "#11111b",
    "bg_surface": "#181825",
    "bg_surface2": "#1e1e2e",
    "bg_overlay": "#313244",
    "bg_control": "#0e0e18",
    "text": "#cdd6f4",
    "text_dim": "#a6adc8",
    "text_muted": "#585b70",
    "accent": "#89b4fa",
    "accent_hover": "#b4d0fb",
    "accent_green": "#a6e3a1",
    "accent_red": "#f38ba8",
    "accent_yellow": "#f9e2af",
    "accent_mauve": "#cba6f7",
    "accent_peach": "#fab387",
    "border": "#313244",
    "border_light": "#45475a",
    "border_hover": "#585b70",
    # Windows 标题栏
    "win_dark_mode": True,
    "caption_color": 0x001b1811,   # ABGR for #11111b
    "caption_text":  0x00f4d6cd,   # ABGR for #cdd6f4
}

# ============ 浅色主题配色 (Catppuccin Latte 风格) ============
LIGHT = {
    "bg_base": "#eff1f5",
    "bg_surface": "#e6e9ef",
    "bg_surface2": "#dce0e8",
    "bg_overlay": "#bcc0cc",
    "bg_control": "#e8eaf0",
    "text": "#4c4f69",
    "text_dim": "#6c6f85",
    "text_muted": "#9ca0b0",
    "accent": "#1e66f5",
    "accent_hover": "#4c83f7",
    "accent_green": "#40a02b",
    "accent_red": "#d20f39",
    "accent_yellow": "#df8e1d",
    "accent_mauve": "#8839ef",
    "accent_peach": "#fe640b",
    "border": "#ccd0da",
    "border_light": "#bcc0cc",
    "border_hover": "#9ca0b0",
    # Windows 标题栏
    "win_dark_mode": False,
    "caption_color": 0x00efe1e0,   # ABGR for #eff1f5 → little-endian BGR
    "caption_text":  0x00694f4c,   # ABGR for #4c4f69
}

# 当前主题 (全局状态)
_current_theme = "dark"


def set_theme(theme: str):
    """设置当前主题 'dark' or 'light'"""
    global _current_theme
    _current_theme = "dark" if theme == "dark" else "light"


def get_theme() -> str:
    """获取当前主题名"""
    return _current_theme


def get_colors() -> dict:
    """获取当前主题配色字典"""
    return DARK if _current_theme == "dark" else LIGHT


def get_qss() -> str:
    """生成当前主题的 QSS 样式表"""
    C = get_colors()
    return _build_qss(C)


def _build_qss(C: dict) -> str:
    """根据配色字典生成完整QSS"""
    return (
        # ========== 全局基础 ==========
        "QWidget {\n"
        f"    background-color: {C['bg_base']};\n"
        f"    color: {C['text']};\n"
        '    font-family: "Microsoft YaHei", "Segoe UI", sans-serif;\n'
        "    font-size: 13px;\n"
        "}\n\n"

        "QMainWindow, QDialog {\n"
        f"    background-color: {C['bg_base']};\n"
        "}\n\n"

        "QFrame {\n"
        f"    background-color: {C['bg_surface']};\n"
        f"    border: 1px solid {C['border']};\n"
        "    border-radius: 6px;\n"
        "}\n\n"

        # ========== 工具栏 ==========
        "QToolBar {\n"
        f"    background-color: {C['bg_surface']};\n"
        "    border: none;\n"
        f"    border-bottom: 1px solid {C['border']};\n"
        "    spacing: 6px;\n"
        "    padding: 6px 12px;\n"
        "}\n\n"

        "QToolBar::separator {\n"
        "    width: 1px;\n"
        f"    background-color: {C['border']};\n"
        "    margin: 6px 8px;\n"
        "}\n\n"

        "QToolBar QToolButton {\n"
        f"    background-color: {C['bg_surface2']};\n"
        f"    color: {C['text']};\n"
        f"    border: 1px solid {C['border']};\n"
        "    border-radius: 5px;\n"
        "    padding: 7px 14px;\n"
        "    font-size: 13px;\n"
        "    font-weight: 500;\n"
        "    min-height: 28px;\n"
        "}\n\n"

        "QToolBar QToolButton:hover {\n"
        f"    background-color: {C['bg_overlay']};\n"
        f"    border: 1px solid {C['border_light']};\n"
        "}\n\n"

        "QToolBar QToolButton:pressed {\n"
        f"    background-color: {C['border']};\n"
        "}\n\n"

        # ========== 通用按钮 ==========
        "QPushButton {\n"
        f"    background-color: {C['bg_surface2']};\n"
        f"    color: {C['text']};\n"
        f"    border: 1px solid {C['border']};\n"
        "    border-radius: 5px;\n"
        "    padding: 7px 18px;\n"
        "    font-weight: 500;\n"
        "    min-height: 20px;\n"
        "}\n\n"

        "QPushButton:hover {\n"
        f"    background-color: {C['bg_overlay']};\n"
        f"    border: 1px solid {C['border_hover']};\n"
        "}\n\n"

        "QPushButton:pressed {\n"
        f"    background-color: {C['border']};\n"
        "}\n\n"

        "QPushButton:disabled {\n"
        f"    background-color: {C['bg_surface']};\n"
        f"    color: {C['text_muted']};\n"
        f"    border-color: {C['border']};\n"
        "}\n\n"

        # 播放按钮
        "QPushButton#playButton {\n"
        f"    background-color: {C['accent']};\n"
        f"    color: {'#11111b' if _current_theme == 'dark' else '#ffffff'};\n"
        "    border: none;\n"
        "    font-weight: bold;\n"
        "    font-size: 13px;\n"
        "    letter-spacing: 1px;\n"
        "}\n\n"

        "QPushButton#playButton:hover {\n"
        f"    background-color: {C['accent_hover']};\n"
        "}\n\n"

        "QPushButton#playButton:pressed {\n"
        f"    background-color: {C['accent']};\n"
        "}\n\n"

        # 停止按钮
        "QPushButton#stopButton {\n"
        f"    background-color: {C['bg_overlay']};\n"
        f"    color: {C['text_dim']};\n"
        "    border: none;\n"
        "    font-weight: 500;\n"
        "    font-size: 13px;\n"
        "}\n\n"

        "QPushButton#stopButton:hover {\n"
        f"    background-color: {C['border_hover']};\n"
        f"    color: {C['text']};\n"
        "}\n\n"

        # ========== 视频控制栏 ==========
        "QWidget#videoControlBar {\n"
        f"    background-color: {C['bg_control']};\n"
        f"    border-top: 1px solid {C['border']};\n"
        "}\n\n"

        "QLabel#timeLabel {\n"
        f"    color: {C['text_dim']};\n"
        "    font-size: 12px;\n"
        '    font-family: "Consolas", "Courier New", monospace;\n'
        "    background: transparent;\n"
        "}\n\n"

        # ========== 标签 ==========
        "QLabel {\n"
        f"    color: {C['text']};\n"
        "    background: transparent;\n"
        "}\n\n"

        # ========== 输入框 ==========
        "QLineEdit, QTextEdit, QPlainTextEdit {\n"
        f"    background-color: {C['bg_surface']};\n"
        f"    color: {C['text']};\n"
        f"    border: 1px solid {C['border']};\n"
        "    border-radius: 4px;\n"
        "    padding: 5px 8px;\n"
        f"    selection-background-color: {C['accent']};\n"
        f"    selection-color: {'#11111b' if _current_theme == 'dark' else '#ffffff'};\n"
        "}\n\n"

        "QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {\n"
        f"    border: 1px solid {C['accent']};\n"
        "}\n\n"

        # ========== 列表/树 ==========
        "QListWidget, QListView, QTreeWidget, QTableWidget {\n"
        f"    background-color: {C['bg_surface']};\n"
        f"    color: {C['text']};\n"
        f"    border: 1px solid {C['border']};\n"
        "    border-radius: 6px;\n"
        "    padding: 4px;\n"
        "    outline: none;\n"
        "}\n\n"

        "QListWidget::item, QListView::item, QTreeWidget::item, QTableWidget::item {\n"
        "    padding: 7px 8px;\n"
        "    border-radius: 4px;\n"
        "}\n\n"

        "QListWidget::item:selected, QListView::item:selected, QTreeWidget::item:selected {\n"
        f"    background-color: {C['bg_overlay']};\n"
        f"    color: {C['text']};\n"
        "}\n\n"

        "QListWidget::item:hover, QListView::item:hover {\n"
        f"    background-color: {C['bg_surface2']};\n"
        "}\n\n"

        # ========== 滚动条 ==========
        "QScrollBar:vertical {\n"
        f"    background: {C['bg_surface']};\n"
        "    width: 8px;\n"
        "    margin: 0px;\n"
        "    border-radius: 4px;\n"
        "}\n\n"

        "QScrollBar::handle:vertical {\n"
        f"    background: {C['bg_overlay']};\n"
        "    min-height: 30px;\n"
        "    border-radius: 4px;\n"
        "}\n\n"

        "QScrollBar::handle:vertical:hover {\n"
        f"    background: {C['border_hover']};\n"
        "}\n\n"

        "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {\n"
        "    height: 0px;\n"
        "}\n\n"

        "QScrollBar:horizontal {\n"
        f"    background: {C['bg_surface']};\n"
        "    height: 8px;\n"
        "    margin: 0px;\n"
        "    border-radius: 4px;\n"
        "}\n\n"

        "QScrollBar::handle:horizontal {\n"
        f"    background: {C['bg_overlay']};\n"
        "    min-width: 30px;\n"
        "    border-radius: 4px;\n"
        "}\n\n"

        "QScrollBar::handle:horizontal:hover {\n"
        f"    background: {C['border_hover']};\n"
        "}\n\n"

        "QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {\n"
        "    width: 0px;\n"
        "}\n\n"

        # ========== 滑块 ==========
        "QSlider::groove:horizontal {\n"
        f"    background-color: {C['bg_overlay']};\n"
        "    height: 4px;\n"
        "    border-radius: 2px;\n"
        "}\n\n"

        "QSlider::handle:horizontal {\n"
        f"    background-color: {C['accent']};\n"
        "    width: 14px;\n"
        "    height: 14px;\n"
        "    margin: -5px 0;\n"
        "    border-radius: 7px;\n"
        "}\n\n"

        "QSlider::handle:horizontal:hover {\n"
        f"    background-color: {C['accent_hover']};\n"
        "}\n\n"

        "QSlider::sub-page:horizontal {\n"
        f"    background-color: {C['accent']};\n"
        "    border-radius: 2px;\n"
        "}\n\n"

        "QSlider#volumeSlider::groove:horizontal {\n"
        "    height: 3px;\n"
        "}\n\n"

        "QSlider#volumeSlider::handle:horizontal {\n"
        "    width: 12px;\n"
        "    height: 12px;\n"
        "    margin: -5px 0;\n"
        "    border-radius: 6px;\n"
        "}\n\n"

        # ========== 下拉框 ==========
        "QComboBox {\n"
        f"    background-color: {C['bg_surface']};\n"
        f"    color: {C['text']};\n"
        f"    border: 1px solid {C['border']};\n"
        "    border-radius: 4px;\n"
        "    padding: 5px 10px;\n"
        "    min-height: 24px;\n"
        "}\n\n"

        "QComboBox:hover {\n"
        f"    border: 1px solid {C['border_hover']};\n"
        "}\n\n"

        "QComboBox:focus {\n"
        f"    border: 1px solid {C['accent']};\n"
        "}\n\n"

        "QComboBox QAbstractItemView {\n"
        f"    background-color: {C['bg_surface2']};\n"
        f"    border: 1px solid {C['border']};\n"
        f"    selection-background-color: {C['bg_overlay']};\n"
        "    outline: 0px;\n"
        "}\n\n"

        # ========== 进度条 ==========
        "QProgressBar {\n"
        f"    background-color: {C['bg_surface']};\n"
        f"    color: {C['text_dim']};\n"
        f"    border: 1px solid {C['border']};\n"
        "    border-radius: 4px;\n"
        "    text-align: center;\n"
        "    height: 12px;\n"
        "    font-size: 11px;\n"
        "}\n\n"

        "QProgressBar::chunk {\n"
        f"    background-color: {C['accent']};\n"
        "    border-radius: 3px;\n"
        "}\n\n"

        # ========== 复选框 ==========
        "QCheckBox, QRadioButton {\n"
        f"    color: {C['text']};\n"
        "    spacing: 8px;\n"
        "}\n\n"

        "QCheckBox::indicator, QRadioButton::indicator {\n"
        "    width: 16px;\n"
        "    height: 16px;\n"
        "    border-radius: 3px;\n"
        f"    border: 2px solid {C['text_muted']};\n"
        "    background-color: transparent;\n"
        "}\n\n"

        "QCheckBox::indicator:checked, QRadioButton::indicator:checked {\n"
        f"    background-color: {C['accent']};\n"
        f"    border: 2px solid {C['accent']};\n"
        "}\n\n"

        "QCheckBox::indicator:hover, QRadioButton::indicator:hover {\n"
        f"    border: 2px solid {C['accent']};\n"
        "}\n\n"

        "QRadioButton::indicator {\n"
        "    border-radius: 8px;\n"
        "}\n\n"

        # ========== 状态栏 ==========
        "QStatusBar {\n"
        f"    background-color: {C['bg_surface']};\n"
        f"    color: {C['text_muted']};\n"
        f"    border-top: 1px solid {C['border']};\n"
        "    font-size: 12px;\n"
        "    padding: 2px 8px;\n"
        "}\n\n"

        # ========== 分割器 ==========
        "QSplitter::handle {\n"
        f"    background-color: {C['border']};\n"
        "}\n\n"

        "QSplitter::handle:horizontal {\n"
        "    width: 1px;\n"
        "}\n\n"

        "QSplitter::handle:vertical {\n"
        "    height: 1px;\n"
        "}\n\n"

        # ========== 标签页 ==========
        "QTabWidget::pane {\n"
        f"    border: 1px solid {C['border']};\n"
        "    border-radius: 6px;\n"
        "}\n\n"

        "QTabBar::tab {\n"
        f"    background-color: {C['bg_surface']};\n"
        f"    color: {C['text_dim']};\n"
        f"    border: 1px solid {C['border']};\n"
        "    border-bottom: none;\n"
        "    border-top-left-radius: 6px;\n"
        "    border-top-right-radius: 6px;\n"
        "    padding: 8px 18px;\n"
        "    margin-right: 2px;\n"
        "}\n\n"

        "QTabBar::tab:selected {\n"
        f"    background-color: {C['bg_surface2']};\n"
        f"    color: {C['text']};\n"
        f"    border-bottom: 2px solid {C['accent']};\n"
        "}\n\n"

        "QTabBar::tab:hover:!selected {\n"
        f"    background-color: {C['bg_overlay']};\n"
        f"    color: {C['text']};\n"
        "}\n\n"

        # ========== 自旋框 ==========
        "QSpinBox, QDoubleSpinBox {\n"
        f"    background-color: {C['bg_surface']};\n"
        f"    color: {C['text']};\n"
        f"    border: 1px solid {C['border']};\n"
        "    border-radius: 4px;\n"
        "    padding: 3px 6px;\n"
        "}\n\n"

        "QSpinBox:focus, QDoubleSpinBox:focus {\n"
        f"    border: 1px solid {C['accent']};\n"
        "}\n\n"

        # ========== 工具提示 ==========
        "QToolTip {\n"
        f"    background-color: {C['bg_surface2']};\n"
        f"    color: {C['text']};\n"
        f"    border: 1px solid {C['border_light']};\n"
        "    padding: 5px 10px;\n"
        "    border-radius: 4px;\n"
        "    font-size: 12px;\n"
        "}\n\n"

        # ========== 菜单 ==========
        "QMenuBar {\n"
        f"    background-color: {C['bg_surface']};\n"
        f"    color: {C['text']};\n"
        f"    border-bottom: 1px solid {C['border']};\n"
        "}\n\n"

        "QMenuBar::item {\n"
        "    padding: 6px 12px;\n"
        "    background-color: transparent;\n"
        "}\n\n"

        "QMenuBar::item:selected {\n"
        f"    background-color: {C['bg_overlay']};\n"
        "    border-radius: 4px;\n"
        "}\n\n"

        "QMenu {\n"
        f"    background-color: {C['bg_surface2']};\n"
        f"    color: {C['text']};\n"
        f"    border: 1px solid {C['border']};\n"
        "    border-radius: 6px;\n"
        "    padding: 4px;\n"
        "}\n\n"

        "QMenu::item {\n"
        "    padding: 7px 28px;\n"
        "    border-radius: 4px;\n"
        "}\n\n"

        "QMenu::item:selected {\n"
        f"    background-color: {C['bg_overlay']};\n"
        "}\n\n"

        # ========== 分组框 ==========
        "QGroupBox {\n"
        f"    border: 1px solid {C['border']};\n"
        "    border-radius: 6px;\n"
        "    margin-top: 14px;\n"
        "    padding-top: 14px;\n"
        "}\n\n"

        "QGroupBox::title {\n"
        f"    color: {C['accent']};\n"
        "    subcontrol-origin: margin;\n"
        "    left: 12px;\n"
        "    padding: 0 8px;\n"
        "    font-size: 12px;\n"
        "    font-weight: bold;\n"
        "}\n\n"

        # ========== 表头 ==========
        "QHeaderView::section {\n"
        f"    background-color: {C['bg_surface']};\n"
        f"    color: {C['text_dim']};\n"
        "    padding: 8px;\n"
        "    border: none;\n"
        f"    border-right: 1px solid {C['border']};\n"
        f"    border-bottom: 1px solid {C['border']};\n"
        "}\n\n"

        # ========== ScrollArea ==========
        "QScrollArea {\n"
        "    background: transparent;\n"
        "    border: none;\n"
        "}\n\n"
    )


def apply_theme(widget):
    """应用当前主题到指定控件"""
    widget.setStyleSheet(get_qss())


def toggle_theme():
    """切换深色/浅色主题，返回切换后的主题名"""
    global _current_theme
    _current_theme = "light" if _current_theme == "dark" else "dark"
    return _current_theme


def get_color(name: str) -> str:
    """获取当前主题颜色"""
    C = get_colors()
    return C.get(name, "#000000")


def apply_window_titlebar(window):
    """设置Windows标题栏颜色与当前主题一致"""
    if not hasattr(window, 'winId'):
        return
    try:
        import ctypes
        C = get_colors()
        hwnd = int(window.winId())
        # DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        dark_val = ctypes.c_int(1 if C['win_dark_mode'] else 0)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 20, ctypes.byref(dark_val), ctypes.sizeof(dark_val)
        )
        # DWMWA_CAPTION_COLOR = 35
        caption_color = ctypes.c_int(C['caption_color'])
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 35, ctypes.byref(caption_color), ctypes.sizeof(caption_color)
        )
        # DWMWA_TEXT_COLOR = 36
        text_color = ctypes.c_int(C['caption_text'])
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 36, ctypes.byref(text_color), ctypes.sizeof(text_color)
        )
    except Exception:
        pass
