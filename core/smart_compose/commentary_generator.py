"""
解说文案生成模块 v1.0
根据选中的片段和解说风格自动生成解说文案
支持风格：激情高燃、情感走心、幽默搞笑、平静解说
"""
import random
from typing import List, Dict, Optional
from dataclasses import dataclass
from utils.logger import logger


@dataclass
class CommentarySegment:
    """解说片段"""
    start_time: float
    end_time: float
    text: str
    duration_hint: float = 3.0


class CommentaryGenerator:
    """解说文案生成器"""
    
    # 风格开场白
    OPENINGS = {
        "passionate": [
            "注意看！这个视频太燃了！",
            "前方高能！千万别眨眼！",
            "这绝对是你今年看过最爽的片段！",
            "战斗开始！全程高燃！",
            "来了来了！名场面来了！",
        ],
        "emotional": [
            "有些画面，总能触动我们内心最柔软的地方。",
            "故事的开始，总是那么美好。",
            "看到这里，你有没有想起某个人？",
            "这一段，不知道看哭了多少人。",
            "有些话，不需要说出口。",
        ],
        "humorous": [
            "不是哥们，这操作给我看傻了！",
            "笑死我了，这是什么神仙剧情！",
            "我敢打赌，你绝对猜不到接下来发生什么！",
            "导演：这届观众真难带。",
            "你看他笑得多开心啊（狗头）",
        ],
        "calm": [
            "今天我们来看一段经典画面。",
            "接下来这个片段，成为了很多人心中的经典。",
            "让我们慢慢欣赏这段故事。",
            "故事要从这里说起。",
            "这是一部作品中最经典的桥段之一。",
        ],
    }
    
    # 转折连接词
    TRANSITIONS = {
        "passionate": [
            "紧接着！", "下一秒！", "万万没想到！", "就在这时！", "高潮来了！",
            "最精彩的地方来了！", "说时迟那时快！", "只见！", "关键时刻！",
        ],
        "emotional": [
            "可是，", "然而，", "就在这时，", "命运总是，", "后来，",
            "谁能想到，", "渐渐地，", "终于，",
        ],
        "humorous": [
            "结果下一秒，", "但是你猜怎么着？", "搞笑的来了，", "没想到吧，",
            "结果尴尬了，", "这时候主角发话了，", "然后就没有然后了，",
        ],
        "calm": [
            "接下来，", "随后，", "在这之后，", "此时，", "画面一转，",
            "就在这个时候，",
        ],
    }
    
    # 片段点评模板
    COMMENT_TEMPLATES = {
        "action": {
            "passionate": [
                "这打斗场面简直帅炸了！", "这波操作直接拉满！", "战斗力直接爆表！",
                "这一套连招行云流水！", "这一击直接封神！", "拳拳到肉，看得人热血沸腾！",
                "这反应速度绝了！", "高手过招，招招致命！",
            ],
            "emotional": [
                "每一次出招，都带着坚定的信念。", "战斗的背后，是守护的决心。",
                "即使身负重伤，也绝不后退。", "这一战，不仅是为了胜利。",
            ],
            "humorous": [
                "这哥们怕不是开了外挂吧？", "打人都没力气，还说自己是杀手？",
                "你这一拳下去，我可能会死。", "建议直接去申请漫威入职。",
            ],
            "calm": [
                "这是一场精彩的对决。", "双方展开了激烈的交战。",
                "这场战斗成为了经典。", "动作设计十分流畅。",
            ],
        },
        "dialogue": {
            "passionate": [
                "这句话说得太霸气了！", "就问你硬气不硬气！", "听完直接让人热血上头！",
                "这才是真男人该说的话！",
            ],
            "emotional": [
                "这句话，戳中了多少人的痛点。", "简单一句话，却包含了千言万语。",
                "有些话，说出来就是一辈子。", "听到这里，眼泪忍不住了。",
            ],
            "humorous": [
                "听听，这说的是人话吗？", "这对话给我整不会了。",
                "好家伙，搁这说相声呢？", "你是懂聊天的。",
            ],
            "calm": [
                "这段对话意味深长。", "简单的对话，交代了故事背景。",
                "人物情感在对话中自然流露。",
            ],
        },
        "emotion": {
            "passionate": [
                "这一幕直接让人泪目！", "破防了家人们！",
            ],
            "emotional": [
                "所有的情绪在这一刻爆发。", "有些遗憾，终究是错过了。",
                "原来最痛苦的是笑着说再见。", "世界上最远的距离，莫过于此。",
                "这一眼，便是永别。",
            ],
            "humorous": [
                "他哭得好伤心啊（我差点就信了）。", "这演技，不去奥斯卡可惜了。",
                "别骂了别骂了，再骂人要傻了。",
            ],
            "calm": [
                "情感在这一刻得到了升华。", "所有的铺垫都为了这一幕。",
                "人物的情感在这里得到了释放。",
            ],
        },
        "funny": {
            "passionate": [
                "这波操作给我笑不活了！",
            ],
            "emotional": [
                "笑着笑着就哭了。", "喜剧的内核是悲剧啊。",
            ],
            "humorous": [
                "笑不活了家人们！", "这就是人类的迷惑行为吗？",
                "笑死，根本停不下来。", "这操作给我整笑了。",
                "导演你是懂搞笑的。",
            ],
            "calm": [
                "这一段充满了喜剧色彩。", "轻松幽默的桥段。",
            ],
        },
        "famous_scene": {
            "passionate": [
                "全网最火名场面来了！", "这段我也就看了几十遍！",
                "经典永流传！", "前方史诗级名场面！",
            ],
            "emotional": [
                "这一幕，成为了永恒的经典。", "多少人因为这一幕看完了整部作品。",
                "童年回忆瞬间涌上心头。",
            ],
            "humorous": [
                "前方名场面预警！", "全网翻拍最多的片段来了！",
                "DNA动了！",
            ],
            "calm": [
                "这是整部作品最经典的片段之一。", "相信很多人对这段都印象深刻。",
                "这个镜头成为了影史经典。",
            ],
        },
        "effect": {
            "passionate": [
                "这特效值十块钱！", "经费在燃烧！", "视觉效果直接拉满！",
                "这特效甩国产剧十条街！",
            ],
            "emotional": [
                "华丽的特效背后，是制作组的匠心。",
            ],
            "humorous": [
                "特效师：我尽力了。", "这五毛钱花得值。",
            ],
            "calm": [
                "特效制作十分精良。", "视觉效果十分震撼。",
            ],
        },
        "landscape": {
            "passionate": [
                "这景色也太美了吧！",
            ],
            "emotional": [
                "美景依旧，故人已不在。", "看到这样的风景，心情也平静了下来。",
            ],
            "humorous": [
                "这地方我去过（在梦里）。",
            ],
            "calm": [
                "美丽的风景让人心旷神怡。", "空镜头往往能烘托氛围。",
            ],
        },
    }
    
    # 结尾模板
    ENDINGS = {
        "passionate": [
            "关注我，下期更精彩！", "这波操作你给几分？",
            "好了，今天的视频就到这里，我们下期再见！",
        ],
        "emotional": [
            "故事到这里就结束了。", "愿所有的美好，都能被温柔以待。",
            "感谢观看。",
        ],
        "humorous": [
            "好了家人们，今天就到这，喜欢的点个赞！",
            "笑完别忘了点关注哦！", "咱下期接着乐！",
        ],
        "calm": [
            "今天的解说就到这里。", "感谢大家观看。",
            "我们下期再见。",
        ],
    }
    
    def __init__(self):
        random.seed(42)
    
    def generate(
        self,
        clips: List,
        style: str = "passionate",
        total_duration: Optional[float] = None,
        custom_prompt: str = "",
    ) -> List[CommentarySegment]:
        """
        根据选中片段生成解说文案
        注意：时间坐标为**成片后**的时间轴，不是原视频时间
        
        Args:
            clips: 选中的片段列表(SelectedClip)
            style: 解说风格 passionate/emotional/humorous/calm
            total_duration: 目标总时长，用于调整语速和文案长度
            custom_prompt: 用户自定义提示词（预留）
        
        Returns:
            解说片段列表，start_time/end_time为成片时间轴
        """
        logger.info(f"[Commentary] 开始生成解说文案, 风格={style}, 片段数={len(clips)}")
        
        if style not in self.OPENINGS:
            style = "calm"
            logger.warning(f"[Commentary] 未知风格 {style}, 使用平静解说风格")
        
        segments = []
        total_clips = len(clips)
        
        if total_clips == 0:
            return segments
        
        clip_timeline = []
        current_pos = 0.0
        for i, clip in enumerate(clips):
            clip_start_in_composed = current_pos
            clip_end_in_composed = current_pos + clip.duration
            clip_timeline.append((clip_start_in_composed, clip_end_in_composed))
            current_pos = clip_end_in_composed
        
        composed_total_duration = current_pos
        logger.debug(f"[Commentary] 成片总时长: {composed_total_duration:.2f}s")
        
        opening = random.choice(self.OPENINGS[style])
        opening_dur = self._estimate_tts_duration(opening, style)
        opening_end = min(opening_dur + 0.3, clip_timeline[0][0] + 0.5 if total_clips > 0 else opening_dur)
        segments.append(CommentarySegment(
            start_time=0.1,
            end_time=opening_end,
            text=opening,
        ))
        
        last_voice_end = segments[-1].end_time
        
        for i, clip in enumerate(clips):
            clip_comp_start, clip_comp_end = clip_timeline[i]
            content_type = clip.metadata.get("content_type", "action")
            clip_dur_comp = clip_comp_end - clip_comp_start
            
            if clip_dur_comp < 1.8:
                continue
            
            if i > 0:
                gap = clip_comp_start - last_voice_end
                if gap < 0.8 and random.random() < 0.7:
                    continue
            
            templates = self.COMMENT_TEMPLATES.get(content_type, self.COMMENT_TEMPLATES["action"])
            style_templates = templates.get(style, templates["calm"])
            
            use_transition = i > 0 and gap > 1.5 and random.random() < 0.35
            text = ""
            if use_transition and i < total_clips - 1:
                transition = random.choice(self.TRANSITIONS[style])
                text += transition
            
            comment = random.choice(style_templates)
            text += comment
            
            tts_dur = self._estimate_tts_duration(text, style)
            
            max_allowed_dur = clip_comp_end - max(last_voice_end, clip_comp_start + 0.3) - 0.3
            if max_allowed_dur < tts_dur * 0.7:
                continue
            
            voice_start = max(last_voice_end + 0.25, clip_comp_start + 0.3)
            voice_end = min(voice_start + tts_dur, clip_comp_end - 0.2)
            
            if voice_end - voice_start < 1.0:
                continue
            
            segments.append(CommentarySegment(
                start_time=voice_start,
                end_time=voice_end,
                text=text,
            ))
            last_voice_end = voice_end
        
        ending = random.choice(self.ENDINGS[style])
        ending_dur = self._estimate_tts_duration(ending, style)
        final_clip_end = clip_timeline[-1][1]
        if final_clip_end - last_voice_end > ending_dur + 0.8:
            segments.append(CommentarySegment(
                start_time=last_voice_end + 0.4,
                end_time=last_voice_end + 0.4 + ending_dur,
                text=ending,
            ))
        
        full_text = " ".join([s.text for s in segments])
        logger.info(f"[Commentary] 解说文案生成完成, 共{len(segments)}段, 总字数={len(full_text)}")
        for i, seg in enumerate(segments):
            logger.debug(f"  解说{i}: [{seg.start_time:.2f}s-{seg.end_time:.2f}s] {seg.text}")
        logger.debug(f"[Commentary] 完整文案: {full_text}")
        
        return segments
    
    def _estimate_tts_duration(self, text: str, style: str) -> float:
        """估算TTS音频时长，根据风格调整语速"""
        char_count = len(text)
        
        if style == "passionate":
            chars_per_sec = 4.5
        elif style == "humorous":
            chars_per_sec = 4.0
        elif style == "emotional":
            chars_per_sec = 3.0
        else:
            chars_per_sec = 3.5
        
        return char_count / chars_per_sec
    
    def segments_to_srt(self, segments: List[CommentarySegment], output_path: str) -> bool:
        """生成SRT字幕文件"""
        try:
            with open(output_path, 'w', encoding='utf-8') as f:
                for i, seg in enumerate(segments, 1):
                    start = self._format_srt_time(seg.start_time)
                    end = self._format_srt_time(seg.end_time)
                    f.write(f"{i}\n")
                    f.write(f"{start} --> {end}\n")
                    f.write(f"{seg.text}\n\n")
            logger.info(f"[Commentary] SRT字幕已生成: {output_path}")
            return True
        except Exception as e:
            logger.error(f"[Commentary] 生成SRT失败: {e}", exc_info=True)
            return False
    
    def _format_srt_time(self, seconds: float) -> str:
        """格式化时间为SRT格式 HH:MM:SS,mmm"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds % 1) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
