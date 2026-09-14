import io
from urllib.parse import urlparse

import pandas as pd

from models import db, Category, Product

REQUIRED_COLUMNS = ('商品名', '单价', '库存', '分类')
# 官方模板的表头是「图片链接(可选)」，精确匹配永远命中不了，所以要按前缀找。
IMAGE_COLUMN_PREFIX = '图片链接'
MAX_REPORTED_ERRORS = 20
# 中文 Excel 把 CSV 默认存成 ANSI/GBK 且不带 BOM，只认 UTF-8 会让
# 「下载模板 → 填写 → 上传」这条主流程直接失败。utf-8 严格模式必须排在前面，
# 否则 gb18030 会先一步"成功"解码出乱码。
CSV_ENCODINGS = ('utf-8-sig', 'gb18030')
ALLOWED_IMAGE_SCHEMES = ('http', 'https')

def sanitize_image_link(value):
    """校验图片链接，非法返回 None、未填写返回空串。

    该值会被渲染进 <img src>，放行 javascript: / data: 等伪协议等于开放注入。
    """
    value = (value or '').strip()
    if not value:
        return ''
    parsed = urlparse(value)
    if parsed.scheme.lower() not in ALLOWED_IMAGE_SCHEMES or not parsed.netloc:
        return None
    return value

class ImportResult:
    """CSV 导入结果。

    原实现只返回 True/False，于是「所有行都被跳过」也会提示导入成功；
    逐行失败原因只打到 stdout，前端完全看不到。
    """

    def __init__(self):
        self.created = 0
        self.updated = 0
        self.restored = 0
        self.skipped = 0
        self.errors = []
        self.note = ''

    @property
    def imported(self):
        return self.created + self.updated + self.restored

    def summary(self):
        parts = [f'新增 {self.created}', f'更新 {self.updated}']
        if self.restored:
            parts.append(f'恢复 {self.restored}')
        if self.skipped:
            parts.append(f'跳过 {self.skipped}')
        return '，'.join(parts)

def _cell(row, key):
    """取单元格文本；缺失值（NaN）与纯空格都归一成空字符串。"""
    value = row.get(key)
    if value is None or pd.isna(value):
        return ''
    return str(value).strip()

def _read_csv(file):
    """按 CSV_ENCODINGS 依次尝试解码，返回 (DataFrame, 实际编码)。

    先整块读字节再解码：pandas 直接吃文件对象时，第一次尝试失败后流位置不会复位。
    gb18030 几乎能解码任意字节串，所以回退成功也不能直接信——调用方必须再校验
    必填列，否则损坏文件会变成一堆乱码商品入库，而不是给出清晰报错。
    """
    raw = file.read()
    if isinstance(raw, str):
        raw = raw.encode('utf-8')

    candidates = CSV_ENCODINGS
    if raw[:2] in (b'\xff\xfe', b'\xfe\xff'):
        # UTF-16 只在有 BOM 时尝试：无 BOM 的 utf-16 能把任意偶数长度字节串解成乱码
        candidates = ('utf-16',) + CSV_ENCODINGS

    for encoding in candidates:
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        df = pd.read_csv(io.StringIO(text), dtype=str)
        df.columns = [str(col).strip() for col in df.columns]
        return df, encoding

    return None, None

def import_products_csv(file):
    result = ImportResult()

    df, encoding = _read_csv(file)
    if df is None:
        result.errors.append(
            '无法解析 CSV 文件：文件编码无法识别，请在 Excel 里另存为'
            '「CSV UTF-8 (逗号分隔)」后重试')
        return result

    missing = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        # 回退解码出的列名对不上，八成是编码猜错了，别让用户去翻表头
        if encoding != CSV_ENCODINGS[0]:
            result.errors.append(
                f'按 {encoding} 解码后仍找不到必填列（{"、".join(missing)}），'
                '文件编码可能不对：请另存为「CSV UTF-8 (逗号分隔)」后重试')
        else:
            result.errors.append('缺少必填列：' + '、'.join(missing))
        return result

    if encoding != CSV_ENCODINGS[0]:
        result.note = f'文件按 {encoding} 解码，建议另存为「CSV UTF-8 (逗号分隔)」以免乱码'

    image_column = next((col for col in df.columns
                         if col.startswith(IMAGE_COLUMN_PREFIX)), None)

    for idx, row in df.iterrows():
        row_num = idx + 2  # 表头占第 1 行，行号对齐用户在 Excel 里看到的位置

        name = _cell(row, '商品名')
        price_str = _cell(row, '单价')
        stock_str = _cell(row, '库存')
        category_name = _cell(row, '分类')

        problem = None
        if not name:
            problem = '商品名为空'
        elif not price_str:
            problem = '单价为空'
        elif not stock_str:
            problem = '库存为空'
        if problem:
            result.skipped += 1
            if len(result.errors) < MAX_REPORTED_ERRORS:
                result.errors.append(f'第 {row_num} 行：{problem}')
            continue

        if not category_name:
            category_name = '未分类'

        try:
            price = float(price_str)
            stock = int(float(stock_str))
        except ValueError:
            result.skipped += 1
            if len(result.errors) < MAX_REPORTED_ERRORS:
                result.errors.append(
                    f'第 {row_num} 行：单价或库存格式错误（单价 {price_str}，库存 {stock_str}）')
            continue

        # 负库存在手工表单里已被 NumberRange 拦住，CSV 这条路径原先没有校验，
        # 会把 -3 原样写库并提示「导入完成」。与单价格式错误同类处理：不作废整份文件，只跳过该行。
        if price < 0 or stock < 0:
            result.skipped += 1
            if len(result.errors) < MAX_REPORTED_ERRORS:
                result.errors.append(
                    f'第 {row_num} 行：单价与库存不能为负数（单价 {price_str}，库存 {stock_str}）')
            continue

        # 图片链接非必填：填错不该让整行作废，忽略图片并单独提示
        image_link = _cell(row, image_column) if image_column else ''
        if image_link:
            checked = sanitize_image_link(image_link)
            if checked is None:
                if len(result.errors) < MAX_REPORTED_ERRORS:
                    result.errors.append(
                        f'第 {row_num} 行：图片链接必须以 http:// 或 https:// 开头，已忽略该图片')
                image_link = ''
            else:
                image_link = checked

        category = Category.query.filter_by(name=category_name).first()
        if not category:
            category = Category(name=category_name)
            db.session.add(category)
            db.session.flush()

        # 只匹配未删除的同名商品；同名商品若已被软删除则恢复它，
        # 避免出现「提示导入成功、列表里却看不到」的静默陷阱。
        existing = Product.query.filter_by(name=name, is_deleted=False).first()
        if existing:
            existing.price = price
            existing.stock = stock
            existing.category_id = category.id
            if image_link:
                existing.image = image_link
            result.updated += 1
            continue

        recycled = Product.query.filter_by(name=name, is_deleted=True).first()
        if recycled:
            recycled.is_deleted = False
            recycled.deleted_at = None
            recycled.price = price
            recycled.stock = stock
            recycled.category_id = category.id
            if image_link:
                recycled.image = image_link
            result.restored += 1
            continue

        db.session.add(Product(
            name=name, price=price, stock=stock,
            category_id=category.id,
            image=image_link or None,
        ))
        result.created += 1

    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        result.created = result.updated = result.restored = result.skipped = 0
        result.errors.append(f'写入数据库失败：{exc}')

    return result
