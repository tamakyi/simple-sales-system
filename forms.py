from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField, IntegerField, DecimalField, SelectField, FileField, BooleanField
from wtforms.validators import DataRequired, InputRequired, Length, NumberRange, Optional, EqualTo, Email
from flask_wtf.file import FileAllowed

class LoginForm(FlaskForm):
    username = StringField('用户名', validators=[DataRequired()])
    password = PasswordField('密码', validators=[DataRequired()])
    captcha = StringField('验证码', validators=[DataRequired(), Length(5, 5, message='验证码必须为5位')])
    submit = SubmitField('登录')

class RegisterForm(FlaskForm):
    username = StringField('用户名', validators=[DataRequired(), Length(min=3)])
    password = PasswordField('密码', validators=[DataRequired(), Length(min=6, message='密码长度至少为6位')])
    email = StringField('邮箱', validators=[DataRequired(), Email()])
    captcha = StringField('验证码', validators=[DataRequired(), Length(5, 5, message='验证码必须为5位')])
    submit = SubmitField('注册')

class ForgotPasswordForm(FlaskForm):
    email = StringField('注册邮箱', validators=[DataRequired(), Email()])
    captcha = StringField('验证码', validators=[DataRequired(), Length(5, 5, message='验证码必须为5位')])
    submit = SubmitField('发送重置邮件')

class ResetPasswordForm(FlaskForm):
    password = PasswordField('新密码', validators=[DataRequired(), Length(min=6)])
    confirm_password = PasswordField('确认新密码', validators=[
        DataRequired(),
        EqualTo('password', message='两次输入的密码不一致')
    ])
    submit = SubmitField('重置密码')

class ProductForm(FlaskForm):
    name = StringField('商品名', validators=[DataRequired()])
    # 数值字段用 InputRequired：DataRequired 会把 0 当作「未填写」而拒绝整个提交，
    # 于是库存为 0 的商品连改价都改不了。下界交给 NumberRange。
    price = DecimalField('单价', places=2, validators=[InputRequired(), NumberRange(min=0)])
    stock = IntegerField('库存', validators=[InputRequired(), NumberRange(min=0)])
    low_stock_threshold = IntegerField('库存预警阈值', validators=[Optional(), NumberRange(min=0)])
    category = SelectField('分类', coerce=int)
    image_link = StringField('图片链接', validators=[Optional()])
    image = FileField('或上传图片', validators=[Optional(), FileAllowed(['jpg', 'png', 'jpeg', 'webp'])])
    submit = SubmitField('保存')

class ManualProductForm(FlaskForm):
    name = StringField('商品名', validators=[DataRequired()])
    price = DecimalField('单价', places=2, validators=[InputRequired(), NumberRange(min=0)])
    stock = IntegerField('库存', validators=[InputRequired(), NumberRange(min=0)])
    low_stock_threshold = IntegerField('库存预警阈值', validators=[Optional(), NumberRange(min=0)])
    category = SelectField('分类', coerce=int)
    image_link = StringField('图片链接', validators=[Optional()])
    image = FileField('或上传图片', validators=[Optional(), FileAllowed(['jpg', 'png', 'jpeg', 'webp'])])
    submit = SubmitField('添加商品')

class ProductImportForm(FlaskForm):
    file = FileField('CSV文件', validators=[DataRequired(), FileAllowed(['csv'])])
    submit = SubmitField('批量导入')

class CategoryForm(FlaskForm):
    name = StringField('分类名', validators=[DataRequired()])
    submit = SubmitField('添加')

class UserApproveForm(FlaskForm):
    username = StringField('用户名', validators=[DataRequired()])
    email = StringField('邮箱', validators=[Optional(), Email()])
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
