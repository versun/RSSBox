import os
import tempfile
import shutil
from unittest import mock
from django.test import SimpleTestCase, override_settings
from django.conf import settings

from utils.backup_db import backup_db


class BackupDbTests(SimpleTestCase):
    """Tests for utils.backup_db.backup_db function."""

    def setUp(self):
        """Set up test environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_db.sqlite3")
        self.backup_dir = tempfile.mkdtemp()
        
        # 创建测试数据库文件
        with open(self.db_path, 'w') as f:
            f.write("test database content")

    def tearDown(self):
        """Clean up test environment."""
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        shutil.rmtree(self.backup_dir, ignore_errors=True)

    @mock.patch('utils.backup_db.settings')
    def test_backup_db_success(self, mock_settings):
        """测试数据库备份成功的情况"""
        mock_settings.DATABASES = {"default": {"NAME": self.db_path}}
        
        result = backup_db(None, None)
        
        # 验证返回了备份文件路径
        self.assertIsNotNone(result)
        self.assertTrue(result.endswith('.bak'))
        
        # 验证备份文件确实被创建
        self.assertTrue(os.path.exists(result))
        
        # 验证备份文件内容与原始文件相同
        with open(self.db_path, 'r') as original:
            with open(result, 'r') as backup:
                self.assertEqual(original.read(), backup.read())

    @mock.patch('utils.backup_db.settings')
    def test_backup_db_existing_backup_removed(self, mock_settings):
        """测试当备份文件已存在时，先删除再创建新备份"""
        mock_settings.DATABASES = {"default": {"NAME": self.db_path}}
        
        # 先创建一个备份文件，使用与函数生成相同的命名模式
        current_date = "20231201_120000"
        backup_path = f"{self.db_path}.{current_date}.bak"
        with open(backup_path, 'w') as f:
            f.write("old backup content")
        
        # 验证旧备份文件确实存在
        self.assertTrue(os.path.exists(backup_path))
        
        # 执行备份
        result = backup_db(None, None)
        
        # 验证返回了新的备份文件路径
        self.assertIsNotNone(result)
        self.assertNotEqual(result, backup_path)
        
        # 验证新备份文件被创建
        self.assertTrue(os.path.exists(result))
        
        # 验证新备份文件内容与原始数据库文件相同
        with open(self.db_path, 'r') as original:
            with open(result, 'r') as backup:
                self.assertEqual(original.read(), backup.read())

    @mock.patch('utils.backup_db.settings')
    def test_backup_db_database_not_found(self, mock_settings):
        """测试数据库文件不存在的情况"""
        non_existent_db = "/non/existent/db.sqlite3"
        mock_settings.DATABASES = {"default": {"NAME": non_existent_db}}
        
        result = backup_db(None, None)
        
        # 验证返回None
        self.assertIsNone(result)

    @mock.patch('utils.backup_db.settings')
    @mock.patch('utils.backup_db.os')
    def test_backup_db_permission_error_on_remove(self, mock_os, mock_settings):
        """测试删除已存在备份文件时出现权限错误"""
        mock_settings.DATABASES = {"default": {"NAME": self.db_path}}
        
        # 创建一个已存在的备份文件
        backup_path = f"{self.db_path}.20231201_120000.bak"
        with open(backup_path, 'w') as f:
            f.write("old backup content")
        
        # 设置 mock 让删除操作失败，但其他操作正常
        def mock_remove(path):
            if path == backup_path:
                raise PermissionError("Permission denied")
            # 对于其他路径，使用真实的 os.remove
            import os
            os.remove(path)
        
        mock_os.remove = mock_remove
        
        result = backup_db(None, None)
        
        # 验证返回None
        self.assertIsNone(result)

    @mock.patch('utils.backup_db.settings')
    @mock.patch('utils.backup_db.shutil.copyfile')
    def test_backup_db_permission_error_on_copy(self, mock_copyfile, mock_settings):
        """测试复制数据库文件时出现权限错误"""
        mock_settings.DATABASES = {"default": {"NAME": self.db_path}}
        mock_copyfile.side_effect = PermissionError("Permission denied")
        
        result = backup_db(None, None)
        
        # 验证返回None
        self.assertIsNone(result)

    @mock.patch('utils.backup_db.settings')
    @mock.patch('utils.backup_db.shutil.copyfile')
    def test_backup_db_os_error_on_copy(self, mock_copyfile, mock_settings):
        """测试复制数据库文件时出现OS错误"""
        mock_settings.DATABASES = {"default": {"NAME": self.db_path}}
        mock_copyfile.side_effect = OSError("Disk full")
        
        result = backup_db(None, None)
        
        # 验证返回None
        self.assertIsNone(result)

    @mock.patch('utils.backup_db.settings')
    @mock.patch('utils.backup_db.shutil.copyfile')
    def test_backup_db_unexpected_error(self, mock_copyfile, mock_settings):
        """测试复制数据库文件时出现意外错误"""
        mock_settings.DATABASES = {"default": {"NAME": self.db_path}}
        mock_copyfile.side_effect = ValueError("Unexpected error")
        
        result = backup_db(None, None)
        
        # 验证返回None
        self.assertIsNone(result)

    @mock.patch('utils.backup_db.settings')
    def test_backup_db_backup_filename_format(self, mock_settings):
        """测试备份文件名的格式"""
        mock_settings.DATABASES = {"default": {"NAME": self.db_path}}
        
        result = backup_db(None, None)
        
        # 验证文件名格式：原文件名.YYYYMMDD_HHMMSS.bak
        self.assertIsNotNone(result)
        filename = os.path.basename(result)
        self.assertTrue(filename.startswith("test_db.sqlite3."))
        self.assertTrue(filename.endswith(".bak"))
        
        # 验证日期时间格式
        date_part = filename.replace("test_db.sqlite3.", "").replace(".bak", "")
        self.assertEqual(len(date_part), 15)  # YYYYMMDD_HHMMSS = 15字符
        self.assertIn("_", date_part)

    @mock.patch('utils.backup_db.settings')
    @mock.patch('utils.backup_db.logger')
    def test_backup_db_logging_on_success(self, mock_logger, mock_settings):
        """测试成功备份时的日志记录"""
        mock_settings.DATABASES = {"default": {"NAME": self.db_path}}
        
        backup_db(None, None)
        
        # 验证成功日志被记录
        mock_logger.info.assert_called()
        info_calls = [call[0][0] for call in mock_logger.info.call_args_list]
        self.assertTrue(any("Database backup completed successfully" in call for call in info_calls))

    @mock.patch('utils.backup_db.settings')
    @mock.patch('utils.backup_db.logger')
    def test_backup_db_logging_on_error(self, mock_logger, mock_settings):
        """测试错误情况下的日志记录"""
        non_existent_db = "/non/existent/db.sqlite3"
        mock_settings.DATABASES = {"default": {"NAME": non_existent_db}}
        
        backup_db(None, None)
        
        # 验证错误日志被记录
        mock_logger.error.assert_called()
        error_calls = [call[0][0] for call in mock_logger.error.call_args_list]
        self.assertTrue(any("Database file" in call and "not found" in call for call in error_calls))

    @mock.patch('utils.backup_db.settings')
    @mock.patch('utils.backup_db.logger')
    def test_backup_db_logging_on_successful_remove(self, mock_logger, mock_settings):
        """测试成功删除已存在备份文件时的日志记录"""
        mock_settings.DATABASES = {"default": {"NAME": self.db_path}}
        
        # 创建一个已存在的备份文件，使用与函数生成相同的命名模式
        # 我们需要 mock datetime 来确保文件名匹配
        with mock.patch('utils.backup_db.datetime') as mock_datetime:
            mock_datetime.datetime.now.return_value.strftime.return_value = "20231201_120000"
            
            # 创建一个已存在的备份文件
            backup_path = f"{self.db_path}.20231201_120000.bak"
            with open(backup_path, 'w') as f:
                f.write("old backup content")
            
            # 执行备份
            result = backup_db(None, None)
            
            # 验证成功日志被记录
            mock_logger.info.assert_called()
            info_calls = [call[0][0] for call in mock_logger.info.call_args_list]
            

            
            self.assertTrue(any("Removed existing backup file" in call for call in info_calls))
            self.assertTrue(any("Database backup completed successfully" in call for call in info_calls))
            
            # 验证返回了备份文件路径
            self.assertIsNotNone(result)
            self.assertTrue(os.path.exists(result))

    @mock.patch('utils.backup_db.settings')
    @mock.patch('utils.backup_db.logger')
    def test_backup_db_logging_on_remove_failure(self, mock_logger, mock_settings):
        """测试删除已存在备份文件失败时的日志记录"""
        mock_settings.DATABASES = {"default": {"NAME": self.db_path}}
        
        # 创建一个已存在的备份文件，使用与函数生成相同的命名模式
        with mock.patch('utils.backup_db.datetime') as mock_datetime:
            mock_datetime.datetime.now.return_value.strftime.return_value = "20231201_120000"
            
            # 创建一个已存在的备份文件
            backup_path = f"{self.db_path}.20231201_120000.bak"
            with open(backup_path, 'w') as f:
                f.write("old backup content")
            
            # 设置 mock 让删除操作失败
            with mock.patch('utils.backup_db.os.remove', side_effect=OSError("Test error")):
                result = backup_db(None, None)
            
            # 验证错误日志被记录
            mock_logger.error.assert_called()
            error_calls = [call[0][0] for call in mock_logger.error.call_args_list]
            self.assertTrue(any("Failed to remove existing backup file" in call for call in error_calls))
            
            # 验证返回None
            self.assertIsNone(result)
