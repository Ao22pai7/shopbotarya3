import logging
import os
import telebot
from dotenv import load_dotenv
from database import Database
from service import ShopService
from handler import TelegramBot


def main():
    folder = os.path.dirname(os.path.abspath(__file__))
    load_dotenv(os.path.join(folder, '.env'))
    token = os.environ.get('TOKEN', '').strip()
    if not token:
        print('TOKEN is missing. Copy .env.example to .env and add your BotFather token.')
        return
    raw_admins = os.environ.get('ADMIN_IDS', '').strip()
    try:
        admin_ids = {int(value.strip()) for value in raw_admins.split(',') if value.strip()}
        if any(value <= 0 for value in admin_ids):
            raise ValueError
    except ValueError:
        print('ADMIN_IDS must contain numeric Telegram user IDs separated by commas.')
        return
    filename = os.environ.get('DATABASE_PATH', 'shop.db').strip() or 'shop.db'
    if not os.path.isabs(filename):
        filename = os.path.join(folder, filename)
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    db = Database(filename)
    # Exactly one synchronous polling process; the SQLite connection stays in one thread.
    try:
        bot = telebot.TeleBot(token, threaded=False, parse_mode=None)
        service = ShopService(bot, db, admin_ids, os.environ.get('SHOP_NAME', 'فروشگاه آریا'))
        TelegramBot(service)
        me = bot.get_me()
        print(f'Bot is running: @{me.username}')
        if not admin_ids:
            print('Send /id to your bot, add the returned number to ADMIN_IDS, then restart.')
        service.dispatch_notifications()
        bot.infinity_polling(skip_pending=False, timeout=20, long_polling_timeout=20,
                             allowed_updates=['message', 'callback_query'])
    except KeyboardInterrupt:
        print('Bot stopped.')
    except Exception as error:
        # Never print the token, request URL, phone numbers or message bodies.
        print(f'Could not run bot ({type(error).__name__}). Check TOKEN, network and duplicate polling instances.')
    finally:
        db.close()


if __name__ == '__main__':
    logging.basicConfig(level=logging.WARNING, format='%(levelname)s: %(message)s')
    # Library error strings may include Telegram URLs; application errors are sanitized above.
    telebot.logger.setLevel(logging.CRITICAL)
    main()
