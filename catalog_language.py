"""Validated English catalog text and offline translations for store terms."""
import html
import re

ARABIC = re.compile(r'[\u0621-\u064a\u066e-\u06d3]')
TERMS = {
    'جميع مايخص': 'Everything related to',
    'جميع ما يخص': 'Everything related to',
    'جميع المنتجات بضمان': 'All products include a warranty',
    'جميع المنتجات لمدة شهر': 'All products last one month',
    'ماعدا المنتج الثالث': 'Except the third product',
    'ما عدا المنتج الثالث': 'Except the third product',
    'المدة': 'Duration:',
    'ع ايميلك': 'on your email',
    'ع إيميلك': 'on your email',
    'على ايميلك': 'On your email',
    'على إيميلك': 'On your email',
    'مشترك،مع عدد قليل': 'Shared with a small group',
    'مشترك,مع عدد قليل': 'Shared with a small group',
    'مشترك مع عدد قليل': 'Shared with a small group',
    'بيانات جاهزة': 'Ready account',
    'بيانات جاهزه': 'Ready account',
    'شات جي بي تي': 'ChatGPT',
    'ملف خاص': 'Private profile',
    'حساب عائلي كامل وخاص': 'Private full family account',
    'حساب كامل': 'Full account',
    'دعوة عائلة': 'Family invite',
    'دعوة عائله': 'Family invite',
    'عروض تيليجرام': 'Telegram offers',
    'الحسومات والعروض': 'Discounts and offers',
    'بضمان كامل': 'With full warranty',
    'مدة الضمان': 'Warranty period',
    'الضمان': 'Warranty',
    'مدة الاشتراك': 'Subscription duration',
    'اشتراك': 'Subscription',
    'مشترك': 'Shared',
    'خاص': 'Private',
    'تجديد': 'Renewal',
    'شاهد': 'Shahid',
    'شهور': 'months',
    'أشهر': 'months',
    'اشهر': 'months',
    'شهر': 'month',
    'أيام': 'days',
    'ايام': 'days',
    'يومان': 'two days',
    'يوم': 'day',
    'سنة': 'year',
    'بلس': 'Plus',
    'برو': 'Pro',
}

def english(text):
    # Ignore HTML attributes, including links, when checking visible language.
    return not ARABIC.search(html.unescape(re.sub(r'<[^>]+>', '', str(text or ''))))

def offline(text, saved=None):
    text = str(text or '')
    if english(text):
        return text
    reverse = {v: k for k, v in (saved or {}).items()}
    if text in reverse:
        return reverse[text]
    def segment(value):
        if not ARABIC.search(value):
            return value
        result = value.translate(str.maketrans('٠١٢٣٤٥٦٧٨٩', '0123456789'))
        for source in sorted(TERMS, key=len, reverse=True):
            result = result.replace(source, TERMS[source])
        return result
    rendered = ''.join(p if p.startswith('<') else segment(p) for p in re.split(r'(<[^>]+>)', text))
    return rendered if english(rendered) else None

def fallback(field):
    return 'Product' if field in ('name', 'category_name') else 'Contact support for the full product details and terms in English.'
