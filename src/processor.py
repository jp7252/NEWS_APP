"""Module B: LLM processing — translate, extract keywords, summarise."""

import json
import logging
from dataclasses import dataclass, field

from openai import OpenAI

from src.fetcher import RawArticle

logger = logging.getLogger(__name__)

MAX_RETRIES = 3


@dataclass
class Keyword:
    word: str
    phonetic: str
    pos: str
    definition_cn: str
    definition_en: str
    context_sentence: str
    context_translation: str


@dataclass
class ProcessedArticle:
    title: str
    url: str
    pub_date: str
    summary_cn: str
    paragraphs_en: list[str]
    paragraphs_cn: list[str]
    keywords: list[Keyword] = field(default_factory=list)
    lead_image_url: str | None = None


def _call_llm(client: OpenAI, system: str, user: str, model: str) -> dict:
    """Call the LLM with retries and JSON parsing."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = client.chat.completions.create(
                model=model,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.3,
            )
            text = resp.choices[0].message.content
            return json.loads(text)
        except (json.JSONDecodeError, Exception) as e:
            logger.warning("LLM call attempt %d/%d failed: %s", attempt, MAX_RETRIES, e)
            if attempt == MAX_RETRIES:
                raise


def _translate(client: OpenAI, model: str, paragraphs: list[str]) -> list[str]:
    system = (
        "你是一个英中翻译专家。请将以下英文新闻文章逐段翻译为中文。\n"
        "要求：\n"
        "- 保持段落一一对应，每个英文段落对应一个中文段落\n"
        "- 翻译风格：准确自然，适合中等英语水平的中国读者理解\n"
        '- 专有名词首次出现时用\u201c中文（English）\u201d格式\n'
        '- 输出为 JSON：{"translated_paragraphs": ["段落1", "段落2", ...]}'
    )
    user = json.dumps(paragraphs, ensure_ascii=False)
    data = _call_llm(client, system, user, model)
    translated = data.get("translated_paragraphs", [])

    if len(translated) != len(paragraphs):
        logger.warning(
            "Paragraph count mismatch: EN=%d, CN=%d — truncating to match EN",
            len(paragraphs), len(translated),
        )
        if len(translated) < len(paragraphs):
            translated.extend(["（翻译缺失）"] * (len(paragraphs) - len(translated)))
        else:
            translated = translated[: len(paragraphs)]

    return translated


def _extract_keywords(client: OpenAI, model: str, full_text: str) -> list[Keyword]:
    system = (
        "你是一个英语教学专家。从以下英文新闻文章中提取 5-8 个值得学习的关键词。\n"
        "选词标准：\n"
        "- 优先选择科技领域常见但非初级的词汇（适合 CET-4 水平学习者）\n"
        "- 排除过于简单的词（如 the, is, have, make, good）\n"
        "- 排除过于专业/罕见的词\n"
        "- 优先选择在其他语境中也实用的词汇\n\n"
        "对每个词输出：\n"
        "- word: 原形（小写）\n"
        "- phonetic: 国际音标（IPA 格式）\n"
        "- pos: 词性（n./v./adj./adv. 等）\n"
        "- definition_cn: 中文释义（简洁，15 字以内）\n"
        "- definition_en: 英文释义（简洁，一句话）\n"
        "- context_sentence: 该词在原文中所在的完整句子\n"
        "- context_translation: 该句子的中文翻译\n\n"
        '输出格式为 JSON：{"keywords": [...]}'
    )
    data = _call_llm(client, system, full_text, model)
    raw_keywords = data.get("keywords", [])

    keywords = []
    for kw in raw_keywords:
        try:
            keywords.append(Keyword(
                word=kw["word"],
                phonetic=kw.get("phonetic", ""),
                pos=kw.get("pos", ""),
                definition_cn=kw.get("definition_cn", ""),
                definition_en=kw.get("definition_en", ""),
                context_sentence=kw.get("context_sentence", ""),
                context_translation=kw.get("context_translation", ""),
            ))
        except KeyError as e:
            logger.warning("Skipping malformed keyword entry: %s", e)

    return keywords


def _summarise(client: OpenAI, model: str, full_text: str) -> str:
    system = (
        "用中文写一段 2-3 句话的新闻摘要，让读者快速了解这篇文章在讲什么。\n"
        "语气：简洁客观的新闻语气。\n"
        '输出为 JSON：{"summary_cn": "..."}'
    )
    data = _call_llm(client, system, full_text, model)
    return data.get("summary_cn", "")


def process_article(
    raw: RawArticle,
    api_key: str,
    base_url: str = "https://api.deepseek.com",
    model: str = "deepseek-chat",
) -> ProcessedArticle:
    """Run all three LLM calls and return a ProcessedArticle."""
    client = OpenAI(api_key=api_key, base_url=base_url)
    full_text = "\n\n".join(raw.paragraphs)

    logger.info("LLM call 1/3: translating %d paragraphs…", len(raw.paragraphs))
    paragraphs_cn = _translate(client, model, raw.paragraphs)

    logger.info("LLM call 2/3: extracting keywords…")
    keywords = _extract_keywords(client, model, full_text)

    logger.info("LLM call 3/3: generating Chinese summary…")
    summary_cn = _summarise(client, model, full_text)

    return ProcessedArticle(
        title=raw.title,
        url=raw.url,
        pub_date=raw.pub_date,
        summary_cn=summary_cn,
        paragraphs_en=raw.paragraphs,
        paragraphs_cn=paragraphs_cn,
        keywords=keywords,
        lead_image_url=raw.lead_image_url,
    )
