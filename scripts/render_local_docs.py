"""Render local ignored markdown docs into simple standalone HTML.

Currently used for `Ekaette_Architecture.md` -> `Ekaette_Architecture.html`.
The renderer is intentionally lightweight and dependency-free so it can run in
the existing dev environment without external markdown packages.
"""

from __future__ import annotations

import argparse
import html
import re
from pathlib import Path


INLINE_CODE_RE = re.compile(r"`([^`]+)`")
INLINE_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
INLINE_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def _render_inline(text: str) -> str:
    escaped = html.escape(text, quote=False)
    escaped = INLINE_LINK_RE.sub(r'<a href="\2">\1</a>', escaped)
    escaped = INLINE_BOLD_RE.sub(r"<strong>\1</strong>", escaped)
    escaped = INLINE_CODE_RE.sub(r"<code>\1</code>", escaped)
    return escaped


def _table_rows(lines: list[str], start: int) -> tuple[str, int] | None:
    if start + 1 >= len(lines):
        return None
    head = lines[start]
    sep = lines[start + 1]
    if "|" not in head or "|" not in sep:
        return None
    if not re.fullmatch(r"\s*\|?[\s:-]+\|[\s|:-]*\|?\s*", sep):
        return None

    def split_row(row: str) -> list[str]:
        raw = row.strip().strip("|")
        return [cell.strip() for cell in raw.split("|")]

    header_cells = split_row(head)
    body_rows: list[list[str]] = []
    i = start + 2
    while i < len(lines):
        row = lines[i]
        if "|" not in row or not row.strip():
            break
        body_rows.append(split_row(row))
        i += 1

    parts = ["<table>", "<thead><tr>"]
    for cell in header_cells:
        parts.append(f"<th>{_render_inline(cell)}</th>")
    parts.append("</tr></thead>")
    if body_rows:
        parts.append("<tbody>")
        for row in body_rows:
            parts.append("<tr>")
            for cell in row:
                parts.append(f"<td>{_render_inline(cell)}</td>")
            parts.append("</tr>")
        parts.append("</tbody>")
    parts.append("</table>")
    return "".join(parts), i


def render_markdown(markdown_text: str) -> tuple[str, bool]:
    lines = markdown_text.splitlines()
    i = 0
    parts: list[str] = []
    has_mermaid = False

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        if stripped.startswith("```"):
            lang = stripped[3:].strip()
            fence_lines: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                fence_lines.append(lines[i])
                i += 1
            content = "\n".join(fence_lines)
            if lang == "mermaid":
                has_mermaid = True
                parts.append(f'<pre class="mermaid">{html.escape(content)}</pre>')
            else:
                klass = f' class="language-{html.escape(lang)}"' if lang else ""
                parts.append(f"<pre><code{klass}>{html.escape(content)}</code></pre>")
            i += 1
            continue

        if stripped in {"---", "***"}:
            parts.append("<hr>")
            i += 1
            continue

        table_rendered = _table_rows(lines, i)
        if table_rendered is not None:
            table_html, next_i = table_rendered
            parts.append(table_html)
            i = next_i
            continue

        if stripped.startswith("> "):
            quote_lines: list[str] = []
            while i < len(lines) and lines[i].strip().startswith("> "):
                quote_lines.append(lines[i].strip()[2:])
                i += 1
            parts.append(f"<blockquote>{_render_inline(' '.join(quote_lines))}</blockquote>")
            continue

        if stripped.startswith("- "):
            items: list[str] = []
            while i < len(lines) and lines[i].strip().startswith("- "):
                items.append(lines[i].strip()[2:])
                i += 1
            parts.append("<ul>")
            for item in items:
                parts.append(f"<li>{_render_inline(item)}</li>")
            parts.append("</ul>")
            continue

        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading:
            level = len(heading.group(1))
            text = _render_inline(heading.group(2).strip())
            parts.append(f"<h{level}>{text}</h{level}>")
            i += 1
            continue

        para_lines = [stripped]
        i += 1
        while i < len(lines):
            candidate = lines[i].strip()
            if not candidate:
                break
            if candidate.startswith(("```", "> ", "- ", "#")) or candidate in {"---", "***"}:
                break
            if _table_rows(lines, i) is not None:
                break
            para_lines.append(candidate)
            i += 1
        parts.append(f"<p>{_render_inline(' '.join(para_lines))}</p>")

    return "\n".join(parts), has_mermaid


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
  {mermaid_head}
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      max-width: 1100px;
      margin: 0 auto;
      padding: 32px 20px 60px;
      background: #f5f5f5;
      color: #1a1a1a;
      line-height: 1.65;
    }}
    main {{
      background: #fff;
      border-radius: 16px;
      padding: 28px 32px;
      box-shadow: 0 2px 12px rgba(0,0,0,0.08);
    }}
    h1, h2, h3, h4 {{
      color: #16213e;
    }}
    h1 {{
      border-bottom: 3px solid #16213e;
      padding-bottom: 12px;
      margin-top: 0;
    }}
    code {{
      background: #f0f4f8;
      padding: 0.12rem 0.35rem;
      border-radius: 6px;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 0.95em;
    }}
    pre {{
      background: #0f172a;
      color: #e2e8f0;
      padding: 16px;
      border-radius: 12px;
      overflow-x: auto;
    }}
    pre code {{
      background: transparent;
      padding: 0;
      color: inherit;
    }}
    blockquote {{
      margin: 0;
      padding: 12px 16px;
      border-left: 4px solid #1565c0;
      background: #edf4ff;
      border-radius: 8px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      margin: 18px 0;
      background: #fff;
    }}
    th, td {{
      border: 1px solid #d8dee9;
      padding: 10px 12px;
      text-align: left;
      vertical-align: top;
    }}
    th {{
      background: #edf2f7;
    }}
    a {{
      color: #1565c0;
      text-decoration: none;
    }}
    a:hover {{
      text-decoration: underline;
    }}
    hr {{
      border: none;
      border-top: 1px solid #e2e8f0;
      margin: 28px 0;
    }}
  </style>
</head>
<body>
  <main>
{content}
  </main>
  {mermaid_script}
</body>
</html>
"""


def build_html(title: str, body_html: str, has_mermaid: bool) -> str:
    mermaid_head = ""
    mermaid_script = ""
    if has_mermaid:
        mermaid_head = (
            '<script src="https://cdn.jsdelivr.net/npm/mermaid@11.12.3/dist/mermaid.min.js" '
            'integrity="sha384-jFhLSLFn4m565eRAS0CDMWubMqOtfZWWbE8kqgGdU+VHbJ3B2G/4X8u+0BM8MtdU" '
            'crossorigin="anonymous"></script>'
        )
        mermaid_script = (
            "<script>\n"
            "  mermaid.initialize({ startOnLoad: true, theme: 'default', securityLevel: 'loose' });\n"
            "</script>"
        )
    return HTML_TEMPLATE.format(
        title=html.escape(title),
        mermaid_head=mermaid_head,
        content=body_html,
        mermaid_script=mermaid_script,
    )


def render_file(input_path: Path, output_path: Path) -> None:
    raw = input_path.read_text()
    body_html, has_mermaid = render_markdown(raw)
    title = input_path.stem.replace("_", " ")
    match = re.search(r"^#\s+(.+)$", raw, flags=re.MULTILINE)
    if match:
        title = match.group(1).strip()
    output_path.write_text(build_html(title, body_html, has_mermaid))


DEFAULT_DOCS = {
    "Ekaette_Architecture.md": "Ekaette_Architecture.html",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Render local ignored markdown docs into HTML.")
    parser.add_argument("--repo-root", default=".", help="Repository root")
    parser.add_argument("--input", help="Single markdown file to render")
    parser.add_argument("--output", help="Single HTML output path")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    if args.input and args.output:
        render_file((repo_root / args.input).resolve(), (repo_root / args.output).resolve())
        return

    for source, target in DEFAULT_DOCS.items():
        input_path = (repo_root / source).resolve()
        if not input_path.exists():
            continue
        render_file(input_path, (repo_root / target).resolve())


if __name__ == "__main__":
    main()
