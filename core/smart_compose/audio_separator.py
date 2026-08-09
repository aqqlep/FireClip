"""
人声分离模块 v1.0
第一阶段：基于频域滤波的轻量人声/伴奏分离，无额外模型依赖
预留深度学习模型加载接口（支持Demucs/Kim_Vocal等后续扩展）
"""
import os
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Optional, Tuple, Callable
from pathlib import Path
import logging

logger = logging.getLogger("FireClip")

try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False
    np = None


@dataclass
class SeparationResult:
    """分离结果"""
    vocals_path: Optional[str] = None
    accompaniment_path: Optional[str] = None
    sample_rate: int = 44100
    success: bool = False
    method: str = "spectral"


class AudioSeparatorConfig:
    """人声分离配置"""
    def __init__(
        self,
        fft_size: int = 2048,
        hop_length: int = 512,
        vocal_freq_range: Tuple[int, int] = (300, 3400),
        accompaniment_freq_low: int = 200,
        use_deep_model: bool = False,
        model_path: Optional[str] = None,
    ):
        self.fft_size = fft_size
        self.hop_length = hop_length
        self.vocal_freq_range = vocal_freq_range
        self.accompaniment_freq_low = accompaniment_freq_low
        self.use_deep_model = use_deep_model
        self.model_path = model_path


class AudioSeparator:
    """人声分离器"""
    
    def __init__(self, config: Optional[AudioSeparatorConfig] = None, ffmpeg_path: str = "ffmpeg"):
        self.config = config or AudioSeparatorConfig()
        self.ffmpeg_path = ffmpeg_path
        self._deep_model = None
    
    def separate(
        self,
        video_path: str,
        output_dir: Optional[str] = None,
        callback: Optional[Callable[[int, str], None]] = None,
    ) -> SeparationResult:
        """
        从视频中提取并分离人声和伴奏
        
        Args:
            video_path: 输入视频路径
            output_dir: 输出目录，默认临时目录
            callback: 进度回调
        
        Returns:
            SeparationResult
        """
        if not NUMPY_AVAILABLE:
            logger.debug("[AudioSeparator] numpy不可用，跳过音频分离")
            return SeparationResult(success=False, method="numpy_unavailable")
            
        if output_dir is None:
            output_dir = tempfile.mkdtemp(prefix="audio_sep_")
        os.makedirs(output_dir, exist_ok=True)
        
        try:
            if callback:
                callback(10, "提取音频中...")
            
            audio_path = os.path.join(output_dir, "original_audio.wav")
            self._extract_audio(video_path, audio_path)
        except Exception as e:
            logger.warning(f"[AudioSeparator] 提取音频失败，跳过分离: {str(e)[:200]}")
            return SeparationResult(success=False, method="extract_failed")
        
        if self.config.use_deep_model and self.config.model_path:
            if callback:
                callback(30, "加载深度学习模型进行分离...")
            return self._separate_deep(audio_path, output_dir, callback)
        else:
            if callback:
                callback(30, "使用频域滤波进行轻量分离...")
            return self._separate_spectral(audio_path, output_dir, callback)
    
    def _extract_audio(self, video_path: str, output_path: str):
        """用ffmpeg提取音频"""
        cmd = [
            self.ffmpeg_path, "-y", "-i", video_path,
            "-vn", "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "2",
            output_path
        ]
        try:
            subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                errors="ignore",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"提取音频失败: {e.stderr}")
    
    def _separate_spectral(
        self,
        audio_path: str,
        output_dir: str,
        callback: Optional[Callable[[int, str], None]] = None,
    ) -> SeparationResult:
        """基于频域滤波的轻量分离"""
        try:
            import librosa
            import soundfile as sf
        except ImportError:
            return self._separate_ffmpeg_basic(audio_path, output_dir, callback)
        
        try:
            if callback:
                callback(50, "加载音频数据...")
            
            y, sr = librosa.load(audio_path, sr=44100, mono=True)
            
            if callback:
                callback(65, "STFT频域变换...")
            
            S = np.abs(librosa.stft(y, n_fft=self.config.fft_size, hop_length=self.config.hop_length))
            
            freq_bins = librosa.fft_frequencies(sr=sr, n_fft=self.config.fft_size)
            
            vocal_mask = np.zeros_like(S, dtype=bool)
            low_v, high_v = self.config.vocal_freq_range
            for i, f in enumerate(freq_bins):
                if low_v <= f <= high_v:
                    vocal_mask[i, :] = True
            
            S_vocal = S * vocal_mask * 0.8
            S_accomp = S * (~vocal_mask) * 1.2
            
            low_accomp = self.config.accompaniment_freq_low
            for i, f in enumerate(freq_bins):
                if f < low_accomp:
                    S_accomp[i, :] = S[i, :] * 0.5
            
            if callback:
                callback(80, "逆变换生成音轨...")
            
            y_vocal = librosa.griffinlim(S_vocal, n_fft=self.config.fft_size, hop_length=self.config.hop_length)
            y_accomp = librosa.griffinlim(S_accomp, n_fft=self.config.fft_size, hop_length=self.config.hop_length)
            
            vocals_path = os.path.join(output_dir, "vocals.wav")
            accompaniment_path = os.path.join(output_dir, "accompaniment.wav")
            
            sf.write(vocals_path, y_vocal, sr)
            sf.write(accompaniment_path, y_accomp, sr)
            
            if callback:
                callback(100, "人声分离完成")
            
            return SeparationResult(
                vocals_path=vocals_path,
                accompaniment_path=accompaniment_path,
                sample_rate=sr,
                success=True,
                method="spectral_librosa",
            )
            
        except Exception as e:
            return self._separate_ffmpeg_basic(audio_path, output_dir, callback)
    
    def _separate_ffmpeg_basic(
        self,
        audio_path: str,
        output_dir: str,
        callback: Optional[Callable[[int, str], None]] = None,
    ) -> SeparationResult:
        """
        最简ffmpeg实现：使用高低通滤波粗分离
        当librosa不可用时fallback
        """
        vocals_path = os.path.join(output_dir, "vocals.wav")
        accompaniment_path = os.path.join(output_dir, "accompaniment.wav")
        
        if callback:
            callback(70, "ffmpeg滤波分离中...")
        
        vocal_cmd = [
            self.ffmpeg_path, "-y", "-i", audio_path,
            "-af", f"highpass=f={self.config.vocal_freq_range[0]},lowpass=f={self.config.vocal_freq_range[1]}",
            vocals_path
        ]
        
        accomp_cmd = [
            self.ffmpeg_path, "-y", "-i", audio_path,
            "-af", f"lowpass=f={self.config.accompaniment_freq_low},volume=2",
            accompaniment_path
        ]
        
        try:
            subprocess.run(vocal_cmd, check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            subprocess.run(accomp_cmd, check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            
            if callback:
                callback(100, "基础人声分离完成")
            
            return SeparationResult(
                vocals_path=vocals_path,
                accompaniment_path=accompaniment_path,
                sample_rate=44100,
                success=True,
                method="ffmpeg_basic",
            )
        except Exception:
            return SeparationResult(success=False)
    
    def _separate_deep(
        self,
        audio_path: str,
        output_dir: str,
        callback: Optional[Callable[[int, str], None]] = None,
    ) -> SeparationResult:
        """深度学习模型分离（预留接口，待后续扩展Demucs/Kim_Vocal）"""
        return SeparationResult(success=False, method="deep_model_not_loaded")
    
    def cleanup(self, result: SeparationResult):
        """清理临时文件"""
        for p in [result.vocals_path, result.accompaniment_path]:
            if p and os.path.exists(p):
                try:
                    os.unlink(p)
                except Exception:
                    pass
