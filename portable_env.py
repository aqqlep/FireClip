"""
FireClip 便携式环境管理器
负责管理嵌入式 Python、虚拟环境、FFmpeg 等依赖的路径检测与初始化
"""
import os
import sys
import subprocess
import shutil
from pathlib import Path
from typing import Optional


class PortableEnv:
    """便携式环境管理器"""
    
    # 项目根目录（FireClip 所在目录）
    PROJECT_ROOT = Path(__file__).parent
    
    # 嵌入式 Python 目录
    EMBEDDED_PYTHON_DIR = PROJECT_ROOT / "python-embed"
    
    # 虚拟环境目录
    VENV_DIR = PROJECT_ROOT / "venv"
    
    # 便携式 FFmpeg 目录（项目上级目录中的 ffmpeg）
    FFMPEG_EXTERNAL_DIR = PROJECT_ROOT.parent / "ffmpeg-8.1.1-essentials_build"
    FFMPEG_INTERNAL_DIR = PROJECT_ROOT / "ffmpeg"
    
    # 模型缓存目录
    MODELS_DIR = PROJECT_ROOT / "models_cache"
    
    # pip 自解压包路径
    GET_PIP_PATH = PROJECT_ROOT / "get-pip.py"
    
    def __init__(self):
        self._ffmpeg_path: Optional[str] = None
        self._python_path: Optional[str] = None
        self._site_packages: Optional[str] = None
    
    # ─────────────────── Python 环境 ───────────────────
    
    def get_python_path(self) -> str:
        """获取可用的 Python 解释器路径（优先嵌入式）"""
        if self._python_path:
            return self._python_path
        
        # 1. 检查嵌入式 Python
        embed_python = self.EMBEDDED_PYTHON_DIR / "python.exe"
        if embed_python.exists():
            self._python_path = str(embed_python)
            return self._python_path
        
        # 2. 检查虚拟环境
        venv_python = self.VENV_DIR / "Scripts" / "python.exe"
        if venv_python.exists():
            self._python_path = str(venv_python)
            return self._python_path
        
        # 3. 回退到系统 Python
        self._python_path = sys.executable
        return self._python_path
    
    def is_embedded_python_available(self) -> bool:
        """检查嵌入式 Python 是否已部署"""
        return (self.EMBEDDED_PYTHON_DIR / "python.exe").exists()
    
    def is_venv_available(self) -> bool:
        """检查虚拟环境是否已创建"""
        return (self.VENV_DIR / "Scripts" / "python.exe").exists()
    
    def has_pip(self) -> bool:
        """检查 pip 是否可用"""
        try:
            python = self.get_python_path()
            result = subprocess.run(
                [python, "-m", "pip", "--version"],
                capture_output=True, text=True, timeout=10
            )
            return result.returncode == 0
        except Exception:
            return False
    
    def enable_embedded_pip(self) -> bool:
        """
        为嵌入式 Python 启用 pip
        1. 解除 python3xx._pth 中 import site 的注释
        2. 安装 get-pip.py
        """
        if not self.is_embedded_python_available():
            return False
        
        python_exe = self.EMBEDDED_PYTHON_DIR / "python.exe"
        
        # 找到 _pth 文件并启用 import site
        pth_files = list(self.EMBEDDED_PYTHON_DIR.glob("python*._pth"))
        for pth_file in pth_files:
            content = pth_file.read_text(encoding="utf-8")
            if "#import site" in content:
                content = content.replace("#import site", "import site")
                pth_file.write_text(content, encoding="utf-8")
        
        # 下载 get-pip.py（如果不存在）
        if not self.GET_PIP_PATH.exists():
            try:
                import urllib.request
                urllib.request.urlretrieve(
                    "https://bootstrap.pypa.io/get-pip.py",
                    str(self.GET_PIP_PATH)
                )
            except Exception as e:
                print(f"[PortableEnv] 下载 get-pip.py 失败: {e}")
                return False
        
        # 安装 pip
        try:
            result = subprocess.run(
                [str(python_exe), str(self.GET_PIP_PATH), "--no-warn-script-location"],
                capture_output=True, text=True, timeout=120,
                cwd=str(self.EMBEDDED_PYTHON_DIR)
            )
            return result.returncode == 0
        except Exception as e:
            print(f"[PortableEnv] 安装 pip 失败: {e}")
            return False
    
    # ─────────────────── FFmpeg ───────────────────
    
    def get_ffmpeg_path(self) -> str:
        """获取 FFmpeg 可执行文件路径（优先便携式）"""
        if self._ffmpeg_path:
            return self._ffmpeg_path
        
        # 优先级：项目内 ffmpeg > 上级目录 ffmpeg > 系统 PATH
        candidates = [
            self.FFMPEG_INTERNAL_DIR / "bin" / "ffmpeg.exe",
            self.FFMPEG_EXTERNAL_DIR / "bin" / "ffmpeg.exe",
        ]
        
        for candidate in candidates:
            if candidate.exists():
                self._ffmpeg_path = str(candidate.parent)  # 返回 bin 目录
                return self._ffmpeg_path
        
        # 回退到系统 PATH
        self._ffmpeg_path = "ffmpeg"
        return self._ffmpeg_path
    
    def get_ffprobe_path(self) -> str:
        """获取 FFprobe 路径"""
        ffmpeg_dir = self.get_ffmpeg_path()
        if ffmpeg_dir != "ffmpeg":
            ffprobe = Path(ffmpeg_dir) / "ffprobe.exe"
            if ffprobe.exists():
                return str(ffprobe)
        return "ffprobe"
    
    def is_ffmpeg_available(self) -> bool:
        """检查 FFmpeg 是否可用"""
        ffmpeg_dir = self.get_ffmpeg_path()
        if ffmpeg_dir != "ffmpeg":
            return True
        # 检查系统 PATH
        try:
            result = subprocess.run(
                ["ffmpeg", "-version"],
                capture_output=True, text=True, timeout=5
            )
            return result.returncode == 0
        except Exception:
            return False
    
    def copy_ffmpeg_to_project(self) -> bool:
        """将 FFmpeg 复制到项目内部（用于打包分发）"""
        src = self.FFMPEG_EXTERNAL_DIR / "bin"
        dst = self.FFMPEG_INTERNAL_DIR / "bin"
        
        if not src.exists():
            return False
        
        try:
            dst.mkdir(parents=True, exist_ok=True)
            for exe_file in src.glob("*.exe"):
                shutil.copy2(str(exe_file), str(dst / exe_file.name))
            self._ffmpeg_path = str(dst)
            return True
        except Exception as e:
            print(f"[PortableEnv] 复制 FFmpeg 失败: {e}")
            return False
    
    # ─────────────────── 依赖安装 ───────────────────
    
    def install_requirements(self, requirements_file: Optional[str] = None) -> bool:
        """安装 requirements.txt 中的依赖"""
        python = self.get_python_path()
        
        if requirements_file is None:
            requirements_file = str(self.PROJECT_ROOT / "requirements.txt")
        
        if not os.path.exists(requirements_file):
            print(f"[PortableEnv] requirements.txt 不存在: {requirements_file}")
            return False
        
        try:
            result = subprocess.run(
                [python, "-m", "pip", "install", "-r", requirements_file,
                 "--no-warn-script-location"],
                capture_output=True, text=True, timeout=600
            )
            if result.returncode == 0:
                print("[PortableEnv] 依赖安装成功")
                return True
            else:
                print(f"[PortableEnv] 依赖安装失败:\n{result.stderr}")
                return False
        except Exception as e:
            print(f"[PortableEnv] 安装依赖异常: {e}")
            return False
    
    def install_package(self, package: str) -> bool:
        """安装单个包"""
        python = self.get_python_path()
        try:
            result = subprocess.run(
                [python, "-m", "pip", "install", package, "--no-warn-script-location"],
                capture_output=True, text=True, timeout=300
            )
            return result.returncode == 0
        except Exception:
            return False
    
    # ─────────────────── 模型目录 ───────────────────
    
    def get_models_dir(self) -> Path:
        """获取模型缓存目录（自动创建）"""
        self.MODELS_DIR.mkdir(parents=True, exist_ok=True)
        return self.MODELS_DIR
    
    def get_whisper_model_path(self, model_name: str = "large-v3") -> Optional[Path]:
        """获取 Whisper 模型路径"""
        model_dir = self.get_models_dir() / "whisper"
        model_file = model_dir / f"{model_name}.pt"
        if model_file.exists():
            return model_file
        return None
    
    def get_hf_cache_dir(self) -> Path:
        """获取 HuggingFace 模型缓存目录"""
        cache_dir = self.get_models_dir() / "huggingface"
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir
    
    # ─────────────────── 环境初始化 ───────────────────
    
    def init_env(self) -> dict:
        """
        初始化便携式环境，返回环境状态
        
        Returns:
            dict: {
                "python_ok": bool,
                "pip_ok": bool,
                "ffmpeg_ok": bool,
                "python_path": str,
                "ffmpeg_path": str,
                "messages": list[str]
            }
        """
        status = {
            "python_ok": False,
            "pip_ok": False,
            "ffmpeg_ok": False,
            "python_path": "",
            "ffmpeg_path": "",
            "messages": []
        }
        
        # 检查 Python
        python = self.get_python_path()
        status["python_path"] = python
        try:
            result = subprocess.run(
                [python, "--version"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                status["python_ok"] = True
                status["messages"].append(f"Python: {result.stdout.strip()}")
            else:
                status["messages"].append("Python: 不可用")
        except Exception as e:
            status["messages"].append(f"Python: 检测失败 - {e}")
        
        # 检查 pip
        status["pip_ok"] = self.has_pip()
        if status["pip_ok"]:
            status["messages"].append("pip: 可用")
        else:
            status["messages"].append("pip: 不可用（需要运行 setup）")
        
        # 检查 FFmpeg
        ffmpeg_dir = self.get_ffmpeg_path()
        status["ffmpeg_path"] = ffmpeg_dir
        status["ffmpeg_ok"] = self.is_ffmpeg_available()
        if status["ffmpeg_ok"]:
            status["messages"].append(f"FFmpeg: {ffmpeg_dir}")
        else:
            status["messages"].append("FFmpeg: 未找到")
        
        return status
    
    def apply_to_config(self):
        """将便携式环境路径应用到全局配置"""
        from config import CONFIG
        
        # 设置 FFmpeg 路径
        ffmpeg_dir = self.get_ffmpeg_path()
        if ffmpeg_dir != "ffmpeg":
            CONFIG.ffmpeg_path = str(Path(ffmpeg_dir) / "ffmpeg.exe")
        
        # 设置模型缓存目录
        hf_cache = self.get_hf_cache_dir()
        os.environ["HF_HOME"] = str(hf_cache)
        os.environ["TRANSFORMERS_CACHE"] = str(hf_cache)
        
        # 设置 Whisper 模型目录
        whisper_dir = self.get_models_dir() / "whisper"
        whisper_dir.mkdir(parents=True, exist_ok=True)
        os.environ["XDG_CACHE_HOME"] = str(self.get_models_dir())


# 全局便携式环境实例
PORTABLE_ENV = PortableEnv()
