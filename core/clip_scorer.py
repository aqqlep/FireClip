"""
通道7: CLIP 语义评分（v2.8新增）
使用 OpenAI CLIP 模型对视频帧进行语义理解
识别"打斗、爆炸、高潮"等内容语义，而非仅靠像素特征
"""
import subprocess
import os
import numpy as np
from pathlib import Path
from typing import List, Dict, Callable, Optional
from utils.logger import logger
from config import CONFIG


class CLIPScorer:
    """CLIP 语义评分器"""
    
    # 正向提示词（描述"燃"的画面内容）
    POSITIVE_PROMPTS = [
        "intense action scene with fighting and combat",
        "explosion and fire with dramatic lighting",
        "epic battle with sword or weapon effects",
        "fast moving objects with motion blur",
        "magical energy blast and visual effects",
        "dramatic climax moment in a movie",
        "car chase with high speed",
        "martial arts fight scene",
        "superhero using powers with energy effects",
        "stunning visual spectacle with bright colors",
    ]
    
    # 负向提示词（描述"平淡"的画面）
    NEGATIVE_PROMPTS = [
        "empty static scene with no movement",
        "person sitting and talking calmly",
        "black screen or loading screen",
        "simple background with nothing happening",
        "boring landscape with no action",
        "credits or text overlay on dark background",
    ]
    
    # 默认模型（HuggingFace 上的 CLIP）
    DEFAULT_MODEL = "openai/clip-vit-base-patch32"
    
    def __init__(self, model_name: str = None, device: str = "cuda"):
        """
        初始化 CLIP 评分器
        
        Args:
            model_name: HuggingFace 模型名称或本地路径
            device: 计算设备 ("cuda" / "cpu")
        """
        self.model_name = model_name or self._resolve_model_path()
        self.device = device
        
        # 延迟加载
        self._model = None
        self._processor = None
        self._text_features = None  # 缓存文本特征
        
        logger.info(f"CLIP评分器初始化: model={self.model_name}, device={device}")
    
    def _resolve_model_path(self) -> str:
        """解析模型路径（优先本地 models_cache）"""
        project_root = Path(__file__).parent.parent
        local_path = project_root / "models_cache" / "clip-vit-base-patch32"
        
        if local_path.exists():
            return str(local_path)
        
        # 也检查 HuggingFace 缓存格式
        hf_cache = project_root / "models_cache" / "huggingface"
        if hf_cache.exists():
            # 查找 clip 相关目录
            for d in hf_cache.iterdir():
                if "clip" in d.name.lower():
                    return str(d)
        
        # 回退到 HuggingFace Hub 名称（会自动下载）
        return self.DEFAULT_MODEL
    
    def _load_model(self):
        """加载 CLIP 模型到 GPU"""
        if self._model is not None:
            return
        
        try:
            import torch
            from transformers import CLIPModel, CLIPProcessor
            
            logger.info(f"正在加载 CLIP 模型: {self.model_name}")
            
            torch.cuda.empty_cache()
            
            self._processor = CLIPProcessor.from_pretrained(
                self.model_name,
                local_files_only=not self.model_name.startswith("openai/"),
            )
            
            self._model = CLIPModel.from_pretrained(
                self.model_name,
                torch_dtype=torch.float16 if self.device == "cuda" else torch.float32,
                local_files_only=not self.model_name.startswith("openai/"),
                use_safetensors=True,  # v2.8: 使用 safetensors 避免 torch.load CVE
            )
            
            if self.device == "cuda" and torch.cuda.is_available():
                self._model = self._model.to("cuda")
            
            self._model.eval()
            
            # 预计算文本特征（只需计算一次）
            self._precompute_text_features()
            
            logger.info(f"CLIP 模型加载完成 (device={self.device})")
            
        except Exception as e:
            logger.error(f"CLIP 模型加载失败: {e}")
            raise
    
    def _precompute_text_features(self):
        """预计算正向和负向提示词的文本特征向量"""
        import torch
        
        all_prompts = self.POSITIVE_PROMPTS + self.NEGATIVE_PROMPTS
        
        inputs = self._processor(
            text=all_prompts,
            return_tensors="pt",
            padding=True,
            truncation=True,
            max_length=77
        )
        
        if self.device == "cuda":
            inputs = {k: v.to("cuda") for k, v in inputs.items()}
        
        with torch.no_grad():
            text_outputs = self._model.get_text_features(**inputs)
            # transformers 5.x 返回 BaseModelOutputWithPooling
            text_features = text_outputs.text_embeds if hasattr(text_outputs, 'text_embeds') else text_outputs.pooler_output
            # L2 归一化
            text_features = text_features / text_features.norm(dim=-1, keepdim=True)
        
        n_pos = len(self.POSITIVE_PROMPTS)
        self._pos_features = text_features[:n_pos]    # 正向文本特征
        self._neg_features = text_features[n_pos:]     # 负向文本特征
        
        logger.info(f"文本特征预计算完成: {n_pos}正向 + {len(self.NEGATIVE_PROMPTS)}负向")
    
    def analyze(self, video_path: str, fps: float = 2.0,
                callback: Optional[Callable] = None) -> List[Dict]:
        """
        用 CLIP 对视频帧进行语义评分
        
        流程：
        1. FFmpeg 管道提取帧（2fps, 224x224）
        2. 批量送入 CLIP 视觉编码器
        3. 计算帧特征与正向/负向文本特征的余弦相似度
        4. score = mean(pos_similarity) - mean(neg_similarity)
        
        Args:
            video_path: 视频路径
            fps: 采样帧率（默认2fps）
            callback: 进度回调 callback(progress, message)
        
        Returns:
            语义评分列表 [{"time": float, "score": float, "clip_score": float}, ...]
        """
        logger.info(f"开始 CLIP 语义分析: {video_path}")
        
        if callback:
            callback(0, "正在加载 CLIP 模型...")
        
        try:
            self._load_model()
        except Exception as e:
            logger.error(f"CLIP 加载失败，跳过语义分析: {e}")
            return []
        
        if callback:
            callback(10, "正在提取视频帧...")
        
        try:
            import torch
            
            # 获取视频时长
            duration = self._get_duration(video_path)
            if duration <= 0:
                return []
            
            # FFmpeg 管道提取帧（224x224 RGB，CLIP 输入尺寸）
            target_size = 224
            cmd = [
                CONFIG.ffmpeg_path or self._find_ffmpeg(),
                "-i", video_path,
                "-vf", f"fps={int(fps)},scale={target_size}:{target_size}",
                "-f", "rawvideo",
                "-pix_fmt", "rgb24",
                "-nostats",
                "-loglevel", "error",
                "-"
            ]
            
            process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                bufsize=1024 * 1024
            )
            
            frame_size = target_size * target_size * 3
            batch_size = 16  # GPU 批处理大小
            
            all_scores = []
            frame_batch = []
            frame_times = []
            frame_count = 0
            
            progress_interval = max(1, int(fps * 10))
            
            try:
                while True:
                    raw = process.stdout.read(frame_size)
                    if not raw or len(raw) < frame_size:
                        break
                    
                    frame = np.frombuffer(raw, dtype=np.uint8).reshape((target_size, target_size, 3))
                    frame_count += 1
                    time = frame_count / fps
                    
                    frame_batch.append(frame)
                    frame_times.append(time)
                    
                    # 批处理推理
                    if len(frame_batch) >= batch_size:
                        scores = self._score_batch(frame_batch)
                        for t, s in zip(frame_times, scores):
                            all_scores.append({
                                "time": t,
                                "score": float(s),
                                "clip_score": float(s)
                            })
                        
                        frame_batch = []
                        frame_times = []
                    
                    # 进度
                    if callback and frame_count % progress_interval == 0:
                        progress = 10 + int(min((time / duration) * 85, 85))
                        callback(progress, f"CLIP分析: {frame_count}帧 ({time:.0f}s/{duration:.0f}s)")
                
                # 处理剩余帧
                if frame_batch:
                    scores = self._score_batch(frame_batch)
                    for t, s in zip(frame_times, scores):
                        all_scores.append({
                            "time": t,
                            "score": float(s),
                            "clip_score": float(s)
                        })
                
                process.wait()
            
            finally:
                del frame_batch
            
            if callback:
                callback(100, f"CLIP分析完成: {len(all_scores)}帧")
            
            # 统计
            if all_scores:
                scores_arr = [s["score"] for s in all_scores]
                logger.info(f"CLIP分析完成: {len(all_scores)}帧, "
                           f"平均分={np.mean(scores_arr):.3f}, "
                           f"最高={np.max(scores_arr):.3f}")
            
            return all_scores
        
        except Exception as e:
            logger.error(f"CLIP 语义分析失败: {e}")
            if callback:
                callback(100, f"CLIP分析失败: {e}")
            return []
    
    def _score_batch(self, frames: List[np.ndarray]) -> np.ndarray:
        """
        对一批帧计算 CLIP 语义得分
        
        Args:
            frames: 帧列表 (H, W, 3) uint8
        
        Returns:
            得分数组 (N,) 范围约 [-1, 1]，越高越"燃"
        """
        import torch
        
        # 预处理：numpy → tensor → CLIP 格式
        # CLIP 期望 [0,1] 范围的 RGB 图像
        images = [f.astype(np.float32) / 255.0 for f in frames]
        
        # 用 processor 处理（包含 CLIP 的归一化）
        inputs = self._processor(
            images=images,
            return_tensors="pt",
            padding=True
        )
        
        if self.device == "cuda":
            inputs = {k: v.to("cuda") for k, v in inputs.items()}
        
        with torch.no_grad():
            # 获取图像特征
            image_outputs = self._model.get_image_features(**inputs)
            # transformers 5.x 返回 BaseModelOutputWithPooling
            image_features = image_outputs.image_embeds if hasattr(image_outputs, 'image_embeds') else image_outputs.pooler_output
            image_features = image_features / image_features.norm(dim=-1, keepdim=True)
            
            # 计算与正向/负向文本的余弦相似度
            # pos_sim: (N, num_pos), neg_sim: (N, num_neg)
            pos_sim = image_features @ self._pos_features.T
            neg_sim = image_features @ self._neg_features.T
            
            # 取平均相似度
            pos_mean = pos_sim.mean(dim=-1)  # (N,)
            neg_mean = neg_sim.mean(dim=-1)  # (N,)
            
            # 语义燃度 = 正向相似度 - 负向相似度
            # 范围约 [-0.5, 0.5]，映射到 [0, 1]
            raw_score = pos_mean - neg_mean
            
            # 归一化到 0-1（基于经验范围）
            # CLIP cosine similarity 通常在 [-0.3, 0.5] 范围
            normalized = (raw_score + 0.15) / 0.50  # 映射 [-0.15, 0.35] → [0, 1]
            normalized = torch.clamp(normalized, 0.0, 1.0)
        
        return normalized.cpu().numpy()
    
    def _get_duration(self, video_path: str) -> float:
        """获取视频时长"""
        try:
            ffprobe = CONFIG.ffmpeg_path.replace("ffmpeg.exe", "ffprobe.exe") if CONFIG.ffmpeg_path else "ffprobe"
            if not os.path.exists(ffprobe):
                ffprobe = "ffprobe"
            
            cmd = [ffprobe, "-v", "error", "-show_entries", "format=duration",
                   "-of", "default=noprint_wrappers=1:nokey=1", video_path]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                return float(result.stdout.strip())
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
        return 0.0
    
    def _find_ffmpeg(self) -> str:
        """查找 ffmpeg 路径"""
        project_root = Path(__file__).parent.parent
        local_ffmpeg = project_root / "ffmpeg" / "bin" / "ffmpeg.exe"
        if local_ffmpeg.exists():
            return str(local_ffmpeg)
        return "ffmpeg"
