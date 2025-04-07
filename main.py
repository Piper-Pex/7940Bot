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

# Load environment variables
load_dotenv()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    welcome_msg = (
        "🎮 Welcome to the Game Partner Matching Bot!\n\n"
        "Please tell me the games or game genres you like, for example:\n"
        "· I like Genshin Impact and Honor of Kings\n"
        "· I often play survival horror and open-world games\n"
        "· Recently playing Elden Ring and Stardew Valley"
    )
    await update.message.reply_text(welcome_msg)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle user messages"""
    user = update.message.from_user
    user_id = str(user.id)
    username = user.username or user.first_name or "Anonymous Player"
    user_input = update.message.text

    try:
        # Step 1: Extract interest keywords
        raw_interests = await _extract_interests(user_input)
        if not raw_interests:
            await update.message.reply_text("⚠️ No valid game interests recognized, please try a more specific description (e.g., game name or genre)")
            return

        # Step 2: Save to database
        if not save_user_interests(user_id, username, raw_interests):
            await update.message.reply_text("❌ Failed to save interests, please try again later")
            return

        # Step 3: Find matches
        await _process_matching(update, user_id, raw_interests)

    except Exception as e:
        print(f"Error processing message: {e}")
        await update.message.reply_text("🌀 Service temporarily unavailable, please try again later")

async def _extract_interests(text: str) -> list:
    """Use OpenAI to extract interest keywords"""

    response = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a game interest extraction assistant. Please extract the game or game genre keywords from the user message, "
                    "separated by commas. Return only keywords, no explanations.\n"
                    "Example input: 'I like playing Genshin Impact and Honor of Kings'\n"
                    "Example output: Genshin Impact, Honor of Kings"
                )
            },
            {"role": "user", "content": text}
        ],
        temperature=0.3
    )
    
    raw = response.choices[0].message.content.strip()
    return [x.strip() for x in raw.split(",") if x.strip()]

async def _process_matching(update: Update, user_id: str, interests: list):
    """Handle the matching process"""
    # Exact matching
    exact_matches = await find_matching_users(user_id, interests)
    if exact_matches:
        match_list = "\n".join(
            [f"· {user['username']} (Common interests: {', '.join(user['interests'])})"
             for user in exact_matches[:3]]  # Show the top 3
        )
        await update.message.reply_text(
            f"🎉 Found {len(exact_matches)} players with similar interests:\n{match_list}"
        )

    # Cross-game matching
    cross_matches = await find_matching_users(user_id, interests)
    if cross_matches:
        for match in cross_matches[:3]:  # Show the top 3
            common = match["common_games"]
            msg = (
                f"🌟 Recommended player: {match['username']}\n"
                f"📈 Match score: {match['score']*100:.0f}%\n"
                f"🎮 Common games: {', '.join(common) if common else 'None'}\n"
                f"💡 Recommendation reason: {await _generate_match_reason(interests, match)}"
            )
            await update.message.reply_text(msg)
    elif not exact_matches:
        await update.message.reply_text("No matching players found for now, we will keep looking for you!")

async def _generate_match_reason(base_interests: list, match: dict) -> str:
    """Generate match reason description (fix data structure issue)"""
    try:
        # Fix data structure access issue
        candidate_interests = match.get("interests", [])  # Directly access interests field
        
        # Validate input validity
        if not base_interests or not candidate_interests:
            return "Recommended based on similar game interests"
        
        # Construct a more specific prompt
        system_prompt = f"""You are a professional game matching analyst. Please explain the match reason in one sentence based on the following game interest lists:
        My interests: {', '.join(base_interests[:5])} (show up to 5)
        Their interests: {', '.join(candidate_interests[:5])} (show up to 5)
        Analysis angles: game genre, gameplay mechanics, user profile, trends, etc.
        Output requirement: Use colloquial English, no more than 20 words"""
        
        # Add API call protection
        response = openai_client.chat.completions.create(
            model="gpt-3.5-turbo",  # Ensure available model
            messages=[{
                "role": "system",
                "content": system_prompt
            }],
            temperature=0.7,
            max_tokens=50,
            timeout=10  # Add timeout setting
        )
        
        # Handle empty response
        if not response.choices[0].message.content:
            raise ValueError("OpenAI returned empty content")
            
        return response.choices[0].message.content.strip()
        
    except KeyError as e:
        print(f"Data structure error: {str(e)}")
        return "Found common game interests"
    except Exception as e:
        print(f"Failed to generate recommendation reason: {str(e)}")
        return "These games may have similar gameplay features"


def main():
    """Start the bot"""
    # Initialize the application
    app = ApplicationBuilder() \
        .token(os.getenv("TELEGRAM_TOKEN")) \
        .concurrent_updates(True) \
        .build()

    # Register handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Start polling
    print("🤖 Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
