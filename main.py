# main.py
import os
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes
)
from database import (
    save_user_interests,
    find_matching_users,
    openai_client
)
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /start 命令"""
    welcome_msg = (
        "🎮 欢迎来到游戏伙伴匹配机器人！\n\n"
        "请告诉我你喜欢的游戏或游戏类型，例如：\n"
        "· 我喜欢原神和王者荣耀\n"
        "· 我常玩生存恐怖类和开放世界游戏\n"
        "· 最近在玩艾尔登法环和星露谷物语"
    )
    await update.message.reply_text(welcome_msg)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理用户消息"""
    user = update.message.from_user
    user_id = str(user.id)
    username = user.username or user.first_name or "匿名玩家"
    user_input = update.message.text

    try:
        # 第一步：提取兴趣关键词
        raw_interests = await _extract_interests(user_input)
        if not raw_interests:
            await update.message.reply_text("⚠️ 没有识别到有效的游戏兴趣，请尝试更具体的描述（如游戏名称或类型）")
            return

        # 第二步：保存到数据库
        if not save_user_interests(user_id, username, raw_interests):
            await update.message.reply_text("❌ 保存兴趣失败，请稍后再试")
            return

        # 第三步：查找匹配
        await _process_matching(update, user_id, raw_interests)

    except Exception as e:
        print(f"处理消息时出错: {e}")
        await update.message.reply_text("🌀 服务暂时不可用，请稍后再试")

async def _extract_interests(text: str) -> list:
    """调用OpenAI提取兴趣关键词"""

    response = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": (
                    "你是一个游戏兴趣提取助手。请从用户消息中提取游戏或游戏类型关键词，"
                    "用中文逗号分隔。只返回关键词，不要解释。\n"
                    "示例输入：'我喜欢玩原神和王者荣耀'\n"
                    "示例输出：原神, 王者荣耀"
                )
            },
            {"role": "user", "content": text}
        ],
        temperature=0.3
    )
    
    raw = response.choices[0].message.content.strip()
    return [x.strip() for x in raw.split(",") if x.strip()]

async def _process_matching(update: Update, user_id: str, interests: list):
    """处理匹配流程"""
    # 精确匹配
    exact_matches = await find_matching_users(user_id, interests)
    if exact_matches:
        match_list = "\n".join(
            [f"· {user['username']} （共同兴趣：{', '.join(user['interests'])}）"
             for user in exact_matches[:3]]  # 显示前3个
        )
        await update.message.reply_text(
            f"🎉 找到{len(exact_matches)}位兴趣相同的玩家：\n{match_list}"
        )

    # 跨游戏匹配
    cross_matches = await find_matching_users(user_id, interests)
    if cross_matches:
        for match in cross_matches[:3]:  # 显示前3个
            common = match["common_games"]
            msg = (
                f"🌟 推荐玩家：{match['username']}\n"
                f"📈 匹配度：{match['score']*100:.0f}%\n"
                f"🎮 共同游戏：{', '.join(common) if common else '暂无'}\n"
                f"💡 推荐理由：{await _generate_match_reason(interests, match)}"
            )
            await update.message.reply_text(msg)
    elif not exact_matches:
        await update.message.reply_text("暂时没有找到匹配的玩家，我们会继续为您关注！")

async def _generate_match_reason(base_interests: list, match: dict) -> str:
    """生成匹配原因描述（修复数据结构问题）"""
    try:
        # 修复数据结构访问问题
        candidate_interests = match.get("interests", [])  # 直接访问interests字段
        
        # 验证输入有效性
        if not base_interests or not candidate_interests:
            return "基于双方游戏兴趣的相似性推荐"
        
        # 构造更明确的提示词
        system_prompt = f"""你是一个专业的游戏匹配分析师。请根据以下游戏兴趣列表，用1句话说明匹配原因：
        我的兴趣：{', '.join(base_interests[:5])}（最多展示5个）
        对方兴趣：{', '.join(candidate_interests[:5])}（最多展示5个）
        分析角度：游戏类型、玩法机制、用户画像、流行趋势等
        输出要求：用口语化中文，不超过20个字"""
        
        # 添加API调用保护
        response = openai_client.chat.completions.create(
            model="gpt-3.5-turbo",  # 确认可用模型
            messages=[{
                "role": "system",
                "content": system_prompt
            }],
            temperature=0.7,
            max_tokens=50,
            timeout=10  # 添加超时设置
        )
        
        # 处理空响应
        if not response.choices[0].message.content:
            raise ValueError("OpenAI返回空内容")
            
        return response.choices[0].message.content.strip()
        
    except KeyError as e:
        print(f"数据结构错误: {str(e)}")
        return "发现共同的游戏兴趣"
    except Exception as e:
        print(f"推荐理由生成失败: {str(e)}")
        return "这些游戏可能有相似的玩法特点"


def main():
    """启动机器人"""
    # 初始化应用
    app = ApplicationBuilder() \
        .token(os.getenv("TELEGRAM_TOKEN")) \
        .concurrent_updates(True) \
        .build()

    # 注册处理器
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # 启动轮询
    print("🤖 机器人已启动...")
    app.run_polling()

if __name__ == "__main__":
    main()