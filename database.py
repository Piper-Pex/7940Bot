import os
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
from openai import OpenAI
from typing import List, Dict
import asyncio

# Load environment variables
load_dotenv()

# Initialize OpenAI client
client = OpenAI(
    api_key="sk-13DJKXp6QBphm8MaRbUwOiwRmx9E2qwW6lf9dMP30eEeqyXJ",
    base_url="https://api.deerapi.com/v1"
)

# Database connection pool
def _get_connection():
    """Get database connection (adapted for Heroku)"""
    try:
        return psycopg2.connect(
            dsn=os.getenv("DATABASE_URL"),
            cursor_factory=RealDictCursor,
            sslmode='require'  # Enforce SSL
        )
    except Exception as e:
        print(f"Connection failed: {e}")
        return None

def _create_tables():
    """Initialize database table structure"""
    conn = _get_connection()
    try:
        with conn.cursor() as cur:
            # Users table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS new_users (
                    user_id VARCHAR(255) PRIMARY KEY,
                    username VARCHAR(255),
                    interests TEXT[],
                    last_active TIMESTAMP DEFAULT NOW()
                )
            """)
            
            # Game similarity cache table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS game_similarities (
                    game1 VARCHAR(255),
                    game2 VARCHAR(255),
                    similarity FLOAT CHECK (similarity BETWEEN 0 AND 1),
                    PRIMARY KEY (game1, game2)
                 )
            """)
            
            # Create index
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_interests 
                ON new_users USING GIN (interests)
            """)
            conn.commit()
    except Exception as e:
        print(f"Table creation failed: {e}")
        conn.rollback()
    finally:
        conn.close()

# Create tables at initialization
_create_tables()

def save_user_interests(user_id, username, interests):
    """Save user interests (with last active time)"""
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
        print(f"Failed to save user interests: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()

def _get_cached_similarity(game1, game2):
    """Get cached similarity from the database"""
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

# Modify cache writing method (avoid closing connection while async task is incomplete)
async def _cache_similarity(game1, game2, similarity):
    """Async safe caching"""
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
        print(f"Cache writing failed: {e}")

async def analyze_game_pair(game1: str, game2: str) -> float:
    """Analyze game similarity (with caching mechanism)"""
    # Prioritize reading from cache
    cached = _get_cached_similarity(game1, game2)
    if cached is not None:
        return cached

    # Call OpenAI API
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{
                "role": "system",
                "content": "You are a game analysis expert. Please evaluate the similarity (0-1) of the following two games, considering type, gameplay, art style, etc., and return the number directly."
            }, {
                "role": "user",
                "content": f"The similarity score between '{game1}' and '{game2}' is:"
            }],
            temperature=0.2
        )
        similarity = max(0.0, min(1.0, float(response.choices[0].message.content.strip())))
        
        # Asynchronously cache the result
        asyncio.create_task(_cache_similarity(game1, game2, similarity))
        return similarity
    except Exception as e:
        print(f"Game similarity analysis failed: {e}")
        return 0.0

async def find_matching_users(user_id: str, interests: List[str], threshold: float = 0.6) -> List[Dict]:
    """Find cross-game matching users (fix parameter passing issue)"""
    conn = _get_connection()
    try:
        # Get candidate users (fix field name matching issue)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT user_id, username, interests 
                FROM new_users 
                WHERE user_id != %s 
                AND last_active > NOW() - INTERVAL '7 days'
            """, (str(user_id),))
            candidates = [dict(row) for row in cur.fetchall()]  # Convert to dictionary

        # Fix parameter passing (remove redundant parameters)
        tasks = [
            _calculate_user_similarity(
                base_interests=interests,  # Use correct parameter name
                candidate_data=candidate   # Pass only necessary parameters
            )
            for candidate in candidates
        ]
        
        results = await asyncio.gather(*tasks)
        
        # Filter and sort results (add type checking)
        valid_results = [
            res for res in results 
            if isinstance(res, dict) and res.get("score", 0) >= threshold
        ]
        return sorted(valid_results, key=lambda x: x["score"], reverse=True)[:10]
    finally:
        conn.close()


async def _calculate_user_similarity(base_interests: List[str], candidate_data: dict) -> dict:
    """Calculate user similarity score (safe field handling)"""
    # Safely get interest data
    raw_interests = candidate_data.get("interests", [])
    
    # Handle PostgreSQL array format
    if isinstance(raw_interests, str):
        candidate_interests = [i.strip() for i in raw_interests.strip('{}').split(',')]
    elif isinstance(raw_interests, list):
        candidate_interests = raw_interests
    else:
        candidate_interests = []

    # Exact match calculation
    common = set(base_interests) & set(candidate_interests)
    total = len(common) * 1.0
    valid_pairs = len(common)
    
    # Cross-game matching (add null value protection)
    try:
        base_remain = [g for g in base_interests if g not in common]
        candidate_remain = [g for g in candidate_interests if g not in common]
        
        for g1 in base_remain:
            for g2 in candidate_remain:
                similarity = await analyze_game_pair(g1, g2)
                if similarity and similarity >= 0.4:
                    total += similarity
                    valid_pairs += 1
    except Exception as e:
        print(f"Matching calculation exception: {str(e)}")

    # Safely calculate score
    score = total / valid_pairs if valid_pairs > 0 else 0.0
    return {
        "user_id": candidate_data.get("user_id", ""),
        "username": candidate_data.get("username", "Unknown User"),
        "score": round(score, 2),
        "common_games": list(common),
        "interests": candidate_interests  # Return processed interest list
    }

openai_client = client
