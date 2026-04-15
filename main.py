"""DailyBBC — daily pipeline entry point."""

import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import asdict
from datetime import datetime

import yaml
from dotenv import load_dotenv

from src.fetcher import fetch_top_article
from src.processor import process_article
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
DEBUG_LOG_PATH = "/Users/jp/NEWS_APP/.cursor/debug-c5512b.log"
DEBUG_SESSION_ID = "c5512b"


def _debug_log(run_id: str, hypothesis_id: str, location: str, message: str, data: dict) -> None:
    # #region agent log
    payload = {
        "sessionId": DEBUG_SESSION_ID,
        "runId": run_id,
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data,
        "timestamp": int(time.time() * 1000),
    }
    with open(DEBUG_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    # #endregion


def load_config() -> dict:
    config_path = os.path.join(os.path.dirname(__file__), "config.yaml")
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def normalize_pages_base_url(base_url: str) -> str:
    """Normalize page base URL to a public GitHub Pages URL.

    Accepts either:
    - https://<username>.github.io/<repo>
    - https://github.com/<username>/<repo> (auto-converted)
    """
    clean = (base_url or "").strip().rstrip("/")
    if clean.startswith("https://github.com/"):
        parts = clean.replace("https://github.com/", "", 1).split("/")
        if len(parts) >= 2 and parts[0] and parts[1]:
            return f"https://{parts[0]}.github.io/{parts[1]}"
    return clean


async def daily_pipeline():
    date_str = datetime.now().strftime("%Y-%m-%d")
    run_id = f"run-{date_str}-{int(time.time())}"
    logger.info("=== DailyBBC pipeline starting for %s ===", date_str)

    config = load_config()
    deepseek_key = os.environ.get("DEEPSEEK_API_KEY", "")
    gmail_address = os.environ.get("GMAIL_ADDRESS", "")
    gmail_password = os.environ.get("GMAIL_APP_PASSWORD", "")

    if not deepseek_key:
        logger.error("DEEPSEEK_API_KEY not set. Aborting.")
        sys.exit(1)

    # #region agent log
    _debug_log(
        run_id,
        "H1",
        "main.py:66",
        "DeepSeek key env diagnostics",
        {
            "deepseek_key_len": len(deepseek_key),
            "deepseek_key_prefix": deepseek_key[:3],
            "deepseek_key_has_whitespace": any(ch.isspace() for ch in deepseek_key),
            "gmail_address_set": bool(gmail_address),
            "config_llm_base_url": config["llm"]["base_url"],
            "config_llm_model": config["llm"]["model"],
        },
    )
    # #endregion

    # Step 1: Fetch the top BBC Technology article
    logger.info("Step 1/6: Fetching BBC Technology article…")
    raw_article = fetch_top_article(config["rss"]["url"])
    logger.info("Fetched: %s (%d paragraphs)", raw_article.title, len(raw_article.paragraphs))

    # Step 2: LLM processing (translate, keywords, summary)
    logger.info("Step 2/6: Processing with LLM…")
    processed = process_article(
        raw_article,
        api_key=deepseek_key,
        base_url=config["llm"]["base_url"],
        model=config["llm"]["model"],
        run_id=run_id,
    )
    # #region agent log
    _debug_log(
        run_id,
        "H5",
        "main.py:108",
        "LLM processing completed",
        {
            "paragraphs_en_count": len(processed.paragraphs_en),
            "paragraphs_cn_count": len(processed.paragraphs_cn),
            "keywords_count": len(processed.keywords),
            "summary_cn_len": len(processed.summary_cn or ""),
        },
    )
    # #endregion
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
        # #region agent log
        _debug_log(
            run_id,
            "H6",
            "main.py:138",
            "Audio generation completed",
            {
                "audio_bytes_len": len(audio_bytes),
                "timeline_sentences": len((timeline or {}).get("sentences", [])),
                "timeline_total_ms": (timeline or {}).get("total_duration_ms", 0),
            },
        )
        # #endregion
    except Exception as e:
        # #region agent log
        _debug_log(
            run_id,
            "H6",
            "main.py:151",
            "Audio generation failed",
            {"error_type": type(e).__name__, "error_text_snippet": str(e)[:220]},
        )
        # #endregion
        logger.error("Audio generation failed: %s — continuing without audio", e)

    # Step 4: Build the article web page
    logger.info("Step 4/6: Assembling article page…")
    base_url = normalize_pages_base_url(config["pages"]["base_url"])
    audio_url = f"{base_url}/audio/{date_str}.mp3" if audio_bytes else None
    article_page_url = f"{base_url}/articles/{date_str}.html"

    page_html = build_article_page(processed, timeline, date_str, audio_url)
    save_article_page(page_html, date_str)
    # #region agent log
    _debug_log(
        run_id,
        "H7",
        "main.py:167",
        "Article page assembled",
        {"article_page_url": article_page_url, "has_audio": bool(audio_bytes), "html_len": len(page_html)},
    )
    # #endregion

    # Step 5: Deploy to GitHub Pages
    logger.info("Step 5/6: Deploying to GitHub Pages…")
    try:
        deploy_to_github_pages(date_str)
        # #region agent log
        _debug_log(
            run_id,
            "H8",
            "main.py:179",
            "Deploy succeeded",
            {"date_str": date_str},
        )
        # #endregion
    except Exception as e:
        # #region agent log
        _debug_log(
            run_id,
            "H8",
            "main.py:188",
            "Deploy failed",
            {"error_type": type(e).__name__, "error_text_snippet": str(e)[:220]},
        )
        # #endregion
        logger.error("Deploy failed: %s — email will still be sent", e)

    # Step 6: Build and send email
    logger.info("Step 6/6: Sending email…")
    if gmail_address and gmail_password:
        email_html = build_email_html(processed, date_str, article_page_url)
        recipients = config["email"]["recipients"]
        title_short = processed.title[:30] + ("…" if len(processed.title) > 30 else "")
        subject = config["email"]["subject_template"].format(
            title_short=title_short, date=date_str,
        )
        try:
            send_email(gmail_address, gmail_password, recipients, subject, email_html)
            # #region agent log
            _debug_log(
                run_id,
                "H9",
                "main.py:210",
                "Email send succeeded",
                {"recipients_count": len(recipients), "subject_len": len(subject)},
            )
            # #endregion
        except Exception as e:
            # #region agent log
            _debug_log(
                run_id,
                "H9",
                "main.py:219",
                "Email send failed",
                {"error_type": type(e).__name__, "error_text_snippet": str(e)[:220]},
            )
            # #endregion
            logger.error("Email send failed: %s", e)
    else:
        logger.warning("Gmail credentials not set — skipping email send.")

    logger.info("=== Pipeline complete for %s ===", date_str)


if __name__ == "__main__":
    asyncio.run(daily_pipeline())
