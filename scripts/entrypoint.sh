#!/bin/sh

# 将环境变量写入cron可以访问的文件
echo "Exporting environment variables for cron..."
printenv | grep -Ev 'BASHOPTS|BASH_VERSINFO|EUID|PPID|SHELLOPTS|UID' >> /etc/environment

echo "Running initialization script..."
# gosu rsstranslator /opt/venv/bin/python $DockerHOME/scripts/init.py
/opt/venv/bin/python $DockerHOME/scripts/init.py

# 修复权限问题
# chown rsstranslator:rsstranslator /var/run/crond.pid
# chown -R rsstranslator:rsstranslator $DockerHOME

# 以root用户启动cron服务
cron -n &

# 等待Redis服务可用
REDIS_URL=${REDIS_URL:-redis://rsstranslator_redis:6379/0}
until python -c "import redis; r=redis.Redis.from_url('$REDIS_URL'); r.ping()" 2>/dev/null; do
  echo "Waiting for Redis at $REDIS_URL..."
  sleep 2
done
echo "Redis is available!"

# 切换到应用用户并执行命令
# exec gosu rsstranslator "$@"
exec "$@"