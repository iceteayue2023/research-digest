# 科研文献日报

每天自动抓取你关注的期刊（完整列表见 `scripts/config.yaml`，目前覆盖 Nature 系列、Science、PNAS、
AGU/Copernicus 地球系统期刊、BES/Springer 生态学期刊等共24种），用 DeepSeek 按你的研究方向
（大型食草动物 rewilding 对土壤碳的影响）打分、筛选、生成中文摘要与深度解读，并生成一个可以
"添加到手机主屏幕"的网页 App。

## 工作原理

```
GitHub Actions（每天定时）
  └─ scripts/fetch_digest.py
       ├─ 抓取 scripts/config.yaml 里配置的期刊 RSS
       ├─ 按关键词/主题粗筛，减少无关文章
       ├─ 用 Crossref API 尝试补全作者单位
       ├─ 调用 DeepSeek API 打分 + 生成中文摘要/翻译/相关性说明
       ├─ 对入选文章额外生成深度解读、ORCID作者简介、OpenAlex相关文献
       └─ 写入 docs/data/YYYY-MM-DD.json 和 docs/data/latest.json
  └─ 自动 commit & push 结果
GitHub Pages（托管 docs/ 目录）
  └─ 手机浏览器打开网页，可"添加到主屏幕"，像一个 App
```

## 首次部署步骤

1. **在 GitHub 上创建一个新仓库**（比如叫 `research-digest`），设为 Public 或 Private 均可
   （Private 仓库也能免费用 GitHub Pages 和 Actions）。

2. **把本地代码推送上去**：
   ```
   cd research-digest
   git remote add origin https://github.com/<你的用户名>/research-digest.git
   git branch -M main
   git push -u origin main
   ```
   推送时如果要求登录，用你的 GitHub 账号密码或 Personal Access Token 登录即可
   （现代 GitHub 不再支持密码登录 git，如果失败请用浏览器登录一次 GitHub Desktop，
   或生成一个 token: Settings → Developer settings → Personal access tokens）。

3. **添加 DeepSeek API Key 作为仓库密钥**：
   仓库页面 → Settings → Secrets and variables → Actions → New repository secret
   - Name: `DEEPSEEK_API_KEY`
   - Value: 你在 platform.deepseek.com 生成的 key

4. **开启 GitHub Pages**：
   仓库页面 → Settings → Pages → Build and deployment → Source 选择 "Deploy from a branch"，
   Branch 选择 `main`，文件夹选择 `/docs`，保存。
   几分钟后会出现网址，形如 `https://<你的用户名>.github.io/research-digest/`。

5. **手动触发一次抓取**（不用等到明天）：
   仓库页面 → Actions → Daily Research Digest → Run workflow。
   跑完后 `docs/data/` 下会多出今天的 json 文件并自动 commit。

6. **手机上打开** Pages 网址，Safari/Chrome 菜单里选择"添加到主屏幕"，
   桌面上就会出现一个"文献日报"图标，点开是全屏 App 体验。

## 以后如何调整

- **增删关键词/期刊/研究方向描述**：直接编辑 `scripts/config.yaml`，commit push 后，
  下次定时任务（每天北京时间 8:00）会用新配置。
- **调整相关性门槛**：`config.yaml` 里的 `relevance_threshold`（0-10，越高越严格）。
- **换其他DeepSeek模型**（如 deepseek-reasoner）：`config.yaml` 里的 `model` 字段。
- **想立刻看到新配置的效果**：Actions 页面手动 Run workflow，不用等第二天。

## 成本

- GitHub Actions、GitHub Pages：免费额度内完全够用。
- DeepSeek API：按 token 计费，比同级别模型便宜很多，每天几十篇文章的打分+摘要+深度解读，
  成本通常在每月几毛到1美元左右，可在 platform.deepseek.com 的用量页面查看实际花费。

## 已知局限

- 作者单位信息依赖 Crossref 是否收录该期刊的完整元数据，Nature/Wiley 系列通常有，
  部分期刊可能拿不到，会显示"未提供，见原文"。
- RSS 只能看到期刊官方发布的最新目录，如果某期刊 RSS 地址后续失效，需要去期刊官网重新找
  RSS/Atom 链接更新到 `config.yaml`。
