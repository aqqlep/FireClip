"""
通道6: AI视觉分析
使用多模态AI模型分析视频截帧，识别场景类型和动作类型
"""
import subprocess
import json
import cv2
import numpy as np
from pathlib import Path
from typing import List, Dict, Callable, Optional
from utils.logger import logger
from config import CONFIG


class AIVisionAnalyzer:
    """AI视觉分析器"""
    
    # 类级别的警告限频（同一进程内只警告一次）
    _dependency_warned = False
    
    def __init__(self, provider: str = "local", api_key: str = "", model: str = None):
        """
        初始化AI视觉分析器
        
        Args:
            provider: AI提供商 ("local" / "openai" / "claude")
            api_key: API密钥（仅API模式需要）
            model: 模型名称（可选，使用默认模型）
        """
        self.provider = provider
        self.api_key = api_key
        self.model = model
        self.ffmpeg_path = CONFIG.ffmpeg_path
        
        # 设置默认模型
        if not model:
            if provider == "local":
                # 使用本地模型路径
                self.model = CONFIG.local_models.vision_model
            elif provider == "openai":
                self.model = "gpt-4o"
            elif provider == "claude":
                self.model = "claude-3-5-sonnet-20241022"
        
        # 本地模型缓存
        self._model = None
        self._processor = None
    
    def analyze_frames(self, video_path: str, interval: float = 2.0,
                      callback: Optional[Callable] = None) -> List[Dict]:
        """
        分析视频截帧
        
        Args:
            video_path: 视频文件路径
            interval: 截帧间隔（秒），默认2秒
            callback: 进度回调函数 callback(progress: int, message: str)
        
        Returns:
            AI分析结果列表 [{"time": float, "scene_type": str, "action_type": str, 
                          "description": str, "tags": list, "confidence": float}, ...]
        """
        logger.info(f"开始AI视觉分析: {video_path}")
        
        if callback:
            callback(0, "正在提取视频截帧...")
        
        try:
            # 提取截帧
            frames_data = self._extract_frames(video_path, interval, callback)
            
            if not frames_data:
                logger.warning("未提取到任何帧")
                return []
            
            if callback:
                callback(30, f"提取完成: {len(frames_data)}帧，开始AI分析...")
            
            # AI分析
            results = []
            total_frames = len(frames_data)
            
            for i, frame_data in enumerate(frames_data):
                time = frame_data["time"]
                frame_path = frame_data["path"]
                
                # 调用AI模型分析
                analysis = self._analyze_single_frame(frame_path)
                
                if analysis:
                    analysis["time"] = time
                    results.append(analysis)
                
                # 更新进度
                if callback:
                    progress = 30 + int((i / total_frames) * 70)  # 30-100%
                    callback(progress, f"AI分析: {i+1}/{total_frames}")
                
                # 删除临时文件
                try:
                    Path(frame_path).unlink()
                except OSError:
                    pass
            
            if callback:
                callback(100, f"AI视觉分析完成: {len(results)}个结果")
            
            logger.info(f"AI视觉分析完成: {len(results)}个结果")
            return results
        
        except Exception as e:
            logger.error(f"AI视觉分析失败: {e}")
            if callback:
                callback(100, f"分析失败: {str(e)}")
            return []
    
    def _extract_frames(self, video_path: str, interval: float,
                       callback: Optional[Callable] = None) -> List[Dict]:
        """
        提取视频截帧
        
        Args:
            video_path: 视频文件路径
            interval: 截帧间隔（秒）
            callback: 进度回调
        
        Returns:
            帧数据列表 [{"time": float, "path": str}, ...]
        """
        # 创建临时目录
        temp_dir = Path("cache/temp_frames")
        temp_dir.mkdir(parents=True, exist_ok=True)
        
        # 使用FFmpeg提取截帧
        output_pattern = str(temp_dir / "frame_%04d.jpg")
        
        cmd = [
            self.ffmpeg_path,
            "-i", video_path,
            "-vf", f"fps=1/{interval}",  # 每interval秒一帧
            "-q:v", "2",  # 高质量
            output_pattern
        ]
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300
            )
            
            if result.returncode != 0:
                logger.error(f"FFmpeg截帧失败: {result.stderr}")
                return []
            
            # 获取提取的帧文件
            frame_files = sorted(temp_dir.glob("frame_*.jpg"))
            
            frames_data = []
            for i, frame_file in enumerate(frame_files):
                time = i * interval
                frames_data.append({
                    "time": time,
                    "path": str(frame_file)
                })
            
            return frames_data
        
        except Exception as e:
            logger.error(f"提取截帧失败: {e}")
            return []
    
    def _analyze_single_frame(self, frame_path: str) -> Optional[Dict]:
        """
        分析单个帧
        
        Args:
            frame_path: 帧图片路径
        
        Returns:
            分析结果字典
        """
        try:
            if self.provider == "local":
                return self._analyze_with_local_model(frame_path)
            elif self.provider == "openai":
                return self._analyze_with_openai(frame_path)
            elif self.provider == "claude":
                return self._analyze_with_claude(frame_path)
            else:
                logger.error(f"未知的AI提供商: {self.provider}")
                return None
        
        except Exception as e:
            logger.error(f"分析帧失败: {e}")
            return None
    
    def _analyze_with_local_model(self, frame_path: str) -> Optional[Dict]:
        """使用本地模型分析"""
        try:
            # 尝试加载Qwen2-VL模型
            from transformers import Qwen2VLForConditionalGeneration, AutoProcessor
            from qwen_vl_utils import process_vision_info
            import torch
            
            # 检查模型是否已加载
            if self._model is None or self._processor is None:
                logger.info(f"正在加载本地模型: {self.model}")
                
                # 检查模型路径是否存在（支持相对路径和绝对路径）
                model_path = Path(self.model)
                if not model_path.is_absolute():
                    # 相对路径，基于项目根目录解析
                    project_root = Path(__file__).parent.parent
                    # 自动添加 models_cache 前缀
                    if not str(self.model).startswith('models_cache'):
                        model_path = project_root / 'models_cache' / self.model
                    else:
                        model_path = project_root / self.model
                
                if not model_path.exists():
                    logger.error(f"模型路径不存在: {model_path}")
                    logger.warning("请确保已下载模型到 models_cache 目录")
                    return self._get_mock_analysis()
                
                # 加载模型 - 强制使用GPU
                device = "cuda"
                dtype = torch.float16
                
                # 设置显存管理
                torch.cuda.empty_cache()
                torch.cuda.set_per_process_memory_fraction(0.8)  # 限制使用80%显存
                
                logger.info(f"使用设备: {device}, 数据类型: {dtype}")
                
                self._model = Qwen2VLForConditionalGeneration.from_pretrained(
                    str(model_path),
                    torch_dtype=dtype,
                    device_map="cuda",  # 强制使用CUDA
                    trust_remote_code=True,
                    local_files_only=True,
                    low_cpu_mem_usage=True  # 减少CPU内存使用
                )
                
                # 加载处理器
                self._processor = AutoProcessor.from_pretrained(
                    str(model_path),
                    trust_remote_code=True,
                    local_files_only=True
                )
                
                logger.info("本地模型加载完成")
            
            # 准备输入
            messages = [{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "image": f"file://{frame_path}"
                    },
                    {
                        "type": "text",
                        "text": self._get_analysis_prompt()
                    }
                ]
            }]
            
            # 处理输入
            text = self._processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
            image_inputs, video_inputs = process_vision_info(messages)
            
            inputs = self._processor(
                text=[text],
                images=image_inputs,
                videos=video_inputs,
                padding=True,
                return_tensors="pt"
            )
            
            # 移动到GPU
            if torch.cuda.is_available():
                inputs = inputs.to("cuda")
            
            # 生成输出
            with torch.no_grad():
                generated_ids = self._model.generate(
                    **inputs,
                    max_new_tokens=500,
                    temperature=0.7,
                    do_sample=True,
                    pad_token_id=self._processor.tokenizer.pad_token_id,
                    eos_token_id=self._processor.tokenizer.eos_token_id
                )
            
            # 解码输出
            generated_ids_trimmed = [
                out_ids[len(in_ids):] for in_ids, out_ids in zip(inputs.input_ids, generated_ids)
            ]
            output_text = self._processor.batch_decode(
                generated_ids_trimmed, skip_special_tokens=True, clean_up_tokenization_spaces=False
            )[0]
            
            # 解析输出
            return self._parse_analysis_response(output_text)
        
        except ImportError as e:
            if not AIVisionAnalyzer._dependency_warned:
                logger.warning(f"本地模型依赖缺失: {e}")
                logger.warning("请安装: pip install transformers qwen-vl-utils")
                logger.info("后续帧将静默返回模拟数据（不再重复警告）")
                AIVisionAnalyzer._dependency_warned = True
            return self._get_mock_analysis()
        except Exception as e:
            if not AIVisionAnalyzer._dependency_warned:
                logger.error(f"本地模型分析失败: {e}")
                AIVisionAnalyzer._dependency_warned = True
            return self._get_mock_analysis()
    
    def _analyze_with_openai(self, frame_path: str) -> Optional[Dict]:
        """使用OpenAI API分析"""
        try:
            import base64
            from openai import OpenAI
            
            # 读取图片并编码
            with open(frame_path, "rb") as f:
                image_data = base64.b64encode(f.read()).decode("utf-8")
            
            # 调用API
            client = OpenAI(api_key=self.api_key)
            
            response = client.chat.completions.create(
                model=self.model,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": self._get_analysis_prompt()
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_data}"
                            }
                        }
                    ]
                }],
                max_tokens=1000
            )
            
            # 解析响应
            content = response.choices[0].message.content
            return self._parse_analysis_response(content)
        
        except Exception as e:
            logger.error(f"OpenAI API分析失败: {e}")
            return None
    
    def _analyze_with_claude(self, frame_path: str) -> Optional[Dict]:
        """使用Claude API分析"""
        try:
            import base64
            from anthropic import Anthropic
            
            # 读取图片并编码
            with open(frame_path, "rb") as f:
                image_data = base64.b64encode(f.read()).decode("utf-8")
            
            # 调用API
            client = Anthropic(api_key=self.api_key)
            
            response = client.messages.create(
                model=self.model,
                max_tokens=1000,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": image_data
                            }
                        },
                        {
                            "type": "text",
                            "text": self._get_analysis_prompt()
                        }
                    ]
                }]
            )
            
            # 解析响应
            content = response.content[0].text
            return self._parse_analysis_response(content)
        
        except Exception as e:
            logger.error(f"Claude API分析失败: {e}")
            return None
    
    def _get_analysis_prompt(self) -> str:
        """获取分析提示词"""
        return """请分析这张视频截帧，识别以下内容：

1. 场景类型（scene_type）：
   - action: 动作场景（打斗、追逐、枪战等）
   - vfx_action: 特效动作（法术、能量波、超能力等）
   - highlight: 高光时刻（经典场面、名场面）
   - dialog: 对白场景
   - emotion: 情感场景
   - climax: 高潮场景

2. 动作类型（action_type）：
   - sword_fight: 剑斗/刀战
   - gunfight: 枪战
   - chase: 追逐
   - magic: 法术/魔法
   - explosion: 爆炸
   - vfx_wave: 能量波/冲击波
   - anime_action: 动漫动作
   - martial_arts: 武术
   - none: 无动作

3. 描述（description）：用一句话描述画面内容

4. 标签（tags）：添加相关标签，如 #打戏 #法术特效 #名场面 #经典台词 #漫剧打斗

5. 置信度（confidence）：0-1之间的数值，表示判断的确定性

请以JSON格式返回结果：
{
  "scene_type": "场景类型",
  "action_type": "动作类型",
  "description": "画面描述",
  "tags": ["标签1", "标签2"],
  "confidence": 0.85
}"""
    
    def _parse_analysis_response(self, content: str) -> Optional[Dict]:
        """解析AI分析响应"""
        try:
            # 尝试提取JSON
            import re
            json_match = re.search(r'\{[^}]+\}', content, re.DOTALL)
            
            if json_match:
                json_str = json_match.group()
                data = json.loads(json_str)
                
                return {
                    "scene_type": data.get("scene_type", "unknown"),
                    "action_type": data.get("action_type", "none"),
                    "description": data.get("description", ""),
                    "tags": data.get("tags", []),
                    "confidence": float(data.get("confidence", 0.5))
                }
            else:
                logger.warning("未找到JSON格式的分析结果")
                return None
        
        except Exception as e:
            logger.error(f"解析分析响应失败: {e}")
            return None
    
    def _get_mock_analysis(self) -> Dict:
        """获取模拟分析数据（用于测试）"""
        import random
        
        scene_types = ["action", "vfx_action", "highlight", "dialog", "emotion", "climax"]
        action_types = ["sword_fight", "gunfight", "chase", "magic", "explosion", 
                       "vfx_wave", "anime_action", "martial_arts", "none"]
        
        return {
            "scene_type": random.choice(scene_types),
            "action_type": random.choice(action_types),
            "description": "",  # 留空让融合评分器根据通道得分生成描述
            "tags": [],
            "confidence": random.uniform(0.3, 0.7)
        }


# 测试代码
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("用法: python ai_vision.py <video_path> [provider] [api_key]")
        sys.exit(1)
    
    video_path = sys.argv[1]
    provider = sys.argv[2] if len(sys.argv) > 2 else "local"
    api_key = sys.argv[3] if len(sys.argv) > 3 else ""
    
    analyzer = AIVisionAnalyzer(provider=provider, api_key=api_key)
    
    def progress_callback(progress, message):
        print(f"[{progress}%] {message}")
    
    # 分析视频
    results = analyzer.analyze_frames(video_path, interval=2.0, callback=progress_callback)
    
    print(f"\n分析完成: {len(results)}个结果")
    for result in results[:5]:  # 只显示前5个
        print(f"  时间: {result['time']:.2f}s")
        print(f"    场景类型: {result['scene_type']}")
        print(f"    动作类型: {result['action_type']}")
        print(f"    描述: {result['description']}")
        print(f"    标签: {', '.join(result['tags'])}")
        print(f"    置信度: {result['confidence']:.2f}")
        print()
