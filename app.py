from flask import Flask, render_template, redirect, url_for, request, flash, send_file, abort
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from forms import *
from models import db, User, Category, Product, Sale, Log
from utils import import_products_csv
import os
import datetime
import openpyxl
from io import BytesIO
from sqlalchemy import func, desc

UPLOAD_FOLDER = 'static/uploads'
PER_PAGE = 10  # 商品每页数量

app = Flask(__name__)
app.config['SECRET_KEY'] = 'sales-secret'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///sales.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024  # 2MB

db.init_app(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

def log_action(user, action):
    db.session.add(Log(user_id=user.id, action=action))
    db.session.commit()

@login_manager.user_loader
def load_user(user_id):
#    return User.query.get(int(user_id))
    return db.session.get(User, int(user_id))

def today_range():
    today = datetime.date.today()
    start = datetime.datetime.combine(today, datetime.time.min)
    end = datetime.datetime.combine(today, datetime.time.max)
    return start, end

def check_admin():
    if not (current_user.is_admin and current_user.is_active):
        abort(403)

@app.route('/')
@login_required
def dashboard():
    # 销售排行榜
    sale_ranks = db.session.query(
        Product.name,
        func.sum(Sale.amount).label("total_amount"),
        func.sum(Sale.quantity).label("total_qty")
    ).join(Sale.product).filter(Sale.type=='out').group_by(Product.id).order_by(desc("total_amount")).limit(5).all()

    # 分类销售额
    cat_sales = db.session.query(
        Category.name, func.sum(Sale.amount)
    ).join(Product, Product.category_id == Category.id).join(Sale, Sale.product_id == Product.id)\
     .filter(Sale.type=='out').group_by(Category.id).all()
    cat_names = [c[0] for c in cat_sales]
    cat_amounts = [float(c[1] or 0) for c in cat_sales]

    t_start, t_end = today_range()
    today_total = db.session.query(func.sum(Sale.amount)).filter(Sale.type=='out', Sale.created_at >= t_start, Sale.created_at <= t_end).scalar() or 0
    yest = datetime.date.today() - datetime.timedelta(days=1)
    y_start = datetime.datetime.combine(yest, datetime.time.min)
    y_end = datetime.datetime.combine(yest, datetime.time.max)
    yesterday_total = db.session.query(func.sum(Sale.amount)).filter(Sale.type=='out', Sale.created_at >= y_start, Sale.created_at <= y_end).scalar() or 0
    all_total = db.session.query(func.sum(Sale.amount)).filter(Sale.type=='out').scalar() or 0
    sales = Sale.query.filter(Sale.type=='out').order_by(Sale.created_at.desc()).limit(20).all()
    return render_template('dashboard.html',
                           sale_ranks=sale_ranks, cat_names=cat_names, cat_amounts=cat_amounts,
                           today_total=today_total, yesterday_total=yesterday_total, all_total=all_total,
                           sales=sales)

@app.route('/login', methods=['GET', 'POST'])
def login():
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        if user and check_password_hash(user.password, form.password.data):
            if not user.is_active:
                flash('账户未激活，请联系管理员')
                return render_template('login.html', form=form)
            login_user(user)
            return redirect(url_for('dashboard'))
        flash('用户名或密码错误')
    return render_template('login.html', form=form)

@app.route('/register', methods=['GET', 'POST'])
def register():
    form = RegisterForm()
    if form.validate_on_submit():
        if User.query.filter_by(username=form.username.data).first():
            flash('用户名已存在')
        else:
            user = User(username=form.username.data, password=generate_password_hash(form.password.data))
            db.session.add(user)
            db.session.commit()
            flash('注册成功，请等待管理员审核')
            return redirect(url_for('login'))
    return render_template('register.html', form=form)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/products')
@login_required
def products():
    page = request.args.get('page', 1, type=int)
    keyword = request.args.get('keyword', '')
    query = Product.query
    if keyword:
        query = query.filter(Product.name.like(f'%{keyword}%'))
    products = query.order_by(Product.id.desc()).paginate(page=page, per_page=PER_PAGE)
    return render_template('products.html', products=products)

@app.route('/products/edit/<int:pid>', methods=['GET', 'POST'])
@login_required
def product_edit(pid):
    check_admin()
    prod = Product.query.get_or_404(pid)
    form = ProductForm(obj=prod)
    categories = Category.query.all()
    form.category.choices = [(c.id, c.name) for c in categories]
    if request.method == "GET":
        form.category.data = prod.category_id
    if form.validate_on_submit():
        prod.name = form.name.data
        prod.price = form.price.data
        prod.stock = form.stock.data
        prod.category_id = form.category.data
        img_path = form.image_link.data.strip() if form.image_link.data else ''
        img = form.image.data
        if not img_path and img:
            filename = secure_filename(img.filename)
            filename = datetime.datetime.now().strftime('%Y%m%d%H%M%S_') + filename
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            img.save(file_path)
            img_path = 'uploads/' + filename
        if img_path:
            prod.image = img_path
        db.session.commit()
        log_action(current_user, f"编辑商品:{prod.name}")
        flash('保存成功')
        return redirect(url_for('products'))
    return render_template('product_edit.html', form=form, product=prod)

@app.route('/products/delete/<int:pid>')
@login_required
def delete_product(pid):
    check_admin()
    prod = Product.query.get_or_404(pid)
    db.session.delete(prod)
    db.session.commit()
    log_action(current_user, f"删除商品:{prod.name}")
    flash('商品已删除')
    return redirect(url_for('products'))

@app.route('/products/import', methods=['GET', 'POST'])
@login_required
def product_import():
    check_admin()
    form = ProductImportForm()
    if form.validate_on_submit():
        file = form.file.data
        try:
            import_products_csv(file)
            log_action(current_user, "批量导入商品")
            flash('导入成功')
        except Exception as e:
            flash('导入失败: ' + str(e))
        return redirect(url_for('products'))
    return render_template('product_import.html', form=form)

@app.route('/categories', methods=['GET', 'POST'])
@login_required
def categories():
    check_admin()
    form = CategoryForm()
    if form.validate_on_submit():
        if Category.query.filter_by(name=form.name.data).first():
            flash('分类已存在')
        else:
            db.session.add(Category(name=form.name.data))
            db.session.commit()
            log_action(current_user, f"添加分类:{form.name.data}")
            flash('添加成功')
        return redirect(url_for('categories'))
    cats = Category.query.all()
    return render_template('categories.html', form=form, categories=cats)

@app.route('/categories/delete/<int:cat_id>')
@login_required
def delete_category(cat_id):
    check_admin()
    cat = Category.query.get(cat_id)
    if cat:
        db.session.delete(cat)
        db.session.commit()
        log_action(current_user, f"删除分类:{cat.name}")
        flash('已删除')
    return redirect(url_for('categories'))

@app.route('/sales', methods=['GET', 'POST'])
@login_required
def sales():
    page = request.args.get('page', 1, type=int)
    cats = Category.query.all()
    query = Product.query
    products = query.order_by(Product.id.desc()).paginate(page=page, per_page=PER_PAGE)
    # 提供每种商品的历史销售、今日销售额、库存等
    t_start, t_end = today_range()
    stat_map = {}
    for p in products.items:
        all_sale = db.session.query(func.sum(Sale.amount)).filter(Sale.product_id==p.id, Sale.type=='out').scalar() or 0
        today_sale = db.session.query(func.sum(Sale.amount)).filter(Sale.product_id==p.id, Sale.type=='out', Sale.created_at >= t_start, Sale.created_at <= t_end).scalar() or 0
        all_qty = db.session.query(func.sum(Sale.quantity)).filter(Sale.product_id==p.id, Sale.type=='out').scalar() or 0
        stat_map[p.id] = dict(all_sale=all_sale, today_sale=today_sale, all_qty=all_qty)
    # 行内表单直接用 request.form，不用 FlaskForm
    return render_template('sales.html', products=products, cats=cats, stat_map=stat_map)

@app.route('/sales/operate/<int:pid>', methods=['POST'])
@login_required
def sales_operate(pid):
    prod = Product.query.get_or_404(pid)
    try:
        qty = int(request.form.get("quantity"))
    except Exception:
        flash("数量不合法")
        return redirect(url_for('sales'))
    if "submit_in" in request.form:
        prod.stock += qty
        sale = Sale(product_id=prod.id, quantity=qty, type='in', user_id=current_user.id, amount=0)
        db.session.add(sale)
        db.session.commit()
        log_action(current_user, f"进货:{prod.name} 数量:{qty}")
        flash('进货成功')
    elif "submit_out" in request.form:
        if prod.stock < qty:
            flash('库存不足')
            return redirect(url_for('sales'))
        prod.stock -= qty
        amount = qty * prod.price
        sale = Sale(product_id=prod.id, quantity=qty, type='out', user_id=current_user.id, amount=amount)
        db.session.add(sale)
        db.session.commit()
        log_action(current_user, f"销售:{prod.name} 数量:{qty}")
        flash('销售成功')
    else:
        flash('未知操作')
    return redirect(url_for('sales'))

@app.route('/sales/detail/<int:pid>')
@login_required
def sales_detail(pid):
    prod = Product.query.get_or_404(pid)
    sales = Sale.query.filter_by(product_id=pid, type='out').order_by(Sale.created_at.desc()).all()
    return render_template('sales_detail.html', prod=prod, sales=sales)

@app.route('/export')
@login_required
def export():
    sales = Sale.query.all()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(['商品', '数量', '类型', '金额', '操作人', '时间'])
    for s in sales:
        ws.append([s.product.name, s.quantity, '进货' if s.type == 'in' else '销售', s.amount, s.user.username, s.created_at.strftime('%Y-%m-%d %H:%M')])
    bio = BytesIO()
    wb.save(bio)
    bio.seek(0)
    return send_file(bio, as_attachment=True, download_name='sales.xlsx', mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@app.route('/users')
@login_required
def users():
    check_admin()
    users = User.query.all()
    return render_template('users.html', users=users)

@app.route('/users/approve/<int:uid>', methods=['GET', 'POST'])
@login_required
def user_approve(uid):
    check_admin()
    user = User.query.get_or_404(uid)
    form = UserApproveForm(obj=user)
    
    if form.validate_on_submit():
        # 处理密码修改
        if form.old_password.data:
            if not check_password_hash(user.password, form.old_password.data):
                flash('当前密码错误', 'error')
                return render_template('user_approve.html', form=form, user=user)
            
            # 更新密码
            user.password = generate_password_hash(form.new_password.data)
            db.session.commit()
            flash('密码已成功更新', 'success')
        
        # 保留原有功能
        user.is_active = form.is_active.data
        user.is_admin = form.is_admin.data
        db.session.commit()
        flash('用户信息已保存', 'success')
        return redirect(url_for('users'))
    
    return render_template('user_approve.html', form=form, user=user)

@app.route('/logs')
@login_required
def logs():
    check_admin()
    logs = Log.query.order_by(Log.ts.desc()).limit(100).all()
    return render_template('logs.html', logs=logs)

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        if not User.query.filter_by(username='admin').first():
            admin = User(username='admin', password=generate_password_hash('admin'), is_admin=True, is_active=True)
            db.session.add(admin)
            db.session.commit()
        if not Category.query.first():
            db.session.add(Category(name="未分类"))
            db.session.commit()
    app.run(debug=True)
