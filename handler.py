import logging
import secrets
from buttons import Buttons
from admin import AdminTelegramBot
from models import ShopError, STATUS_LABELS, clean_text, phone_number, money


class TelegramBot:
    def __init__(self, service):
        self.service = service
        self.bot, self.db = service.bot, service.db
        self.admin = AdminTelegramBot(service)
        self.backend_bot()

    def main_menu(self, user_id):
        self.db.clear_state(user_id)
        user = self.db.get_user(user_id)
        if not user or not user.can_shop():
            self.service.send(user_id, f'سلام! به {self.service.shop_name} خوش آمدی.\n'
                'برای ثبت‌نام یا ورود، شمارهٔ خودت را با دکمهٔ زیر بفرست.', Buttons.contact())
        else:
            self.service.send(user_id, f'سلام {user.name}! به {self.service.shop_name} خوش آمدی.\n'
                'از منو انتخاب کن. خروج از حساب: /logout', Buttons.main(self.service.is_admin(user_id)))

    def safe_action(self, chat_id, action):
        try:
            action()
        except ShopError as error:
            try:
                self.service.send(chat_id, str(error))
            except Exception:
                logging.warning('Could not deliver user-facing error.')
        except Exception as error:
            logging.error('Handler failed (%s); credentials and message text omitted.', type(error).__name__)
            try:
                self.service.send(chat_id, 'درخواست کامل نشد. /start را بفرست و دوباره امتحان کن.\n'
                    'اگر در حال خرید بودی، پیش از خرید مجدد «سفارش‌های من» را بررسی کن.')
            except Exception:
                pass
        finally:
            try:
                self.service.dispatch_notifications()
            except Exception:
                logging.warning('Pending notifications will be checked again later.')

    def backend_bot(self):
        @self.bot.message_handler(content_types=['text', 'contact', 'photo', 'document', 'voice', 'audio', 'video', 'sticker', 'location'])
        def message_handler(message):
            if message.chat.type != 'private':
                return
            self.safe_action(message.chat.id, lambda: self.message(message))

        @self.bot.callback_query_handler(func=lambda call: True)
        def callback_handler(call):
            try:
                self.bot.answer_callback_query(call.id)
            except Exception:
                logging.info('Callback acknowledgement unavailable; continuing safely.')
            if not call.message or call.message.chat.type != 'private' or call.message.chat.id != call.from_user.id:
                return
            self.safe_action(call.message.chat.id, lambda: self.callback(call))

    def identify(self, sender):
        user = self.db.ensure_user(sender.id, sender.first_name or 'کاربر', sender.username)
        if user.blocked:
            raise ShopError('دسترسی حساب شما توسط مدیریت محدود شده است.')
        return user

    def message(self, message):
        user = self.identify(message.from_user)
        user_id = user.id
        text = (message.text or '').strip()
        command = text.split()[0].split('@')[0] if text else ''
        if command == '/id':
            self.service.send(user_id, f'شناسهٔ عددی شما: {user_id}')
            return
        if command == '/admin' or text == 'پنل ادمین':
            self.admin.menu(user_id)
            return
        if command in ('/start', '/help', '/cancel'):
            self.main_menu(user_id)
            return
        if command == '/logout':
            self.db.logout(user_id)
            self.service.send(user_id, 'از حساب خارج شدی. برای ورود دوباره شماره‌ات را تأیید کن.', Buttons.contact())
            return
        if message.contact:
            if message.contact.user_id != user_id:
                raise ShopError('فقط شمارهٔ حساب خودت پذیرفته می‌شود؛ دکمهٔ «ارسال شمارهٔ خودم» را بزن.')
            self.db.register(user_id, phone_number(message.contact.phone_number))
            self.main_menu(user_id)
            return
        state = self.db.get_state(user_id)
        if state and state[0].startswith('admin_'):
            if not text:
                raise ShopError('لطفاً پاسخ را به‌صورت متن بفرست. لغو: /cancel')
            self.admin.input(user_id, text, state)
            return
        if not user.can_shop():
            self.main_menu(user_id)
            return
        if not text:
            raise ShopError('در این نسخه نام کالا یا متن پیام را بنویس. لغو: /cancel')
        if state and state[0] not in ('search_results',):
            self.customer_input(user_id, text, state)
            return
        if text.lower().strip(' !؟.') in ('سلام', 'درود', 'hello', 'hi'):
            self.main_menu(user_id)
        elif text == 'محصولات':
            self.show_categories(user_id)
        elif text == 'جستجوی محصول':
            self.db.set_state(user_id, 'search')
            self.service.send(user_id, 'نام یا بخشی از نام محصول را بفرست.\nلغو: /cancel')
        elif text == 'سبد خرید':
            self.show_cart(user_id)
        elif text == 'سفارش‌های من':
            self.show_orders(user_id)
        elif text == 'کدهای تخفیف من':
            self.show_coupons(user_id)
        elif text == 'پشتیبانی':
            self.support(user_id)
        elif command.startswith('/product_'):
            try:
                product_id = int(command[len('/product_'):])
            except ValueError:
                raise ShopError('شناسهٔ محصول معتبر نیست.') from None
            self.show_product(user_id, product_id)
        elif command.startswith('/'):
            self.service.send(user_id, 'دستور شناخته نشد. /start یا /help را بفرست.')
        else:
            self.search(user_id, clean_text(text, 'جست‌وجو', 1, 80))

    def customer_input(self, user_id, text, state):
        name, payload = state
        if name == 'search':
            self.search(user_id, clean_text(text, 'نام', 1, 80))
        elif name == 'ticket':
            question = clean_text(text, 'تیکت', 1, 1500)
            ticket_id = self.db.add_ticket(user_id, question, self.service.admin_ids)
            self.db.clear_state(user_id)
            self.service.send(user_id, f'تیکت #{ticket_id} ثبت شد. پاسخ مدیریت در همین ربات ارسال می‌شود.',
                Buttons.main(self.service.is_admin(user_id)))
        elif name == 'checkout_coupon':
            code = '' if text in ('-', 'بدون کد', 'ندارم') else clean_text(text, 'کد', 1, 30).upper()
            self.db.quote(user_id, code)
            self.db.set_state(user_id, 'checkout_address', {'code': code})
            self.service.send(user_id, 'نام گیرنده و نشانی کامل دریافت را در یک پیام بفرست (۱۰ تا ۶۰۰ نویسه).\n'
                'روش پرداخت: هنگام تحویل؛ هزینهٔ ارسال: رایگان.\nلغو: /cancel')
        elif name == 'checkout_address':
            address = clean_text(text, 'نام و نشانی', 10, 600)
            quote = self.db.quote(user_id, payload['code'])
            key = secrets.token_hex(12)
            self.db.set_state(user_id, 'checkout_confirm', {'quote': quote, 'address': address, 'key': key})
            self.service.send(user_id, self.service.quote_text(quote) + '\n\nنام و نشانی گیرنده:\n' + address,
                Buttons.inline([[('تأیید و ثبت سفارش', f'buy:{key}')], [('انصراف / بازگشت به سبد', 'cart:0')]]))
        elif name == 'checkout_confirm':
            self.service.send(user_id, 'برای ثبت سفارش دکمهٔ تأیید را بزن؛ برای لغو /cancel بفرست.')
        else:
            self.db.clear_state(user_id)
            self.main_menu(user_id)

    def callback(self, call):
        user = self.identify(call.from_user)
        user_id = user.id
        data = call.data or ''
        if data.startswith('admin:'):
            self.admin.callback(user_id, data)
            return
        self.db.require_customer(user_id)
        parts = data.split(':')
        action = parts[0]
        if action not in ('buy', 'searchpage'):
            self.db.clear_state(user_id)
        if action == 'home':
            self.main_menu(user_id)
        elif action == 'cats':
            self.show_categories(user_id, int(parts[1]))
        elif action == 'cat':
            self.show_products(user_id, int(parts[1]), int(parts[2]))
        elif action == 'p':
            self.show_product(user_id, int(parts[1]))
        elif action == 'watch':
            self.db.watch(user_id, int(parts[1]))
            self.service.send(user_id, 'ثبت شد؛ وقتی موجودی این محصول از صفر بیشتر شد، خبرت می‌کنیم.')
        elif action in ('add', 'inc', 'dec'):
            self.db.change_cart(user_id, int(parts[1]), -1 if action == 'dec' else 1)
            if action == 'add':
                self.service.send(user_id, 'یک عدد به سبد اضافه شد.', Buttons.inline([[('مشاهدهٔ سبد', 'cart:0'), ('ادامهٔ خرید', 'cats:0')]]))
            else:
                self.show_cart(user_id)
        elif action == 'remove':
            self.db.remove_cart(user_id, int(parts[1]))
            self.show_cart(user_id)
        elif action == 'clearcart':
            self.db.remove_cart(user_id)
            self.show_cart(user_id)
        elif action == 'cart':
            self.show_cart(user_id, int(parts[1]) if len(parts) > 1 else 0)
        elif action == 'ci':
            product_id = int(parts[1])
            items = [item for item in self.db.cart(user_id) if item['id'] == product_id]
            if not items:
                raise ShopError('این محصول در سبد شما نیست.')
            item = items[0]
            self.service.send(user_id, f'{item["name"]}\nتعداد در سبد: {item["quantity"]}\nموجودی فعلی: {item["stock"]}',
                Buttons.inline([[('➕', f'inc:{product_id}'), ('➖', f'dec:{product_id}'), ('حذف', f'remove:{product_id}')],
                                [('سبد خرید', 'cart:0')]]))
        elif action == 'checkout':
            self.db.quote(user_id)
            self.db.set_state(user_id, 'checkout_coupon')
            self.service.send(user_id, 'کد تخفیف شخصی را بفرست؛ اگر کد نداری فقط - بفرست.\nلغو: /cancel')
        elif action == 'buy':
            state = self.db.get_state(user_id)
            if not state or state[0] != 'checkout_confirm' or state[1]['key'] != parts[1]:
                raise ShopError('این تأیید منقضی شده یا قبلاً انجام شده؛ «سفارش‌های من» را بررسی کن.')
            payload = state[1]
            order_id, created = self.db.checkout(user_id, payload['quote'], payload['address'], payload['key'], self.service.admin_ids)
            self.service.send(user_id, f'سفارش #{order_id} ثبت شده است. جزئیات و کد تخفیف در همین گفت‌وگو ارسال می‌شود.',
                Buttons.inline([[('سفارش‌های من', 'orders:0'), ('منوی اصلی', 'home')]]))
        elif action == 'orders':
            self.show_orders(user_id, int(parts[1]))
        elif action == 'order':
            self.service.send(user_id, self.service.order_text(int(parts[1]), user_id), Buttons.inline([[('سفارش‌های من', 'orders:0')]]))
        elif action == 'searchpage':
            state = self.db.get_state(user_id)
            if not state or state[0] != 'search_results':
                raise ShopError('این جست‌وجو منقضی شده؛ نام کالا را دوباره بفرست.')
            self.search(user_id, state[1]['query'], int(parts[1]))
        elif action == 'newticket':
            self.db.set_state(user_id, 'ticket')
            self.service.send(user_id, 'متن تیکت را بفرست (حداکثر ۱۵۰۰ نویسه).\nلغو: /cancel')
        elif action == 'tickets':
            items = [(f'تیکت #{t["id"]} | {"باز" if t["status"] == "open" else "پاسخ داده شده"}', f'ticket:{t["id"]}')
                     for t in self.db.tickets(user_id)]
            self.service.send(user_id, 'تیکت‌های من' if items else 'تیکتی ثبت نکرده‌ای.', Buttons.page(items, 'tickets', int(parts[1])))
        elif action == 'ticket':
            ticket = self.db.ticket(int(parts[1]), user_id)
            self.service.send(user_id, f'تیکت #{ticket["id"]}\n{ticket["question"]}\n\n'
                f'پاسخ: {ticket["answer"] or "هنوز پاسخی ثبت نشده"}', Buttons.inline([[('تیکت‌های من', 'tickets:0')]]))
        else:
            raise ShopError('دکمه معتبر نیست. /start را بفرست.')

    def show_categories(self, user_id, page=0):
        self.db.clear_state(user_id)
        items = [(row['name'], f'cat:{row["id"]}:0') for row in self.db.categories()]
        self.service.send(user_id, 'یک دسته‌بندی انتخاب کن:' if items else 'فروشگاه هنوز محصولی ندارد.', Buttons.page(items, 'cats', page))

    def show_products(self, user_id, category_id, page=0):
        products = self.db.products(category_id=category_id)
        items = [(f'{p.name} | {money(p.price)}' + (' | ناموجود' if not p.stock else ''), f'p:{p.id}') for p in products]
        self.service.send(user_id, 'محصولات دسته‌بندی:' if items else 'این دسته‌بندی محصولی ندارد.',
            Buttons.page(items, f'cat:{category_id}', page, [[('دسته‌بندی‌ها', 'cats:0')]]))

    def show_product(self, user_id, product_id):
        product = self.db.get_product(product_id)
        if not product or not product.active:
            raise ShopError('محصول پیدا نشد یا حذف شده است.')
        self.service.send(user_id, f'🛍 {product.name}\n\n{product.description}\n\n'
            f'قیمت هر عدد: {money(product.get_price())}\n'
            f'موجودی: {product.stock}' + (' (ناموجود)' if not product.stock else ''), Buttons.product(product))

    def search(self, user_id, query, page=0):
        self.db.set_state(user_id, 'search_results', {'query': query})
        items = [(f'{p.name} | {money(p.price)}', f'p:{p.id}') for p in self.db.products(query=query)]
        self.service.send(user_id, f'نتایج جست‌وجوی «{query}»:' if items else 'محصولی با این نام پیدا نشد.', Buttons.page(items, 'searchpage', page))

    def show_cart(self, user_id, page=0):
        self.db.clear_state(user_id)
        items = self.db.cart(user_id)
        if not items:
            self.service.send(user_id, 'سبد خرید خالی است.', Buttons.inline([[('دیدن محصولات', 'cats:0')]]))
            return
        subtotal = sum(item['price'] * item['quantity'] for item in items)
        text = self.service.quote_text({'items': items, 'subtotal': subtotal, 'discount': 0, 'total': subtotal})
        if any(item['quantity'] > item['stock'] or not item['active'] for item in items):
            text += '\n⚠️ موجودی بعضی کالاها کافی نیست؛ تعداد را کم کن یا کالا را حذف کن.'
        text += '\n\nبرای تغییر تعداد روی نام کالا بزن. کد تخفیف در مرحلهٔ بعد دریافت می‌شود.'
        buttons = [(f'{item["name"]} × {item["quantity"]}', f'ci:{item["id"]}') for item in items]
        extras = [[('نهایی‌کردن سفارش', 'checkout')], [('خالی‌کردن سبد', 'clearcart'), ('ادامهٔ خرید', 'cats:0')]]
        self.service.send(user_id, text, Buttons.page(buttons, 'cart', page, extras))

    def show_orders(self, user_id, page=0):
        self.db.clear_state(user_id)
        items = [(f'#{o["id"]} | {STATUS_LABELS[o["status"]]} | {money(o["total"])}', f'order:{o["id"]}') for o in self.db.orders(user_id)]
        self.service.send(user_id, 'سفارش‌های من' if items else 'هنوز سفارشی ثبت نکرده‌ای.', Buttons.page(items, 'orders', page))

    def show_coupons(self, user_id):
        self.db.clear_state(user_id)
        coupons = self.db.coupons(user_id)
        text = '\n\n'.join(f'{c["code"]} | {c["percent"]}٪ | تا {c["expires_at"][:10]} UTC' for c in coupons)
        self.service.send(user_id, text or 'کد تخفیف فعالی نداری؛ پس از ثبت خرید، کد خرید بعدی صادر می‌شود.')

    def support(self, user_id):
        self.db.clear_state(user_id)
        self.service.send(user_id, 'پشتیبانی فروشگاه', Buttons.inline([[('ارسال تیکت', 'newticket'), ('تیکت‌های من', 'tickets:0')]]))
