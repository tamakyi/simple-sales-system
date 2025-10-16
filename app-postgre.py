from flask import Flask, render_template, redirect, url_for, request, flash, send_file, abort
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from forms import *
from models import db, User, Category, Product, Sale, Log
from utils import import_products_csv
import os
import os.path as op
import datetime as dt
import openpyxl
from io import BytesIO
from sqlalchemy import func, desc
from dotenv import load_dotenv
from sqlalchemy.exc import OperationalError
from sqlalchemy import create_engine, text
from urllib.parse import urlparse
from flask import make_response
import csv
from io import StringIO
from flask_wtf.csrf import CSRFProtect, generate_csrf
from urllib.parse import urlparse, parse_qs
import random
import string
from datetime import datetime, timedelta
from flask import session, jsonify
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from itsdangerous import URLSafeTimedSerializer


basedir = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(basedir, '.env'))

app = Flask(__name__)
csrf = CSRFProtect(app)

app.config['APP_TITLE'] = os.getenv('APP_TITLE', '狼的小卖部')
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'fallback_secret_key')
app.config['UPLOAD_FOLDER'] = op.abspath(op.expanduser(os.getenv('UPLOAD_FOLDER', 'static/uploads')))
app.config['BACKGROUND_IMAGE_URL'] = os.getenv('BACKGROUND_IMAGE_URL', '')
app.config['BACKGROUND_OPACITY'] = float(os.getenv('BACKGROUND_OPACITY', 0.1))
app.config['BACKGROUND_SIZE'] = os.getenv('BACKGROUND_SIZE', 'cover')
app.config['MAX_CONTENT_LENGTH'] = int(os.getenv('MAX_CONTENT_LENGTH', 2097152))
app.config['PER_PAGE'] = int(os.getenv('PER_PAGE', 10))
app.config['SMTP_SERVER'] = os.getenv('SMTP_SERVER', 'smtp.qq.com')
app.config['SMTP_PORT'] = int(os.getenv('SMTP_PORT', 587))
app.config['SMTP_USERNAME'] = os.getenv('SMTP_USERNAME', '')
app.config['SMTP_PASSWORD'] = os.getenv('SMTP_PASSWORD', '')
app.config['SMTP_USE_TLS'] = os.getenv('SMTP_USE_TLS', 'True').lower() == 'true'
app.config['FROM_EMAIL'] = os.getenv('FROM_EMAIL', '')
app.config['SECURITY_PASSWORD_SALT'] = os.getenv('SECURITY_PASSWORD_SALT', 'another-secret-salt')
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('SQLALCHEMY_DATABASE_URI', 'postgresql://username:password@localhost:5432/your_database_name')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ECHO'] = False
app.config['DASHBOARD_ANNOUNCEMENT'] = os.getenv('DASHBOARD_ANNOUNCEMENT', '')
app.config['ANNOUNCEMENT_ENABLED'] = os.getenv('ANNOUNCEMENT_ENABLED', 'False').lower() == 'true'
app.config['ANALYZE_SCRIPT'] = os.getenv('ANALYZE_SCRIPT', '')
app.config['ANALYZE_ENABLE'] = os.getenv('ANALYZE_ENABLE', 'False').lower() == 'true'
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_size': 10,
    'max_overflow': 20,
    'pool_recycle': 3600,
    'pool_pre_ping': True,
    'connect_args': {
        'connect_timeout': 10,
        'application_name': 'sss_postgre'
    }
}

db.init_app(app)
register_attempts = {}
login_attempts = {}
login_manager = LoginManager(app)
login_manager.login_view = 'login'

UPLOAD_FOLDER = app.config['UPLOAD_FOLDER']
PER_PAGE = app.config['PER_PAGE']

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

def check_database_health():
    """检查数据库连接健康状态"""
    try:
        # 简单查询测试连接
        result = db.session.execute(text("SELECT version()"))
        db_version = result.scalar()
        print(f"数据库连接正常: {db_version}")
        return True
    except Exception as e:
        print(f"数据库健康检查失败: {e}")
        return False

def is_register_locked(ip_address):
    """检查IP是否被锁定注册"""
    if ip_address in register_attempts:
        attempts, lock_time = register_attempts[ip_address]
        if lock_time and datetime.now() < lock_time:
            return True
    return False

def record_register_attempt(ip_address):
    """记录注册尝试"""
    if ip_address not in register_attempts:
        register_attempts[ip_address] = [0, None]
    
    attempts, lock_time = register_attempts[ip_address]
    
    # 如果还在锁定期内，直接返回
    if lock_time and datetime.now() < lock_time:
        return
    
    # 增加尝试计数
    attempts += 1
    register_attempts[ip_address][0] = attempts
    
    # 如果1小时内尝试注册超过10次，锁定1小时
    if attempts >= 10:
        lock_time = datetime.now() + timedelta(hours=1)
        register_attempts[ip_address] = [attempts, lock_time]

def generate_reset_token(email):
    """生成密码重置令牌"""
    serializer = URLSafeTimedSerializer(app.config['SECRET_KEY'])
    return serializer.dumps(email, salt=app.config['SECURITY_PASSWORD_SALT'])

def verify_reset_token(token, expiration=3600):
    """验证密码重置令牌"""
    serializer = URLSafeTimedSerializer(app.config['SECRET_KEY'])
    try:
        email = serializer.loads(
            token,
            salt=app.config['SECURITY_PASSWORD_SALT'],
            max_age=expiration
        )
    except:
        return None
    return email

def send_reset_email(user):
    """发送密码重置邮件"""
    token = generate_reset_token(user.email)
    
    # 构建重置链接
    reset_url = url_for('reset_password', token=token, _external=True)
    
    # 邮件内容
    subject = f"{app.config['APP_TITLE']} - 密码重置请求"
    
    html_content = f"""
    <html>
    <body>
        <h2>密码重置请求</h2>
        <p>您好 {user.username},</p>
        <p>我们收到了您重置密码的请求。请点击下面的链接来重置您的密码：</p>
        <p><a href="{reset_url}" style="background-color: #007bff; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">重置密码</a></p>
        <p>或者复制以下链接到浏览器：</p>
        <p>{reset_url}</p>
        <p>如果您没有请求重置密码，请忽略此邮件。</p>
        <p>此链接将在1小时后过期。</p>
        <br>
        <p>祝好,<br>{app.config['APP_TITLE']} 团队</p>
    </body>
    </html>
    """
    
    text_content = f"""
    密码重置请求
    
    您好 {user.username},
    
    我们收到了您重置密码的请求。请访问以下链接来重置您的密码：
    
    {reset_url}
    
    如果您没有请求重置密码，请忽略此邮件。
    
    此链接将在1小时后过期。
    
    祝好,
    {app.config['APP_TITLE']} 团队
    """
    
    try:
        print(f"=== 邮件发送调试信息 ===")
        print(f"收件人: {user.email}")
        print(f"SMTP服务器: {app.config['SMTP_SERVER']}:{app.config['SMTP_PORT']}")
        print(f"发件人: {app.config['FROM_EMAIL']}")
        print(f"用户名: {app.config['SMTP_USERNAME']}")
        print(f"使用TLS: {app.config['SMTP_USE_TLS']}")
        print(f"端口: {app.config['SMTP_PORT']}")
        
        # 创建邮件
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = app.config['FROM_EMAIL']
        msg['To'] = user.email
        
        # 添加文本和HTML版本
        part1 = MIMEText(text_content, 'plain', 'utf-8')
        part2 = MIMEText(html_content, 'html', 'utf-8')
        msg.attach(part1)
        msg.attach(part2)
        
        # 根据端口选择连接方式
        if app.config['SMTP_PORT'] == 465:
            print("使用SSL连接 (端口465)...")
            server = smtplib.SMTP_SSL(app.config['SMTP_SERVER'], app.config['SMTP_PORT'])
            server.set_debuglevel(1)  # 开启详细调试
        else:
            print(f"使用普通连接 (端口{app.config['SMTP_PORT']})...")
            server = smtplib.SMTP(app.config['SMTP_SERVER'], app.config['SMTP_PORT'])
            server.set_debuglevel(1)  # 开启详细调试
            
            # 检查服务器能力
            try:
                server.ehlo()
                if server.has_extn('STARTTLS'):
                    print("服务器支持STARTTLS，正在启用...")
                    server.starttls()
                    server.ehlo()  # 重新ehlo
                else:
                    print("服务器不支持STARTTLS")
            except Exception as e:
                print(f"EHLO/STARTTLS检查失败: {e}")
        
        print("正在登录SMTP服务器...")
        server.login(app.config['SMTP_USERNAME'], app.config['SMTP_PASSWORD'])
        
        print("正在发送邮件...")
        server.send_message(msg)
        print("邮件发送成功！")
        
        server.quit()
        return True
        
    except smtplib.SMTPConnectError as e:
        print(f"SMTP连接错误: {e}")
        print("可能的原因:")
        print("1. SMTP服务器地址或端口错误")
        print("2. 防火墙阻止了连接")
        print("3. 网络连接问题")
        return False
    except smtplib.SMTPAuthenticationError as e:
        print(f"SMTP认证失败: {e}")
        print("可能的原因:")
        print("1. 用户名或密码错误")
        print("2. 需要使用授权码而不是密码")
        print("3. 邮箱未开启SMTP服务")
        return False
    except smtplib.SMTPServerDisconnected as e:
        print(f"SMTP服务器断开连接: {e}")
        print("可能的原因:")
        print("1. 服务器主动断开连接")
        print("2. 连接超时")
        print("3. 服务器要求使用SSL/TLS")
        return False
    except Exception as e:
        print(f"发送邮件失败: {e}")
        import traceback
        traceback.print_exc()
        return False

@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    """忘记密码页面"""
    form = ForgotPasswordForm()
    
    if form.validate_on_submit():
        # 检查验证码
        if 'captcha' not in session or session['captcha'].upper() != form.captcha.data.upper():
            flash('验证码错误')
            return render_template('forgot_password.html', form=form)
        
        # 查找用户
        user = User.query.filter_by(email=form.email.data).first()
        if user and user.is_active:
            if send_reset_email(user):
                flash('密码重置邮件已发送，请检查您的邮箱。', 'success')
                # 清除验证码session
                session.pop('captcha', None)
                return redirect(url_for('login'))
            else:
                flash('发送邮件失败，请稍后重试。', 'error')
        else:
            flash('该邮箱未注册或账户未激活。', 'error')
    
    return render_template('forgot_password.html', form=form)

@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    """重置密码页面"""
    # 验证令牌
    email = verify_reset_token(token)
    if not email:
        flash('重置链接无效或已过期。', 'error')
        return redirect(url_for('forgot_password'))
    
    # 查找用户
    user = User.query.filter_by(email=email).first()
    if not user or not user.is_active:
        flash('用户不存在或账户未激活。', 'error')
        return redirect(url_for('forgot_password'))
    
    form = ResetPasswordForm()
    
    if form.validate_on_submit():
        try:
            # 更新密码
            user.password = generate_password_hash(form.password.data)
            db.session.commit()
            
            flash('密码重置成功，请使用新密码登录。', 'success')
            return redirect(url_for('login'))
        except Exception as e:
            db.session.rollback()
            flash(f'重置密码失败: {str(e)}', 'error')
    
    return render_template('reset_password.html', form=form, token=token)

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

def create_database_if_not_exists():
    """
    检查配置中的PostgreSQL数据库是否存在，若不存在则自动创建。
    """
    database_uri = app.config['SQLALCHEMY_DATABASE_URI']
    parsed_uri = urlparse(database_uri)
    db_name = parsed_uri.path.lstrip('/')
    db_user = parsed_uri.username
    db_password = parsed_uri.password
    db_host = parsed_uri.hostname
    db_port = parsed_uri.port if parsed_uri.port else 5432

    print(f"检查数据库: {db_name}")

    try:
        # 1. 首先尝试连接目标数据库
        print(f"尝试连接目标数据库: {db_name}")
        test_engine = create_engine(database_uri)
        with test_engine.connect() as conn:
            print(f"数据库 '{db_name}' 已存在，直接使用。")
        return True
    except Exception as e:
        error_msg = str(e).lower()
        print(f"连接目标数据库失败: {error_msg}")
        
        # 检查是否是数据库不存在的错误
        if any(pattern in error_msg for pattern in ['database', 'does not exist', '不存在']):
            print(f"数据库 '{db_name}' 不存在，尝试创建...")
            try:
                # 构建连接到postgres系统数据库的URI
                if db_password:
                    base_uri = f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/postgres"
                else:
                    base_uri = f"postgresql://{db_user}@{db_host}:{db_port}/postgres"
                
                print(f"使用基础连接: postgresql://{db_user}:****@{db_host}:{db_port}/postgres")
                
                # 连接到PostgreSQL服务器（使用postgres数据库）
                base_engine = create_engine(base_uri)
                with base_engine.connect() as conn:
                    # 检查数据库是否已经存在（可能在其他会话中创建了）
                    result = conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :db_name"), 
                                         {'db_name': db_name})
                    exists = result.scalar() is not None
                    
                    if exists:
                        print(f"数据库 '{db_name}' 已存在（在其他会话中创建）。")
                        return True
                    
                    # 执行CREATE DATABASE语句
                    print(f"正在创建数据库: {db_name}")
                    conn.execute(text(f"CREATE DATABASE {db_name};"))
                    conn.commit()
                
                print(f"数据库 '{db_name}' 创建成功。")
                
                # 验证新创建的数据库
                print("验证新创建的数据库...")
                test_engine = create_engine(database_uri)
                with test_engine.connect() as conn:
                    print(f"数据库 '{db_name}' 验证成功。")
                
                return True
            except Exception as create_error:
                print(f"创建数据库失败: {create_error}")
                print("请确保：")
                print("1. PostgreSQL服务正在运行")
                print("2. 数据库用户有创建数据库的权限")
                print("3. 连接信息正确")
                return False
        else:
            # 其他连接错误（权限、网络、认证等）
            print(f"数据库连接错误: {e}")
            print("请检查：")
            print("1. PostgreSQL服务是否启动")
            print("2. 数据库连接信息是否正确")
            print("3. 网络连接是否正常")
            return False

def log_action(user, action, sale=None):
    db.session.add(Log(user_id=user.id, action=action, sale=sale))
    db.session.commit()

@app.errorhandler(404)
def not_found_error(error):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_error(error):
    return render_template('500.html'), 500

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

def today_range():
    today = datetime.now().date()
    start = datetime(today.year, today.month, today.day, 0, 0, 0)
    end = datetime(today.year, today.month, today.day, 23, 59, 59)
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
        'analyze_enable': app.config['ANALYZE_ENABLE'],
        'app_title': app.config['APP_TITLE']
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
    # 获取日期范围参数，默认为今天
    start_date_str = request.args.get('start_date', '')
    end_date_str = request.args.get('end_date', '')
    
    today = dt.date.today()
    
    # 处理日期范围
    if start_date_str and end_date_str:
        try:
            start_date = dt.datetime.strptime(start_date_str, '%Y-%m-%d').date()
            end_date = dt.datetime.strptime(end_date_str, '%Y-%m-%d').date()
            
            # 确保结束日期不晚于今天
            if end_date > today:
                end_date = today
            # 确保开始日期不晚于结束日期
            if start_date > end_date:
                start_date = end_date
        except ValueError:
            start_date = today
            end_date = today
    else:
        # 如果没有提供日期范围，默认为今天
        start_date = today
        end_date = today
    
    # 获取日期范围
    def get_date_range(start_date, end_date):
        # 注意：结束日期要包含当天的23:59:59
        start = dt.datetime.combine(start_date, dt.time.min)
        end = dt.datetime.combine(end_date, dt.time.max)
        return start, end
    
    # 获取选定日期范围
    selected_start, selected_end = get_date_range(start_date, end_date)
    
    # 获取前一天的日期（用于前一天按钮，现在不需要了）
    # prev_date = selected_date - dt.timedelta(days=1)
    
    # 获取今天的日期范围
    today_start, today_end = get_date_range(today, today)
    
    # 获取昨天的日期范围
    yesterday = today - dt.timedelta(days=1)
    y_start, y_end = get_date_range(yesterday, yesterday)
    
    # 销售排行榜（选定日期范围）
    sale_ranks = db.session.query(
        Product.name,
        func.sum(Sale.amount).label("total_amount"),
        func.sum(Sale.quantity).label("total_qty")
    ).join(Sale.product).filter(
        Sale.type=='out', 
        Sale.is_reversed == False,
        Sale.created_at >= selected_start, 
        Sale.created_at <= selected_end
    ).group_by(Product.id).order_by(desc("total_amount")).limit(5).all()

    # 分类销售额（选定日期范围）
    cat_sales = db.session.query(
        Category.name, func.sum(Sale.amount)
    ).join(Product, Product.category_id == Category.id).join(Sale, Sale.product_id == Product.id)\
     .filter(
         Sale.type=='out',
         Sale.is_reversed == False,
         Sale.created_at >= selected_start,
         Sale.created_at <= selected_end
     ).group_by(Category.id).all()
    
    # 提取分类名称和销售额
    cat_names = [c[0] for c in cat_sales]
    cat_amounts = [float(c[1] or 0) for c in cat_sales]

    # 今日销售额
    today_total = db.session.query(func.sum(Sale.amount)).filter(
        Sale.type=='out', 
        Sale.is_reversed == False,
        Sale.created_at >= today_start, 
        Sale.created_at <= today_end
    ).scalar() or 0
    
    # 昨日销售额
    yesterday_total = db.session.query(func.sum(Sale.amount)).filter(
        Sale.type=='out', 
        Sale.is_reversed == False,
        Sale.created_at >= y_start, 
        Sale.created_at <= y_end
    ).scalar() or 0
    
    # 选定日期范围的销售额
    selected_date_total = db.session.query(func.sum(Sale.amount)).filter(
        Sale.type=='out', 
        Sale.is_reversed == False,
        Sale.created_at >= selected_start, 
        Sale.created_at <= selected_end
    ).scalar() or 0
    
    # 历史总销售额
    all_total = db.session.query(func.sum(Sale.amount)).filter(Sale.type=='out', Sale.is_reversed == False).scalar() or 0
    
    # 选定日期范围的销售流水
    sales = Sale.query.filter(
        Sale.type=='out',
        Sale.is_reversed == False,
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
                           start_date=start_date,
                           end_date=end_date,
                           datetime=dt)

@app.route('/captcha')
def captcha():
    """生成验证码图片"""
    from io import BytesIO
    from PIL import Image, ImageDraw, ImageFont
    import os
    
    captcha_text = ''.join(random.choices(string.ascii_uppercase + string.digits, k=5))
    session['captcha'] = captcha_text
    
    width, height = 180, 38
    image = Image.new('RGB', (width, height), color=(255, 255, 255))
    draw = ImageDraw.Draw(image)
    
    try:
        # 使用合适的字体大小
        font = ImageFont.truetype("arial.ttf", 24)
    except:
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/Arial.ttf", 24) #自己找这个字体，我是去pip包的资源里扒的
        except:
            try:
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 24)
            except:
                font = ImageFont.load_default()
    
    text_width = len(captcha_text) * 22
    start_x = (width - text_width) // 2
    
    for i, char in enumerate(captcha_text):
        y_position = (height - 24) // 2
        draw.text((start_x + i * 26, y_position), char, fill=(0, 0, 0), font=font)
    
    # 添加少量干扰线
    for i in range(5):
        x1 = random.randint(0, width)
        y1 = random.randint(0, height)
        x2 = random.randint(0, width)
        y2 = random.randint(0, height)
        draw.line((x1, y1, x2, y2), fill=(180, 180, 180), width=1)
    
    # 添加少量噪点
    for i in range(30):
        x = random.randint(0, width)
        y = random.randint(0, height)
        draw.point((x, y), fill=(200, 200, 200))
    
    # 返回图片
    buffer = BytesIO()
    image.save(buffer, 'PNG')
    buffer.seek(0)
    
    return send_file(buffer, mimetype='image/png')

def is_login_locked(username):
    """检查用户是否被锁定"""
    if username in login_attempts:
        attempts, lock_time = login_attempts[username]
        if lock_time and datetime.now() < lock_time:
            return True
    return False

def get_lock_time_remaining(username):
    """获取剩余锁定时间（分钟）"""
    if username in login_attempts:
        attempts, lock_time = login_attempts[username]
        if lock_time:
            remaining = lock_time - datetime.now()
            return max(0, int(remaining.total_seconds() / 60))
    return 0

def record_login_attempt(username, success):
    """记录登录尝试"""
    if username not in login_attempts:
        login_attempts[username] = [0, None]
    
    if success:
        # 登录成功，重置计数
        login_attempts[username] = [0, None]
    else:
        # 登录失败
        attempts, lock_time = login_attempts[username]
        
        # 如果还在锁定期内，直接返回
        if lock_time and datetime.now() < lock_time:
            return
        
        # 增加失败计数
        attempts += 1
        login_attempts[username][0] = attempts
        
        # 如果失败5次，锁定10分钟
        if attempts >= 5:
            lock_time = datetime.now() + timedelta(minutes=10)
            login_attempts[username] = [attempts, lock_time]


@app.route('/login', methods=['GET', 'POST'])
def login():
    form = LoginForm()
    
    # 检查是否被锁定
    login_locked = False
    lock_time_remaining = 0
    
    if form.username.data:
        if is_login_locked(form.username.data):
            login_locked = True
            lock_time_remaining = get_lock_time_remaining(form.username.data)
    
    if form.validate_on_submit():
        # 检查验证码
        if 'captcha' not in session or session['captcha'].upper() != form.captcha.data.upper():
            flash('验证码错误')
            if form.username.data:
                record_login_attempt(form.username.data, False)
            return render_template('login.html', form=form, 
                                 login_locked=login_locked, 
                                 lock_time_remaining=lock_time_remaining)
        
        # 检查是否被锁定
        if is_login_locked(form.username.data):
            login_locked = True
            lock_time_remaining = get_lock_time_remaining(form.username.data)
            flash('登录失败次数过多，请稍后再试')
            return render_template('login.html', form=form, 
                                 login_locked=login_locked, 
                                 lock_time_remaining=lock_time_remaining)
        
        user = User.query.filter_by(username=form.username.data).first()
        if user and check_password_hash(user.password, form.password.data):
            if not user.is_active:
                flash('账户未激活，请联系管理员')
                record_login_attempt(form.username.data, False)
                return render_template('login.html', form=form)
            
            # 登录成功
            login_user(user)
            record_login_attempt(form.username.data, True)
            # 清除验证码session
            session.pop('captcha', None)
            return redirect(url_for('dashboard'))
        else:
            # 登录失败
            record_login_attempt(form.username.data, False)
            flash('用户名或密码错误')
            
            # 重新检查是否被锁定
            if form.username.data and is_login_locked(form.username.data):
                login_locked = True
                lock_time_remaining = get_lock_time_remaining(form.username.data)
    
    return render_template('login.html', form=form, 
                         login_locked=login_locked, 
                         lock_time_remaining=lock_time_remaining)

@app.route('/register', methods=['GET', 'POST'])
def register():
    form = RegisterForm()
    client_ip = request.remote_addr
    
    # 检查注册频率限制
    if is_register_locked(client_ip):
        flash('注册尝试过于频繁，请1小时后再试')
        return render_template('register.html', form=form)
    
    if form.validate_on_submit():
        # 检查验证码
        if 'captcha' not in session or session['captcha'].upper() != form.captcha.data.upper():
            flash('验证码错误')
            record_register_attempt(client_ip)
            return render_template('register.html', form=form)
        
        if User.query.filter_by(username=form.username.data).first():
            flash('用户名已存在')
            record_register_attempt(client_ip)
        elif User.query.filter_by(email=form.email.data).first():
            flash('邮箱已被注册')
            record_register_attempt(client_ip)
        else:
            user = User(
                username=form.username.data, 
                password=generate_password_hash(form.password.data),
                email=form.email.data  # 保存邮箱
            )
            db.session.add(user)
            db.session.commit()
            # 清除验证码session
            session.pop('captcha', None)
            flash('注册成功，请等待管理员审核')
            return redirect(url_for('login'))
    
    return render_template('register.html', form=form)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/debug/email-test')
def debug_email_test():
    """邮件配置测试页面（仅在调试模式下可用）"""
    if not app.debug:
        abort(404)
    
    return render_template('email_test.html')

@app.route('/debug/send-test-email', methods=['POST'])
def debug_send_test_email():
    """发送测试邮件（仅在调试模式下可用）"""
    if not app.debug:
        abort(404)
    
    email = request.form.get('email')
    if not email:
        return jsonify({'success': False, 'message': '请输入邮箱地址'})
    
    # 创建一个虚拟用户用于测试
    class TestUser:
        def __init__(self, username, email):
            self.username = username
            self.email = email
    
    test_user = TestUser("测试用户", email)
    
    # 使用修改后的发送函数
    success = send_reset_email(test_user)
    
    if success:
        return jsonify({'success': True, 'message': '测试邮件发送成功！'})
    else:
        return jsonify({'success': False, 'message': '测试邮件发送失败，请查看控制台日志'})

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
    prod = db.session.get(Product, pid)
    if prod is None:
        abort(404)
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
            filename = dt.datetime.now().strftime('%Y%m%d%H%M%S_') + filename
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
                filename = dt.datetime.now().strftime('%Y%m%d%H%M%S_') + filename
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                img.save(file_path)
                img_path = 'uploads/' + filename

            if img_path:
                product.image = img_path

            db.session.add(product)
            db.session.commit()

            log_action(current_user, f"手动添加商品: {product.name} (ID:{product.id})")
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
    cat = db.session.get(Category, cat_id)
    if cat is None:
        abort(404)
    
    # 检查是否有商品使用该分类
    product_count = Product.query.filter_by(category_id=cat_id).count()
    if product_count > 0:
        flash(f'该分类下有 {product_count} 个商品，无法删除', 'error')
        return redirect(url_for('categories'))
    
    if cat:
        db.session.delete(cat)
        db.session.commit()
        log_action(current_user, f"删除分类:{cat.name}")
        flash('已删除', 'success')
    return redirect(url_for('categories'))

@app.route('/categories/batch', methods=['POST'])
@login_required
def batch_categories():
    """批量操作分类"""
    check_admin()
    
    # CSRF验证
    from flask_wtf.csrf import validate_csrf
    try:
        validate_csrf(request.form.get('csrf_token'))
    except:
        flash('CSRF令牌验证失败', 'error')
        return redirect(url_for('categories'))
    
    action = request.form.get('action')
    category_ids = request.form.getlist('category_ids')
    
    if not category_ids:
        flash('请至少选择一个分类', 'error')
        return redirect(url_for('categories'))
    
    try:
        category_ids = [int(cat_id) for cat_id in category_ids]
        
        if action == 'delete':
            deleted_count = 0
            deleted_names = []
            
            for cat_id in category_ids:
                category = db.session.get(Category, cat_id)
                if category:
                    # 检查是否有商品使用该分类
                    product_count = Product.query.filter_by(category_id=cat_id).count()
                    if product_count > 0:
                        flash(f'分类 "{category.name}" 下有 {product_count} 个商品，无法删除', 'warning')
                        continue
                    
                    deleted_names.append(category.name)
                    db.session.delete(category)
                    deleted_count += 1
            
            if deleted_count > 0:
                db.session.commit()
                log_action(current_user, f"批量删除分类: {', '.join(deleted_names)}")
                flash(f'成功删除 {deleted_count} 个分类', 'success')
            else:
                flash('没有分类被删除', 'info')
                
    except Exception as e:
        db.session.rollback()
        flash(f'操作失败: {str(e)}', 'error')
    
    return redirect(url_for('categories'))

@app.route('/categories/edit/<int:cat_id>', methods=['POST'])
@login_required
def edit_category(cat_id):
    """编辑分类"""
    check_admin()
    
    # CSRF验证
    from flask_wtf.csrf import validate_csrf
    try:
        validate_csrf(request.form.get('csrf_token'))
    except:
        flash('CSRF令牌验证失败', 'error')
        return redirect(url_for('categories'))
    
    category = db.session.get(Category, cat_id)
    if not category:
        flash('分类不存在', 'error')
        return redirect(url_for('categories'))
    
    new_name = request.form.get('name', '').strip()
    if not new_name:
        flash('分类名不能为空', 'error')
        return redirect(url_for('categories'))
    
    # 检查分类名是否已存在（排除自身）
    existing_category = Category.query.filter(
        Category.name == new_name, 
        Category.id != cat_id
    ).first()
    
    if existing_category:
        flash('分类名已存在', 'error')
        return redirect(url_for('categories'))
    
    try:
        old_name = category.name
        category.name = new_name
        db.session.commit()
        log_action(current_user, f"修改分类: {old_name} -> {new_name}")
        flash('分类修改成功', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'修改失败: {str(e)}', 'error')
    
    return redirect(url_for('categories'))

@app.route('/sales')
@login_required
def sales():
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

    products = query.paginate(page=page, per_page=PER_PAGE)
    
    # 按分类分组
    from collections import defaultdict
    grouped_products = defaultdict(list)
    for product in products.items:
        grouped_products[product.category.name].append(product)
    
    stat_map = {}
    today_start, today_end = today_range()  # 先获取今天的日期范围
    
    for p in products.items:
        all_sale = db.session.query(func.sum(Sale.amount)).filter(
            Sale.product_id==p.id, Sale.type=='out', Sale.is_reversed == False
        ).scalar() or 0
        today_sale = db.session.query(func.sum(Sale.amount)).filter(
            Sale.product_id==p.id, Sale.type=='out',
            Sale.created_at >= today_start,  # 使用预获取的范围
            Sale.created_at <= today_end,
            Sale.is_reversed == False
        ).scalar() or 0
        all_qty = db.session.query(func.sum(Sale.quantity)).filter(
            Sale.product_id==p.id, Sale.type=='out', Sale.is_reversed == False
        ).scalar() or 0
        today_qty = db.session.query(func.sum(Sale.quantity)).filter(
            Sale.product_id==p.id, Sale.type=='out',
            Sale.created_at >= today_start,  # 使用预获取的范围
            Sale.created_at <= today_end,
            Sale.is_reversed == False
        ).scalar() or 0
        stat_map[p.id] = {'all_sale': all_sale, 'today_sale': today_sale, 'all_qty': all_qty, 'today_qty': today_qty}
    
    return render_template('sales.html', 
                           products=products, 
                           grouped_products=grouped_products, 
                           cats=cats, 
                           stat_map=stat_map,
                           keyword=keyword,
                           category_id=category_id)

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
        log_action(current_user, f"进货:{prod.name} 数量:{qty}", sale=sale)
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
        log_action(current_user, f"销售:{prod.name} 数量:{qty}", sale=sale)
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
    prod = db.session.get(Product, pid)
    if prod is None:
        abort(404)
    
    # 使用 filter() 方法
    sales = Sale.query.filter(
        Sale.product_id == pid, 
        Sale.type == 'out',
        Sale.is_reversed == False
    ).order_by(Sale.created_at.desc()).all()
    
    return render_template('sales_detail.html', prod=prod, sales=sales)

@app.route('/sales/reverse/<int:sale_id>', methods=['POST'])
@login_required
def reverse_sale(sale_id):
    """撤回进销存操作"""
    sale = Sale.query.get_or_404(sale_id)
    product = sale.product
    
    # 检查操作权限（管理员或操作者本人）
    if not (current_user.is_admin or current_user.id == sale.user_id):
        flash('无权限执行此操作', 'danger')
        return redirect(url_for('logs'))
    
    try:
        # 根据操作类型反向操作
        if sale.type == 'in':
            # 撤回进货：减少库存
            product.stock -= sale.quantity
        elif sale.type == 'out':
            # 撤回销售：增加库存
            product.stock += sale.quantity
        
        # 标记为已撤回
        sale.is_reversed = True
        db.session.commit()
        
        # 记录日志
        log_action(current_user, f"撤回了{sale.type}操作: {sale.product.name} x {sale.quantity}", sale=sale)
        flash('撤回成功', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'撤回失败: {str(e)}', 'danger')
    
    return redirect(url_for('logs'))


@app.route('/export')
@login_required
def export():
    sales = Sale.query.filter(Sale.is_reversed == False).all()
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
    user = db.session.get(User, uid)
    if user is None:
        abort(404)
    form = UserApproveForm(obj=user)
    
    if form.validate_on_submit():
        # 初始化修改标记
        changes_made = False
        message_parts = []

        # 处理用户名修改
        new_username = form.username.data.strip()
        if new_username != user.username:
            existing_user = User.query.filter(User.username == new_username, User.id != user.id).first()
            if existing_user:
                flash('用户名已存在，请选择其他用户名', 'error')
                return render_template('user_approve.html', form=form, user=user)
            
            old_username = user.username
            user.username = new_username
            log_action(current_user, f"修改用户 {old_username} 的用户名为 {new_username}")
            changes_made = True
            message_parts.append("用户名")

        new_email = form.email.data.strip() if form.email.data else None
        if new_email != user.email:
            if new_email:  # 如果提供了新邮箱
                existing_user = User.query.filter(User.email == new_email, User.id != user.id).first()
                if existing_user:
                    flash('邮箱已被其他用户使用', 'error')
                    return render_template('user_approve.html', form=form, user=user)
            
            old_email = user.email
            user.email = new_email
            log_action(current_user, f"修改用户 {user.username} 的邮箱")
            changes_made = True
            message_parts.append("邮箱")

        # 处理密码修改 - 只在提供了旧密码和新密码时才验证和更新
        if form.old_password.data or form.new_password.data:
            # 如果提供了旧密码但新密码为空
            if form.old_password.data and not form.new_password.data:
                flash('如果修改密码，必须填写新密码', 'error')
                return render_template('user_approve.html', form=form, user=user)
            
            # 如果提供了新密码但旧密码为空
            if form.new_password.data and not form.old_password.data:
                flash('要修改密码，必须提供当前密码', 'error')
                return render_template('user_approve.html', form=form, user=user)
            
            # 只有当两个字段都提供时才进行验证
            if form.old_password.data and form.new_password.data:
                if not check_password_hash(user.password, form.old_password.data):
                    flash('当前密码错误', 'error')
                    return render_template('user_approve.html', form=form, user=user)
                
                if len(form.new_password.data) < 6:
                    flash('新密码长度至少为6位', 'error')
                    return render_template('user_approve.html', form=form, user=user)
                
                user.password = generate_password_hash(form.new_password.data)
                changes_made = True
                message_parts.append("密码")
        
        # 处理状态修改
        if user.is_active != form.is_active.data:
            changes_made = True
            message_parts.append("激活状态")
        if user.is_admin != form.is_admin.data:
            changes_made = True
            message_parts.append("管理员权限")
        
        # 应用所有修改
        user.is_active = form.is_active.data
        user.is_admin = form.is_admin.data
        
        # 如果有修改才提交并显示消息
        if changes_made:
            db.session.commit()  # 统一提交一次
            message = "已更新" + "、".join(message_parts)
            flash(f'{message} 信息已保存', 'success')
        else:
            flash('没有检测到任何修改', 'info')
        
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
    page = request.args.get('page', 1, type=int)
    #logs = Log.query.order_by(Log.ts.desc()).paginate(page=page, per_page=20)

    from sqlalchemy.orm import joinedload
    logs = Log.query.options(joinedload(Log.sale))\
                   .order_by(Log.ts.desc())\
                   .paginate(page=page, per_page=20)

    return render_template('logs.html', logs=logs)

def migrate_log_sale_relations():
    """将历史销售记录与日志关联"""
    logs = Log.query.filter(Log.action.like('%销售:%') | Log.action.like('%进货:%')).all()
    for log in logs:
        # 从日志信息中提取销售ID（需要根据实际日志格式调整）
        # 此处仅为示例，实际实现需根据日志格式定制
        if 'ID:' in log.action:
            sale_id = int(log.action.split('ID:')[1].split()[0])
            log.sale_id = sale_id
    db.session.commit()

if __name__ == '__main__':
    with app.app_context():
        # 检查是否需要创建数据库
        db_initialized = False
        max_retries = 3
        retry_count = 0

        if not check_database_health():
            print("警告: 数据库连接存在问题，应用可能无法正常工作。")
        
        while not db_initialized and retry_count < max_retries:
            if app.config['SQLALCHEMY_DATABASE_URI'].startswith('postgresql'):
                db_initialized = create_database_if_not_exists()
            else:
                # 对于其他数据库类型，直接尝试连接
                db_initialized = True
            
            if not db_initialized:
                retry_count += 1
                if retry_count < max_retries:
                    print(f"数据库初始化失败，第 {retry_count} 次重试...")
                    import time
                    time.sleep(2)  # 等待2秒后重试
                else:
                    print("数据库初始化失败，应用无法启动。")
                    print("请手动创建数据库或检查数据库配置。")
                    exit(1)
        
        # 创建所有表
        try:
            print("创建数据库表...")
            db.create_all()
            print("数据库表创建完成。")
        except Exception as e:
            print(f"创建数据库表失败: {e}")
            print("请检查数据库连接和权限。")
            exit(1)

        # 确保至少有一个活跃的管理员账户
        try:
            active_admin_exists = User.query.filter_by(is_admin=True, is_active=True).first()
            if not active_admin_exists:
                # 尝试查找名为'admin'的用户
                admin_user = User.query.filter_by(username='admin').first()
                if admin_user:
                    # 如果找到，确保它是活跃的管理员
                    admin_user.is_admin = True
                    admin_user.is_active = True
                    db.session.commit()
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
                    db.session.commit()
                    print("创建了默认管理员账户: admin/admin")
        except Exception as e:
            print(f"初始化管理员账户失败: {e}")
            # 继续启动，因为管理员账户不是绝对必要的
        
        # 确保存在默认分类
        try:
            if not Category.query.first():
                default_category = Category(name="未分类")
                db.session.add(default_category)
                db.session.commit()
                print("创建了默认分类: 未分类")
        except Exception as e:
            print(f"初始化默认分类失败: {e}")
        
        print("应用启动完成。")
    
    app.run(debug=True)
