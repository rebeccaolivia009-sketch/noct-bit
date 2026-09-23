"""
Generates a lightweight, dark-themed HTML transcript of a ticket channel's
message history. No external dependency -- built by hand to keep the
requirements list short and the output on-brand.
"""

from __future__ import annotations

import html
from io import BytesIO

import discord

from bot.core.theme import BRAND_NAME

_TEMPLATE_HEAD = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  body {{
    background: #0f0b1a;
    color: #e7e3f4;
    font-family: -apple-system, Segoe UI, Helvetica, Arial, sans-serif;
    padding: 24px;
    max-width: 860px;
    margin: 0 auto;
  }}
  h1 {{
    color: #b79cff;
    font-size: 20px;
    border-bottom: 1px solid #3a2e63;
    padding-bottom: 12px;
  }}
  .meta {{ color: #9a8fc2; font-size: 13px; margin-bottom: 24px; }}
  .msg {{
    border-left: 2px solid #4b1fa8;
    padding: 8px 14px;
    margin-bottom: 10px;
    background: #16102b;
    border-radius: 4px;
  }}
  .author {{ color: #c9b6ff; font-weight: 600; font-size: 13px; }}
  .timestamp {{ color: #6f6592; font-size: 11px; margin-left: 8px; }}
  .content {{ color: #e7e3f4; font-size: 14px; margin-top: 4px; white-space: pre-wrap; }}
  .footer {{ color: #534a7a; font-size: 11px; margin-top: 30px; text-align: center; }}
</style>
</head>
<body>
<h1>{title}</h1>
<div class="meta">{meta}</div>
"""

_TEMPLATE_TAIL = f"""
<div class="footer">{BRAND_NAME} STORE -- TICKET TRANSCRIPT</div>
</body>
</html>
"""


async def build_html_transcript(channel: discord.TextChannel) -> discord.File:
    messages = [msg async for msg in channel.history(limit=500, oldest_first=True)]

    parts = [
        _TEMPLATE_HEAD.format(
            title=html.escape(f"Transcript - #{channel.name}"),
            meta=html.escape(f"Channel ID: {channel.id} | Messages: {len(messages)}"),
        )
    ]

    for msg in messages:
        author = html.escape(str(msg.author))
        timestamp = msg.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        content = html.escape(msg.content) if msg.content else "<em>(no text content)</em>"
        if msg.attachments:
            content += "<br>" + "<br>".join(
                html.escape(a.url) for a in msg.attachments
            )
        parts.append(
            f'<div class="msg"><span class="author">{author}</span>'
            f'<span class="timestamp">{timestamp}</span>'
            f'<div class="content">{content}</div></div>'
        )

    parts.append(_TEMPLATE_TAIL)
    buffer = BytesIO("".join(parts).encode("utf-8"))
    buffer.seek(0)
    return discord.File(buffer, filename=f"transcript-{channel.id}.html")
