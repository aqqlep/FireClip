"""
节拍检测模块 v1.0
BPM估计 + 节拍点定位
优先使用librosa，提供能量峰值fallback
"""
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import List, Optional, Callable
import logging

logger = logging.getLogger("FireClip")

try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False
    np = None


@dataclass
class BeatAnalysisResult:
    """节拍分析结果"""
    bpm: float = 120.0
    beat_times: List[float] = field(default_factory=list)
    confidence: float = 0.0
    success: bool = False
    method: str = "fallback"


class BeatDetectorConfig:
    """节拍检测配置"""
    def __init__(
        self,
        min_bpm: int = 60,
        max_bpm: int = 200,
        onset_threshold: float = 0.5,
        sample_rate: int = 22050,
    ):
        self.min_bpm = min_bpm
        self.max_bpm = max_bpm
        self.onset_threshold = onset_threshold
        self.sample_rate = sample_rate


class BeatDetector:
    """节拍检测器"""
    
    def __init__(self, config: Optional[BeatDetectorConfig] = None, ffmpeg_path: str = "ffmpeg"):
        self.config = config or BeatDetectorConfig()
        self.ffmpeg_path = ffmpeg_path
    
    def analyze(
        self,
        audio_path: str,
        is_video: bool = False,
        callback: Optional[Callable[[int, str], None]] = None,
    ) -> BeatAnalysisResult:
        """
        分析音频/视频文件的节拍
        
        Args:
            audio_path: 音频文件路径，如果is_video=True则为视频路径
            is_video: 是否是视频文件
            callback: 进度回调
        """
        temp_audio = None
        
        try:
            if is_video:
                if callback:
                    callback(10, "提取音频用于节拍分析...")
                temp_dir = tempfile.mkdtemp(prefix="beat_")
                temp_audio = os.path.join(temp_dir, "beat_audio.wav")
                try:
                    self._extract_audio(audio_path, temp_audio)
                    analyze_path = temp_audio
                except Exception as e:
                    logger.warning(f"[BeatDetector] 提取音频失败，跳过节拍检测: {str(e)[:200]}")
                    return BeatAnalysisResult(success=False, method="failed")
            else:
                analyze_path = audio_path
            
            if callback:
                callback(30, "BPM与节拍检测...")
            
            if not NUMPY_AVAILABLE:
                logger.debug("[BeatDetector] numpy不可用，使用默认节拍")
                return BeatAnalysisResult(success=False, method="numpy_unavailable")
            
            try:
                return self._analyze_librosa(analyze_path, callback)
            except ImportError:
                logger.debug("[BeatDetector] librosa不可用，使用能量检测fallback")
                return self._analyze_energy_fallback(analyze_path, callback)
            except Exception as e:
                logger.warning(f"[BeatDetector] 节拍分析失败: {str(e)[:200]}")
                return BeatAnalysisResult(success=False, method="error")
                
        except Exception as e:
            logger.warning(f"[BeatDetector] 节拍检测整体失败: {str(e)[:200]}")
            return BeatAnalysisResult(success=False, method="exception")
        finally:
            if temp_audio and os.path.exists(temp_audio):
                try:
                    os.unlink(temp_audio)
                    os.rmdir(os.path.dirname(temp_audio))
                except Exception:
                    pass
    
    def _extract_audio(self, video_path: str, output_path: str):
        cmd = [
            self.ffmpeg_path, "-y", "-i", video_path,
            "-vn", "-acodec", "pcm_s16le", "-ar", str(self.config.sample_rate), "-ac", "1",
            output_path
        ]
        result = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
            errors="ignore",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
        )
        if not os.path.exists(output_path) or os.path.getsize(output_path) < 1024:
            raise RuntimeError("提取的音频文件无效")
    
    def _analyze_librosa(
        self,
        audio_path: str,
        callback: Optional[Callable[[int, str], None]] = None,
    ) -> BeatAnalysisResult:
        """使用librosa进行专业节拍检测"""
        import librosa
        
        if callback:
            callback(50, "加载音频...")
        
        y, sr = librosa.load(audio_path, sr=self.config.sample_rate, mono=True)
        
        if callback:
            callback(70, "BPM估计与节拍追踪...")
        
        tempo, beat_frames = librosa.beat.beat_track(
            y=y,
            sr=sr,
            start_bpm=120,
            tightness=100,
            trim=True,
        )
        
        beat_times = librosa.frames_to_time(beat_frames, sr=sr)
        
        if isinstance(tempo, np.ndarray):
            tempo = float(tempo[0]) if len(tempo) > 0 else 120.0
        else:
            tempo = float(tempo)
        
        if callback:
            callback(100, f"节拍检测完成 BPM={tempo:.1f}")
        
        return BeatAnalysisResult(
            bpm=tempo,
            beat_times=beat_times.tolist(),
            confidence=min(1.0, len(beat_times) / max(1, len(y) / sr * tempo / 60)),
            success=True,
            method="librosa",
        )
    
    def _analyze_energy_fallback(
        self,
        audio_path: str,
        callback: Optional[Callable[[int, str], None]] = None,
    ) -> BeatAnalysisResult:
        """能量峰值fallback检测，不依赖librosa"""
        import wave
        import struct
        
        if callback:
            callback(50, "能量峰值检测中...")
        
        try:
            wf = wave.open(audio_path, 'rb')
            n_channels = wf.getnchannels()
            sample_width = wf.getsampwidth()
            framerate = wf.getframerate()
            n_frames = wf.getnframes()
            
            chunk_size = int(framerate * 0.02)
            energies = []
            times = []
            
            for i in range(0, n_frames, chunk_size):
                frames = wf.readframes(chunk_size)
                if sample_width == 2:
                    fmt = f'{len(frames)//2}h'
                    samples = struct.unpack(fmt, frames)
                    energy = np.sqrt(np.mean(np.array(samples, dtype=np.float32)**2))
                else:
                    energy = 0.0
                
                energies.append(float(energy))
                times.append(i / framerate)
            
            wf.close()
            
            energies = np.array(energies)
            times = np.array(times)
            
            if len(energies) == 0:
                return BeatAnalysisResult(bpm=120.0, beat_times=[], success=False)
            
            energies = (energies - energies.min()) / (energies.max() - energies.min() + 1e-8)
            
            window = max(1, int(0.3 / (chunk_size / framerate)))
            smoothed = np.convolve(energies, np.ones(window)/window, mode='same')
            
            threshold = self.config.onset_threshold
            beats = []
            for i in range(1, len(smoothed)-1):
                if smoothed[i] > threshold and smoothed[i] > smoothed[i-1] and smoothed[i] >= smoothed[i+1]:
                    beats.append(float(times[i]))
            
            if len(beats) >= 4:
                intervals = np.diff(beats)
                median_interval = np.median(intervals)
                estimated_bpm = 60.0 / median_interval if median_interval > 0 else 120.0
                
                filtered_beats = []
                last_beat = -10
                min_interval = 60.0 / self.config.max_bpm
                for b in beats:
                    if b - last_beat >= min_interval * 0.8:
                        filtered_beats.append(b)
                        last_beat = b
                
                beats = filtered_beats
            else:
                estimated_bpm = 120.0
            
            if callback:
                callback(100, f"能量峰值检测完成 BPM≈{estimated_bpm:.1f}")
            
            return BeatAnalysisResult(
                bpm=float(estimated_bpm),
                beat_times=beats,
                confidence=0.5,
                success=True,
                method="energy_fallback",
            )
            
        except Exception as e:
            return BeatAnalysisResult(bpm=120.0, success=False)
    
    def align_time_to_nearest_beat(
        self,
        time_point: float,
        beat_result: BeatAnalysisResult,
        max_offset_ms: int = 300,
    ) -> float:
        """将时间点对齐到最近的节拍点"""
        if not beat_result.beat_times:
            return time_point
        
        max_offset = max_offset_ms / 1000.0
        
        closest = None
        min_dist = float('inf')
        
        for bt in beat_result.beat_times:
            dist = abs(bt - time_point)
            if dist < min_dist:
                min_dist = dist
                closest = bt
        
        if closest is not None and min_dist <= max_offset:
            return closest
        
        return time_point
