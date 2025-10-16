from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField, IntegerField, FloatField, SelectField, FileField, BooleanField
from wtforms.validators import DataRequired, Length, NumberRange, Optional, EqualTo
from flask_wtf.file import FileAllowed

class LoginForm(FlaskForm):
    username = StringField('用户名', validators=[DataRequired()])
    password = PasswordField('密码', validators=[DataRequired()])
    captcha = StringField('验证码', validators=[DataRequired(), Length(5, 5, message='验证码必须为5位')])
    submit = SubmitField('登录')

class RegisterForm(FlaskForm):
    username = StringField('用户名', validators=[DataRequired(), Length(min=3)])
    password = PasswordField('密码', validators=[DataRequired(), Length(min=3)])
    captcha = StringField('验证码', validators=[DataRequired(), Length(5, 5, message='验证码必须为5位')])
    submit = SubmitField('注册')

class ProductForm(FlaskForm):
    name = StringField('商品名', validators=[DataRequired()])
    price = FloatField('单价', validators=[DataRequired(), NumberRange(min=0)])
    stock = IntegerField('库存', validators=[DataRequired(), NumberRange(min=0)])
    category = SelectField('分类', coerce=int)
    image_link = StringField('图片链接', validators=[Optional()])
    image = FileField('或上传图片', validators=[Optional(), FileAllowed(['jpg', 'png', 'jpeg', 'webp'])])
    submit = SubmitField('保存')

class ManualProductForm(FlaskForm):
    name = StringField('商品名', validators=[DataRequired()])
    price = FloatField('单价', validators=[DataRequired(), NumberRange(min=0)])
    stock = IntegerField('库存', validators=[DataRequired(), NumberRange(min=0)])
    category = SelectField('分类', coerce=int)
    image_link = StringField('图片链接', validators=[Optional()])
    image = FileField('或上传图片', validators=[Optional(), FileAllowed(['jpg', 'png', 'jpeg', 'webp'])])
    submit = SubmitField('添加商品')

class ProductImportForm(FlaskForm):
    file = FileField('CSV文件', validators=[DataRequired(), FileAllowed(['csv'])])
    submit = SubmitField('批量导入')

class SaleOneForm(FlaskForm):
    quantity = IntegerField('数量', validators=[DataRequired(), NumberRange(min=1)])
    submit_in = SubmitField('进货')
    submit_out = SubmitField('销售')

class SaleForm(FlaskForm):
    product = SelectField('商品', coerce=int)
    quantity = IntegerField('数量', validators=[DataRequired(), NumberRange(min=1)])
    type = SelectField('类型', choices=[('in', '进货'), ('out', '销售')])
    submit = SubmitField('提交')

class CategoryForm(FlaskForm):
    name = StringField('分类名', validators=[DataRequired()])
    submit = SubmitField('添加')

class UserApproveForm(FlaskForm):
    username = StringField('用户名', validators=[DataRequired()])
    is_active = BooleanField('激活账户')
    is_admin = BooleanField('管理员权限')
    
    # 修改密码字段为可选，只有在填写时才验证
    old_password = PasswordField('当前密码', validators=[Optional()])
    new_password = PasswordField('新密码', validators=[
        Optional(), 
        Length(min=6, message='密码长度至少为6位')
    ])
    confirm_password = PasswordField('确认新密码', validators=[
        Optional(),
        EqualTo('new_password', message='两次输入的密码不一致')
    ])
    
    submit = SubmitField('保存')

class CategoryEditForm(FlaskForm):
    name = StringField('分类名', validators=[DataRequired()])
    submit = SubmitField('保存')