"""Module A: Fetch the top BBC headline article from RSS."""

import time
import logging
from urllib.parse import urljoin
from dataclasses import dataclass, field

import feedparser
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BBC_TOP_NEWS_RSS = "http://feeds.bbci.co.uk/news/rss.xml"
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 10

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


@dataclass
class RawArticle:
    title: str
    url: str
    description: str
    pub_date: str
    paragraphs: list[str] = field(default_factory=list)
    lead_image_url: str | None = None
    is_summary_only: bool = False


def _fetch_with_retry(url: str, retries: int = MAX_RETRIES) -> requests.Response:
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            logger.warning("Attempt %d/%d failed for %s: %s", attempt, retries, url, e)
            if attempt < retries:
                time.sleep(RETRY_DELAY_SECONDS)
    raise RuntimeError(f"Failed to fetch {url} after {retries} attempts")


def _extract_lead_image(soup: BeautifulSoup, page_url: str) -> str | None:
    """Best-effort extraction of the article's lead image URL."""
    og = soup.find("meta", property="og:image")
    if og and og.get("content"):
        return urljoin(page_url, og["content"])

    article = soup.find("article")
    container = article or soup
    fig_img = container.find("figure")
    if fig_img:
        img = fig_img.find("img")
        if img and img.get("src"):
            return urljoin(page_url, img["src"])

    img = container.find("img", src=True)
    if img:
        return urljoin(page_url, img["src"])

    return None


def _extract_paragraphs(html: str) -> list[str]:
    """Extract article paragraphs from a BBC article page."""
    soup = BeautifulSoup(html, "html.parser")

    article = soup.find("article")
    if not article:
        article = soup

    paragraphs = []
    for p in article.find_all("p"):
        # Preserve spaces across inline tags to avoid merged tokens like "comesafter".
        text = p.get_text(" ", strip=True)
        if len(text) > 20:
            paragraphs.append(text)

    return paragraphs


def fetch_top_article(rss_url: str = BBC_TOP_NEWS_RSS) -> RawArticle:
    """Fetch the #1 article from a BBC RSS feed.

    For "most important headline" behavior, pass BBC's main news feed:
    http://feeds.bbci.co.uk/news/rss.xml
    """
    logger.info("Fetching RSS feed: %s", rss_url)
    feed = feedparser.parse(rss_url)

    if not feed.entries:
        raise RuntimeError("No entries found in RSS feed")

    entry = feed.entries[0]
    title = entry.get("title", "")
    link = entry.get("link", "")
    description = entry.get("description", "")
    pub_date = entry.get("published", "")

    logger.info("Top article: %s", title)

    lead_image_url = None
    try:
        resp = _fetch_with_retry(link)
        paragraphs = _extract_paragraphs(resp.text)
        soup = BeautifulSoup(resp.text, "html.parser")
        lead_image_url = _extract_lead_image(soup, link)
    except Exception as e:
        logger.error("Failed to extract article body: %s", e)
        paragraphs = []

    if not paragraphs:
        logger.warning("Falling back to RSS description as body")
        paragraphs = [description] if description else ["(No content available)"]
        return RawArticle(
            title=title,
            url=link,
            description=description,
            pub_date=pub_date,
            paragraphs=paragraphs,
            lead_image_url=lead_image_url,
            is_summary_only=True,
        )

    return RawArticle(
        title=title,
        url=link,
        description=description,
        pub_date=pub_date,
        paragraphs=paragraphs,
        lead_image_url=lead_image_url,
    )
