"""The Eight Words Daily — daily pipeline entry point."""

import argparse
import asyncio
import json
import logging
import os
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import yaml
from dotenv import load_dotenv

from src.fetcher import fetch_top_article
from src.processor import ProcessedArticle, Keyword, process_article, attach_word_glossary
from src.audio import generate_audio
from src.assembler import build_email_html, build_article_page
from src.mailer import send_email
from src.deployer import (
    save_article_page,
    save_audio,
    save_data_json,
    deploy_to_github_pages,
)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("dailybbc")

DATA_DIR = Path(__file__).resolve().parent / "data"


def load_config() -> dict:
    config_path = os.path.join(os.path.dirname(__file__), "config.yaml")
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def normalize_pages_base_url(base_url: str) -> str:
    """Auto-convert github.com repo URLs to github.io Pages URLs."""
    clean = (base_url or "").strip().rstrip("/")
    if clean.startswith("https://github.com/"):
        parts = clean.replace("https://github.com/", "", 1).split("/")
        if len(parts) >= 2 and parts[0] and parts[1]:
            return f"https://{parts[0]}.github.io/{parts[1]}"
    return clean


def _load_processed_from_json(date_str: str) -> ProcessedArticle:
    """Load a previously saved ProcessedArticle from data/{date}.json."""
    path = DATA_DIR / f"{date_str}.json"
    if not path.exists():
        raise FileNotFoundError(f"No cached data for {date_str} at {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    keywords = [Keyword(**kw) for kw in data.get("keywords", [])]
    return ProcessedArticle(
        title=data["title"],
        url=data["url"],
        pub_date=data["pub_date"],
        summary_cn=data["summary_cn"],
        paragraphs_en=data["paragraphs_en"],
        paragraphs_cn=data["paragraphs_cn"],
        keywords=keywords,
        lead_image_url=data.get("lead_image_url"),
        word_glossary=data.get("word_glossary") or {},
    )


async def daily_pipeline(rebuild_only: bool = False):
    date_str = datetime.now().strftime("%Y-%m-%d")
    config = load_config()

    if rebuild_only:
        logger.info("=== Rebuild-only mode for %s ===", date_str)
        processed = _load_processed_from_json(date_str)
        logger.info("Loaded cached data: %s (%d paragraphs)", processed.title, len(processed.paragraphs_en))
        if not processed.word_glossary:
            ds = os.environ.get("DEEPSEEK_API_KEY", "")
            if ds:
                logger.info("No word_glossary in cache; running one LLM pass for hover translations…")
                processed = attach_word_glossary(
                    processed,
                    api_key=ds,
                    base_url=config["llm"]["base_url"],
                    model=config["llm"]["model"],
                )
                save_data_json(json.dumps(asdict(processed), ensure_ascii=False, indent=2), date_str)
            else:
                logger.warning(
                    "No word_glossary and DEEPSEEK_API_KEY unset — hover uses live zh fallback only (no article-aware glossary)."
                )
    else:
        logger.info("=== DailyBBC pipeline starting for %s ===", date_str)

        deepseek_key = os.environ.get("DEEPSEEK_API_KEY", "")
        if not deepseek_key:
            logger.error("DEEPSEEK_API_KEY not set. Aborting.")
            sys.exit(1)

        logger.info("Step 1/6: Fetching top headline…")
        raw_article = fetch_top_article(config["rss"]["url"])
        logger.info("Fetched: %s (%d paragraphs)", raw_article.title, len(raw_article.paragraphs))

        logger.info("Step 2/6: Processing with LLM…")
        processed = process_article(
            raw_article,
            api_key=deepseek_key,
            base_url=config["llm"]["base_url"],
            model=config["llm"]["model"],
        )
        save_data_json(json.dumps(asdict(processed), ensure_ascii=False, indent=2), date_str)

    # Step 3: Generate TTS audio + timeline
    logger.info("Step 3/6: Generating audio…")
    audio_bytes, timeline = None, None
    try:
        audio_bytes, timeline = await generate_audio(
            processed.paragraphs_en,
            voice=config["tts"]["voice"],
            rate=config["tts"]["rate"],
        )
        save_audio(audio_bytes, date_str)
    except Exception as e:
        logger.error("Audio generation failed: %s — continuing without audio", e)

    # Step 4: Build the article web page
    logger.info("Step 4/6: Assembling article page…")
    base_url = normalize_pages_base_url(config["pages"]["base_url"])
    audio_url = f"{base_url}/audio/{date_str}.mp3" if audio_bytes else None
    article_page_url = f"{base_url}/articles/{date_str}.html"

    page_html = build_article_page(processed, timeline, date_str, audio_url)
    save_article_page(page_html, date_str)

    # Step 5: Deploy to GitHub Pages
    logger.info("Step 5/6: Deploying to GitHub Pages…")
    try:
        deploy_to_github_pages(date_str)
    except Exception as e:
        logger.error("Deploy failed: %s — email will still be sent", e)

    # Step 6: Build and send email (skipped in rebuild-only mode)
    if rebuild_only:
        logger.info("Step 6/6: Skipping email (rebuild-only mode).")
    else:
        logger.info("Step 6/6: Sending email…")
        gmail_address = os.environ.get("GMAIL_ADDRESS", "")
        gmail_password = os.environ.get("GMAIL_APP_PASSWORD", "")
        if gmail_address and gmail_password:
            email_html = build_email_html(processed, date_str, article_page_url)
            recipients = config["email"]["recipients"]
            title_short = processed.title[:30] + ("…" if len(processed.title) > 30 else "")
            subject = config["email"]["subject_template"].format(
                title_short=title_short, date=date_str,
            )
            try:
                send_email(gmail_address, gmail_password, recipients, subject, email_html)
            except Exception as e:
                logger.error("Email send failed: %s", e)
        else:
            logger.warning("Gmail credentials not set — skipping email send.")

    logger.info("=== Pipeline complete for %s ===", date_str)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="The Eight Words Daily pipeline")
    parser.add_argument(
        "--rebuild-only",
        action="store_true",
        help="Skip RSS fetch, full LLM article pass, and email; regenerate audio + HTML. "
        "If cache lacks word_glossary, runs one LLM glossary pass when DEEPSEEK_API_KEY is set.",
    )
    args = parser.parse_args()
    asyncio.run(daily_pipeline(rebuild_only=args.rebuild_only))
