"""Railway entry point for the storefront and notification modes."""
import os

if __name__ == "__main__":
    if os.getenv("STOREFRONT_MODE") == "notifications":
        from notification_bot import main
        main()
    else:
        import bot
        bot.main()
