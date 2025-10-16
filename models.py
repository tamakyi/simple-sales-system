from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime

db = SQLAlchemy()

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True)
    password = db.Column(db.String(255))
    email = db.Column(db.String(120), unique=True, nullable=True)
    is_admin = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=False)
    reset_token = db.Column(db.String(255), nullable=True)
    reset_token_expiry = db.Column(db.DateTime, nullable=True)

class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), unique=True)

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128))
    stock = db.Column(db.Integer, default=0)
    price = db.Column(db.Numeric(10, 2))
    image = db.Column(db.String(1024))
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'))
    category = db.relationship('Category')

class Sale(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'))
    quantity = db.Column(db.Integer)
    type = db.Column(db.String(10))  # in/out
    amount = db.Column(db.Numeric(10, 2))
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at = db.Column(db.DateTime, default=datetime.now)
    product = db.relationship('Product')
    user = db.relationship('User')
    is_reversed = db.Column(db.Boolean, default=False)

class Log(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    action = db.Column(db.String(255))
    ts = db.Column(db.DateTime, default=datetime.now)
    user = db.relationship('User')
    sale_id = db.Column(db.Integer, db.ForeignKey('sale.id'), nullable=True)
    sale = db.relationship('Sale')