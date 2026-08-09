"""
字幕处理模块
负责字幕的生成、解析、格式化
"""
import re
from typing import List, Dict
from pathlib import Path
from utils.logger import logger


class SubtitleProcessor:
    """字幕处理器"""
    
    def __init__(self):
        """初始化字幕处理器"""
        logger.info("字幕处理器初始化完成")
    
    def generate_srt(self, segments: List[Dict], output_path: str) -> bool:
        """
        生成SRT字幕文件
        
        Args:
            segments: 片段列表 [{"start": float, "end": float, "text": str}, ...]
            output_path: 输出路径
        
        Returns:
            是否成功
        """
        try:
            with open(output_path, "w", encoding="utf-8") as f:
                for i, seg in enumerate(segments, 1):
                    start_time = self._format_srt_time(seg["start"])
                    end_time = self._format_srt_time(seg["end"])
                    text = seg.get("text", "")
                    
                    f.write(f"{i}\n")
                    f.write(f"{start_time} --> {end_time}\n")
                    f.write(f"{text}\n\n")
            
            logger.info(f"SRT字幕生成成功: {output_path}")
            return True
        
        except Exception as e:
            logger.error(f"SRT字幕生成失败: {e}")
            return False
    
    def parse_srt(self, srt_path: str) -> List[Dict]:
        """
        解析SRT字幕文件
        
        Args:
            srt_path: SRT文件路径
        
        Returns:
            字幕片段列表 [{"start": float, "end": float, "text": str}, ...]
        """
        try:
            with open(srt_path, "r", encoding="utf-8") as f:
                content = f.read()
            
            # 使用正则表达式解析SRT格式
            pattern = r"(\d+)\n(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})\n(.+?)(?=\n\n|\n*$)"
            matches = re.findall(pattern, content, re.DOTALL)
            
            segments = []
            for match in matches:
                index, start_str, end_str, text = match
                
                start_time = self._parse_srt_time(start_str)
                end_time = self._parse_srt_time(end_str)
                
                segments.append({
                    "start": start_time,
                    "end": end_time,
                    "text": text.strip()
                })
            
            logger.info(f"SRT字幕解析成功: {len(segments)}条")
            return segments
        
        except Exception as e:
            logger.error(f"SRT字幕解析失败: {e}")
            return []
    
    def generate_ass(self, segments: List[Dict], output_path: str,
                    font_name: str = "Microsoft YaHei",
                    font_size: int = 20,
                    primary_color: str = "&H00FFFFFF",
                    outline_color: str = "&H00000000",
                    outline_width: int = 2) -> bool:
        """
        生成ASS字幕文件（支持样式）
        
        Args:
            segments: 片段列表
            output_path: 输出路径
            font_name: 字体名称
            font_size: 字体大小
            primary_color: 主颜色（BGR格式）
            outline_color: 描边颜色
            outline_width: 描边宽度
        
        Returns:
            是否成功
        """
        try:
            with open(output_path, "w", encoding="utf-8") as f:
                # ASS文件头
                f.write("[Script Info]\n")
                f.write("Title: FireClip Subtitle\n")
                f.write("ScriptType: v4.00+\n")
                f.write("WrapStyle: 0\n")
                f.write("ScaledBorderAndShadow: yes\n")
                f.write("YCbCr Matrix: None\n")
                f.write("PlayResX: 1920\n")
                f.write("PlayResY: 1080\n\n")
                
                # 样式定义
                f.write("[V4+ Styles]\n")
                f.write("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
                       "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
                       "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
                       "Alignment, MarginL, MarginR, MarginV, Encoding\n")
                
                f.write(f"Style: Default,{font_name},{font_size},{primary_color},"
                       "&H000000FF,{outline_color},&H00000000,0,0,0,0,100,100,0,0,1,"
                       f"{outline_width},0,2,10,10,10,1\n\n")
                
                # 事件
                f.write("[Events]\n")
                f.write("Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
                
                for seg in segments:
                    start_time = self._format_ass_time(seg["start"])
                    end_time = self._format_ass_time(seg["end"])
                    text = seg.get("text", "").replace("\n", "\\N")
                    
                    f.write(f"Dialogue: 0,{start_time},{end_time},Default,,0,0,0,,{text}\n")
            
            logger.info(f"ASS字幕生成成功: {output_path}")
            return True
        
        except Exception as e:
            logger.error(f"ASS字幕生成失败: {e}")
            return False
    
    def _format_srt_time(self, seconds: float) -> str:
        """将秒数格式化为SRT时间格式 (HH:MM:SS,mmm)"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds % 1) * 1000)
        
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
    
    def _format_ass_time(self, seconds: float) -> str:
        """将秒数格式化为ASS时间格式 (H:MM:SS.cc)"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        centis = int((seconds % 1) * 100)
        
        return f"{hours:d}:{minutes:02d}:{secs:02d}.{centis:02d}"
    
    def _parse_srt_time(self, time_str: str) -> float:
        """解析SRT时间格式为秒数"""
        match = re.match(r"(\d{2}):(\d{2}):(\d{2}),(\d{3})", time_str)
        if match:
            hours, minutes, seconds, millis = map(int, match.groups())
            return hours * 3600 + minutes * 60 + seconds + millis / 1000
        return 0.0
    
    def merge_subtitles(self, subtitle_files: List[str], output_path: str) -> bool:
        """
        合并多个字幕文件
        
        Args:
            subtitle_files: 字幕文件路径列表
            output_path: 输出路径
        
        Returns:
            是否成功
        """
        try:
            all_segments = []
            
            for srt_file in subtitle_files:
                segments = self.parse_srt(srt_file)
                all_segments.extend(segments)
            
            # 按时间排序
            all_segments.sort(key=lambda x: x["start"])
            
            # 生成新的SRT文件
            return self.generate_srt(all_segments, output_path)
        
        except Exception as e:
            logger.error(f"字幕合并失败: {e}")
            return False


# 测试代码
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("用法: python subtitle.py <command> <args...>")
        print("命令:")
        print("  generate <output.srt> <start1> <end1> <text1> ...")
        print("  parse <input.srt>")
        sys.exit(1)
    
    processor = SubtitleProcessor()
    command = sys.argv[1]
    
    if command == "generate":
        if len(sys.argv) < 5:
            print("用法: generate <output.srt> <start1> <end1> <text1> ...")
            sys.exit(1)
        
        output = sys.argv[2]
        args = sys.argv[3:]
        
        segments = []
        for i in range(0, len(args), 3):
            if i + 2 < len(args):
                segments.append({
                    "start": float(args[i]),
                    "end": float(args[i + 1]),
                    "text": args[i + 2]
                })
        
        success = processor.generate_srt(segments, output)
        print(f"字幕生成{'成功' if success else '失败'}")
    
    elif command == "parse":
        if len(sys.argv) < 3:
            print("用法: parse <input.srt>")
            sys.exit(1)
        
        srt_file = sys.argv[2]
        segments = processor.parse_srt(srt_file)
        
        print(f"解析到 {len(segments)} 条字幕:")
        for seg in segments[:5]:  # 只显示前5条
            print(f"  {seg['start']:.2f}s - {seg['end']:.2f}s: {seg['text']}")
    
    else:
        print(f"未知命令: {command}")
