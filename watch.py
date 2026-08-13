#!/usr/bin/env python3
"""神姫バス 路線バス新着情報ウォッチャー.

公式サイトの新着情報ページを取得し、前回実行時との差分を検出して
Discord / メールに通知し、あわせて RSS フィード (feed.xml) を生成する。

依存ライブラリなし（標準ライブラリのみ）。
"""

from __future__ import annotations

import json
import os
import re
import smtplib
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

JST = timezone(timedelta(hours=9))
BASE = "https://www.shinkibus.co.jp"

# 監視対象。lists01 = 路線バス。旅行/PR系は lists03, lists04 に分かれるので
# ここに入れなければノイズはほぼ入らない。
TARGET_URLS = [
    f"{BASE}/sys/frames/lists01",
]

# タイトルにこのいずれかを含む記事だけ通知する（NOTIFY_ALL=1 で無効化）。
DEFAULT_KEYWORDS = [
    "ダイヤ",
    "運休",
    "運行",
    "臨時",
    "迂回",
    "経路変更",
    "減便",
    "増便",
    "時刻",
    "休止",
    "廃止",
    "路線",
]

STATE_PATH = Path(os.environ.get("STATE_PATH", "state.json"))
FEED_PATH = Path(os.environ.get("FEED_PATH", "feed.xml"))
FEED_SELF_URL = os.environ.get("FEED_SELF_URL", "")
FEED_MAX_ITEMS = 50
UA = "shinkibus-watch/1.0 (personal commute notifier)"

# 記事リンク。日付・カテゴリはリンクの前後どちらにあっても拾えるよう、
# マッチ位置の周辺テキストから別途探す（CSSセレクタに依存しない）。
LINK_RE = re.compile(
    r'href="(?P<href>[^"]*?/sys/frames/view/(?P<id>\d+))"'
    r"[^>]*>"
    r"(?P<title>.*?)</a>",
    re.S,
)
DATE_RE = re.compile(r"\d{4}\.\d{1,2}\.\d{1,2}")
LOOKBEHIND = 600
LOOKAHEAD = 300
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
CATEGORY_RE = re.compile(r"(路線バス|高速バス|旅行|その他)")
PDF_RE = re.compile(r'href="(?P<href>[^"]*?/sys/whatsnew/download/\d+)"')


@dataclass
class Entry:
    id: str
    date: str
    category: str
    title: str
    url: str
    pdf_url: str = ""
    first_seen: str = ""


def log(msg: str) -> None:
    print(f"[{datetime.now(JST):%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def fetch(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        raw = res.read()
    # サイトは UTF-8 だが、念のため寛容にデコードする
    return raw.decode("utf-8", errors="replace")


def clean(text: str) -> str:
    return WS_RE.sub(" ", TAG_RE.sub(" ", text)).strip()


def _context(html: str, start: int, end: int) -> tuple[str, str]:
    """リンク前後のテキストから日付とカテゴリを推定する。"""
    before = clean(html[max(0, start - LOOKBEHIND) : start])
    after = clean(html[end : end + LOOKAHEAD])

    dates = DATE_RE.findall(before)
    date = dates[-1] if dates else ""
    if not date:
        m = DATE_RE.search(after)
        date = m.group(0) if m else ""

    cat = ""
    # 日付の直後にカテゴリ名が来る作りなので、日付以降の断片を優先して探す
    tail = before.rsplit(date, 1)[-1] if date and date in before else before
    for source in (tail, before, after):
        m = CATEGORY_RE.search(source)
        if m:
            cat = m.group(1)
            break
    return date, cat


def parse_entries(html: str) -> list[Entry]:
    entries: dict[str, Entry] = {}
    for m in LINK_RE.finditer(html):
        entry_id = m.group("id")
        if entry_id in entries:
            continue
        title = clean(m.group("title"))
        if not title:
            continue

        # リンクの内側に日付・カテゴリが入っている作りの場合は先頭から切り出す
        inline_date, inline_cat = "", ""
        m_date = DATE_RE.match(title)
        if m_date:
            inline_date = m_date.group(0)
            title = title[m_date.end() :].strip()
        m_cat = CATEGORY_RE.match(title)
        if m_cat:
            inline_cat = m_cat.group(1)
            title = title[m_cat.end() :].strip()
        if not title:
            continue

        date, category = _context(html, m.start(), m.end())
        date = inline_date or date
        category = inline_cat or category
        href = m.group("href")
        entries[entry_id] = Entry(
            id=entry_id,
            date=date,
            category=category,
            title=title,
            url=href if href.startswith("http") else BASE + href,
        )
    return list(entries.values())


def find_pdf_url(entry: Entry) -> str:
    """記事本文ページから PDF の直リンクを拾う（取れなくても致命的ではない）。"""
    try:
        html = fetch(entry.url, timeout=20)
    except Exception as exc:  # noqa: BLE001
        log(f"  PDF リンク取得失敗 ({entry.id}): {exc}")
        return ""
    m = PDF_RE.search(html)
    if not m:
        return ""
    href = m.group("href")
    return href if href.startswith("http") else BASE + href


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {"entries": {}}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        log("state.json が壊れているため初期化します")
        return {"entries": {}}


def save_state(state: dict) -> None:
    state["updated_at"] = datetime.now(JST).isoformat()
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def matches_keywords(entry: Entry, keywords: list[str]) -> bool:
    return any(kw in entry.title for kw in keywords)


# --------------------------------------------------------------------------
# 通知
# --------------------------------------------------------------------------


def format_lines(entries: list[Entry]) -> list[str]:
    lines = []
    for e in entries:
        head = f"■ {e.date} [{e.category or '-'}] {e.title}"
        lines.append(head)
        lines.append(f"  {e.url}")
        if e.pdf_url:
            lines.append(f"  PDF: {e.pdf_url}")
        lines.append("")
    return lines


def notify_discord(subject: str, entries: list[Entry]) -> None:
    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook:
        return
    body = "\n".join([f"**{subject}**", ""] + format_lines(entries))
    # Discord の上限は 2000 文字
    if len(body) > 1900:
        body = body[:1900] + "\n…(省略)"
    payload = json.dumps({"content": body}).encode("utf-8")
    req = urllib.request.Request(
        webhook,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": UA},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as res:
            log(f"Discord 通知 OK (HTTP {res.status})")
    except urllib.error.HTTPError as exc:
        log(f"Discord 通知失敗: HTTP {exc.code} {exc.read()[:200]!r}")
    except Exception as exc:  # noqa: BLE001
        log(f"Discord 通知失敗: {exc}")


def notify_email(subject: str, entries: list[Entry]) -> None:
    host = os.environ.get("SMTP_HOST", "").strip()
    mail_to = os.environ.get("MAIL_TO", "").strip()
    if not host or not mail_to:
        return
    port = int(os.environ.get("SMTP_PORT", "465"))
    user = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASS", "")
    mail_from = os.environ.get("MAIL_FROM", user or mail_to)

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = mail_from
    msg["To"] = mail_to
    msg.set_content("\n".join(format_lines(entries)))

    try:
        if port == 587:
            with smtplib.SMTP(host, port, timeout=30) as smtp:
                smtp.starttls()
                if user:
                    smtp.login(user, password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP_SSL(host, port, timeout=30) as smtp:
                if user:
                    smtp.login(user, password)
                smtp.send_message(msg)
        log(f"メール通知 OK -> {mail_to}")
    except Exception as exc:  # noqa: BLE001
        log(f"メール通知失敗: {exc}")


# --------------------------------------------------------------------------
# RSS
# --------------------------------------------------------------------------


def to_rfc822(date_str: str, fallback: str = "") -> str:
    try:
        y, m, d = (int(x) for x in date_str.split("."))
        dt = datetime(y, m, d, 9, 0, 0, tzinfo=JST)
    except (ValueError, AttributeError):
        try:
            dt = datetime.fromisoformat(fallback)
        except (ValueError, TypeError):
            dt = datetime.now(JST)
    return format_datetime(dt)


def build_feed(entries: list[Entry]) -> str:
    ordered = sorted(entries, key=lambda e: (e.date, e.id), reverse=True)[:FEED_MAX_ITEMS]
    items = []
    for e in ordered:
        desc_parts = [f"{e.date} / {e.category or '-'}"]
        if e.pdf_url:
            desc_parts.append(f'PDF: <a href="{escape(e.pdf_url)}">{escape(e.pdf_url)}</a>')
        items.append(
            "    <item>\n"
            f"      <title>{escape(e.title)}</title>\n"
            f"      <link>{escape(e.url)}</link>\n"
            f'      <guid isPermaLink="false">shinkibus-{e.id}</guid>\n'
            f"      <pubDate>{to_rfc822(e.date, e.first_seen)}</pubDate>\n"
            f"      <category>{escape(e.category or 'その他')}</category>\n"
            f"      <description>{escape(' / '.join(desc_parts))}</description>\n"
            "    </item>"
        )
    self_link = (
        f'    <atom:link href="{escape(FEED_SELF_URL)}" rel="self" type="application/rss+xml"/>\n'
        if FEED_SELF_URL
        else ""
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">\n'
        "  <channel>\n"
        "    <title>神姫バス 路線バス新着情報（非公式）</title>\n"
        f"    <link>{TARGET_URLS[0]}</link>\n"
        "    <description>神姫バス公式サイトの新着情報を非公式にRSS化したものです。</description>\n"
        "    <language>ja</language>\n"
        f"    <lastBuildDate>{format_datetime(datetime.now(JST))}</lastBuildDate>\n"
        f"{self_link}"
        + "\n".join(items)
        + "\n  </channel>\n</rss>\n"
    )


# --------------------------------------------------------------------------


def main() -> int:
    keywords = [
        k.strip()
        for k in os.environ.get("KEYWORDS", ",".join(DEFAULT_KEYWORDS)).split(",")
        if k.strip()
    ]
    notify_all = os.environ.get("NOTIFY_ALL", "").strip() in {"1", "true", "yes"}

    scraped: dict[str, Entry] = {}
    for url in TARGET_URLS:
        log(f"取得中: {url}")
        html = fetch(url)
        found = parse_entries(html)
        log(f"  {len(found)} 件を検出")
        if not found:
            # 静かに「新着なし」と誤認するのが一番危険なので、ここで落として
            # GitHub Actions の失敗通知に気づけるようにする。
            log("!! 記事を1件も抽出できませんでした。HTML構造が変わった可能性があります。")
            return 2
        for e in found:
            scraped.setdefault(e.id, e)

    state = load_state()
    known: dict[str, dict] = state.get("entries", {})
    first_run = not known
    now_iso = datetime.now(JST).isoformat()

    changed: list[Entry] = []
    for entry_id, entry in scraped.items():
        prev = known.get(entry_id)
        if prev is None:
            entry.first_seen = now_iso
            changed.append(entry)
        else:
            entry.first_seen = prev.get("first_seen", now_iso)
            entry.pdf_url = prev.get("pdf_url", "")
            if prev.get("title") != entry.title:
                # 【8月5日内容更新】のようなタイトル書き換えも拾う
                changed.append(entry)

    # 既知だがページから消えた記事も、RSS の履歴として残す
    merged: dict[str, Entry] = {}
    for entry_id, data in known.items():
        merged[entry_id] = Entry(**{k: data.get(k, "") for k in Entry.__annotations__})
    merged.update(scraped)

    targets = changed if notify_all else [e for e in changed if matches_keywords(e, keywords)]

    if first_run:
        log(f"初回実行のため通知はスキップし、{len(scraped)} 件を state に記録します")
        targets = []
    else:
        log(f"新規・更新 {len(changed)} 件 / 通知対象 {len(targets)} 件")

    for e in targets:
        if not e.pdf_url:
            e.pdf_url = find_pdf_url(e)
        merged[e.id] = e

    if targets:
        subject = f"【神姫バス】新着 {len(targets)} 件: {targets[0].title[:40]}"
        notify_discord(subject, targets)
        notify_email(subject, targets)
        for line in format_lines(targets):
            print(line)

    FEED_PATH.write_text(build_feed(list(merged.values())), encoding="utf-8")
    save_state({"entries": {k: asdict(v) for k, v in merged.items()}})
    log(f"完了: {FEED_PATH} / {STATE_PATH} を更新しました")
    return 0


if __name__ == "__main__":
    sys.exit(main())
