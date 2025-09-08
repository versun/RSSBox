#!/bin/sh

# 将环境变量写入cron可以访问的文件
echo "Exporting environment variables for cron..."
printenv | grep -Ev 'BASHOPTS|BASH_VERSINFO|EUID|PPID|SHELLOPTS|UID' >> /etc/environment

echo "Running initialization script..."
/opt/venv/bin/python $DockerHOME/scripts/init.py


cron -n &

# 等待Redis服务可用
REDIS_URL=${REDIS_URL:-redis://rssbox_redis:6379/0}
max_attempts=10
attempt=0

until [ $attempt -ge $max_attempts ] || python -c "import redis; r=redis.Redis.from_url('$REDIS_URL'); r.ping()" 2>/dev/null; do
  attempt=$((attempt+1))
  echo "Waiting for Redis at $REDIS_URL... (Attempt $attempt/$max_attempts)"
  sleep 2
done

if [ $attempt -lt $max_attempts ]; then
  echo "Redis is available!"
else
  echo "Failed to connect to Redis after $max_attempts attempts."
fi

exec "$@"