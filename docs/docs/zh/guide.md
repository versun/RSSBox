## 快速开始

首次登录后，建议点击右上方的修改密码修改默认密码

建议先添加翻译引擎后再添加Feed，除非只是想代理源

首次添加源后，需要一些时间进行翻译和生成，可能会耗时1分钟左右。

状态说明：

<table> <tr> <td><img src="/assets/icon-loading.svg" width="20" height="20"></td> <td>正在处理中</td> </tr> <tr> <td><img src="/assets/icon-yes.svg" width="20" height="20"></td> <td>处理完成/有效</td> </tr> <tr> <td><img src="/assets/icon-no.svg" width="20" height="20"></td> <td>处理失败/无效</td> </tr> </table>

目前状态不会自动更新，请刷新页面以获取最新状态

## 添加翻译引擎
在左侧选择需要添加的翻译引擎提供商，点击 +增加 按钮
![add_translator_1](/assets/add_translator_1.png)
然后输入相关信息后，保存即可

注意检查是否是有效的，如果无效，则需要检查你输入的相关信息后重新保存验证
![translator_list](/assets/translator_list.png)

## 添加源
点击左侧源的 +增加 按钮
![core_feeds](/assets/core_feeds.png)
输入相关信息
![feed_detail](/assets/feed_detail.png)
保存后会跳转到源列表
![feed_list](/assets/feeds_list.png)
等待翻译状态完成后，即可复制下方的的RSS URL地址，并使用你喜欢的阅读器中订阅即可
![translated_feed_status](/assets/feed_status.png)
proxy地址是代理原始源，内容和原始源一致
rss地址是翻译后的订阅地址，json是翻译后的json格式的订阅地址

## 动作
勾选需要操作的源，点击Action，选择对应选项，点击执行(Go)即可
![action](/assets/action.png)

## 单独订阅某个类别的源

`http://127.0.0.1:8000/rss/category/mycategory-1`
