#!/bin/sh

# 将环境变量写入cron可以访问的文件
echo "Exporting environment variables for cron..."
printenv | grep -Ev 'BASHOPTS|BASH_VERSINFO|EUID|PPID|SHELLOPTS|UID' >> /etc/environment

# 等待Redis服务可用（默认值与 config/settings.py 保持一致）
REDIS_URL=${REDIS_URL:-redis://localhost:6379/1}
max_attempts=10
attempt=0

until [ $attempt -ge $max_attempts ] || REDIS_URL="$REDIS_URL" /opt/venv/bin/python -c "import os, redis; redis.Redis.from_url(os.environ['REDIS_URL']).ping()" 2>/dev/null; do
  attempt=$((attempt+1))
  echo "Waiting for Redis at $REDIS_URL... (Attempt $attempt/$max_attempts)"
  sleep 2
done

if [ $attempt -lt $max_attempts ]; then
  echo "Redis is available!"
else
  echo "Failed to connect to Redis after $max_attempts attempts."
fi

echo "Running initialization script..."
/opt/venv/bin/python $DockerHOME/scripts/init.py


cron -n &

exec "$@"