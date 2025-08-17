import os
import logging
import shutil
import datetime
from django.conf import settings

logger = logging.getLogger(__name__)


def backup_db(apps, schema_editor):
    """
    备份数据库文件
    
    Args:
        apps: Django apps registry
        schema_editor: Django schema editor
    
    Returns:
        str: 备份文件路径，如果失败则返回None
    """
    db_path = settings.DATABASES["default"]["NAME"]
    
    # 检查数据库文件是否存在
    if not os.path.exists(db_path):
        logger.error(f"Database file {db_path} not found.")
        return None
    
    # 添加日期到备份文件名
    current_date = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{db_path}.{current_date}.bak"
    
    try:
        # 如果备份文件已存在，先删除
        if os.path.exists(backup_path):
            try:
                os.remove(backup_path)
                logger.info(f"Removed existing backup file: {backup_path}")
            except (PermissionError, OSError) as e:
                logger.error(f"Failed to remove existing backup file {backup_path}: {str(e)}")
                return None
        
        # 执行备份
        shutil.copyfile(db_path, backup_path)
        logger.info(f"Database backup completed successfully: {backup_path}")
        return backup_path
        
    except FileNotFoundError:
        logger.error(f"Database file {db_path} not found during backup.")
        return None
    except PermissionError:
        logger.error(f"Permission denied when accessing {db_path} or {backup_path}.")
        return None
    except OSError as e:
        logger.error(f"OS error occurred during database backup: {str(e)}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error occurred during database backup: {str(e)}")
        return None
