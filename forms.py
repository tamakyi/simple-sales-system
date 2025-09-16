from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField, IntegerField, FloatField, SelectField, FileField, BooleanField
from wtforms.validators import DataRequired, Length, NumberRange, Optional
from flask_wtf.file import FileAllowed

class LoginForm(FlaskForm):
    username = StringField('用户名', validators=[DataRequired()])
    password = PasswordField('密码', validators=[DataRequired()])
    submit = SubmitField('登录')

class RegisterForm(FlaskForm):
    username = StringField('用户名', validators=[DataRequired(), Length(min=3)])
    password = PasswordField('密码', validators=[DataRequired(), Length(min=3)])
    submit = SubmitField('注册')

class ProductForm(FlaskForm):
    name = StringField('商品名', validators=[DataRequired()])
    price = FloatField('单价', validators=[DataRequired(), NumberRange(min=0)])
    stock = IntegerField('库存', validators=[DataRequired(), NumberRange(min=0)])
    category = SelectField('分类', coerce=int)
    image_link = StringField('图片链接', validators=[Optional()])
    image = FileField('或上传图片', validators=[Optional(), FileAllowed(['jpg', 'png', 'jpeg'])])
    submit = SubmitField('保存')

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
    is_active = BooleanField('激活')
    is_admin = BooleanField('管理员')
    submit = SubmitField('保存')