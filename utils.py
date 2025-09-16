import pandas as pd
from models import db, Product, Category

def import_products_csv(file):
    # 文件对象为 werkzeug FileStorage
    df = pd.read_csv(file)
    # 要求列: 商品名, 单价, 库存, 分类
    for idx, row in df.iterrows():
        cname = str(row['分类']).strip()
        cat = Category.query.filter_by(name=cname).first()
        if not cat:
            cat = Category(name=cname)
            db.session.add(cat)
            db.session.commit()
        prod = Product(
            name=str(row['商品名']).strip(),
            price=float(row['单价']),
            stock=int(row['库存']),
            category_id=cat.id,
            image=''  # 可扩展
        )
        db.session.add(prod)
    db.session.commit()