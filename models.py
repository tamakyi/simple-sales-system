from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime

db = SQLAlchemy()

# 商品的默认库存预警阈值。商品未单独设置 low_stock_threshold 时用这个值。
DEFAULT_LOW_STOCK_THRESHOLD = 5

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True)
    password = db.Column(db.String(255))
    email = db.Column(db.String(120), unique=True, nullable=True)
    is_admin = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=False)
    reset_token = db.Column(db.String(255), nullable=True)
    reset_token_expiry = db.Column(db.DateTime, nullable=True)
    # 软删除标记：置 True 后不再出现在用户列表中，且无法登录。
    # 保留数据行是为了维持 Sale.user_id / Log.user_id 的外键完整性。
    is_deleted = db.Column(db.Boolean, default=False, nullable=False)
    deleted_at = db.Column(db.DateTime, nullable=True)

class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), unique=True)

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    # 建索引是因为 CSV 导入按名称逐行查重，否则每次导入都是全表扫描
    name = db.Column(db.String(128), index=True)
    stock = db.Column(db.Integer, default=0)
    price = db.Column(db.Numeric(10, 2))
    image = db.Column(db.String(1024))
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'))
    category = db.relationship('Category')
    # 软删除标记：置 True 后不再出现在商品/销售列表与选择器中。
    # 保留数据行是为了让历史销售流水与导出报表继续可读，不篡改财务历史。
    is_deleted = db.Column(db.Boolean, default=False, nullable=False)
    deleted_at = db.Column(db.DateTime, nullable=True)
    # 留空（NULL）表示沿用 DEFAULT_LOW_STOCK_THRESHOLD。
    # 之所以允许 NULL 而不是统一写死默认值：需要区分「从未设置」与「有意设成 5」，
    # 否则以后调整默认阈值时，改不动任何既有商品。
    low_stock_threshold = db.Column(db.Integer, nullable=True)

    @property
    def effective_threshold(self):
        return (DEFAULT_LOW_STOCK_THRESHOLD if self.low_stock_threshold is None
                else self.low_stock_threshold)

    @property
    def effective_stock(self):
        # stock 列在历史库中可能为 NULL，直接参与比较会抛 TypeError
        return 0 if self.stock is None else self.stock

    @property
    def stock_level(self):
        """库存状态。阈值判定只在此处实现——视图与模板一律读这个属性。

        此前销售页硬编码了 10，其余页面完全不判定，同一个「库存是否够」有三套口径。
        """
        stock = self.effective_stock
        if stock < 0:
            return 'negative'
        if stock == 0:
            return 'out'
        if stock <= self.effective_threshold:
            return 'low'
        return 'ok'

class Sale(db.Model):
    __table_args__ = (
        # dashboard 的统计与流水都按「类型 + 是否撤回 + 日期范围」过滤
        db.Index('ix_sale_type_created', 'type', 'is_reversed', 'created_at'),
        db.CheckConstraint("type IN ('in', 'out')", name='ck_sale_type'),
    )
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), index=True)
    quantity = db.Column(db.Integer)
    type = db.Column(db.String(10))  # in/out
    amount = db.Column(db.Numeric(10, 2))
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at = db.Column(db.DateTime, default=datetime.now, index=True)
    product = db.relationship('Product')
    user = db.relationship('User')
    is_reversed = db.Column(db.Boolean, default=False)

class Log(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    action = db.Column(db.String(255))
    ts = db.Column(db.DateTime, default=datetime.now, index=True)
    user = db.relationship('User')
    sale_id = db.Column(db.Integer, db.ForeignKey('sale.id'), nullable=True)
    sale = db.relationship('Sale')
