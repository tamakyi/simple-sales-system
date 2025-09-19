import pandas as pd
from models import db, Product, Category

def import_products_csv(file):
    df = pd.read_csv(file)
    # 1. 检查列名是否完全匹配（忽略空格和大小写，但严格匹配文字）
    required_columns = ['商品名', '单价', '库存', '分类']
    # 清洗列名（去除前后空格）
    df.columns = [col.strip() for col in df.columns]
    missing_columns = [col for col in required_columns if col not in df.columns]
    if missing_columns:
        print(f"缺少必填列：{missing_columns}")
        return False  # 明确返回失败
    
    # 记录成功导入的商品数量
    success_count = 0
    
    for idx, row in df.iterrows():
        row_num = idx + 1
        # 2. 清洗字段值（去除前后空格）
        prod_name = str(row.get('商品名', '')).strip()
        price_str = str(row.get('单价', '')).strip()
        stock_str = str(row.get('库存', '')).strip()
        category_name = str(row.get('分类', '')).strip()
        
        # 3. 严格检查空值（包括空字符串和纯空格）
        if not prod_name:
            print(f"第{row_num}行：商品名为空或仅含空格")
            continue
        if not price_str:
            print(f"第{row_num}行：单价为空或仅含空格")
            continue
        if not stock_str:
            print(f"第{row_num}行：库存为空或仅含空格")
            continue
        if not category_name:
            print(f"第{row_num}行：分类为空或仅含空格")
            category_name = "未分类"  # 分类为空时设为默认
        
        # 4. 验证单价和库存的数值格式
        try:
            price = float(price_str)
            stock = int(stock_str)
        except ValueError:
            print(f"第{row_num}行：单价或库存格式错误（单价：{price_str}，库存：{stock_str}）")
            continue
        
        # 5. 处理分类
        category = Category.query.filter_by(name=category_name).first()
        if not category:
            # 如果分类不存在，创建新分类
            category = Category(name=category_name)
            db.session.add(category)
            db.session.flush()  # 立即获取新分类的ID
        
        # 6. 处理图片链接（可选）
        image_link = str(row.get('图片链接', '')).strip() if '图片链接' in df.columns else ''
        
        # 7. 创建或更新商品
        # 检查是否已存在同名商品
        existing_product = Product.query.filter_by(name=prod_name).first()
        if existing_product:
            # 更新现有商品
            existing_product.price = price
            existing_product.stock = stock
            existing_product.category_id = category.id
            if image_link:
                existing_product.image = image_link
            print(f"第{row_num}行：更新商品 '{prod_name}'")
        else:
            # 创建新商品
            product = Product(
                name=prod_name,
                price=price,
                stock=stock,
                category_id=category.id,
                image=image_link if image_link else None
            )
            db.session.add(product)
            print(f"第{row_num}行：创建商品 '{prod_name}'")
        
        success_count += 1
    
    try:
        db.session.commit()
        print(f"成功导入/更新 {success_count} 个商品")
        return True
    except Exception as e:
        db.session.rollback()
        print(f"提交到数据库时出错: {e}")
        return False
