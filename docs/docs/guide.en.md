# User Guide

Welcome to RSSBox! This guide will help you quickly master all core features, from basic configuration to advanced functionality.

## 🚀 Quick Start

### First Login
1. After logging in with the default account, **strongly recommend** clicking the top right to change your password
2. Recommend configuring translation engines first before adding RSS sources (unless you only need proxy functionality)
3. After adding sources for the first time, allow 1-2 minutes for processing, please be patient

### Status Indicators

<table> <tr> <td><img src="/assets/icon-loading.svg" width="20" height="20"></td> <td>Processing</td> </tr> <tr> <td><img src="/assets/icon-yes.svg" width="20" height="20"></td> <td>Completed/Valid</td> </tr> <tr> <td><img src="/assets/icon-no.svg" width="20" height="20"></td> <td>Failed/Invalid</td> </tr> </table>

> 💡 **Tip**: Status does not auto-update, please refresh the page for latest status

## ⚙️ Translation Engine Configuration

### Adding Translation Engines
1. Select the desired translation engine type from the left navigation
2. Click **+Add** button
3. Fill in the relevant configuration information
4. Save and verify validity

### Verify Engine Status
- Green checkmark: Configuration valid, ready to use
- Red X: Configuration invalid, please check API key and other information

## 📡 RSS Source Management

### Adding RSS Sources
1. Click **+Add** button in the left **Sources** section
2. Fill in RSS source basic information
3. Select translation engine and translation strategy
4. Configure update frequency and filtering rules

### Subscribe to Translation Results
After configuration is complete, wait for translation status to complete, then you can get subscription addresses:

- **proxy address**: Proxies the original source, content identical to original
- **rss address**: Translated RSS subscription address
- **json address**: Translated JSON format subscription address

## 🔧 Advanced Features

### Content Filtering

#### Keyword Filtering
- Support include/exclude modes
- Can filter titles or content

#### AI Smart Filtering
- Semantic understanding-based intelligent content filtering
- Can set filtering topics and criteria
- More accurate and flexible than keyword filtering

### Tag Management
Through the tag system you can:
- Organize multiple related RSS sources together
- Create topic-categorized aggregated sources
- Apply unified filtering rules

## 📋 Practical Tips

### Cost Control Recommendations
1. **Set reasonable maximum entries**: Avoid excessive translation
2. **Choose appropriate content type**: Title translation has lowest cost
3. **Use free engines**: LibreTranslate and other open source solutions
4. **Regular Token statistics review**: Monitor translation costs

### Performance Optimization
1. **Appropriate update frequency**: Avoid frequent updates wasting resources
2. **Use filters**: Reduce unnecessary translations
3. **Batch operations**: Improve management efficiency

## 🆘 Common Issues

When encountering problems, please first check the [FAQ page](/en/faq/). If still unresolved:

1. **Submit Issue**: [GitHub Issues](https://github.com/versun/rssbox/issues)
2. **Community Discussion**: [Telegram Group](https://t.me/rssboxapp)
3. **Check Logs**: Review server error logs

---

🎉 **Enjoy using RSSBox!** If RSSBox has helped you, welcome to give the project a Star to support my development.
