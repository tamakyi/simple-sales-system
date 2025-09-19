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
from dotenv import load_dotenv
from flask import make_response
import csv
from io import StringIO
from flask_wtf.csrf import CSRFProtect, generate_csrf
from urllib.parse import urlparse, parse_qs

basedir = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(basedir, '.env'))

app = Flask(__name__)
csrf = CSRFProtect(app)

app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'fallback_secret_key')
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('SQLALCHEMY_DATABASE_URI', 'sqlite:///default.db')
app.config['UPLOAD_FOLDER'] = os.getenv('UPLOAD_FOLDER', 'static/uploads')
app.config['BACKGROUND_IMAGE_URL'] = os.getenv('BACKGROUND_IMAGE_URL', '')
app.config['BACKGROUND_OPACITY'] = float(os.getenv('BACKGROUND_OPACITY', 0.1))
app.config['BACKGROUND_SIZE'] = os.getenv('BACKGROUND_SIZE', 'cover')
app.config['MAX_CONTENT_LENGTH'] = int(os.getenv('MAX_CONTENT_LENGTH', 2097152))
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['PER_PAGE'] = int(os.getenv('PER_PAGE', 10))
app.config['DASHBOARD_ANNOUNCEMENT'] = os.getenv('DASHBOARD_ANNOUNCEMENT', '')
app.config['ANNOUNCEMENT_ENABLED'] = os.getenv('ANNOUNCEMENT_ENABLED', 'False').lower() == 'true'
app.config['ANALYZE_SCRIPT'] = os.getenv('ANALYZE_SCRIPT', '')
app.config['ANALYZE_ENABLE'] = os.getenv('ANALYZE_ENABLE', 'False').lower() == 'true'

db.init_app(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

UPLOAD_FOLDER = app.config['UPLOAD_FOLDER']
PER_PAGE = app.config['PER_PAGE']

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

def get_redirect_url(target, default_params=None):
    if default_params is None:
        default_params = {}
    
    # 获取来源页面的查询参数
    referer = request.referrer or ''
    parsed_url = urlparse(referer)
    query_params = parse_qs(parsed_url.query)
    
    # 合并参数
    params = {**default_params, **{k: v[0] for k, v in query_params.items() if k != 'page'}}
    
    # 构建URL
    return url_for(target, **params)

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

@app.context_processor
def inject_background_config():
    return {
        'background_image_url': app.config['BACKGROUND_IMAGE_URL'],
        'background_opacity': app.config['BACKGROUND_OPACITY'],
        'background_size': app.config['BACKGROUND_SIZE'],
        'announcement': app.config['DASHBOARD_ANNOUNCEMENT'],
        'announcement_enabled': app.config['ANNOUNCEMENT_ENABLED'],
        'analyzer_script': app.config['ANALYZE_SCRIPT'],
        'analyze_enable': app.config['ANALYZE_ENABLE']
    }

# 添加日期格式化过滤器
@app.template_filter('dateformat')
def dateformat(value, format='%Y-%m-%d'):
    if value is None:
        return ""
    return value.strftime(format)

@app.route('/')
@login_required
def dashboard():
    # 获取日期参数，默认为今天
    date_str = request.args.get('date', '')
    if date_str:
        try:
            selected_date = datetime.datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            selected_date = datetime.date.today()
    else:
        selected_date = datetime.date.today()
    
    # 获取日期范围
    def get_date_range(date):
        start = datetime.datetime.combine(date, datetime.time.min)
        end = datetime.datetime.combine(date, datetime.time.max)
        return start, end
    
    # 获取选定日期的范围
    selected_start, selected_end = get_date_range(selected_date)
    
    # 获取前一天的日期
    prev_date = selected_date - datetime.timedelta(days=1)
    
    # 销售排行榜（选定日期）
    sale_ranks = db.session.query(
        Product.name,
        func.sum(Sale.amount).label("total_amount"),
        func.sum(Sale.quantity).label("total_qty")
    ).join(Sale.product).filter(
        Sale.type=='out', 
        Sale.created_at >= selected_start, 
        Sale.created_at <= selected_end
    ).group_by(Product.id).order_by(desc("total_amount")).limit(5).all()

    # 分类销售额（选定日期）
    cat_sales = db.session.query(
        Category.name, func.sum(Sale.amount)
    ).join(Product, Product.category_id == Category.id).join(Sale, Sale.product_id == Product.id)\
     .filter(
         Sale.type=='out',
         Sale.created_at >= selected_start,
         Sale.created_at <= selected_end
     ).group_by(Category.id).all()
    cat_names = [c[0] for c in cat_sales]
    cat_amounts = [float(c[1] or 0) for c in cat_sales]

    # 今日销售额
    today_start, today_end = get_date_range(datetime.date.today())
    today_total = db.session.query(func.sum(Sale.amount)).filter(
        Sale.type=='out', 
        Sale.created_at >= today_start, 
        Sale.created_at <= today_end
    ).scalar() or 0
    
    # 昨日销售额
    yesterday = datetime.date.today() - datetime.timedelta(days=1)
    y_start, y_end = get_date_range(yesterday)
    yesterday_total = db.session.query(func.sum(Sale.amount)).filter(
        Sale.type=='out', 
        Sale.created_at >= y_start, 
        Sale.created_at <= y_end
    ).scalar() or 0
    
    # 选定日期的销售额
    selected_date_total = db.session.query(func.sum(Sale.amount)).filter(
        Sale.type=='out', 
        Sale.created_at >= selected_start, 
        Sale.created_at <= selected_end
    ).scalar() or 0
    
    # 历史总销售额
    all_total = db.session.query(func.sum(Sale.amount)).filter(Sale.type=='out').scalar() or 0
    
    # 选定日期的销售流水
    sales = Sale.query.filter(
        Sale.type=='out',
        Sale.created_at >= selected_start,
        Sale.created_at <= selected_end
    ).order_by(Sale.created_at.desc()).limit(20).all()
    
    return render_template('dashboard.html',
                           sale_ranks=sale_ranks, 
                           cat_names=cat_names, 
                           cat_amounts=cat_amounts,
                           today_total=today_total, 
                           yesterday_total=yesterday_total,
                           selected_date_total=selected_date_total,
                           all_total=all_total,
                           sales=sales,
                           selected_date=selected_date,
                           prev_date=prev_date,
                           datetime=datetime)

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
    category_id = request.args.get('category_id', type=int)
    query = Product.query
    if keyword:
        query = query.filter(Product.name.like(f'%{keyword}%'))
    if category_id:  # 如果有分类ID则过滤
        query = query.filter_by(category_id=category_id)
    products = query.order_by(Product.id.desc()).paginate(page=page, per_page=PER_PAGE)

    # 预加载分类数据用于下拉菜单
    categories = Category.query.all()

    return render_template('products.html', products=products, categories=categories, keyword=keyword, category_id=category_id)

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

@app.route('/products/delete/<int:pid>', methods=['POST'])
@login_required
def delete_product(pid):

    from flask_wtf.csrf import validate_csrf
    try:
        validate_csrf(request.form.get('csrf_token')) 
    except:
        flash('CSRF令牌验证失败', 'error')
        return redirect(url_for('products'))
    
    check_admin()
    prod = db.session.get(Product, pid)
    if prod is None:
        abort(404)
    db.session.delete(prod)
    db.session.commit()
    log_action(current_user, f"删除商品:{prod.name}")
    flash('商品已删除')
    return redirect(url_for('products'))

@app.route('/products/batch_delete', methods=['POST'])
@login_required
def batch_delete_products():
    check_admin()
    
    # 使用 Flask-WTF 的 CSRF 验证
    from flask_wtf.csrf import validate_csrf
    try:
        validate_csrf(request.form.get('csrf_token'))
    except:
        flash('CSRF令牌验证失败', 'error')
        return redirect(url_for('products'))
    
    product_ids = request.form.get('product_ids', '')
    if not product_ids:
        flash('请选择要删除的商品', 'error')
        return redirect(url_for('products'))
    
    try:
        product_ids = [int(pid) for pid in product_ids.split(',')]
        deleted_count = 0
        deleted_names = []
        
        for pid in product_ids:
            product = db.session.get(Product, pid)
            if product:
                # 记录删除的商品名称
                deleted_names.append(product.name)
                
                # 删除相关销售记录
                Sale.query.filter_by(product_id=pid).delete()
                # 删除商品
                db.session.delete(product)
                deleted_count += 1
        
        db.session.commit()
        log_action(current_user, f"批量删除商品: {', '.join(deleted_names)}")
        flash(f'成功删除 {deleted_count} 个商品', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'删除商品时出错: {str(e)}', 'error')
    
    return redirect(url_for('products'))

@app.route('/products/import', methods=['GET', 'POST'])
@login_required
def product_import():
    check_admin()
    csv_form = ProductImportForm()
    manual_form = ManualProductForm()

    # 预加载分类数据用于下拉菜单
    categories = Category.query.all()
    manual_form.category.choices = [(c.id, c.name) for c in categories]

    # 处理CSV导入
    if csv_form.validate_on_submit() and csv_form.submit.data:
        file = csv_form.file.data
        try:
            success = import_products_csv(file)
            if success:
                log_action(current_user, "批量导入商品")
                flash('CSV导入成功', 'success')
            else:
                flash('CSV导入失败，请检查数据格式', 'error')
        except Exception as e:
            flash('CSV导入失败: ' + str(e), 'error')
        return redirect(url_for('product_import'))

    # 处理手动添加商品
    if manual_form.validate_on_submit() and manual_form.submit.data:
        try:
            # 创建新商品
            product = Product(
                name=manual_form.name.data,
                price=manual_form.price.data,
                stock=manual_form.stock.data,
                category_id=manual_form.category.data
            )

            # 处理图片
            img_path = manual_form.image_link.data.strip() if manual_form.image_link.data else ''
            img = manual_form.image.data
            if not img_path and img:
                filename = secure_filename(img.filename)
                filename = datetime.datetime.now().strftime('%Y%m%d%H%M%S_') + filename
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                img.save(file_path)
                img_path = 'uploads/' + filename

            if img_path:
                product.image = img_path

            db.session.add(product)
            db.session.commit()

            log_action(current_user, f"手动添加商品: {product.name}")
            flash('商品添加成功', 'success')
            return redirect(url_for('product_import'))

        except Exception as e:
            flash('商品添加失败: ' + str(e), 'error')

    return render_template('product_import.html', csv_form=csv_form, manual_form=manual_form)

@app.route('/products/import/template')
@login_required
def download_import_template():
    """下载CSV导入模板"""
    check_admin()

    # 创建CSV内容
    output = StringIO()
    writer = csv.writer(output)

    # 写入表头
    writer.writerow(['商品名', '单价', '库存', '分类', '图片链接(可选)'])

    # 写入示例数据
    writer.writerow(['示例商品1', '19.99', '100', '电子产品', 'http://example.com/image1.jpg'])
    writer.writerow(['示例商品2', '29.99', '50', '服装', ''])
    writer.writerow(['示例商品3', '9.99', '200', '食品', ''])

    # 创建响应
    response = make_response(output.getvalue())
    response.headers["Content-Disposition"] = "attachment; filename=product_import_template.csv"
    response.headers["Content-type"] = "text/csv"

    return response

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

@app.route('/categories/delete/<int:cat_id>', methods=['POST'])
@login_required
def delete_category(cat_id):
    # 添加CSRF验证
    from flask_wtf.csrf import validate_csrf
    try:
        validate_csrf(request.form.get('csrf_token'))
    except:
        flash('CSRF令牌验证失败', 'error')
        return redirect(url_for('categories'))

    check_admin()
    cat = Category.query.get(cat_id)
    if cat:
        db.session.delete(cat)
        db.session.commit()
        log_action(current_user, f"删除分类:{cat.name}")
        flash('已删除')
    return redirect(url_for('categories'))

@app.route('/sales')
@login_required
def sales():
    page = request.args.get('page', 1, type=int)
    cats = Category.query.all()
    
    # 查询并分页
    query = Product.query.order_by(Product.id.desc())
    products = query.paginate(page=page, per_page=PER_PAGE)
    
    # 按分类分组
    from collections import defaultdict
    grouped_products = defaultdict(list)
    for product in products.items:
        grouped_products[product.category.name].append(product)
    
    # 统计信息
    stat_map = {}
    for p in products.items:
        all_sale = db.session.query(func.sum(Sale.amount)).filter(
            Sale.product_id==p.id, Sale.type=='out'
        ).scalar() or 0
        today_sale = db.session.query(func.sum(Sale.amount)).filter(
            Sale.product_id==p.id, Sale.type=='out',
            Sale.created_at >= today_range()[0],
            Sale.created_at <= today_range()[1]
        ).scalar() or 0
        all_qty = db.session.query(func.sum(Sale.quantity)).filter(
            Sale.product_id==p.id, Sale.type=='out'
        ).scalar() or 0
        today_qty = db.session.query(func.sum(Sale.quantity)).filter(
            Sale.product_id==p.id, Sale.type=='out',
            Sale.created_at >= today_range()[0],
            Sale.created_at <= today_range()[1]
        ).scalar() or 0
        stat_map[p.id] = {'all_sale': all_sale, 'today_sale': today_sale, 'all_qty': all_qty, 'today_qty': today_qty}
    
    return render_template('sales.html', 
                           products=products, 
                           grouped_products=grouped_products, 
                           cats=cats, 
                           stat_map=stat_map)

@app.route('/sales-simple')
@login_required
def sales_simple():
    page = request.args.get('page', 1, type=int)
    keyword = request.args.get('keyword', '')  # 获取搜索关键词
    category_id = request.args.get('category_id', type=int)  # 获取分类筛选
    cats = Category.query.all()
    
    # 查询并分页
    query = Product.query.order_by(Product.id.desc())
    if keyword:
        query = query.filter(Product.name.like(f'%{keyword}%'))
    if category_id:
        query = query.filter_by(category_id=category_id)

    products = query.paginate(page=page, per_page=12)  # 每页显示12个商品
    
    return render_template('sales-simple.html', 
                           products=products, 
                           cats=cats, 
                           keyword=keyword,
                           category_id=category_id)

@app.route('/sales/operate/<int:pid>', methods=['POST'])
@login_required
def sales_operate(pid):
    prod = db.session.get(Product, pid)
    if prod is None:
        abort(404)
    try:
        qty = int(request.form.get("quantity"))
    except Exception:
        flash("数量不合法")
        # 根据来源页面决定重定向
        if request.form.get("source_page") == "sales_simple" or 'sales-simple' in (request.referrer or ''):
            return redirect(url_for('sales_simple'))
        return redirect(url_for('sales'))
    
    # 获取来源页面信息，用于操作完成后重定向
    source_page = request.form.get("source_page")
    referer = request.referrer or ''
    redirect_to_simple = source_page == "sales_simple" or 'sales-simple' in referer
    
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
            # 根据来源页面决定重定向
            if redirect_to_simple:
                return redirect(get_redirect_url('sales_simple', {'page': 1}))
            return redirect(get_redirect_url('sales', {'page': 1}))
        prod.stock -= qty
        amount = round(qty * prod.price, 2)
        sale = Sale(product_id=prod.id, quantity=qty, type='out', user_id=current_user.id, amount=amount)
        db.session.add(sale)
        db.session.commit()
        log_action(current_user, f"销售:{prod.name} 数量:{qty}")
        flash('销售成功')
    else:
        flash('未知操作')
    
    # 根据来源页面决定重定向
    if redirect_to_simple:
        return redirect(get_redirect_url('sales_simple', {'page': 1}))
    return redirect(get_redirect_url('sales', {'page': 1}))

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
        # 处理用户名修改
        new_username = form.username.data.strip()
        if new_username != user.username:
            # 检查新用户名是否已存在（排除当前用户）
            existing_user = User.query.filter(User.username == new_username, User.id != user.id).first()
            if existing_user:
                flash('用户名已存在，请选择其他用户名', 'error')
                return render_template('user_approve.html', form=form, user=user)
            
            # 更新用户名
            old_username = user.username
            user.username = new_username
            log_action(current_user, f"修改用户 {old_username} 的用户名为 {new_username}")
            flash('用户名已更新', 'success')

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

@app.route('/users/delete/<int:uid>', methods=['POST'])
@login_required
def delete_user(uid):
    check_admin()
    
    # 不能删除自己
    if uid == current_user.id:
        flash('不能删除自己的账户', 'error')
        return redirect(url_for('users'))
    
    user = db.session.get(User, uid)
    if user is None:
        flash('用户不存在', 'error')
        return redirect(url_for('users'))
    
    # 检查是否至少保留一个管理员账户
    if user.is_admin:
        admin_count = User.query.filter_by(is_admin=True).count()
        if admin_count <= 1:
            flash('必须至少保留一个管理员账户', 'error')
            return redirect(url_for('users'))
    
    # 记录删除操作
    username = user.username
    
    # 删除用户相关的日志记录（可选，根据需求决定）
    # Log.query.filter_by(user_id=uid).delete()
    
    # 删除用户相关的销售记录（可选，根据需求决定）
    # Sale.query.filter_by(user_id=uid).delete()
    
    # 删除用户
    db.session.delete(user)
    db.session.commit()
    
    log_action(current_user, f"删除用户: {username}")
    flash(f'用户 {username} 已删除', 'success')
    return redirect(url_for('users'))

@app.route('/logs')
@login_required
def logs():
    check_admin()
    logs = Log.query.order_by(Log.ts.desc()).limit(100).all()
    return render_template('logs.html', logs=logs)

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        # 确保至少有一个活跃的管理员账户
        active_admin_exists = User.query.filter_by(is_admin=True, is_active=True).first()
        if not active_admin_exists:
            # 尝试查找名为'admin'的用户
            admin_user = User.query.filter_by(username='admin').first()
            if admin_user:
                # 如果找到，确保它是活跃的管理员
                admin_user.is_admin = True
                admin_user.is_active = True
                print(f"已将用户 '{admin_user.username}' 设置为活跃管理员")
            else:
                # 如果没有找到，创建新的默认管理员账户
                admin = User(
                    username='admin', 
                    password=generate_password_hash('admin'), 
                    is_admin=True, 
                    is_active=True
                )
                db.session.add(admin)
                print("创建了默认管理员账户: admin/admin")
            db.session.commit()
        
        # 确保存在默认分类
        if not Category.query.first():
            default_category = Category(name="未分类")
            db.session.add(default_category)
            db.session.commit()
            print("创建了默认分类: 未分类")
    app.run(debug=True)
