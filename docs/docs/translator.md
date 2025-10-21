# 翻译引擎配置指南

RSSBox支持多种翻译引擎，您可以根据需求、成本和翻译质量选择最适合的方案。

## 🤖 OpenAI API兼容模型

### 🌐 官方OpenAI
**官方网站**：[openai.com/product](https://openai.com/product)

> ⚠️ **重要提示**：目前OpenAI官方暂不支持中国用户使用，请查看[官方支持的国家/地区](https://platform.openai.com/docs/supported-countries)

**配置参数**：
- **Base URL**: `https://api.openai.com/v1`
- **API Key**: 从[OpenAI平台](https://platform.openai.com/api-keys)获取
- **推荐模型**: `gpt-4o-mini`（性价比高）或 `gpt-4o`（高质量）

### 🌏 国内可用替代服务

为了方便国内用户，推荐以下可靠的服务提供商：

#### OhMyGPT️☃️
- **网站**：[OhMyGPT](https://www.ohmygpt.com?aff=FQcnRPCb)
- **特点**：国内稳定访问，中文支持好
- **配置**：获取API Key后直接使用

#### OpenRouter
- **网站**：[OpenRouter](https://openrouter.ai/)
- **特点**：支持多种模型，价格透明
- **Base URL**: `https://openrouter.ai/api/v1`
- **注意**：可能需要科学上网

### 🎯 其他支持OpenAI接口的服务商

| 服务商 | Base URL | 特点 | 推荐指数 |
|---------|----------|------|----------|
| [Anthropic Claude](https://console.anthropic.com/) | `https://api.anthropic.com/v1/` | 高质量对话 | ⭐⭐⭐⭐⭐ |
| [Google Gemini](https://aistudio.google.com/) | `https://generativelanguage.googleapis.com/v1beta/openai/` | 谷歌出品 | ⭐⭐⭐⭐ |
| [Moonshot AI](https://www.moonshot.cn) | `https://api.moonshot.cn/v1` | 国内服务 | ⭐⭐⭐⭐ |
| [豆包(Doubao)](https://www.volcengine.com/product/doubao) | `https://ark.cn-beijing.volces.com/api/v3/` | 字节跳动 | ⭐⭐⭐ |
| [Together AI](https://www.together.ai) | `https://api.together.xyz/v1` | 开源模型 | ⭐⭐⭐ |
| [Groq](https://groq.com/) | `https://api.groq.com/openai/v1` | 高速推理 | ⭐⭐⭐ |

### ⚙️ 配置步骤

1. **获取API凭据**
   - 注册选择的服务平台
   - 获取API Key和相关参数

2. **在RSSBox中配置**
   ```
   名称: 自定义名称（如：OpenAI-GPT4）
   API Key: 您的API密钥
   Base URL: 服务商的API地址
   模型: 具体模型名称（如：gpt-4o-mini）
   ```

3. **测试验证**
   - 保存配置后系统会自动验证
   - 绿色勾号表示配置成功

## 🚀 DeepL

### 简介
**官方网站**：[www.deepl.com/zh/pro-api](https://www.deepl.com/zh/pro-api)

DeepL是专业的翻译服务，在欧洲语言翻译方面表现出色。特别适合需要高质量翻译的用户。

### 💳 支付要求
> ⚠️ **重要提示**：需要提供一张DeepL支持的国家或地区发行的VISA或MASTER信用卡

- **不支持**：国内发行的任何信用卡（包括双币卡和外币卡）
- **替代方案**：可使用第三方代理服务，但请自行承担风险

### 🎯 优劣势对比

**优势**：
- 翻译质量高，特别是欧洲语言
- API稳定性好
- 支持多种文档格式

**列劣势**：
- 支付限制较多
- 成本相对较高
- 支持语言数量有限

## 🆓 LibreTranslate（免费开源）

### 简介
LibreTranslate是一个完全开源的翻译服务，可以自建或使用公共实例。

### ⚙️ 配置方式

#### 使用公共实例
```
名称: LibreTranslate-Free
服务器URL: https://libretranslate.com
API Key: （可选，免费实例无需）
```

### 🎯 优劣势对比

**优势**：
- 完全免费
- 数据隐私保护
- 可自部署

**列劣势**：
- 翻译质量相对较低
- 公共实例可能有速率限制
- 支持语言数量有限

## 🔧 选择建议

### 根据使用场景选择

#### 💼 个人使用/测试
- **首选**: LibreTranslate（免费）
- **备选**: OpenAI API兼容服务（低成本模型）

#### 🏢 小型团队/商用
- **首选**: OpenAI API（gpt-4o-mini）
- **备选**: 国内服务商（Moonshot、豆包等）

#### 🏦 企业级/高质量需求
- **首选**: DeepL Pro
- **备选**: OpenAI API（gpt-4o）

### 成本控制技巧

1. **合理设置最大条目数**：避免过度翻译
2. **选择合适的内容类型**：
   - 标题翻译：成本最低
   - 全文翻译：成本最高
   - 摘要生成：中等成本，但内容更精炼
3. **使用过滤器**：减少不必要的翻译
4. **混合使用**：不同源使用不同成本的引擎

---

💡 **小贴士**：建议同时配置多个不同的翻译引擎，这样可以根据不同RSS源的内容类型和重要性，选择最合适的翻译方案。