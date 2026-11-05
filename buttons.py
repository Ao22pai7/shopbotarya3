from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from telebot.types import ReplyKeyboardMarkup, KeyboardButton


class Buttons:
    PAGE_SIZE = 6

    @staticmethod
    def contact():
        markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        markup.add(KeyboardButton('📱 ارسال شمارهٔ خودم', request_contact=True))
        return markup

    @staticmethod
    def main(admin=False):
        markup = ReplyKeyboardMarkup(resize_keyboard=True)
        markup.row('محصولات', 'جستجوی محصول')
        markup.row('سبد خرید', 'سفارش‌های من')
        markup.row('کدهای تخفیف من', 'پشتیبانی')
        if admin:
            markup.row('پنل ادمین')
        return markup

    @staticmethod
    def inline(rows):
        markup = InlineKeyboardMarkup()
        for row in rows:
            markup.row(*[InlineKeyboardButton(text, callback_data=data) for text, data in row])
        return markup

    @staticmethod
    def page(items, prefix, page=0, extra=None):
        pages = max(1, (len(items) + Buttons.PAGE_SIZE - 1) // Buttons.PAGE_SIZE)
        page = min(max(page, 0), pages - 1)
        start = page * Buttons.PAGE_SIZE
        rows = [[(text[:60], data)] for text, data in items[start:start + Buttons.PAGE_SIZE]]
        navigation = []
        if page > 0:
            navigation.append(('قبلی', f'{prefix}:{page - 1}'))
        if page + 1 < pages:
            navigation.append(('بعدی', f'{prefix}:{page + 1}'))
        if navigation:
            rows.append(navigation)
        rows.extend(extra or [[('منوی اصلی', 'home')]])
        return Buttons.inline(rows)

    @staticmethod
    def product(product):
        rows = []
        if product.stock:
            rows.append([('افزودن یک عدد به سبد', f'add:{product.id}')])
        else:
            rows.append([('موجود شد خبرم کن', f'watch:{product.id}')])
        rows.append([('سبد خرید', 'cart'), ('دسته‌بندی‌ها', 'cats:0')])
        return Buttons.inline(rows)

    @staticmethod
    def admin():
        return Buttons.inline([
            [('افزودن محصول', 'admin:new'), ('مدیریت محصولات', 'admin:products:0')],
            [('مدیریت سفارش‌ها', 'admin:orders:0'), ('تیکت‌ها', 'admin:tickets:0')],
            [('مدیریت کاربران', 'admin:users:0'), ('ارسال اطلاعیه', 'admin:broadcast')],
            [('پیام‌های معوق / تلاش مجدد', 'admin:outbox')],
            [('منوی مشتری', 'home')]])
