from __future__ import annotations

import html
import pathlib
import re
from email.message import EmailMessage
from email.utils import formataddr
from typing import Any, Mapping


def safe_email_filename(raw: str, fallback: str = "attachment.bin") -> str:
    name = pathlib.PurePosixPath(str(raw or "").strip()).name
    name = re.sub(r"[\r\n\t]+", " ", name).strip()
    return name or fallback


def split_email_addresses(raw: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in re.split(r"[,;\n]+", str(raw or "")):
        value = item.strip()
        if not value or "@" not in value:
            continue
        lowered = value.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        out.append(value)
    return out


def inline_markdown_to_email_html(value: str) -> str:
    code_spans: list[str] = []

    def _stash_code(match: re.Match[str]) -> str:
        code_spans.append(f"<code>{html.escape(match.group(1), quote=False)}</code>")
        return f"\x00CODE{len(code_spans) - 1}\x00"

    text = re.sub(r"`([^`\n]+)`", _stash_code, str(value or ""))
    text = html.escape(text, quote=False)

    def _link(match: re.Match[str]) -> str:
        label = match.group(1).strip()
        href = html.unescape(match.group(2).strip())
        if not re.match(r"^(https?://|mailto:)", href, flags=re.IGNORECASE):
            return match.group(0)
        return f'<a href="{html.escape(href, quote=True)}">{label}</a>'

    text = re.sub(r"\[([^\]\n]+)\]\(([^)\s]+)\)", _link, text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![\w])__(.+?)__(?![\w])", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", text)
    text = re.sub(r"(?<![\w])_([^_\n]+)_(?![\w])", r"<em>\1</em>", text)

    for index, rendered in enumerate(code_spans):
        text = text.replace(f"\x00CODE{index}\x00", rendered)
    return text


def _is_markdown_table_separator(line: str) -> bool:
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell or "") for cell in cells)


def _markdown_table_to_email_html(lines: list[str]) -> str:
    def _cells(line: str) -> list[str]:
        stripped = line.strip()
        if stripped.startswith("|"):
            stripped = stripped[1:]
        if stripped.endswith("|"):
            stripped = stripped[:-1]
        return [cell.strip() for cell in stripped.split("|")]

    headers = _cells(lines[0])
    rows = [_cells(line) for line in lines[2:]]
    parts = ['<table class="kdcube-table">', "<thead><tr>"]
    for cell in headers:
        parts.append(f"<th>{inline_markdown_to_email_html(cell)}</th>")
    parts.append("</tr></thead>")
    if rows:
        parts.append("<tbody>")
        for row in rows:
            padded = row + [""] * max(0, len(headers) - len(row))
            parts.append("<tr>")
            for cell in padded[: max(len(headers), len(row))]:
                parts.append(f"<td>{inline_markdown_to_email_html(cell)}</td>")
            parts.append("</tr>")
        parts.append("</tbody>")
    parts.append("</table>")
    return "".join(parts)


def markdown_to_email_html(markdown_text: str) -> str:
    lines = str(markdown_text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[str] = []
    index = 0

    def _is_special_start(line: str) -> bool:
        stripped = line.strip()
        return bool(
            not stripped
            or stripped.startswith("```")
            or re.match(r"^#{1,6}\s+", stripped)
            or stripped in {"---", "***", "___"}
            or re.match(r"^\s*>\s?", line)
            or re.match(r"^\s*[-*+]\s+\S", line)
            or re.match(r"^\s*\d+[.)]\s+\S", line)
            or (
                index + 1 < len(lines)
                and "|" in stripped
                and _is_markdown_table_separator(lines[index + 1])
            )
        )

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if not stripped:
            index += 1
            continue
        if stripped.startswith("```"):
            language = stripped.strip("`").strip()
            code_lines: list[str] = []
            index += 1
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code_lines.append(lines[index])
                index += 1
            if index < len(lines):
                index += 1
            language_class = f' class="language-{html.escape(language, quote=True)}"' if language else ""
            blocks.append(
                f"<pre><code{language_class}>{html.escape(chr(10).join(code_lines), quote=False)}</code></pre>"
            )
            continue
        heading = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", stripped)
        if heading:
            level = min(len(heading.group(1)), 6)
            blocks.append(f"<h{level}>{inline_markdown_to_email_html(heading.group(2))}</h{level}>")
            index += 1
            continue
        if stripped in {"---", "***", "___"}:
            blocks.append("<hr>")
            index += 1
            continue
        if index + 1 < len(lines) and "|" in stripped and _is_markdown_table_separator(lines[index + 1]):
            table_lines = [line, lines[index + 1]]
            index += 2
            while index < len(lines) and "|" in lines[index].strip() and lines[index].strip():
                table_lines.append(lines[index])
                index += 1
            blocks.append(_markdown_table_to_email_html(table_lines))
            continue
        if re.match(r"^\s*>\s?", line):
            quote_lines: list[str] = []
            while index < len(lines) and re.match(r"^\s*>\s?", lines[index]):
                quote_lines.append(re.sub(r"^\s*>\s?", "", lines[index]).strip())
                index += 1
            quote_body = "<br>".join(inline_markdown_to_email_html(item) for item in quote_lines)
            blocks.append(f"<blockquote>{quote_body}</blockquote>")
            continue
        bullet = re.match(r"^\s*[-*+]\s+(.+)$", line)
        if bullet:
            items: list[str] = []
            while index < len(lines):
                match = re.match(r"^\s*[-*+]\s+(.+)$", lines[index])
                if not match:
                    break
                items.append(match.group(1))
                index += 1
            blocks.append("<ul>" + "".join(f"<li>{inline_markdown_to_email_html(item)}</li>" for item in items) + "</ul>")
            continue
        ordered = re.match(r"^\s*\d+[.)]\s+(.+)$", line)
        if ordered:
            items = []
            while index < len(lines):
                match = re.match(r"^\s*\d+[.)]\s+(.+)$", lines[index])
                if not match:
                    break
                items.append(match.group(1))
                index += 1
            blocks.append("<ol>" + "".join(f"<li>{inline_markdown_to_email_html(item)}</li>" for item in items) + "</ol>")
            continue

        paragraph_lines = [stripped]
        index += 1
        while index < len(lines) and lines[index].strip() and not _is_special_start(lines[index]):
            paragraph_lines.append(lines[index].strip())
            index += 1
        blocks.append(f"<p>{inline_markdown_to_email_html(' '.join(paragraph_lines))}</p>")

    body = "\n".join(blocks) or "<p>(empty report)</p>"
    return (
        "<!doctype html>"
        '<html><head><meta charset="utf-8">'
        "<style>"
        "body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;line-height:1.5;color:#1f2937;margin:0;padding:0;}"
        ".kdcube-email{max-width:760px;margin:0 auto;padding:20px;}"
        "h1,h2,h3,h4,h5,h6{color:#111827;margin:22px 0 10px;line-height:1.2;}"
        "p{margin:0 0 14px;} ul,ol{margin:0 0 14px 22px;padding:0;} li{margin:4px 0;}"
        "a{color:#0b66c3;text-decoration:none;} strong{color:#111827;}"
        "code{background:#f3f4f6;border-radius:4px;padding:1px 4px;font-family:SFMono-Regular,Consolas,monospace;font-size:0.92em;}"
        "pre{background:#111827;color:#f9fafb;border-radius:8px;padding:14px;overflow:auto;}"
        "pre code{background:transparent;color:inherit;padding:0;}"
        "blockquote{border-left:4px solid #d9468c;background:#fdf2f8;margin:0 0 14px;padding:10px 12px;color:#374151;}"
        "table{border-collapse:collapse;width:100%;margin:0 0 16px;} th,td{border:1px solid #d1d5db;padding:8px 10px;text-align:left;vertical-align:top;}"
        "th{background:#eff6ff;color:#111827;font-weight:700;} tr:nth-child(even) td{background:#f9fafb;} hr{border:0;border-top:1px solid #e5e7eb;margin:18px 0;}"
        "</style></head><body>"
        f'<div class="kdcube-email">{body}</div>'
        "</body></html>"
    )


def email_html_to_text(html_body: str) -> str:
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", str(html_body or ""))
    text = re.sub(r"(?i)</\s*(p|div|h[1-6]|li|tr|table|blockquote)\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return html.unescape(re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n\s*", "\n\n", text))).strip()


def build_email_message(
    *,
    sender_email: str,
    sender_name: str = "",
    recipients: list[str],
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    subject: str,
    body_text: str,
    body_html: str = "",
    attachments: list[Mapping[str, Any]] | None = None,
) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = formataddr((sender_name, sender_email)) if sender_name else sender_email
    msg["To"] = ", ".join(recipients)
    if cc:
        msg["Cc"] = ", ".join(cc)
    if bcc:
        msg["Bcc"] = ", ".join(bcc)
    msg["Subject"] = str(subject or "KDCube report").strip() or "KDCube report"
    text = str(body_text or "").strip()
    html_body = str(body_html or "").strip()
    if text and not html_body:
        html_body = markdown_to_email_html(text)
    if not text and html_body:
        text = email_html_to_text(html_body)
    msg.set_content(text or "(empty report)")
    if html_body:
        msg.add_alternative(html_body, subtype="html")
    for item in attachments or []:
        maintype, _, subtype = str(item.get("mime_type") or "application/octet-stream").partition("/")
        if not subtype:
            maintype, subtype = "application", "octet-stream"
        msg.add_attachment(
            item["data"],
            maintype=maintype,
            subtype=subtype,
            filename=safe_email_filename(str(item.get("filename") or "attachment.bin")),
        )
    return msg


# Backward-compatible aliases for bundle code that still uses the original
# private helper names during migration.
_split_addresses = split_email_addresses
_safe_filename = safe_email_filename
_email_inline_markdown_to_html = inline_markdown_to_email_html
_markdown_to_email_html = markdown_to_email_html
_email_html_to_text = email_html_to_text
_build_email_message = build_email_message
