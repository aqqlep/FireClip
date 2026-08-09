"""
解说文案生成器 — 独立于 AnalysisController，仅依赖片段数据
支持 AI 生成 / 模板生成 / 纯手动三种模式
"""
import os
import gc
import json
import re
import random
from pathlib import Path
from typing import List, Dict, Optional
from utils.logger import logger
from config import CONFIG


# ============ 解说风格模板库 ============
_STYLE_TEMPLATES = {
    "专业解说": {
        "action": [
            "这段动作戏设计精妙，{desc}，每一个招式都经过精心编排。",
            "注意看，{desc}，动作导演在这里展现了极高的专业水准。",
            "精彩的动作场面，{desc}，节奏把控恰到好处。"
        ],
        "vfx_action": [
            "特效与动作完美结合，{desc}，视觉冲击力十足。",
            "这段特效动作戏，{desc}，制作团队功力深厚。",
            "特效炸裂的名场面，{desc}，技术水准令人叹服。"
        ],
        "highlight": [
            "全片的经典时刻，{desc}，这一幕注定让人难忘。",
            "高能场面来袭，{desc}，导演在这里安排了全剧最精彩的桥段。",
            "名场面再现，{desc}，这一段堪称全片精华。"
        ],
        "dialog": [
            "这段对白极具张力，{desc}，编剧的文字功底可见一斑。",
            "注意听，{desc}，台词字字珠玑。",
            "经典台词，{desc}，寥寥数语便道尽人物内心。"
        ],
        "emotion": [
            "情感爆发的高光时刻，{desc}，演技细腻入微。",
            "这一段情感戏，{desc}，直击观众内心最柔软的地方。",
            "高能情感场面，{desc}，演员的表演极具感染力。"
        ],
        "climax": [
            "全片高潮，{desc}，前期的铺垫在这一刻全面爆发。",
            "巅峰时刻到来，{desc}，剧情张力达到最高点。",
            "高潮迭起，{desc}，这是全剧最激动人心的瞬间。"
        ],
        "unknown": [
            "精彩片段，{desc}。",
        ]
    },
    "轻松吐槽": {
        "action": [
            "来了来了！{desc}，燃起来了朋友们！",
            "哎哟这动作，{desc}，看得我肾上腺素飙升！",
            "前方高能！{desc}，这段真的太帅了！"
        ],
        "vfx_action": [
            "特效经费在燃烧！{desc}，特效组这是加班了吧！",
            "我的天，{desc}，这特效也太顶了！",
            "特效炸裂预警，{desc}，眼睛要被闪瞎了！"
        ],
        "highlight": [
            "名场面来了！{desc}，这段可以反复看一百遍！",
            "注意看！{desc}，这里不看后悔系列！",
            "精彩名场面，{desc}，就问你帅不帅！"
        ],
        "dialog": [
            "这段台词绝了，{desc}，编剧上分！",
            "听听这台词，{desc}，简直神来之笔！",
            "经典发言，{desc}，建议全文背诵！"
        ],
        "emotion": [
            "呜呜呜，{desc}，这里真的太好哭了！",
            "破防了家人们，{desc}，眼泪止不住！",
            "这里真的绝了，{desc}，谁看谁哭！"
        ],
        "climax": [
            "全片最燃时刻！{desc}，鸡皮疙瘩都起来了！",
            "来了来了！{desc}，这才是真正的高潮！",
            "燃起来了！{desc}，看到这里谁不激动！"
        ],
        "unknown": [
            "这段不错，{desc}。"
        ]
    },
    "\u60ac\u7591\u60ac\u5ff5": {
        "action": [
            "\u5c31\u5728\u8fd9\u65f6\uff0c{desc}\uff0c\u4e8b\u6001\u6025\u8f6c\u76f4\u4e0b\u3002",
            "\u7a81\u5982\u5176\u6765\u7684\u53d8\u6545\uff0c{desc}\uff0c\u4e00\u5207\u4f3c\u4e4e\u65e9\u5df2\u6ce8\u5b9a\u3002",
            "\u5371\u9669\u964d\u4e34\uff0c{desc}\uff0c\u8c01\u4e5f\u65e0\u6cd5\u7f6e\u8eab\u4e8b\u5916\u3002"
        ],
        "vfx_action": [
            "\u8be1\u5f02\u7684\u5f02\u8c61\u51fa\u73b0\uff0c{desc}\uff0c\u672a\u77e5\u7684\u529b\u91cf\u7684\u5f00\u59cb\u89c9\u9192\u3002",
            "\u4e0d\u53ef\u601d\u8bae\u7684\u753b\u9762\uff0c{desc}\uff0c\u8fd9\u4e00\u5207\u80cc\u540e\u7a76\u7adf\u9690\u85cf\u7740\u4ec0\u4e48\uff1f",
            "\u5f02\u53d8\u7a81\u751f\uff0c{desc}\uff0c\u771f\u5047\u96be\u8fa8\u3002"
        ],
        "highlight": [
            "\u5173\u952e\u7684\u4e00\u5e55\u51fa\u73b0\u4e86\uff0c{desc}\uff0c\u771f\u76f8\u4f3c\u4e4e\u5c31\u5728\u773c\u524d\u3002",
            "\u8fd9\u4e2a\u7ec6\u8282\u81f3\u5173\u91cd\u8981\uff0c{desc}\uff0c\u4f60\u6ce8\u610f\u5230\u4e86\u5417\uff1f",
            "\u8c1c\u5e95\u5373\u5c06\u63ed\u6653\uff0c{desc}\uff0c\u4f46\u4e8b\u60c5\u6ca1\u90a3\u4e48\u7b80\u5355\u3002"
        ],
        "dialog": [
            "\u610f\u5473\u6df1\u957f\u7684\u8bdd\u8bed\uff0c{desc}\uff0c\u6bcf\u4e00\u53e5\u90fd\u6697\u85cf\u7384\u673a\u3002",
            "\u8fd9\u756a\u8bdd\u7edd\u4e0d\u7b80\u5355\uff0c{desc}\uff0c\u80cc\u540e\u4e00\u5b9a\u53e6\u6709\u9690\u60c5\u3002",
            "\u5b57\u91cc\u884c\u95f4\u5168\u662f\u6697\u793a\uff0c{desc}\uff0c\u771f\u76f8\u8fdc\u6bd4\u8868\u9762\u590d\u6742\u3002"
        ],
        "emotion": [
            "\u60c5\u611f\u7684\u8f6c\u6298\u70b9\uff0c{desc}\uff0c\u4eba\u7269\u5185\u5fc3\u5230\u5e95\u7ecf\u5386\u4e86\u4ec0\u4e48\uff1f",
            "\u8fd9\u4e00\u523b\u7684\u5d29\u6e83\uff0c{desc}\uff0c\u662f\u771f\u60c5\u6d41\u9732\u8fd8\u662f\u53e6\u6709\u76ee\u7684\uff1f",
            "\u60c5\u601f\u7684\u7206\u53d1\uff0c{desc}\uff0c\u80cc\u540e\u9690\u85cf\u7740\u4e0d\u4e3a\u4eba\u77e5\u7684\u6545\u4e8b\u3002"
        ],
        "climax": [
            "\u6240\u6709\u7ebf\u7d22\u6c47\u805a\u4e8e\u6b64\uff0c{desc}\uff0c\u771f\u76f8\u5373\u5c06\u5927\u767d\u3002",
            "\u8ff7\u96fe\u6563\u53bb\u7684\u65f6\u523b\uff0c{desc}\uff0c\u4e00\u5207\u90fd\u4e32\u8054\u8d77\u6765\u4e86\u3002",
            "\u7ec8\u5c40\u964d\u4e34\uff0c{desc}\uff0c\u6700\u540e\u7684\u7b54\u6848\u51fa\u4e4e\u6240\u6709\u4eba\u7684\u610f\u6599\u3002"
        ],
        "unknown": [
            "\u8010\u4eba\u5bfb\u5473\uff0c{desc}\u3002"
        ]
    },
    "\u70ed\u8840\u71c3\u5411": {
        "action": [
            "\u71c3\u7206\u4e86\uff01{desc}\uff0c\u8fd9\u624d\u53eb\u771f\u6b63\u7684\u6218\u6597\uff01",
            "\u706b\u529b\u5168\u5f00\uff01{desc}\uff0c\u6bcf\u4e00\u5e27\u90fd\u662f\u7206\u70b8\uff01",
            "\u6218\u6597\u5f00\u59cb\uff01{desc}\uff0c\u7edd\u4e0d\u9000\u7f29\uff01"
        ],
        "vfx_action": [
            "\u89c6\u89c9\u9707\u64bc\uff01{desc}\uff0c\u7279\u6548\u7ecf\u8d39\u5168\u90e8\u70e7\u5b8c\uff01",
            "\u7206\u88c2\u7279\u6548\uff01{desc}\uff0c\u773c\u775b\u90fd\u4e0d\u591f\u7528\u4e86\uff01",
            "\u5929\u5730\u5d29\u88c2\uff01{desc}\uff0c\u8fd9\u624d\u53eb\u5927\u573a\u9762\uff01"
        ],
        "highlight": [
            "\u540d\u573a\u9762\u8b66\u62a5\uff01{desc}\uff0c\u5168\u8eab\u7ec6\u80de\u90fd\u71c3\u8d77\u6765\u4e86\uff01",
            "\u6781\u81f4\u7206\u53d1\uff01{desc}\uff0c\u8fd9\u4e00\u523b\u5c01\u795e\uff01",
            "\u6700\u5f3a\u9ad8\u80fd\uff01{desc}\uff0c\u8fd9\u624d\u662f\u771f\u6b63\u7684\u7edd\u6740\u77ac\u95f4\uff01"
        ],
        "dialog": [
            "\u7206\u88c2\u53d1\u8a00\uff01{desc}\uff0c\u8fd9\u53e5\u8bdd\u70b9\u71c3\u4e86\u6240\u6709\u4eba\uff01",
            "\u8c6a\u8a00\u58ee\u8bed\uff01{desc}\uff0c\u8840\u8109\u504c\u5f20\uff01",
            "\u5c71\u6cb3\u7834\u788e\u822c\u7684\u8a00\u8bed\uff01{desc}\uff0c\u6bcf\u4e2a\u5b57\u90fd\u5728\u71c3\u70e7\uff01"
        ],
        "emotion": [
            "\u70ed\u8840\u6cb8\u817e\uff01{desc}\uff0c\u5fc3\u4e2d\u7684\u706b\u7130\u518d\u4e5f\u538b\u4e0d\u4f4f\uff01",
            "\u6124\u6012\u7206\u53d1\uff01{desc}\uff0c\u8fd9\u4e00\u523b\u7684\u529b\u91cf\u65e0\u4eba\u80fd\u6321\uff01",
            "\u5fc3\u810f\u5728\u71c3\u70e7\uff01{desc}\uff0c\u8fd9\u5c31\u662f\u4e0d\u5c48\u7684\u529b\u91cf\uff01"
        ],
        "climax": [
            "\u6781\u9650\u7206\u53d1\uff01{desc}\uff0c\u8fd9\u4e00\u523b\u4e16\u754c\u90fd\u5728\u98a4\u6296\uff01",
            "\u5de5\u53e3\u7206\u53d1\uff01{desc}\uff0c\u6240\u6709\u4eba\u90fd\u7ed9\u6211\u71c3\u8d77\u6765\uff01",
            "\u7edd\u5883\u53cd\u51fb\uff01{desc}\uff0c\u6700\u540e\u7684\u80dc\u5229\u5c5e\u4e8e\u6211\u4eec\uff01"
        ],
        "unknown": [
            "\u71c3\uff01{desc}\uff01"
        ]
    },
    "\u6df1\u60c5\u8d70\u5fc3": {
        "action": [
            "\u6bcf\u4e00\u62f3\u90fd\u627f\u8f7d\u7740\u65e0\u8a00\u7684\u6323\u624e\uff0c{desc}\uff0c\u8c01\u53c8\u80fd\u61c2\u8fd9\u4efd\u575a\u5f3a\u80cc\u540e\u7684\u5fc3\u9178\u3002",
            "\u8fd9\u4e0d\u662f\u5355\u7eaf\u7684\u6218\u6597\uff0c{desc}\uff0c\u662f\u5c5e\u4e8e\u4ed6\u7684\u6700\u540e\u5b88\u62a4\u3002",
            "\u529b\u91cf\u4e0e\u67d4\u60c5\u4ea4\u7ec7\uff0c{desc}\uff0c\u6bcf\u4e00\u62db\u90fd\u662f\u5bf9\u547d\u8fd0\u7684\u62b5\u6297\u3002"
        ],
        "vfx_action": [
            "\u591a\u4e48\u58ee\u89c2\u7684\u753b\u9762\uff0c{desc}\uff0c\u53ef\u8c01\u53c8\u77e5\u9053\u8fd9\u5149\u8292\u80cc\u540e\u7684\u4ee3\u4ef7\u3002",
            "\u89c6\u89c9\u5947\u89c2\u80cc\u540e\uff0c{desc}\uff0c\u662f\u65e0\u5c3d\u7684\u727a\u7272\u4e0e\u6210\u5c31\u3002",
            "\u5149\u5f71\u4e0e\u6ce5\u6c93\u5e76\u5b58\uff0c{desc}\uff0c\u8fd9\u4efd\u7f8e\u4e3d\u6765\u4e4b\u4e0d\u6613\u3002"
        ],
        "highlight": [
            "\u8fd9\u4e00\u523b\uff0c\u6240\u6709\u7b49\u5f85\u90fd\u503c\u5f97\u4e86\uff0c{desc}\uff0c\u65f6\u5149\u5728\u8fd9\u4e00\u79d2\u51dd\u56fa\u3002",
            "\u5168\u7247\u6700\u6e29\u67d4\u7684\u77ac\u95f4\uff0c{desc}\uff0c\u89e6\u52a8\u4e86\u5fc3\u5e95\u6700\u6df1\u7684\u5f26\u3002",
            "\u65e0\u6cd5\u590d\u5236\u7684\u611f\u52a8\uff0c{desc}\uff0c\u8fd9\u4e00\u5e55\u503c\u5f97\u6c38\u8fdc\u73cd\u85cf\u3002"
        ],
        "dialog": [
            "\u7b80\u5355\u7684\u8bdd\u8bed\uff0c{desc}\uff0c\u5374\u91cd\u91cd\u5730\u7838\u5728\u5fc3\u4e0a\u3002",
            "\u6709\u4e9b\u8bdd\u4e0d\u9700\u8981\u592a\u591a\u89e3\u91ca\uff0c{desc}\uff0c\u61c2\u7684\u4eba\u81ea\u7136\u4f1a\u61c2\u3002",
            "\u4e00\u53e5\u8bdd\u62c6\u5f00\u4e86\u5fc3\u5899\uff0c{desc}\uff0c\u5374\u4e5f\u6696\u4e86\u5fc3\u623f\u3002"
        ],
        "emotion": [
            "\u8fd9\u4e00\u523b\uff0c\u6240\u6709\u7684\u575a\u5f3a\u90fd\u5316\u4f5c\u4e86\u773c\u6cea\uff0c{desc}\uff0c\u5fc3\u4e2d\u6700\u67d4\u8f6f\u7684\u5730\u65b9\u88ab\u89e6\u78b0\u3002",
            "\u6ce5\u6c93\u4e2d\u7684\u6e29\u67d4\uff0c{desc}\uff0c\u662f\u8fd9\u4e16\u754c\u6700\u73cd\u8d35\u7684\u5149\u3002",
            "\u65e0\u58f0\u7684\u6cea\u6c34\uff0c{desc}\uff0c\u5374\u80dc\u8fc7\u4e07\u8bed\u5343\u8a00\u3002"
        ],
        "climax": [
            "\u6240\u6709\u7684\u7b49\u5f85\u90fd\u6709\u4e86\u7b54\u6848\uff0c{desc}\uff0c\u8fd9\u4e00\u523b\u503c\u5f97\u4e00\u5207\u3002",
            "\u5c5e\u4e8e\u6211\u4eec\u7684\u9ad8\u5149\u65f6\u523b\uff0c{desc}\uff0c\u611f\8c22\u6bcf\u4e00\u6b21\u7684\u575a\u6301\u3002",
            "\u5386\u7ecf\u5343\u5e06\uff0c{desc}\uff0c\u7ec8\u4e8e\u62e5\u62b1\u4e86\u5149\u3002"
        ],
        "unknown": [
            "\u5fc3\u4e2d\u6709\u611f\uff0c{desc}\u3002"
        ]
    }
}

STYLE_NAMES = list(_STYLE_TEMPLATES.keys())


class CommentaryTextGenerator:
    """解说文案生成器 — 独立模块，不依赖 AnalysisController"""

    def __init__(self):
        self._text_model = None
        self._tokenizer = None

    # ================================================================
    # 公开接口
    # ================================================================
    def generate(self, segments: List[Dict],
                 style: str = "专业解说",
                 mode: str = "template",
                 progress_callback: Optional[callable] = None) -> Dict:
        """
        生成解说文案

        Args:
            segments: 片段列表 [{"start_time", "end_time", "scene_type", "description", ...}]
            style: 解说风格 ("专业解说" / "轻松吐槽" / "悬疑悬念")
            mode: 生成模式 ("template" / "ai" / "manual")
            progress_callback: 进度回调

        Returns:
            {"segments": [...], "full_text": str, "duration": float}
        """
        if not segments:
            return {"segments": [], "full_text": "", "duration": 0}

        if mode == "ai":
            return self._generate_ai(segments, style, progress_callback)
        else:
            return self._generate_template(segments, style)

    # ================================================================
    # 模板生成
    # ================================================================
    def _generate_template(self, segments: List[Dict], style: str) -> Dict:
        """\u4f7f\u7528\u98ce\u683c\u6a21\u677f\u751f\u6210\u6587\u6848\uff0c\u6587\u6848\u957f\u5ea6\u81ea\u9002\u5e94\u7247\u6bb5\u65f6\u957f"""
        templates = _STYLE_TEMPLATES.get(style, _STYLE_TEMPLATES["\u4e13\u4e1a\u89e3\u8bf4"])
        result_segments = []
        full_text = ""
    
        for idx, seg in enumerate(segments):
            scene_type = seg.get("scene_type", "unknown")
            description = seg.get("description", "\u7cbe\u5f69\u7247\u6bb5")
            duration = seg.get("duration", seg.get("end_time", 0) - seg.get("start_time", 0))
    
            template_list = templates.get(scene_type, templates.get("unknown", ["{desc}\u3002"]))
            text = random.choice(template_list).format(desc=description)
    
            # \u6587\u6848\u957f\u5ea6\u81ea\u9002\u5e94\u7247\u6bb5\u65f6\u957f\uff08\u8bed\u901f\u7ea6 4-5 \u5b57/\u79d2\uff09
            text = self._adapt_text_length(text, duration)
    
            # \u6dfb\u52a0\u8fc7\u6e21\u53e5\uff08\u975e\u9996\u7247\u65f6\uff09
            if idx > 0 and style in ("\u4e13\u4e1a\u89e3\u8bf4", "\u70ed\u8840\u71c3\u5411", "\u6df1\u60c5\u8d70\u5fc3") :
                transition = self._get_transition(style, idx, len(segments))
                if transition:
                    text = transition + text
    
            result_segments.append({
                "start_time": seg.get("start_time", 0),
                "end_time": seg.get("end_time", 0),
                "duration": duration,
                "text": text,
                "scene_type": scene_type
            })
            full_text += text + "\n\n"
    
        total_duration = sum(s["duration"] for s in result_segments)
    
        return {
            "segments": result_segments,
            "full_text": full_text.strip(),
            "duration": total_duration
        }
    
    @staticmethod
    def _adapt_text_length(text: str, duration: float) -> str:
        """\u6839\u636e\u7247\u6bb5\u65f6\u957f\u8c03\u6574\u6587\u6848\u957f\u5ea6\n        \u8bed\u901f\u7ea6 4-5 \u5b57/\u79d2\uff0c\u77ed\u7247\u6bb5\u88c1\u526a\u6587\u6848\uff0c\u957f\u7247\u6bb5\u4fdd\u7559\u539f\u6587\u6848
        """
        max_chars = int(duration * 4.5)  # \u6bcf\u79d2\u7ea64.5\u5b57
        min_chars = int(duration * 2.5)  # \u6700\u5c11\u7ea62.5\u5b57/\u79d2
        char_count = len(text.replace(" ", ""))
    
        if char_count <= max_chars:
            return text  # \u6587\u6848\u8db3\u591f\u77ed\uff0c\u4e0d\u9700\u8c03\u6574
    
        # \u6587\u6848\u592a\u957f\uff0c\u9700\u8981\u622a\u65ad\n        if char_count > max_chars * 1.5:
            # \u4e25\u91cd\u8d85\u957f\uff0c\u53d6\u524d\u534a\u90e8\u5206 + \u7701\u7565\u53f7
            truncate_at = max(min_chars, int(max_chars * 0.9))
            # \u5728\u6700\u8fd1\u7684\u6807\u70b9\u5904\u622a\u65ad
            for sep in ['\u3002', '\uff0c', '\uff01', '\uff1f', '\uff1b']:
                pos = text.rfind(sep, 0, truncate_at + 5)
                if pos > 0:
                    return text[:pos + 1]
            return text[:truncate_at] + "\u2026\u2026"
        else:
            # \u8f7b\u5ea6\u8d85\u957f\uff0c\u76f4\u63a5\u622a\u65ad\u5230\u5408\u9002\u4f4d\u7f6e
            for sep in ['\u3002', '\uff0c', '\uff01', '\uff1f', '\uff1b']:
                pos = text.rfind(sep, 0, max_chars + 5)
                if pos > 0:
                    return text[:pos + 1]
            return text
    
    @staticmethod
    def _get_transition(style: str, idx: int, total: int) -> str:
        """\u751f\u6210\u7247\u6bb5\u95f4\u8fc7\u6e21\u53e5"""
        transitions = {
            "\u4e13\u4e1a\u89e3\u8bf4": [
                "\u7d27\u63a5\u7740\uff0c", "\u968f\u540e\uff0c", "\u53e6\u4e00\u8fb9\uff0c", "\u8fd9\u65f6\uff0c", "\u4e0e\u6b64\u540c\u65f6\uff0c"
            ],
            "\u70ed\u8840\u71c3\u5411": [
                "\u7d27\u63a5\u7740\uff01", "\u8fd8\u6ca1\u5b8c\uff01", "\u7ee7\u7eed\uff01", "\u8fd8\u6709\uff01", "\u522b\u505c\uff01"
            ],
            "\u6df1\u60c5\u8d70\u5fc3": [
                "\u800c\u540e\uff0c", "\u6162\u6162\u5730\uff0c", "\u4e4b\u540e\uff0c", "\u6e10\u6e10\u5730\uff0c", "\u518d\u540e\u6765\uff0c"
            ]
        }
        pool = transitions.get(style, [])
        if not pool:
            return ""
        # \u6839\u636e\u7d22\u5f15\u548c\u603b\u6570\u9009\u62e9\u8fc7\u6e21\u53e5\uff0c\u907f\u514d\u8fde\u7eed\u91cd\u590d
        return pool[idx % len(pool)]

    # ================================================================
    # AI 生成
    # ================================================================
    def _generate_ai(self, segments: List[Dict], style: str,
                     progress_callback: Optional[callable] = None) -> Dict:
        """使用 Qwen2.5-7B 生成文案"""
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer

            if progress_callback:
                progress_callback(0, "加载AI模型...")

            # 加载模型（懒加载）
            if self._text_model is None:
                model_path = self._resolve_model_path()
                if not model_path:
                    logger.warning("文本模型路径不存在，回退模板生成")
                    return self._generate_template(segments, style)

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

            if progress_callback:
                progress_callback(30, "AI生成文案中...")

            prompt = self._build_prompt(segments, style)
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
            del inputs, outputs
            torch.cuda.empty_cache()

            if progress_callback:
                progress_callback(80, "解析AI响应...")

            return self._parse_ai_response(response, segments, style)

        except ImportError as e:
            logger.warning(f"AI生成依赖缺失: {e}，回退模板生成")
            return self._generate_template(segments, style)
        except Exception as e:
            logger.error(f"AI文案生成失败: {e}，回退模板生成")
            return self._generate_template(segments, style)

    def _resolve_model_path(self) -> Optional[Path]:
        """解析文本模型路径"""
        model_name = CONFIG.local_models.text_model
        model_path = Path(model_name)
        if not model_path.is_absolute():
            project_root = Path(__file__).parent.parent
            if not str(model_name).startswith('models_cache'):
                model_path = project_root / 'models_cache' / model_name
            else:
                model_path = project_root / model_name
        return model_path if model_path.exists() else None

    def _build_prompt(self, segments: List[Dict], style: str) -> str:
        """构建 AI 提示词"""
        style_desc = {
            "\u4e13\u4e1a\u89e3\u8bf4": "\u4e13\u4e1a\u5f71\u89c6\u89e3\u8bf4\uff0c\u8bed\u8a00\u4e25\u8c28\u6709\u6df1\u5ea6\uff0c\u6ce8\u91cd\u6280\u672f\u5206\u6790",
            "\u8f7b\u677e\u5410\u69fd": "\u8f7b\u677e\u5e7d\u9ed8\u7684\u89e3\u8bf4\u98ce\u683c\uff0c\u7528\u7f51\u7edc\u6d41\u884c\u8bed\u548c\u5410\u69fd\u7684\u65b9\u5f0f",
            "\u60ac\u7591\u60ac\u5ff5": "\u60ac\u7591\u60ac\u5ff5\u98ce\u683c\uff0c\u8425\u9020\u7d27\u5f20\u6c1b\u56f4\uff0c\u8bbe\u7f6e\u60ac\u5ff5\u5438\u5f15\u89c2\u4f17",
            "\u70ed\u8840\u71c3\u5411": "\u70ed\u8840\u71c3\u5411\u98ce\u683c\uff0c\u8bed\u8a00\u7206\u70b8\u611f\u5341\u8db3\uff0c\u8ba9\u89c2\u4f17\u8840\u8109\u504c\u5f20\uff0c\u5927\u91cf\u4f7f\u7528\u611f\u53f9\u53f7\u548c\u71c3\u5411\u8bcd\u6c47",
            "\u6df1\u60c5\u8d70\u5fc3": "\u6df1\u60c5\u8d70\u5fc3\u98ce\u683c\uff0c\u8bed\u8a00\u67d4\u548c\u7ec6\u817b\uff0c\u5145\u6ee1\u611f\u60c5\u8272\u5f69\uff0c\u89e6\u52a8\u89c2\u4f17\u5fc3\u5e95\u6700\u67d4\u8f6f\u7684\u5730\u65b9"
        }.get(style, "\u4e13\u4e1a\u5f71\u89c6\u89e3\u8bf4")

        scenes_desc = []
        for i, seg in enumerate(segments[:15]):
            st = seg.get("start_time", 0)
            et = seg.get("end_time", 0)
            scene_type = seg.get("scene_type", "unknown")
            desc = seg.get("description", "")
            scenes_desc.append(f"{i+1}. 时间{st:.1f}s-{et:.1f}s, 类型:{scene_type}, 描述:{desc}")

        scenes_text = "\n".join(scenes_desc)

        prompt = f"""你是一个专业的影视解说文案撰写者。请根据以下视频高光时刻，撰写一段{style_desc}的解说文案。

视频高光时刻：
{scenes_text}

要求：
1. 风格：{style_desc}
2. 每段文案控制在30-80字，语速自然
3. 文案要与画面内容和场景类型匹配
4. 避免重复使用相同的开场白

请以JSON格式返回：
{{
  "segments": [
    {{"time": 开始时间, "duration": 持续时间, "text": "解说文案", "scene_type": "场景类型"}},
  ],
  "full_text": "完整文案文本"
}}

开始撰写："""
        return prompt

    def _parse_ai_response(self, response: str, segments: List[Dict],
                           style: str) -> Dict:
        """解析 AI 响应，失败则回退模板"""
        try:
            json_match = re.search(r'\{[\s\S]*\}', response)
            if json_match:
                data = json.loads(json_match.group())
                ai_segments = data.get("segments", [])
                full_text = data.get("full_text", "")

                if ai_segments and all("text" in s for s in ai_segments[:3]):
                    # 补齐时间信息
                    for i, seg in enumerate(ai_segments):
                        if i < len(segments):
                            seg.setdefault("start_time", segments[i].get("start_time", 0))
                            seg.setdefault("end_time", segments[i].get("end_time", 0))
                            seg.setdefault("duration", segments[i].get("duration", 5.0))
                            seg.setdefault("scene_type", segments[i].get("scene_type", "unknown"))

                    if not full_text:
                        full_text = "\n\n".join(s.get("text", "") for s in ai_segments)

                    total_duration = sum(s.get("duration", 5.0) for s in ai_segments)
                    return {
                        "segments": ai_segments,
                        "full_text": full_text,
                        "duration": total_duration
                    }

        except Exception as e:
            logger.error(f"解析AI响应失败: {e}")

        return self._generate_template(segments, style)

    # ================================================================
    # 手动模式：将用户输入的纯文本拆分为带时间的段落
    # ================================================================
    @staticmethod
    def split_manual_text(text: str, segments: List[Dict]) -> Dict:
        """
        将用户手动输入的文案按段落分配给片段

        Args:
            text: 用户输入的完整文案（按空行分段）
            segments: 片段列表，用于获取时间信息

        Returns:
            {"segments": [...], "full_text": str, "duration": float}
        """
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        if not paragraphs:
            paragraphs = [text.strip()] if text.strip() else []

        result_segments = []
        for i, para in enumerate(paragraphs):
            if i < len(segments):
                seg = segments[i]
                result_segments.append({
                    "start_time": seg.get("start_time", 0),
                    "end_time": seg.get("end_time", 0),
                    "duration": seg.get("duration", seg.get("end_time", 0) - seg.get("start_time", 0)),
                    "text": para,
                    "scene_type": seg.get("scene_type", "unknown")
                })
            else:
                # 文案段落多于片段时，均匀分配时间
                result_segments.append({
                    "start_time": 0,
                    "end_time": 5.0,
                    "duration": 5.0,
                    "text": para,
                    "scene_type": "unknown"
                })

        total_duration = sum(s["duration"] for s in result_segments)
        return {
            "segments": result_segments,
            "full_text": text.strip(),
            "duration": total_duration
        }

    # ================================================================
    # 资源清理
    # ================================================================
    def unload_model(self):
        """卸载 AI 模型释放显存"""
        if self._text_model is not None:
            import torch
            del self._text_model
            del self._tokenizer
            self._text_model = None
            self._tokenizer = None
            torch.cuda.empty_cache()
            gc.collect()
            logger.info("文本生成模型已卸载，显存已释放")
