import secrets
from buttons import Buttons
from models import ShopError, STATUS_LABELS, clean_text, integer, money


class AdminTelegramBot:
    def __init__(self, service):
        self.service = service
        self.db = service.db

    def menu(self, user_id):
        self.service.require_admin(user_id)
        self.db.clear_state(user_id)
        self.service.send(user_id, 'پنل مدیریت فروشگاه', Buttons.admin())

    def product(self, user_id, product_id):
        product = self.db.get_product(product_id)
        if not product or not product.active:
            raise ShopError('محصول پیدا نشد یا حذف شده است.')
        rows = [
            [('ویرایش نام', f'admin:edit:{product_id}:name'),
             ('ویرایش توضیح', f'admin:edit:{product_id}:description')],
            [('ویرایش قیمت', f'admin:edit:{product_id}:price'),
             ('ویرایش دسته', f'admin:edit:{product_id}:category')],
            [('تنظیم موجودی', f'admin:edit:{product_id}:stock'),
             ('حذف محصول', f'admin:delete:{product_id}')],
            [('بازگشت', 'admin:products:0')]]
        self.service.send(user_id, f'#{product.id} | {product.name}\n{product.description}\n'
            f'قیمت: {money(product.price)}\nموجودی: {product.stock}', Buttons.inline(rows))

    def callback(self, user_id, data):
        self.service.require_admin(user_id)
        parts = data.split(':')
        action = parts[1]
        if action != 'broadcast_yes':
            self.db.clear_state(user_id)
        if action == 'home':
            self.menu(user_id)
        elif action == 'new':
            self.db.set_state(user_id, 'admin_new', {'step': 'name'})
            self.service.send(user_id, 'نام محصول را بفرست (حداکثر ۸۰ نویسه).\nلغو: /cancel')
        elif action == 'products':
            items = [(f'#{p.id} {p.name} | موجودی {p.stock}', f'admin:product:{p.id}') for p in self.db.products()]
            self.service.send(user_id, 'مدیریت محصولات' if items else 'هنوز محصولی ثبت نشده.',
                Buttons.page(items, 'admin:products', int(parts[2]), [[('پنل ادمین', 'admin:home')]]))
        elif action == 'product':
            self.product(user_id, int(parts[2]))
        elif action == 'edit':
            product_id, field = int(parts[2]), parts[3]
            names = {'name': 'نام', 'description': 'توضیح', 'category': 'دسته‌بندی', 'price': 'قیمت به تومان', 'stock': 'موجودی'}
            if field not in names:
                raise ShopError('عملیات ویرایش معتبر نیست.')
            product = self.db.get_product(product_id)
            if not product or not product.active:
                raise ShopError('محصول پیدا نشد.')
            self.db.set_state(user_id, 'admin_edit', {'id': product_id, 'field': field})
            self.service.send(user_id, f'{names[field]} جدید را بفرست.\nلغو: /cancel')
        elif action == 'delete':
            product = self.db.get_product(int(parts[2]))
            if not product or not product.active:
                raise ShopError('محصول پیدا نشد.')
            self.service.send(user_id, f'محصول «{product.name}» حذف شود؟ سفارش‌های قبلی حفظ می‌شوند.',
                Buttons.inline([[('تأیید حذف', f'admin:delete_yes:{product.id}'), ('انصراف', f'admin:product:{product.id}')]]))
        elif action == 'delete_yes':
            self.db.delete_product(int(parts[2]))
            self.db.clear_state(user_id)
            self.service.send(user_id, 'محصول حذف شد.', Buttons.admin())
        elif action == 'orders':
            items = [(f'#{o["id"]} | {STATUS_LABELS[o["status"]]} | {money(o["total"])}', f'admin:order:{o["id"]}')
                     for o in self.db.orders()]
            self.service.send(user_id, 'سفارش‌ها' if items else 'سفارشی ثبت نشده.',
                Buttons.page(items, 'admin:orders', int(parts[2]), [[('پنل ادمین', 'admin:home')]]))
        elif action == 'order':
            order_id = int(parts[2])
            order, items = self.db.order(order_id)
            rows = []
            next_status = {'processing': 'shipped', 'shipped': 'delivered'}.get(order['status'])
            if next_status:
                rows.append([(f'تغییر به «{STATUS_LABELS[next_status]}»', f'admin:status:{order_id}:{next_status}')])
            rows.append([('سفارش‌ها', 'admin:orders:0')])
            self.service.send(user_id, self.service.order_text(order_id) + f'\nشناسهٔ مشتری: {order["user_id"]}', Buttons.inline(rows))
        elif action == 'status':
            self.db.set_order_status(int(parts[2]), parts[3])
            self.service.send(user_id, 'وضعیت سفارش ذخیره و اعلان مشتری در صف ارسال قرار گرفت.', Buttons.admin())
        elif action == 'tickets':
            items = [(f'#{t["id"]} | {"باز" if t["status"] == "open" else "پاسخ داده شده"} | {t["question"][:30]}',
                      f'admin:ticket:{t["id"]}') for t in self.db.tickets()]
            self.service.send(user_id, 'تیکت‌ها' if items else 'تیکتی ثبت نشده.',
                Buttons.page(items, 'admin:tickets', int(parts[2]), [[('پنل ادمین', 'admin:home')]]))
        elif action == 'ticket':
            ticket = self.db.ticket(int(parts[2]))
            user = self.db.get_user(ticket['user_id'])
            flag = 0 if user.blocked else 1
            rows = [[('پاسخ دادن', f'admin:reply:{ticket["id"]}'),
                     ('آنبلاک' if user.blocked else 'بلاک', f'admin:block:{user.id}:{flag}')],
                    [('تیکت‌ها', 'admin:tickets:0')]]
            self.service.send(user_id, f'تیکت #{ticket["id"]}\nکاربر: {user.name} ({user.id})\n'
                f'شماره: {user.phone or "ثبت نشده"}\n\n{ticket["question"]}\n\n'
                f'آخرین پاسخ: {ticket["answer"] or "بدون پاسخ"}', Buttons.inline(rows))
        elif action == 'reply':
            ticket = self.db.ticket(int(parts[2]))
            self.db.set_state(user_id, 'admin_reply', {'id': ticket['id']})
            self.service.send(user_id, f'پاسخ تیکت #{ticket["id"]} را بنویس (حداکثر ۱۵۰۰ نویسه).\nلغو: /cancel')
        elif action == 'users':
            items = [(f'{u.name} | {u.id} | {"بلاک" if u.blocked else "فعال"}', f'admin:user:{u.id}') for u in self.db.users()]
            self.service.send(user_id, 'کاربران', Buttons.page(items, 'admin:users', int(parts[2]), [[('پنل ادمین', 'admin:home')]]))
        elif action == 'user':
            user = self.db.get_user(int(parts[2]))
            if not user:
                raise ShopError('کاربر پیدا نشد.')
            self.service.send(user_id, f'{user.name}\nشناسه: {user.id}\nشماره: {user.phone or "ثبت نشده"}\n'
                f'وضعیت: {"بلاک" if user.blocked else "فعال"}',
                Buttons.inline([[('آنبلاک' if user.blocked else 'بلاک', f'admin:block:{user.id}:{0 if user.blocked else 1}')],
                                [('کاربران', 'admin:users:0')]]))
        elif action == 'block':
            target, flag = int(parts[2]), int(parts[3])
            if flag not in (0, 1):
                raise ShopError('وضعیت کاربر معتبر نیست.')
            if self.service.is_admin(target):
                raise ShopError('حساب ادمین را نمی‌توان بلاک کرد.')
            self.db.set_blocked(target, bool(flag))
            self.service.send(user_id, 'کاربر بلاک شد.' if flag else 'کاربر آنبلاک شد.', Buttons.admin())
        elif action == 'broadcast':
            self.db.set_state(user_id, 'admin_broadcast')
            self.service.send(user_id, 'متن اطلاعیه را بنویس (حداکثر ۱۵۰۰ نویسه). قبل از ارسال پیش‌نمایش می‌بینی.\nلغو: /cancel')
        elif action == 'broadcast_yes':
            state = self.db.get_state(user_id)
            if (not state or state[0] != 'admin_broadcast_confirm'
                    or len(parts) != 3 or state[1]['key'] != parts[2]):
                raise ShopError('پیش‌نمایش منقضی شده یا قبلاً ارسال شده است.')
            count = self.db.broadcast(state[1]['text'])
            self.db.clear_state(user_id)
            self.service.send(user_id, f'اطلاعیه برای {count} مشتری در صف ارسال قرار گرفت.', Buttons.admin())
        elif action == 'outbox':
            sent, failed = self.service.dispatch_notifications()
            counts = self.db.outbox_counts()
            self.service.send(user_id, f'ارسال موفق در این نوبت: {sent}\n'
                f'در انتظار: {counts.get("pending", 0)}\nناموفق دائمی: {counts.get("failed", 0)}\n'
                'پیام‌های موقتاً ناموفق پس از مهلت منبع، با دریافت پیام بعدی یا همین دکمه دوباره بررسی می‌شوند.', Buttons.admin())
        else:
            raise ShopError('این دکمه معتبر نیست؛ /admin را بفرست.')

    def input(self, user_id, text, state):
        self.service.require_admin(user_id)
        name, payload = state
        if name == 'admin_new':
            step = payload['step']
            limits = {'name': (1, 80), 'category': (1, 50), 'description': (1, 800)}
            if step in limits:
                low, high = limits[step]
                payload[step] = clean_text(text, step, low, high)
            else:
                payload[step] = integer(text, 'قیمت' if step == 'price' else 'موجودی',
                                        1 if step == 'price' else 0, 10**12 if step == 'price' else 10**6)
            following = {'name': ('category', 'نام دسته‌بندی را بفرست؛ دستهٔ جدید خودکار ساخته می‌شود.'),
                         'category': ('description', 'توضیح محصول را بفرست (حداکثر ۸۰۰ نویسه).'),
                         'description': ('price', 'قیمت را به تومان و با عدد صحیح بفرست.'),
                         'price': ('stock', 'موجودی اولیه را بفرست؛ صفر یعنی ناموجود.')}
            if step in following:
                payload['step'], prompt = following[step]
                self.db.set_state(user_id, name, payload)
                self.service.send(user_id, prompt + '\nلغو: /cancel')
            else:
                product_id = self.db.add_product(payload['name'], payload['category'], payload['description'], payload['price'], payload['stock'])
                self.db.clear_state(user_id)
                self.service.send(user_id, 'محصول اضافه شد.')
                self.product(user_id, product_id)
        elif name == 'admin_edit':
            field = payload['field']
            if field in ('price', 'stock'):
                value = integer(text, 'مقدار', 1 if field == 'price' else 0, 10**12 if field == 'price' else 10**6)
            else:
                value = clean_text(text, 'متن', 1, {'name': 80, 'category': 50, 'description': 800}[field])
            self.db.edit_product(payload['id'], field, value)
            self.db.clear_state(user_id)
            self.service.send(user_id, 'تغییر ذخیره شد.')
            self.product(user_id, payload['id'])
        elif name == 'admin_reply':
            answer = clean_text(text, 'پاسخ', 1, 1500)
            self.db.answer_ticket(payload['id'], answer)
            self.db.clear_state(user_id)
            self.service.send(user_id, 'پاسخ ذخیره و در صف ارسال قرار گرفت.', Buttons.admin())
        elif name == 'admin_broadcast':
            text = clean_text(text, 'اطلاعیه', 1, 1500)
            key = secrets.token_hex(8)
            self.db.set_state(user_id, 'admin_broadcast_confirm', {'text': text, 'key': key})
            self.service.send(user_id, 'پیش‌نمایش اطلاعیه:\n\n' + text,
                Buttons.inline([[('ارسال برای مشتریان', f'admin:broadcast_yes:{key}'), ('انصراف', 'admin:home')]]))
        else:
            self.service.send(user_id, 'از دکمه‌های تأیید استفاده کن یا /cancel بفرست.')
