"""Simple classes, like the User/Product classes in the classroom projects."""


class ShopError(Exception):
    """An expected error whose message can be shown to a customer."""


class User:
    def __init__(self, row):
        self.id = row['id']
        self.name = row['name']
        self.username = row['username']
        self.phone = row['phone']
        self.blocked = bool(row['blocked'])
        self.logged_in = bool(row['logged_in'])

    def can_shop(self):
        return bool(self.phone) and self.logged_in and not self.blocked


class Product:
    def __init__(self, row):
        self.id = row['id']
        self.name = row['name']
        self.description = row['description']
        self.category_id = row['category_id']
        self.price = row['price']
        self.stock = row['stock']
        self.active = bool(row['active'])

    def get_price(self):
        return self.price


STATUS_LABELS = {
    'processing': 'در حال پردازش',
    'shipped': 'ارسال شده',
    'delivered': 'تحویل شده',
}


def normalize_text(value):
    return ' '.join(str(value).replace('ي', 'ی').replace('ك', 'ک').split())


def clean_text(value, title, minimum=1, maximum=100):
    value = normalize_text(value)
    if not minimum <= len(value) <= maximum:
        raise ShopError(f'{title} باید بین {minimum} و {maximum} نویسه باشد.')
    return value


def digits(value):
    return str(value).translate(str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789'))


def integer(value, title, minimum=0, maximum=1000000):
    value = digits(value).replace(',', '').replace('٬', '').strip()
    if not value.isascii() or not value.isdigit():
        raise ShopError(f'{title} باید عدد صحیح باشد.')
    number = int(value)
    if not minimum <= number <= maximum:
        raise ShopError(f'{title} باید بین {minimum:,} و {maximum:,} باشد.')
    return number


def phone_number(value):
    value = digits(value).replace(' ', '').replace('-', '').lstrip('+')
    if not value.isascii() or not value.isdigit() or not 7 <= len(value) <= 15:
        raise ShopError('شمارهٔ ارسال‌شده معتبر نیست؛ از دکمهٔ اشتراک شماره استفاده کن.')
    return '+' + value


def money(value):
    return f'{value:,} تومان'
