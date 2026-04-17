"""Module B: LLM processing — translate, extract keywords, summarise."""

import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path

from openai import OpenAI

from src.fetcher import RawArticle

logger = logging.getLogger(__name__)

MAX_RETRIES = 3

_GLOSSARY_CHUNK = 100
_MAX_GLOSSARY_WORDS = 800
_GLOBAL_GLOSSARY_PATH = Path(__file__).resolve().parent.parent / "data" / "word_glossary_master.json"

# Only exclude fragments that aren't real words (contractions, single chars).
_GLOSS_SKIP = re.compile(r"^(?:'s|'t|'d|'ll|'re|'ve|'m|[a-z])$")


def _collect_glossary_candidates(paragraphs: list[str], keyword_words: set[str]) -> list[str]:
    """All unique words in the article (excluding keywords), ordered by frequency."""
    text = " ".join(paragraphs)
    tokens = re.findall(r"\b[a-zA-Z][a-zA-Z'-]*\b", text)
    counts: Counter[str] = Counter()
    for t in tokens:
        w = t.lower()
        if _GLOSS_SKIP.match(w) or w in keyword_words:
            continue
        counts[w] += 1
    return [w for w, _ in counts.most_common(_MAX_GLOSSARY_WORDS)]


def _load_global_glossary() -> dict[str, str]:
    """Load persistent cross-article glossary cache."""
    if not _GLOBAL_GLOSSARY_PATH.exists():
        return {}
    try:
        raw = json.loads(_GLOBAL_GLOSSARY_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("Failed to load global glossary cache: %s", e)
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        str(k).lower(): str(v).strip()
        for k, v in raw.items()
        if isinstance(v, str) and str(v).strip()
    }


def _save_global_glossary(glossary: dict[str, str]) -> None:
    """Persist cross-article glossary cache."""
    _GLOBAL_GLOSSARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    _GLOBAL_GLOSSARY_PATH.write_text(
        json.dumps(glossary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


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


def _build_article_glossary(
    client: OpenAI,
    model: str,
    paragraphs: list[str],
    keyword_words: set[str],
) -> dict[str, str]:
    """Build per-article glossary using persistent cache; only translate missing words."""
    candidates = _collect_glossary_candidates(paragraphs, keyword_words)
    if not candidates:
        return {}

    global_glossary = _load_global_glossary()
    article_glossary = {w: global_glossary[w] for w in candidates if w in global_glossary}
    missing = [w for w in candidates if w not in article_glossary]

    if missing:
        logger.info(
            "LLM call 4/4: hover word glossary %d total, %d cache-hit, %d to translate…",
            len(candidates), len(article_glossary), len(missing),
        )
        translated = _translate_word_glossary(client, model, missing)
        article_glossary.update(translated)
        if translated:
            global_glossary.update(translated)
            _save_global_glossary(global_glossary)
    else:
        logger.info("LLM call 4/4: hover word glossary all cache-hit (%d tokens).", len(candidates))

    return article_glossary


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
    word_glossary = _build_article_glossary(client, model, raw.paragraphs, kw_lower)

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
    kw_lower = {k.word.lower() for k in article.keywords}
    client = OpenAI(api_key=api_key, base_url=base_url)
    glossary = _build_article_glossary(client, model, article.paragraphs_en, kw_lower)
    if not glossary:
        return article
    return replace(article, word_glossary=glossary)
