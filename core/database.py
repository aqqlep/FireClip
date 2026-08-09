"""
数据库管理器
负责项目数据的持久化存储
"""
import sqlite3
import json
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime
from utils.logger import logger


class DatabaseManager:
    """数据库管理器"""
    
    def __init__(self, db_path: str = "fireclip.db"):
        """
        初始化数据库管理器
        
        Args:
            db_path: 数据库文件路径
        """
        self.db_path = db_path
        self.init_database()
        logger.info(f"数据库管理器初始化完成: {db_path}")
    
    def _get_connection(self):
        """获取数据库连接（启用外键）"""
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn
    
    def init_database(self):
        """初始化数据库表"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # 创建项目表
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS projects (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        source_path TEXT NOT NULL,
                        video_info TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                """)
                
                # 创建场景表
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS scenes (
                        id TEXT PRIMARY KEY,
                        project_id TEXT NOT NULL,
                        start_time REAL NOT NULL,
                        end_time REAL NOT NULL,
                        duration REAL NOT NULL,
                        scene_type TEXT NOT NULL,
                        confidence REAL NOT NULL,
                        channel_scores TEXT,
                        thumbnail_path TEXT,
                        description TEXT,
                        tags TEXT,
                        action_type TEXT,
                        video_type TEXT,
                        audio_energy REAL,
                        motion_score REAL,
                        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
                    )
                """)
                
                # 创建解说文案表
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS commentaries (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        project_id TEXT NOT NULL,
                        text TEXT NOT NULL,
                        start_time REAL NOT NULL,
                        end_time REAL NOT NULL,
                        scene_id TEXT,
                        tts_audio_path TEXT,
                        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
                        FOREIGN KEY (scene_id) REFERENCES scenes(id) ON DELETE SET NULL
                    )
                """)
                
                # 创建索引
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_scenes_project_id ON scenes(project_id)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_scenes_scene_type ON scenes(scene_type)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_scenes_confidence ON scenes(confidence DESC)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_commentaries_project_id ON commentaries(project_id)")
                
                conn.commit()
            
            logger.info("数据库表初始化完成")
        
        except Exception as e:
            logger.error(f"数据库初始化失败: {e}")
            raise
    
    def create_project(self, project_id: str, name: str, source_path: str,
                      video_info: Dict) -> bool:
        """
        创建新项目
        
        Args:
            project_id: 项目ID
            name: 项目名称
            source_path: 源视频路径
            video_info: 视频信息
        
        Returns:
            是否成功
        """
        try:
            now = datetime.now().isoformat()
            with self._get_connection() as conn:
                conn.execute("""
                    INSERT INTO projects (id, name, source_path, video_info, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (project_id, name, source_path, json.dumps(video_info), now, now))
                conn.commit()
            
            logger.info(f"项目创建成功: {project_id}")
            return True
        
        except Exception as e:
            logger.error(f"项目创建失败: {e}")
            return False
    
    def get_project(self, project_id: str) -> Optional[Dict]:
        """
        获取项目信息
        
        Args:
            project_id: 项目ID
        
        Returns:
            项目信息字典
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM projects WHERE id = ?", (project_id,))
                row = cursor.fetchone()
                
                if not row:
                    return None
                
                return {
                    "id": row[0],
                    "name": row[1],
                    "source_path": row[2],
                    "video_info": json.loads(row[3]) if row[3] else {},
                    "created_at": row[4],
                    "updated_at": row[5]
                }
        
        except Exception as e:
            logger.error(f"获取项目失败: {e}")
            return None
    
    def list_projects(self) -> List[Dict]:
        """
        列出所有项目
        
        Returns:
            项目列表
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM projects ORDER BY updated_at DESC")
                rows = cursor.fetchall()
                
                return [{
                    "id": row[0],
                    "name": row[1],
                    "source_path": row[2],
                    "video_info": json.loads(row[3]) if row[3] else {},
                    "created_at": row[4],
                    "updated_at": row[5]
                } for row in rows]
        
        except Exception as e:
            logger.error(f"列出项目失败: {e}")
            return []
    
    def delete_project(self, project_id: str) -> bool:
        """
        删除项目
        
        Args:
            project_id: 项目ID
        
        Returns:
            是否成功
        """
        try:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
                conn.commit()
            
            logger.info(f"项目删除成功: {project_id}")
            return True
        
        except Exception as e:
            logger.error(f"项目删除失败: {e}")
            return False
    
    def _row_to_scene(self, row, include_source=False) -> Dict:
        """将数据库行转为场景字典（减少重复代码）"""
        scene = {
            "id": row[0],
            "project_id": row[1],
            "start_time": row[2],
            "end_time": row[3],
            "duration": row[4],
            "scene_type": row[5],
            "confidence": row[6],
            "channel_scores": json.loads(row[7]) if row[7] else {},
            "thumbnail_path": row[8],
            "description": row[9],
            "tags": json.loads(row[10]) if row[10] else [],
            "action_type": row[11],
            "video_type": row[12],
            "audio_energy": row[13],
            "motion_score": row[14],
        }
        if include_source and len(row) > 15:
            scene["source_path"] = row[15]
        return scene
    
    def save_scenes(self, project_id: str, scenes: List[Dict]) -> bool:
        """
        保存场景数据
        
        Args:
            project_id: 项目ID
            scenes: 场景列表
        
        Returns:
            是否成功
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # 先删除旧的场景数据
                cursor.execute("DELETE FROM scenes WHERE project_id = ?", (project_id,))
                
                # 批量插入新的场景数据
                if scenes:
                    scene_rows = [
                        (
                            scene.get("id", ""),
                            project_id,
                            scene.get("start_time", 0.0),
                            scene.get("end_time", 0.0),
                            scene.get("duration", 0.0),
                            scene.get("scene_type", ""),
                            scene.get("confidence", 0.0),
                            json.dumps(scene.get("channel_scores", {})),
                            scene.get("thumbnail_path", ""),
                            scene.get("description", ""),
                            json.dumps(scene.get("tags", [])),
                            scene.get("action_type", ""),
                            scene.get("video_type", ""),
                            scene.get("audio_energy", 0.0),
                            scene.get("motion_score", 0.0)
                        )
                        for scene in scenes
                    ]
                    cursor.executemany("""
                        INSERT INTO scenes (
                            id, project_id, start_time, end_time, duration, scene_type,
                            confidence, channel_scores, thumbnail_path, description,
                            tags, action_type, video_type, audio_energy, motion_score
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, scene_rows)
                
                # 更新项目的更新时间
                cursor.execute("""
                    UPDATE projects SET updated_at = ? WHERE id = ?
                """, (datetime.now().isoformat(), project_id))
                
                conn.commit()
            
            logger.info(f"场景保存成功: {len(scenes)}个")
            return True
        
        except Exception as e:
            logger.error(f"场景保存失败: {e}")
            return False
    
    def get_scenes(self, project_id: str) -> List[Dict]:
        """
        获取项目的场景数据
        
        Args:
            project_id: 项目ID
        
        Returns:
            场景列表
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM scenes WHERE project_id = ? ORDER BY confidence DESC
                """, (project_id,))
                rows = cursor.fetchall()
                
                return [self._row_to_scene(row) for row in rows]
        
        except Exception as e:
            logger.error(f"获取场景失败: {e}")
            return []
    
    def get_all_scenes(self) -> List[Dict]:
        """
        获取所有项目的所有场景（用于批量导出）
        
        Returns:
            所有场景列表，按 project_id + start_time 排序
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT s.*, p.source_path
                    FROM scenes s
                    LEFT JOIN projects p ON s.project_id = p.id
                    ORDER BY p.source_path, s.start_time
                """)
                rows = cursor.fetchall()
                
                scenes = [self._row_to_scene(row, include_source=True) for row in rows]
            
            logger.info(f"获取全部场景: {len(scenes)}个")
            return scenes
        
        except Exception as e:
            logger.error(f"获取全部场景失败: {e}")
            return []
    
    def save_commentary(self, project_id: str, segments: List[Dict]) -> bool:
        """
        保存解说文案
        
        Args:
            project_id: 项目ID
            segments: 解说片段列表
        
        Returns:
            是否成功
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # 先删除旧的解说数据
                cursor.execute("DELETE FROM commentaries WHERE project_id = ?", (project_id,))
                
                # 批量插入新的解说数据
                if segments:
                    seg_rows = [
                        (
                            project_id,
                            seg.get("text", ""),
                            seg.get("start_time", 0.0),
                            seg.get("end_time", 0.0),
                            seg.get("scene_id", ""),
                            seg.get("tts_audio_path", "")
                        )
                        for seg in segments
                    ]
                    cursor.executemany("""
                        INSERT INTO commentaries (project_id, text, start_time, end_time, scene_id, tts_audio_path)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, seg_rows)
                
                conn.commit()
            
            logger.info(f"解说文案保存成功: {len(segments)}段")
            return True
        
        except Exception as e:
            logger.error(f"解说文案保存失败: {e}")
            return False
    
    def save_commentary_text(self, project_id: str, text: str) -> bool:
        """
        保存纯文本解说文案（单段）
        
        Args:
            project_id: 项目ID
            text: 解说文本
        
        Returns:
            是否成功
        """
        return self.save_commentary(project_id, [{"text": text, "start_time": 0.0, "end_time": 0.0}])
    
    def get_commentary(self, project_id: str) -> List[Dict]:
        """
        获取项目的解说文案
        
        Args:
            project_id: 项目ID
        
        Returns:
            解说片段列表
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM commentaries WHERE project_id = ? ORDER BY start_time
                """, (project_id,))
                rows = cursor.fetchall()
                
                return [{
                    "id": row[0],
                    "project_id": row[1],
                    "text": row[2],
                    "start_time": row[3],
                    "end_time": row[4],
                    "scene_id": row[5],
                    "tts_audio_path": row[6]
                } for row in rows]
        
        except Exception as e:
            logger.error(f"获取解说文案失败: {e}")
            return []


# 测试代码
if __name__ == "__main__":
    import sys
    
    db = DatabaseManager("test.db")
    
    # 创建测试项目
    project_id = "test_project_001"
    db.create_project(
        project_id=project_id,
        name="测试项目",
        source_path="/path/to/video.mp4",
        video_info={"duration": 120.0, "width": 1920, "height": 1080}
    )
    
    # 保存测试场景
    test_scenes = [
        {
            "id": "scene_001",
            "start_time": 10.0,
            "end_time": 20.0,
            "duration": 10.0,
            "scene_type": "action",
            "confidence": 0.85,
            "channel_scores": {"motion": 0.8, "audio_energy": 0.9},
            "description": "测试场景1",
            "tags": ["#打戏", "#动作"]
        }
    ]
    db.save_scenes(project_id, test_scenes)
    
    # 获取项目
    project = db.get_project(project_id)
    print("项目信息:", project)
    
    # 获取场景
    scenes = db.get_scenes(project_id)
    print("场景列表:", scenes)
    
    # 列出所有项目
    projects = db.list_projects()
    print("所有项目:", projects)
    
    # 删除项目
    db.delete_project(project_id)
    print("项目已删除")
