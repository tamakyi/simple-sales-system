import csv
import os
import os.path as op
import random
import re
import secrets
import smtplib
import string
import time
import datetime as dt
from collections import defaultdict
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from io import BytesIO, StringIO
from urllib.parse import parse_qs, urlparse

import openpyxl
from PIL import Image, ImageDraw, ImageFont
from dotenv import load_dotenv
from itsdangerous import URLSafeTimedSerializer
from sqlalchemy import (and_, case, create_engine, desc, func,
                        inspect as sa_inspect, text)
from sqlalchemy.orm import joinedload

from flask import (Flask, abort, flash, jsonify, make_response, redirect,
                   render_template, request, send_file, session, url_for)
from flask_login import (LoginManager, current_user, login_required, login_user,
                         logout_user)
from flask_wtf.csrf import CSRFProtect
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from forms import (CategoryForm, ForgotPasswordForm, LoginForm, ManualProductForm,
                   ProductForm, ProductImportForm, RegisterForm, ResetPasswordForm,
                   UserApproveForm)
from models import (DEFAULT_LOW_STOCK_THRESHOLD, Category, Log, Product, Sale,
                    User, db)
from utils import import_products_csv, sanitize_image_link


basedir = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(basedir, '.env'))

app = Flask(__name__)
csrf = CSRFProtect(app)

# 已知的示例/占位密钥。继续使用它们等于把会话签名与密码重置令牌的密钥公开在代码里。
_INSECURE_SECRET_VALUES = {
    '', 'fallback_secret_key', 'mysecretkey', 'another-secret-salt',
    'changeme', 'secret', 'your_secret_key',
}

def _resolve_secret(env_name):
    """读取密钥类配置；缺失或仍是示例值时生成随机密钥并显著告警。"""
    value = (os.getenv(env_name) or '').strip()
    if value not in _INSECURE_SECRET_VALUES:
        return value
    generated = secrets.token_hex(32)
    print("=" * 68)
    print(f"[安全警告] 未配置 {env_name}（或仍是示例值），已生成临时随机密钥。")
    print("           影响：重启后所有登录会话与密码重置链接失效；")
    print("                 多进程部署时各 worker 密钥不一致会导致登录状态异常。")
    print(f"           请在 .env 中设置 {env_name}=<64位随机字符串> 后重启。")
    print("=" * 68)
    return generated

def _validate_analyze_script(value):
    """校验分析脚本配置。

    该值以 |safe 注入页面，等于把 .env 的内容当 HTML 执行，因此只放行
    <script src="http(s)://..."></script> 与裸 http(s) 链接。
    """
    value = (value or '').strip()
    if not value:
        return ''
    if re.fullmatch(r'<script[^>]*\bsrc="https?://[^"]+"[^>]*>\s*</script>', value):
        return value
    if re.fullmatch(r'https?://\S+', value):
        return f'<script defer src="{value}"></script>'
    print("[安全警告] ANALYZE_SCRIPT 不是合法的 http(s) 脚本地址，已忽略该配置。")
    return ''

app.config['APP_TITLE'] = os.getenv('APP_TITLE', '狼的小卖部')
app.config['SECRET_KEY'] = _resolve_secret('SECRET_KEY')
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
app.config['SECURITY_PASSWORD_SALT'] = _resolve_secret('SECURITY_PASSWORD_SALT')
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('SQLALCHEMY_DATABASE_URI', '')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ECHO'] = False
app.config['DASHBOARD_ANNOUNCEMENT'] = os.getenv('DASHBOARD_ANNOUNCEMENT', '')
app.config['ANNOUNCEMENT_ENABLED'] = os.getenv('ANNOUNCEMENT_ENABLED', 'False').lower() == 'true'
app.config['ANALYZE_SCRIPT'] = _validate_analyze_script(os.getenv('ANALYZE_SCRIPT', ''))
app.config['ANALYZE_ENABLE'] = os.getenv('ANALYZE_ENABLE', 'False').lower() == 'true'
# 生产环境每次渲染都查模板时间戳纯属损耗，默认跟随 FLASK_DEBUG
app.config['TEMPLATES_AUTO_RELOAD'] = os.getenv(
    'TEMPLATES_AUTO_RELOAD', os.getenv('FLASK_DEBUG', 'False')).lower() == 'true'

# 会话 Cookie 安全：HTTPS 部署时在 .env 中设 SESSION_COOKIE_SECURE=True
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = os.getenv('SESSION_COOKIE_SAMESITE', 'Lax')
app.config['SESSION_COOKIE_SECURE'] = os.getenv('SESSION_COOKIE_SECURE', 'False').lower() == 'true'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=int(os.getenv('SESSION_LIFETIME_HOURS', 12)))

DB_TYPE_CHOICES = ('mysql', 'postgres')
_URI_SCHEME_TO_DB_TYPE = {
    'mysql': 'mysql',
    'postgres': 'postgres',
    'postgresql': 'postgres',
}

def resolve_db_type(uri):
    """确定数据库类型：以 .env 的 DB_TYPE 为准，并与 URI 协议交叉校验。"""
    declared = (os.getenv('DB_TYPE') or '').strip().lower()
    if declared and declared not in DB_TYPE_CHOICES:
        raise SystemExit(f"DB_TYPE 只能取 {' 或 '.join(DB_TYPE_CHOICES)}，当前值为: {declared}")

    scheme = uri.split('://', 1)[0].split('+', 1)[0].lower() if '://' in uri else ''
    detected = _URI_SCHEME_TO_DB_TYPE.get(scheme)

    # 两者都写却互相矛盾时直接退出：否则会带着错误的连接参数去连库，
    # 报出来的是驱动层的参数错误，很难定位。
    if declared and detected and declared != detected:
        raise SystemExit(
            f"配置冲突：DB_TYPE={declared}，但 SQLALCHEMY_DATABASE_URI 的协议是 {scheme}（属于 {detected}）。"
            "请将两者改为一致，例如 DB_TYPE=postgres 搭配 postgresql://..."
        )
    if detected:
        return detected
    if declared:
        return declared
    raise SystemExit(
        "无法确定数据库类型。请在 .env 中设置 DB_TYPE=mysql|postgres，"
        "或让 SQLALCHEMY_DATABASE_URI 以 mysql / postgresql 开头。"
    )

DB_TYPE = resolve_db_type(app.config['SQLALCHEMY_DATABASE_URI'])
DB_DEFAULT_PORT = 3306 if DB_TYPE == 'mysql' else 5432

_connect_args = {'connect_timeout': 10}
if DB_TYPE == 'postgres':
    _connect_args['application_name'] = 'simple_sales_system'

app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_size': 10,
    'max_overflow': 20,
    'pool_recycle': 3600,
    'pool_pre_ping': True,
    'connect_args': _connect_args,
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

def ensure_schema():
    """为既有数据库补齐新增列与索引（幂等，可安全重复调用）。

    db.create_all() 只创建缺失的表，既不给已存在的表加列，也不补索引；
    项目尚未引入 Alembic，因此启动时显式补齐，让老库平滑升级。
    """
    inspector = sa_inspect(db.engine)
    preparer = db.engine.dialect.identifier_preparer
    # PostgreSQL 的布尔默认值不能写 0，必须写 FALSE
    ddl_default = 'FALSE' if db.engine.dialect.name == 'postgresql' else '0'

    applied = []
    columns = (
        ('product', 'is_deleted', 'BOOLEAN NOT NULL DEFAULT ' + ddl_default),
        ('user', 'is_deleted', 'BOOLEAN NOT NULL DEFAULT ' + ddl_default),
        ('product', 'deleted_at', 'TIMESTAMP NULL'),
        ('user', 'deleted_at', 'TIMESTAMP NULL'),
        ('product', 'low_stock_threshold', 'INTEGER NULL'),
    )
    for table_name, column_name, column_type in columns:
        if not inspector.has_table(table_name):
            continue
        existing_columns = {col['name'] for col in inspector.get_columns(table_name)}
        if column_name in existing_columns:
            continue
        # 标识符必须按方言加引号：user 在 PostgreSQL 中是保留字
        ddl = "ALTER TABLE {} ADD COLUMN {} {}".format(
            preparer.quote(table_name), preparer.quote(column_name), column_type
        )
        db.session.execute(text(ddl))
        db.session.commit()
        applied.append('{}.{}'.format(table_name, column_name))

    # 索引与列同理。每次重新 inspect，避免刚提交的 DDL 让缓存失真。
    # 不在此处补 CheckConstraint：SQLite 不支持 ALTER TABLE ADD CONSTRAINT，
    # 且既有数据若含非法 type 会让启动直接失败，代价高于收益。
    indexes = (
        ('sale', 'ix_sale_product_id', ('product_id',)),
        ('sale', 'ix_sale_created_at', ('created_at',)),
        ('sale', 'ix_sale_type_created', ('type', 'is_reversed', 'created_at')),
        ('log', 'ix_log_ts', ('ts',)),
        ('product', 'ix_product_name', ('name',)),
    )
    for table_name, index_name, column_names in indexes:
        if not sa_inspect(db.engine).has_table(table_name):
            continue
        existing = {ix['name'] for ix in sa_inspect(db.engine).get_indexes(table_name)}
        if index_name in existing:
            continue
        columns_sql = ', '.join(preparer.quote(name) for name in column_names)
        ddl = "CREATE INDEX {} ON {} ({})".format(
            preparer.quote(index_name), preparer.quote(table_name), columns_sql
        )
        db.session.execute(text(ddl))
        db.session.commit()
        applied.append('{}@{}'.format(table_name, index_name))

    if applied:
        print("[schema] 已为既有数据库补齐: {}".format(', '.join(applied)))
    return applied

_schema_state = {'done': False, 'attempts': 0}

@app.before_request
def ensure_schema_once():
    """首个请求前补齐缺失列。覆盖 gunicorn 等不经过 __main__ 的启动方式。"""
    if _schema_state['done'] or _schema_state['attempts'] >= 3:
        return
    _schema_state['attempts'] += 1
    try:
        ensure_schema()
        _schema_state['done'] = True
    except Exception as e:
        db.session.rollback()
        print("[schema] 自动补列失败（第 {} 次）: {}".format(_schema_state['attempts'], e))

CAPTCHA_MAX_AGE_SECONDS = 600

def verify_captcha(submitted):
    """校验验证码；无论成功或失败都作废当前验证码，防止同一个码被反复复用。"""
    expected = session.pop('captcha', None)
    issued_at = session.pop('captcha_ts', None)
    if not expected or not submitted:
        return False
    if issued_at is not None and time.time() - issued_at > CAPTCHA_MAX_AGE_SECONDS:
        return False
    return expected.upper() == str(submitted).upper()

# 限流阈值。计数保存在进程内存中：多 worker 部署时每个 worker 各持一份，
# 防护强度会按 worker 数稀释，需要严格限流请改用 Redis。
REGISTER_MAX_ATTEMPTS = 10
REGISTER_WINDOW = timedelta(hours=1)
REGISTER_LOCK_DURATION = timedelta(hours=1)
LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW = timedelta(minutes=10)
LOGIN_LOCK_DURATION = timedelta(minutes=10)
RATE_LIMIT_MAX_KEYS = 5000

def rate_limit_prune(store, window):
    """淘汰过期键并限制字典规模，避免被刷时内存无上限增长。"""
    now = datetime.now()
    expired = [k for k, v in store.items()
               if (v['locked_until'] is None or now >= v['locked_until'])
               and now - v['first'] > window]
    for key in expired:
        store.pop(key, None)
    if len(store) > RATE_LIMIT_MAX_KEYS:
        oldest = sorted(store.items(), key=lambda kv: kv[1]['first'])
        for key, _ in oldest[:len(store) - RATE_LIMIT_MAX_KEYS]:
            store.pop(key, None)

def rate_limit_is_locked(store, key):
    entry = store.get(key)
    return bool(entry and entry['locked_until'] and datetime.now() < entry['locked_until'])

def rate_limit_record(store, key, max_attempts, window, lock_duration):
    now = datetime.now()
    entry = store.get(key)
    if entry is not None:
        if entry['locked_until'] and now < entry['locked_until']:
            return
        # 锁定期结束或计数窗口过期后重新计数。原实现从不清零，
        # 解锁后第一次尝试就会因历史计数仍达标而立刻再次锁定，等于永久封禁。
        if entry['locked_until'] or now - entry['first'] > window:
            entry = None
    if entry is None:
        entry = {'count': 0, 'first': now, 'locked_until': None}
    entry['count'] += 1
    if entry['count'] >= max_attempts:
        entry['locked_until'] = now + lock_duration
        entry['first'] = now
    store[key] = entry
    rate_limit_prune(store, window)

def is_register_locked(ip_address):
    return rate_limit_is_locked(register_attempts, ip_address)

def record_register_attempt(ip_address):
    rate_limit_record(register_attempts, ip_address,
                      REGISTER_MAX_ATTEMPTS, REGISTER_WINDOW, REGISTER_LOCK_DURATION)

def is_login_locked(username):
    return rate_limit_is_locked(login_attempts, username)

def get_lock_time_remaining(username):
    entry = login_attempts.get(username)
    if entry and entry['locked_until']:
        remaining = entry['locked_until'] - datetime.now()
        return max(0, int(remaining.total_seconds() / 60))
    return 0

def record_login_attempt(username, success):
    if success:
        login_attempts.pop(username, None)
    else:
        rate_limit_record(login_attempts, username,
                          LOGIN_MAX_ATTEMPTS, LOGIN_WINDOW, LOGIN_LOCK_DURATION)

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
    except Exception:
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
        
        # 465 是隐式 SSL 端口；其余端口按 SMTP_USE_TLS 决定是否显式 STARTTLS。
        # 原先只按端口判断，SMTP_USE_TLS 这个配置项读了却从未生效。
        if app.config['SMTP_PORT'] == 465:
            server = smtplib.SMTP_SSL(app.config['SMTP_SERVER'], app.config['SMTP_PORT'])
        else:
            server = smtplib.SMTP(app.config['SMTP_SERVER'], app.config['SMTP_PORT'])
            server.ehlo()
            if app.config['SMTP_USE_TLS']:
                if server.has_extn('STARTTLS'):
                    server.starttls()
                    server.ehlo()
                else:
                    app.logger.warning("SMTP 服务器不支持 STARTTLS，本次连接未加密")

        # set_debuglevel(1) 会打印整段 SMTP 交互（含认证过程），只在调试模式打开
        server.set_debuglevel(1 if app.debug else 0)

        server.login(app.config['SMTP_USERNAME'], app.config['SMTP_PASSWORD'])
        server.send_message(msg)
        server.quit()
        return True

    except smtplib.SMTPConnectError:
        app.logger.exception("SMTP 连接失败")
        return False
    except smtplib.SMTPAuthenticationError:
        app.logger.exception("SMTP 认证失败")
        return False
    except smtplib.SMTPServerDisconnected:
        app.logger.exception("SMTP 服务器断开连接")
        return False
    except Exception:
        app.logger.exception("发送邮件失败")
        return False

@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    """忘记密码页面"""
    form = ForgotPasswordForm()
    
    if form.validate_on_submit():
        # 检查验证码
        if not verify_captcha(form.captcha.data):
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
        except Exception:
            db.session.rollback()
            app.logger.exception("重置密码失败")
            flash('重置密码失败，请稍后重试或联系管理员', 'error')
    
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

def parse_db_uri():
    parsed = urlparse(app.config['SQLALCHEMY_DATABASE_URI'])
    return {
        'name': parsed.path.lstrip('/'),
        'user': parsed.username,
        'password': parsed.password or '',
        'host': parsed.hostname,
        'port': parsed.port or DB_DEFAULT_PORT,
    }

def decode_db_error(exc):
    """还原数据库错误消息原文。

    libpq 在握手阶段按服务端 locale 输出本地化消息（实测为
    Chinese (Simplified)_China.936 / GBK），而 psycopg2 固定按 UTF-8 解码，
    于是抛 UnicodeDecodeError 并丢掉原文与错误码。这里从异常携带的原始字节还原。
    """
    if isinstance(exc, UnicodeDecodeError):
        raw = exc.object
        for encoding in ('utf-8', 'gb18030', 'latin-1'):
            try:
                return raw.decode(encoding).strip()
            except UnicodeDecodeError:
                continue
        return repr(raw)
    return str(exc).strip() or type(exc).__name__

def create_database_if_not_exists():
    """确保目标库存在，不存在则创建。

    不靠「连接目标库失败」反推库是否存在：该路径的错误受服务端 locale 影响、
    且 psycopg2 解码失败时会丢掉错误码，判断不可靠。改为连系统库查 catalog。
    """
    info = parse_db_uri()
    db_name = info['name']

    if not db_name or not all(c.isalnum() or c == '_' for c in db_name):
        print(f"数据库名 '{db_name}' 为空或含非法字符，拒绝自动创建，请检查 SQLALCHEMY_DATABASE_URI。")
        return False

    try:
        with create_engine(app.config['SQLALCHEMY_DATABASE_URI']).connect():
            pass
        return True
    except Exception as target_error:
        target_message = decode_db_error(target_error)

    auth = f"{info['user']}:{info['password']}@" if info['password'] else f"{info['user']}@"
    host = f"{info['host']}:{info['port']}"

    try:
        # CREATE DATABASE 不能在事务块中执行，这条连接必须 AUTOCOMMIT。
        if DB_TYPE == 'mysql':
            engine = create_engine(f"mysql+pymysql://{auth}{host}/", isolation_level='AUTOCOMMIT')
            with engine.connect() as conn:
                conn.execute(text(f"CREATE DATABASE IF NOT EXISTS `{db_name}` "
                                  "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"))
        else:
            engine = create_engine(f"postgresql://{auth}{host}/postgres", isolation_level='AUTOCOMMIT')
            with engine.connect() as conn:
                if conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :name"),
                                {'name': db_name}).scalar():
                    print(f"数据库 '{db_name}' 已存在，但连接失败: {target_message}")
                    print(f"请检查账户 '{info['user']}' 是否具备该库的访问权限。")
                    return False
                conn.execute(text(f'CREATE DATABASE "{db_name}";'))
        print(f"数据库 '{db_name}' 已就绪。")
        return True
    except Exception as e:
        print(f"连接数据库服务失败: {decode_db_error(e)}")
        print("请检查数据库服务是否启动，以及连接信息、网络是否正常。")
        return False

def log_action(user, action, sale=None):
    """把操作日志加入当前事务，由调用方统一 commit。

    原实现在这里自行 commit，会在业务变更尚未完成时就提交整个会话，
    一旦后续步骤失败便无法回滚。改用 flush 后，调用方必须紧随其后 commit。
    """
    # action 列为 String(255)，批量操作拼接出的长文本在 MySQL 严格模式下会触发
    # "Data too long"（1406）并导致整个事务回滚，这里统一截断兜底。
    if action and len(action) > 250:
        action = action[:247] + '...'
    db.session.add(Log(user_id=user.id, action=action, sale=sale))
    db.session.flush()

@app.errorhandler(404)
def not_found_error(error):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_error(error):
    return render_template('500.html'), 500

@login_manager.user_loader
def load_user(user_id):
    user = db.session.get(User, int(user_id))
    # 软删除的账户视为不存在，让既有登录会话立即失效
    if user is None or user.is_deleted:
        return None
    return user

def get_date_range(start_date, end_date):
    """把日期区间转成包含首尾整天的 datetime 区间。

    用 dt.time.max 而不是 23:59:59，否则带微秒的时间戳会被漏掉。
    """
    return (dt.datetime.combine(start_date, dt.time.min),
            dt.datetime.combine(end_date, dt.time.max))

def today_range():
    today = datetime.now().date()
    return get_date_range(today, today)

def parse_date_arg(name):
    """读取 ?name=YYYY-MM-DD 的日期参数，缺失或非法一律返回 None。"""
    value = (request.args.get(name) or '').strip()
    if not value:
        return None
    try:
        return dt.datetime.strptime(value, '%Y-%m-%d').date()
    except ValueError:
        return None

def product_stock_expr():
    """SQL 侧的库存取值，与 Product.effective_stock 同口径（NULL 视作 0）。

    不共用这个表达式就会出现「属性说断货、SQL 却查不出来」的两套结论。
    """
    return func.coalesce(Product.stock, 0)

def low_stock_condition():
    """待补货的 SQL 条件：库存 <= 该商品的预警阈值（未设置则用默认值）。

    阈值是逐行的列，无法预先算成常量，故用 coalesce 在 SQL 侧取值。
    """
    return (product_stock_expr()
            <= func.coalesce(Product.low_stock_threshold, DEFAULT_LOW_STOCK_THRESHOLD))

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

@app.context_processor
def inject_page_url():
    """分页宏的页码链接构造器：沿用当前查询串，只替换 page。
    此前各模板各自 url_for 显式传参或手拼查询串，新增筛选条件时容易漏改某一处。"""
    def page_url(page):
        args = request.args.to_dict(flat=True)
        args.pop('page', None)
        if request.endpoint is None:
            return f'?page={int(page)}'
        return url_for(request.endpoint, page=page, **args)
    return {'page_url': page_url}

# 添加日期格式化过滤器
@app.template_filter('dateformat')
def dateformat(value, format='%Y-%m-%d'):
    if value is None:
        return ""
    return value.strftime(format)

@app.route('/')
@login_required
def dashboard():
    page = request.args.get('page', 1, type=int)

    today = dt.date.today()
    # 起止日期各自独立回退到今天，只填一侧也能生效
    end_date = min(parse_date_arg('end_date') or today, today)
    start_date = min(parse_date_arg('start_date') or today, end_date)

    selected_start, selected_end = get_date_range(start_date, end_date)
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

    # 昨日/选定区间/历史总额原先各跑一次全表聚合，合并为一次条件聚合。
    # today_total 原本也在算，但没有任何模板消费它，故不再保留。
    totals = db.session.query(
        func.sum(case((Sale.created_at.between(y_start, y_end), Sale.amount), else_=0)),
        func.sum(case((Sale.created_at.between(selected_start, selected_end), Sale.amount), else_=0)),
        func.sum(Sale.amount),
    ).filter(Sale.type == 'out', Sale.is_reversed == False).one()
    yesterday_total, selected_date_total, all_total = (value or 0 for value in totals)
    
    # 选定日期范围的销售流水。原先硬编码 limit(20)，更早的记录无法查看。
    sales = Sale.query.filter(
        Sale.type=='out',
        Sale.is_reversed == False,
        Sale.created_at >= selected_start,
        Sale.created_at <= selected_end
    ).order_by(Sale.created_at.desc()).paginate(page=page, per_page=20)

    # 库存预警：一次条件聚合算出三档计数（负库存/断货/待补货），不逐商品判定。
    stock_counts = db.session.query(
        func.sum(case((product_stock_expr() < 0, 1), else_=0)),
        func.sum(case((product_stock_expr() == 0, 1), else_=0)),
        func.sum(case((and_(product_stock_expr() > 0, low_stock_condition()), 1), else_=0)),
    ).filter(Product.is_deleted == False).one()
    stock_alert = {
        'negative': int(stock_counts[0] or 0),
        'out': int(stock_counts[1] or 0),
        'low': int(stock_counts[2] or 0),
    }
    stock_alert['total'] = stock_alert['negative'] + stock_alert['out'] + stock_alert['low']
    # 最紧急的排前面：负数 → 0 → 逼近阈值
    low_stock_items = Product.query.filter(
        Product.is_deleted == False, low_stock_condition()
    ).order_by(product_stock_expr().asc()).limit(5).all()

    return render_template('dashboard.html',
                           sale_ranks=sale_ranks,
                           cat_names=cat_names,
                           cat_amounts=cat_amounts,
                           yesterday_total=yesterday_total,
                           selected_date_total=selected_date_total,
                           all_total=all_total,
                           sales=sales,
                           stock_alert=stock_alert,
                           low_stock_items=low_stock_items,
                           start_date=start_date,
                           end_date=end_date,
                           datetime=dt)

CAPTCHA_MIN_INTERVAL = 1.0
_captcha_hits = {}

def captcha_throttled(ip):
    """同一 IP 在 CAPTCHA_MIN_INTERVAL 秒内重复请求即视为刷取。

    每次 /captcha 都要加载字体并绘图，无条件放开等于提供了一个廉价的 CPU 消耗点。
    """
    now = time.time()
    if len(_captcha_hits) > RATE_LIMIT_MAX_KEYS:
        cutoff = now - CAPTCHA_MIN_INTERVAL
        for key in [k for k, ts in _captcha_hits.items() if ts < cutoff]:
            _captcha_hits.pop(key, None)
    last = _captcha_hits.get(ip)
    _captcha_hits[ip] = now
    return last is not None and now - last < CAPTCHA_MIN_INTERVAL

@app.route('/captcha')
def captcha():
    """生成验证码图片"""
    if captcha_throttled(request.remote_addr or 'unknown'):
        abort(429)

    captcha_text = ''.join(random.choices(string.ascii_uppercase + string.digits, k=5))
    session['captcha'] = captcha_text
    session['captcha_ts'] = time.time()
    
    width, height = 180, 38
    image = Image.new('RGB', (width, height), color=(255, 255, 255))
    draw = ImageDraw.Draw(image)
    
    # 字体按平台依次回退，全部不可用时退回 PIL 内置位图字体
    font = None
    for font_path in ("arial.ttf",
                      "/usr/share/fonts/truetype/Arial.ttf",
                      "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        try:
            font = ImageFont.truetype(font_path, 24)
            break
        except Exception:
            continue
    if font is None:
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
    
    buffer = BytesIO()
    image.save(buffer, 'PNG')
    buffer.seek(0)

    # 验证码不能被浏览器或中间层缓存，否则同一张图可能被反复复用
    response = send_file(buffer, mimetype='image/png')
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return response

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
        if not verify_captcha(form.captcha.data):
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
        if not verify_captcha(form.captcha.data):
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
@login_required
def debug_email_test():
    """邮件配置测试页面（仅管理员，且仅在调试模式下可用）"""
    check_admin()
    if not app.debug:
        abort(404)
    
    return render_template('email_test.html')

@app.route('/debug/send-test-email', methods=['POST'])
@login_required
def debug_send_test_email():
    """发送测试邮件（仅管理员，且仅在调试模式下可用）"""
    # 此前该接口只靠 app.debug 判断，而 app.run(debug=True) 让它恒为真，
    # 且没有登录校验 —— 等于任何人都能借此接口向任意邮箱发信。
    check_admin()
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
    # 待补货视图：dashboard 的预警条链接进来，让提示可直接落到要处理的商品上
    low_stock_only = request.args.get('stock') == 'low'
    query = Product.query.filter(Product.is_deleted == False)
    if low_stock_only:
        query = query.filter(low_stock_condition())
    if keyword:
        query = query.filter(Product.name.like(f'%{keyword}%'))
    if category_id:  # 如果有分类ID则过滤
        query = query.filter_by(category_id=category_id)
    products = query.order_by(Product.id.desc()).paginate(page=page, per_page=PER_PAGE)

    # 预加载分类数据用于下拉菜单
    categories = Category.query.all()

    return render_template('products.html', products=products, categories=categories,
                           keyword=keyword, category_id=category_id,
                           low_stock_only=low_stock_only)

@app.route('/products/edit/<int:pid>', methods=['GET', 'POST'])
@login_required
def product_edit(pid):
    check_admin()
    prod = db.session.get(Product, pid)
    if prod is None or prod.is_deleted:
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
        prod.low_stock_threshold = form.low_stock_threshold.data
        prod.category_id = form.category.data
        img_path = sanitize_image_link(form.image_link.data)
        if img_path is None:
            flash('图片链接必须以 http:// 或 https:// 开头', 'error')
            return render_template('product_edit.html', form=form, product=prod)
        img = form.image.data
        if not img_path and img:
            filename = secure_filename(img.filename)
            filename = dt.datetime.now().strftime('%Y%m%d%H%M%S_') + filename
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            img.save(file_path)
            img_path = 'uploads/' + filename
        if img_path:
            prod.image = img_path
        log_action(current_user, f"编辑商品:{prod.name}")
        db.session.commit()
        flash('保存成功')
        return redirect(url_for('products'))
    return render_template('product_edit.html', form=form, product=prod)

@app.route('/products/delete/<int:pid>', methods=['POST'])
@login_required
def delete_product(pid):
    check_admin()
    prod = db.session.get(Product, pid)
    if prod is None or prod.is_deleted:
        abort(404)
    # 软删除：只打标记、保留数据行。这样 Sale.product_id 与 Log 的外键始终有效，
    # 既不会在 MySQL/PG 上触发外键冲突，也不会让导出的历史流水断链。
    prod.is_deleted = True
    prod.deleted_at = datetime.now()
    log_action(current_user, f"删除商品:{prod.name}")
    db.session.commit()
    flash('商品已删除')
    return redirect(url_for('products'))

@app.route('/products/batch_delete', methods=['POST'])
@login_required
def batch_delete_products():
    check_admin()

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
            if product and not product.is_deleted:
                deleted_names.append(product.name)

                # 软删除：不再删除销售记录。原先此处会物理删除 Sale，既与单个删除
                # 入口行为不一致，也抹掉了财务历史。
                product.is_deleted = True
                product.deleted_at = datetime.now()
                deleted_count += 1

        if deleted_count:
            # 不再把所有商品名拼进日志：action 列只有 255 字符，批量大时会超长。
            log_action(current_user, f"批量删除商品 {deleted_count} 个: {', '.join(deleted_names)[:150]}")
        db.session.commit()
        flash(f'成功删除 {deleted_count} 个商品', 'success')

    except Exception as e:
        db.session.rollback()
        app.logger.exception("批量删除商品失败")
        flash('删除商品时出错，请查看服务端日志', 'error')

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
            result = import_products_csv(file)
            if result.imported:
                log_action(current_user, f"批量导入商品: {result.summary()}")
                db.session.commit()
            if result.errors:
                detail = '；'.join(result.errors[:3])
                if len(result.errors) > 3:
                    detail += f'…（共 {len(result.errors)} 条）'
                flash(f'CSV 问题：{detail}', 'warning')
            if result.note:
                flash(result.note, 'info')
            if result.imported:
                flash(f'CSV 导入完成：{result.summary()}', 'success')
            else:
                flash('CSV 没有导入任何商品，请检查文件内容', 'error')
        except Exception:
            db.session.rollback()
            app.logger.exception("CSV 导入失败")
            flash('CSV导入失败，请查看服务端日志', 'error')
        return redirect(url_for('product_import'))

    # 处理手动添加商品
    if manual_form.validate_on_submit() and manual_form.submit.data:
        try:
            # 手动入口不允许重名：否则一次同名 CSV 导入会静默覆盖它的价格与库存
            if Product.query.filter_by(name=manual_form.name.data, is_deleted=False).first():
                flash(f'已存在未删除的同名商品「{manual_form.name.data}」，请改用商品编辑', 'error')
                return render_template('product_import.html', csv_form=csv_form, manual_form=manual_form)

            # 创建新商品
            product = Product(
                name=manual_form.name.data,
                price=manual_form.price.data,
                stock=manual_form.stock.data,
                low_stock_threshold=manual_form.low_stock_threshold.data,
                category_id=manual_form.category.data
            )

            # 处理图片
            img_path = sanitize_image_link(manual_form.image_link.data)
            if img_path is None:
                flash('图片链接必须以 http:// 或 https:// 开头', 'error')
                return render_template('product_import.html', csv_form=csv_form, manual_form=manual_form)
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
            db.session.flush()
            log_action(current_user, f"手动添加商品: {product.name} (ID:{product.id})")
            db.session.commit()
            flash('商品添加成功', 'success')
            return redirect(url_for('product_import'))

        except Exception:
            db.session.rollback()
            app.logger.exception("手动添加商品失败")
            flash('商品添加失败，请查看服务端日志', 'error')

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
    # 必须带 UTF-8 BOM：否则中文 Windows 的 Excel 双击打开会按 ANSI/GBK 猜测，
    # 表头显示成乱码，用户一保存就把文件存成 GBK，再上传就报编码错误。
    response = make_response(output.getvalue().encode('utf-8-sig'))
    response.headers["Content-Disposition"] = "attachment; filename=product_import_template.csv"
    response.headers["Content-type"] = "text/csv; charset=utf-8"

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
            log_action(current_user, f"添加分类:{form.name.data}")
            db.session.commit()
            flash('添加成功')
        return redirect(url_for('categories'))
    cats = Category.query.all()
    return render_template('categories.html', form=form, categories=cats)

@app.route('/categories/delete/<int:cat_id>', methods=['POST'])
@login_required
def delete_category(cat_id):
    check_admin()
    cat = db.session.get(Category, cat_id)
    if cat is None:
        abort(404)
    
    # 检查是否有商品使用该分类（只算未删除的商品）
    product_count = Product.query.filter_by(category_id=cat_id, is_deleted=False).count()
    if product_count > 0:
        flash(f'该分类下有 {product_count} 个商品，无法删除', 'error')
        return redirect(url_for('categories'))
    
    # 软删除的商品行仍持有 category_id，直接删分类会触发外键冲突。
    # 这些商品已不可见，解除其分类归属即可。
    Product.query.filter_by(category_id=cat_id, is_deleted=True).update(
        {'category_id': None}, synchronize_session=False
    )
    db.session.delete(cat)
    log_action(current_user, f"删除分类:{cat.name}")
    db.session.commit()
    flash('已删除', 'success')
    return redirect(url_for('categories'))

@app.route('/categories/batch', methods=['POST'])
@login_required
def batch_categories():
    """批量操作分类"""
    check_admin()

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
                    # 检查是否有商品使用该分类（只算未删除的商品）
                    product_count = Product.query.filter_by(category_id=cat_id, is_deleted=False).count()
                    if product_count > 0:
                        flash(f'分类 "{category.name}" 下有 {product_count} 个商品，无法删除', 'warning')
                        continue
                    
                    # 先解除软删除商品的分类归属，避免删分类时外键冲突
                    Product.query.filter_by(category_id=cat_id, is_deleted=True).update(
                        {'category_id': None}, synchronize_session=False
                    )
                    deleted_names.append(category.name)
                    db.session.delete(category)
                    deleted_count += 1
            
            if deleted_count > 0:
                log_action(current_user, f"批量删除分类 {deleted_count} 个: {', '.join(deleted_names)[:150]}")
                db.session.commit()
                flash(f'成功删除 {deleted_count} 个分类', 'success')
            else:
                flash('没有分类被删除', 'info')

    except Exception as e:
        db.session.rollback()
        app.logger.exception("批量操作分类失败")
        flash('操作失败，请查看服务端日志', 'error')

    return redirect(url_for('categories'))

@app.route('/categories/edit/<int:cat_id>', methods=['POST'])
@login_required
def edit_category(cat_id):
    """编辑分类"""
    check_admin()

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
        log_action(current_user, f"修改分类: {old_name} -> {new_name}")
        db.session.commit()
        flash('分类修改成功', 'success')
    except Exception:
        db.session.rollback()
        app.logger.exception("修改分类失败")
        flash('修改失败，请查看服务端日志', 'error')
    
    return redirect(url_for('categories'))

@app.route('/sales')
@login_required
def sales():
    page = request.args.get('page', 1, type=int)
    keyword = request.args.get('keyword', '')  # 获取搜索关键词
    category_id = request.args.get('category_id', type=int)  # 获取分类筛选
    cats = Category.query.all()
    
    # 查询并分页
    query = Product.query.filter(Product.is_deleted == False).order_by(Product.id.desc())
    if keyword:
        query = query.filter(Product.name.like(f'%{keyword}%'))
    if category_id:
        query = query.filter_by(category_id=category_id)

    products = query.paginate(page=page, per_page=PER_PAGE)
    
    # 按分类分组
    grouped_products = defaultdict(list)
    for product in products.items:
        grouped_products[product.category.name].append(product)

    # 原先每个商品要跑 4 次独立聚合（全部/今日 × 金额/数量），一页 10 个商品就是 40 条 SQL。
    # 合并为两次 group_by，指标在 Python 侧拆列。
    stat_map = {p.id: {'all_sale': 0, 'today_sale': 0, 'all_qty': 0, 'today_qty': 0}
                for p in products.items}
    product_ids = list(stat_map)

    if product_ids:
        today_start, today_end = today_range()
        base_filters = (Sale.product_id.in_(product_ids),
                        Sale.type == 'out',
                        Sale.is_reversed == False)

        for row in db.session.query(
                Sale.product_id, func.sum(Sale.amount), func.sum(Sale.quantity)
        ).filter(*base_filters).group_by(Sale.product_id):
            stat_map[row[0]]['all_sale'] = row[1] or 0
            stat_map[row[0]]['all_qty'] = row[2] or 0

        for row in db.session.query(
                Sale.product_id, func.sum(Sale.amount), func.sum(Sale.quantity)
        ).filter(*base_filters,
                 Sale.created_at >= today_start,
                 Sale.created_at <= today_end
        ).group_by(Sale.product_id):
            stat_map[row[0]]['today_sale'] = row[1] or 0
            stat_map[row[0]]['today_qty'] = row[2] or 0
    
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
    query = Product.query.filter(Product.is_deleted == False).order_by(Product.id.desc())
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
    if prod is None or prod.is_deleted:
        abort(404)

    # 数量必须是 >=1 的整数。原先直接 int(request.form.get("quantity")) 且没有下界校验，
    # 提交 quantity=-5 会让「销售」反而增加库存、并写入负金额流水。
    try:
        qty = int(request.form.get("quantity", ""))
    except (TypeError, ValueError):
        qty = None
    if qty is None or qty < 1 or qty > 1000000:
        flash("数量必须为 1 以上的整数")
        # 根据来源页面决定重定向
        if request.form.get("source_page") == "sales_simple" or 'sales-simple' in (request.referrer or ''):
            return redirect(url_for('sales_simple'))
        return redirect(url_for('sales'))
    
    # 获取来源页面信息，用于操作完成后重定向
    source_page = request.form.get("source_page")
    referer = request.referrer or ''
    redirect_to_simple = source_page == "sales_simple" or 'sales-simple' in referer
    
    if "submit_in" in request.form:
        # 原子自增。原先 prod.stock += qty 是「读-改-写」，两个并发请求会互相覆盖，
        # 两次进货只记一次库存。
        db.session.query(Product).filter(Product.id == prod.id).update(
            {'stock': Product.stock + qty}, synchronize_session=False)
        sale = Sale(product_id=prod.id, quantity=qty, type='in', user_id=current_user.id, amount=0)
        db.session.add(sale)
        log_action(current_user, f"进货:{prod.name} 数量:{qty}", sale=sale)
        db.session.commit()
        flash('进货成功')
    elif "submit_out" in request.form:
        # 条件更新抢占出库权。原先「先读 prod.stock 再判断」会让并发的两个请求同时通过，
        # 各扣一次却只生效一次——账面库存多于实物（双击提交即可触发）。
        # 把 stock >= qty 放进 WHERE，判断与扣减才是同一个原子操作。
        claimed = db.session.query(Product).filter(
            Product.id == prod.id,
            Product.is_deleted == False,
            Product.stock >= qty,
        ).update({'stock': Product.stock - qty}, synchronize_session=False)
        if not claimed:
            db.session.rollback()
            flash('库存不足')
            if redirect_to_simple:
                return redirect(get_redirect_url('sales_simple', {'page': 1}))
            return redirect(get_redirect_url('sales', {'page': 1}))
        amount = round(qty * prod.price, 2)
        sale = Sale(product_id=prod.id, quantity=qty, type='out', user_id=current_user.id, amount=amount)
        db.session.add(sale)
        log_action(current_user, f"销售:{prod.name} 数量:{qty}", sale=sale)
        db.session.commit()
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
    if prod is None or prod.is_deleted:
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
    sale = db.session.get(Sale, sale_id)
    if sale is None:
        abort(404)
    product = sale.product
    
    # 检查操作权限（管理员或操作者本人）
    if not (current_user.is_admin or current_user.id == sale.user_id):
        flash('无权限执行此操作', 'danger')
        return redirect(url_for('logs'))

    # 商品已被软删除（或历史遗留的孤儿流水）时不得再改动库存
    if product is None or product.is_deleted:
        flash('该流水对应的商品已删除，无法撤回', 'danger')
        return redirect(url_for('logs'))

    if sale.is_reversed:
        flash('该操作已被撤回，无需重复操作', 'warning')
        return redirect(url_for('logs'))
    
    try:
        # 以条件更新抢占撤回权：并发的重复提交里只有一个能把 is_reversed 从 False 改成 True，
        # 其余影响 0 行。原先「先读后写」的判断会让两个请求同时通过，库存被恢复两次。
        claimed = db.session.query(Sale).filter(
            Sale.id == sale_id, Sale.is_reversed == False
        ).update({'is_reversed': True}, synchronize_session=False)
        if not claimed:
            db.session.rollback()
            flash('该操作已被撤回，无需重复操作', 'warning')
            return redirect(url_for('logs'))

        if sale.type == 'in':
            # 撤回进货要把库存减回去。货可能早已卖出，此时撤回会凭空制造负库存，
            # 故把下界判断放进 WHERE（理由同 sales_operate 的出库）；失败则连撤回标记一起回滚。
            restored = db.session.query(Product).filter(
                Product.id == product.id,
                Product.stock >= sale.quantity,
            ).update({'stock': Product.stock - sale.quantity}, synchronize_session=False)
            if not restored:
                db.session.rollback()
                flash(f'撤回后库存将为负数（当前 {product.stock}，需扣回 {sale.quantity}），已阻止', 'danger')
                return redirect(url_for('logs'))
        elif sale.type == 'out':
            db.session.query(Product).filter(Product.id == product.id).update(
                {'stock': Product.stock + sale.quantity}, synchronize_session=False)

        log_action(current_user, f"撤回了{sale.type}操作: {product.name} x {sale.quantity}", sale=sale)
        db.session.commit()
        flash('撤回成功', 'success')
    except Exception:
        db.session.rollback()
        app.logger.exception("撤回操作失败")
        flash('撤回失败，请查看服务端日志', 'danger')
    
    return redirect(url_for('logs'))


@app.route('/export')
@login_required
def export():
    # 导出包含全部商品、金额与操作人，权限模型须与 logs/users 保持一致
    check_admin()

    query = Sale.query.options(
        joinedload(Sale.product), joinedload(Sale.user)
    ).filter(Sale.is_reversed == False)

    start_date = parse_date_arg('start_date')
    end_date = parse_date_arg('end_date')
    if start_date:
        query = query.filter(Sale.created_at >= get_date_range(start_date, start_date)[0])
    if end_date:
        query = query.filter(Sale.created_at <= get_date_range(end_date, end_date)[1])

    # write_only + yield_per 避免把全部流水与关联对象一次性载入内存；
    # joinedload 把商品/操作人一次取回，消除逐行懒加载（原实现的 N+1）。
    wb = openpyxl.Workbook(write_only=True)
    ws = wb.create_sheet('销售流水')
    ws.append(['商品', '数量', '类型', '金额', '操作人', '时间'])
    for s in query.order_by(Sale.id).yield_per(500):
        # 历史遗留数据里可能存在商品/用户行已不存在的孤儿流水，这里做空值兜底
        product_name = s.product.name if s.product else '(已删除商品)'
        username = s.user.username if s.user else '(已删除用户)'
        created_at = s.created_at.strftime('%Y-%m-%d %H:%M') if s.created_at else ''
        ws.append([product_name, s.quantity, '进货' if s.type == 'in' else '销售',
                   s.amount, username, created_at])

    bio = BytesIO()
    wb.save(bio)
    bio.seek(0)
    return send_file(bio, as_attachment=True, download_name='sales.xlsx',
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@app.route('/users')
@login_required
def users():
    check_admin()
    users = User.query.filter(User.is_deleted == False).all()
    return render_template('users.html', users=users)

@app.route('/users/approve/<int:uid>', methods=['GET', 'POST'])
@login_required
def user_approve(uid):
    check_admin()
    user = db.session.get(User, uid)
    if user is None or user.is_deleted:
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
        
        # 最后一个管理员保护：降权或停用唯一的管理员会让系统失去所有管理入口，
        # 只能直接改库恢复。
        if user.is_admin and user.is_active and (
                not form.is_admin.data or not form.is_active.data):
            active_admins = User.query.filter_by(is_admin=True, is_active=True, is_deleted=False).count()
            if active_admins <= 1:
                flash('系统必须保留至少一个启用状态的管理员，无法降权或停用该账户', 'error')
                return render_template('user_approve.html', form=form, user=user)

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
    if user is None or user.is_deleted:
        flash('用户不存在', 'error')
        return redirect(url_for('users'))
    
    # 至少保留一个「启用状态的管理员」：只统计管理员数量会把已停用的账户也算进来，
    # 可能删掉最后一个还能登录的管理员。
    if user.is_admin and user.is_active:
        active_admins = User.query.filter_by(is_admin=True, is_active=True, is_deleted=False).count()
        if active_admins <= 1:
            flash('必须至少保留一个启用状态的管理员账户', 'error')
            return redirect(url_for('users'))

    username = user.username

    # 软删除：保留数据行，避免 Sale.user_id / Log.user_id 外键悬空。
    # 原先被注释掉的 Sale/Log 清理逻辑就是 /export 崩溃的根因——现在不需要清，
    # 因为行还在，历史流水的操作人始终可读。
    user.is_deleted = True
    user.deleted_at = datetime.now()
    user.is_active = False
    log_action(current_user, f"删除用户: {username}")
    db.session.commit()
    flash(f'用户 {username} 已删除', 'success')
    return redirect(url_for('users'))

@app.route('/recycle-bin')
@login_required
def recycle_bin():
    check_admin()
    deleted_products = Product.query.filter(Product.is_deleted == True)\
        .order_by(Product.deleted_at.desc(), Product.id.desc()).all()
    deleted_users = User.query.filter(User.is_deleted == True)\
        .order_by(User.deleted_at.desc(), User.id.desc()).all()
    return render_template('recycle_bin.html',
                           deleted_products=deleted_products,
                           deleted_users=deleted_users)

@app.route('/recycle-bin/restore', methods=['POST'])
@login_required
def recycle_bin_restore():
    """从回收站还原商品或用户。"""
    check_admin()
    kind = request.form.get('kind', '')
    ids = [int(part) for part in request.form.get('ids', '').split(',') if part.strip().isdigit()]

    if not ids:
        flash('请先选择要还原的条目', 'warning')
        return redirect(url_for('recycle_bin'))

    if kind == 'product':
        model, name_attr, label = Product, 'name', '商品'
    elif kind == 'user':
        model, name_attr, label = User, 'username', '用户'
    else:
        flash('未知的还原类型', 'error')
        return redirect(url_for('recycle_bin'))

    restored = 0
    conflicts = []
    for item_id in ids:
        item = db.session.get(model, item_id)
        if item is None or not item.is_deleted:
            continue
        # 同名记录可能是删除之后新建的，直接还原会撞唯一约束或产生重名
        duplicated = model.query.filter(
            getattr(model, name_attr) == getattr(item, name_attr),
            model.is_deleted == False,
            model.id != item.id
        ).first()
        if duplicated:
            conflicts.append(getattr(item, name_attr))
            continue
        item.is_deleted = False
        item.deleted_at = None
        restored += 1

    if restored:
        log_action(current_user, f"回收站还原{label} {restored} 项")
        db.session.commit()
        flash(f'已还原 {restored} 个{label}', 'success')
        if kind == 'user':
            flash('还原后的用户处于「未启用」状态，需在用户管理中启用后才能登录', 'info')
    if conflicts:
        flash(f'以下{label}因已存在同名记录未能还原: {", ".join(conflicts[:10])}', 'warning')
    if not restored and not conflicts:
        flash('没有可还原的条目', 'info')
    return redirect(url_for('recycle_bin'))

@app.route('/logs')
@login_required
def logs():
    check_admin()
    page = request.args.get('page', 1, type=int)
    logs = Log.query.options(joinedload(Log.sale))\
                   .order_by(Log.ts.desc())\
                   .paginate(page=page, per_page=20)

    return render_template('logs.html', logs=logs)

if __name__ == '__main__':
    with app.app_context():
        print(f"数据库类型: {DB_TYPE}")

        for attempt in range(1, 4):
            if create_database_if_not_exists():
                break
            if attempt < 3:
                print(f"数据库初始化失败，2 秒后重试（第 {attempt}/3 次）...")
                time.sleep(2)
        else:
            print("数据库初始化失败，应用无法启动。请检查数据库配置与权限。")
            raise SystemExit(1)

        try:
            db.create_all()
            ensure_schema()
        except Exception as e:
            print(f"创建数据库表失败: {decode_db_error(e)}")
            print("请检查数据库连接和权限。")
            raise SystemExit(1)

        # 确保至少有一个活跃的管理员账户（软删除的账户不计入）
        try:
            active_admin_exists = User.query.filter_by(is_admin=True, is_active=True, is_deleted=False).first()
            if not active_admin_exists:
                # 尝试查找名为'admin'的用户
                admin_user = User.query.filter_by(username='admin', is_deleted=False).first()
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

        # 安全提醒：默认口令仅供首次引导使用
        try:
            bootstrap_admin = User.query.filter_by(username='admin', is_deleted=False).first()
            if bootstrap_admin and check_password_hash(bootstrap_admin.password, 'admin'):
                print("=" * 68)
                print("[安全警告] 管理员账户 admin 仍在使用默认口令，请立即登录后修改！")
                print("=" * 68)
        except Exception as e:
            print(f"默认口令检查跳过: {e}")
        
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
    
    # debug 必须由环境变量控制：开启后 Werkzeug 调试器在生产环境等于远程代码执行漏洞。
    # 本地开发如需自动重载，请在 .env 中设置 FLASK_DEBUG=True。
    debug_mode = os.getenv('FLASK_DEBUG', 'False').strip().lower() == 'true'
    if debug_mode:
        print("[警告] FLASK_DEBUG=True，调试器已启用，请勿在生产环境使用。")
    app.run(host=os.getenv('FLASK_HOST', '127.0.0.1'),
            port=int(os.getenv('FLASK_PORT', 5000)),
            debug=debug_mode)
