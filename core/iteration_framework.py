"""
迭代框架 v2.1 - 性能指标记录 + 多版本对比

每次迭代完成后，记录当前版本的性能指标：
- 视频处理时长 (total, analyze, filter, extract)
- 内存峰值 (MB)
- 识别片段数
- 平均评分
- 硬件加速方式
- 分析有效FPS

版本对比表格式:
| 版本 | 总耗时(s) | 分析(s) | 筛选(s) | 提取(s) | 内存峰值(MB) | 片段数 | 硬件加速 | 评分 | FPS | 时间 |
|------|----------|---------|---------|---------|--------------|--------|----------|------|-----|------|
"""
import time
import os
import json
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class PerformanceMetrics:
    """单次迭代的性能指标"""
    version: str = "2.1.0"
    total_time_sec: float = 0.0
    analyze_time_sec: float = 0.0
    filter_time_sec: float = 0.0
    extract_time_sec: float = 0.0
    peak_memory_mb: float = 0.0
    segments_count: int = 0
    avg_score: float = 0.0
    video_duration_sec: float = 0.0
    hardware_accel: str = "none"
    effective_fps: float = 0.0
    timestamp: str = ""
    
    # 性能等级 (A > B > C > D)
    def perf_grade(self) -> str:
        """根据速度和资源占用评级"""
        if self.video_duration_sec == 0:
            return "N/A"
        ratio = self.total_time_sec / self.video_duration_sec
        if ratio < 0.1:  # 10秒视频处理1秒 = A级
            return "A"
        elif ratio < 0.2:
            return "B"
        elif ratio < 0.5:
            return "C"
        else:
            return "D"
    
    def summary(self) -> str:
        """单行摘要"""
        grade = self.perf_grade()
        return (f"[{grade}] {self.version} | "
                f"总{self.total_time_sec:.1f}s = "
                f"分析{self.analyze_time_sec:.1f}s + "
                f"筛选{self.filter_time_sec:.1f}s + "
                f"提取{self.extract_time_sec:.1f}s | "
                f"内存{self.peak_memory_mb:.0f}MB | "
                f"{self.segments_count}片段 avg_score={self.avg_score:.2f} | "
                f"{self.hardware_accel}")


class IterationVersion:
    """版本信息"""
    def __init__(self, version: str, release_date: str, 
                 changes: List[str], architecture_desc: str):
        self.version = version
        self.release_date = release_date
        self.changes = changes
        self.architecture_desc = architecture_desc
    
    def to_dict(self) -> dict:
        return {
            'version': self.version,
            'release_date': self.release_date,
            'changes': self.changes,
            'architecture': self.architecture_desc,
        }


# =========================================================
# 版本记录 (历史版本的架构变更记录)
# =========================================================
VERSION_HISTORY = [
    IterationVersion(
        version="2.0.0",
        release_date="2024-11-20",
        changes=[
            "初始三阶段筛选架构",
            "简单帧差计算运动",
            "固定阈值参数",
            "软件解码为主",
        ],
        architecture_desc="简单帧差 + 固定阈值 + 软解码 (基准版本)"
    ),
    IterationVersion(
        version="2.1.0",
        release_date="2024-11-25",
        changes=[
            "SAD (Sum of Absolute Differences) 运动分析替代简单帧差",
            "动态百分位阈值 (P25/P50/P75) 替代固定阈值",
            "快切镜头组识别：连续短镜头组合判断",
            "多特征融合评分：运动+音频+颜色+切变频率",
            "自动硬件加速检测 (CUDA/QSV/VAAPI)",
            "内存压力控制 + CPU节流监测",
            "码流拷贝优先的视频提取（无重编码最快）",
            "流式处理仅保留上一帧，降低内存占用",
            "1fps基础抽帧（降采样到960x540，SAD再降采样到1/4）",
        ],
        architecture_desc="SAD运动分析 + 动态百分位阈值 + 快切组识别 + "
                         "多特征融合 + 硬件加速 + 内存压力控制 + 码流拷贝"
    ),
]


class IterationTracker:
    """迭代追踪器 - 记录每次运行的性能指标"""
    
    def __init__(self, data_dir: str = None):
        self.metrics_history: List[PerformanceMetrics] = []
        if data_dir is None:
            # 默认: 父目录/logs/iterations
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            data_dir = os.path.join(base, 'logs', 'iterations')
        
        self.data_dir = data_dir
        os.makedirs(self.data_dir, exist_ok=True)
        
        self.data_file = os.path.join(self.data_dir, 'metrics_history.json')
        self._load_existing()
    
    # =========================================================
    # 记录
    # =========================================================
    def record_iteration(self, metrics: PerformanceMetrics):
        """记录一次迭代指标"""
        self.metrics_history.append(metrics)
        self._save_to_file()
        print(f"[IterationTracker] 记录版本 {metrics.version}: "
              f"{metrics.total_time_sec:.1f}s, "
              f"{metrics.segments_count}片段, "
              f"内存{metrics.peak_memory_mb:.0f}MB")
    
    # =========================================================
    # 持久化
    # =========================================================
    def _load_existing(self):
        """从JSON文件加载历史数据"""
        try:
            if os.path.exists(self.data_file) and os.path.getsize(self.data_file) > 0:
                with open(self.data_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                for item in data:
                    metrics = PerformanceMetrics(
                        version=item.get('version', 'unknown'),
                        total_time_sec=float(item.get('total_time_sec', 0)),
                        analyze_time_sec=float(item.get('analyze_time_sec', 0)),
                        filter_time_sec=float(item.get('filter_time_sec', 0)),
                        extract_time_sec=float(item.get('extract_time_sec', 0)),
                        peak_memory_mb=float(item.get('peak_memory_mb', 0)),
                        segments_count=int(item.get('segments_count', 0)),
                        avg_score=float(item.get('avg_score', 0)),
                        video_duration_sec=float(item.get('video_duration_sec', 0)),
                        hardware_accel=item.get('hardware_accel', 'none'),
                        effective_fps=float(item.get('effective_fps', 0)),
                        timestamp=item.get('timestamp', ''),
                    )
                    self.metrics_history.append(metrics)
                
                print(f"[IterationTracker] 加载了 {len(self.metrics_history)} 条历史记录")
        except Exception as e:
            print(f"[IterationTracker] 加载历史失败 (将新建): {e}")
            self.metrics_history = []
    
    def _save_to_file(self):
        """保存到JSON文件"""
        try:
            data = []
            for m in self.metrics_history:
                data.append({
                    'version': m.version,
                    'total_time_sec': m.total_time_sec,
                    'analyze_time_sec': m.analyze_time_sec,
                    'filter_time_sec': m.filter_time_sec,
                    'extract_time_sec': m.extract_time_sec,
                    'peak_memory_mb': m.peak_memory_mb,
                    'segments_count': m.segments_count,
                    'avg_score': m.avg_score,
                    'video_duration_sec': m.video_duration_sec,
                    'hardware_accel': m.hardware_accel,
                    'effective_fps': m.effective_fps,
                    'timestamp': m.timestamp,
                    'perf_grade': m.perf_grade(),
                })
            
            with open(self.data_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[IterationTracker] 保存失败: {e}")
    
    # =========================================================
    # 获取历史
    # =========================================================
    def get_history_text(self) -> List[str]:
        """获取文本格式的历史记录"""
        lines = []
        if not self.metrics_history:
            lines.append("  暂无历史记录")
            return lines
        
        for i, m in enumerate(self.metrics_history):
            lines.append(f"  [{i+1}] {m.summary()}")
        
        # 汇总统计
        if self.metrics_history:
            times = [m.total_time_sec for m in self.metrics_history]
            mems = [m.peak_memory_mb for m in self.metrics_history]
            lines.append("")
            lines.append(f"  统计汇总: 平均用时 {sum(times)/len(times):.1f}s, "
                        f"最佳用时 {min(times):.1f}s, "
                        f"平均内存 {sum(mems)/len(mems):.0f}MB, "
                        f"最低内存 {min(mems):.0f}MB")
        return lines
    
    def get_summary_table(self) -> str:
        """生成性能指标对比表 (Markdown)"""
        if not self.metrics_history:
            return "暂无性能指标"
        
        header = "| # | 版本 | 等级 | 总耗时(s) | 分析(s) | 筛选(s) | 提取(s) | 内存(MB) | 片段数 | 评分 | FPS | 硬件 | 时间 |\n"
        separator = "|---|------|------|----------|---------|---------|---------|----------|--------|------|-----|------|------|\n"
        body = ""
        
        for i, m in enumerate(self.metrics_history):
            row = (f"| {i+1} | {m.version} | {m.perf_grade()} | "
                  f"{m.total_time_sec:.1f} | {m.analyze_time_sec:.1f} | "
                  f"{m.filter_time_sec:.1f} | {m.extract_time_sec:.1f} | "
                  f"{m.peak_memory_mb:.0f} | {m.segments_count} | "
                  f"{m.avg_score:.2f} | {m.effective_fps:.0f} | "
                  f"{m.hardware_accel} | {m.timestamp} |\n")
            body += row
        
        return header + separator + body
    
    def get_versions_summary_table(self) -> str:
        """版本架构变更对比表"""
        lines = []
        lines.append("# FireClip 版本迭代历史\n")
        lines.append(f"> 最近更新: {time.strftime('%Y-%m-%d')}\n")
        
        for i, v in enumerate(VERSION_HISTORY):
            lines.append(f"## v{v.version} ({v.release_date})\n")
            lines.append(f"**架构:** {v.architecture_desc}\n")
            lines.append("**核心变更:**\n")
            for j, change in enumerate(v.changes):
                lines.append(f"- {change}")
            lines.append("")
        
        return '\n'.join(lines)
    
    # =========================================================
    # 统计分析
    # =========================================================
    def get_statistics(self) -> dict:
        """获取统计分析数据"""
        if not self.metrics_history:
            return {}
        
        n = len(self.metrics_history)
        times = [m.total_time_sec for m in self.metrics_history]
        mems = [m.peak_memory_mb for m in self.metrics_history]
        scores = [m.avg_score for m in self.metrics_history]
        segs = [m.segments_count for m in self.metrics_history]
        
        return {
            'iterations': n,
            'avg_total_time': sum(times) / n,
            'best_total_time': min(times),
            'worst_total_time': max(times),
            'avg_memory': sum(mems) / n,
            'best_memory': min(mems),
            'avg_score': sum(scores) / n,
            'avg_segments': sum(segs) / n,
            'latest_version': self.metrics_history[-1].version,
            'latest_time': self.metrics_history[-1].timestamp,
        }


# =========================================================
# 自测
# =========================================================
if __name__ == "__main__":
    print("="*60)
    print("IterationFramework v2.1 自测")
    print("="*60)
    
    tracker = IterationTracker()
    
    # 模拟几次迭代记录
    for i in range(3):
        metrics = PerformanceMetrics(
            version="2.1.0-test",
            total_time_sec=30.0 + i * 2.5,
            analyze_time_sec=15.0 + i,
            filter_time_sec=3.0 + i * 0.2,
            extract_time_sec=12.0 + i * 1.3,
            peak_memory_mb=450.0 + i * 10,
            segments_count=8 + i,
            avg_score=0.78 - i * 0.01,
            video_duration_sec=300.0,
            hardware_accel="cuda",
            effective_fps=100.0,
            timestamp=time.strftime('%Y-%m-%d %H:%M:%S'),
        )
        tracker.record_iteration(metrics)
    
    print("\n历史记录:")
    for line in tracker.get_history_text():
        print(line)
    
    print("\n性能指标表:")
    print(tracker.get_summary_table())
    
    print("\n版本变更表:")
    print(tracker.get_versions_summary_table()[:500] + "...")
    
    print("\n统计信息:")
    stats = tracker.get_statistics()
    for k, v in stats.items():
        print(f"  {k}: {v}")
    
    print("\n" + "="*60)
    print("迭代框架功能完整")
    print("="*60)
    print("\n数据文件位置:", tracker.data_file)
