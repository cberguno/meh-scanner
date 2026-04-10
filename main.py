from config import Config
from sheets import GoogleSheets

def main():
    print("🚀 Meh-Scanner starting...")

    # Check config
    if not Config.SERPER_API_KEY or Config.SERPER_API_KEY == "your_serper_key_here":
        print("❌ Please add your Serper API key to .env")
        return

    if not Config.ANTHROPIC_API_KEY or Config.ANTHROPIC_API_KEY.startswith("your_"):
        print("❌ Please add your Anthropic API key to .env")
        return

    # Setup sheets
    sheets = GoogleSheets()
    sheets.setup()

    print("✅ All modules loaded successfully!")
    print("📊 Ready for Phase 3 (scraper + analyzer)")
    print("\nNext: reply 'foundation done' in chat")

if __name__ == "__main__":
    main()
