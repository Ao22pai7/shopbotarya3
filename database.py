import json
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from models import User, Product, ShopError, STATUS_LABELS, normalize_text


def now_text():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


class Database:
    def __init__(self, filename='shop.db'):
        self.conn = sqlite3.connect(filename, timeout=15)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute('PRAGMA foreign_keys = ON')
        self.create_tables()

    def create_tables(self):
        self.conn.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, username TEXT,
            phone TEXT UNIQUE, blocked INTEGER NOT NULL DEFAULT 0,
            logged_in INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE);
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
            description TEXT NOT NULL, category_id INTEGER NOT NULL REFERENCES categories(id),
            price INTEGER NOT NULL CHECK(price > 0), stock INTEGER NOT NULL CHECK(stock >= 0),
            active INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS cart (
            user_id INTEGER REFERENCES users(id), product_id INTEGER REFERENCES products(id),
            quantity INTEGER NOT NULL CHECK(quantity BETWEEN 1 AND 99),
            PRIMARY KEY(user_id, product_id));
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id),
            phone TEXT NOT NULL, address TEXT NOT NULL,
            subtotal INTEGER NOT NULL, discount INTEGER NOT NULL, total INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'processing'
                CHECK(status IN ('processing', 'shipped', 'delivered')),
            checkout_key TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS order_items (
            order_id INTEGER REFERENCES orders(id), product_id INTEGER REFERENCES products(id),
            name TEXT NOT NULL, unit_price INTEGER NOT NULL, quantity INTEGER NOT NULL,
            PRIMARY KEY(order_id, product_id));
        CREATE TABLE IF NOT EXISTS coupons (
            code TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
            percent INTEGER NOT NULL CHECK(percent BETWEEN 1 AND 100),
            expires_at TEXT NOT NULL, used_order_id INTEGER REFERENCES orders(id),
            source_order_id INTEGER NOT NULL UNIQUE REFERENCES orders(id));
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id),
            question TEXT NOT NULL, answer TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'open', created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS watches (
            user_id INTEGER REFERENCES users(id), product_id INTEGER REFERENCES products(id),
            PRIMARY KEY(user_id, product_id));
        CREATE TABLE IF NOT EXISTS states (
            user_id INTEGER PRIMARY KEY REFERENCES users(id), name TEXT NOT NULL,
            payload TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS outbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT, recipient INTEGER NOT NULL,
            text TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'service',
            status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
            next_try REAL NOT NULL DEFAULT 0);
        CREATE INDEX IF NOT EXISTS order_user ON orders(user_id, id);
        CREATE INDEX IF NOT EXISTS ticket_user ON tickets(user_id, id);
        ''')
        self.conn.commit()

    def close(self):
        self.conn.close()

    def ensure_user(self, user_id, name, username=None):
        with self.conn:
            self.conn.execute('''INSERT INTO users (id, name, username) VALUES (?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET name=excluded.name, username=excluded.username''',
                (user_id, name[:100], username))
        return self.get_user(user_id)

    def get_user(self, user_id):
        row = self.conn.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
        return User(row) if row else None

    def register(self, user_id, phone):
        user = self.get_user(user_id)
        if not user or user.blocked:
            raise ShopError('دسترسی این حساب محدود شده است.')
        try:
            with self.conn:
                self.conn.execute('UPDATE users SET phone=?, logged_in=1 WHERE id=?', (phone, user_id))
        except sqlite3.IntegrityError:
            raise ShopError('این شماره قبلاً برای حساب دیگری ثبت شده؛ با ادمین تماس بگیر.') from None

    def logout(self, user_id):
        with self.conn:
            self.conn.execute('UPDATE users SET logged_in=0 WHERE id=?', (user_id,))
            self.conn.execute('DELETE FROM states WHERE user_id=?', (user_id,))

    def require_customer(self, user_id):
        user = self.get_user(user_id)
        if not user or not user.can_shop():
            raise ShopError('ابتدا وارد حساب مجاز خودت شو: /start')
        return user

    def users(self):
        return [User(row) for row in self.conn.execute('SELECT * FROM users ORDER BY id DESC')]

    def set_blocked(self, user_id, blocked):
        if not self.get_user(user_id):
            raise ShopError('کاربر پیدا نشد.')
        with self.conn:
            self.conn.execute('UPDATE users SET blocked=? WHERE id=?', (int(blocked), user_id))
            self.conn.execute('DELETE FROM states WHERE user_id=?', (user_id,))

    def set_state(self, user_id, name, payload=None):
        with self.conn:
            self.conn.execute('''INSERT INTO states VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET name=excluded.name,
                payload=excluded.payload, updated_at=excluded.updated_at''',
                (user_id, name, json.dumps(payload or {}, ensure_ascii=False), now_text()))

    def get_state(self, user_id):
        row = self.conn.execute('SELECT * FROM states WHERE user_id=?', (user_id,)).fetchone()
        if not row:
            return None
        if datetime.now(timezone.utc) - datetime.fromisoformat(row['updated_at']) > timedelta(hours=2):
            self.clear_state(user_id)
            return None
        return row['name'], json.loads(row['payload'])

    def clear_state(self, user_id):
        with self.conn:
            self.conn.execute('DELETE FROM states WHERE user_id=?', (user_id,))

    def category(self, name):
        name = normalize_text(name)
        self.conn.execute('INSERT OR IGNORE INTO categories(name) VALUES (?)', (name,))
        return self.conn.execute('SELECT id FROM categories WHERE name=?', (name,)).fetchone()['id']

    def categories(self):
        return self.conn.execute('''SELECT c.id, c.name FROM categories c
            WHERE EXISTS (SELECT 1 FROM products p WHERE p.category_id=c.id AND p.active=1)
            ORDER BY c.name''').fetchall()

    def add_product(self, name, category, description, price, stock):
        with self.conn:
            category_id = self.category(category)
            result = self.conn.execute('''INSERT INTO products
                (name, category_id, description, price, stock) VALUES (?, ?, ?, ?, ?)''',
                (normalize_text(name), category_id, description, price, stock))
        return result.lastrowid

    def get_product(self, product_id):
        row = self.conn.execute('SELECT * FROM products WHERE id=?', (product_id,)).fetchone()
        return Product(row) if row else None

    def products(self, category_id=None, query=None):
        sql, params = 'SELECT * FROM products WHERE active=1', []
        if category_id is not None:
            sql += ' AND category_id=?'
            params.append(category_id)
        if query is not None:
            # Literal substring search: user-entered % and _ are not wildcards.
            query = normalize_text(query).replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            sql += " AND name LIKE ? ESCAPE '\\'"
            params.append('%' + query + '%')
        return [Product(row) for row in self.conn.execute(sql + ' ORDER BY id DESC', params)]

    def edit_product(self, product_id, field, value):
        if field not in ('name', 'description', 'category', 'price', 'stock'):
            raise ShopError('فیلد ویرایش معتبر نیست.')
        with self.conn:
            self.conn.execute('BEGIN IMMEDIATE')
            product = self.get_product(product_id)
            if not product or not product.active:
                raise ShopError('محصول حذف شده یا وجود ندارد.')
            if field == 'category':
                field, value = 'category_id', self.category(value)
            if field == 'name':
                value = normalize_text(value)
            self.conn.execute(f'UPDATE products SET {field}=? WHERE id=?', (value, product_id))
            if field == 'stock' and product.stock == 0 and value > 0:
                watchers = self.conn.execute('''SELECT w.user_id FROM watches w JOIN users u
                    ON u.id=w.user_id WHERE w.product_id=? AND u.blocked=0''', (product_id,)).fetchall()
                for watcher in watchers:
                    self._queue(watcher['user_id'], f'🔔 محصول «{product.name}» دوباره موجود شد.\n'
                                f'برای مشاهده: /product_{product_id}', 'restock')
                    self.conn.execute('DELETE FROM watches WHERE user_id=? AND product_id=?',
                                      (watcher['user_id'], product_id))

    def delete_product(self, product_id):
        with self.conn:
            result = self.conn.execute('UPDATE products SET active=0 WHERE id=? AND active=1', (product_id,))
            if not result.rowcount:
                raise ShopError('محصول قبلاً حذف شده یا وجود ندارد.')
            self.conn.execute('DELETE FROM cart WHERE product_id=?', (product_id,))
            self.conn.execute('DELETE FROM watches WHERE product_id=?', (product_id,))

    def watch(self, user_id, product_id):
        self.require_customer(user_id)
        product = self.get_product(product_id)
        if not product or not product.active:
            raise ShopError('محصول پیدا نشد.')
        if product.stock > 0:
            raise ShopError('این محصول همین حالا موجود است.')
        with self.conn:
            self.conn.execute('INSERT OR IGNORE INTO watches VALUES (?, ?)', (user_id, product_id))

    def change_cart(self, user_id, product_id, delta):
        self.require_customer(user_id)
        with self.conn:
            self.conn.execute('BEGIN IMMEDIATE')
            product = self.get_product(product_id)
            if not product or not product.active:
                raise ShopError('این محصول در دسترس نیست.')
            old = self.conn.execute('SELECT quantity FROM cart WHERE user_id=? AND product_id=?',
                                    (user_id, product_id)).fetchone()
            quantity = (old['quantity'] if old else 0) + delta
            if quantity <= 0:
                self.conn.execute('DELETE FROM cart WHERE user_id=? AND product_id=?', (user_id, product_id))
                return
            if delta > 0 and quantity > product.stock:
                raise ShopError(f'موجودی فعلی این کالا {product.stock} عدد است.')
            if quantity > 99:
                raise ShopError('حداکثر تعداد هر کالا در سبد ۹۹ عدد است.')
            count = self.conn.execute('SELECT COUNT(*) FROM cart WHERE user_id=?', (user_id,)).fetchone()[0]
            if not old and count >= 20:
                raise ShopError('حداکثر ۲۰ محصول متفاوت در هر سفارش مجاز است.')
            self.conn.execute('''INSERT INTO cart VALUES (?, ?, ?) ON CONFLICT(user_id, product_id)
                DO UPDATE SET quantity=excluded.quantity''', (user_id, product_id, quantity))

    def remove_cart(self, user_id, product_id=None):
        with self.conn:
            if product_id is None:
                self.conn.execute('DELETE FROM cart WHERE user_id=?', (user_id,))
            else:
                self.conn.execute('DELETE FROM cart WHERE user_id=? AND product_id=?', (user_id, product_id))

    def cart(self, user_id):
        return [dict(row) for row in self.conn.execute('''SELECT p.id, p.name, p.price, p.stock,
            p.active, c.quantity FROM cart c JOIN products p ON p.id=c.product_id
            WHERE c.user_id=? ORDER BY p.id''', (user_id,))]

    def quote(self, user_id, code=''):
        self.require_customer(user_id)
        items = self.cart(user_id)
        if not items:
            raise ShopError('سبد خرید خالی است.')
        for item in items:
            if not item['active'] or item['quantity'] > item['stock']:
                raise ShopError(f'موجودی «{item["name"]}» کافی نیست؛ سبد را اصلاح کن.')
        subtotal = sum(item['price'] * item['quantity'] for item in items)
        code = code.strip().upper()
        discount = 0
        if code:
            coupon = self.conn.execute('SELECT * FROM coupons WHERE code=? AND user_id=?', (code, user_id)).fetchone()
            if not coupon or coupon['used_order_id'] is not None or coupon['expires_at'] <= now_text():
                raise ShopError('کد تخفیف نامعتبر، استفاده‌شده، منقضی یا متعلق به حساب دیگری است.')
            discount = subtotal * coupon['percent'] // 100
        return {'items': [{'id': i['id'], 'name': i['name'], 'price': i['price'], 'quantity': i['quantity']}
                          for i in items], 'subtotal': subtotal, 'discount': discount,
                'total': subtotal - discount, 'code': code}

    def checkout(self, user_id, quote, address, checkout_key, admin_ids=()):
        with self.conn:
            self.conn.execute('BEGIN IMMEDIATE')
            previous = self.conn.execute('SELECT * FROM orders WHERE checkout_key=?', (checkout_key,)).fetchone()
            if previous:
                if previous['user_id'] != user_id:
                    raise ShopError('درخواست سفارش متعلق به شما نیست.')
                return previous['id'], False
            user = self.require_customer(user_id)
            fresh = self.quote(user_id, quote.get('code', ''))
            if fresh != quote:
                raise ShopError('سبد یا قیمت‌ها تغییر کرده‌اند؛ دوباره سبد را بررسی و تأیید کن.')
            result = self.conn.execute('''INSERT INTO orders
                (user_id, phone, address, subtotal, discount, total, checkout_key, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                (user_id, user.phone, address, fresh['subtotal'], fresh['discount'], fresh['total'], checkout_key, now_text()))
            order_id = result.lastrowid
            for item in fresh['items']:
                changed = self.conn.execute('UPDATE products SET stock=stock-? WHERE id=? AND active=1 AND stock>=?',
                                            (item['quantity'], item['id'], item['quantity']))
                if changed.rowcount != 1:
                    raise ShopError('موجودی تغییر کرده؛ سبد را دوباره بررسی کن.')
                self.conn.execute('INSERT INTO order_items VALUES (?, ?, ?, ?, ?)',
                                  (order_id, item['id'], item['name'], item['price'], item['quantity']))
            if fresh['code']:
                self.conn.execute('UPDATE coupons SET used_order_id=? WHERE code=?', (order_id, fresh['code']))
            new_code = secrets.token_hex(6).upper()
            expires = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(timespec='seconds')
            self.conn.execute('INSERT INTO coupons VALUES (?, ?, 10, ?, NULL, ?)', (new_code, user_id, expires, order_id))
            self.conn.execute('DELETE FROM cart WHERE user_id=?', (user_id,))
            self.conn.execute('DELETE FROM states WHERE user_id=?', (user_id,))
            self._queue(user_id, f'✅ سفارش #{order_id} ثبت شد. پرداخت هنگام تحویل.\n'
                        f'مبلغ نهایی: {fresh["total"]:,} تومان\n'
                        f'کد تخفیف شخصی خرید بعدی: {new_code}\n۱۰٪، یک‌بارمصرف، اعتبار ۳۰ روز.')
            for admin_id in admin_ids:
                self._queue(admin_id, f'📦 سفارش جدید #{order_id}\nمبلغ: {fresh["total"]:,} تومان\n'
                            f'بررسی در پنل ادمین: /admin')
        return order_id, True

    def coupons(self, user_id):
        return self.conn.execute('''SELECT * FROM coupons WHERE user_id=?
            AND used_order_id IS NULL AND expires_at>? ORDER BY source_order_id DESC''', (user_id, now_text())).fetchall()

    def orders(self, user_id=None):
        if user_id is None:
            return self.conn.execute('SELECT * FROM orders ORDER BY id DESC').fetchall()
        return self.conn.execute('SELECT * FROM orders WHERE user_id=? ORDER BY id DESC', (user_id,)).fetchall()

    def order(self, order_id, user_id=None):
        row = self.conn.execute('SELECT * FROM orders WHERE id=?', (order_id,)).fetchone()
        if not row or (user_id is not None and row['user_id'] != user_id):
            raise ShopError('سفارش پیدا نشد.')
        items = self.conn.execute('SELECT * FROM order_items WHERE order_id=? ORDER BY product_id', (order_id,)).fetchall()
        return row, items

    def set_order_status(self, order_id, status):
        if status not in STATUS_LABELS:
            raise ShopError('وضعیت سفارش معتبر نیست.')
        with self.conn:
            self.conn.execute('BEGIN IMMEDIATE')
            row, items = self.order(order_id)
            if row['status'] == status:
                return
            next_status = {'processing': 'shipped', 'shipped': 'delivered'}
            if next_status.get(row['status']) != status:
                raise ShopError('وضعیت باید به‌ترتیب پردازش، ارسال، تحویل تغییر کند.')
            self.conn.execute('UPDATE orders SET status=? WHERE id=?', (status, order_id))
            self._queue(row['user_id'], f'📦 سفارش #{order_id}\nوضعیت جدید: {STATUS_LABELS[status]}')

    def add_ticket(self, user_id, question, admin_ids=()):
        self.require_customer(user_id)
        with self.conn:
            result = self.conn.execute('INSERT INTO tickets(user_id, question, created_at) VALUES (?, ?, ?)',
                                       (user_id, question, now_text()))
            ticket_id = result.lastrowid
            for admin_id in admin_ids:
                self._queue(admin_id, f'🎫 تیکت جدید #{ticket_id}\n{question}\nبررسی و پاسخ: /admin')
        return ticket_id

    def tickets(self, user_id=None):
        if user_id is None:
            return self.conn.execute('SELECT * FROM tickets ORDER BY id DESC').fetchall()
        return self.conn.execute('SELECT * FROM tickets WHERE user_id=? ORDER BY id DESC', (user_id,)).fetchall()

    def ticket(self, ticket_id, user_id=None):
        row = self.conn.execute('SELECT * FROM tickets WHERE id=?', (ticket_id,)).fetchone()
        if not row or (user_id is not None and row['user_id'] != user_id):
            raise ShopError('تیکت پیدا نشد.')
        return row

    def answer_ticket(self, ticket_id, answer):
        with self.conn:
            row = self.ticket(ticket_id)
            self.conn.execute("UPDATE tickets SET answer=?, status='answered' WHERE id=?", (answer, ticket_id))
            self._queue(row['user_id'], f'پاسخ تیکت #{ticket_id}:\n{answer}')

    def _queue(self, user_id, text, kind='service'):
        self.conn.execute('INSERT INTO outbox(recipient, text, kind) VALUES (?, ?, ?)', (user_id, text, kind))

    def broadcast(self, text):
        with self.conn:
            users = self.conn.execute('SELECT id FROM users WHERE phone IS NOT NULL AND blocked=0').fetchall()
            for user in users:
                self._queue(user['id'], '📣 اطلاعیهٔ فروشگاه\n' + text, 'broadcast')
        return len(users)

    def pending_messages(self):
        stamp = datetime.now(timezone.utc).timestamp()
        return self.conn.execute("SELECT * FROM outbox WHERE status='pending' AND next_try<=? ORDER BY id", (stamp,)).fetchall()

    def mark_message(self, message_id, status, delay=0):
        with self.conn:
            self.conn.execute('UPDATE outbox SET status=?, attempts=attempts+1, next_try=? WHERE id=?',
                              (status, datetime.now(timezone.utc).timestamp() + delay, message_id))

    def outbox_counts(self):
        return {row['status']: row['count'] for row in self.conn.execute('SELECT status, COUNT(*) AS count FROM outbox GROUP BY status')}
