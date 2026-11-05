import logging
from telebot.apihelper import ApiTelegramException
from models import ShopError, STATUS_LABELS, money


class ShopService:
    def __init__(self, bot, db, admin_ids=(), shop_name='فروشگاه آریا'):
        self.bot, self.db = bot, db
        self.admin_ids = set(admin_ids)
        self.shop_name = shop_name

    def is_admin(self, user_id):
        return user_id in self.admin_ids

    def require_admin(self, user_id):
        if not self.is_admin(user_id):
            raise ShopError('این بخش فقط برای ادمین است.')

    def send(self, chat_id, text, markup=None):
        # Plain text avoids formatting injection; short chunks fit Telegram limits.
        chunks = [str(text)[i:i + 2000] for i in range(0, len(str(text)), 2000)] or ['—']
        for index, chunk in enumerate(chunks):
            self.bot.send_message(chat_id, chunk,
                reply_markup=markup if index == len(chunks) - 1 else None)

    def dispatch_notifications(self):
        sent, failed = 0, 0
        for item in self.db.pending_messages():
            user = self.db.get_user(item['recipient'])
            if item['kind'] in ('broadcast', 'restock') and user and user.blocked:
                self.db.mark_message(item['id'], 'skipped')
                continue
            try:
                self.bot.send_message(item['recipient'], item['text'])
                self.db.mark_message(item['id'], 'sent')
                sent += 1
            except ApiTelegramException as error:
                failed += 1
                if error.error_code == 429:
                    delay = error.result_json.get('parameters', {}).get('retry_after', 60)
                    self.db.mark_message(item['id'], 'pending', max(int(delay), 1))
                    break
                if error.error_code in (400, 403):
                    self.db.mark_message(item['id'], 'failed')
                else:
                    self.db.mark_message(item['id'], 'pending', 30)
                    break
            except Exception:
                # Keep delivery pending; never roll back a confirmed order for a network error.
                logging.warning('Notification delivery postponed; no credentials logged.')
                self.db.mark_message(item['id'], 'pending', 30)
                failed += 1
                break
        return sent, failed

    @staticmethod
    def quote_text(quote):
        lines = ['🛒 خلاصهٔ سبد خرید']
        for item in quote['items']:
            lines.append(f'{item["name"]} × {item["quantity"]} = {money(item["price"] * item["quantity"])}')
        lines += [f'جمع کالاها: {money(quote["subtotal"])}',
                  f'تخفیف: {money(quote["discount"])}', 'هزینهٔ ارسال: رایگان',
                  f'مبلغ نهایی: {money(quote["total"])}', 'روش پرداخت: هنگام تحویل']
        return '\n'.join(lines)

    def order_text(self, order_id, user_id=None):
        row, items = self.db.order(order_id, user_id)
        lines = [f'📦 سفارش #{row["id"]}', f'وضعیت: {STATUS_LABELS[row["status"]]}',
                 f'زمان ثبت: {row["created_at"][:16].replace("T", " ")} UTC']
        for item in items:
            lines.append(f'{item["name"]} × {item["quantity"]} = {money(item["unit_price"] * item["quantity"])}')
        lines += [f'جمع: {money(row["subtotal"])}', f'تخفیف: {money(row["discount"])}',
                  f'مبلغ نهایی: {money(row["total"])}', 'پرداخت هنگام تحویل؛ ارسال رایگان',
                  f'شمارهٔ تماس: {row["phone"]}', f'نشانی و نام گیرنده: {row["address"]}']
        return '\n'.join(lines)
