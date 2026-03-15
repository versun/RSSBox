#!/usr/bin/env python

import os
import subprocess
import sys
from pathlib import Path
from .init import init_server


def setup_environment():
    """设置环境变量"""
    os.environ["DEMO"] = "1"
    os.environ["DEBUG"] = "1"
    os.environ["LOG_LEVEL"] = "DEBUG"
    # 合并已存在的 CSRF_TRUSTED_ORIGINS
    default_origins = (
        "http://localhost,http://localhost:8000,http://127.0.0.1,http://127.0.0.1:8000,"
        "https://localhost,https://localhost:8000,https://127.0.0.1,https://127.0.0.1:8000"
    )
    existing = os.environ.get("CSRF_TRUSTED_ORIGINS", "")
    origins_set = set(filter(None, (existing + "," + default_origins).split(",")))
    os.environ["CSRF_TRUSTED_ORIGINS"] = ",".join(sorted(origins_set))


def start_development_server():
    """启动开发服务器"""
    print("🌐 Start DEV Server...")
    try:
        subprocess.run([sys.executable, "manage.py", "runserver"], check=True)
    except KeyboardInterrupt:
        print("\n🛑 Server Stopped by User")
    except subprocess.CalledProcessError as e:
        print(f"❌ Start DEV Server Failed : {e}")


def main():
    """主函数"""
    print("=" * 50)
    print("🔥 Django development environment initialization script")  # English:
    print("=" * 50)

    try:
        # 检查是否在Django项目目录中
        if not Path("manage.py").exists():
            print("❌ Error: manage.py file not found")
            print(
                "Please ensure that you run this script in the root directory of your Django project."
            )
            sys.exit(1)

        # 1. 设置环境变量
        setup_environment()

        # 2. 初始化
        init_server()

        start_development_server()

    except Exception as e:
        print(f"❌ ERROR: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
