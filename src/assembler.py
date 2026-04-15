"""Module D: Assemble email HTML and article web page from processed data."""

import re
import json
import logging
from dataclasses import asdict
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from src.processor import ProcessedArticle

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"


def _get_jinja_env() -> Environment:
    return Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=False)


def build_email_html(article: ProcessedArticle, date_str: str, article_page_url: str) -> str:
    """Render the email template with article data."""
    env = _get_jinja_env()
    template = env.get_template("email.html")
    return template.render(
        date=date_str,
        title=article.title,
        summary_cn=article.summary_cn,
        keywords=[asdict(kw) for kw in article.keywords],
        article_url=article_page_url,
    )


def _highlight_keywords(text: str, keywords: list[dict]) -> str:
    """Wrap keyword occurrences in the English text with <span> tags."""
    for kw in keywords:
        word = kw["word"]
        pattern = re.compile(rf"\b({re.escape(word)})\b", re.IGNORECASE)
        replacement = (
            f'<span class="keyword" '
            f'data-word="{kw["word"]}" '
            f'data-phonetic="{kw["phonetic"]}" '
            f'data-pos="{kw["pos"]}" '
            f'data-def="{kw["definition_cn"]}" '
            f'data-def-en="{kw["definition_en"]}">'
            f"\\1</span>"
        )
        text = pattern.sub(replacement, text, count=1)
    return text


def _split_sentences(paragraphs: list[str]) -> list[dict]:
    """Split paragraphs into sentences and track paragraph indices."""
    sentences = []
    for para_idx, para in enumerate(paragraphs):
        parts = re.split(r'(?<=[.!?])\s+', para)
        for part in parts:
            part = part.strip()
            if part:
                sentences.append({"text": part, "paragraph_index": para_idx})
    return sentences


def build_article_page(
    article: ProcessedArticle,
    timeline: dict | None,
    date_str: str,
    audio_url: str | None = None,
) -> str:
    """Render the full article web page."""
    env = _get_jinja_env()
    template = env.get_template("article_page.html")

    kw_dicts = [asdict(kw) for kw in article.keywords]

    sentences = _split_sentences(article.paragraphs_en)
    sentence_index = 0
    paragraphs_en_html = []
    for para_idx, para in enumerate(article.paragraphs_en):
        parts = re.split(r'(?<=[.!?])\s+', para)
        html_parts = []
        for part in parts:
            part = part.strip()
            if part:
                highlighted = _highlight_keywords(part, kw_dicts)
                html_parts.append(
                    f'<span class="sentence" data-sentence-index="{sentence_index}">'
                    f"{highlighted}</span>"
                )
                sentence_index += 1
        paragraphs_en_html.append(" ".join(html_parts))

    return template.render(
        date=date_str,
        title=article.title,
        url=article.url,
        paragraphs=list(zip(paragraphs_en_html, article.paragraphs_cn)),
        keywords=kw_dicts,
        timeline_json=json.dumps(timeline or {"sentences": [], "total_duration_ms": 0}),
        audio_url=audio_url or "",
        has_audio=audio_url is not None,
        lead_image_url=article.lead_image_url or "",
    )
