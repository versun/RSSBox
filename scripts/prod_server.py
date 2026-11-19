#!/usr/bin/env python

import os
import subprocess
import sys
from pathlib import Path
from .init import init_server


def setup_environment():
    """设置环境变量"""
    # os.environ["DEMO"] = "0"
    # os.environ["DEBUG"] = "0"
    # os.environ["LOG_LEVEL"] = "INFO"
    # 合并已存在的 CSRF_TRUSTED_ORIGINS
    default_origins = (
        "http://localhost,http://localhost:8000,http://127.0.0.1,http://127.0.0.1:8000,"
        "https://localhost,https://localhost:8000,https://127.0.0.1,https://127.0.0.1:8000"
    )
    existing = os.environ.get("CSRF_TRUSTED_ORIGINS", "")
    origins_set = set(filter(None, (existing + "," + default_origins).split(",")))
    os.environ["CSRF_TRUSTED_ORIGINS"] = ",".join(sorted(origins_set))


def start_production_server():
    """启动生产服务器"""
    print("🌐 准备Django生产服务器...")
    workers = os.environ.get("WORKERS", "1")
    host = os.environ.get("HOST", "0.0.0.0")
    port = os.environ.get("PORT", "8000")
    # 检查可用的ASGI/WSGI服务器
    server_type = None

    # 1. 优先检查uvicorn（ASGI服务器，支持Django异步功能）
    try:
        subprocess.run(["uvicorn", "--version"], check=True, capture_output=True)
        server_type = "uvicorn"
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    # 2. 检查gunicorn
    if not server_type:
        try:
            subprocess.run(["gunicorn", "--version"], check=True, capture_output=True)
            server_type = "gunicorn"
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass

    # 3. 检查uwsgi
    if not server_type:
        try:
            subprocess.run(["uwsgi", "--version"], check=True, capture_output=True)
            server_type = "uwsgi"
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass

    if server_type == "uvicorn":
        # 使用uvicorn启动（ASGI服务器，支持异步Django）

        # 检查是否有gunicorn可以与uvicorn配合使用
        try:
            subprocess.run(["gunicorn", "--version"], check=True, capture_output=True)
            # 使用gunicorn + uvicorn worker（推荐的生产配置）
            print("🚀 使用 Gunicorn + Uvicorn Worker 启动...")
            cmd = [
                "gunicorn",
                "--workers",
                workers,
                "--worker-class",
                "uvicorn.workers.UvicornWorker",
                "--bind",
                f"{host}:{port}",
                "--timeout",
                "120",
                "--keep-alive",
                "5",
                "--max-requests",
                "1000",
                "--max-requests-jitter",
                "100",
                "--preload",  # 预加载应用以提高性能
                "config.asgi:application",
            ]
        except (subprocess.CalledProcessError, FileNotFoundError):
            # 单独使用uvicorn
            print("🚀 使用 Uvicorn 启动...")
            cmd = [
                "uvicorn",
                "config.asgi:application",
                "--host",
                host,
                "--port",
                port,
                "--workers",
                workers,
                "--loop",
                "uvloop",  # 使用uvloop提高性能
                "--log-level",
                "warning",
                "--access-log",
                "--proxy-headers",  # 支持代理头（如果在Nginx后面）
                "--forwarded-allow-ips",
                "*",
            ]

    elif server_type == "gunicorn":
        # 使用gunicorn启动（WSGI服务器）
        print("🚀 使用 Gunicorn 启动...")
        cmd = [
            "gunicorn",
            # "--workers", workers,
            "--bind",
            f"{host}:{port}",
            "--timeout",
            "120",
            "--keep-alive",
            "5",
            "--max-requests",
            "1000",
            "--max-requests-jitter",
            "100",
            "config.wsgi:application",
        ]

    elif server_type == "uwsgi":
        # 使用uwsgi启动
        print("🚀 使用 uWSGI 启动...")
        cmd = [
            "uwsgi",
            "--http",
            f":{port}",
            "--workers",
            workers,
            "--module",
            "config.wsgi:application",
            "--master",
            "--enable-threads",
        ]
    else:
        # 如果没有安装专业服务器，给出警告并使用Django内置服务器
        print("⚠️  警告: 未找到生产级服务器(uvicorn/gunicorn/uwsgi)")
        print("⚠️  正在使用Django内置服务器，不建议在生产环境使用")

        cmd = [
            "python",
            "manage.py",
            "runserver",
            f"{host}:{port}",
            "--insecure",  # --insecure允许在DEBUG=False时提供静态文件
        ]

    print(f"🚀 启动生产服务器 (http://{host}:{port})...")
    if os.name == "posix":
        # Replace this process with the server so signals (Ctrl+C, kill)
        # reach the server directly instead of orphaning it.
        os.execvp(cmd[0], cmd)
    else:
        try:
            subprocess.run(cmd)
        except KeyboardInterrupt:
            print("\n🛑 服务已停止")
        except subprocess.CalledProcessError as e:
            print(f"❌ 生产服务启动失败: {e}")


def main():
    """主函数"""
    print("=" * 50)
    print("🔥 Django生产环境部署脚本")
    print("=" * 50)

    try:
        # 检查是否在Django项目目录中
        if not Path("manage.py").exists():
            print("❌ 错误: 未找到 manage.py 文件")
            print("请确保在Django项目根目录中运行此脚本")
            sys.exit(1)

        # 1. 设置环境变量
        setup_environment()

        # 2. 初始化
        init_server()

        start_production_server()

    except Exception as e:
        print(f"❌ 发生错误: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
