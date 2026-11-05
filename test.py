"""Offline tests; no real Telegram users, tokens or payments are used."""
import os
import sqlite3
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
import telebot
from telebot.apihelper import ApiTelegramException
from telebot.types import Message, CallbackQuery, InlineKeyboardMarkup
from database import Database
from models import ShopError, clean_text, integer, phone_number
from service import ShopService
from handler import TelegramBot


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.db = Database(':memory:')
        for user_id in [1, 2]:
            self.db.ensure_user(user_id, f'User {user_id}')
            self.db.register(user_id, f'+98912000000{user_id}')
        self.product = self.db.add_product('کتاب پایتون', 'کتاب', 'آموزش پایتون', 100000, 10)

    def tearDown(self):
        self.db.close()

    def buy(self, user_id=1, code='', quantity=1, key=None):
        self.db.change_cart(user_id, self.product, quantity)
        quote = self.db.quote(user_id, code)
        return self.db.checkout(user_id, quote, 'تهران، نشانی آزمایشی', key or str(len(self.db.orders()) + 1))

    def test_registration_login_logout_and_duplicate_phone(self):
        self.assertTrue(self.db.get_user(1).can_shop())
        self.db.logout(1)
        self.assertFalse(self.db.get_user(1).can_shop())
        with self.assertRaises(ShopError):
            self.db.register(2, '+989120000001')
        self.db.register(1, '+989120000001')
        self.assertTrue(self.db.get_user(1).can_shop())

    def test_block_cannot_be_bypassed_by_contact_or_start(self):
        self.db.set_blocked(1, True)
        self.db.ensure_user(1, 'new name')
        self.assertTrue(self.db.get_user(1).blocked)
        with self.assertRaises(ShopError):
            self.db.register(1, '+989120000001')
        with self.assertRaises(ShopError):
            self.db.change_cart(1, self.product, 1)
        self.db.set_blocked(1, False)
        self.db.change_cart(1, self.product, 1)

    def test_categories_search_and_literal_wildcards(self):
        self.db.add_product('یادگیری SQL', 'كتاب', 'توضیح', 50000, 0)
        self.assertEqual(len(self.db.categories()), 1)
        self.assertEqual(len(self.db.products(query='پايتون')), 1)
        self.assertEqual(self.db.products(query='%'), [])
        self.assertEqual(self.db.products(query="'; DROP TABLE products;--"), [])
        self.assertEqual(len(self.db.products()), 2)

    def test_cart_total_quantity_and_isolation(self):
        self.db.change_cart(1, self.product, 2)
        self.db.change_cart(2, self.product, 3)
        self.assertEqual(self.db.quote(1)['total'], 200000)
        self.db.change_cart(1, self.product, -1)
        self.assertEqual(self.db.cart(1)[0]['quantity'], 1)
        self.db.remove_cart(1)
        self.assertEqual(self.db.cart(1), [])
        self.assertEqual(self.db.cart(2)[0]['quantity'], 3)

    def test_stock_limit_and_decreasing_short_cart(self):
        with self.assertRaises(ShopError):
            self.db.change_cart(1, self.product, 11)
        self.db.change_cart(1, self.product, 5)
        self.db.edit_product(self.product, 'stock', 1)
        self.db.change_cart(1, self.product, -1)
        self.assertEqual(self.db.cart(1)[0]['quantity'], 4)
        with self.assertRaises(ShopError):
            self.db.quote(1)

    def test_checkout_snapshot_total_stock_coupon_and_cart(self):
        order_id, created = self.buy(quantity=2)
        order, items = self.db.order(order_id, 1)
        self.assertTrue(created)
        self.assertEqual(order['total'], 200000)
        self.assertEqual(items[0]['quantity'], 2)
        self.assertEqual(self.db.get_product(self.product).stock, 8)
        self.assertEqual(self.db.cart(1), [])
        self.assertEqual(len(self.db.coupons(1)), 1)
        self.assertIn('۱۰٪', self.db.pending_messages()[0]['text'])

    def test_order_snapshot_survives_price_edit_and_delete(self):
        order_id, created = self.buy()
        self.db.edit_product(self.product, 'price', 500000)
        self.db.edit_product(self.product, 'name', 'نام جدید')
        self.db.delete_product(self.product)
        order, items = self.db.order(order_id)
        self.assertEqual(items[0]['name'], 'کتاب پایتون')
        self.assertEqual(items[0]['unit_price'], 100000)
        self.assertEqual(self.db.products(), [])

    def test_duplicate_checkout_is_idempotent(self):
        self.db.change_cart(1, self.product, 1)
        quote = self.db.quote(1)
        first, created = self.db.checkout(1, quote, 'تهران، خیابان نمونه', 'same-key')
        second, created_again = self.db.checkout(1, quote, 'تهران، خیابان نمونه', 'same-key')
        self.assertEqual(first, second)
        self.assertFalse(created_again)
        self.assertEqual(len(self.db.orders()), 1)
        self.assertEqual(len(self.db.coupons(1)), 1)
        self.assertEqual(self.db.get_product(self.product).stock, 9)
        with self.assertRaises(ShopError):
            self.db.checkout(2, quote, 'نشانی آزمایشی', 'same-key')

    def test_price_change_invalidates_confirmation(self):
        self.db.change_cart(1, self.product, 1)
        quote = self.db.quote(1)
        self.db.edit_product(self.product, 'price', 200000)
        with self.assertRaises(ShopError):
            self.db.checkout(1, quote, 'نشانی آزمایشی', 'old-quote')
        self.assertEqual(self.db.orders(), [])
        self.assertEqual(self.db.get_product(self.product).stock, 10)

    def test_cart_change_invalidates_confirmation(self):
        self.db.change_cart(1, self.product, 1)
        quote = self.db.quote(1)
        self.db.change_cart(1, self.product, 1)
        with self.assertRaises(ShopError):
            self.db.checkout(1, quote, 'نشانی آزمایشی', 'old-cart')
        self.assertEqual(self.db.orders(), [])

    def test_checkout_rollback_after_injected_database_failure(self):
        self.db.change_cart(1, self.product, 2)
        quote = self.db.quote(1)
        self.db.conn.execute('''CREATE TRIGGER fail_coupon BEFORE INSERT ON coupons
            BEGIN SELECT RAISE(ABORT, 'test simulated failure'); END''')
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.checkout(1, quote, 'نشانی آزمایشی', 'rollback')
        self.assertEqual(self.db.orders(), [])
        self.assertEqual(self.db.get_product(self.product).stock, 10)
        self.assertEqual(self.db.cart(1)[0]['quantity'], 2)
        self.assertEqual(self.db.pending_messages(), [])

    def test_coupon_owner_single_use_expiry_and_exact_rounding(self):
        self.buy()
        code = self.db.coupons(1)[0]['code']
        self.db.edit_product(self.product, 'price', 100001)
        self.db.change_cart(1, self.product, 1)
        quote = self.db.quote(1, code.lower())
        self.assertEqual(quote['discount'], 10000)
        self.assertEqual(quote['total'], 90001)
        self.db.change_cart(2, self.product, 1)
        with self.assertRaises(ShopError):
            self.db.quote(2, code)
        self.db.checkout(1, quote, 'نشانی آزمایشی', 'coupon-second')
        self.db.change_cart(1, self.product, 1)
        with self.assertRaises(ShopError):
            self.db.quote(1, code)
        fresh_code = self.db.coupons(1)[0]['code']
        with self.db.conn:
            self.db.conn.execute('UPDATE coupons SET expires_at=? WHERE code=?', ('2000-01-01T00:00:00+00:00', fresh_code))
        with self.assertRaises(ShopError):
            self.db.quote(1, fresh_code)

    def test_order_and_ticket_owner_checks(self):
        order_id, created = self.buy()
        ticket_id = self.db.add_ticket(1, 'سؤال من')
        with self.assertRaises(ShopError):
            self.db.order(order_id, 2)
        with self.assertRaises(ShopError):
            self.db.ticket(ticket_id, 2)

    def test_status_progression_and_single_notification(self):
        order_id, created = self.buy()
        with self.assertRaises(ShopError):
            self.db.set_order_status(order_id, 'delivered')
        self.db.set_order_status(order_id, 'shipped')
        count = len(self.db.pending_messages())
        self.db.set_order_status(order_id, 'shipped')
        self.assertEqual(len(self.db.pending_messages()), count)
        self.db.set_order_status(order_id, 'delivered')
        with self.assertRaises(ShopError):
            self.db.set_order_status(order_id, 'processing')

    def test_restock_is_opt_in_and_only_zero_to_positive(self):
        self.db.edit_product(self.product, 'stock', 0)
        self.db.watch(1, self.product)
        self.db.watch(1, self.product)
        self.db.edit_product(self.product, 'stock', 5)
        queued = self.db.pending_messages()
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0]['recipient'], 1)
        self.assertEqual(queued[0]['kind'], 'restock')
        self.db.edit_product(self.product, 'stock', 8)
        self.assertEqual(len(self.db.pending_messages()), 1)

    def test_restock_skips_blocked_customers(self):
        self.db.edit_product(self.product, 'stock', 0)
        self.db.watch(1, self.product)
        self.db.set_blocked(1, True)
        self.db.edit_product(self.product, 'stock', 5)
        self.assertEqual(self.db.pending_messages(), [])

    def test_ticket_reply_persists_and_queues(self):
        ticket = self.db.add_ticket(1, 'سؤال مشتری', [99])
        self.db.answer_ticket(ticket, 'پاسخ ادمین')
        row = self.db.ticket(ticket, 1)
        self.assertEqual(row['status'], 'answered')
        self.assertEqual(row['answer'], 'پاسخ ادمین')
        self.assertEqual([r['recipient'] for r in self.db.pending_messages()], [99, 1])

    def test_broadcast_only_registered_unblocked(self):
        self.db.ensure_user(3, 'unregistered')
        self.db.set_blocked(2, True)
        count = self.db.broadcast('کد تخفیف و خبر فروشگاه')
        self.assertEqual(count, 1)
        self.assertEqual(self.db.pending_messages()[0]['recipient'], 1)

    def test_validation_persian_digits_and_phone(self):
        self.assertEqual(integer('۱۲۳٬۴۵۶', 'قیمت'), 123456)
        self.assertEqual(integer('١٢٣', 'قیمت'), 123)
        self.assertEqual(phone_number('۹۸۹۱۲۱۲۳۴۵۶۷'), '+989121234567')
        for bad in ['-10', '1.5', 'NaN', '1e9']:
            with self.assertRaises(ShopError):
                integer(bad, 'قیمت')
        with self.assertRaises(ShopError):
            clean_text('', 'نام')

    def test_state_isolation_and_expiration(self):
        self.db.set_state(1, 'checkout_coupon', {'hello': 'سلام'})
        self.assertIsNone(self.db.get_state(2))
        self.assertEqual(self.db.get_state(1)[1]['hello'], 'سلام')
        with self.db.conn:
            self.db.conn.execute("UPDATE states SET updated_at='2000-01-01T00:00:00+00:00' WHERE user_id=1")
        self.assertIsNone(self.db.get_state(1))


class PersistenceTests(unittest.TestCase):
    def test_restart_preserves_users_products_cart_states_and_orders(self):
        with tempfile.TemporaryDirectory() as folder:
            filename = os.path.join(folder, 'shop.db')
            db = Database(filename)
            db.ensure_user(1, 'Test')
            db.register(1, '+989121234567')
            pid = db.add_product('Book', 'Books', 'Text', 100, 2)
            db.change_cart(1, pid, 1)
            db.set_state(1, 'checkout_address', {'code': ''})
            db.close()
            db = Database(filename)
            self.assertTrue(db.get_user(1).can_shop())
            self.assertEqual(db.cart(1)[0]['quantity'], 1)
            self.assertEqual(db.get_state(1)[0], 'checkout_address')
            db.checkout(1, db.quote(1), 'Test address', 'persistent')
            db.close()
            db = Database(filename)
            self.assertEqual(len(db.orders(1)), 1)
            self.assertEqual(db.get_product(pid).stock, 1)
            self.assertEqual(len(db.pending_messages()), 1)
            db.close()

    def test_last_item_cannot_be_sold_to_two_buyers(self):
        with tempfile.TemporaryDirectory() as folder:
            filename = os.path.join(folder, 'shop.db')
            db = Database(filename)
            pid = db.add_product('Last item', 'Test', 'Only one', 100, 1)
            for user_id in (1, 2):
                db.ensure_user(user_id, 'Test')
                db.register(user_id, f'+98912000000{user_id}')
                db.change_cart(user_id, pid, 1)
            db.close()
            barrier = threading.Barrier(2)
            outcomes = []

            def buyer(user_id):
                connection = Database(filename)
                try:
                    quote = connection.quote(user_id)
                    barrier.wait(timeout=10)
                    connection.checkout(user_id, quote, 'Test address', str(user_id))
                    outcomes.append('bought')
                except ShopError:
                    outcomes.append('sold-out')
                finally:
                    connection.close()

            threads = [threading.Thread(target=buyer, args=(user_id,)) for user_id in (1, 2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=20)
                self.assertFalse(thread.is_alive())
            self.assertCountEqual(outcomes, ['bought', 'sold-out'])
            db = Database(filename)
            self.assertEqual(db.get_product(pid).stock, 0)
            self.assertEqual(len(db.orders()), 1)
            db.close()


class BotTests(unittest.TestCase):
    def setUp(self):
        self.error_patch = patch('handler.logging.error')
        self.unexpected_errors = self.error_patch.start()
        self.db = Database(':memory:')
        self.bot = telebot.TeleBot('123456:OFFLINE_TEST_ONLY', threaded=False)
        self.sent = []
        self.bot.send_message = self.send
        self.bot.answer_callback_query = Mock()
        self.service = ShopService(self.bot, self.db, admin_ids=[99])
        self.handler = TelegramBot(self.service)
        self.product = self.db.add_product('کتاب', 'آموزشی', 'کتاب پایتون', 100000, 10)

    def tearDown(self):
        self.db.close()
        self.error_patch.stop()
        self.unexpected_errors.assert_not_called()

    def send(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text, kwargs))
        self.assertLessEqual(len(text.encode('utf-16-le')) // 2, 4096)
        markup = kwargs.get('reply_markup')
        if isinstance(markup, InlineKeyboardMarkup):
            for row in markup.to_dict().get('inline_keyboard', []):
                for item in row:
                    self.assertLessEqual(len(item.get('callback_data', '').encode()), 64)
        return SimpleNamespace(message_id=len(self.sent))

    def message(self, user_id, text=None, contact=None):
        payload = {'message_id': len(self.sent) + 1, 'date': 1,
            'chat': {'id': user_id, 'type': 'private'},
            'from': {'id': user_id, 'is_bot': False, 'first_name': f'User {user_id}'}}
        if text is not None:
            payload['text'] = text
        if contact is not None:
            payload['contact'] = contact
        self.bot.process_new_messages([Message.de_json(payload)])

    def click(self, user_id, data):
        payload = {'id': 'fake-callback', 'chat_instance': 'test',
            'from': {'id': user_id, 'is_bot': False, 'first_name': f'User {user_id}'},
            'message': {'message_id': 1, 'date': 1, 'chat': {'id': user_id, 'type': 'private'}, 'text': 'buttons'},
            'data': data}
        self.bot.process_new_callback_query([CallbackQuery.de_json(payload)])

    def register(self, user_id=1):
        self.message(user_id, '/start')
        self.message(user_id, contact={'user_id': user_id, 'phone_number': f'+9891200000{user_id:02}', 'first_name': 'Test'})

    def checkout(self, user_id=1, coupon='-'):
        self.click(user_id, f'add:{self.product}')
        self.click(user_id, 'checkout')
        self.message(user_id, coupon)
        self.message(user_id, 'نام گیرنده و نشانی کامل آزمایشی در تهران')
        key = self.db.get_state(user_id)[1]['key']
        self.click(user_id, 'buy:' + key)
        return self.db.orders(user_id)[0]['id']

    def texts(self, user_id):
        return '\n'.join(text for chat, text, options in self.sent if chat == user_id)

    def test_register_only_own_contact_and_greeting(self):
        self.message(1, '/start')
        self.message(1, contact={'user_id': 2, 'phone_number': '+989121111111', 'first_name': 'Other'})
        self.assertIsNone(self.db.get_user(1).phone)
        self.assertIn('فقط شمارهٔ حساب خودت', self.texts(1))
        self.register()
        self.message(1, 'سلام')
        self.assertTrue(self.db.get_user(1).can_shop())
        self.assertIn('خوش آمدی', self.sent[-1][1])

    def test_missing_contact_owner_id_rejected(self):
        self.message(1, '/start')
        self.message(1, contact={'phone_number': '+989121111111', 'first_name': 'Unknown'})
        self.assertIsNone(self.db.get_user(1).phone)

    def test_full_customer_purchase_coupon_and_order_history(self):
        self.register()
        self.message(1, 'محصولات')
        self.click(1, 'cat:1:0')
        self.click(1, f'p:{self.product}')
        order_id = self.checkout()
        self.message(1, 'سفارش‌های من')
        self.click(1, f'order:{order_id}')
        self.assertIn('پرداخت هنگام تحویل', self.sent[-1][1])
        code = self.db.coupons(1)[0]['code']
        self.checkout(coupon=code)
        self.assertEqual(self.db.orders(1)[0]['total'], 90000)
        self.assertEqual(self.db.get_product(self.product).stock, 8)

    def test_user_cannot_call_admin_functions(self):
        self.register()
        for data in ['admin:new', f'admin:delete_yes:{self.product}', 'admin:block:99:1', 'admin:broadcast_yes:fake']:
            self.click(1, data)
        self.assertTrue(self.db.get_product(self.product).active)
        self.assertIsNone(self.db.get_state(1))
        self.assertIn('فقط برای ادمین', self.texts(1))

    def test_admin_create_and_edit_all_fields(self):
        self.message(99, '/admin')
        self.click(99, 'admin:new')
        for text in ['ماوس', 'دیجیتال', 'ماوس سیمی', '۲۵۰٬۰۰۰', '۵']:
            self.message(99, text)
        product = self.db.products()[0]
        self.assertEqual(product.name, 'ماوس')
        self.assertEqual(product.price, 250000)
        for field, text in [('name', 'ماوس جدید'), ('category', 'لوازم جانبی'),
                            ('description', 'توضیح جدید'), ('price', '300000'), ('stock', '7')]:
            self.click(99, f'admin:edit:{product.id}:{field}')
            self.message(99, text)
        product = self.db.get_product(product.id)
        self.assertEqual((product.name, product.price, product.stock), ('ماوس جدید', 300000, 7))
        self.assertEqual(product.description, 'توضیح جدید')

    def test_invalid_admin_numeric_input_keeps_state(self):
        self.message(99, '/admin')
        self.click(99, f'admin:edit:{self.product}:price')
        self.message(99, '-5')
        self.assertEqual(self.db.get_state(99)[0], 'admin_edit')
        self.assertEqual(self.db.get_product(self.product).price, 100000)
        self.message(99, '5000')
        self.assertEqual(self.db.get_product(self.product).price, 5000)

    def test_ticket_reply_block_unblock_and_order_status(self):
        self.register()
        self.message(1, 'پشتیبانی')
        self.click(1, 'newticket')
        self.message(1, 'سفارش چه زمانی می‌رسد؟')
        ticket = self.db.tickets(1)[0]['id']
        self.message(99, '/admin')
        self.click(99, f'admin:ticket:{ticket}')
        self.click(99, f'admin:reply:{ticket}')
        self.message(99, 'فردا ارسال می‌شود.')
        self.assertIn('فردا ارسال', self.texts(1))
        self.click(99, 'admin:block:1:1')
        self.click(1, f'add:{self.product}')
        self.assertEqual(self.db.cart(1), [])
        self.click(99, 'admin:block:1:0')
        order_id = self.checkout()
        self.click(99, f'admin:status:{order_id}:shipped')
        self.click(99, f'admin:status:{order_id}:delivered')
        self.assertIn('تحویل شده', self.texts(1))

    def test_restock_subscriber_receives_notification(self):
        self.register()
        self.db.edit_product(self.product, 'stock', 0)
        self.click(1, f'watch:{self.product}')
        self.message(99, '/admin')
        self.click(99, f'admin:edit:{self.product}:stock')
        self.message(99, '3')
        self.assertIn('دوباره موجود شد', self.texts(1))

    def test_broadcast_preview_confirm_and_duplicate(self):
        self.register()
        self.message(99, '/admin')
        self.click(99, 'admin:broadcast')
        self.message(99, 'فروش ویژهٔ پنجشنبه')
        self.assertNotIn('فروش ویژه', self.texts(1))
        key = self.db.get_state(99)[1]['key']
        self.click(99, 'admin:broadcast_yes:' + key)
        self.click(99, 'admin:broadcast_yes:' + key)
        self.assertEqual(self.texts(1).count('فروش ویژهٔ پنجشنبه'), 1)

    def test_customer_cannot_read_other_users_order_or_ticket(self):
        self.register(1)
        order_id = self.checkout()
        ticket_id = self.db.add_ticket(1, 'private question')
        self.register(2)
        self.click(2, f'order:{order_id}')
        self.click(2, f'ticket:{ticket_id}')
        self.assertNotIn('private question', self.texts(2))
        self.assertNotIn('نشانی کامل', self.texts(2))

    def test_checkout_double_click_does_not_duplicate(self):
        self.register()
        self.click(1, f'add:{self.product}')
        self.click(1, 'checkout')
        self.message(1, '-')
        self.message(1, 'نشانی کامل آزمایشی برای سفارش')
        key = self.db.get_state(1)[1]['key']
        self.click(1, 'buy:' + key)
        self.click(1, 'buy:' + key)
        self.assertEqual(len(self.db.orders(1)), 1)

    def test_notification_network_failure_preserves_order(self):
        self.register()
        self.click(1, f'add:{self.product}')
        quote = self.db.quote(1)
        order_id, created = self.db.checkout(1, quote, 'نشانی کامل آزمایشی', 'network')
        self.bot.send_message = Mock(side_effect=OSError('network down'))
        sent, failed = self.service.dispatch_notifications()
        self.assertEqual(sent, 0)
        self.assertEqual(self.db.order(order_id)[0]['status'], 'processing')
        self.assertEqual(self.db.outbox_counts()['pending'], 1)
        with self.db.conn:
            self.db.conn.execute('UPDATE outbox SET next_try=0')
        self.bot.send_message = self.send
        self.service.dispatch_notifications()
        self.assertEqual(self.db.outbox_counts()['sent'], 1)

    def test_telegram_rate_limit_preserves_pending_notifications(self):
        self.register()
        self.db.broadcast('اطلاعیه')
        error = ApiTelegramException('sendMessage', None,
            {'ok': False, 'error_code': 429, 'description': 'Too Many Requests', 'parameters': {'retry_after': 10}})
        self.bot.send_message = Mock(side_effect=error)
        self.service.dispatch_notifications()
        self.assertEqual(self.db.outbox_counts()['pending'], 1)
        self.assertEqual(self.db.pending_messages(), [])

    def test_delete_confirmation_and_soft_delete(self):
        self.message(99, '/admin')
        self.click(99, f'admin:delete:{self.product}')
        self.assertTrue(self.db.get_product(self.product).active)
        self.click(99, f'admin:delete_yes:{self.product}')
        self.assertFalse(self.db.get_product(self.product).active)

    def test_navigation_cancels_old_admin_input(self):
        self.message(99, '/admin')
        self.click(99, f'admin:edit:{self.product}:price')
        self.click(99, 'admin:products:0')
        self.assertIsNone(self.db.get_state(99))


if __name__ == '__main__':
    unittest.main(verbosity=2)
