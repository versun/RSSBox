# 2025-06-03
由于之前尝试重构了好几次，所以这次优化思路就顺畅很多，大体上也是一次重构：
1. 将ofeed和tfeed整合层feed模型
2. 取消任务管理，使用系统自带的cron按固定时间刷新（5min，15min，1h，1day，1week）
3. 简化管理页面和feed列表，增加信息密度
4. 取消缓存功能，直接将每个翻译/总结的内容保存到feed模型下，统一管理
5. 由于没了任务管理和缓存，功能逻辑能简化很多，方便后续维护

# 2025-05-26
沉静了半年多，还是不重构了，一点点优化当前项目吧
1. 先整理下架构，使用uv包管理
2. 把初始化等命令转为uv命令
3. 由于没有针对异步进行优化，所以启动器从 uvicorn 换成 gunicorn，等后期优化了异步代码再换回来
4. 升级 django 到最新版本 5.2.1

# 2024-09-17
哎，还是前后端分离吧，reflex这种合并开发实在是有点麻烦，看起来很方便。
后端用fastapi，前端可能会用svelte，不过希望能用react+tailwind，这样直接让ai写应该方便些

# 2024-09-15
搞来搞去，还是不理想，。。。。要不试一试django-ninja吧。。。或者直接用Go重写。。。。。。搞的越来越远越来越难。。。

# 2024-09-12
Replit Agent不熟悉reflex,只熟悉flask。。。还是手动吧

# 2024-09-11
从模板改代码好麻烦，，到现在还跑不通。。。
算了，一会试一试重新开始，用AI Agent试一试

# 2024-09-10
一些事情耽误了好多天。
准备使用reader库来做为主要的feed处理工具，因为它有阅读状态管理和entries管理，很方便。 依旧以xml文件为主，如果reader查询有更新，则再更新xml文件。要2个数据库，一个reader存储entries的，一个翻译器的数据库，

# 2024-08-28
今天将SQLAlchemy的代码和测试代码升级到了2.0版本，删掉了Pydantic的代码，等以后运行起来了再添加Pydantic验证代码，否则前期要考虑的东西太多了，变动太多，不好写验证

# 2024-08-27
这2天一直在弄模型代码，原来的SQLAlchemy+Pydantic模式，模型要分别定义实在是太麻烦了，所以尝试重新用SQLModel重写模型，但各种问题一堆，主要是单表继承它不支持，，好烦

想了想，还是用SQLAlchemy+Pydantic模式，先用SQLAlchemy建一个Engine表，定义所有服务都有的字段，比如name，然后加一个extra_info的json字段，通过Pydantic来定义不同的服务模型，添加到extra_info中
下午试了下，上面的方法还是不行，extra_info字段没想象中的那么好处理

换一个方法吧，每个服务单独一个模型，openai类的定义一个方法基类，然后模型继承这个基类的方法，Feed模型中2个字段记录翻译模型名和id
算了，还是最开始的SQLAlchemy+Pydantic模式吧，

# 2024-08-25
整理了下整体文件架构，清理了暂时没用的文件

准备使用pydantic做数据验证时，才发现之前django真的帮我们做了好多事情

赶紧先把整体框架跑通，细节后面再弄

# 2024-08-23
依旧在重构models和写测试。。。好累。。。

# 2024-08-21
这几天用Cursor写代码，简直太方便了，比copilot和pplx要好很多，开心
总算重构完所有的model了，接下来就是写测试用例排错

# 2024-08-20
看着大家打开了《黑神话悟空》，而我打开了gitpod。。

# 2024-08-19
最近看完了svelte官方的Tutorial的Part 1部分，准备开搞
前端实用svelte，后端使用django ninja

为啥用sveltekit初始化项目后，有这么多文件啊？原来我还得去学sveltekit啊，TNND。。。
官方宣称“提供快如闪电”的开发体验。。。。深表怀疑

算了，还是再试一试reflex吧，啊啊啊啊，要疯了
model直接使用sqlalchemy吧，分开写

# 2024-08-15
还是前后端分离开发吧，reflex这种合并开发有点麻烦，分离开发起码前后端可以独立，后期更换也方便。。。。
那要选哪个前端框架呢，个人看好svelte和htmx，但htmx虽然简洁，但能做的事太少了。。。学下svelte吧。。。哎，这该死的前端
看了10几分钟svelte和django的适配。。。算了算了算了算了，还是试一试reflex吧，怎么简单怎么来吧

reflex的model用的是SQLModel，而SQLModel是重新打包了Pydantic和SQLAlchemy，为了兼容性，还是直接使用SQLAlchemy吧

# 2024-08-14
先把django的model转为reflex的支持的格式，即sqlmodel
然后测试用例也要更上，最好能面向测试开发，省的以后麻烦

啊啊啊啊啊，reflex还不兼容Pydantic 2的版本，官方还在测试，“如果工作量大的话，可能会放弃升级到Pydantic 2，转而使用其它的解决方案”
看到这句话绝望了，也就说，现在写的model，无论如何，将来都得重写？希望不会吧。。。
https://github.com/reflex-dev/reflex/issues/2727

改写个model类都好难，一堆报错。。。。哭

# 2024-08-13
Django发布了5.1.0，应该是有破坏性的更新，导致了翻译器无法构建镜像，只能强制设置为5.0.8后正常了

# 2024-08-12
果然重构好难，没啥头绪，主要是整体框架变更后的设计问题，好多东西要重新考虑。
参考了reflex官方的这篇文章：[构建大型应用程序](https://reflex.dev/blog/2024-03-27-structuring-a-large-app/#first-steps)
然后使用官方的[dashboard模板](https://github.com/reflex-dev/templates/tree/main/dashboard)做为脚手架

# 2024-08-04
终于下定决心重构了，主要原因是Django的admin页面如果要自定义的话，要修改的地方太多了，目前已经有点乱了，所以准备使用Reflex.dev来重构，它后端是Fastapi，前端使用Tailwind CSS，正和我意。
不过reflex还在开发期，希望坑不多

# 2024-07-22
加了OpenL翻译引擎，这个确实不错，集成了很多第三方翻译服务，价格也还可以
还加了Kagi的fastgpt和summarize，fastgpt翻译的话没法关掉网络搜索，总是会有角标，不是很建议用来翻译，不过summarize测试下来还不错，价格的话有点贵，用不起

还有一个bug待修复：google gemini的内容审核参数得加下，实在很不喜欢google的文档，ai有好几个平台，api还不一样，好烦

# 2024-07-04
想使用[django-tasks](https://github.com/RealOrangeOne/django-tasks)来代替huey做为任务管理，但因为还在开发中，所以担心稳定性。主要是因为它的issues中有几个我在使用huey时遇到的问题，且正在解决，但huey还遥遥无期。。。。

添加了huey的shutdown动作，flush所有任务（包括revoke的任务，防止无用的任务堆积）

huey在revoke一个task后，并不会删除这个task，如果之后继续revoke的话，还会重复revoke一遍，本来想搭配restore_by_id来恢复并重新预约任务，但需要存储task的result才能调用reschedule，所以暂时放弃了。

先这样吧，又不是不能用，等django-tasks完善吧

# 2024-07-03
看来还是有必要添加个开发日记，因为有些代码现在看起来很傻逼，但就是不知道当初为啥会这么写，感觉是为了某种边缘情况，但就是记不起来。

开发日记的灵感来源于：https://github.com/cozemble/breezbook/discussions/32

---
今天主要是重构了core/admin.py，原先的代码太乱了。
整个文件直接丢给pplx的claude 3.5 sonnet，效果非常好，基本一次就成了

然后顺便发现了revoke_tasks_by_arg函数的使用逻辑有点问题:
t_feed_force_update不应该调用revoke_tasks_by_arg，因为它是通过update_original_feed task来调用的，只有original_feed才会预约，而update_translated_feed task是实时性的，所以应该在update_translated_feed里检查任务唯一性就行了。

o_feed_force_update也不用revoke_tasks_by_arg，直接放在update_original_feed task更方便直观，否则task完全不知道可能会在哪里被revoke掉

新发布的版本，还是先发布pre release，push到docker dev标签，自己先测试一段时间再push到latest