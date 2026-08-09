"""
FireClip - 影视高燃动作剪辑软件 全局配置（三阶段筛选架构 v2.5）

版本演进:
v2.0: 初始三阶段架构
v2.1: SAD运动分析 + 动态百分位阈值 + 快切镜头组 + 多特征融合
v2.2: 颜色饱和度/亮度分析 + 音频频谱分层 + 多窗口动态阈值
v2.3: 多线程流水线 + 增量分析 + ffmpeg参数极致调优 + 批量并行提取
v2.4: 资源控制（内存监控 + 动态采样率 + CPU节流 + GPU显存池
v2.5: 鲁棒性加固（错误恢复 + 降级策略 + 异常格式处理 + 自动重试）
"""
from dataclasses import dataclass, field
from typing import Optional, Dict, List
import json
import os
from pathlib import Path


@dataclass
class LocalModelsConfig:
    """本地AI模型配置"""
    vision_model: str = "models_cache/Qwen/Qwen2-VL-7B-Instruct"
    text_model: str = "models_cache/Qwen/Qwen2___5-7B-Instruct"
    whisper_model: str = "models_cache/openai-mirror/whisper-large-v3"
    tts_model: str = "models_cache/pengzhendong/ChatTTS"
    quantize: bool = True                # 模型量化（v2.1修复字段名，之前是lightweight_quantize）
    vision_model_int8: bool = True       # 视觉模型INT8量化）


@dataclass
class APIFallbackConfig:
    """API回退配置"""
    enabled: bool = False
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"
    claude_api_key: str = ""
    claude_model: str = "claude-sonnet-4-20250514"


@dataclass
class PipelineConfig:
    """统一视频分析管道配置（v2.3 - 多线程流水线 + 增量分析 + ffmpeg参数极致调优）"""
    
    # ============== 基础抽帧参数 ==============
    base_frame_rate: float = 1.0           # 基础抽帧率（默认1fps，L1/L2分析用）
    high_motion_frame_rate: float = 3.0    # 高运动区域加强抽帧率（3fps，仅对候选区）
    analysis_resolution: str = "720p"     # 分析用分辨率（720p）
    analysis_width: int = 960              # 分析宽度（720p对应的16:9）
    analysis_height: int = 540              # 分析高度
    sad_downsample: int = 4                # SAD运动分析用下采样系数（进一步降分辨率计算）
    enable_hw_decode: bool = True              # 启用硬件解码（自动：cuda/qsv/vaapi）
    hw_priority_cuda: int = 3
    hw_priority_qsv: int = 2
    hw_priority_vaapi: int = 1
    
    # ============== v2.3 新增：多线程流水线参数 ==============
    enable_multithread_pipeline: bool = True   # 启用多线程流水线（分析+筛选+提取并行）
    pipeline_threads_decode: int = 2          # 解码线程数（2线程）
    pipeline_threads_feature: int = 2          # 特征提取线程数（2线程）
    pipeline_threads_filter: int = 2           # 筛选线程数（2线程）
    pipeline_threads_extract: int = 2          # 提取线程数（并行处理多个片段，2线程）
    pipeline_queue_size: int = 50             # 流水线队列大小
    enable_pipeline_overlap: bool = True       # 启用阶段重叠（边分析边筛选）
    
    # ============== v2.3 新增：增量分析参数 ==============
    enable_incremental_analysis: bool = True   # 启用增量分析（分段处理大视频）
    incremental_chunk_sec: int = 120             # 每个增量块大小(秒)，默认2分钟（分块处理长视频）
    incremental_overlap_sec: float = 5.0        # 块间重叠秒数，避免边界丢失
    incremental_cache_results: bool = True      # 缓存已处理块结果（避免重复计算）
    
    # ============== v2.3 新增：ffmpeg参数极致调优 ==============
    ffmpeg_threads: int = 0                     # ffmpeg线程数（0=自动匹配CPU核心数）
    ffmpeg_nostdin: bool = True                 # 禁用标准输入交互
    ffmpeg_loglevel: str = "error"              # ffmpeg日志级别
    ffmpeg_enable_lowlatency: bool = False      # 低延迟模式（速度优先，牺牲缓存）
    ffmpeg_readrate: float = 0.0                # 读取速度限制（0=不限制，>1=倍速读取）
    ffmpeg_probesize: int = 10 * 1024 * 1024   # 探测大小（10MB，加快启动速度）
    ffmpeg_analyzeduration: int = 10 * 1000000  # 分析时长（10秒，加快启动速度）
    ffmpeg_noaccurate_seek: bool = True         # 禁用精确seek（更快但可能有误差，适合快速预览）
    ffmpeg_copyts: bool = True                  # 拷贝时间戳（码流拷贝时使用，保持原始时间线）
    ffmpeg_avoid_negative_ts: str = "make_zero" # 负时间戳处理策略（make_zero=归零处理）
    
    # ============== v2.3 新增：批量并行提取参数 ==============
    enable_parallel_extract: bool = True        # 启用并行片段提取（多片段同时导出）
    parallel_extract_max_workers: int = 3        # 最大并行提取数（避免IO瓶颈，3个线程）
    parallel_extract_min_segments: int = 2      # 少于此数不并行（串行更快，仅1个片段时不启动并行）
    extract_buffer_size: int = 1024 * 1024       # 提取缓冲区大小（1MB，每次读取的块大小）

    # ============== L1 镜头时长检测阈值 ==============
    l1_scene_threshold: float = 0.4             # FFmpeg scene 滤镜阈值（自适应动态调整，0.2-0.6）
    l1_scene_threshold_min: float = 0.2        # 自适应最小scene阈值（低于此值不再降低）
    l1_scene_threshold_max: float = 0.6       # 自适应最大scene阈值（高于此值不再升高）
    l1_min_shot_duration: float = 0.5           # 最小镜头时长（秒，避免误切短片段）
    l1_max_shot_duration: float = 60.0            # 最大镜头时长（秒，超过则强制分割）
    l1_min_scene_gap: float = 0.3             # 场景切换合并时间间隔（秒，小于此间隔的切点合并）
    
    # ============== L2 光流/运动强度检测阈值（SAD + 多特征融合 ==============
    l2_motion_threshold: float = 15.0            # 运动强度绝对阈值（SAD归一化值，>15认为高运动）
    l2_motion_percentile: float = 75.0          # 运动强度动态百分位（取75%分位作为参考基线）
    l2_motion_window: float = 3.0             # 运动窗口（秒，滑动窗口计算运动均值）
    l2_min_high_motion_duration: float = 1.5             # 连续高运动时长（秒，至少持续1.5秒才算）
    l2_audio_energy_weight: float = 0.30         # 音频能量权重（v2.2 调优，在融合评分中的占比）
    l2_min_energy_threshold: float = 0.6             # 音频能量阈值（归一化0-1，>0.6认为音频活跃）
    l2_composite_threshold: float = 0.52            # v4.0 提高综合评分阈值（融合分>0.52认为候选片段，减少误判）
    l2_color_variation_weight: float = 0.20       # 颜色变化权重（v2.1，动作场景颜色变化丰富）
    l2_shot_frequency_weight: float = 0.35        # 镜头切换频率权重（v2.1，快切镜头指示动作场景）
    l2_fast_cut_fps_threshold: float = 0.5         # 快切镜头判定：每秒>0.5次切换即为快切
    
    # ============== L2 v2.2 新增：颜色/亮度/对比度特征 ==============
    l2_brightness_std_weight: float = 0.15       # 亮度标准差权重（动作片场景变化大
    l2_saturation_weight: float = 0.10           # 颜色饱和度权重（动作片更高饱和
    l2_contrast_weight: float = 0.10             # 对比度权重（动作片强对比
    l2_brightness_std_threshold: float = 25.0      # 亮度波动阈值（越大越可能动作
    l2_saturation_min: float = 40.0            # 最小饱和度（经验值
    l2_min_color_window: float = 5.0             # 颜色特征分析窗口
    
    # ============== L2 v2.2 新增：音频频谱分层识别 ==============
    l2_audio_low_freq_weight: float = 0.40           # 低频能量权重（爆炸/冲击 - 动作片特征
    l2_audio_mid_freq_weight: float = 0.25           # 中频能量权重（对话/人声
    l2_audio_high_freq_weight: float = 0.35           # 高频能量权重（音乐/特效
    l2_audio_low_freq_threshold: float = 0.55         # 低频能量阈值（>此值更可能动作
    l2_audio_spectrum_window: float = 2.0            # 音频频谱分析窗口
    l2_audio_band_ratio_threshold: float = 1.2           # 低频/中频比阈值（>此值是动作片音频
    l2_audio_volume_weight: float = 0.30               # 音量大小权重
    
    # ============== L2 v2.2 新增：多窗口动态阈值（提高精度 ==============
    l2_short_window: float = 5.0           # 短窗口(5秒) - 捕捉快速动作
    l2_mid_window: float = 15.0            # 中窗口(15秒) - 场景级别
    l2_long_window: float = 30.0           # 长窗口(30秒) - 全局背景
    
    # ============== v4.0 新增：CLIP 场景分类验证 ==============
    enable_clip_verification: bool = False   # 是否启用 CLIP 轻量分类（Qwen2-VL 启用时此项不生效）
    clip_device: str = "cuda"                # 推理设备 cuda / cpu
    clip_confidence_threshold: float = 0.45  # 排除低价值场景的置信度阈值（0-1）
    clip_low_value_scenes: str = "talking_dialogue,static_landscape,credits_text"  # 需要排除的场景

    # ============== v4.0 新增：Qwen2-VL 高精度场景分类（方案B） ==============
    enable_qwen2vl_verification: bool = True   # 是否启用 Qwen2-VL 7B 多帧视觉分类（主分类器）
    qwen2vl_model_path: str = ""               # 模型路径（留空则自动查找 models_cache/Qwen/Qwen2-VL-7B-Instruct）
    qwen2vl_frames_per_segment: int = 4        # 每个片段分析的帧数（3-5，越多越准但越慢）

    # ============== v4.0 新增：Whisper 语音转文字（辅助搞笑/情感场景识别） ==============
    enable_whisper_asr: bool = True            # 是否启用 Whisper 语音识别（辅助判断台词中的搞笑/情感）
    whisper_model_path: str = ""               # 模型路径（留空则自动查找）
    whisper_min_duration: float = 2.0          # 至少这个时长的片段才跑 ASR（太短没内容）
    l2_window_weight_short: float = 0.45             # 短窗口权重
    l2_window_weight_mid: float = 0.35             # 中窗口权重
    l2_window_weight_long: float = 0.20           # 长窗口权重
    l2_window_ratio_threshold: float = 1.3           # 窗口间对比阈值（短窗口/长窗口 > 此值则有动作
    
    # ============== L3 轻量化AI模型校验阈值 ==============
    l3_enable: bool = True                      # 是否启用L3
    l3_candidate_ratio: float = 0.35            # L2通过率（仅对这部分帧跑AI
    l3_min_confidence: float = 0.6             # AI最小置信度
    l3_sparse_inference: bool = True             # 稀疏推理
    l3_frame_sample_interval: float = 2.0             # L3内部采样间隔
    l3_model_int8: bool = True              # INT8量化
    l3_fallback_to_rules: bool = True          # AI不可用时自动降级为规则校验
    
    # ============== 片段合并与评分 ==============
    merge_gap_threshold: float = 4.0             # v3.2: 小于此间隔的相邻高燃段合并（从2.0→4.0）
    hot_min_duration: float = 5.0             # v3.2: 最小高燃时长（从3.0→5.0秒，过滤短片段）
    hot_max_duration: float = 30.0            # 最大高燃时长
    hot_top_n: int = 10

    # ============== 资源限制（v2.1更精细 ==============
    max_memory_limit_mb: int = 1500              # 内存峰值上限 1.5GB
    max_gpu_memory_mb: int = 500               # 显存上限 500MB
    max_cpu_percent_target: int = 25                # CPU平均占用控制在25%以内
    max_workers_decode: int = 2                 # 解码线程数
    max_threads_filter: int = 2                 # 筛选线程数
    enable_memory_pressure_control: bool = True  # 内存压力控制（动态降采样
    enable_cpu_throttling: bool = True           # CPU节流（高负载时自动休眠
    cpu_throttle_threshold_percent: int = 40     # CPU节流触发阈值
    frame_buffer_max_frames: int = 30            # 流式缓冲最大帧数（避免内存爆炸
    
    # ============== v2.4 新增：资源控制参数（内存 + CPU + GPU ==============
    enable_resource_control: bool = True        # 启用资源控制
    resource_check_interval_sec: float = 5.0    # 资源检查间隔(秒)
    
    # --- 内存控制（精细化三级阈值） ---
    memory_limit_mb: int = 1024                 # 内存上限 (MB)，默认1GB
    memory_graceful_mb: int = 800               # 内存优雅降级阈值 (MB)
    memory_critical_mb: int = 950               # 内存危急阈值 (MB) - 触发采样率调整
    memory_enable_throttle: bool = True         # 内存超限时启用节流
    
    # --- CPU控制（精细化三级阈值） ---
    cpu_limit_percent: float = 40.0             # CPU使用率上限 (%) - 超过即暂停
    cpu_graceful_percent: float = 20.0          # CPU优雅阈值 (%) - 记录警告
    cpu_critical_percent: float = 30.0          # CPU危急阈值 (%) - 触发适度降速
    cpu_throttle_ms: int = 100                  # CPU节流时长 (毫秒)
    cpu_cores_limit: int = 2                    # 最多使用CPU核心数
    
    # --- GPU/显存控制（精细化三级阈值） ---
    gpu_memory_limit_mb: int = 500              # GPU显存上限 (MB)
    gpu_graceful_mb: int = 400                  # GPU优雅阈值 (MB)
    gpu_critical_mb: int = 480                  # GPU危急阈值 (MB)
    gpu_disable_on_critical: bool = True        # GPU危急时禁用硬件加速回退到CPU解码
    
    # --- 动态采样率控制（v2.4核心改进） ---
    enable_dynamic_sampling: bool = True         # 启用动态采样率
    base_sample_interval_sec: float = 1.0        # 基础采样间隔(秒)，1fps
    min_sample_interval_sec: float = 0.33        # 最小采样间隔(秒)，3fps（运动激烈区
    max_sample_interval_sec: float = 3.0         # 最大采样间隔(秒)，0.33fps（资源紧张时
    motion_sample_trigger: float = 30.0          # 运动强度触发高采样的阈值
    sample_adjust_step: float = 0.25             # 采样率调整步长(秒)
    enable_motion_adaptive_sampling: bool = True # 运动自适应采样
    motion_high_fps_interval: float = 0.5        # 高运动区域采样间隔(秒)
    
    # --- 帧缓存管理（v2.4内存优化） ---
    max_frames_in_memory: int = 30             # 最大内存帧数（超过即丢弃旧帧
    frame_cache_enable_compression: bool = False  # 帧缓存启用压缩（省内存但慢
    enable_frame_pruning: bool = True            # 启用帧修剪（低运动帧不缓存
    motion_pruning_threshold: float = 10.0       # 运动强度修剪阈值（低于此值不缓存帧
    
    # --- 资源日志与监控 ---
    enable_resource_logging: bool = False         # 启用资源使用日志
    resource_log_interval_sec: float = 10.0      # 资源日志间隔(秒)
    enable_resource_stats: bool = True           # 记录资源使用统计数据
    
    # ============== v3.0 新增：用户资源调控（设置面板可调） ==============
    # 这是用户最关心的功能——控制软件对电脑资源的占用
    # 三档模式 + 精细参数
    
    # --- 全局资源模式 ---
    resource_mode: str = "balanced"              # "economy"省电 / "balanced"均衡 / "performance"性能
    
    # --- CPU 占用上限（用户最关心） ---
    cpu_max_percent: int = 50                   # CPU占用上限(%)，超过此值自动节流
    cpu_target_percent: int = 30                # CPU目标占用(%)，正常运行时控制在此值
    cpu_check_interval_ms: int = 500            # CPU检查间隔(毫秒)
    cpu_throttle_aggressive: bool = False        # 激进节流模式（更频繁休眠但更稳定）
    
    # --- 内存占用上限 ---
    ram_max_percent: int = 60                   # 系统内存占用上限(%)，超过此值降级
    ram_target_percent: int = 40                # 系统内存目标占用(%)
    
    # --- GPU 显存上限 ---
    gpu_vram_max_percent: int = 50              # GPU显存占用上限(%)，超过此值释放模型
    
    # --- 分析并行度控制 ---
    max_ffmpeg_processes: int = 2               # 最多同时运行的ffmpeg进程数
    max_analysis_threads: int = 2               # 最多分析线程数
    
    # --- 后台运行模式（用户切走时自动降速） ---
    auto_reduce_when_idle: bool = True          # 窗口失焦时自动降低CPU占用
    idle_cpu_target_percent: int = 15           # 后台时的CPU目标占用(%)

    # ============== v2.5 新增：错误恢复与降级策略 ==============
    enable_error_recovery: bool = True            # 启用错误恢复机制
    max_retry_count: int = 3                       # 最大重试次数
    retry_delay_sec: float = 2.0                   # 重试延迟(秒)
    error_log_path: str = "error_logs/"            # 错误日志路径
    enable_fallback_mode: bool = True              # 启用降级模式
    fallback_analysis_resolution: str = "480p"    # 降级分辨率
    fallback_frame_rate: float = 0.5               # 降级帧率(0.5fps)
    skip_corrupted_frames: bool = True             # 跳过损坏帧
    min_valid_frames_ratio: float = 0.7             # 最小有效帧比例(低于此值放弃片段
    enable_format_detection: bool = True            # 启用异常格式自动检测
    unsupported_format_fallback: bool = True       # 不支持格式自动降级处理
    max_analysis_timeout_sec: int = 600             # 分析超时(秒)，10分钟
    enable_safe_mode: bool = False                  # 安全模式（禁用硬件加速，最慢但最稳


@dataclass
class ExportConfig:
    """导出配置 - 码流直接拷贝优先"""
    use_stream_copy: bool = True              # 码流直接拷贝（不重编码，速度最快
    force_reencode: bool = False             # 强制重编码（慢但兼容性好
    copy_video_codec: str = "copy"                # 视频编码（copy表示直接拷贝
    copy_audio_codec: str = "copy"                # 音频编码
    reencode_video_codec: str = "libx264"          # 重编码视频编码
    reencode_preset: str = "veryfast"           # 重编码预设
    reencode_crf: int = 20                  # 重编码CRF质量


@dataclass
class Config:
    """全局配置 (v2.5)"""
    # 版本标识
    app_version: str = "v3.0"  # v3.0 - 资源调控 + 双主题 + 单实例
    
    # 主题
    theme: str = "dark"  # dark / light
    
    # AI提供商
    ai_provider: str = "local"  # local / openai / claude
    local_models: LocalModelsConfig = field(default_factory=LocalModelsConfig)
    api_fallback: APIFallbackConfig = field(default_factory=APIFallbackConfig)
    
    # API密钥（顶层属性，便于访问）
    openai_api_key: str = ""
    claude_api_key: str = ""
    
    # FFmpeg配置（支持相对路径，自动检测便携式FFmpeg）
    ffmpeg_path: str = ""  # 空字符串表示自动检测
    ffprobe_path: str = ""  # 空字符串表示自动检测
    hw_accel: str = "auto"  # auto / cuda / qsv / vaapi / none
    
    # 输出配置
    output_dir: str = "./output"
    cache_dir: str = "./cache"
    default_format: str = "mp4"
    default_resolution: str = "1080p"
    default_codec: str = "h264"
    
    # 分析管道（v2.0三阶段筛选架构）
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)
    
    # 导出配置（码流直接拷贝优先）
    export: ExportConfig = field(default_factory=ExportConfig)
    
    # TTS配置
    tts_engine: str = "ChatTTS"  # ChatTTS / CosyVoice / edge-tts
    tts_voice_male: str = "zh-CN-YunxiNeural"
    tts_voice_female: str = "zh-CN-XiaoxiaoNeural"
    
    # 兼容旧属性（向后兼容，内部会逐步迁移到 pipeline.*）
    scene_threshold: float = 0.3
    energy_threshold_percentile: float = 85
    detection_preset: str = "auto"  # auto / real_action / xianxia_fantasy / anime_cartoon / modern_urban
    detection_top_n: int = 10
    detection_min_duration: float = 5.0  # v3.2: 最小5秒（从3.0→5.0）
    detection_max_duration: float = 30.0
    enable_ai_vision_channel: bool = True
    ai_vision_interval: float = 2.0
    progressive_analysis: bool = True
    
    # 资源管理
    proxy_enabled: bool = True
    proxy_resolution: str = "480p"
    max_batch_files: int = 100
    max_cache_size_gb: float = 50.0
    memory_limit_mb: int = 4000  # 全局应用内存上限(MB)，pipeline内部用pipeline.memory_limit_mb
    
    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "app_version": self.app_version,
            "theme": self.theme,
            "ai_provider": self.ai_provider,
            "local_models": {
                "vision_model": self.local_models.vision_model,
                "text_model": self.local_models.text_model,
                "whisper_model": self.local_models.whisper_model,
                "tts_model": self.local_models.tts_model,
                "quantize": self.local_models.quantize,
                "vision_model_int8": self.local_models.vision_model_int8,
            },
            "api_fallback": {
                "enabled": self.api_fallback.enabled,
                "openai_api_key": self.api_fallback.openai_api_key,
                "openai_model": self.api_fallback.openai_model,
                "claude_api_key": self.api_fallback.claude_api_key,
                "claude_model": self.api_fallback.claude_model,
            },
            "openai_api_key": self.openai_api_key,
            "claude_api_key": self.claude_api_key,
            "ffmpeg_path": self.ffmpeg_path,
            "ffprobe_path": self.ffprobe_path,
            "hw_accel": self.hw_accel,
            "output_dir": self.output_dir,
            "cache_dir": self.cache_dir,
            "default_format": self.default_format,
            "default_resolution": self.default_resolution,
            "default_codec": self.default_codec,
            "pipeline": {
                "base_frame_rate": self.pipeline.base_frame_rate,
                "high_motion_frame_rate": self.pipeline.high_motion_frame_rate,
                "analysis_resolution": self.pipeline.analysis_resolution,
                "analysis_width": self.pipeline.analysis_width,
                "analysis_height": self.pipeline.analysis_height,
                "sad_downsample": self.pipeline.sad_downsample,
                "enable_hw_decode": self.pipeline.enable_hw_decode,
                # v2.3 新增：多线程流水线
                "enable_multithread_pipeline": self.pipeline.enable_multithread_pipeline,
                "pipeline_threads_decode": self.pipeline.pipeline_threads_decode,
                "pipeline_threads_feature": self.pipeline.pipeline_threads_feature,
                "pipeline_threads_filter": self.pipeline.pipeline_threads_filter,
                "pipeline_threads_extract": self.pipeline.pipeline_threads_extract,
                "pipeline_queue_size": self.pipeline.pipeline_queue_size,
                "enable_pipeline_overlap": self.pipeline.enable_pipeline_overlap,
                # v2.3 新增：增量分析
                "enable_incremental_analysis": self.pipeline.enable_incremental_analysis,
                "incremental_chunk_sec": self.pipeline.incremental_chunk_sec,
                "incremental_overlap_sec": self.pipeline.incremental_overlap_sec,
                "incremental_cache_results": self.pipeline.incremental_cache_results,
                # v2.3 新增：ffmpeg参数
                "ffmpeg_threads": self.pipeline.ffmpeg_threads,
                "ffmpeg_nostdin": self.pipeline.ffmpeg_nostdin,
                "ffmpeg_loglevel": self.pipeline.ffmpeg_loglevel,
                "ffmpeg_enable_lowlatency": self.pipeline.ffmpeg_enable_lowlatency,
                "ffmpeg_readrate": self.pipeline.ffmpeg_readrate,
                "ffmpeg_probesize": self.pipeline.ffmpeg_probesize,
                "ffmpeg_analyzeduration": self.pipeline.ffmpeg_analyzeduration,
                "ffmpeg_noaccurate_seek": self.pipeline.ffmpeg_noaccurate_seek,
                "ffmpeg_copyts": self.pipeline.ffmpeg_copyts,
                "ffmpeg_avoid_negative_ts": self.pipeline.ffmpeg_avoid_negative_ts,
                # v2.3 新增：并行提取
                "enable_parallel_extract": self.pipeline.enable_parallel_extract,
                "parallel_extract_max_workers": self.pipeline.parallel_extract_max_workers,
                "parallel_extract_min_segments": self.pipeline.parallel_extract_min_segments,
                "extract_buffer_size": self.pipeline.extract_buffer_size,
                "l1_scene_threshold": self.pipeline.l1_scene_threshold,
                "l1_scene_threshold_min": self.pipeline.l1_scene_threshold_min,
                "l1_scene_threshold_max": self.pipeline.l1_scene_threshold_max,
                "l1_min_shot_duration": self.pipeline.l1_min_shot_duration,
                "l1_max_shot_duration": self.pipeline.l1_max_shot_duration,
                "l1_min_scene_gap": self.pipeline.l1_min_scene_gap,
                "l2_motion_threshold": self.pipeline.l2_motion_threshold,
                "l2_motion_percentile": self.pipeline.l2_motion_percentile,
                "l2_motion_window": self.pipeline.l2_motion_window,
                "l2_min_high_motion_duration": self.pipeline.l2_min_high_motion_duration,
                "l2_audio_energy_weight": self.pipeline.l2_audio_energy_weight,
                "l2_min_energy_threshold": self.pipeline.l2_min_energy_threshold,
                "l2_composite_threshold": self.pipeline.l2_composite_threshold,
                "l2_color_variation_weight": self.pipeline.l2_color_variation_weight,
                "l2_shot_frequency_weight": self.pipeline.l2_shot_frequency_weight,
                "l2_fast_cut_fps_threshold": self.pipeline.l2_fast_cut_fps_threshold,
                # v2.2 新增：颜色/亮度/对比度
                "l2_brightness_std_weight": self.pipeline.l2_brightness_std_weight,
                "l2_saturation_weight": self.pipeline.l2_saturation_weight,
                "l2_contrast_weight": self.pipeline.l2_contrast_weight,
                "l2_brightness_std_threshold": self.pipeline.l2_brightness_std_threshold,
                "l2_saturation_min": self.pipeline.l2_saturation_min,
                "l2_min_color_window": self.pipeline.l2_min_color_window,
                # v2.2 新增：音频频谱分层
                "l2_audio_low_freq_weight": self.pipeline.l2_audio_low_freq_weight,
                "l2_audio_mid_freq_weight": self.pipeline.l2_audio_mid_freq_weight,
                "l2_audio_high_freq_weight": self.pipeline.l2_audio_high_freq_weight,
                "l2_audio_low_freq_threshold": self.pipeline.l2_audio_low_freq_threshold,
                "l2_audio_spectrum_window": self.pipeline.l2_audio_spectrum_window,
                "l2_audio_band_ratio_threshold": self.pipeline.l2_audio_band_ratio_threshold,
                "l2_audio_volume_weight": self.pipeline.l2_audio_volume_weight,
                # v2.2 新增：多窗口动态阈值
                "l2_short_window": self.pipeline.l2_short_window,
                "l2_mid_window": self.pipeline.l2_mid_window,
                "l2_long_window": self.pipeline.l2_long_window,
                "l2_window_weight_short": self.pipeline.l2_window_weight_short,
                "l2_window_weight_mid": self.pipeline.l2_window_weight_mid,
                "l2_window_weight_long": self.pipeline.l2_window_weight_long,
                "l2_window_ratio_threshold": self.pipeline.l2_window_ratio_threshold,
                "l3_enable": self.pipeline.l3_enable,
                "l3_candidate_ratio": self.pipeline.l3_candidate_ratio,
                "l3_min_confidence": self.pipeline.l3_min_confidence,
                "l3_sparse_inference": self.pipeline.l3_sparse_inference,
                "l3_frame_sample_interval": self.pipeline.l3_frame_sample_interval,
                "l3_model_int8": self.pipeline.l3_model_int8,
                "l3_fallback_to_rules": self.pipeline.l3_fallback_to_rules,
                "merge_gap_threshold": self.pipeline.merge_gap_threshold,
                "hot_min_duration": self.pipeline.hot_min_duration,
                "hot_max_duration": self.pipeline.hot_max_duration,
                "hot_top_n": self.pipeline.hot_top_n,
                "max_memory_limit_mb": self.pipeline.max_memory_limit_mb,
                "max_gpu_memory_mb": self.pipeline.max_gpu_memory_mb,
                "max_cpu_percent_target": self.pipeline.max_cpu_percent_target,
                "max_workers_decode": self.pipeline.max_workers_decode,
                "max_threads_filter": self.pipeline.max_threads_filter,
                "enable_memory_pressure_control": self.pipeline.enable_memory_pressure_control,
                "enable_cpu_throttling": self.pipeline.enable_cpu_throttling,
                "cpu_throttle_threshold_percent": self.pipeline.cpu_throttle_threshold_percent,
                "frame_buffer_max_frames": self.pipeline.frame_buffer_max_frames,
                # ============== v2.4 新增：资源控制 ==============
                "enable_resource_control": self.pipeline.enable_resource_control,
                "resource_check_interval_sec": self.pipeline.resource_check_interval_sec,
                "memory_limit_mb": self.pipeline.memory_limit_mb,
                "memory_graceful_mb": self.pipeline.memory_graceful_mb,
                "memory_critical_mb": self.pipeline.memory_critical_mb,
                "memory_enable_throttle": self.pipeline.memory_enable_throttle,
                "cpu_limit_percent": self.pipeline.cpu_limit_percent,
                "cpu_graceful_percent": self.pipeline.cpu_graceful_percent,
                "cpu_critical_percent": self.pipeline.cpu_critical_percent,
                "cpu_throttle_ms": self.pipeline.cpu_throttle_ms,
                "cpu_cores_limit": self.pipeline.cpu_cores_limit,
                "gpu_memory_limit_mb": self.pipeline.gpu_memory_limit_mb,
                "gpu_graceful_mb": self.pipeline.gpu_graceful_mb,
                "gpu_critical_mb": self.pipeline.gpu_critical_mb,
                "gpu_disable_on_critical": self.pipeline.gpu_disable_on_critical,
                # v2.4 核心改进：动态采样率
                "enable_dynamic_sampling": self.pipeline.enable_dynamic_sampling,
                "base_sample_interval_sec": self.pipeline.base_sample_interval_sec,
                "min_sample_interval_sec": self.pipeline.min_sample_interval_sec,
                "max_sample_interval_sec": self.pipeline.max_sample_interval_sec,
                "motion_sample_trigger": self.pipeline.motion_sample_trigger,
                "sample_adjust_step": self.pipeline.sample_adjust_step,
                "enable_motion_adaptive_sampling": self.pipeline.enable_motion_adaptive_sampling,
                "motion_high_fps_interval": self.pipeline.motion_high_fps_interval,
                # v2.4 帧缓存管理
                "max_frames_in_memory": self.pipeline.max_frames_in_memory,
                "frame_cache_enable_compression": self.pipeline.frame_cache_enable_compression,
                "enable_frame_pruning": self.pipeline.enable_frame_pruning,
                "motion_pruning_threshold": self.pipeline.motion_pruning_threshold,
                # v2.4 资源日志
                "enable_resource_logging": self.pipeline.enable_resource_logging,
                "resource_log_interval_sec": self.pipeline.resource_log_interval_sec,
                "enable_resource_stats": self.pipeline.enable_resource_stats,
                # v3.0 用户资源调控
                "resource_mode": self.pipeline.resource_mode,
                "cpu_max_percent": self.pipeline.cpu_max_percent,
                "cpu_target_percent": self.pipeline.cpu_target_percent,
                "cpu_check_interval_ms": self.pipeline.cpu_check_interval_ms,
                "cpu_throttle_aggressive": self.pipeline.cpu_throttle_aggressive,
                "ram_max_percent": self.pipeline.ram_max_percent,
                "ram_target_percent": self.pipeline.ram_target_percent,
                "gpu_vram_max_percent": self.pipeline.gpu_vram_max_percent,
                "max_ffmpeg_processes": self.pipeline.max_ffmpeg_processes,
                "max_analysis_threads": self.pipeline.max_analysis_threads,
                "auto_reduce_when_idle": self.pipeline.auto_reduce_when_idle,
                "idle_cpu_target_percent": self.pipeline.idle_cpu_target_percent,
                # ============== v2.5 新增：错误恢复与降级策略 ==============
                "enable_error_recovery": self.pipeline.enable_error_recovery,
                "max_retry_count": self.pipeline.max_retry_count,
                "retry_delay_sec": self.pipeline.retry_delay_sec,
                "error_log_path": self.pipeline.error_log_path,
                "enable_fallback_mode": self.pipeline.enable_fallback_mode,
                "fallback_analysis_resolution": self.pipeline.fallback_analysis_resolution,
                "fallback_frame_rate": self.pipeline.fallback_frame_rate,
                "skip_corrupted_frames": self.pipeline.skip_corrupted_frames,
                "min_valid_frames_ratio": self.pipeline.min_valid_frames_ratio,
                "enable_format_detection": self.pipeline.enable_format_detection,
                "unsupported_format_fallback": self.pipeline.unsupported_format_fallback,
                "max_analysis_timeout_sec": self.pipeline.max_analysis_timeout_sec,
                "enable_safe_mode": self.pipeline.enable_safe_mode,
            },
            "export": {
                "use_stream_copy": self.export.use_stream_copy,
                "force_reencode": self.export.force_reencode,
                "copy_video_codec": self.export.copy_video_codec,
                "copy_audio_codec": self.export.copy_audio_codec,
                "reencode_video_codec": self.export.reencode_video_codec,
                "reencode_preset": self.export.reencode_preset,
                "reencode_crf": self.export.reencode_crf,
            },
            "tts_engine": self.tts_engine,
            "tts_voice_male": self.tts_voice_male,
            "tts_voice_female": self.tts_voice_female,
            "scene_threshold": self.scene_threshold,
            "energy_threshold_percentile": self.energy_threshold_percentile,
            "detection_preset": self.detection_preset,
            "detection_top_n": self.detection_top_n,
            "detection_min_duration": self.detection_min_duration,
            "detection_max_duration": self.detection_max_duration,
            "enable_ai_vision_channel": self.enable_ai_vision_channel,
            "ai_vision_interval": self.ai_vision_interval,
            "progressive_analysis": self.progressive_analysis,
            "proxy_enabled": self.proxy_enabled,
            "proxy_resolution": self.proxy_resolution,
            "max_batch_files": self.max_batch_files,
            "max_cache_size_gb": self.max_cache_size_gb,
            "memory_limit_mb": self.memory_limit_mb,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'Config':
        """从字典创建配置"""
        config = cls()
        config.app_version = data.get("app_version", config.app_version)
        config.theme = data.get("theme", config.theme)
        config.ai_provider = data.get("ai_provider", config.ai_provider)
        
        if "local_models" in data:
            lm = data["local_models"]
            config.local_models.vision_model = lm.get("vision_model", config.local_models.vision_model)
            config.local_models.text_model = lm.get("text_model", config.local_models.text_model)
            config.local_models.whisper_model = lm.get("whisper_model", config.local_models.whisper_model)
            config.local_models.tts_model = lm.get("tts_model", config.local_models.tts_model)
            config.local_models.quantize = lm.get("quantize", config.local_models.quantize)
            config.local_models.vision_model_int8 = lm.get("vision_model_int8", config.local_models.vision_model_int8)
        
        if "api_fallback" in data:
            af = data["api_fallback"]
            config.api_fallback.enabled = af.get("enabled", config.api_fallback.enabled)
            config.api_fallback.openai_api_key = af.get("openai_api_key", config.api_fallback.openai_api_key)
            config.api_fallback.openai_model = af.get("openai_model", config.api_fallback.openai_model)
            config.api_fallback.claude_api_key = af.get("claude_api_key", config.api_fallback.claude_api_key)
            config.api_fallback.claude_model = af.get("claude_model", config.api_fallback.claude_model)
        
        if "pipeline" in data:
            pl = data["pipeline"]
            # 基础参数
            config.pipeline.base_frame_rate = pl.get("base_frame_rate", config.pipeline.base_frame_rate)
            config.pipeline.high_motion_frame_rate = pl.get("high_motion_frame_rate", config.pipeline.high_motion_frame_rate)
            config.pipeline.analysis_resolution = pl.get("analysis_resolution", config.pipeline.analysis_resolution)
            config.pipeline.analysis_width = pl.get("analysis_width", config.pipeline.analysis_width)
            config.pipeline.analysis_height = pl.get("analysis_height", config.pipeline.analysis_height)
            config.pipeline.sad_downsample = pl.get("sad_downsample", config.pipeline.sad_downsample)
            config.pipeline.enable_hw_decode = pl.get("enable_hw_decode", config.pipeline.enable_hw_decode)
            # v2.3 新增：多线程流水线
            config.pipeline.enable_multithread_pipeline = pl.get("enable_multithread_pipeline", config.pipeline.enable_multithread_pipeline)
            config.pipeline.pipeline_threads_decode = pl.get("pipeline_threads_decode", config.pipeline.pipeline_threads_decode)
            config.pipeline.pipeline_threads_feature = pl.get("pipeline_threads_feature", config.pipeline.pipeline_threads_feature)
            config.pipeline.pipeline_threads_filter = pl.get("pipeline_threads_filter", config.pipeline.pipeline_threads_filter)
            config.pipeline.pipeline_threads_extract = pl.get("pipeline_threads_extract", config.pipeline.pipeline_threads_extract)
            config.pipeline.pipeline_queue_size = pl.get("pipeline_queue_size", config.pipeline.pipeline_queue_size)
            config.pipeline.enable_pipeline_overlap = pl.get("enable_pipeline_overlap", config.pipeline.enable_pipeline_overlap)
            # v2.3 新增：增量分析
            config.pipeline.enable_incremental_analysis = pl.get("enable_incremental_analysis", config.pipeline.enable_incremental_analysis)
            config.pipeline.incremental_chunk_sec = pl.get("incremental_chunk_sec", config.pipeline.incremental_chunk_sec)
            config.pipeline.incremental_overlap_sec = pl.get("incremental_overlap_sec", config.pipeline.incremental_overlap_sec)
            config.pipeline.incremental_cache_results = pl.get("incremental_cache_results", config.pipeline.incremental_cache_results)
            # v2.3 新增：ffmpeg参数
            config.pipeline.ffmpeg_threads = pl.get("ffmpeg_threads", config.pipeline.ffmpeg_threads)
            config.pipeline.ffmpeg_nostdin = pl.get("ffmpeg_nostdin", config.pipeline.ffmpeg_nostdin)
            config.pipeline.ffmpeg_loglevel = pl.get("ffmpeg_loglevel", config.pipeline.ffmpeg_loglevel)
            config.pipeline.ffmpeg_enable_lowlatency = pl.get("ffmpeg_enable_lowlatency", config.pipeline.ffmpeg_enable_lowlatency)
            config.pipeline.ffmpeg_readrate = pl.get("ffmpeg_readrate", config.pipeline.ffmpeg_readrate)
            config.pipeline.ffmpeg_probesize = pl.get("ffmpeg_probesize", config.pipeline.ffmpeg_probesize)
            config.pipeline.ffmpeg_analyzeduration = pl.get("ffmpeg_analyzeduration", config.pipeline.ffmpeg_analyzeduration)
            config.pipeline.ffmpeg_noaccurate_seek = pl.get("ffmpeg_noaccurate_seek", config.pipeline.ffmpeg_noaccurate_seek)
            config.pipeline.ffmpeg_copyts = pl.get("ffmpeg_copyts", config.pipeline.ffmpeg_copyts)
            config.pipeline.ffmpeg_avoid_negative_ts = pl.get("ffmpeg_avoid_negative_ts", config.pipeline.ffmpeg_avoid_negative_ts)
            # v2.3 新增：并行提取
            config.pipeline.enable_parallel_extract = pl.get("enable_parallel_extract", config.pipeline.enable_parallel_extract)
            config.pipeline.parallel_extract_max_workers = pl.get("parallel_extract_max_workers", config.pipeline.parallel_extract_max_workers)
            config.pipeline.parallel_extract_min_segments = pl.get("parallel_extract_min_segments", config.pipeline.parallel_extract_min_segments)
            config.pipeline.extract_buffer_size = pl.get("extract_buffer_size", config.pipeline.extract_buffer_size)
            # L1
            config.pipeline.l1_scene_threshold = pl.get("l1_scene_threshold", config.pipeline.l1_scene_threshold)
            config.pipeline.l1_scene_threshold_min = pl.get("l1_scene_threshold_min", config.pipeline.l1_scene_threshold_min)
            config.pipeline.l1_scene_threshold_max = pl.get("l1_scene_threshold_max", config.pipeline.l1_scene_threshold_max)
            config.pipeline.l1_min_shot_duration = pl.get("l1_min_shot_duration", config.pipeline.l1_min_shot_duration)
            config.pipeline.l1_max_shot_duration = pl.get("l1_max_shot_duration", config.pipeline.l1_max_shot_duration)
            config.pipeline.l1_min_scene_gap = pl.get("l1_min_scene_gap", config.pipeline.l1_min_scene_gap)
            # L2
            config.pipeline.l2_motion_threshold = pl.get("l2_motion_threshold", config.pipeline.l2_motion_threshold)
            config.pipeline.l2_motion_percentile = pl.get("l2_motion_percentile", config.pipeline.l2_motion_percentile)
            config.pipeline.l2_motion_window = pl.get("l2_motion_window", config.pipeline.l2_motion_window)
            config.pipeline.l2_min_high_motion_duration = pl.get("l2_min_high_motion_duration", config.pipeline.l2_min_high_motion_duration)
            config.pipeline.l2_audio_energy_weight = pl.get("l2_audio_energy_weight", config.pipeline.l2_audio_energy_weight)
            config.pipeline.l2_min_energy_threshold = pl.get("l2_min_energy_threshold", config.pipeline.l2_min_energy_threshold)
            config.pipeline.l2_composite_threshold = pl.get("l2_composite_threshold", config.pipeline.l2_composite_threshold)
            config.pipeline.l2_color_variation_weight = pl.get("l2_color_variation_weight", config.pipeline.l2_color_variation_weight)
            config.pipeline.l2_shot_frequency_weight = pl.get("l2_shot_frequency_weight", config.pipeline.l2_shot_frequency_weight)
            config.pipeline.l2_fast_cut_fps_threshold = pl.get("l2_fast_cut_fps_threshold", config.pipeline.l2_fast_cut_fps_threshold)
            # v2.2 新增：颜色/亮度/对比度
            config.pipeline.l2_brightness_std_weight = pl.get("l2_brightness_std_weight", config.pipeline.l2_brightness_std_weight)
            config.pipeline.l2_saturation_weight = pl.get("l2_saturation_weight", config.pipeline.l2_saturation_weight)
            config.pipeline.l2_contrast_weight = pl.get("l2_contrast_weight", config.pipeline.l2_contrast_weight)
            config.pipeline.l2_brightness_std_threshold = pl.get("l2_brightness_std_threshold", config.pipeline.l2_brightness_std_threshold)
            config.pipeline.l2_saturation_min = pl.get("l2_saturation_min", config.pipeline.l2_saturation_min)
            config.pipeline.l2_min_color_window = pl.get("l2_min_color_window", config.pipeline.l2_min_color_window)
            # v2.2 新增：音频频谱分层
            config.pipeline.l2_audio_low_freq_weight = pl.get("l2_audio_low_freq_weight", config.pipeline.l2_audio_low_freq_weight)
            config.pipeline.l2_audio_mid_freq_weight = pl.get("l2_audio_mid_freq_weight", config.pipeline.l2_audio_mid_freq_weight)
            config.pipeline.l2_audio_high_freq_weight = pl.get("l2_audio_high_freq_weight", config.pipeline.l2_audio_high_freq_weight)
            config.pipeline.l2_audio_low_freq_threshold = pl.get("l2_audio_low_freq_threshold", config.pipeline.l2_audio_low_freq_threshold)
            config.pipeline.l2_audio_spectrum_window = pl.get("l2_audio_spectrum_window", config.pipeline.l2_audio_spectrum_window)
            config.pipeline.l2_audio_band_ratio_threshold = pl.get("l2_audio_band_ratio_threshold", config.pipeline.l2_audio_band_ratio_threshold)
            config.pipeline.l2_audio_volume_weight = pl.get("l2_audio_volume_weight", config.pipeline.l2_audio_volume_weight)
            # v2.2 新增：多窗口动态阈值
            config.pipeline.l2_short_window = pl.get("l2_short_window", config.pipeline.l2_short_window)
            config.pipeline.l2_mid_window = pl.get("l2_mid_window", config.pipeline.l2_mid_window)
            config.pipeline.l2_long_window = pl.get("l2_long_window", config.pipeline.l2_long_window)
            config.pipeline.l2_window_weight_short = pl.get("l2_window_weight_short", config.pipeline.l2_window_weight_short)
            config.pipeline.l2_window_weight_mid = pl.get("l2_window_weight_mid", config.pipeline.l2_window_weight_mid)
            config.pipeline.l2_window_weight_long = pl.get("l2_window_weight_long", config.pipeline.l2_window_weight_long)
            config.pipeline.l2_window_ratio_threshold = pl.get("l2_window_ratio_threshold", config.pipeline.l2_window_ratio_threshold)
            # L3
            config.pipeline.l3_enable = pl.get("l3_enable", config.pipeline.l3_enable)
            config.pipeline.l3_candidate_ratio = pl.get("l3_candidate_ratio", config.pipeline.l3_candidate_ratio)
            config.pipeline.l3_min_confidence = pl.get("l3_min_confidence", config.pipeline.l3_min_confidence)
            config.pipeline.l3_sparse_inference = pl.get("l3_sparse_inference", config.pipeline.l3_sparse_inference)
            config.pipeline.l3_frame_sample_interval = pl.get("l3_frame_sample_interval", config.pipeline.l3_frame_sample_interval)
            config.pipeline.l3_model_int8 = pl.get("l3_model_int8", config.pipeline.l3_model_int8)
            config.pipeline.l3_fallback_to_rules = pl.get("l3_fallback_to_rules", config.pipeline.l3_fallback_to_rules)
            # 合并与评分
            config.pipeline.merge_gap_threshold = pl.get("merge_gap_threshold", config.pipeline.merge_gap_threshold)
            config.pipeline.hot_min_duration = pl.get("hot_min_duration", config.pipeline.hot_min_duration)
            config.pipeline.hot_max_duration = pl.get("hot_max_duration", config.pipeline.hot_max_duration)
            config.pipeline.hot_top_n = pl.get("hot_top_n", config.pipeline.hot_top_n)
            # 资源限制
            config.pipeline.max_memory_limit_mb = pl.get("max_memory_limit_mb", config.pipeline.max_memory_limit_mb)
            config.pipeline.max_gpu_memory_mb = pl.get("max_gpu_memory_mb", config.pipeline.max_gpu_memory_mb)
            config.pipeline.max_cpu_percent_target = pl.get("max_cpu_percent_target", config.pipeline.max_cpu_percent_target)
            config.pipeline.max_workers_decode = pl.get("max_workers_decode", config.pipeline.max_workers_decode)
            config.pipeline.max_threads_filter = pl.get("max_threads_filter", config.pipeline.max_threads_filter)
            config.pipeline.enable_memory_pressure_control = pl.get("enable_memory_pressure_control", config.pipeline.enable_memory_pressure_control)
            config.pipeline.enable_cpu_throttling = pl.get("enable_cpu_throttling", config.pipeline.enable_cpu_throttling)
            config.pipeline.cpu_throttle_threshold_percent = pl.get("cpu_throttle_threshold_percent", config.pipeline.cpu_throttle_threshold_percent)
            config.pipeline.frame_buffer_max_frames = pl.get("frame_buffer_max_frames", config.pipeline.frame_buffer_max_frames)
            # ============== v2.5 新增：错误恢复与降级策略 ==============
            config.pipeline.enable_error_recovery = pl.get("enable_error_recovery", config.pipeline.enable_error_recovery)
            config.pipeline.max_retry_count = pl.get("max_retry_count", config.pipeline.max_retry_count)
            config.pipeline.retry_delay_sec = pl.get("retry_delay_sec", config.pipeline.retry_delay_sec)
            config.pipeline.error_log_path = pl.get("error_log_path", config.pipeline.error_log_path)
            config.pipeline.enable_fallback_mode = pl.get("enable_fallback_mode", config.pipeline.enable_fallback_mode)
            config.pipeline.fallback_analysis_resolution = pl.get("fallback_analysis_resolution", config.pipeline.fallback_analysis_resolution)
            config.pipeline.fallback_frame_rate = pl.get("fallback_frame_rate", config.pipeline.fallback_frame_rate)
            config.pipeline.skip_corrupted_frames = pl.get("skip_corrupted_frames", config.pipeline.skip_corrupted_frames)
            config.pipeline.min_valid_frames_ratio = pl.get("min_valid_frames_ratio", config.pipeline.min_valid_frames_ratio)
            config.pipeline.enable_format_detection = pl.get("enable_format_detection", config.pipeline.enable_format_detection)
            config.pipeline.unsupported_format_fallback = pl.get("unsupported_format_fallback", config.pipeline.unsupported_format_fallback)
            config.pipeline.max_analysis_timeout_sec = pl.get("max_analysis_timeout_sec", config.pipeline.max_analysis_timeout_sec)
            config.pipeline.enable_safe_mode = pl.get("enable_safe_mode", config.pipeline.enable_safe_mode)
            # v3.0 用户资源调控
            config.pipeline.resource_mode = pl.get("resource_mode", config.pipeline.resource_mode)
            config.pipeline.cpu_max_percent = pl.get("cpu_max_percent", config.pipeline.cpu_max_percent)
            config.pipeline.cpu_target_percent = pl.get("cpu_target_percent", config.pipeline.cpu_target_percent)
            config.pipeline.cpu_check_interval_ms = pl.get("cpu_check_interval_ms", config.pipeline.cpu_check_interval_ms)
            config.pipeline.cpu_throttle_aggressive = pl.get("cpu_throttle_aggressive", config.pipeline.cpu_throttle_aggressive)
            config.pipeline.ram_max_percent = pl.get("ram_max_percent", config.pipeline.ram_max_percent)
            config.pipeline.ram_target_percent = pl.get("ram_target_percent", config.pipeline.ram_target_percent)
            config.pipeline.gpu_vram_max_percent = pl.get("gpu_vram_max_percent", config.pipeline.gpu_vram_max_percent)
            config.pipeline.max_ffmpeg_processes = pl.get("max_ffmpeg_processes", config.pipeline.max_ffmpeg_processes)
            config.pipeline.max_analysis_threads = pl.get("max_analysis_threads", config.pipeline.max_analysis_threads)
            config.pipeline.auto_reduce_when_idle = pl.get("auto_reduce_when_idle", config.pipeline.auto_reduce_when_idle)
            config.pipeline.idle_cpu_target_percent = pl.get("idle_cpu_target_percent", config.pipeline.idle_cpu_target_percent)
        
        if "export" in data:
            ex = data["export"]
            config.export.use_stream_copy = ex.get("use_stream_copy", config.export.use_stream_copy)
            config.export.force_reencode = ex.get("force_reencode", config.export.force_reencode)
            config.export.copy_video_codec = ex.get("copy_video_codec", config.export.copy_video_codec)
            config.export.copy_audio_codec = ex.get("copy_audio_codec", config.export.copy_audio_codec)
            config.export.reencode_video_codec = ex.get("reencode_video_codec", config.export.reencode_video_codec)
            config.export.reencode_preset = ex.get("reencode_preset", config.export.reencode_preset)
            config.export.reencode_crf = ex.get("reencode_crf", config.export.reencode_crf)
        
        config.ffmpeg_path = data.get("ffmpeg_path", config.ffmpeg_path)
        config.ffprobe_path = data.get("ffprobe_path", config.ffprobe_path)
        config.openai_api_key = data.get("openai_api_key", config.openai_api_key)
        config.claude_api_key = data.get("claude_api_key", config.claude_api_key)
        config.hw_accel = data.get("hw_accel", config.hw_accel)
        config.output_dir = data.get("output_dir", config.output_dir)
        config.cache_dir = data.get("cache_dir", config.cache_dir)
        config.default_format = data.get("default_format", config.default_format)
        config.default_resolution = data.get("default_resolution", config.default_resolution)
        config.default_codec = data.get("default_codec", config.default_codec)
        config.tts_engine = data.get("tts_engine", config.tts_engine)
        config.tts_voice_male = data.get("tts_voice_male", config.tts_voice_male)
        config.tts_voice_female = data.get("tts_voice_female", config.tts_voice_female)
        config.scene_threshold = data.get("scene_threshold", config.scene_threshold)
        config.energy_threshold_percentile = data.get("energy_threshold_percentile", config.energy_threshold_percentile)
        config.detection_preset = data.get("detection_preset", config.detection_preset)
        config.detection_top_n = data.get("detection_top_n", config.detection_top_n)
        config.detection_min_duration = data.get("detection_min_duration", config.detection_min_duration)
        config.detection_max_duration = data.get("detection_max_duration", config.detection_max_duration)
        config.enable_ai_vision_channel = data.get("enable_ai_vision_channel", config.enable_ai_vision_channel)
        config.ai_vision_interval = data.get("ai_vision_interval", config.ai_vision_interval)
        config.progressive_analysis = data.get("progressive_analysis", config.progressive_analysis)
        config.proxy_enabled = data.get("proxy_enabled", config.proxy_enabled)
        config.proxy_resolution = data.get("proxy_resolution", config.proxy_resolution)
        config.max_batch_files = data.get("max_batch_files", config.max_batch_files)
        config.max_cache_size_gb = data.get("max_cache_size_gb", config.max_cache_size_gb)
        config.memory_limit_mb = data.get("memory_limit_mb", config.memory_limit_mb)
        
        return config
    
    def save(self, filepath: str = "config.json"):
        """保存配置到文件"""
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
    
    @classmethod
    def load(cls, filepath: str = "config.json") -> 'Config':
        """从文件加载配置"""
        if not os.path.exists(filepath):
            config = cls()
            config.save(filepath)
            return config
        
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        return cls.from_dict(data)


# 全局配置实例
CONFIG = Config.load()
