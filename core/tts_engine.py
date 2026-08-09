"""
TTS引擎模块
负责文本转语音，支持多种TTS引擎
"""
import subprocess
import os
from typing import Optional
from pathlib import Path
from utils.logger import logger
from config import CONFIG


class TTSEngine:
    """TTS引擎"""

    # 中文语音映射表（edge-tts 全部中文声音）
    VOICE_MAP = {
        "激昂热血(男)": "zh-CN-YunjianNeural",
        "阳光活泼(男)": "zh-CN-YunxiNeural",
        "专业沉稳(男)": "zh-CN-YunyangNeural",
        "可爱少年(男)": "zh-CN-YunxiaNeural",
        "温暖知性(女)": "zh-CN-XiaoxiaoNeural",
        "活泼俏皮(女)": "zh-CN-XiaoyiNeural",
        "东北方言(女)": "zh-CN-liaoning-XiaobeiNeural",
        "陕西方言(女)": "zh-CN-shaanxi-XiaoniNeural",
    }

    # 显示名列表（保持顺序）
    VOICE_NAMES = list(VOICE_MAP.keys())

    def __init__(self):
        """初始化TTS引擎"""
        self.engine = CONFIG.tts_engine
        self.voice_male = CONFIG.tts_voice_male
        self.voice_female = CONFIG.tts_voice_female
        logger.info(f"TTS引擎初始化完成: {self.engine}")

    def synthesize(self, text: str, output_path: str,
                  voice: str = "male", speed: float = 1.0) -> bool:
        """
        文本转语音

        Args:
            text: 要转换的文本
            output_path: 输出音频路径
            voice: 声音 — 支持三种格式:
                   "male"/"female" — 向后兼容，映射为默认男/女声
                   "zh-CN-YunxiNeural" — 直接指定 edge-tts voice name
                   "激昂热血(男)" — 显示名，从 VOICE_MAP 查找
            speed: 语速 (0.5-2.0)

        Returns:
            是否成功
        """
        # 解析 voice 参数为实际的 voice_name
        voice_name = self._resolve_voice(voice)

        try:
            if self.engine == "edge-tts":
                return self._synthesize_edge_tts(text, output_path, voice_name, speed)
            elif self.engine == "ChatTTS":
                return self._synthesize_chattts(text, output_path, voice_name, speed)
            elif self.engine == "CosyVoice":
                return self._synthesize_cosyvoice(text, output_path, voice_name, speed)
            else:
                logger.error(f"不支持的TTS引擎: {self.engine}")
                return False

        except Exception as e:
            logger.error(f"TTS合成失败: {e}")
            return False

    def _resolve_voice(self, voice: str) -> str:
        """将 voice 参数解析为实际的 voice_name"""
        # 1. 向后兼容: male/female
        if voice == "male":
            return self.voice_male
        if voice == "female":
            return self.voice_female
        # 2. 显示名查找
        if voice in self.VOICE_MAP:
            return self.VOICE_MAP[voice]
        # 3. 已经是 voice_name（包含 Neural 或 -CN-）
        if "Neural" in voice or "-CN-" in voice:
            return voice
        # 4. 兜底: 默认男声
        logger.warning(f"未知声音 '{voice}'，使用默认男声")
        return self.voice_male
    
    def _synthesize_edge_tts(self, text: str, output_path: str,
                            voice: str = "male", speed: float = 1.0) -> bool:
        """
        使用edge-tts合成语音
        
        优先使用 edge_tts Python API（更可靠），回退到命令行
        
        注意：不在 QThread 中使用 asyncio.run()，避免与 Qt 事件循环冲突导致死锁
        """
        voice_name = self.voice_male if voice == "male" else self.voice_female
        rate_str = f"{int((speed - 1) * 100):+d}%"

        # ---- 方案1: 显式事件循环 + 超时（避免 asyncio.run() 在 QThread 中的死锁问题）----
        try:
            import edge_tts as _edge_tts
            import asyncio

            async def _do_synthesize():
                communicate = _edge_tts.Communicate(text, voice_name, rate=rate_str)
                await asyncio.wait_for(communicate.save(output_path), timeout=30)

            # 显式创建新事件循环，避免与 Qt 事件循环冲突
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(_do_synthesize())
            finally:
                loop.close()

            if os.path.exists(output_path) and os.path.getsize(output_path) > 100:
                logger.info(f"edge-tts合成成功: {output_path}")
                return True
            else:
                logger.warning("edge-tts API 合成文件异常，回退命令行")
        except ImportError:
            logger.warning("edge_tts 包未安装，尝试命令行方式")
        except asyncio.TimeoutError:
            logger.warning("edge-tts API 合成超时(30s)，回退命令行")
        except Exception as e:
            logger.warning(f"edge-tts API 合成异常: {e}，回退命令行")

        # ---- 方案2: 命令行回退（subprocess 有超时，不依赖 asyncio）----
        try:
            # 确保 PATH 包含 Scripts 目录（edge-tts.exe 所在）和 ffmpeg 目录
            env = os.environ.copy()
            scripts_dir = str(Path(__file__).parent.parent / "python-embed" / "Scripts")
            if scripts_dir not in env.get("PATH", ""):
                env["PATH"] = scripts_dir + os.pathsep + env.get("PATH", "")
            if CONFIG.ffmpeg_path and CONFIG.ffmpeg_path != "ffmpeg":
                ffmpeg_dir = str(Path(CONFIG.ffmpeg_path).parent)
                if ffmpeg_dir not in env.get("PATH", ""):
                    env["PATH"] = ffmpeg_dir + os.pathsep + env.get("PATH", "")

            cmd = [
                "edge-tts",
                "--text", text,
                "--voice", voice_name,
                "--rate", f"{int((speed - 1) * 100):+d}%",
                "--write-media", output_path
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
                env=env
            )

            if result.returncode == 0 and os.path.exists(output_path):
                logger.info(f"edge-tts CLI合成成功: {output_path}")
                return True
            else:
                logger.error(f"edge-tts CLI合成失败: {result.stderr}")
                return False

        except subprocess.TimeoutExpired:
            logger.error("edge-tts CLI合成超时(60s)")
            return False
        except Exception as e:
            logger.error(f"edge-tts命令行合成异常: {e}")
            return False
    
    def _synthesize_chattts(self, text: str, output_path: str,
                           voice: str = "male", speed: float = 1.0) -> bool:
        """
        使用ChatTTS合成语音
        
        Args:
            text: 文本
            output_path: 输出路径
            voice: 声音类型
            speed: 语速
        
        Returns:
            是否成功
        """
        try:
            import torch
            import ChatTTS
            import torchaudio
            
            # 检查模型路径（支持相对路径和绝对路径）
            model_path = Path(CONFIG.local_models.tts_model)
            if not model_path.is_absolute():
                # 相对路径，基于项目根目录解析
                project_root = Path(__file__).parent.parent
                # 自动添加 models_cache 前缀
                if not str(CONFIG.local_models.tts_model).startswith('models_cache'):
                    model_path = project_root / 'models_cache' / CONFIG.local_models.tts_model
                else:
                    model_path = project_root / CONFIG.local_models.tts_model
            
            if not model_path.exists():
                logger.warning(f"ChatTTS模型路径不存在: {model_path}")
                logger.warning("使用edge-tts作为备选")
                return self._synthesize_edge_tts(text, output_path, voice, speed)
            
            # 懒加载模型
            if not hasattr(self, '_chattts_model') or self._chattts_model is None:
                logger.info("正在加载ChatTTS模型...")
                self._chattts_model = ChatTTS.Chat()
                self._chattts_model.load(
                    source='custom',
                    custom_path=model_path,
                    compile=False
                )
                logger.info("ChatTTS模型加载完成")
            
            # 选择音色（使用不同的seed）
            torch.manual_seed(1 if voice == "male" else 2)
            
            # 生成语音
            wavs = self._chattts_model.infer([text], use_decoder=True)
            
            # 保存音频
            audio = torch.from_numpy(wavs[0])
            torchaudio.save(output_path, audio, 24000)
            
            logger.info(f"ChatTTS合成成功: {output_path}")
            return True
        
        except ImportError as e:
            logger.warning(f"ChatTTS依赖缺失: {e}")
            logger.warning("使用edge-tts作为备选")
            return self._synthesize_edge_tts(text, output_path, voice, speed)
        except Exception as e:
            logger.error(f"ChatTTS合成异常: {e}")
            return False
    
    def _synthesize_cosyvoice(self, text: str, output_path: str,
                              voice: str = "male", speed: float = 1.0) -> bool:
        """
        使用CosyVoice合成语音
        
        Args:
            text: 文本
            output_path: 输出路径
            voice: 声音类型
            speed: 语速
        
        Returns:
            是否成功
        """
        try:
            # 这里需要集成CosyVoice
            # 由于CosyVoice需要大量依赖，这里提供框架代码
            
            # TODO: 集成CosyVoice
            # 示例代码：
            """
            from cosyvoice.cli.cosyvoice import CosyVoice
            
            cosyvoice = CosyVoice('pretrained_models/CosyVoice-300M')
            
            # 选择声音
            voice_name = "male" if voice == "male" else "female"
            
            # 生成语音
            output = cosyvoice.inference_sft(text, voice_name)
            
            # 保存音频
            import soundfile as sf
            sf.write(output_path, output['tts_speech'], 22050)
            """
            
            # 临时使用edge-tts作为备选
            logger.warning("CosyVoice尚未实现，使用edge-tts作为备选")
            return self._synthesize_edge_tts(text, output_path, voice, speed)
        
        except Exception as e:
            logger.error(f"CosyVoice合成异常: {e}")
            return False
    
    def synthesize_segments(self, segments: list, output_dir: str,
                           voice: str = "male", speed: float = 1.0) -> list:
        """
        批量合成多个片段
        
        Args:
            segments: 片段列表 [{"text": str, "id": str}, ...]
            output_dir: 输出目录
            voice: 声音类型
            speed: 语速
        
        Returns:
            生成的音频文件路径列表
        """
        try:
            # 创建输出目录
            Path(output_dir).mkdir(parents=True, exist_ok=True)
            
            audio_files = []
            
            for i, seg in enumerate(segments):
                text = seg.get("text", "")
                seg_id = seg.get("id", f"segment_{i}")
                
                if not text:
                    continue
                
                output_path = os.path.join(output_dir, f"{seg_id}.mp3")
                
                if self.synthesize(text, output_path, voice, speed):
                    audio_files.append(output_path)
                    logger.info(f"片段 {i+1}/{len(segments)} 合成成功")
                else:
                    logger.warning(f"片段 {i+1}/{len(segments)} 合成失败")
            
            logger.info(f"批量合成完成: {len(audio_files)}/{len(segments)}")
            return audio_files
        
        except Exception as e:
            logger.error(f"批量合成失败: {e}")
            return []


# 测试代码
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 3:
        print("用法: python tts_engine.py <text> <output.mp3> [voice] [speed]")
        print("voice: male / female")
        print("speed: 0.5 - 2.0")
        sys.exit(1)
    
    text = sys.argv[1]
    output = sys.argv[2]
    voice = sys.argv[3] if len(sys.argv) > 3 else "male"
    speed = float(sys.argv[4]) if len(sys.argv) > 4 else 1.0
    
    engine = TTSEngine()
    success = engine.synthesize(text, output, voice, speed)
    
    print(f"TTS合成{'成功' if success else '失败'}")
    if success:
        print(f"输出文件: {output}")
