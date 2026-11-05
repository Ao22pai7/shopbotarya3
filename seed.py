"""Optional classroom examples. Run once to populate an empty shop."""
import os
from dotenv import load_dotenv
from database import Database


def main():
    folder = os.path.dirname(os.path.abspath(__file__))
    load_dotenv(os.path.join(folder, '.env'))
    filename = os.environ.get('DATABASE_PATH', 'shop.db').strip() or 'shop.db'
    if not os.path.isabs(filename):
        filename = os.path.join(folder, filename)
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    db = Database(filename)
    try:
        if db.conn.execute('SELECT COUNT(*) FROM products').fetchone()[0]:
            print('Products already exist. No demo products were added.')
            return
        samples = [
            ('کتاب پایتون', 'کتاب', 'کالای نمونه برای تمرین کلاس؛ قیمت واقعی بازار نیست.', 250000, 10),
            ('کتاب الگوریتم', 'کتاب', 'نمونهٔ آموزشی برای تمرین جست‌وجو و سبد خرید.', 300000, 5),
            ('ماوس سیمی', 'دیجیتال', 'نمونهٔ آموزشی موجود.', 180000, 8),
            ('هدفون', 'دیجیتال', 'نمونهٔ ناموجود؛ با این کالا اعلان موجودی را امتحان کن.', 450000, 0),
            ('دفتر یادداشت', 'لوازم تحریر', 'نمونهٔ آموزشی؛ خرید چندتایی را امتحان کن.', 75000, 20),
            ('خودکار', 'لوازم تحریر', 'نمونهٔ آموزشی برای تمرین تخفیف.', 15000, 30),
        ]
        for item in samples:
            db.add_product(*item)
        print('Added 6 clearly labelled classroom demo products.')
    finally:
        db.close()


if __name__ == '__main__':
    main()
