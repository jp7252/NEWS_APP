# PRD: DailyBBC — 每日 BBC 科技新闻英语学习邮件

## 1. 产品概述

### 1.1 一句话描述

一个自动化系统，每天从 BBC Technology 板块抓取排名第一的新闻文章，生成中英双语内容、关键词表和英语音频，通过邮件推送给用户，并提供网页端的沉浸式阅读体验。

### 1.2 产品形态

- **主入口**：每日邮件简报（用户零操作即可收到）
- **延伸体验**：轻量网页（点击邮件中的链接跳转）
- **目标用户**：家庭成员，中等英语水平（能读懂部分英文，约 CET-4 水平）
- **使用场景**：轻量学习，碎片时间，无固定场景

### 1.3 核心价值主张

用户打开邮件就能学到 5-8 个实用科技词汇；点进网页就能中英对照读完一篇 BBC 原文，同时听同步高亮的英语音频。全程无需登录、无需下载 App。

### 1.4 不做的事情（明确边界）

- 不做用户账号/登录系统
- 不做单词本/SRS 间隔复习（后续迭代考虑）
- 不做难度分级（固定面向中等水平）
- 不做移动端 App
- 不做板块选择功能（固定 Technology，后续迭代加板块轮换）
- 不做付费/商业化功能
- 不做社交/排行榜功能

---

## 2. 系统架构总览

```
┌─────────────────────────────────────────────────────────────────────┐
│                     每日定时任务 (Cron / GitHub Actions)              │
│                         每天早上 6:00 AM UTC+8                       │
│                                                                     │
│  ┌──────────┐   ┌───────────┐   ┌───────────┐   ┌──────────────┐  │
│  │ 新闻抓取  │──▶│ LLM 处理  │──▶│ 音频生成  │──▶│  内容组装    │  │
│  │ (Module A)│   │(Module B) │   │(Module C) │   │ (Module D)   │  │
│  └──────────┘   └───────────┘   └───────────┘   └──────┬───────┘  │
│                                                         │          │
│                                    ┌────────────────────┼────────┐ │
│                                    ▼                    ▼        │ │
│                              ┌──────────┐        ┌──────────┐   │ │
│                              │ 邮件发送  │        │ 网页部署  │   │ │
│                              │(Module E) │        │(Module F) │   │ │
│                              └──────────┘        └──────────┘   │ │
│                                                                  │ │
└──────────────────────────────────────────────────────────────────┘ │
                                                                     │
                                                                     │
用户视角：                                                            │
                                                                     │
  📧 收到邮件 ──── 阅读标题 + 中文摘要 + 关键词表                      │
       │                                                             │
       └──── 点击「阅读全文」──▶ 🌐 网页端                            │
                                  ├── 中英双语逐段对照                 │
                                  ├── 音频播放 + 逐句同步高亮           │
                                  └── 关键词点击查词                   │
```

---

## 3. 模块详细设计

---

### Module A: 新闻抓取模块

#### 职责

从 BBC Technology RSS Feed 获取当日排名第一的文章，抓取完整正文。

#### 输入

- BBC Technology RSS Feed URL: `http://feeds.bbci.co.uk/news/technology/rss.xml`

#### 处理逻辑

1. 解析 RSS Feed（XML 格式）
2. 取第一条 `<item>`，提取：
   - `title`：英文标题
   - `link`：文章 URL
   - `description`：英文摘要
   - `pubDate`：发布时间
3. 用 `link` 访问文章页面，提取正文内容
4. 正文提取策略：解析 BBC 文章页 HTML，提取 `<article>` 标签内的段落文本
5. 将正文按段落分割为数组：`paragraphs: string[]`

#### 输出数据结构

```typescript
interface RawArticle {
  title: string;           // BBC 英文原标题
  url: string;             // BBC 原文链接
  description: string;     // RSS 中的英文摘要
  pubDate: string;         // ISO 8601 格式
  paragraphs: string[];    // 英文正文段落数组
}
```

#### 技术选型

- Python `feedparser` 解析 RSS
- Python `requests` + `BeautifulSoup` 或 `newspaper3k` 抓取正文
- 备选：`trafilatura`（对新闻网站正文提取效果好）

#### 异常处理

- RSS 获取失败：重试 3 次，间隔 5 分钟
- 正文提取失败：退回使用 RSS `description` 作为正文，邮件标注"摘要版"
- 如果当天 Technology 板块无新文章：复用前一天未使用的文章

---

### Module B: LLM 处理模块

#### 职责

接收英文原文，输出中文翻译、关键词表、中文摘要。

#### 输入

- `RawArticle` 对象（来自 Module A）

#### 处理逻辑——三次 LLM 调用

**调用 1：逐段翻译**

```
System Prompt:
你是一个英中翻译专家。请将以下英文新闻文章逐段翻译为中文。
要求：
- 保持段落一一对应，每个英文段落对应一个中文段落
- 翻译风格：准确自然，适合中等英语水平的中国读者理解
- 专有名词首次出现时用"中文（English）"格式
- 输出为 JSON 数组，每个元素对应一个段落的中文翻译

User: [英文段落数组 JSON]
```

期望输出：

```json
{
  "translated_paragraphs": ["第一段中文翻译...", "第二段中文翻译..."]
}
```

**调用 2：关键词提取**

```
System Prompt:
你是一个英语教学专家。从以下英文新闻文章中提取 5-8 个值得学习的关键词。
选词标准：
- 优先选择科技领域常见但非初级的词汇（适合 CET-4 水平学习者）
- 排除过于简单的词（如 the, is, have, make, good）
- 排除过于专业/罕见的词（普通人不太会再遇到的词）
- 优先选择在其他语境中也实用的词汇

对每个词输出：
- word: 原形（小写）
- phonetic: 国际音标（IPA 格式）
- pos: 词性（n./v./adj./adv. 等）
- definition_cn: 中文释义（简洁，15 字以内）
- definition_en: 英文释义（简洁，一句话）
- context_sentence: 该词在原文中所在的完整句子
- context_translation: 该句子的中文翻译

输出格式为 JSON。

User: [完整英文文章文本]
```

期望输出：

```json
{
  "keywords": [
    {
      "word": "autonomous",
      "phonetic": "/ɔːˈtɒnəməs/",
      "pos": "adj.",
      "definition_cn": "自主的，自治的",
      "definition_en": "acting independently or having the freedom to do so",
      "context_sentence": "The company unveiled its first autonomous delivery robot.",
      "context_translation": "该公司发布了其首款自主配送机器人。"
    }
  ]
}
```

**调用 3：中文摘要生成**

```
System Prompt:
用中文写一段 2-3 句话的新闻摘要，让读者快速了解这篇文章在讲什么。
语气：简洁客观的新闻语气。

User: [完整英文文章文本]
```

期望输出：

```json
{
  "summary_cn": "苹果公司今日发布了最新的 Vision Pro 2 头戴设备，售价较上一代下降40%。新设备搭载了自研 M4 芯片，主打轻量化设计和企业应用场景。"
}
```

#### 输出数据结构

```typescript
interface ProcessedArticle {
  // 来自 Module A 的原始数据
  title: string;
  url: string;
  pubDate: string;

  // LLM 生成的数据
  summary_cn: string;                // 中文摘要
  paragraphs_en: string[];           // 英文段落数组
  paragraphs_cn: string[];           // 中文段落数组（与英文一一对应）
  keywords: Keyword[];               // 关键词数组
}

interface Keyword {
  word: string;
  phonetic: string;
  pos: string;
  definition_cn: string;
  definition_en: string;
  context_sentence: string;
  context_translation: string;
}
```

#### 技术选型

- LLM API：**DeepSeek V4**（中英翻译质量最佳，成本最低）
  - Model: `deepseek-chat`
  - 备选：OpenAI GPT-4o-mini（质量稍低但稳定）
- 所有 LLM 调用强制 JSON 输出模式
- 每篇文章约消耗 3000-5000 tokens 输入 + 2000-3000 tokens 输出

#### 异常处理

- LLM API 调用失败：重试 3 次
- JSON 解析失败：重试，若仍失败则跳过当天推送并告警
- 段落数不匹配：记录 warning，以英文段落数为准截断或补充

---

### Module C: 音频生成模块

#### 职责

将英文文章生成 TTS 音频文件，并导出逐句时间戳用于前端同步高亮。

#### 输入

- `paragraphs_en: string[]`（英文段落数组）

#### 处理逻辑

1. 将所有英文段落拼接，按句子分割（用 `.` `!` `?` 结尾判断）
2. 为每个句子调用 Edge TTS 生成语音
3. 拼接所有句子音频为一个完整的 MP3 文件
4. 收集每个句子的起止时间戳（利用 Edge TTS 的 word boundary 事件）
5. 生成时间戳 JSON 文件

#### 关键技术细节：Edge TTS Word Boundary

Edge TTS 的 Python 库 `edge-tts` 支持 `WordBoundary` 回调事件，返回每个词的：
- `offset`: 音频中的起始时间（微秒）
- `duration`: 持续时间（微秒）
- `text`: 对应的文本

利用这些数据，可以计算出每个句子的起始时间和结束时间。

#### 实现方案

```python
# 伪代码示意
import edge_tts
import json

async def generate_audio(sentences: list[str]):
    """
    对每个句子独立生成音频片段，记录累计偏移量。
    最后拼接所有片段为一个完整 MP3。
    """
    timeline = []
    audio_chunks = []
    cumulative_offset_ms = 0

    for i, sentence in enumerate(sentences):
        communicate = edge_tts.Communicate(sentence, voice="en-US-AriaNeural")

        chunk_audio = b""
        sentence_duration_ms = 0

        async for event in communicate.stream():
            if event["type"] == "audio":
                chunk_audio += event["data"]
            elif event["type"] == "WordBoundary":
                # 更新句子时长
                word_end = (event["offset"] + event["duration"]) / 10000  # 转为毫秒
                sentence_duration_ms = max(sentence_duration_ms, word_end)

        timeline.append({
            "index": i,
            "text": sentence,
            "start_ms": cumulative_offset_ms,
            "end_ms": cumulative_offset_ms + sentence_duration_ms,
            "paragraph_index": get_paragraph_index(i)  # 映射回段落
        })

        audio_chunks.append(chunk_audio)
        cumulative_offset_ms += sentence_duration_ms

    # 拼接音频并写入文件
    full_audio = b"".join(audio_chunks)
    return full_audio, timeline
```

#### 输出

```typescript
// 音频文件
// article_YYYY-MM-DD.mp3

// 时间戳文件
interface AudioTimeline {
  sentences: TimelineEntry[];
  total_duration_ms: number;
}

interface TimelineEntry {
  index: number;            // 句子序号（全文）
  text: string;             // 句子英文原文
  start_ms: number;         // 起始时间（毫秒）
  end_ms: number;           // 结束时间（毫秒）
  paragraph_index: number;  // 所属段落序号（对应 paragraphs_en 的 index）
}
```

#### Edge TTS Voice 选择

- 推荐：`en-US-AriaNeural`（女声，清晰自然，语速适中）
- 备选：`en-US-GuyNeural`（男声）
- 语速调节：`--rate="-10%"`（略慢，适合学习者）

#### 异常处理

- Edge TTS 服务不可用：重试 3 次，最终 fallback 标记为"本期无音频"
- 音频拼接异常：记录错误，仍发送邮件但不含音频链接

---

### Module D: 内容组装模块

#### 职责

将处理好的文章数据、音频、时间戳组装为邮件内容和网页文件。

#### 输入

- `ProcessedArticle`（来自 Module B）
- `audio.mp3` + `timeline.json`（来自 Module C）

#### 处理逻辑

**1. 生成网页 HTML 文件**

生成一个自包含的静态 HTML 页面（单个 `.html` 文件），包含：

- 文章标题（英文 + 中文）
- 中英双语逐段对照区域
- 音频播放器 + 同步高亮逻辑
- 关键词高亮 + 点击弹窗

**2. 生成邮件 HTML 内容**

邮件 HTML 结构简洁，兼容主流邮件客户端。

#### 网页 HTML 详细结构

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>DailyBBC - {日期}</title>
  <style>
    /* 内联样式，保证单文件可用 */
  </style>
</head>
<body>

  <!-- 顶部标题区 -->
  <header>
    <p class="date">{pubDate}</p>
    <h1 class="title-en">{英文标题}</h1>
    <p class="source">Source: <a href="{url}">BBC Technology</a></p>
  </header>

  <!-- 音频播放器 -->
  <div class="audio-player">
    <audio id="article-audio" src="{audio_url}"></audio>
    <button id="play-btn">▶ 播放文章音频</button>
    <span id="progress">00:00 / 03:45</span>
  </div>

  <!-- 正文区：中英对照 -->
  <main>
    <!-- 每个段落一组 -->
    <div class="paragraph-pair" data-para-index="0">
      <p class="en" data-para-index="0">
        <!-- 关键词用 span 包裹 -->
        The company unveiled its first
        <span class="keyword"
              data-word="autonomous"
              data-phonetic="/ɔːˈtɒnəməs/"
              data-pos="adj."
              data-def="自主的，自治的">autonomous</span>
        delivery robot yesterday.
      </p>
      <p class="cn" data-para-index="0">
        该公司昨日发布了其首款自主配送机器人。
      </p>
    </div>
    <!-- 更多段落... -->
  </main>

  <!-- 关键词弹窗（全局共用一个，动态填充内容） -->
  <div id="keyword-popup" class="popup hidden">
    <div class="popup-word"></div>
    <div class="popup-phonetic"></div>
    <div class="popup-pos"></div>
    <div class="popup-def"></div>
    <button class="popup-close">✕</button>
  </div>

  <script>
    // 时间戳数据（构建时内联注入）
    const TIMELINE = {timeline_json};

    // 音频同步高亮逻辑
    const audio = document.getElementById('article-audio');

    audio.addEventListener('timeupdate', () => {
      const currentMs = audio.currentTime * 1000;
      TIMELINE.sentences.forEach((entry, i) => {
        const el = document.querySelector(`[data-sentence-index="${i}"]`);
        if (!el) return;
        if (currentMs >= entry.start_ms && currentMs < entry.end_ms) {
          el.classList.add('highlight');
          el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        } else {
          el.classList.remove('highlight');
        }
      });
    });

    // 关键词点击弹窗逻辑
    document.querySelectorAll('.keyword').forEach(el => {
      el.addEventListener('click', (e) => {
        const popup = document.getElementById('keyword-popup');
        popup.querySelector('.popup-word').textContent = el.dataset.word;
        popup.querySelector('.popup-phonetic').textContent = el.dataset.phonetic;
        popup.querySelector('.popup-pos').textContent = el.dataset.pos;
        popup.querySelector('.popup-def').textContent = el.dataset.def;
        popup.classList.remove('hidden');
      });
    });
  </script>

</body>
</html>
```

#### 网页样式要求

```css
/* 核心样式指引（供开发参考） */

/* 全局 */
body {
  max-width: 720px;
  margin: 0 auto;
  padding: 20px;
  font-family: -apple-system, "Segoe UI", "PingFang SC", sans-serif;
  line-height: 1.8;
  color: #333;
}

/* 段落对 */
.paragraph-pair {
  margin-bottom: 24px;
  border-left: 3px solid #e0e0e0;
  padding-left: 16px;
}
.paragraph-pair .en {
  font-size: 16px;
  color: #1a1a1a;
  margin-bottom: 8px;
}
.paragraph-pair .cn {
  font-size: 14px;
  color: #666;
}

/* 音频同步高亮 */
.highlight {
  background-color: #FFF3CD;
  border-radius: 3px;
  padding: 2px 0;
  transition: background-color 0.3s ease;
}

/* 关键词 */
.keyword {
  color: #0066CC;
  border-bottom: 1px dashed #0066CC;
  cursor: pointer;
}

/* 弹窗 */
.popup {
  position: fixed;
  bottom: 20px;
  left: 50%;
  transform: translateX(-50%);
  background: white;
  border: 1px solid #ddd;
  border-radius: 12px;
  padding: 16px 20px;
  box-shadow: 0 4px 20px rgba(0,0,0,0.15);
  max-width: 340px;
  width: 90%;
  z-index: 100;
}
.popup.hidden { display: none; }

/* 移动端适配 */
@media (max-width: 480px) {
  body { padding: 12px; }
  .paragraph-pair .en { font-size: 15px; }
}
```

#### 邮件 HTML 结构

```
┌─────────────────────────────────────────┐
│  📰 DailyBBC · 每日科技英语 · {日期}     │
├─────────────────────────────────────────┤
│                                         │
│  TODAY'S HEADLINE                       │
│  {英文标题}                              │
│                                         │
│  📝 {中文摘要，2-3 句话}                  │
│                                         │
│  ──────────────────────                 │
│                                         │
│  📖 今日关键词                           │
│                                         │
│  1. autonomous /ɔːˈtɒnəməs/ adj.       │
│     自主的，自治的                        │
│                                         │
│  2. unveil /ʌnˈveɪl/ v.                │
│     揭开，发布                           │
│                                         │
│  3. ...（共 5-8 个词）                   │
│                                         │
│  ──────────────────────                 │
│                                         │
│  🔗 [阅读全文 + 收听音频]  ← 按钮链接    │
│     （点击跳转到网页端）                  │
│                                         │
│  ──────────────────────                 │
│  BBC Technology · DailyBBC              │
└─────────────────────────────────────────┘
```

邮件 HTML 注意事项：
- 使用 `<table>` 布局（邮件客户端兼容性）
- 所有样式内联（`style` 属性）
- 不使用 JavaScript
- 不使用外部 CSS 文件
- 按钮用 `<a>` 标签 + 内联背景色模拟
- 宽度设为 600px 居中

---

### Module E: 邮件发送模块

#### 职责

将组装好的邮件 HTML 发送给订阅用户。

#### 技术选型（按推荐顺序）

| 方案 | 免费额度 | 适用场景 |
|------|---------|---------|
| **Resend** | 100 封/天 | 推荐，API 简洁，开发体验好 |
| **SendGrid** | 100 封/天 | 成熟稳定 |
| Gmail SMTP | 500 封/天 | 最简单，家庭用足够 |
| Amazon SES | 前 62,000 封/月免费 | 如已有 AWS 账号 |

#### 推荐方案：Gmail SMTP（最简单）

```python
# 家庭使用场景，直接用 Gmail SMTP 即可
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

def send_email(to_addresses: list[str], subject: str, html_content: str):
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = 'your-email@gmail.com'
    msg['To'] = ', '.join(to_addresses)
    msg.attach(MIMEText(html_content, 'html'))

    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
        server.login('your-email@gmail.com', 'app-password')
        server.send_message(msg)
```

#### 配置项

```yaml
email:
  sender: "your-email@gmail.com"
  app_password: "${GMAIL_APP_PASSWORD}"  # 环境变量
  recipients:
    - "family-member-1@example.com"
    - "family-member-2@example.com"
  subject_template: "📰 DailyBBC · {title_short} · {date}"
  send_time: "06:00"  # UTC+8
```

---

### Module F: 网页托管模块

#### 职责

将生成的网页 HTML 和音频文件发布到公网可访问的 URL。

#### 技术选型（按推荐顺序）

| 方案 | 成本 | 适用场景 |
|------|------|---------|
| **GitHub Pages** | 免费 | 推荐，自动部署，自定义域名 |
| Cloudflare Pages | 免费 | 速度快，国内访问较好 |
| Vercel | 免费 | 如果后续想加动态功能 |
| 自有服务器 | ~$5/月 | 完全可控 |

#### 推荐方案：GitHub Pages

```
仓库结构：
dailybbc.github.io/
├── index.html              # 首页（可选，列出历史文章）
├── articles/
│   ├── 2026-04-15.html     # 每日文章页面
│   ├── 2026-04-14.html
│   └── ...
├── audio/
│   ├── 2026-04-15.mp3      # 音频文件
│   └── ...
└── assets/
    └── style.css            # 公共样式（可选）
```

每日任务的最后一步：
1. 将生成的 HTML 文件写入 `articles/{date}.html`
2. 将音频文件写入 `audio/{date}.mp3`
3. `git add . && git commit && git push`
4. GitHub Pages 自动部署

邮件中的链接格式：`https://{username}.github.io/articles/2026-04-15.html`

#### 音频托管注意事项

- GitHub Pages 单文件限制 100MB，一篇文章音频通常 2-5MB，完全够用
- 仓库总大小限制 1GB，按每天 5MB 计算可撑 200 天
- 到达限制后的方案：迁移到 Cloudflare R2（免费 10GB 存储）或清理旧文件

---

## 4. 自动化 Pipeline（Module G: 编排层）

### 每日执行流程

```python
# main.py —— 每日执行入口
import asyncio
from datetime import datetime

async def daily_pipeline():
    date_str = datetime.now().strftime("%Y-%m-%d")
    print(f"[{date_str}] Starting daily pipeline...")

    # Step 1: 抓取新闻
    article = fetch_bbc_tech_top_article()
    # → RawArticle

    # Step 2: LLM 处理
    processed = await process_with_llm(article)
    # → ProcessedArticle (含翻译、关键词、摘要)

    # Step 3: 生成音频 + 时间戳
    audio_bytes, timeline = await generate_audio(processed.paragraphs_en)
    save_audio(audio_bytes, f"audio/{date_str}.mp3")
    # → audio file + timeline JSON

    # Step 4: 组装网页 HTML
    html = build_article_page(processed, timeline, date_str)
    save_html(html, f"articles/{date_str}.html")

    # Step 5: 部署网页（git push）
    deploy_to_github_pages()

    # Step 6: 组装并发送邮件
    email_html = build_email(processed, date_str)
    send_email(
        recipients=CONFIG["email"]["recipients"],
        subject=f"📰 DailyBBC · {processed.title[:30]} · {date_str}",
        html_content=email_html
    )

    print(f"[{date_str}] Pipeline completed successfully!")

if __name__ == "__main__":
    asyncio.run(daily_pipeline())
```

### 定时调度方案

#### 推荐：GitHub Actions（免费、无需服务器）

```yaml
# .github/workflows/daily.yml
name: Daily BBC News Pipeline

on:
  schedule:
    - cron: '0 22 * * *'  # UTC 22:00 = 北京时间早上 6:00
  workflow_dispatch:        # 允许手动触发（调试用）

jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Run pipeline
        env:
          DEEPSEEK_API_KEY: ${{ secrets.DEEPSEEK_API_KEY }}
          GMAIL_APP_PASSWORD: ${{ secrets.GMAIL_APP_PASSWORD }}
        run: python main.py

      - name: Deploy to GitHub Pages
        run: |
          git config user.name "DailyBBC Bot"
          git config user.email "bot@dailybbc.com"
          git add articles/ audio/
          git commit -m "📰 Daily update: $(date +%Y-%m-%d)" || true
          git push
```

---

## 5. 技术栈汇总

| 层面 | 技术 | 理由 |
|------|------|------|
| **语言** | Python 3.11+ | 生态最全（RSS解析、爬虫、TTS、LLM SDK） |
| **新闻抓取** | feedparser + BeautifulSoup | 成熟稳定，BBC RSS 解析可靠 |
| **LLM** | DeepSeek API | 中英翻译质量最佳 + 成本最低 |
| **TTS** | edge-tts (Python) | 免费 + 音质好 + 支持 word boundary |
| **邮件** | Gmail SMTP (smtplib) | 零成本，家庭规模完全够用 |
| **网页托管** | GitHub Pages | 免费 + 自动部署 + 自定义域名 |
| **定时调度** | GitHub Actions | 免费 + 无需维护服务器 |
| **网页前端** | 原生 HTML/CSS/JS | 单文件，无需构建工具，无依赖 |

### Python 依赖清单

```
# requirements.txt
feedparser>=6.0
beautifulsoup4>=4.12
requests>=2.31
edge-tts>=6.1
openai>=1.30          # DeepSeek 兼容 OpenAI SDK
jinja2>=3.1           # HTML 模板渲染
python-dotenv>=1.0    # 环境变量管理
```

---

## 6. 文件/目录结构

```
dailybbc/
├── main.py                     # Pipeline 入口
├── requirements.txt
├── .env                        # 本地环境变量（不提交）
├── .env.example                # 环境变量模板
│
├── config.yaml                 # 配置文件（收件人、时间等）
│
├── src/
│   ├── __init__.py
│   ├── fetcher.py              # Module A: 新闻抓取
│   ├── processor.py            # Module B: LLM 翻译/关键词/摘要
│   ├── audio.py                # Module C: TTS 音频 + 时间戳
│   ├── assembler.py            # Module D: 内容组装（网页 + 邮件）
│   ├── mailer.py               # Module E: 邮件发送
│   └── deployer.py             # Module F: GitHub Pages 部署
│
├── templates/
│   ├── article_page.html       # 网页模板（Jinja2）
│   └── email.html              # 邮件模板（Jinja2）
│
├── articles/                   # 生成的文章页面（GitHub Pages）
│   └── 2026-04-15.html
│
├── audio/                      # 生成的音频文件
│   └── 2026-04-15.mp3
│
├── data/                       # 中间数据（可选，调试用）
│   └── 2026-04-15.json         # 当天的 ProcessedArticle JSON
│
├── .github/
│   └── workflows/
│       └── daily.yml           # GitHub Actions 定时任务
│
└── README.md
```

---

## 7. 核心数据流

```
BBC RSS Feed
    │
    ▼
[fetcher.py] ── RawArticle JSON ──▶ [processor.py]
                                        │
                                        ├── LLM Call 1: 逐段翻译
                                        ├── LLM Call 2: 关键词提取
                                        └── LLM Call 3: 中文摘要
                                        │
                                        ▼
                                   ProcessedArticle JSON
                                        │
                          ┌─────────────┼─────────────┐
                          ▼             ▼             ▼
                    [audio.py]   [assembler.py]  [assembler.py]
                        │         (网页 HTML)    (邮件 HTML)
                        │             │             │
                   MP3 + Timeline     │             │
                        │             │             │
                        ▼             ▼             ▼
                  [deployer.py]                [mailer.py]
                  (git push to                 (Gmail SMTP)
                   GitHub Pages)
                        │                          │
                        ▼                          ▼
                   🌐 公网网页              📧 用户收到邮件
```

---

## 8. 环境变量

```bash
# .env.example
DEEPSEEK_API_KEY=sk-xxxx          # DeepSeek API 密钥
GMAIL_ADDRESS=your-email@gmail.com
GMAIL_APP_PASSWORD=xxxx-xxxx       # Gmail 应用专用密码（非登录密码）
GITHUB_TOKEN=ghp_xxxx             # 用于 GitHub Pages 推送（Actions 中自动提供）
```

---

## 9. 后续迭代方向（不在 MVP 范围内）

按优先级排列：

1. **板块轮换**：周一至周日轮换 Technology / Business / World / Science / Sports 等板块
2. **周末词汇回顾邮件**：汇总本周所有关键词，以简单测试题形式发送
3. **历史文章索引页**：在 GitHub Pages 上生成一个 index.html，列出所有历史文章
4. **难度自适应**：根据反馈调整关键词选择的难度
5. **音频语速调节**：生成两个版本的音频（正常/慢速）
6. **RSS 板块订阅管理**：让用户通过回复邮件选择感兴趣的板块
