"""
v4.0 新增: 场景分类器系统（双模式：CLIP轻量 / Qwen2-VL高精度）

核心能力:
1. CLIPSceneClassifier - 轻量零样本分类（7种场景）
2. Qwen2VLSceneClassifier - 高精度视觉理解（A高燃/B高光/C其他）
3. WhisperASRHelper - 语音转文字，辅助判断搞笑/情感场景
4. UnifiedSceneClassifier - 统一接口，根据配置自动选择

筛选输出:
- "高燃" (hot_fire): 古装打斗/仙侠特效/动漫大招/爆炸大场面
- "高光" (highlight): 搞笑喜剧/甜蜜浪漫/情感高潮
- "排除" (exclude): 普通对话/空镜/过渡/片头片尾
"""
import subprocess
import os
import tempfile
import numpy as np
from pathlib import Path
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass
from utils.logger import logger
from config import CONFIG


@dataclass
class SceneClassification:
    """单片段的场景分类结果"""
    start_time: float
    end_time: float
    main_category: str          # "hot_fire" / "highlight" / "exclude"
    sub_type: str               # 细分类型（古装打斗/仙侠特效/搞笑等）
    confidence: float           # 置信度 0-1
    reason: str = ""            # AI 给出的理由
    raw_text: str = ""          # 原始模型输出（用于调试）
    frames_analyzed: int = 0    # 分析了多少帧
    asr_text: str = ""          # Whisper 识别的台词（如有）


class Qwen2VLSceneClassifier:
    """
    Qwen2-VL 7B 场景分类器（高精度主力）

    特点:
    - 支持多帧输入（一个片段抽 3-5 帧，模型可以看到完整的"前后文"）
    - 对中文场景理解能力远超过 CLIP
    - 可以区分"快切对话镜头" vs "真正的打斗场景"

    提示词策略:
    - 用简洁中文三分类（A高燃/B高光/C其他）
    - 要求结构化输出，便于正则解析
    - 给明确的分类标准，降低歧义
    """

    # 提示词（中文，Qwen2-VL 对中文理解极好）
    CLASSIFICATION_PROMPT = """你是一个电影场景分析专家。请仔细分析以下视频画面（共 {num_frames} 张图片，按时间顺序排列，来自同一个视频片段），判断它属于哪一类场景。

【A. 高燃动作场景】（激烈的打斗/爆炸/追逐）
   - 古装武打：人物手持兵器（刀枪剑棍）正在打斗，或激烈近身拳脚对决
   - 仙侠奇幻：有魔法能量/剑气/法术爆炸/发光特效
   - 动漫大招：夸张的能量爆发/冲击波/必杀技
   - 爆炸大场面：大规模爆炸/战争场景/建筑破坏
   - 紧张追逐：汽车/摩托车/人物高速追击

【B. 高光情感场景】（搞笑/甜蜜/情感高潮）
   - 搞笑喜剧：人物做出夸张表情/动作，有明显逗趣意味
   - 甜蜜浪漫：情侣亲密互动（牵手/拥抱/亲吻），温暖氛围
   - 情感高潮：人物感动落泪/告别/重逢，情绪强烈

【C. 其他场景】（普通对话/空镜/过渡）
   - 普通对话：人物在室内/室外交谈，没有动作特效
   - 空镜/风景：静态画面、远景、自然风景
   - 过渡画面：过场/片头/片尾/文字屏幕

请严格按照以下格式输出（只输出这三行，不要输出其他任何文字）：
分类: A
置信度: 0.8
理由: 两个角色持剑打斗"""

    def __init__(self, model_path: str = None):
        """初始化"""
        self.model_path = model_path or "models_cache/Qwen/Qwen2-VL-7B-Instruct"
        self._model = None
        self._processor = None
        self._dependency_warned = False

    def _load_model(self):
        """延迟加载模型（第一次推理时才加载，约14GB显存）"""
        if self._model is not None:
            return

        try:
            import torch
            from transformers import Qwen2VLForConditionalGeneration, AutoProcessor
            from qwen_vl_utils import process_vision_info

            # 解析模型路径
            model_path = Path(self.model_path)
            if not model_path.is_absolute():
                project_root = Path(__file__).parent.parent
                model_path = project_root / self.model_path

            if not model_path.exists():
                logger.error(f"Qwen2-VL 模型路径不存在: {model_path}")
                raise FileNotFoundError(f"模型路径不存在: {model_path}")

            logger.info(f"正在加载 Qwen2-VL 模型: {model_path}")
            logger.info(f"  预计显存占用: ~12-14GB (FP16)")

            torch.cuda.empty_cache()
            try:
                torch.cuda.set_per_process_memory_fraction(0.85)
            except Exception:
                pass

            device = "cuda"
            dtype = torch.float16

            self._model = Qwen2VLForConditionalGeneration.from_pretrained(
                str(model_path),
                torch_dtype=dtype,
                device_map="cuda",
                trust_remote_code=True,
                local_files_only=True,
                low_cpu_mem_usage=True,
            )

            self._processor = AutoProcessor.from_pretrained(
                str(model_path),
                trust_remote_code=True,
                local_files_only=True,
            )

            logger.info("✅ Qwen2-VL 模型加载完成")

        except ImportError as e:
            if not self._dependency_warned:
                logger.warning(f"Qwen2-VL 依赖缺失: {e}")
                logger.warning("请确保 transformers 和 qwen-vl-utils 已安装")
                self._dependency_warned = True
            raise
        except Exception as e:
            logger.error(f"Qwen2-VL 模型加载失败: {e}")
            raise

    def classify_segment(self, video_path: str, start_time: float, end_time: float,
                         num_frames: int = 4) -> Optional[SceneClassification]:
        """
        对单个时间区间做场景分类

        步骤:
        1. 在区间内均匀抽取 num_frames 帧（默认4帧，让模型看到完整动作）
        2. 把多帧图片喂给 Qwen2-VL
        3. 解析结构化输出（分类/置信度/理由）
        """
        try:
            self._load_model()
        except Exception as e:
            logger.debug(f"Qwen2-VL 未加载，跳过: {e}")
            return None

        # 1. 抽取关键帧
        frame_files = self._extract_key_frames(video_path, start_time, end_time, num_frames)
        if not frame_files:
            logger.debug(f"抽帧失败 [{start_time:.1f}-{end_time:.1f}s]")
            return None

        try:
            # 2. 准备多图输入（Qwen2-VL 原生支持多图）
            result = self._run_multi_frame_inference(frame_files, start_time, end_time, len(frame_files))
            return result
        finally:
            # 3. 清理临时文件
            for f in frame_files:
                try:
                    Path(f).unlink()
                except Exception:
                    pass

    def classify_multiple_segments(self, video_path: str,
                                    segments: List[Tuple[float, float]],
                                    progress_callback=None) -> List[SceneClassification]:
        """批量分类多个片段"""
        results = []
        total = len(segments)

        for i, (start, end) in enumerate(segments):
            if progress_callback:
                progress = int((i / max(total, 1)) * 100)
                progress_callback(progress, f"AI场景分析: {i+1}/{total}")

            result = self.classify_segment(video_path, start, end, num_frames=4)
            if result:
                results.append(result)

        if progress_callback:
            progress_callback(100, f"AI场景分析完成: {len(results)}/{total}")

        return results

    # ================= 内部方法 =================

    def _extract_key_frames(self, video_path: str, start_time: float, end_time: float,
                            num_frames: int) -> List[str]:
        """从视频抽取关键帧，返回临时图片文件路径列表"""
        duration = max(end_time - start_time, 1.0)
        step = duration / (num_frames + 1)
        target_times = [start_time + step * (i + 1) for i in range(num_frames)]

        frame_files = []
        try:
            ffmpeg_path = CONFIG.ffmpeg_path
            if not ffmpeg_path or not os.path.exists(ffmpeg_path):
                ffmpeg_path = "ffmpeg"

            for i, t in enumerate(target_times):
                tmp_path = os.path.join(tempfile.gettempdir(), f"qwen_frame_{os.getpid()}_{i}_{int(t*1000)}.jpg")

                cmd = [
                    ffmpeg_path, "-y",
                    "-ss", f"{t:.3f}",
                    "-i", video_path,
                    "-vframes", "1",
                    "-vf", "scale=512:512",
                    "-q:v", "2",
                    tmp_path
                ]

                result = subprocess.run(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=8,
                )

                if result.returncode == 0 and os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 0:
                    frame_files.append(tmp_path)
                else:
                    # 退一步：试着把时间点往前移一点
                    t2 = max(start_time, t - 0.5)
                    cmd2 = cmd.copy()
                    cmd2[3] = f"{t2:.3f}"
                    cmd2[-1] = tmp_path
                    try:
                        subprocess.run(cmd2, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8)
                        if os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 0:
                            frame_files.append(tmp_path)
                    except Exception:
                        pass

        except Exception as e:
            logger.debug(f"抽帧失败: {e}")

        return frame_files

    def _run_multi_frame_inference(self, frame_files: List[str],
                                   start_time: float, end_time: float,
                                   num_frames_total: int) -> Optional[SceneClassification]:
        """用 Qwen2-VL 对多帧图片进行推理"""
        try:
            import torch
            from qwen_vl_utils import process_vision_info

            # 1. 准备消息：多张图片 + 分类提示词
            content_list = []
            for f in frame_files:
                content_list.append({"type": "image", "image": f"file://{f}"})

            prompt_text = self.CLASSIFICATION_PROMPT.format(num_frames=len(frame_files))
            content_list.append({"type": "text", "text": prompt_text})

            messages = [{"role": "user", "content": content_list}]

            # 2. 处理输入
            text = self._processor.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )
            image_inputs, video_inputs = process_vision_info(messages)

            inputs = self._processor(
                text=[text],
                images=image_inputs,
                videos=video_inputs,
                padding=True,
                return_tensors="pt",
            )
            inputs = inputs.to("cuda")

            # 3. 生成（控制长度，降低随机性）
            with torch.no_grad():
                generated_ids = self._model.generate(
                    **inputs,
                    max_new_tokens=128,      # 短输出，够三行就行
                    temperature=0.1,        # 低随机性，结果稳定
                    do_sample=False,        # 贪心搜索，最稳定
                    pad_token_id=self._processor.tokenizer.pad_token_id,
                    eos_token_id=self._processor.tokenizer.eos_token_id,
                )

            # 4. 解码
            generated_ids_trimmed = [
                out_ids[len(in_ids):] for in_ids, out_ids in zip(inputs.input_ids, generated_ids)
            ]
            output_text = self._processor.batch_decode(
                generated_ids_trimmed, skip_special_tokens=True, clean_up_tokenization_spaces=False
            )[0].strip()

            # 5. 解析结构化输出
            return self._parse_structured_output(output_text, start_time, end_time, len(frame_files))

        except Exception as e:
            logger.debug(f"Qwen2-VL 推理失败: {e}")
            return None

    def _parse_structured_output(self, output_text: str,
                                 start_time: float, end_time: float,
                                 frames_analyzed: int) -> SceneClassification:
        """
        解析模型的结构化输出

        期望格式:
            分类: A
            置信度: 0.85
            理由: 两个角色持剑打斗

        如果格式不符合，做容错解析
        """
        # 默认值
        category = "exclude"
        sub_type = "无法确定"
        confidence = 0.3
        reason = ""

        lines = output_text.split("\n")

        for line in lines:
            line = line.strip()

            # 解析分类
            if line.startswith("分类") or line.startswith("类别") or "分类:" in line:
                for key in ["A", "高燃", "打斗", "动作"]:
                    if key in line:
                        category = "hot_fire"
                        break
                if category == "exclude":
                    for key in ["B", "高光", "搞笑", "甜蜜", "情感"]:
                        if key in line:
                            category = "highlight"
                            break
                if category == "exclude" and ("C" in line or "其他" in line or "排除" in line):
                    category = "exclude"

            # 解析置信度
            if line.startswith("置信度") or "置信度" in line or "信心" in line:
                try:
                    # 提取数字
                    for part in line.replace(":", " ").split():
                        try:
                            val = float(part)
                            if 0 <= val <= 1:
                                confidence = val
                                break
                        except ValueError:
                            continue
                except Exception:
                    pass

            # 解析理由
            if line.startswith("理由") or "理由" in line or "原因" in line or "说明" in line:
                idx = line.find(":")
                if idx > 0:
                    reason = line[idx + 1:].strip()

        # 容错：如果分类没解析出来，检查理由里的关键词
        if category == "exclude" and not reason:
            lower_text = output_text.lower()
            if any(k in output_text for k in ["打斗", "打斗", "爆炸", "追逐", "打斗", "特效", "剑气", "魔法", "动作"]):
                category = "hot_fire"
                confidence = 0.4
            elif any(k in output_text for k in ["搞笑", "笑", "甜蜜", "吻", "拥抱", "哭", "感动", "泪"]):
                category = "highlight"
                confidence = 0.4

        # 细分类型（基于理由文本或分类判断）
        sub_type = self._infer_sub_type(category, reason, output_text)

        return SceneClassification(
            start_time=start_time,
            end_time=end_time,
            main_category=category,
            sub_type=sub_type,
            confidence=confidence,
            reason=reason,
            raw_text=output_text,
            frames_analyzed=frames_analyzed,
        )

    def _infer_sub_type(self, category: str, reason: str, raw_text: str) -> str:
        """根据模型输出文本，推断细分类型"""
        text = reason + " " + raw_text

        if category == "hot_fire":
            if "仙侠" in text or "魔法" in text or "能量" in text or "剑气" in text or "特效" in text or "法术" in text:
                return "仙侠特效"
            if "动漫" in text or "必杀" in text or "大招" in text:
                return "动漫大招"
            if "爆炸" in text or "战争" in text or "破坏" in text:
                return "爆炸大场面"
            if "追逐" in text or "追" in text or "跑" in text or "车" in text:
                return "紧张追逐"
            return "古装打斗"

        elif category == "highlight":
            if "笑" in text or "搞笑" in text or "喜剧" in text or "逗" in text:
                return "搞笑喜剧"
            if "吻" in text or "抱" in text or "情侣" in text or "甜蜜" in text or "浪漫" in text:
                return "甜蜜浪漫"
            if "哭" in text or "泪" in text or "感动" in text or "告别" in text or "重逢" in text:
                return "情感高潮"
            return "高光时刻"

        return "普通场景"


class WhisperASRHelper:
    """
    Whisper 语音转文字辅助类

    用于:
    - 识别搞笑场景的台词（"哈哈哈"、搞笑台词模式）
    - 识别情感场景的对话（有助于区分"对白" vs "情感高潮对白"）
    - 辅助判断纯对话场景（对白多但无动作→排除）

    注意: Whisper-large-v3 约 6GB 显存
    """

    def __init__(self, model_path: str = None):
        self.model_path = model_path or "models_cache/openai-mirror/whisper-large-v3"
        self._model = None
        self._dependency_warned = False

    def _load_model(self):
        if self._model is not None:
            return

        try:
            import torch
            from transformers import WhisperForConditionalGeneration, WhisperProcessor

            model_path = Path(self.model_path)
            if not model_path.is_absolute():
                project_root = Path(__file__).parent.parent
                model_path = project_root / self.model_path

            if not model_path.exists():
                logger.warning(f"Whisper 模型路径不存在: {model_path}")
                raise FileNotFoundError(f"Whisper 模型不存在")

            logger.info("正在加载 Whisper 语音识别模型...")

            self._processor = WhisperProcessor.from_pretrained(
                str(model_path), local_files_only=True
            )
            self._model = WhisperForConditionalGeneration.from_pretrained(
                str(model_path), torch_dtype=torch.float16, local_files_only=True
            ).to("cuda")

            logger.info("✅ Whisper 模型加载完成")

        except Exception as e:
            if not self._dependency_warned:
                logger.warning(f"Whisper 加载失败: {e}")
                self._dependency_warned = True
            raise

    def transcribe_segment(self, video_path: str, start_time: float, end_time: float,
                           max_duration: float = 30.0) -> str:
        """
        提取单个片段的语音并转文字

        返回: 识别到的文本字符串（空字符串表示无台词或失败）
        """
        try:
            self._load_model()
        except Exception:
            return ""

        duration = min(end_time - start_time, max_duration)
        if duration < 0.5:
            return ""

        # 1. 用 FFmpeg 提取音频 → 临时 wav
        tmp_audio = os.path.join(tempfile.gettempdir(),
                                  f"whisper_audio_{os.getpid()}_{int(start_time*1000)}_{int(end_time*1000)}.wav")
        try:
            ffmpeg_path = CONFIG.ffmpeg_path or "ffmpeg"

            cmd = [
                ffmpeg_path, "-y",
                "-ss", f"{start_time:.3f}",
                "-t", f"{duration:.3f}",
                "-i", video_path,
                "-vn", "-ac", "1", "-ar", "16000",
                "-f", "wav", tmp_audio
            ]

            result = subprocess.run(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30
            )

            if result.returncode != 0 or not os.path.exists(tmp_audio) or os.path.getsize(tmp_audio) < 1024:
                return ""

            # 2. 读取音频并转文字
            import torch
            import wave
            import numpy as np

            with wave.open(tmp_audio, "rb") as wf:
                audio_data = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32)
                audio_data = audio_data / 32768.0
                sr = wf.getframerate()

            # 如果音频 RMS 能量太低，直接跳过（节省时间）
            rms = np.sqrt(np.mean(audio_data ** 2))
            if rms < 0.01:
                return ""

            # 3. Whisper 推理
            try:
                with torch.no_grad():
                    input_features = self._processor(
                        audio_data, sampling_rate=sr, return_tensors="pt"
                    ).input_features.to("cuda").half()

                    predicted_ids = self._model.generate(
                        input_features,
                        language="zh",
                        task="transcribe",
                        max_new_tokens=100,
                        temperature=0,
                    )[0]

                transcription = self._processor.decode(predicted_ids, skip_special_tokens=True).strip()
                return transcription
            except Exception as e:
                logger.debug(f"Whisper 推理失败: {e}")
                return ""

        finally:
            if os.path.exists(tmp_audio):
                try:
                    os.remove(tmp_audio)
                except Exception:
                    pass

    # ================= 辅助: 台词语义检测 =================

    COMEDY_KEYWORDS = ["哈哈", "哈哈哈哈", "笑死", "搞笑", "逗", "笑", "滑稽"]
    EMOTION_KEYWORDS = ["我爱", "喜欢你", "对不起", "再见", "永远", "别哭", "等你", "感动",
                        "眼泪", "哭", "泪", "抱", "吻", "亲爱的"]

    def detect_emotion_tone(self, text: str) -> Tuple[str, float]:
        """
        简单的台词情感检测

        返回 (emotion_type, score)
        emotion_type: "comedy" / "emotion" / "neutral"
        score: 0-1
        """
        if not text:
            return ("neutral", 0.0)

        text_lower = text.lower()

        comedy_hits = sum(1 for k in self.COMEDY_KEYWORDS if k in text_lower)
        emotion_hits = sum(1 for k in self.EMOTION_KEYWORDS if k in text)

        max_len = max(len(text_lower) / 20, 1)

        comedy_score = min(1.0, comedy_hits / max_len)
        emotion_score = min(1.0, emotion_hits / max_len)

        if comedy_score > emotion_score and comedy_score > 0.2:
            return ("comedy", comedy_score)
        elif emotion_score > comedy_score and emotion_score > 0.2:
            return ("emotion", emotion_score)
        else:
            return ("neutral", max(comedy_score, emotion_score))


# ==================== 统一分类接口（给 three_stage_filter 调用） ====================

class UnifiedSceneClassifier:
    """
    统一场景分类器入口

    根据配置选择:
    - 高精度: Qwen2-VL 7B + Whisper
    - 轻量速度: CLIP
    - 混合: 先用 CLIP 快速过滤明显误判，对边缘情况用 Qwen2-VL

    输出:
    - "hot_fire" (高燃)
    - "highlight" (高光)
    - "exclude" (排除)
    """

    def __init__(self, use_qwen2vl: bool = True, use_whisper: bool = True):
        self.use_qwen2vl = use_qwen2vl
        self.use_whisper = use_whisper

        self.qwen2vl = Qwen2VLSceneClassifier() if use_qwen2vl else None
        self.clip = CLIPSceneClassifier() if not use_qwen2vl else None  # 如果启用 Qwen2-VL，就不用 CLIP（Qwen2-VL 效果更好）
        self.whisper = WhisperASRHelper() if use_whisper else None

        # 最小置信度（低于这个值的"高燃/高光"也排除，宁错过不犯错）
        self.min_confidence = 0.35

        logger.info(f"统一场景分类器初始化: Qwen2-VL={use_qwen2vl}, Whisper={use_whisper}")

    def classify(self, video_path: str, start_time: float, end_time: float) -> Optional[SceneClassification]:
        """对单个片段做分类"""
        result = None

        # 1. 视觉分类（Qwen2-VL 优先，否则 CLIP）
        if self.qwen2vl:
            result = self.qwen2vl.classify_segment(video_path, start_time, end_time, num_frames=4)
        elif self.clip:
            clip_result = self.clip.classify_segment(video_path, start_time, end_time, num_frames=3)
            if clip_result:
                # CLIP 对搞笑/情感场景几乎无识别能力，一律设 exclude（由 Whisper 补）
                if clip_result.main_category != "hot_fire":
                    clip_result.main_category = "exclude"
                result = clip_result

        # 2. 语音辅助（如果启用 Whisper 且 Whisper 可用）
        if self.whisper and result is not None:
            try:
                asr_text = self.whisper.transcribe_segment(video_path, start_time, end_time)
                if asr_text:
                    result.asr_text = asr_text[:200] if len(asr_text) > 200 else asr_text

                    # 台词情感检测
                    emotion, score = self.whisper.detect_emotion_tone(asr_text)

                    # 如果视觉分类是 exclude，但台词明显是 comedy/emotion → 升级为 highlight
                    if result.main_category == "exclude" and score > 0.3:
                        if emotion == "comedy":
                            result.main_category = "highlight"
                            result.sub_type = "搞笑喜剧（台词识别）"
                            result.confidence = max(result.confidence, score)
                            result.reason = f"[台词] 识别到搞笑关键词: {asr_text[:50]}"
                        elif emotion == "emotion":
                            result.main_category = "highlight"
                            result.sub_type = "情感高潮（台词识别）"
                            result.confidence = max(result.confidence, score)
                            result.reason = f"[台词] 识别到情感词: {asr_text[:50]}"

                    # 如果视觉分类已经是 highlight，但有台词情感词，提升置信度
                    elif result.main_category == "highlight" and score > 0.2:
                        result.confidence = min(1.0, result.confidence + score * 0.2)

                    # 如果视觉分类是 hot_fire，但台词里没有动作词也没有音效 → 可能是错判
                    elif result.main_category == "hot_fire":
                        # 不做反向排除，相信视觉模型（画面比台词更可靠）
                        pass

            except Exception as e:
                logger.debug(f"Whisper 辅助失败: {e}")

        # 3. 最终过滤：低置信度统一设为 exclude
        if result is not None and result.main_category != "exclude":
            if result.confidence < self.min_confidence:
                # 置信度太低，降级为 exclude
                old_cat = result.main_category
                result.main_category = "exclude"
                result.sub_type = f"低置信度{old_cat}"

        return result

    def classify_batch(self, video_path: str,
                       segments: List[Tuple[float, float]],
                       progress_callback=None) -> List[Optional[SceneClassification]]:
        """批量分类 - 保证返回数量与输入一致，失败返回None占位，避免错位"""
        results = []
        total = len(segments)

        for i, (start, end) in enumerate(segments):
            if progress_callback:
                progress = int((i / max(total, 1)) * 100)
                progress_callback(progress, f"AI场景分析: {i+1}/{total}")

            try:
                result = self.classify(video_path, start, end)
                results.append(result)  # 即使None也添加，保持位置对应
            except Exception as e:
                logger.debug(f"片段 {start:.1f}-{end:.1f}s 分类失败: {e}")
                results.append(None)

        if progress_callback:
            success_count = sum(1 for r in results if r is not None)
            progress_callback(100, f"AI场景分析完成: {success_count}/{total}")

        return results


# ==================== 旧的 CLIP 分类器（保留作为轻量备用） ====================

class CLIPSceneClassifier:
    """保留的 CLIP 轻量分类器（Qwen2-VL 不可用时的备用方案）"""

    SCENE_PROMPTS = {
        "action_fight": [
            "people fighting with swords or weapons in a dramatic scene",
            "martial arts combat scene with intense action",
            "ancient Chinese warriors fighting in a movie",
            "two characters engaged in close combat with visible motion blur",
        ],
        "xianxia_special_effect": [
            "magical energy blast and mystical visual effects in a fantasy movie",
            "sword qi or magical weapon effects with glowing energy",
            "chinese xianxia style cultivation battle scene with special effects",
            "fantasy battle scene with energy beams and glowing auras",
        ],
        "anime_ultimate": [
            "anime ultimate attack with dramatic visual effects and energy",
            "anime power up scene with dramatic lighting and effects",
            "anime fight scene with super powers and energy blasts",
            "animated battle with dramatic visual impact",
        ],
        "explosion_battle": [
            "explosion and fire with dramatic lighting and smoke",
            "large scale battle scene with explosions and destruction",
            "war battle scene with explosions and flying debris",
        ],
        "chase_scene": [
            "high speed chase scene with cars or people running",
            "fast moving chase scene with motion blur",
            "action chase scene in a movie",
        ],
        "talking_dialogue": [
            "people sitting and talking calmly in a room",
            "two people having a conversation with no visible action",
            "dialogue scene with people standing still and talking",
            "interview or talking head scene with no movement",
        ],
        "static_landscape": [
            "empty static scene with no movement or action",
            "boring landscape or scenery with no people",
            "still shot of nature or buildings with nothing happening",
            "wide shot of a quiet empty scene",
        ],
        "credits_text": [
            "movie credits or text overlay on a dark screen",
            "title screen with text and logo only",
            "end credits rolling with text on screen",
        ],
    }

    SCENE_CN_NAMES = {
        "action_fight": "古装打斗",
        "xianxia_special_effect": "仙侠特效",
        "anime_ultimate": "动漫大招",
        "explosion_battle": "爆炸大场面",
        "chase_scene": "追逐场景",
        "talking_dialogue": "对话场景",
        "static_landscape": "空镜/风景",
        "credits_text": "片头片尾",
    }

    HIGH_VALUE_SCENES = {"action_fight", "xianxia_special_effect", "anime_ultimate", "explosion_battle", "chase_scene"}
    LOW_VALUE_SCENES = {"talking_dialogue", "static_landscape", "credits_text"}

    def __init__(self, device: str = "cuda"):
        self.device = device
        self._model = None
        self._processor = None
        self._scene_text_features = {}
        self._model_path = self._resolve_model_path()

    def _resolve_model_path(self) -> str:
        project_root = Path(__file__).parent.parent
        candidates = [
            project_root / "models_cache" / "clip-vit-base-patch32",
            project_root / "models_cache" / "huggingface" / "clip-vit-base-patch32",
        ]
        for path in candidates:
            if path.exists():
                return str(path)
        cache_dir = project_root / "models_cache"
        if cache_dir.exists():
            for d in cache_dir.iterdir():
                if "clip" in str(d).lower() and d.is_dir():
                    return str(d)
        return "openai/clip-vit-base-patch32"

    def _load_model(self):
        if self._model is not None:
            return
        try:
            import torch
            from transformers import CLIPModel, CLIPProcessor

            is_local = not self._model_path.startswith("openai/")

            self._processor = CLIPProcessor.from_pretrained(self._model_path, local_files_only=is_local)
            self._model = CLIPModel.from_pretrained(
                self._model_path,
                torch_dtype=torch.float16 if self.device == "cuda" else torch.float32,
                local_files_only=is_local,
                use_safetensors=True,
            )

            if self.device == "cuda":
                self._model = self._model.to("cuda")
            self._model.eval()

            self._precompute_scene_text_features()
            logger.info("CLIP 模型加载完成")
        except Exception as e:
            logger.error(f"CLIP 模型加载失败: {e}")
            raise

    def _precompute_scene_text_features(self):
        import torch
        self._scene_text_features = {}

        for scene_type, prompts in self.SCENE_PROMPTS.items():
            inputs = self._processor(
                text=prompts, return_tensors="pt", padding=True, truncation=True, max_length=77
            )
            if self.device == "cuda":
                inputs = {k: v.to("cuda") for k, v in inputs.items()}

            with torch.no_grad():
                text_outputs = self._model.get_text_features(**inputs)
                if hasattr(text_outputs, 'text_embeds'):
                    text_features = text_outputs.text_embeds
                else:
                    text_features = text_outputs.pooler_output

                text_features = text_features / text_features.norm(dim=-1, keepdim=True)
                avg_feature = text_features.mean(dim=0, keepdim=True)
                avg_feature = avg_feature / avg_feature.norm(dim=-1, keepdim=True)

            self._scene_text_features[scene_type] = avg_feature

    def classify_segment(self, video_path: str, start_time: float, end_time: float,
                         num_frames: int = 3) -> Optional[SceneClassification]:
        try:
            self._load_model()
        except Exception as e:
            return None

        frames = self._extract_key_frames(video_path, start_time, end_time, num_frames)
        if not frames:
            return None

        all_frame_scores = []
        for frame in frames:
            scores = self._classify_single_frame(frame)
            if scores:
                all_frame_scores.append(scores)

        if not all_frame_scores:
            return None

        # 取平均得分
        scene_types = list(self.SCENE_PROMPTS.keys())
        avg_scores = {}
        for st in scene_types:
            avg_scores[st] = np.mean([fs[st] for fs in all_frame_scores])

        best_scene = max(avg_scores, key=avg_scores.get)
        total = sum(avg_scores.values())
        if total > 0:
            normalized_scores = {k: v / total for k, v in avg_scores.items()}
            confidence = normalized_scores[best_scene]
        else:
            confidence = 0.3

        try:
            for f in frames:
                try:
                    os.remove(f)
                except Exception:
                    pass
        except Exception:
            pass

        # 判断主分类
        if best_scene in self.HIGH_VALUE_SCENES:
            main_category = "hot_fire"
        else:
            main_category = "exclude"
        
        # 转换为中文子类型
        sub_type = self.SCENE_CN_NAMES.get(best_scene, best_scene)
        
        # 构建调试信息
        debug_scores = normalized_scores if total > 0 else avg_scores
        reason = f"CLIP分类: {sub_type} (置信度={confidence:.2f})"
        raw_text = str({k: f"{v:.3f}" for k, v in debug_scores.items()})
        
        return SceneClassification(
            start_time=start_time,
            end_time=end_time,
            main_category=main_category,
            sub_type=sub_type,
            confidence=confidence,
            reason=reason,
            raw_text=raw_text,
            frames_analyzed=len(all_frame_scores),
        )

    def classify_multiple_segments(self, video_path: str, segments, progress_callback=None):
        try:
            self._load_model()
        except Exception:
            return []

        results = []
        total = len(segments)
        for i, (start, end) in enumerate(segments):
            if progress_callback:
                progress = int((i / max(total, 1)) * 100)
                progress_callback(progress, f"CLIP分类: {i+1}/{total}")
            result = self.classify_segment(video_path, start, end, num_frames=3)
            if result:
                results.append(result)
        return results

    def _extract_key_frames(self, video_path: str, start_time: float, end_time: float,
                            num_frames: int) -> List[str]:
        duration = max(end_time - start_time, 1.0)
        step = duration / (num_frames + 1)
        target_times = [start_time + step * (i + 1) for i in range(num_frames)]

        frames = []
        for t in target_times:
            try:
                ffmpeg_path = CONFIG.ffmpeg_path or "ffmpeg"
                tmp_path = os.path.join(tempfile.gettempdir(),
                                         f"clip_frame_{os.getpid()}_{int(t*1000)}.jpg")

                cmd = [
                    ffmpeg_path, "-y",
                    "-ss", f"{t:.3f}",
                    "-i", video_path,
                    "-vframes", "1",
                    "-vf", "scale=224:224",
                    "-q:v", "2",
                    tmp_path
                ]
                result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)

                if result.returncode == 0 and os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 0:
                    frames.append(tmp_path)
            except Exception:
                continue
        return frames

    def _classify_single_frame(self, frame_path: str) -> Optional[Dict[str, float]]:
        try:
            import torch
            from PIL import Image

            image = Image.open(frame_path).convert("RGB")

            inputs = self._processor(images=image, return_tensors="pt", padding=True)
            if self.device == "cuda":
                inputs = {k: v.to("cuda") for k, v in inputs.items()}

            with torch.no_grad():
                image_outputs = self._model.get_image_features(**inputs)
                if hasattr(image_outputs, 'image_embeds'):
                    image_feature = image_outputs.image_embeds
                else:
                    image_feature = image_outputs.pooler_output
                image_feature = image_feature / image_feature.norm(dim=-1, keepdim=True)

            scores = {}
            for scene_type, text_feature in self._scene_text_features.items():
                sim = (image_feature @ text_feature.T).squeeze().item()
                scores[scene_type] = sim
            return scores

        except Exception as e:
            logger.debug(f"CLIP 单帧分类失败: {e}")
            return None

    def is_high_value_scene(self, classification: SceneClassification, min_confidence: float = 0.25) -> bool:
        return classification.main_category == "hot_fire" and classification.confidence >= min_confidence

    def get_scene_name_cn(self, scene_type: str) -> str:
        return self.SCENE_CN_NAMES.get(scene_type, scene_type)
