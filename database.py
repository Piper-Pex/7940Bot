# database.py 修改版
import os
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
from openai import OpenAI
from typing import List, Dict
import asyncio

# 加载环境变量
load_dotenv()

# 初始化OpenAI客户端
client = OpenAI(
    api_key="sk-13DJKXp6QBphm8MaRbUwOiwRmx9E2qwW6lf9dMP30eEeqyXJ",
    base_url="https://api.deerapi.com/v1"
)

# 数据库连接池
def _get_connection():
    """获取数据库连接（适配Heroku）"""
    try:
        return psycopg2.connect(
            dsn=os.getenv("DATABASE_URL"),
            cursor_factory=RealDictCursor,
            sslmode='require'  # 强制SSL
        )
    except Exception as e:
        print(f"连接失败: {e}")
        return None

def _create_tables():
    """初始化数据库表结构"""
    conn = _get_connection()
    try:
        with conn.cursor() as cur:
            # 用户表
            cur.execute("""
                CREATE TABLE IF NOT EXISTS new_users (
                    user_id VARCHAR(255) PRIMARY KEY,
                    username VARCHAR(255),
                    interests TEXT[],
                    last_active TIMESTAMP DEFAULT NOW()
                )
            """)
            
            # 游戏相似度缓存表
            cur.execute("""
                CREATE TABLE IF NOT EXISTS game_similarities (
                    game1 VARCHAR(255),
                    game2 VARCHAR(255),
                    similarity FLOAT CHECK (similarity BETWEEN 0 AND 1),
                    PRIMARY KEY (game1, game2)
                 )
            """)
            
            # 创建索引
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_interests 
                ON new_users USING GIN (interests)
            """)
            conn.commit()
    except Exception as e:
        print(f"表创建失败: {e}")
        conn.rollback()
    finally:
        conn.close()

# 初始化时创建表
_create_tables()

def save_user_interests(user_id, username, interests):
    """保存用户兴趣（带最后活跃时间）"""
    conn = _get_connection()
    if not conn:
        return False

    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO new_users (user_id, username, interests, last_active)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (user_id) DO UPDATE SET
                    username = EXCLUDED.username,
                    interests = EXCLUDED.interests,
                    last_active = NOW()
            """, (str(user_id), username, interests))
            conn.commit()
            return True
    except Exception as e:
        print(f"保存用户兴趣失败: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()

def _get_cached_similarity(game1, game2):
    """从数据库获取缓存的相似度"""
    conn = _get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT similarity 
                FROM game_similarities
                WHERE (game1 = %s AND game2 = %s)
                OR (game1 = %s AND game2 = %s)
            """, (game1, game2, game2, game1))
            result = cur.fetchone()
            return result['similarity'] if result else None
    finally:
        conn.close()

# 修改缓存写入方式（避免异步任务未完成时连接关闭）
async def _cache_similarity(game1, game2, similarity):
    """异步安全版缓存"""
    try:
        with psycopg2.connect(os.getenv("DATABASE_URL"), sslmode='require') as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO game_similarities 
                    VALUES (%s, %s, %s)
                    ON CONFLICT DO NOTHING
                """, (game1, game2, similarity))
                conn.commit()
    except Exception as e:
        print(f"缓存写入失败: {e}")

async def analyze_game_pair(game1: str, game2: str) -> float:
    """分析游戏相似度（带缓存机制）"""
    # 优先读取缓存
    cached = _get_cached_similarity(game1, game2)
    if cached is not None:
        return cached

    # 调用OpenAI API
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{
                "role": "system",
                "content": "你是一个游戏分析专家，请评估以下两个游戏的相似度（0-1），考虑类型、玩法、画风等因素，直接返回数字"
            }, {
                "role": "user",
                "content": f"《{game1}》和《{game2}》的相似度分数是："
            }],
            temperature=0.2
        )
        similarity = max(0.0, min(1.0, float(response.choices[0].message.content.strip())))
        
        # 异步缓存结果
        asyncio.create_task(_cache_similarity(game1, game2, similarity))
        return similarity
    except Exception as e:
        print(f"游戏相似度分析失败: {e}")
        return 0.0

async def find_matching_users(user_id: str, interests: List[str], threshold: float = 0.6) -> List[Dict]:
    """查找跨游戏匹配用户"""
    conn = _get_connection()
    try:
        # 获取候选用户
        with conn.cursor() as cur:
            cur.execute("""
                SELECT user_id, username, interests 
                FROM new_users 
                WHERE user_id != %s 
                AND last_active > NOW() - INTERVAL '7 days'
            """, (str(user_id),))
            candidates = cur.fetchall()

        # 并行计算相似度
        tasks = []
        for candidate in candidates:
            tasks.append(_calculate_user_similarity(interests, candidate["interests"], candidate))
        
        results = await asyncio.gather(*tasks)
        
        # 筛选和排序结果
        matches = [res for res in results if res["score"] >= threshold]
        return sorted(matches, key=lambda x: x["score"], reverse=True)[:10]
    finally:
        conn.close()

async def _calculate_user_similarity(base_interests, candidate_interests, candidate_data):
    """计算用户相似度得分"""
    total = 0.0
    valid_pairs = 0
    
    # 精确匹配直接加分
    common = set(base_interests) & set(candidate_interests)
    total += len(common) * 1.0
    valid_pairs += len(common)
    
    # 跨游戏匹配
    base_remain = [g for g in base_interests if g not in common]
    candidate_remain = [g for g in candidate_interests if g not in common]
    
    for g1 in base_remain:
        for g2 in candidate_remain:
            similarity = await analyze_game_pair(g1, g2)
            if similarity >= 0.4:
                total += similarity
                valid_pairs += 1
    
    score = total / valid_pairs if valid_pairs > 0 else 0.0
    return {
        "user_id": candidate_data["user_id"],
        "username": candidate_data["username"],
        "score": round(score, 2),
        "common_games": list(common)
    }

openai_client = client 