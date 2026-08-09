"""
Whisper语音识别模块
使用OpenAI Whisper模型进行语音转文字
"""
import subprocess
import json
from pathlib import Path
from typing import List, Dict, Optional, Callable
from utils.logger import logger
from config import CONFIG


class WhisperTranscriber:
    """Whisper语音识别器"""
    
    def __init__(self, model_name: str = None):
        """
        初始化Whisper识别器
        
        Args:
            model_name: 模型名称，默认使用配置中的模型
        """
        self.model_name = model_name or CONFIG.local_models.whisper_model
        self.model = None
        self.ffmpeg_path = CONFIG.ffmpeg_path
        logger.info(f"Whisper识别器初始化: {self.model_name}")
    
    def transcribe(self, video_path: str, language: str = "zh",
                  callback: Optional[Callable] = None) -> List[Dict]:
        """
        转录视频中的语音
        
        Args:
            video_path: 视频文件路径
            language: 语言代码（zh=中文, en=英文）
            callback: 进度回调函数 callback(progress: int, message: str)
        
        Returns:
            转录结果列表 [{"start": float, "end": float, "text": str}, ...]
        """
        logger.info(f"开始语音识别: {video_path}")
        
        if callback:
            callback(0, "正在提取音频...")
        
        try:
            # 1. 提取音频
            audio_path = self._extract_audio(video_path, callback)
            if not audio_path:
                return []
            
            if callback:
                callback(30, "正在加载Whisper模型...")
            
            # 2. 加载模型
            if self.model is None:
                self._load_model()
            
            if callback:
                callback(50, "正在进行语音识别...")
            
            # 3. 转录
            result = self.model.transcribe(
                audio_path,
                language=language,
                verbose=False,
                task="transcribe"
            )
            
            if callback:
                callback(90, "正在整理结果...")
            
            # 4. 整理结果
            segments = []
            for seg in result.get("segments", []):
                segments.append({
                    "start": float(seg.get("start", 0)),
                    "end": float(seg.get("end", 0)),
                    "text": seg.get("text", "").strip()
                })
            
            # 5. 清理临时文件
            try:
                Path(audio_path).unlink()
            except:
                pass
            
            if callback:
                callback(100, f"语音识别完成: {len(segments)}个片段")
            
            logger.info(f"语音识别完成: {len(segments)}个片段")
            return segments
        
        except Exception as e:
            logger.error(f"语音识别失败: {e}")
            if callback:
                callback(100, f"识别失败: {str(e)}")
            return []
    
    def _extract_audio(self, video_path: str, callback: Optional[Callable] = None) -> Optional[str]:
        """提取音频为WAV文件"""
        try:
            # 创建临时目录
            temp_dir = Path("cache/audio")
            temp_dir.mkdir(parents=True, exist_ok=True)
            
            audio_path = str(temp_dir / "temp_audio.wav")
            
            # 使用FFmpeg提取音频
            cmd = [
                self.ffmpeg_path,
                "-i", video_path,
                "-vn",  # 不要视频
                "-acodec", "pcm_s16le",  # 16位PCM
                "-ar", "16000",  # 16kHz采样率（Whisper要求）
                "-ac", "1",  # 单声道
                "-y",  # 覆盖
                audio_path
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300
            )
            
            if result.returncode == 0:
                return audio_path
            else:
                logger.error(f"音频提取失败: {result.stderr}")
                return None
        
        except Exception as e:
            logger.error(f"音频提取异常: {e}")
            return None
    
    def _load_model(self):
        """加载Whisper模型"""
        try:
            import whisper
            import torch
            
            # 检查本地模型路径（支持相对路径和绝对路径）
            model_path = Path(self.model_name)
            if not model_path.is_absolute():
                # 相对路径，基于项目根目录解析
                project_root = Path(__file__).parent.parent
                # 自动添加 models_cache 前缀
                if not str(self.model_name).startswith('models_cache'):
                    model_path = project_root / 'models_cache' / self.model_name
                else:
                    model_path = project_root / self.model_name
            
            if model_path.exists() and model_path.is_dir():
                # 检查是否有 safetensors 格式的模型文件（HuggingFace格式）
                safetensors_files = list(model_path.glob("*.safetensors"))
                pt_files = list(model_path.glob("*.pt")) + list(model_path.glob("*.bin"))
                
                if pt_files:
                    # openai-whisper 原生格式（.pt文件）
                    pt_file = pt_files[0]
                    logger.info(f"加载本地Whisper模型: {pt_file}")
                    self.model = whisper.load_model(str(pt_file))
                elif safetensors_files:
                    # HuggingFace格式，使用transformers加载
                    logger.info(f"检测到HuggingFace格式模型，使用transformers加载")
                    self._use_transformers = True
                    self._model_path = str(model_path)
                    self._load_transformers_model()
                    return
                else:
                    logger.warning(f"本地模型目录无模型文件: {model_path}")
                    logger.info("尝试从网络下载模型...")
                    self.model = whisper.load_model("large-v3")
            else:
                # 尝试加载预训练模型名称
                logger.info(f"加载Whisper模型: {self.model_name}")
                self.model = whisper.load_model(self.model_name)
            
            # 移动到GPU
            if torch.cuda.is_available() and self.model:
                self.model = self.model.to("cuda")
                logger.info("Whisper模型已加载到GPU")
            
            logger.info("Whisper模型加载完成")
        
        except ImportError as e:
            logger.error(f"Whisper未安装: {e}")
            logger.error("请安装: pip install openai-whisper")
            raise
        except Exception as e:
            logger.error(f"模型加载失败: {e}")
            raise
    
    def _load_transformers_model(self):
        """使用transformers加载HuggingFace格式的Whisper模型"""
        try:
            from transformers import pipeline
            import torch
            
            device = "cuda" if torch.cuda.is_available() else "cpu"
            torch_dtype = torch.float16 if torch.cuda.is_available() else torch.float32
            
            logger.info(f"使用transformers加载Whisper模型到{device}...")
            
            self._pipe = pipeline(
                "automatic-speech-recognition",
                model=self._model_path,
                device=device,
                torch_dtype=torch_dtype
            )
            
            logger.info("transformers Whisper模型加载完成")
        
        except Exception as e:
            logger.error(f"transformers模型加载失败: {e}")
            raise
    
    def generate_subtitle(self, video_path: str, output_path: str,
                         language: str = "zh", format: str = "srt",
                         callback: Optional[Callable] = None) -> bool:
        """
        生成字幕文件
        
        Args:
            video_path: 视频文件路径
            output_path: 输出字幕文件路径
            language: 语言代码
            format: 字幕格式（srt/ass）
            callback: 进度回调
        
        Returns:
            是否成功
        """
        try:
            # 1. 转录
            segments = self.transcribe(video_path, language, callback)
            
            if not segments:
                return False
            
            # 2. 生成字幕文件
            if format.lower() == "srt":
                return self._generate_srt(segments, output_path)
            elif format.lower() == "ass":
                return self._generate_ass(segments, output_path)
            else:
                logger.error(f"不支持的字幕格式: {format}")
                return False
        
        except Exception as e:
            logger.error(f"字幕生成失败: {e}")
            return False
    
    def _generate_srt(self, segments: List[Dict], output_path: str) -> bool:
        """生成SRT字幕"""
        try:
            with open(output_path, "w", encoding="utf-8") as f:
                for i, seg in enumerate(segments, 1):
                    start_time = self._format_srt_time(seg["start"])
                    end_time = self._format_srt_time(seg["end"])
                    text = seg["text"]
                    
                    f.write(f"{i}\n")
                    f.write(f"{start_time} --> {end_time}\n")
                    f.write(f"{text}\n\n")
            
            logger.info(f"SRT字幕生成成功: {output_path}")
            return True
        
        except Exception as e:
            logger.error(f"SRT字幕生成失败: {e}")
            return False
    
    def _generate_ass(self, segments: List[Dict], output_path: str) -> bool:
        """生成ASS字幕"""
        try:
            with open(output_path, "w", encoding="utf-8") as f:
                # ASS头部
                f.write("[Script Info]\n")
                f.write("Title: FireClip Generated Subtitle\n")
                f.write("ScriptType: v4.00+\n")
                f.write("WrapStyle: 0\n")
                f.write("PlayResX: 1920\n")
                f.write("PlayResY: 1080\n")
                f.write("\n")
                
                # 样式定义
                f.write("[V4+ Styles]\n")
                f.write("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n")
                f.write("Style: Default,Microsoft YaHei,48,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,2,1,2,10,10,10,1\n")
                f.write("\n")
                
                # 事件
                f.write("[Events]\n")
                f.write("Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
                
                for seg in segments:
                    start_time = self._format_ass_time(seg["start"])
                    end_time = self._format_ass_time(seg["end"])
                    text = seg["text"].replace("\n", "\\N")
                    
                    f.write(f"Dialogue: 0,{start_time},{end_time},Default,,0,0,0,,{text}\n")
            
            logger.info(f"ASS字幕生成成功: {output_path}")
            return True
        
        except Exception as e:
            logger.error(f"ASS字幕生成失败: {e}")
            return False
    
    def _format_srt_time(self, seconds: float) -> str:
        """格式化SRT时间（HH:MM:SS,mmm）"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds % 1) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
    
    def _format_ass_time(self, seconds: float) -> str:
        """格式化ASS时间（H:MM:SS.cc）"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        centis = int((seconds % 1) * 100)
        return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"


# 测试代码
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("用法: python whisper_transcriber.py <video_path> [output.srt]")
        sys.exit(1)
    
    video_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else "output.srt"
    
    transcriber = WhisperTranscriber()
    
    def progress_callback(progress, message):
        print(f"[{progress}%] {message}")
    
    # 生成字幕
    success = transcriber.generate_subtitle(
        video_path, output_path,
        language="zh", format="srt",
        callback=progress_callback
    )
    
    print(f"字幕生成{'成功' if success else '失败'}")
