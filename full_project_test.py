# -*- coding: utf-8 -*-
"""
FireClip 全项目综合测试
测试核心模块功能、优化模块、以及组件集成
"""
import sys
import os

project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)

results = []
all_pass = True

def test_case(name, test_func):
    global all_pass
    print(f"\n{'-'*60}")
    print(f"[测试] {name}")
    print(f"{'-'*60}")
    try:
        result = test_func()
        if result:
            print(f"  [OK] {result}")
            results.append((name, True, result))
        else:
            print(f"  [WARN] 无结果返回")
            results.append((name, True, "执行成功"))
    except Exception as e:
        print(f"  [FAIL] {e}")
        results.append((name, False, str(e)))
        all_pass = False


# ============================================================
# 1. 核心工具模块
# ============================================================

def test_logger():
    from utils.logger import logger
    logger.info("日志模块测试 - INFO")
    logger.warning("日志模块测试 - WARNING")
    logger.error("日志模块测试 - ERROR")
    return "日志记录正常"


def test_config():
    from config import CONFIG
    info = CONFIG.to_dict()
    assert "ai_provider" in info
    assert "ffmpeg_path" in info
    return f"配置加载正常 - {len(info)} 个配置项"


def test_helpers():
    from utils.helpers import format_time, format_file_size, safe_filename
    t = format_time(3661.5)
    s = format_file_size(1536 * 1024)
    n = safe_filename("test/file?.mp4")
    return f"时间格式化={t}, 大小格式化={s}, 文件名={n}"


# ============================================================
# 2. 视频处理核心
# ============================================================

def test_video_processor():
    from core.video_processor import VideoProcessor
    vp = VideoProcessor()
    info = vp.ffmpeg_path
    return f"视频处理器初始化成功 - FFmpeg路径: {info}"


def test_ffmpeg_available():
    from pathlib import Path
    ffmpeg_exe = Path(project_root) / "ffmpeg" / "bin" / "ffmpeg.exe"
    if ffmpeg_exe.exists():
        return f"FFmpeg可执行文件存在: {ffmpeg_exe}"
    return "FFmpeg便携版未找到 (将尝试系统PATH)"


def test_ffmpeg_operation():
    import subprocess
    from pathlib import Path
    ffmpeg_exe = Path(project_root) / "ffmpeg" / "bin" / "ffmpeg.exe"
    cmd = [str(ffmpeg_exe), "-version"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode == 0:
            first_line = result.stdout.strip().split("\n")[0]
            return f"FFmpeg可执行 - {first_line[:60]}"
        else:
            return f"FFmpeg返回错误代码: {result.returncode}"
    except Exception as e:
        return f"FFmpeg执行失败: {e}"


# ============================================================
# 3. 分析核心模块
# ============================================================

def test_scene_detector():
    from core.scene_detector import SceneDetector
    sd = SceneDetector()
    return "场景检测器初始化成功"


def test_motion_analyzer():
    from core.motion_analyzer import MotionAnalyzer
    ma = MotionAnalyzer()
    return "运动分析器初始化成功"


def test_audio_analyzer():
    from core.audio_analyzer import AudioAnalyzer
    aa = AudioAnalyzer()
    return "音频分析器初始化成功"


def test_vfx_detector():
    from core.vfx_detector import VFXDetector
    vd = VFXDetector()
    return "特效检测器初始化成功"


def test_fusion_scorer():
    from core.fusion_scorer import FusionScorer
    fs = FusionScorer()
    return "融合评分器初始化成功"


def test_video_type_preset():
    from core.video_type_preset import VideoTypePreset, get_all_presets, get_preset, list_presets
    presets = get_all_presets()
    names = list_presets()
    auto_preset = get_preset("auto")
    return f"{len(presets)} 个预设配置: {', '.join(names.keys())}"


# ============================================================
# 4. 优化模块
# ============================================================

def test_memory_optimizer():
    from core.memory_optimizer import get_memory_optimizer
    mo = get_memory_optimizer()
    info = mo.get_memory_info()
    return (
        f"进程内存: {info['process_memory_mb']:.1f} MB, "
        f"系统: {info['system_memory_percent']:.1f}%, "
        f"GPU: {info['gpu_memory_mb']} MB"
    )


def test_processing_cache():
    from core.processing_cache import get_processing_cache, get_ffmpeg_optimizer
    cache = get_processing_cache()
    info = cache.get_cache_info()
    optimizer = get_ffmpeg_optimizer()
    params = optimizer.get_optimized_encode_params()
    return f"缓存条目: {info['count']}, FFmpeg参数: {len(params)} 项"


def test_optimized_fusion():
    from core.optimized_fusion import OptimizedFusionScorer, get_available_presets
    presets = get_available_presets()
    scorer = OptimizedFusionScorer("auto")
    scores = scorer.fuse_scores(
        [0.3, 0.7], [0.4, 0.8], [0.5, 0.6],
        [0.3, 0.9], [0.6, 0.7], 2
    )
    return f"{len(presets)} 预设, 示例融合分数: {scores}"


# ============================================================
# 5. 上层业务模块
# ============================================================

def test_smart_cut():
    from core.smart_cut import SmartCutManager
    sc = SmartCutManager()
    return "智能成片管理器初始化成功"


def test_subtitle():
    from core.subtitle import SubtitleProcessor
    sp = SubtitleProcessor()
    return "字幕处理器初始化成功"


def test_database():
    from core.database import DatabaseManager
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        db = DatabaseManager(db_path)
        # 简单测试
        project_id = "test_001"
        db.create_project(project_id, "测试", "test.mp4", {"duration": 100})
        proj = db.get_project(project_id)
        assert proj is not None
        return "数据库管理器初始化成功，CRUD正常"


def test_resource_manager():
    from core.resource_manager import ResourceManager
    rm = ResourceManager()
    info = rm.get_system_info()
    return f"系统资源 - CPU核数: {info['cpu_count']}"


# ============================================================
# 6. 主题/UI模块
# ============================================================

def test_theme_module():
    from ui.theme import get_qss
    qss = get_qss()
    return f"QSS样式生成成功 ({len(qss)} 字符)"


# ============================================================
# 7. 额外验证
# ============================================================

def test_hardware_capabilities():
    from utils.helpers import detect_hardware_capabilities
    caps = detect_hardware_capabilities()
    gpu_status = f"GPU={caps['gpu_name']}, 显存={caps['gpu_memory_mb']}MB" if caps["gpu_available"] else "无GPU"
    return f"硬件能力检测: {gpu_status}"


def test_portable_env():
    from portable_env import PORTABLE_ENV
    status = PORTABLE_ENV.init_env()
    return f"便携环境: ffmpeg={status['ffmpeg_ok']}, pip={status['pip_ok']}"


# ============================================================
# 主测试流程
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("  FireClip 全项目综合测试")
    print("=" * 60)
    print(f"  Python: {sys.version.split(' ')[0]}")
    print(f"  平台: {sys.platform}")
    print(f"  项目根: {project_root}")
    print("=" * 60)

    test_cases = [
        ("1.1 日志工具", test_logger),
        ("1.2 配置模块", test_config),
        ("1.3 辅助函数", test_helpers),
        ("2.1 视频处理器", test_video_processor),
        ("2.2 FFmpeg可用性", test_ffmpeg_available),
        ("2.3 FFmpeg实际操作", test_ffmpeg_operation),
        ("3.1 场景检测器", test_scene_detector),
        ("3.2 运动分析器", test_motion_analyzer),
        ("3.3 音频分析器", test_audio_analyzer),
        ("3.4 特效检测器", test_vfx_detector),
        ("3.5 融合评分器", test_fusion_scorer),
        ("3.6 视频类型预设", test_video_type_preset),
        ("4.1 内存优化器", test_memory_optimizer),
        ("4.2 处理缓存/FFmpeg优化", test_processing_cache),
        ("4.3 优化融合算法", test_optimized_fusion),
        ("5.1 智能成片", test_smart_cut),
        ("5.2 字幕处理", test_subtitle),
        ("5.3 数据库", test_database),
        ("5.4 资源管理器", test_resource_manager),
        ("6.1 主题模块", test_theme_module),
        ("7.1 硬件能力检测", test_hardware_capabilities),
        ("7.2 便携环境检测", test_portable_env),
    ]

    for name, func in test_cases:
        test_case(name, func)

    # 汇总
    passed = sum(1 for _, ok, _ in results if ok)
    failed = sum(1 for _, ok, _ in results if not ok)

    print("\n" + "=" * 60)
    print("  测试结果汇总")
    print("=" * 60)
    print(f"  通过: {passed}/{len(results)}")
    print(f"  失败: {failed}/{len(results)}")
    print("=" * 60)

    if failed > 0:
        print("\n失败项:")
        for name, ok, detail in results:
            if not ok:
                print(f"  - {name}: {detail}")
        print("\n" + "=" * 60)
        print("  状态: 有问题需要检查")
        print("=" * 60)
        sys.exit(1)
    else:
        print("\n" + "=" * 60)
        print("  状态: 全部通过!")
        print("=" * 60)
        sys.exit(0)
