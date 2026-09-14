![image](https://github.com/tamakyi/simple-sales-system/blob/main/screenshot/wolf_sales.webp)
<p align="center"> 狼的小卖部 - 基于Python的简单网页端进销存/销售管理系统</p>


## Demo
浏览器打开 [此处Demo](http://lnmp-5000.337845818.best/)

## 截图
 - 主界面
![image](https://github.com/tamakyi/simple-sales-system/blob/main/screenshot/dashboard.png)
 - 商品管理
![image](https://github.com/tamakyi/simple-sales-system/blob/main/screenshot/products.png)
 - 商品导入
![image](https://github.com/tamakyi/simple-sales-system/blob/main/screenshot/products-import.png)
 - 进销存
![image](https://github.com/tamakyi/simple-sales-system/blob/main/screenshot/sales.png)
 - 针对移动端优化的进销存
![image](https://github.com/tamakyi/simple-sales-system/blob/main/screenshot/sales-simple.png)
 - 商品分类
![image](https://github.com/tamakyi/simple-sales-system/blob/main/screenshot/categories.png)
 - 用户管理
![image](https://github.com/tamakyi/simple-sales-system/blob/main/screenshot/users.png)
 - 操作记录
![image](https://github.com/tamakyi/simple-sales-system/blob/main/screenshot/logs.png)



## 简介

本项目是一个基于 Flask + Jinja2 的进销存与销售管理系统，支持商品、分类、库存、销售流水、用户权限、批量导入、统计报表、仪表盘等功能。

---

## 功能特色

- 商品管理（增删改查，支持图片、批量导入）
- 分类管理（增加批量管理）
- 进销存：商品表格展示，直接在行内录入进货/销售（针对移动端单独做优化，即简化销售页面）
- 销售流水、历史/今日销售、库存统计（单日修改为期间）
- 销售排行榜、分类饼图、销售数据卡片
- 用户权限（管理员/普通用户/审核/忘记密码和邮箱绑定）
- 操作日志
- 导出销售报表（Excel）
- 支持分页、搜索、图片外链或本地上传
- 数据库支持 MySQL 或 PostgreSQL，入口统一为 `app.py`，由 `.env` 的 `DB_TYPE` 指定（sqlite 分支已移除）
- 回收站：软删除的商品与用户可查看并还原
- 网页公告
- 背景图片
- 网页跟踪脚本
---

## 快速开始

### 1. 安装依赖

```sh
pip install -r requirements.txt
```

### 2.复制.env.sample为.env并编辑
完整可配置项与逐项说明见 `.env.sample`，复制为 `.env` 后按注释修改即可。最需要留意的是数据库这一段：

```ini
# 二选一：mysql 或 postgres
DB_TYPE = mysql
SQLALCHEMY_DATABASE_URI = mysql+pymysql://username:password@localhost:3306/database
# DB_TYPE = postgres
# SQLALCHEMY_DATABASE_URI = postgresql://username:password@localhost:5432/database
```

> - `DB_TYPE` 决定使用哪种数据库；留空时会按 URI 前缀自动判断。两行 `SQLALCHEMY_DATABASE_URI` 不要同时启用，否则后者会覆盖前者。
> - `SECRET_KEY` 与 `SECURITY_PASSWORD_SALT` 必须替换为随机字符串；若留空或仍是示例值，启动时会生成临时密钥并告警（重启后登录状态与重置链接全部失效）。
> - 目标数据库不存在时会尝试自动创建，需要连接账户具备建库权限。

> 关于 soft delete：商品的「删除」与用户的「删除」均为**软删除**（置 `is_deleted` 标记，不物理删除行），
> 以保全销售流水与操作日志的外键完整性。既有数据库在启动时会自动补上相关字段，无需手工执行 SQL。
> 被删除的商品与用户可在「回收站」页面查看并还原（仅管理员可见）。

### 2. 启动项目

```sh
python app.py # 数据库类型由 .env 的 DB_TYPE 决定，无需切换文件
```

需要本地热重载时，在 `.env` 中设置 `FLASK_DEBUG=True`（生产环境切勿开启）。

### 3. 访问地址

浏览器打开 [http://localhost:5000/](http://localhost:5000/)

### 4. 初始管理员

- 用户名：admin
- 密码：admin

---

## 批量导入商品

支持 CSV 批量导入，**需包含下列列名：**

- 商品名、单价、库存、分类

示例见 `sample_products.csv`。
网页内支持直接导出模板文件，修改好导入即可实现批量导入。

---

## 目录结构

```
.
├─ app.py
├─ forms.py
├─ models.py
├─ utils.py
├─ requirements.txt
├─ .env.sample
├─ static/
│    ├─ uploads/
│    ├─ favicon.ico
│    └─ style.css
├─ templates/
│    ├─ base.html
│    ├─ ...other html
├─ sample_products.csv
├─ README.md
```

---

## 常见问题

- **图片404？** 请确保 `static/uploads/` 文件夹存在且有写权限。
- **批量导入出错？** 请确保 CSV 列名和样例一致，编码为 UTF-8。
- **注册后不能登录？** 管理员审核通过后方可激活。
- **数据库连接失败？** 确认 `.env` 中 `DB_TYPE` 与实际 URI 前缀一致，且连接账户具备建库权限；数据库不存在时程序会尝试自动创建，失败则退出并打印原因。
- **忘记管理员密码？** 如果删除或停用了最后一个管理员，可从「回收站」还原管理员账户并在用户管理中启用。
- **PostgreSQL 报 `UnicodeDecodeError` 或错误消息乱码？** 中文 Windows 的 PostgreSQL 按 GBK（代码页 936）返回本地化错误消息，程序已做兼容解码。若希望所有客户端都干净，可在 `postgresql.conf` 中设置 `lc_messages = 'C'` 后重启服务。

---

## 测试

```sh
python tests/run_regression.py
```

无需数据库服务即可运行（内部使用临时 sqlite 库），覆盖配置解析、既有库自动补列、软删除与导出、权限与限流、回收站还原、CSRF 以及启动失败路径共 60 项断言，退出码非 0 表示有失败项。

## 许可证

MIT License

## 测试

 - 当前已在Debian12 + conda python 3.11 下测试通过。
