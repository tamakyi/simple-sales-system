# 基于Python的简单网页端进销存/销售管理系统

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
- 分类管理
- 进销存：商品表格展示，直接在行内录入进货/销售（针对移动端单独做优化）
- 销售流水、历史/今日销售、库存统计
- 销售排行榜、分类饼图、销售数据卡片
- 用户权限（管理员/普通用户/审核）
- 操作日志
- 导出销售报表（Excel）
- 支持分页、搜索、图片外链或本地上传
- 可选使用sql轻量化存储数据或使用mysql存储
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
```
UPLOAD_FOLDER = 'static/uploads' #图片上传路径
SECRET_KEY = mysecretkey #安全密钥，建议替换
SQLALCHEMY_DATABASE_URI = 'sqlite:///sales.db' # 使用SQLITE作为数据库时使用
SQLALCHEMY_DATABASE_URI = mysql+pymysql://root:password@localhost:3306/test_sale # 使用MYSQL作为数据库时使用
MAX_CONTENT_LENGTH = 2097152 #最大上传大小，默认2M，即2*1024*1024
PER_PAGE = 10 # 商品页面每页体现的商品数量
BACKGROUND_IMAGE_URL=https://xxx.com/xxx.png # 默认背景图片地址
BACKGROUND_OPACITY=0.1 # 背景图片不透明度，范围0-1，越接近0越透明。
BACKGROUND_SIZE=cover #背景图片尺寸，默认cover即可，详情查看web css中关于background_size的参数说明。
DASHBOARD_ANNOUNCEMENT="请注意：系统将于本周五晚进行维护，届时可能无法访问。"
ANNOUNCEMENT_ENABLED=True  # 控制公告是否显示
ANALYZE_SCRIPT="" #分析脚本，建议使用umami
ANALYZE_ENABLE=True #开启分析脚本功能
```

### 2. 启动项目

```sh
python app-mysql.py #使用mysql存储数据
python app-sqlite.py #使用sqlite存储数据
```

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

---

## 许可证

MIT License

## 测试

 - 当前已在Debian12 + conda python 3.11 下测试通过。
