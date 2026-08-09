"""
素材质量过滤模块 v1.0
检测并过滤低质量片段：黑屏/纯色、过曝/欠曝、模糊、严重抖动
纯OpenCV实现，轻量高效，无大模型依赖
"""
import os
from dataclasses import dataclass
from typing import List, Tuple, Optional, Callable, TYPE_CHECKING

try:
    import cv2
    import numpy as np
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False
    import numpy as np


@dataclass
class QualityResult:
    """单帧质量检测结果"""
    timestamp: float
    is_black: bool = False
    is_solid_color: bool = False
    is_overexposed: bool = False
    is_underexposed: bool = False
    is_blurry: bool = False
    blur_score: float = 0.0
    brightness_mean: float = 0.0
    brightness_std: float = 0.0
    is_shaky: bool = False
    shake_score: float = 0.0
    passed: bool = True


@dataclass
class SegmentQuality:
    """片段质量结果"""
    start_time: float
    end_time: float
    pass_ratio: float = 0.0
    black_ratio: float = 0.0
    blurry_ratio: float = 0.0
    shaky_ratio: float = 0.0
    overexposed_ratio: float = 0.0
    overall_passed: bool = True


class QualityFilterConfig:
    """质量过滤配置"""
    def __init__(
        self,
        black_threshold: int = 20,
        solid_color_std_threshold: float = 8.0,
        overexposed_threshold: int = 240,
        underexposed_threshold: int = 15,
        blur_threshold: float = 60.0,
        shake_threshold: float = 25.0,
        min_pass_ratio: float = 0.7,
        sample_fps: float = 2.0,
    ):
        self.black_threshold = black_threshold
        self.solid_color_std_threshold = solid_color_std_threshold
        self.overexposed_threshold = overexposed_threshold
        self.underexposed_threshold = underexposed_threshold
        self.blur_threshold = blur_threshold
        self.shake_threshold = shake_threshold
        self.min_pass_ratio = min_pass_ratio
        self.sample_fps = sample_fps


class QualityFilter:
    """素材质量过滤器"""
    
    def __init__(self, config: Optional[QualityFilterConfig] = None):
        self.config = config or QualityFilterConfig()
    
    def analyze_video(
        self,
        video_path: str,
        callback: Optional[Callable[[int, str], None]] = None,
    ) -> List[QualityResult]:
        """分析整个视频的帧质量"""
        if not CV2_AVAILABLE or not NUMPY_AVAILABLE:
            return []
        
        import cv2
        import numpy as np
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return []
        
        fps = cap.get(cv2.CAP_PROP_FPS)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        sample_interval = max(1, int(fps / self.config.sample_fps))
        
        results = []
        prev_gray = None
        frame_count = 0
        
        try:
            while True:
                ret, frame = cap.read()
                if not ret:
                    break
                
                frame_count += 1
                if frame_count % sample_interval != 0:
                    continue
                
                timestamp = frame_count / fps
                if callback and frame_count % (sample_interval * 10) == 0:
                    progress = int(frame_count / total_frames * 100)
                    callback(progress, f"质量检测中... {progress}%")
                
                qr = self._analyze_frame(frame, timestamp, prev_gray)
                results.append(qr)
                
                if not qr.is_black and not qr.is_solid_color:
                    gray = cv2.cvtColor(cv2.resize(frame, (320, 180)), cv2.COLOR_BGR2GRAY)
                    prev_gray = gray
            
        finally:
            cap.release()
        
        return results
    
    def _analyze_frame(
        self,
        frame: np.ndarray,
        timestamp: float,
        prev_gray: Optional[np.ndarray],
    ) -> QualityResult:
        """分析单帧质量"""
        qr = QualityResult(timestamp=timestamp)
        
        small = cv2.resize(frame, (320, 180))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        
        qr.brightness_mean = float(np.mean(gray))
        qr.brightness_std = float(np.std(gray))
        
        # 黑屏/暗场检测
        if qr.brightness_mean < self.config.black_threshold:
            qr.is_black = True
            qr.passed = False
        
        # 纯色画面检测
        if qr.brightness_std < self.config.solid_color_std_threshold:
            qr.is_solid_color = True
            qr.passed = False
        
        # 过曝检测
        over_ratio = np.mean(gray > self.config.overexposed_threshold)
        if over_ratio > 0.9:
            qr.is_overexposed = True
            qr.passed = False
        
        # 欠曝检测
        under_ratio = np.mean(gray < self.config.underexposed_threshold)
        if under_ratio > 0.9:
            qr.is_underexposed = True
            qr.passed = False
        
        # 模糊检测（拉普拉斯方差）
        laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
        qr.blur_score = float(laplacian_var)
        if laplacian_var < self.config.blur_threshold:
            qr.is_blurry = True
            qr.passed = False
        
        # 抖动检测（帧差）
        if prev_gray is not None and prev_gray.shape == gray.shape:
            diff = cv2.absdiff(prev_gray, gray)
            mean_diff = float(np.mean(diff))
            qr.shake_score = mean_diff
            if mean_diff > self.config.shake_threshold:
                qr.is_shaky = True
                qr.passed = False
        
        return qr
    
    def filter_segments(
        self,
        segments: List[Tuple[float, float]],
        quality_results: List[QualityResult],
    ) -> Tuple[List[Tuple[float, float]], List[SegmentQuality]]:
        """过滤低质量片段"""
        if not quality_results:
            return segments, []
        
        passed_segments = []
        segment_qualities = []
        
        for start, end in segments:
            sq = self._evaluate_segment(start, end, quality_results)
            segment_qualities.append(sq)
            if sq.overall_passed:
                passed_segments.append((start, end))
        
        return passed_segments, segment_qualities
    
    def _evaluate_segment(
        self,
        start: float,
        end: float,
        results: List[QualityResult],
    ) -> SegmentQuality:
        """评估单个片段质量"""
        segment_frames = [r for r in results if start <= r.timestamp <= end]
        if not segment_frames:
            return SegmentQuality(start, end, 0.0, 1.0, 0.0, 0.0, 0.0, False)
        
        total = len(segment_frames)
        black_count = sum(1 for r in segment_frames if r.is_black)
        blurry_count = sum(1 for r in segment_frames if r.is_blurry)
        shaky_count = sum(1 for r in segment_frames if r.is_shaky)
        over_count = sum(1 for r in segment_frames if r.is_overexposed or r.is_underexposed)
        passed_count = sum(1 for r in segment_frames if r.passed)
        
        pass_ratio = passed_count / total
        black_ratio = black_count / total
        blurry_ratio = blurry_count / total
        shaky_ratio = shaky_count / total
        over_ratio = over_count / total
        
        overall_passed = (
            pass_ratio >= self.config.min_pass_ratio
            and black_ratio < 0.3
            and blurry_ratio < 0.4
        )
        
        return SegmentQuality(
            start_time=start,
            end_time=end,
            pass_ratio=pass_ratio,
            black_ratio=black_ratio,
            blurry_ratio=blurry_ratio,
            shaky_ratio=shaky_ratio,
            overexposed_ratio=over_ratio,
            overall_passed=overall_passed,
        )
