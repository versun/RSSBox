import os
import logging
import shutil
import datetime
from django.conf import settings

logger = logging.getLogger(__name__)


def backup_db(apps, schema_editor):
    db_path = settings.DATABASES["default"]["NAME"]
    # 添加日期到备份文件名
    current_date = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{db_path}.{current_date}.bak"
    try:
        if os.path.exists(backup_path):
            os.remove(backup_path)
        shutil.copyfile(db_path, backup_path)
    except FileNotFoundError:
        logger.error(f"Database file {db_path} not found.")
    except PermissionError:
        logger.error(f"Permission denied when accessing {db_path} or {backup_path}.")
    except Exception as e:
        logger.error(f"Error occurred during database backup: {str(e)}")
