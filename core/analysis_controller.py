"""
分析控制器
整合6通道分析引擎，提供统一的分析接口
资源优化：顺序执行分析、GPU优先、内存限制
"""
import os
import gc
from pathlib import Path
from typing import List, Dict, Optional, Callable
from core.scene_detector import SceneDetector
from core.motion_analyzer import MotionAnalyzer
from core.audio_analyzer import AudioAnalyzer
from core.vfx_detector import VFXDetector
from core.ai_vision import AIVisionAnalyzer
from core.fusion_scorer import FusionScorer, FusionResult
from core.video_type_preset import get_preset
from core.clip_scorer import CLIPScorer
from utils.logger import logger
from utils.helpers import get_video_info
from config import CONFIG


def _check_memory():
    """检查内存使用，超过阈值时触发GC"""
    try:
        import psutil
        mem = psutil.virtual_memory()
        if mem.percent > 85:
            logger.warning(f"内存使用过高: {mem.percent:.1f}%，触发垃圾回收")
            gc.collect()
            return False
        return True
    except ImportError:
        return True


class AnalysisController:
    """分析控制器 v3.0 - 共享帧缓存减少重复解码"""
    
    def __init__(self, preset_name: str = "auto", enable_ai: bool = True):
        self.preset = get_preset(preset_name)
        self.enable_ai = enable_ai
        
        # 7通道分析器（延迟加载）
        self.scene_detector = None
        self.motion_analyzer = None
        self.audio_analyzer = None
        self.vfx_detector = None
        self.ai_vision = None
        self.clip_scorer = None
        
        # 融合评分引擎
        self.fusion_scorer = FusionScorer(preset_name)
        
        # v3.0: 共享帧缓存
        self._shared_frames = None
        
        logger.info(f"分析控制器初始化完成 (预设: {preset_name}, AI: {'启用' if enable_ai else '禁用'}, 共享缓存: 启用)")
    
    def _get_ai_vision(self):
        """延迟加载AI视觉分析器"""
        if self.ai_vision is None and self.enable_ai:
            self.ai_vision = AIVisionAnalyzer(
                provider=CONFIG.ai_provider,
                api_key=CONFIG.openai_api_key if CONFIG.ai_provider == "openai" else ""
            )
        return self.ai_vision
    
    def analyze(self, video_path: str, 
                progress_callback: Optional[Callable] = None) -> List[FusionResult]:
        """
        执行完整的7通道分析（v3.0: 共享帧缓存减少重复解码）
        """
        logger.info(f"开始分析视频(v3.0共享缓存): {video_path}")
        
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"视频文件不存在: {video_path}")
        
        video_info = get_video_info(video_path, CONFIG.ffmpeg_path)
        duration = video_info.get("duration", 0)
        
        if duration <= 0:
            raise ValueError("无法获取视频时长")
        
        # v3.0: 预提取共享帧缓存（供Motion+VFX复用）
        shared_frames_info = None
        try:
            from core.shared_frame_extractor import SharedFrameCache
            sfc = SharedFrameCache.get_instance()
            cache_path, fcount, fw, fh, ffps = sfc.extract_frames(
                video_path, target_fps=4.0, target_width=320
            )
            if cache_path and fcount > 0:
                shared_frames_info = (cache_path, fcount, fw, fh, ffps)
                logger.info(f"共享帧缓存就绪: {fcount}帧, {fw}x{fh}, {ffps}fps")
        except Exception as e:
            logger.warning(f"共享帧缓存创建失败(回退到独立解码): {e}")
        
        results = {}
        
        # v3.0: ResourceGovernor节流
        from core.resource_governor import ResourceGovernor
        gov = ResourceGovernor.get_instance()
        
        # 顺序执行各通道分析（避免多个FFmpeg进程同时运行）
        
        # 通道1: 场景切换检测
        try:
            if progress_callback:
                progress_callback(1, 0, "通道1: 场景切换检测")
            if self.scene_detector is None:
                self.scene_detector = SceneDetector(threshold=CONFIG.scene_threshold)
            results["scene"] = self.scene_detector.detect(video_path)
            if progress_callback:
                progress_callback(1, 100, "通道1: 完成")
            logger.info("通道1 场景切换检测完成")
        except Exception as e:
            logger.error(f"通道1 分析失败: {e}")
            results["scene"] = []
        
            gov.throttle()
            _check_memory()
            
            # 通道2: 运动向量分析（v3.0: 使用共享帧缓存）
        try:
            if progress_callback:
                progress_callback(2, 0, "通道2: 运动向量分析")
            if self.motion_analyzer is None:
                self.motion_analyzer = MotionAnalyzer()
            results["motion"] = self.motion_analyzer.analyze(
                video_path, shared_frames=shared_frames_info
            )
            if progress_callback:
                progress_callback(2, 100, "通道2: 完成")
            logger.info("通道2 运动向量分析完成")
        except Exception as e:
            logger.error(f"通道2 分析失败: {e}")
            results["motion"] = []
        
        gov.throttle()
        _check_memory()
        
        # 通道3: 音频能量分析
        try:
            if progress_callback:
                progress_callback(3, 0, "通道3: 音频能量分析")
            if self.audio_analyzer is None:
                self.audio_analyzer = AudioAnalyzer()
            results["audio"] = self.audio_analyzer.analyze(video_path)
            if progress_callback:
                progress_callback(3, 100, "通道3: 完成")
            logger.info("通道3 音频能量分析完成")
        except Exception as e:
            logger.error(f"通道3 分析失败: {e}")
            results["audio"] = []
        
        gov.throttle()
        _check_memory()
        
        # 通道4&5: 特效检测（v3.0: 使用共享帧缓存）
        try:
            if progress_callback:
                progress_callback(4, 0, "通道4&5: 特效检测")
            if self.vfx_detector is None:
                self.vfx_detector = VFXDetector()
            results["vfx"] = self.vfx_detector.detect(
                video_path, shared_frames=shared_frames_info
            )
            if progress_callback:
                progress_callback(4, 100, "通道4&5: 完成")
            logger.info("通道4&5 特效检测完成")
        except Exception as e:
            logger.error(f"通道4&5 分析失败: {e}")
            results["vfx"] = {"color_burst": [], "brightness_flash": [], "vfx_energy": []}
        
        gov.throttle()
        _check_memory()
        
        # 通道6: AI视觉分析（可选，使用GPU）
        if self.enable_ai:
            try:
                if progress_callback:
                    progress_callback(6, 0, "通道6: AI视觉分析")
                ai_vision = self._get_ai_vision()
                if ai_vision:
                    results["ai"] = ai_vision.analyze_frames(
                        video_path, interval=CONFIG.ai_vision_interval
                    )
                if progress_callback:
                    progress_callback(6, 100, "通道6: 完成")
                logger.info("通道6 AI视觉分析完成")
            except Exception as e:
                logger.error(f"通道6 分析失败: {e}")
                results["ai"] = []
        
        gov.throttle()
        _check_memory()
        
        # 通道7: CLIP语义评分（v2.8新增）
        try:
            if progress_callback:
                progress_callback(7, 0, "通道7: CLIP语义评分")
            if self.clip_scorer is None:
                self.clip_scorer = CLIPScorer()
            results["clip"] = self.clip_scorer.analyze(video_path, fps=2.0)
            if progress_callback:
                progress_callback(7, 100, "通道7: 完成")
            logger.info(f"通道7 CLIP语义评分完成: {len(results['clip'])}个数据点")
        except Exception as e:
            logger.error(f"通道7 分析失败: {e}")
            results["clip"] = []
        
        _check_memory()
        gc.collect()
        
        # 融合评分
        if progress_callback:
            progress_callback(0, 0, "融合评分...")
        
        fused_results = self.fusion_scorer.fuse(
            scene_data=results.get("scene", []),
            motion_data=results.get("motion", []),
            audio_data=results.get("audio", []),
            color_burst_data=results.get("vfx", {}).get("color_burst", []),
            brightness_flash_data=results.get("vfx", {}).get("brightness_flash", []),
            ai_vision_data=results.get("ai", []),
            video_duration=duration,
            vfx_energy_data=results.get("vfx", {}).get("vfx_energy", []),
            clip_data=results.get("clip", [])  # v2.8
        )
        
        if progress_callback:
            progress_callback(0, 100, "分析完成")
        
        logger.info(f"分析完成: {len(fused_results)}个时间点")
        return fused_results
    
    def extract_hot_segments(self, video_path: str, top_n: int = 10,
                            progress_callback: Optional[Callable] = None) -> List[FusionResult]:
        logger.info(f"提取高燃片段: {video_path}")
        
        # 获取视频时长（用于总时长约束）
        video_info = get_video_info(video_path, CONFIG.ffmpeg_path)
        video_duration = video_info.get("duration", 0)
        
        # v3.3: 先进行镜头切割检测
        if progress_callback:
            progress_callback(0, 100, "检测镜头切割点...")
        
        scene_cuts = self._detect_scene_cuts(video_path)
        
        fused_results = self.analyze(video_path, progress_callback)
        
        hot_segments = self.fusion_scorer.extract_highlights(
            fused_results,
            top_n=top_n,
            threshold=self.preset.thresholds["hot"],
            scene_cuts=scene_cuts,
            video_duration=video_duration  # v3.4: 总时长约束
        )
        
        logger.info(f"提取完成: {len(hot_segments)}个高燃片段 (镜头切割点: {len(scene_cuts)}个, 视频时长: {video_duration:.1f}s)")
        return hot_segments
    
    def extract_highlight_segments(self, video_path: str, top_n: int = 20,
                                  progress_callback: Optional[Callable] = None) -> List[FusionResult]:
        logger.info(f"提取高光时刻: {video_path}")
        
        # 获取视频时长（用于总时长约束）
        video_info = get_video_info(video_path, CONFIG.ffmpeg_path)
        video_duration = video_info.get("duration", 0)
        
        # v3.3: 先进行镜头切割检测
        if progress_callback:
            progress_callback(0, 100, "检测镜头切割点...")
        
        scene_cuts = self._detect_scene_cuts(video_path)
        
        fused_results = self.analyze(video_path, progress_callback)
        
        highlight_segments = self.fusion_scorer.extract_high_moments(
            fused_results,
            top_n=top_n,
            threshold=self.preset.thresholds["highlight"],
            scene_cuts=scene_cuts,
            video_duration=video_duration  # v3.4: 总时长约束
        )
        
        logger.info(f"提取完成: {len(highlight_segments)}个高光时刻 (镜头切割点: {len(scene_cuts)}个, 视频时长: {video_duration:.1f}s)")
        return highlight_segments
    
    def _detect_scene_cuts(self, video_path: str) -> List[float]:
        """
        v3.3: 检测视频中的镜头切割点
        
        Returns:
            镜头切割点列表（秒）
        """
        try:
            if self.scene_detector is None:
                from core.scene_detector import SceneDetector
                self.scene_detector = SceneDetector(threshold=CONFIG.scene_threshold)
            
            scenes = self.scene_detector.detect(video_path)
            scene_cuts = [s["time"] for s in scenes]
            
            # 添加起点和终点
            if not scene_cuts or scene_cuts[0] > 0:
                scene_cuts.insert(0, 0.0)
            
            video_info = get_video_info(video_path, CONFIG.ffmpeg_path)
            duration = video_info.get("duration", 0)
            if duration > 0 and (not scene_cuts or scene_cuts[-1] < duration):
                scene_cuts.append(duration)
            
            logger.info(f"镜头切割检测: {len(scene_cuts)}个切割点")
            return scene_cuts
        except Exception as e:
            logger.warning(f"镜头切割检测失败: {e}，将不使用镜头对齐")
            return []
    
    def generate_commentary(self, video_path: str, 
                           progress_callback: Optional[Callable] = None) -> Dict:
        logger.info(f"生成解说文案: {video_path}")
        
        if progress_callback:
            progress_callback(0, 0, "提取高光时刻...")
        
        highlights = self.extract_highlight_segments(video_path, top_n=20)
        
        if not highlights:
            logger.warning("未找到高光时刻，无法生成解说文案")
            return {"segments": [], "full_text": "", "duration": 0}
        
        if progress_callback:
            progress_callback(0, 50, "生成解说文案...")
        
        commentary = self._generate_commentary_text(highlights)
        
        if progress_callback:
            progress_callback(0, 100, "解说文案生成完成")
        
        logger.info(f"解说文案生成完成: {len(commentary['segments'])}段")
        return commentary
    
    def _generate_commentary_text(self, highlights: List[FusionResult]) -> Dict:
        """生成解说文案文本（强制使用GPU）"""
        try:
            from transformers import AutoModelForCausalLM, AutoTokenizer
            import torch
            
            if not torch.cuda.is_available():
                logger.warning("CUDA不可用，使用模板生成文案")
                return self._generate_template_commentary(highlights)
            
            # 解析模型路径
            model_name = CONFIG.local_models.text_model
            model_path = Path(model_name)
            if not model_path.is_absolute():
                project_root = Path(__file__).parent.parent
                if not str(model_name).startswith('models_cache'):
                    model_path = project_root / 'models_cache' / model_name
                else:
                    model_path = project_root / model_name
            
            if not model_path.exists():
                logger.warning(f"文本模型路径不存在: {model_path}")
                return self._generate_template_commentary(highlights)
            
            # 懒加载模型（强制GPU）
            if not hasattr(self, '_text_model') or self._text_model is None:
                logger.info("正在加载文本生成模型到GPU...")
                
                torch.cuda.empty_cache()
                gc.collect()
                
                self._text_model = AutoModelForCausalLM.from_pretrained(
                    str(model_path),
                    torch_dtype=torch.float16,
                    device_map="cuda",
                    trust_remote_code=True,
                    local_files_only=True,
                    low_cpu_mem_usage=True
                )
                
                self._tokenizer = AutoTokenizer.from_pretrained(
                    str(model_path),
                    trust_remote_code=True,
                    local_files_only=True
                )
                logger.info("文本生成模型加载完成（GPU）")
            
            # 构建提示词
            prompt = self._build_commentary_prompt(highlights)
            
            # 生成文案
            inputs = self._tokenizer(prompt, return_tensors="pt").to("cuda")
            
            with torch.no_grad():
                outputs = self._text_model.generate(
                    **inputs,
                    max_new_tokens=1000,
                    temperature=0.7,
                    do_sample=True,
                    pad_token_id=self._tokenizer.eos_token_id
                )
            
            response = self._tokenizer.decode(outputs[0], skip_special_tokens=True)
            
            # 释放推理临时显存
            del inputs, outputs
            torch.cuda.empty_cache()
            
            return self._parse_commentary_response(response, highlights)
            
        except ImportError as e:
            logger.warning(f"文本生成依赖缺失: {e}")
            return self._generate_template_commentary(highlights)
        except Exception as e:
            logger.error(f"AI文案生成失败: {e}")
            return self._generate_template_commentary(highlights)
    
    def _build_commentary_prompt(self, highlights: List[FusionResult]) -> str:
        scenes_desc = []
        for i, h in enumerate(highlights[:10]):
            scenes_desc.append(f"{i+1}. 时间{h.time:.1f}s-{h.time+h.duration:.1f}s, 类型:{h.scene_type}, 描述:{h.description}")
        
        scenes_text = "\n".join(scenes_desc)
        
        prompt = f"""你是一个专业的影视解说文案撰写者。请根据以下视频高光时刻，撰写一段生动有趣的解说文案。

视频高光时刻：
{scenes_text}

要求：
1. 语言生动、有感染力，吸引观众
2. 根据不同场景类型使用不同的解说风格
3. 每段文案控制在50-100字
4. 使用"注意看"、"精彩来了"等吸引注意力的开场白
5. 文案要与画面内容匹配

请以JSON格式返回，格式如下：
{{
  "segments": [
    {{"time": 开始时间, "duration": 持续时间, "text": "解说文案", "scene_type": "场景类型"}},
    ...
  ],
  "full_text": "完整文案文本"
}}

现在开始撰写："""
        
        return prompt
    
    def _parse_commentary_response(self, response: str, highlights: List[FusionResult]) -> Dict:
        try:
            import json
            import re
            
            json_match = re.search(r'\{[\s\S]*\}', response)
            if json_match:
                json_str = json_match.group()
                data = json.loads(json_str)
                
                segments = data.get("segments", [])
                full_text = data.get("full_text", "")
                
                for seg in segments:
                    if "time" not in seg or "text" not in seg:
                        continue
                
                total_duration = sum(seg.get("duration", 5.0) for seg in segments)
                
                return {
                    "segments": segments,
                    "full_text": full_text,
                    "duration": total_duration
                }
            else:
                return self._generate_template_commentary(highlights)
        
        except Exception as e:
            logger.error(f"解析文案响应失败: {e}")
            return self._generate_template_commentary(highlights)
    
    def _generate_template_commentary(self, highlights: List[FusionResult]) -> Dict:
        """使用模板生成文案（备用方案）"""
        import random
        
        segments = []
        full_text = ""
        
        templates = {
            "action": [
                "注意看，这里是一段精彩的{desc}，紧张刺激！",
                "精彩来了！{desc}，让人目不转睛！",
                "高能预警！{desc}，动作戏太燃了！"
            ],
            "vfx_action": [
                "特效炸裂！{desc}，视觉冲击力满分！",
                "注意看，{desc}，特效太震撼了！",
                "高能场面！{desc}，特效制作精良！"
            ],
            "vfx_spectacle": [
                "视觉盛宴！{desc}，画面太美了！",
                "注意看，{desc}，特效光效太惊艳了！",
                "震撼视觉！{desc}，这一幕美到室息！"
            ],
            "highlight": [
                "经典场面来了！{desc}，这一幕太经典了！",
                "注意看，{desc}，名场面再现！",
                "精彩瞬间！{desc}，让人印象深刻！"
            ],
            "dialog": [
                "这段对白太经典了：{desc}，句句戳心！",
                "注意听，{desc}，台词写得真好！",
                "经典台词！{desc}，让人回味无穷！"
            ],
            "emotion": [
                "情感爆发！{desc}，演技太到位了！",
                "注意看，{desc}，情感戏太感人了！",
                "高能情感！{desc}，让人动容！"
            ],
            "climax": [
                "高潮来了！{desc}，剧情太精彩了！",
                "注意看，{desc}，全片最燃时刻！",
                "巅峰时刻！{desc}，让人热血沸腾！"
            ]
        }
        
        for i, highlight in enumerate(highlights[:10]):
            scene_type = highlight.scene_type
            description = highlight.description
            
            template_list = templates.get(scene_type, [f"精彩片段：{description}。"])
            text = random.choice(template_list).format(desc=description)
            
            segments.append({
                "time": highlight.time,
                "duration": highlight.duration,
                "text": text,
                "scene_type": scene_type
            })
            
            full_text += text + "\n"
        
        total_duration = sum(seg["duration"] for seg in segments)
        
        return {
            "segments": segments,
            "full_text": full_text,
            "duration": total_duration
        }
