# Translation Engine Configuration Guide

RSS Translator supports multiple translation engines. You can choose the most suitable solution based on your needs, cost, and translation quality requirements.

## 🤖 OpenAI API Compatible Models

### 🌐 Official OpenAI
**Official Website**: [openai.com/product](https://openai.com/product)

> ⚠️ **Important Notice**: Currently OpenAI official does not support users from China, please check [officially supported countries/regions](https://platform.openai.com/docs/supported-countries)

**Configuration Parameters**:
- **Base URL**: `https://api.openai.com/v1`
- **API Key**: Obtain from [OpenAI Platform](https://platform.openai.com/api-keys)
- **Recommended Models**: `gpt-4o-mini` (cost-effective) or `gpt-4o` (high quality)

### 🌏 Alternative Services Available in China

For the convenience of domestic users, we recommend the following reliable service providers:

#### OhMyGPT️☃️
- **Website**: [OhMyGPT](https://www.ohmygpt.com?aff=FQcnRPCb)
- **Features**: Stable access in China, good Chinese support
- **Configuration**: Use directly after obtaining API Key

#### OpenRouter
- **Website**: [OpenRouter](https://openrouter.ai/)
- **Features**: Supports multiple models, transparent pricing
- **Base URL**: `https://openrouter.ai/api/v1`
- **Note**: May require VPN access

### 🎯 Other Services Supporting OpenAI Interface

| Service Provider | Base URL | Features | Rating |
|---------|----------|------|----------|
| [Anthropic Claude](https://console.anthropic.com/) | `https://api.anthropic.com/v1/` | High-quality dialogue | ⭐⭐⭐⭐⭐ |
| [Google Gemini](https://aistudio.google.com/) | `https://generativelanguage.googleapis.com/v1beta/openai/` | Google product | ⭐⭐⭐⭐ |
| [Moonshot AI](https://www.moonshot.cn) | `https://api.moonshot.cn/v1` | Domestic service | ⭐⭐⭐⭐ |
| [Doubao](https://www.volcengine.com/product/doubao) | `https://ark.cn-beijing.volces.com/api/v3/` | ByteDance | ⭐⭐⭐ |
| [Together AI](https://www.together.ai) | `https://api.together.xyz/v1` | Open source models | ⭐⭐⭐ |
| [Groq](https://groq.com/) | `https://api.groq.com/openai/v1` | High-speed inference | ⭐⭐⭐ |

### ⚙️ Configuration Steps

1. **Obtain API Credentials**
   - Register with your chosen service platform
   - Obtain API Key and related parameters

2. **Configure in RSS Translator**
   ```
   Name: Custom name (e.g., OpenAI-GPT4)
   API Key: Your API key
   Base URL: Service provider's API address
   Model: Specific model name (e.g., gpt-4o-mini)
   ```

3. **Test and Verify**
   - System will automatically verify after saving configuration
   - Green checkmark indicates successful configuration

## 🚀 DeepL

### Introduction
**Official Website**: [www.deepl.com/zh/pro-api](https://www.deepl.com/zh/pro-api)

DeepL is a professional translation service that excels in European language translation. Particularly suitable for users requiring high-quality translation.

### 💳 Payment Requirements
> ⚠️ **Important Notice**: Requires a VISA or MASTER credit card issued by a country or region supported by DeepL

- **Not Supported**: Any credit cards issued domestically in China (including dual-currency and foreign currency cards)
- **Alternative**: Third-party proxy services available, but use at your own risk

### 🎯 Pros and Cons Comparison

**Advantages**:
- High translation quality, especially for European languages
- Good API stability
- Supports multiple document formats

**Disadvantages**:
- Many payment restrictions
- Relatively high cost
- Limited number of supported languages

## 🆓 LibreTranslate (Free & Open Source)

### Introduction
LibreTranslate is a completely open source translation service that can be self-hosted or use public instances.

### ⚙️ Configuration Methods

#### Using Public Instance
```
Name: LibreTranslate-Free
Server URL: https://libretranslate.com
API Key: (Optional, not needed for free instances)
```

### 🎯 Pros and Cons Comparison

**Advantages**:
- Completely free
- Data privacy protection
- Can be self-deployed

**Disadvantages**:
- Relatively lower translation quality
- Public instances may have rate limits
- Limited number of supported languages

## 🔧 Selection Recommendations

### Choose Based on Use Case

#### 💼 Personal Use/Testing
- **First Choice**: LibreTranslate (free)
- **Alternative**: OpenAI API compatible services (low-cost models)

#### 🏢 Small Teams/Commercial Use
- **First Choice**: OpenAI API (gpt-4o-mini)
- **Alternative**: Domestic service providers (Moonshot, Doubao, etc.)

#### 🏦 Enterprise/High Quality Requirements
- **First Choice**: DeepL Pro
- **Alternative**: OpenAI API (gpt-4o)

### Cost Control Tips

1. **Set reasonable maximum entries**: Avoid excessive translation
2. **Choose appropriate content type**:
   - Title translation: Lowest cost
   - Full-text translation: Highest cost
   - Summary generation: Medium cost, but more refined content
3. **Use filters**: Reduce unnecessary translations
4. **Mixed usage**: Use different cost engines for different sources

---

💡 **Pro Tip**: Recommend configuring multiple different translation engines simultaneously, so you can choose the most suitable translation solution based on different RSS source content types and importance levels.