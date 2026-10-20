"""Apply Alembic migrations to a disposable R0 database without using alembic.ini's production URL."""
import argparse
import os
from pathlib import Path
import tempfile

from alembic import command
from alembic.config import Config

parser = argparse.ArgumentParser()
parser.add_argument('--database', required=True)
args = parser.parse_args()

if os.environ.get('PERSONAL_OS_TESTING') != '1':
    raise SystemExit('Refusing test migration without PERSONAL_OS_TESTING=1')

from app.config import BACKEND_ROOT, settings
from app.database import engine

target = Path(args.database).resolve()
temp_root = Path(tempfile.gettempdir()).resolve()
if not target.is_relative_to(temp_root) or not target.parent.name.lower().startswith('personal_os_r033_r0_e2e_'):
    raise SystemExit('Refusing migration outside the isolated R0 temporary directory')
if Path(engine.url.database).resolve() != target:
    raise SystemExit('Configured SQLAlchemy target does not match the requested isolated DB')

config = Config(str(BACKEND_ROOT / 'alembic.ini'))
config.set_main_option('script_location', str(BACKEND_ROOT / 'alembic'))
config.set_main_option('sqlalchemy.url', settings.database_url.replace('%', '%%'))
command.upgrade(config, 'head')
print('isolated schema upgraded to Alembic head')
