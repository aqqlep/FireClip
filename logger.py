"""日志统一配置模块

基于 loguru 实现分级日志、按天轮转、分类存储。
"""
import sys
from pathlib import Path
from loguru import logger


_configured = False


def setup_logger(log_dir: str = "./logs", level: str = "INFO",
                 rotation: str = "1 day", retention: str = "7 days"):
    """初始化全局日志配置，仅生效一次。

    Args:
        log_dir: 日志目录
        level: 日志级别 DEBUG/INFO/WARNING/ERROR/CRITICAL
        rotation: 轮转策略
        retention: 保留时长
    """
    global _configured
    if _configured:
        return

    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    # 移除默认 handler
    logger.remove()

    # 控制台输出（彩色）
    logger.add(
        sys.stderr,
        level=level,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | "
               "<cyan>{name}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        colorize=True,
    )

    # 分类日志文件
    categories = {
        "ai_infer": "AI 推理日志",
        "hardware": "硬件监控日志",
        "task": "任务执行日志",
        "error": "错误日志",
    }
    for cat, _desc in categories.items():
        logger.add(
            log_path / f"{cat}.log",
            level=level,
            rotation=rotation,
            retention=retention,
            encoding="utf-8",
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{line} | {message}",
            filter=lambda record, c=cat: record["extra"].get("category") == c,
        )

    # 全量日志
    logger.add(
        log_path / "all.log",
        level=level,
        rotation=rotation,
        retention=retention,
        encoding="utf-8",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{line} | {message}",
    )

    _configured = True
    logger.info("日志系统初始化完成", extra={"category": "task"})


def get_logger(category: str = ""):
    """获取带分类标签的 logger 实例。

    Args:
        category: 日志分类 ai_infer/hardware/task/error
    """
    if category:
        return logger.bind(category=category)
    return logger
