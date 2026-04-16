"""Module B: LLM processing — translate, extract keywords, summarise."""

import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field, replace

from openai import OpenAI

from src.fetcher import RawArticle

logger = logging.getLogger(__name__)

MAX_RETRIES = 3

# Words excluded from the hover glossary (function words); content words like "comes" stay in.
_EN_STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "if", "then", "else", "when",
    "at", "from", "by", "for", "with", "about", "into", "through", "during",
    "before", "after", "above", "below", "between", "under", "again", "further",
    "once", "here", "there", "all", "each", "few", "more", "most", "other",
    "some", "such", "no", "nor", "not", "only", "own", "same", "so", "than",
    "too", "very", "just", "now", "i", "me", "my", "we", "our", "us", "you",
    "your", "he", "him", "his", "she", "her", "it", "its", "they", "them", "their",
    "what", "which", "who", "whom", "this", "that", "these", "those",
    "am", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "having", "do", "does", "did", "doing",
    "can", "could", "may", "might", "must", "shall", "should", "will", "would",
    "of", "to", "in", "on", "off", "out", "up", "down", "over", "also",
    "as", "both", "either", "neither", "any", "every", "such", "how", "why",
})

_GLOSSARY_CHUNK = 100
_MAX_GLOSSARY_WORDS = 400


def _collect_glossary_candidates(paragraphs: list[str], keyword_words: set[str]) -> list[str]:
    """Most frequent content words (excluding keywords + stopwords), capped for LLM cost."""
    text = " ".join(paragraphs)
    tokens = re.findall(r"\b[a-z][a-z'-]*\b", text, flags=re.IGNORECASE)
    counts: Counter[str] = Counter()
    for t in tokens:
        w = t.lower()
        if len(w) < 2 or w in _EN_STOPWORDS or w in keyword_words:
            continue
        counts[w] += 1
    ordered = [w for w, _ in counts.most_common(_MAX_GLOSSARY_WORDS)]
    return ordered


def _translate_word_glossary(client: OpenAI, model: str, words: list[str]) -> dict[str, str]:
    """One or more LLM calls: English token → concise Chinese gloss for this article."""
    result: dict[str, str] = {}
    for i in range(0, len(words), _GLOSSARY_CHUNK):
        chunk = words[i : i + _GLOSSARY_CHUNK]
        system = (
            "你是专业英汉词典编辑。用户给你一个 JSON 数组，其中的英文单词（小写）都来自同一篇英语新闻。\n"
            "请为每个单词给出该词在新闻语体里最贴切的一个中文释义：2-12 个汉字，不要词性标签，不要例句，不要英文。\n"
            '输出严格的 JSON：{"glossary": {"单词":"中文", ...}}。glossary 的键必须与输入中的每个字符串完全一致。\n'
            "不要遗漏任何输入单词。"
        )
        user = json.dumps(chunk, ensure_ascii=False)
        data = _call_llm(client, system, user, model)
        raw: dict = {}
        if isinstance(data, dict):
            g = data.get("glossary")
            raw = g if isinstance(g, dict) else data
        if not isinstance(raw, dict):
            raw = {}
        normalized = {
            str(k).lower(): str(v).strip()
            for k, v in raw.items()
            if isinstance(v, str) and str(v).strip()
        }
        for w in chunk:
            if w in normalized:
                result[w] = normalized[w]
        missing = [w for w in chunk if w not in result]
        if missing:
            logger.warning("Glossary missing %d/%d entries in chunk starting at %d", len(missing), len(chunk), i)
    return result


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
    word_glossary: dict[str, str] = field(default_factory=dict)


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
        "你是一个英语教学专家。从以下英文新闻文章中提取恰好 8 个值得学习的关键词。\n"
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

    logger.info("LLM call 1/4: translating %d paragraphs…", len(raw.paragraphs))
    paragraphs_cn = _translate(client, model, raw.paragraphs)

    logger.info("LLM call 2/4: extracting keywords…")
    keywords = _extract_keywords(client, model, full_text)

    logger.info("LLM call 3/4: generating Chinese summary…")
    summary_cn = _summarise(client, model, full_text)

    kw_lower = {k.word.lower() for k in keywords}
    gloss_candidates = _collect_glossary_candidates(raw.paragraphs, kw_lower)
    word_glossary: dict[str, str] = {}
    if gloss_candidates:
        logger.info("LLM call 4/4: hover word glossary (%d tokens)…", len(gloss_candidates))
        word_glossary = _translate_word_glossary(client, model, gloss_candidates)

    return ProcessedArticle(
        title=raw.title,
        url=raw.url,
        pub_date=raw.pub_date,
        summary_cn=summary_cn,
        paragraphs_en=raw.paragraphs,
        paragraphs_cn=paragraphs_cn,
        keywords=keywords,
        lead_image_url=raw.lead_image_url,
        word_glossary=word_glossary,
    )


def attach_word_glossary(
    article: ProcessedArticle,
    api_key: str,
    base_url: str = "https://api.deepseek.com",
    model: str = "deepseek-chat",
) -> ProcessedArticle:
    """Fill `word_glossary` when missing (e.g. old cached JSON). One or more LLM calls."""
    if article.word_glossary:
        return article
    kw_lower = {k.word.lower() for k in article.keywords}
    candidates = _collect_glossary_candidates(article.paragraphs_en, kw_lower)
    if not candidates:
        return article
    client = OpenAI(api_key=api_key, base_url=base_url)
    logger.info("Building hover word glossary (%d tokens)…", len(candidates))
    glossary = _translate_word_glossary(client, model, candidates)
    return replace(article, word_glossary=glossary)
