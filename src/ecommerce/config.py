"""应用配置模块"""

from pathlib import Path

# 项目根目录
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# 数据库 - SQLite 文件存放于项目根目录
DATABASE_URL = f"sqlite:///{BASE_DIR / 'ecommerce.db'}"

# JWT 密钥（生产环境请使用环境变量覆盖）
SECRET_KEY = "ecommerce-secret-key-change-in-production"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 小时

# 首页商品分页
PRODUCTS_PAGE_SIZE = 20
