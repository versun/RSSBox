## Docker Compose(推荐)

进入要存放数据的文件夹，如`/home/versun/rsstranslator/`

下载[docker-compose.yml](https://github.com/versun/RSS-Translator/blob/main/deploy/docker-compose.yml)文件

`wget "https://raw.githubusercontent.com/versun/RSS-Translator/main/deploy/docker-compose.yml"`

运行 `docker-compose -f docker-compose.yml up -d`

安装完成，访问 http://127.0.0.1:8000

**默认账户：admin 密码：rsstranslator**

请登录后立即修改你的密码