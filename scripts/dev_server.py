#!/usr/bin/env python

import os
import subprocess
import sys
import signal
from pathlib import Path
from .init import init_server


def setup_environment():
    """设置环境变量"""
    os.environ["DEMO"] = "1"
    os.environ["DEBUG"] = "1"
    os.environ["LOG_LEVEL"] = "INFO"
    # 合并已存在的 CSRF_TRUSTED_ORIGINS
    default_origins = (
        "http://localhost,http://localhost:8000,http://127.0.0.1,http://127.0.0.1:8000,"
        "https://localhost,https://localhost:8000,https://127.0.0.1,https://127.0.0.1:8000"
    )
    existing = os.environ.get("CSRF_TRUSTED_ORIGINS", "")
    origins_set = set(filter(None, (existing + "," + default_origins).split(",")))
    os.environ["CSRF_TRUSTED_ORIGINS"] = ",".join(sorted(origins_set))
    print("Allow below domains access CSRF protection:")
    for origin in os.environ["CSRF_TRUSTED_ORIGINS"].split(","):
        print(f"  - {origin}")


def install_dependencies():
    """安装开发依赖"""
    pyproject_file = Path("pyproject.toml")

    if pyproject_file.exists():
        print("📦 安装<开发>依赖...")

        # 首先尝试使用uv sync安装dev group
        try:
            subprocess.run(["uv", "sync", "--group", "dev"], check=True)
            print("✓ <开发>依赖安装完成")
            return
        except subprocess.CalledProcessError:
            print("⚠️  uv sync失败，尝试使用uv pip install方式")

        # 如果sync失败，尝试使用pip install方式
        try:
            subprocess.run(["uv", "pip", "install", "-e", ".[dev]"], check=True)
            print("✓ <开发>依赖安装完成")
            return
        except subprocess.CalledProcessError:
            print("⚠️  无法安装<开发>依赖")
            sys.exit(1)


def prepare_django():
    try:
        init_server()
        print("✓ Django初始化完成")
    except subprocess.CalledProcessError as e:
        print(f"⚠️  Django初始化失败: {e}")
        return False
    return True


def start_huey_worker():
    """启动Huey后台任务处理器"""
    print("🚀 启动Huey任务处理器...")
    process = subprocess.Popen(["uv", "run", "python", "manage.py", "run_huey", "-f"])

    def cleanup():
        """清理函数"""
        if process.poll() is None:
            print("\n🛑 正在停止Huey任务处理器...")
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

    return process, cleanup


def start_development_server():
    """启动开发服务器"""
    print("🌐 启动Django开发服务器...")
    try:
        subprocess.run(["uv", "run", "python", "manage.py", "runserver"], check=True)
    except KeyboardInterrupt:
        print("\n🛑 服务器已停止")
    except subprocess.CalledProcessError as e:
        print(f"❌ 开发服务器启动失败: {e}")


def main():
    """主函数"""
    print("=" * 50)
    print("🔥 Django开发环境初始化脚本")
    print("=" * 50)

    try:
        # 检查是否在Django项目目录中
        if not Path("manage.py").exists():
            print("❌ 错误: 未找到 manage.py 文件")
            print("请确保在Django项目根目录中运行此脚本")
            sys.exit(1)

        # 1. 安装依赖
        install_dependencies()

        # 2. 设置环境变量
        setup_environment()

        # 3. 准备Django环境
        prepare_django()

        # 4. 启动Huey任务处理器
        huey_process, cleanup_func = start_huey_worker()

        try:
            # 5. 启动开发服务器
            start_development_server()
        finally:
            cleanup_func()

    except Exception as e:
        print(f"❌ 发生错误: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
