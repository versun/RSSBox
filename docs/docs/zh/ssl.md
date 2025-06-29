建议使用caddy并配合cloudflare的DNS代理使用

安装Caddy: https://caddyserver.com/docs/install#debian-ubuntu-raspbian

创建caddy配置文件

可参考[/home/rsstranslator/deploy/Caddyfile](https://github.com/versun/RSS-Translator/blob/main/deploy/Caddyfile)进行修改，正常只要修改第一行的域名即可

`sudo nano /app/deploy/Caddyfile`

内容如下:
```
example.com {
        encode zstd gzip
        #tls internal
        handle_path /static/* {
                root * /home/rsstranslator/static/
                file_server
        }

        handle_path /media/* {
                root * /home/rsstranslator/media/
                file_server
        }

        reverse_proxy 127.0.0.1:8000
}
```
修改完成后，复制配置文件到/etc/caddy/Caddyfile，并重启即可
```
sudo mv /etc/caddy/Caddyfile /etc/caddy/Caddyfile.back
sudo cp /app/deploy/Caddyfile /etc/caddy/
sudo systemctl reload caddy
```
如果cloudflare开启了dns代理，则需要在cloudflare的SSL/TLS页面，加密模式选择Full