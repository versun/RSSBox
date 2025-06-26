#!/bin/sh

# 将环境变量写入cron可以访问的文件
echo "Exporting environment variables for cron..."
printenv | grep -Ev 'BASHOPTS|BASH_VERSINFO|EUID|PPID|SHELLOPTS|UID' >> /etc/environment

echo "Running initialization script..."
gosu rsstranslator /opt/venv/bin/python $DockerHOME/scripts/init.py

# 修复权限问题
chown rsstranslator:rsstranslator /var/run/crond.pid

# 以root用户启动cron服务
cron -n &

# 切换到应用用户并执行命令
exec gosu rsstranslator "$@"