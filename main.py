"""
FireClip - 影视高燃动作剪辑软件
主程序入口
"""
import sys
import os
from pathlib import Path

# 在导入 PyQt6 之前设置环境变量，避免与便携式 FFmpeg 冲突
# 使用 Windows Media Foundation 作为 Qt Multimedia 后端
os.environ["QT_MEDIA_BACKEND"] = "windows"

# 添加项目根目录到路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from PyQt6.QtWidgets import QApplication, QSplashScreen, QMessageBox
from PyQt6.QtGui import QPixmap, QFont, QIcon
from PyQt6.QtCore import Qt, QTimer

from ui.main_window import MainWindow
from ui.theme import apply_theme, set_theme, apply_window_titlebar
from utils.logger import logger
from config import CONFIG
from portable_env import PORTABLE_ENV


def init_portable_environment():
    """初始化便携式环境"""
    logger.info("正在检测便携式环境...")
    
    # 初始化环境状态
    status = PORTABLE_ENV.init_env()
    
    for msg in status["messages"]:
        logger.info(msg)
    
    # 将便携式环境路径应用到配置
    PORTABLE_ENV.apply_to_config()
    
    # 如果配置中 ffmpeg_path 为空，使用自动检测的路径
    if not CONFIG.ffmpeg_path:
        ffmpeg_dir = PORTABLE_ENV.get_ffmpeg_path()
        if ffmpeg_dir != "ffmpeg":
            CONFIG.ffmpeg_path = str(Path(ffmpeg_dir) / "ffmpeg.exe")
            logger.info(f"自动检测 FFmpeg: {CONFIG.ffmpeg_path}")
        else:
            CONFIG.ffmpeg_path = "ffmpeg"
            logger.info("使用系统 PATH 中的 FFmpeg")
    
    # 检查关键组件
    if not status["ffmpeg_ok"]:
        logger.warning("FFmpeg 未找到，部分功能可能无法使用")
    
    if not status["pip_ok"]:
        logger.warning("pip 不可用，如需安装依赖请运行 setup.bat")
    
    return status


def check_single_instance():
    """单实例检测 — 文件锁 + 命名互斥体双重保障
    方案1: 文件排他锁（最可靠，进程退出自动释放）
    方案2: Windows 命名互斥体（补充保障）
    """
    if sys.platform != 'win32':
        return True

    # --- 方案1: 文件锁（核心方案，最可靠） ---
    try:
        import msvcrt
        lock_path = str(project_root / ".fireclip.lock")
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
        # 写入当前 PID 用于诊断
        os.write(lock_fd, f"PID={os.getpid()}\n".encode())
        os.lseek(lock_fd, 0, os.SEEK_SET)
        # 非阻塞排他锁 — 如果另一个进程已持有，立即抛出 OSError
        msvcrt.locking(lock_fd, msvcrt.LK_NBLCK, 1)
        global _single_instance_lock_fd
        _single_instance_lock_fd = lock_fd
        logger.info(f"单实例文件锁获取成功 (PID={os.getpid()})")
    except (OSError, IOError):
        logger.warning("FireClip 已经在运行中（文件锁冲突），不再重复启动")
        return False

    # --- 方案2: 命名互斥体（补充保障） ---
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        # 必须声明函数签名，否则 64 位系统句柄被截断
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CreateMutexW.argtypes = [
            wintypes.LPVOID,   # lpMutexAttributes
            wintypes.BOOL,     # bInitialOwner
            wintypes.LPCWSTR   # lpName
        ]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

        mutex_name = "FireClip_SingleInstance_Mutex"
        mutex = kernel32.CreateMutexW(None, True, mutex_name)
        err = ctypes.get_last_error()
        if err == 183:  # ERROR_ALREADY_EXISTS
            if mutex:
                kernel32.CloseHandle(mutex)
            logger.warning("FireClip 已经在运行中（互斥体冲突），不再重复启动")
            return False
        if not mutex:
            logger.warning("互斥体创建失败，仅靠文件锁保障")
            return True
        global _single_instance_mutex
        _single_instance_mutex = mutex
        logger.info("单实例互斥体创建成功")
    except Exception as e:
        logger.warning(f"互斥体检测失败(文件锁仍生效): {e}")

    return True


_single_instance_lock_fd = None
_single_instance_mutex = None


def check_dependencies():
    """检查依赖"""
    missing = []
    
    try:
        import PyQt6
    except ImportError:
        missing.append("PyQt6")
    
    try:
        import cv2
    except ImportError:
        missing.append("opencv-python")
    
    try:
        import numpy
    except ImportError:
        missing.append("numpy")
    
    if missing:
        logger.error(f"缺少依赖: {', '.join(missing)}")
        return False
    
    return True


def main():
    """主函数"""
    # 初始化便携式环境
    env_status = init_portable_environment()
    
    # 单实例检测
    if not check_single_instance():
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            0,
            "FireClip 已经在运行中！\n\n请检查任务栏或系统托盘。",
            "FireClip - 已在运行",
            0x40  # MB_ICONINFORMATION
        )
        sys.exit(0)
    
    # 设置 Qt Multimedia 使用 Windows Media Foundation
    os.environ["QT_MEDIA_BACKEND"] = "windows"
    
    # 检查依赖
    if not check_dependencies():
        logger.error("缺少必要的依赖包")
        logger.error("请运行 setup.bat 或执行: pip install -r requirements.txt")
        sys.exit(1)
    
    # 初始化主题
    theme = getattr(CONFIG, 'theme', 'dark')
    set_theme(theme)
    logger.info(f"主题: {theme}")
    
    # 创建应用
    app = QApplication(sys.argv)
    
    # 设置应用信息
    app.setApplicationName("FireClip")
    app.setApplicationVersion("1.0.0")
    app.setOrganizationName("FireClip")
    
    # 设置应用图标（任务栏+窗口标题栏）
    icon_path = str(project_root / "assets" / "clip.png")
    if os.path.exists(icon_path):
        app_icon = QIcon(icon_path)
        app.setWindowIcon(app_icon)
        logger.info(f"Application icon loaded: {icon_path}")
    
    # Windows 任务栏图标：设置 AppUserModelID 避免显示 Python 默认图标
    if sys.platform == 'win32':
        import ctypes
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('FireClip.App')
        except Exception:
            pass
    
    # 应用主题
    apply_theme(app)
    
    # 设置字体
    font = QFont("Microsoft YaHei", 10)
    app.setFont(font)
    
    # 创建主窗口
    window = MainWindow()
    
    # Windows 标题栏与主题联动
    apply_window_titlebar(window)
    
    # 显示窗口
    window.show()
    
    logger.info("FireClip 启动成功")
    
    # 全局异常捕获（防止闪退时无法定位问题）
    def exception_hook(exc_type, exc_value, exc_tb):
        import traceback
        tb_str = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        logger.error(f"未捕获的异常:\n{tb_str}")
        # 写入崩溃日志
        try:
            crash_path = os.path.join(str(project_root), "logs", "crash.log")
            with open(crash_path, "a", encoding="utf-8") as f:
                import datetime
                f.write(f"\n{'='*60}\n{datetime.datetime.now()}\n{tb_str}\n")
        except:
            pass
        QMessageBox.critical(None, "崩溃", f"FireClip 遇到错误:\n{exc_value}\n\n详细信息已保存到 logs/crash.log")
        sys.__excepthook__(exc_type, exc_value, exc_tb)
    
    sys.excepthook = exception_hook
    
    # 运行应用
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
