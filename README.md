# 基于Python的简单网页端进销存/销售管理系统

## 简介

本项目是一个基于 Flask + Jinja2 的进销存与销售管理系统，支持商品、分类、库存、销售流水、用户权限、批量导入、统计报表、仪表盘等功能。

---

## 功能特色

- 商品管理（增删改查，支持图片、批量导入）
- 分类管理
- 进销存：商品表格展示，直接在行内录入进货/销售
- 销售流水、历史/今日销售、库存统计
- 销售排行榜、分类饼图、销售数据卡片
- 用户权限（管理员/普通用户/审核）
- 操作日志
- 导出销售报表（Excel）
- 支持分页、搜索、图片外链或本地上传
- 使用sql轻量化运行，不依赖mysql

---

## 快速开始

### 1. 安装依赖

```sh
pip install -r requirements.txt
```

### 2. 启动项目

```sh
python app.py
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
│    └─ style.css
├─ templates/
│    ├─ base.html
│    ├─ ...其他html
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
