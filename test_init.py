import sys, asyncio
sys.path.insert(0, '.')
from models.database import init_db
asyncio.run(init_db())
print('DB init OK')
