"""Module F: Deploy generated files to GitHub Pages via git push."""

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent


def save_article_page(html: str, date_str: str) -> Path:
    """Write the article HTML to articles/{date}.html."""
    path = REPO_ROOT / "articles" / f"{date_str}.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    logger.info("Saved article page: %s", path)
    return path


def save_audio(audio_bytes: bytes, date_str: str) -> Path:
    """Write the audio MP3 to audio/{date}.mp3."""
    path = REPO_ROOT / "audio" / f"{date_str}.mp3"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(audio_bytes)
    logger.info("Saved audio: %s (%d bytes)", path, len(audio_bytes))
    return path


def save_data_json(data: str, date_str: str) -> Path:
    """Write intermediate JSON to data/{date}.json for debugging."""
    path = REPO_ROOT / "data" / f"{date_str}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data, encoding="utf-8")
    logger.info("Saved debug data: %s", path)
    return path


def deploy_to_github_pages(date_str: str) -> None:
    """Stage generated files, commit, and push to trigger GitHub Pages."""
    cmds = [
        ["git", "add", "articles/", "audio/", "data/"],
        ["git", "commit", "-m", f"📰 Daily update: {date_str}"],
        ["git", "push"],
    ]

    for cmd in cmds:
        logger.info("Running: %s", " ".join(cmd))
        result = subprocess.run(
            cmd, cwd=str(REPO_ROOT), capture_output=True, text=True
        )
        if result.returncode != 0:
            if "nothing to commit" in result.stdout + result.stderr:
                logger.info("Nothing new to commit, skipping push.")
                return
            logger.error("Command failed: %s\nstderr: %s", cmd, result.stderr)
            raise RuntimeError(f"Deploy command failed: {' '.join(cmd)}")

    logger.info("Deployed to GitHub Pages successfully.")
