# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chat/sdk/pub/pages.py
"""Catalog pages and site chrome for public content.

A catalog is a configured slug prefix of an alias served as a server-rendered,
paginated, searchable listing page; the same data renders as the collapsible
side rail on item pages under that prefix. The chrome is a slim sticky header
(brand + navigation) shown above catalog and item pages.

Everything renders from the hot alias index (bounded card records) — a catalog
request never touches the durable backend. Search (``?q=``) dispatches to the
providing app's declared search hook when one exists and falls back to a
lexical match over the index otherwise.

Design language (editorial, approved 2026-07-14): a compact masthead (eyebrow,
display-serif title, one-line description, count + latest date), a segmented
fold control, a quiet search toolbar, and the article list as a full-bleed
surface band of hairline-divided rows — selection states are transparent tints
of the catalog accent (never solid fills), radii stay modest. All classes are
namespaced ``kdcpub-`` and the styles are self-contained, so authored article
CSS and the chrome cannot bleed into each other.

Styling boundary: the SDK owns behavior and semantic structure plus a neutral
default theme; every visual decision is a ``--kdcpub-*`` token, and the app
overrides tokens or appends whole stylesheets from config — see
``model.PublicContentPresentation`` and the styling README under
``docs/sdk/solutions/cdn-pub``.
"""
from __future__ import annotations

import html
import re
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import quote_plus, urlsplit

from kdcube_ai_app.apps.chat.sdk.pub.model import (
    PublicContentAliasConfig,
    PublicContentCatalogConfig,
    PublicContentChromeConfig,
    PublicContentIndexEntry,
)

_RAIL_ITEM_CAP = 120
_RAIL_PAGE_SIZE = 6


def _esc(value: str) -> str:
    return html.escape(str(value or ""), quote=True)


# ------------------------------------------------------------------ theming
#
# Boundary: the SDK owns behavior and semantic structure (routing, SEO,
# pagination, search, accessibility, the stable ``kdcpub-*`` classes) and a
# neutral default theme. Applications own presentation: every visual decision
# below is expressed as a ``--kdcpub-*`` design token, and an app overrides
# tokens (``presentation.theme``) or whole styles (``presentation.stylesheets``,
# loaded after the SDK styles) from its config — see model.PublicContentPresentation.

_DEFAULT_THEME = {
    # color world
    "bg": "#F6FAFA",
    "surface": "#FFFFFF",
    "ink": "#0D1E2C",
    "dim": "#3A5672",
    "muted": "#7A99B0",
    "border": "#D8ECEB",
    "accent": "#01BEB2",
    "accent_dark": "#009C92",
    "accent_rgb": "1,190,178",
    # type + metrics
    "font": "Inter,system-ui,-apple-system,'Segoe UI',sans-serif",
    "display": "Georgia,'Times New Roman',serif",
    "radius": "10px",
    "width": "1140px",
}

# Emitted token values must stay inert inside a <style> block.
_SAFE_TOKEN_VALUE_RE = re.compile(r"^[^<>{};]*$")


def _hex_to_rgb(value: str) -> Optional[Tuple[int, int, int]]:
    text = str(value or "").strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6:
        return None
    try:
        return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return None


def _darken(rgb: Tuple[int, int, int], factor: float = 0.78) -> str:
    return "#%02X%02X%02X" % tuple(max(0, min(255, int(c * factor))) for c in rgb)


def catalog_theme(
    catalog: Optional[PublicContentCatalogConfig],
    alias_config: Optional[PublicContentAliasConfig] = None,
) -> Dict[str, str]:
    """The resolved design tokens for a catalog surface.

    Merge order (later wins): SDK defaults ← alias ``presentation.theme`` ←
    catalog accent/background/border shorthands ← catalog
    ``presentation.theme``. When an override changes ``accent`` without
    explicitly providing ``accent_rgb``/``accent_dark``, both are derived from
    the accent hex so tints and hovers follow automatically.
    """
    theme = dict(_DEFAULT_THEME)
    overridden: set = set()

    def _apply(mapping: Optional[Dict[str, str]]) -> None:
        for key, value in (mapping or {}).items():
            name = str(key).strip().lower().replace("-", "_")
            text = str(value).strip()
            if not name or not _SAFE_TOKEN_VALUE_RE.match(text):
                continue
            theme[name] = text
            overridden.add(name)

    if alias_config is not None and alias_config.presentation is not None:
        _apply(alias_config.presentation.theme)
    if catalog is not None:
        shorthand: Dict[str, str] = {}
        if _hex_to_rgb(catalog.accent) is not None:
            shorthand["accent"] = catalog.accent.strip()
        if catalog.background.strip():
            shorthand["bg"] = catalog.background.strip()
        if catalog.border.strip():
            shorthand["border"] = catalog.border.strip()
        _apply(shorthand)
        if catalog.presentation is not None:
            _apply(catalog.presentation.theme)

    if "accent" in overridden:
        rgb = _hex_to_rgb(theme["accent"])
        if rgb is not None:
            if "accent_rgb" not in overridden:
                theme["accent_rgb"] = f"{rgb[0]},{rgb[1]},{rgb[2]}"
            if "accent_dark" not in overridden:
                theme["accent_dark"] = _darken(rgb)
    return theme


def _theme_css(theme: Dict[str, str]) -> str:
    decls = "".join(
        f"--kdcpub-{name.replace('_', '-')}:{value};"
        for name, value in theme.items()
        if _SAFE_TOKEN_VALUE_RE.match(str(value))
    )
    return f"<style>:root{{{decls}}}</style>"


def presentation_stylesheets(
    alias_config: Optional[PublicContentAliasConfig],
    catalog: Optional[PublicContentCatalogConfig] = None,
) -> str:
    """App-owned stylesheet links (alias-level first, then the catalog's),
    emitted AFTER the SDK styles + theme tokens so they can override anything
    via the stable ``kdcpub-*`` classes."""
    urls: List[str] = []
    if alias_config is not None and alias_config.presentation is not None:
        urls.extend(alias_config.presentation.stylesheets)
    if catalog is not None and catalog.presentation is not None:
        urls.extend(catalog.presentation.stylesheets)
    return "".join(f'<link rel="stylesheet" href="{_esc(url)}">' for url in urls)


# ------------------------------------------------------------------ styles

_BASE_CSS = """
  :root{
    --kdcpub-bg:#F6FAFA; --kdcpub-surface:#FFFFFF; --kdcpub-ink:#0D1E2C;
    --kdcpub-dim:#3A5672; --kdcpub-muted:#7A99B0;
    --kdcpub-accent:#01BEB2; --kdcpub-accent-dark:#009C92;
    --kdcpub-accent-rgb:1,190,178; --kdcpub-border:#D8ECEB;
    --kdcpub-font:Inter,system-ui,-apple-system,'Segoe UI',sans-serif;
    --kdcpub-display:Georgia,'Times New Roman',serif;
    --kdcpub-radius:10px; --kdcpub-width:1140px;
  }
  /* Full-bleed bands stretch 50vw past the column; clip the scrollbar-width
     overshoot without creating a scroll container (keeps sticky working). */
  html{overflow-x:clip}
  .kdcpub-header,.kdcpub-header *,.kdcpub-wrap,.kdcpub-wrap *,.kdcpub-rail,.kdcpub-rail *,
  .kdcpub-crumb,.kdcpub-crumb *,.kdcpub-foot,.kdcpub-foot *{box-sizing:border-box}
  body.kdcpub-body{margin:0;background:var(--kdcpub-bg);font-family:var(--kdcpub-font);color:var(--kdcpub-ink)}
  .kdcpub-header{position:sticky;top:0;z-index:50;background:var(--kdcpub-bg);border-bottom:1px solid var(--kdcpub-border);font-family:var(--kdcpub-font)}
  @supports (background:color-mix(in srgb,#fff 50%,transparent)){
    .kdcpub-header{background:color-mix(in srgb,var(--kdcpub-bg) 96%,transparent);backdrop-filter:blur(10px)}
  }
  .kdcpub-header-in{max-width:var(--kdcpub-width);min-height:60px;margin:0 auto;display:flex;align-items:center;gap:22px;padding:12px 24px}
  .kdcpub-brand{display:flex;align-items:center;gap:8px;text-decoration:none;margin-right:auto;color:var(--kdcpub-ink);font-weight:700;font-size:15px}
  .kdcpub-brand img{height:28px;width:auto;display:block}
  .kdcpub-nav{display:flex;align-items:center;gap:12px;overflow-x:auto;scrollbar-width:none}
  .kdcpub-nav::-webkit-scrollbar{display:none}
  .kdcpub-nav a{display:flex;align-items:center;min-height:44px;white-space:nowrap;color:var(--kdcpub-dim);text-decoration:none;font-size:13px;font-weight:500;padding:8px 4px}
  .kdcpub-nav a:hover{color:var(--kdcpub-ink)}
  .kdcpub-nav a.kdcpub-active{color:var(--kdcpub-accent-dark);font-weight:600}
  .kdcpub-menu{display:none;flex-direction:column;align-items:center;justify-content:center;gap:4px;width:44px;height:44px;padding:0;border:0;border-radius:8px;background:transparent;cursor:pointer}
  .kdcpub-menu:hover{background:rgba(var(--kdcpub-accent-rgb),.07)}
  .kdcpub-menu span{display:block;width:20px;height:2px;border-radius:2px;background:var(--kdcpub-ink)}
  .kdcpub-chipbtn{display:inline-flex;align-items:center;gap:6px;padding:7px 12px;border-radius:8px;border:1px solid var(--kdcpub-border);background:var(--kdcpub-surface);color:var(--kdcpub-dim);font-family:inherit;font-size:13px;font-weight:600;text-decoration:none;cursor:pointer;line-height:1.2}
  .kdcpub-chipbtn:hover{border-color:rgba(var(--kdcpub-accent-rgb),.5);color:var(--kdcpub-accent-dark)}
  .kdcpub-date{color:var(--kdcpub-muted);font-size:13px;font-variant-numeric:tabular-nums}
  .kdcpub-rubric{font-size:10.5px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;padding:2px 8px;border-radius:6px;background:rgba(var(--kdcpub-accent-rgb),.10);color:var(--kdcpub-accent-dark)}
  .kdcpub-terms{display:flex;flex-wrap:wrap;gap:6px}
  .kdcpub-term{font-size:11.5px;color:var(--kdcpub-dim);border:1px solid var(--kdcpub-border);border-radius:6px;padding:2px 8px;background:var(--kdcpub-bg)}
  .kdcpub-card-top{display:flex;align-items:center;gap:10px;margin-bottom:6px}
  .kdcpub-foot{font-family:var(--kdcpub-font)}
  .kdcpub-foot-in{max-width:var(--kdcpub-width);margin:0 auto;display:flex;align-items:center;gap:16px;flex-wrap:wrap;padding:22px 24px 46px;color:var(--kdcpub-muted);font-size:12.5px}
  .kdcpub-foot-nav{margin-left:auto;display:flex;align-items:center;gap:14px;flex-wrap:wrap}
  .kdcpub-foot a{color:var(--kdcpub-muted);text-decoration:none}
  .kdcpub-foot a:hover{color:var(--kdcpub-accent-dark)}
  @media(max-width:720px){
    .kdcpub-header-in{position:relative;gap:12px;padding:8px 16px}
    .kdcpub-brand img{height:24px}
    .kdcpub-menu{display:flex}
    .kdcpub-nav{display:none;position:absolute;left:0;right:0;top:100%;z-index:51;flex-direction:column;align-items:stretch;gap:0;padding:8px 16px 14px;overflow:visible;background:var(--kdcpub-bg);border-bottom:1px solid var(--kdcpub-border);box-shadow:0 8px 18px rgba(13,30,44,.08)}
    .kdcpub-header.kdcpub-menu-open .kdcpub-nav{display:flex}
    .kdcpub-nav a{width:100%;min-height:46px;padding:10px 8px;font-size:14px}
    .kdcpub-foot-in{padding:18px 18px 40px}
    .kdcpub-foot-nav{margin-left:0}
  }
"""

_CATALOG_CSS = """
  .kdcpub-wrap{max-width:var(--kdcpub-width);margin:0 auto;padding:34px 24px 0;font-family:var(--kdcpub-font)}
  /* masthead — compact editorial: eyebrow, display title, one-line description,
     count + latest date */
  .kdcpub-hero{margin:0 0 4px}
  .kdcpub-eyebrow{font-size:12px;font-weight:700;letter-spacing:.15em;color:var(--kdcpub-accent-dark);text-transform:uppercase}
  .kdcpub-hero h1{font-family:var(--kdcpub-display);font-weight:600;font-size:clamp(28px,4.2vw,36px);line-height:1.12;letter-spacing:-.01em;margin:8px 0;color:var(--kdcpub-ink)}
  /* Always reserve two lines: fold descriptions differ in length, and a
     1-vs-2-line subtitle would shift everything below when switching folds. */
  .kdcpub-sub{color:var(--kdcpub-dim);font-size:15px;line-height:1.55;max-width:62ch;margin:0 0 10px;min-height:3.1em}
  .kdcpub-meta{color:var(--kdcpub-muted);font-size:13px;font-variant-numeric:tabular-nums}
  .kdcpub-meta a{color:var(--kdcpub-accent-dark)}
  /* toolbar — segmented fold control + quiet search, one stable row */
  .kdcpub-toolbar{display:flex;align-items:center;justify-content:space-between;gap:14px;flex-wrap:wrap;margin:18px 0 0}
  .kdcpub-folds{display:inline-flex;align-items:center;gap:2px;padding:3px;background:var(--kdcpub-surface);border:1px solid var(--kdcpub-border);border-radius:var(--kdcpub-radius);max-width:100%;overflow-x:auto;scrollbar-width:none}
  .kdcpub-folds::-webkit-scrollbar{display:none}
  .kdcpub-fold{padding:6px 13px;border-radius:calc(var(--kdcpub-radius) - 3px);border:1px solid transparent;color:var(--kdcpub-dim);font-size:13px;font-weight:600;text-decoration:none;white-space:nowrap}
  .kdcpub-fold:hover{color:var(--kdcpub-accent-dark)}
  .kdcpub-fold.kdcpub-on{background:rgba(var(--kdcpub-accent-rgb),.10);border-color:rgba(var(--kdcpub-accent-rgb),.30);color:var(--kdcpub-accent-dark)}
  .kdcpub-search{display:flex;gap:8px;flex:1;min-width:240px;max-width:380px;margin-left:auto}
  .kdcpub-search input{flex:1;min-width:0;padding:8px 13px;border-radius:calc(var(--kdcpub-radius) - 1px);border:1px solid var(--kdcpub-border);background:var(--kdcpub-surface);font:inherit;font-size:13.5px;color:var(--kdcpub-ink);outline:none}
  .kdcpub-search input:focus{border-color:rgba(var(--kdcpub-accent-rgb),.55);box-shadow:0 0 0 3px rgba(var(--kdcpub-accent-rgb),.10)}
  .kdcpub-search button{padding:8px 16px;border-radius:calc(var(--kdcpub-radius) - 1px);border:0;background:var(--kdcpub-accent);color:#fff;font:inherit;font-size:13.5px;font-weight:600;cursor:pointer}
  .kdcpub-search button:hover{background:var(--kdcpub-accent-dark)}
  .kdcpub-search-hint{color:var(--kdcpub-muted);font-size:12.5px;margin:8px 0 0}
  .kdcpub-search-hint a{color:var(--kdcpub-accent-dark)}
  /* article list — a full-bleed surface band; rows divided by hairlines,
     aligned back to the page column */
  .kdcpub-band{margin:18px calc(50% - 50vw) 0;padding:4px calc(50vw - 50%) 0;background:var(--kdcpub-surface);border-top:1px solid var(--kdcpub-border);border-bottom:1px solid var(--kdcpub-border)}
  .kdcpub-list{display:block}
  .kdcpub-row{position:relative;padding:17px 0 15px 22px;border-bottom:1px solid var(--kdcpub-border)}
  .kdcpub-row:last-child{border-bottom:0}
  .kdcpub-row::before{content:"";position:absolute;left:2px;top:25px;width:7px;height:7px;border-radius:50%;background:var(--kdcpub-accent)}
  .kdcpub-row h2{margin:0 0 5px;font-size:16.5px;line-height:1.35;font-weight:650}
  .kdcpub-row h2 a{color:var(--kdcpub-ink);text-decoration:none}
  .kdcpub-row h2 a:hover{color:var(--kdcpub-accent-dark)}
  /* Clamp AND reserve two lines, so every row is the same height whether its
     summary runs one line, two, or is absent — no jumping between rows/pages. */
  .kdcpub-row p{margin:0 0 8px;color:var(--kdcpub-dim);font-size:13.5px;line-height:1.55;max-width:76ch;min-height:3.1em;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
  .kdcpub-row-meta{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
  .kdcpub-row-meta .kdcpub-date{font-size:12.5px;margin-left:auto}
  /* deliberate empty state */
  .kdcpub-empty{padding:56px 0 60px;text-align:center}
  .kdcpub-empty-t{font-family:var(--kdcpub-display);font-weight:600;font-size:22px;color:var(--kdcpub-ink);margin:0 0 8px}
  .kdcpub-empty p{margin:0 0 10px;color:var(--kdcpub-dim);font-size:14px}
  .kdcpub-empty a{color:var(--kdcpub-accent-dark);font-weight:600}
  /* quiet pagination — hairline row inside the band */
  .kdcpub-pager{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:13px 0;border-top:1px solid var(--kdcpub-border)}
  .kdcpub-pager span{color:var(--kdcpub-muted);font-size:12.5px;font-variant-numeric:tabular-nums}
  .kdcpub-pgbtn{color:var(--kdcpub-accent-dark);font-size:13.5px;font-weight:600;text-decoration:none;white-space:nowrap}
  .kdcpub-pgbtn:hover{text-decoration:underline}
  .kdcpub-pgbtn.kdcpub-off{color:var(--kdcpub-muted);opacity:.45;pointer-events:none}
  @media(max-width:720px){
    .kdcpub-wrap{padding:26px 18px 0}
    .kdcpub-toolbar{flex-direction:column;align-items:stretch}
    .kdcpub-search{max-width:none;margin-left:0}
    .kdcpub-row{padding-left:20px}
    .kdcpub-row::before{left:1px}
  }
"""

_RAIL_CSS = """
  .kdcpub-layout{display:grid;grid-template-columns:324px minmax(0,1fr);align-items:start;max-width:1420px;margin:0 auto;font-family:var(--kdcpub-font)}
  .kdcpub-layout.kdcpub-rail-hidden{grid-template-columns:minmax(0,1fr)}
  .kdcpub-layout.kdcpub-rail-hidden .kdcpub-rail{display:none}
  .kdcpub-rail{position:sticky;top:51px;height:calc(100vh - 51px);padding:16px 0 16px 16px}
  .kdcpub-rail-card{display:flex;flex-direction:column;height:100%;background:var(--kdcpub-surface);border:1px solid var(--kdcpub-border);border-radius:14px;overflow:hidden}
  .kdcpub-rail-head{display:flex;align-items:center;gap:8px;padding:14px 14px 10px}
  .kdcpub-rail-title{font-size:13px;font-weight:700;color:var(--kdcpub-ink);margin-right:auto}
  .kdcpub-rail-title span{color:var(--kdcpub-muted);font-weight:600}
  .kdcpub-rail-btn{border:1px solid var(--kdcpub-border);background:var(--kdcpub-surface);color:var(--kdcpub-dim);border-radius:8px;width:26px;height:26px;font-size:13px;cursor:pointer;line-height:1}
  .kdcpub-rail-btn:hover{border-color:rgba(var(--kdcpub-accent-rgb),.5);color:var(--kdcpub-accent-dark)}
  .kdcpub-rail-search{display:flex;gap:6px;padding:0 14px 10px}
  .kdcpub-rail-search input{flex:1;min-width:0;padding:8px 11px;border-radius:8px;border:1px solid var(--kdcpub-border);background:var(--kdcpub-bg);font:inherit;font-size:13px;color:var(--kdcpub-ink);outline:none}
  .kdcpub-rail-search input:focus{border-color:rgba(var(--kdcpub-accent-rgb),.6)}
  .kdcpub-rail-search button{padding:8px 13px;border-radius:8px;border:0;background:var(--kdcpub-accent);color:#fff;font:inherit;font-size:13px;font-weight:600;cursor:pointer}
  .kdcpub-rail-search button:hover{background:var(--kdcpub-accent-dark)}
  .kdcpub-rail-folds{display:flex;gap:6px;padding:0 14px 12px}
  .kdcpub-rail-fold{flex:1;text-align:center;padding:5px 0;border-radius:8px;border:1px solid var(--kdcpub-border);color:var(--kdcpub-dim);font-size:12px;font-weight:600;text-decoration:none;background:var(--kdcpub-surface)}
  .kdcpub-rail-fold:hover{border-color:rgba(var(--kdcpub-accent-rgb),.5);color:var(--kdcpub-accent-dark)}
  .kdcpub-rail-fold.kdcpub-on{background:rgba(var(--kdcpub-accent-rgb),.10);border-color:rgba(var(--kdcpub-accent-rgb),.45);color:var(--kdcpub-accent-dark)}
  .kdcpub-rail-list{flex:1;overflow-y:auto;padding:6px 8px;border-top:1px solid var(--kdcpub-border)}
  .kdcpub-rail-item{display:block;padding:10px 12px;border-radius:10px;border:1px solid transparent;text-decoration:none;margin:4px 2px}
  .kdcpub-rail-item:hover{background:rgba(var(--kdcpub-accent-rgb),.05)}
  .kdcpub-rail-item.kdcpub-now{background:rgba(var(--kdcpub-accent-rgb),.07);border-color:rgba(var(--kdcpub-accent-rgb),.25)}
  .kdcpub-rail-item .kdcpub-card-top{display:flex;align-items:center;gap:8px;margin-bottom:4px}
  .kdcpub-rail-item .kdcpub-date{font-size:11.5px}
  .kdcpub-rail-item .kdcpub-rubric{font-size:9.5px;padding:2px 6px}
  .kdcpub-rail-item .t{font-size:13px;font-weight:600;color:var(--kdcpub-ink);line-height:1.35;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
  .kdcpub-rail-item .s{margin:3px 0 6px;font-size:12px;color:var(--kdcpub-dim);line-height:1.45;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
  .kdcpub-rail-item .kdcpub-terms{gap:4px}
  .kdcpub-rail-item .kdcpub-term{font-size:10.5px;padding:2px 7px;background:var(--kdcpub-surface)}
  .kdcpub-rail-pager{display:flex;align-items:center;justify-content:space-between;padding:10px 14px;border-top:1px solid var(--kdcpub-border)}
  .kdcpub-rail-pager span{font-size:12px;color:var(--kdcpub-muted)}
  .kdcpub-rail-pg{border:1px solid var(--kdcpub-border);background:var(--kdcpub-surface);color:var(--kdcpub-dim);border-radius:8px;width:28px;height:26px;font-size:13px;cursor:pointer}
  .kdcpub-rail-pg:hover{border-color:rgba(var(--kdcpub-accent-rgb),.5);color:var(--kdcpub-accent-dark)}
  .kdcpub-rail-pg:disabled{opacity:.35;cursor:default}
  .kdcpub-main{min-width:0;padding:16px 24px 48px 8px}
  .kdcpub-main-inner{max-width:980px;margin:0}
  .kdcpub-layout.kdcpub-rail-hidden .kdcpub-main{padding-left:24px}
  .kdcpub-layout.kdcpub-rail-hidden .kdcpub-main-inner{margin:0 auto}
  .kdcpub-crumb{display:flex;align-items:center;gap:8px;margin:0 0 12px}
  #kdcpub-show-rail{display:none}
  body.kdcpub-rail-hidden-body #kdcpub-show-rail{display:inline-flex}
  .kdcpub-article-card{background:#fff;border:1px solid var(--kdcpub-border);border-radius:14px;overflow:hidden}
  @media (max-width: 900px){
    .kdcpub-layout{grid-template-columns:minmax(0,1fr)}
    .kdcpub-rail{display:none}
    #kdcpub-show-rail{display:none !important}
  }
"""

# Collapse toggle (persisted per browser) + client-side rail pagination over
# the server-embedded items. Progressive enhancement: without JS the rail
# shows the full (scrollable) list and the hide/show controls stay inert.
_RAIL_JS = """
<script>
(function(){
  var layout=document.querySelector('.kdcpub-layout');
  var railBtn=document.querySelector('.kdcpub-rail-btn');
  var showBtn=document.getElementById('kdcpub-show-rail');
  if(!layout||!railBtn||!showBtn)return;
  function setHidden(h){
    layout.classList.toggle('kdcpub-rail-hidden',h);
    document.body.classList.toggle('kdcpub-rail-hidden-body',h);
    try{localStorage.setItem('kdcpub-rail',h?'hidden':'open')}catch(e){}
  }
  railBtn.addEventListener('click',function(){setHidden(true)});
  showBtn.addEventListener('click',function(){setHidden(false)});
  try{if(localStorage.getItem('kdcpub-rail')==='hidden')setHidden(true)}catch(e){}
  var PAGE=__RAIL_PAGE_SIZE__, off=0;
  var items=Array.prototype.slice.call(document.querySelectorAll('.kdcpub-rail-item'));
  var label=document.querySelector('.kdcpub-rail-pager span');
  var prev=document.querySelector('.kdcpub-rail-pg[data-dir="prev"]');
  var next=document.querySelector('.kdcpub-rail-pg[data-dir="next"]');
  if(!label||!prev||!next||!items.length)return;
  function draw(){
    items.forEach(function(el,i){el.style.display=(i>=off&&i<off+PAGE)?'block':'none'});
    label.textContent=(off+1)+'\\u2013'+Math.min(off+PAGE,items.length)+' of '+items.length;
    prev.disabled=off<=0; next.disabled=off+PAGE>=items.length;
  }
  prev.addEventListener('click',function(){off=Math.max(0,off-PAGE);draw()});
  next.addEventListener('click',function(){off=Math.min(items.length-1,off+PAGE);draw()});
  var now=items.findIndex(function(el){return el.classList.contains('kdcpub-now')});
  if(now>=0) off=Math.floor(now/PAGE)*PAGE;
  draw();
})();
</script>
"""


# ------------------------------------------------------------------ pieces

def _nav_link_is_active(link_href: str, active_href: str) -> bool:
    """Match a navigation section across relative and canonical URLs."""
    if not link_href or not active_href:
        return False
    link = urlsplit(link_href)
    active = urlsplit(active_href)
    if link.netloc and active.netloc and link.netloc != active.netloc:
        return False
    link_path = "/" + link.path.strip("/") if link.path.strip("/") else "/"
    active_path = "/" + active.path.strip("/") if active.path.strip("/") else "/"
    if link_path == "/":
        return active_path == "/"
    return active_path == link_path or active_path.startswith(f"{link_path}/")


def render_chrome_header(
    chrome: Optional[PublicContentChromeConfig],
    *,
    active_href: str = "",
) -> str:
    """The sticky brand+nav header. Empty string when no chrome is configured."""
    if chrome is None:
        return ""
    if chrome.logo_url:
        brand_body = f'<img src="{_esc(chrome.logo_url)}" alt="{_esc(chrome.brand_label or "Home")}">'
    else:
        brand_body = _esc(chrome.brand_label or "Home")
    brand = (
        f'<a class="kdcpub-brand" href="{_esc(chrome.brand_href or "/")}">{brand_body}</a>'
    )
    nav_links = "".join(
        f'<a href="{_esc(link.href)}"'
        + (
            ' class="kdcpub-active" aria-current="page"'
            if _nav_link_is_active(link.href, active_href)
            else ""
        )
        + f">{_esc(link.label)}</a>"
        for link in chrome.links
    )
    menu = (
        '<button class="kdcpub-menu" type="button" aria-expanded="false" '
        'aria-controls="kdcpub-site-nav" aria-label="Open navigation">'
        '<span></span><span></span><span></span></button>'
        if nav_links
        else ""
    )
    nav = (
        f'<nav class="kdcpub-nav" id="kdcpub-site-nav" '
        f'aria-label="Primary navigation">{nav_links}</nav>'
        if nav_links
        else ""
    )
    menu_script = (
        "<script>(function(){"
        "var h=document.querySelector('.kdcpub-header');"
        "var b=h&&h.querySelector('.kdcpub-menu');"
        "if(!h||!b)return;"
        "function close(){h.classList.remove('kdcpub-menu-open');"
        "b.setAttribute('aria-expanded','false');b.setAttribute('aria-label','Open navigation')}"
        "b.addEventListener('click',function(){var open=!h.classList.contains('kdcpub-menu-open');"
        "h.classList.toggle('kdcpub-menu-open',open);"
        "b.setAttribute('aria-expanded',open?'true':'false');"
        "b.setAttribute('aria-label',open?'Close navigation':'Open navigation')});"
        "document.addEventListener('keydown',function(e){if(e.key==='Escape')close()});"
        "})();</script>"
        if nav_links
        else ""
    )
    return (
        '<header class="kdcpub-header"><div class="kdcpub-header-in">'
        f"{brand}{menu}{nav}"
        "</div></header>"
        f"{menu_script}"
    )


def _card_top(entry: PublicContentIndexEntry) -> str:
    date = (entry.published_at or "")[:10]
    kicker = (
        f'<span class="kdcpub-rubric">{_esc(entry.kicker)}</span>' if entry.kicker else ""
    )
    return (
        f'<div class="kdcpub-card-top"><span class="kdcpub-date">{_esc(date)}</span>{kicker}</div>'
    )


def _terms_html(entry: PublicContentIndexEntry, *, limit: int = 5) -> str:
    chips = "".join(
        f'<span class="kdcpub-term">{_esc(tag)}</span>' for tag in entry.tags[:limit]
    )
    return f'<div class="kdcpub-terms">{chips}</div>' if chips else ""


def _catalog_row(entry: PublicContentIndexEntry, *, item_url: str) -> str:
    """One article row in the catalog band: title, clamped summary, then a
    quiet meta line (kicker badge + tag chips, date right-aligned). The summary
    paragraph renders even when empty — it reserves its two lines so every row
    is the same height."""
    summary = f"<p>{_esc(entry.summary)}</p>"
    kicker = (
        f'<span class="kdcpub-rubric">{_esc(entry.kicker)}</span>' if entry.kicker else ""
    )
    date = (entry.published_at or "")[:10]
    date_html = f'<span class="kdcpub-date">{_esc(date)}</span>' if date else ""
    meta = f'<div class="kdcpub-row-meta">{kicker}{_terms_html(entry)}{date_html}</div>'
    return (
        '<article class="kdcpub-row">'
        f'<h2><a href="{_esc(item_url)}">{_esc(entry.title or entry.slug)}</a></h2>'
        f"{summary}"
        f"{meta}"
        "</article>"
    )


def render_chrome_footer(
    chrome: Optional[PublicContentChromeConfig],
    *,
    site_name: str = "",
) -> str:
    """Quiet page footer: © site name plus the chrome's own navigation links
    (auth-neutral — only what the config declares)."""
    copyright_html = f"<span>© {_esc(site_name)}</span>" if site_name else ""
    links = "".join(
        f'<a href="{_esc(link.href)}">{_esc(link.label)}</a>'
        for link in (chrome.links if chrome else [])
    )
    nav = f'<nav class="kdcpub-foot-nav">{links}</nav>' if links else ""
    if not copyright_html and not nav:
        return ""
    return (
        '<footer class="kdcpub-foot"><div class="kdcpub-foot-in">'
        f"{copyright_html}{nav}"
        "</div></footer>"
    )


def _fold_pills(
    config: PublicContentAliasConfig,
    *,
    active_prefix: str,
    catalog_url_for: Callable[[PublicContentCatalogConfig], str],
    counts: Dict[str, int],
    css_class: str,
) -> str:
    pills = []
    for entry in config.catalogs:
        active = " kdcpub-on" if entry.prefix == active_prefix else ""
        count = counts.get(entry.prefix, 0)
        pills.append(
            f'<a class="{css_class}{active}" href="{_esc(catalog_url_for(entry))}">'
            f"{_esc(entry.label)} · {count}</a>"
        )
    return "".join(pills)


def _document(
    *,
    title: str,
    theme: Dict[str, str],
    extra_css: str,
    body: str,
    head_extra: str = "",
    meta_robots: str = "",
    app_styles: str = "",
) -> str:
    """The full HTML document. ``app_styles`` (presentation stylesheets) come
    AFTER the SDK styles and the theme tokens so the app's CSS wins."""
    robots = f'<meta name="robots" content="{_esc(meta_robots)}" />' if meta_robots else ""
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n'
        '<meta charset="utf-8" />\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1" />\n'
        f"<title>{_esc(title)}</title>\n"
        f"{robots}"
        f"<style>{_BASE_CSS}{extra_css}</style>{_theme_css(theme)}{app_styles}\n"
        f"{head_extra}\n"
        "</head>\n"
        '<body class="kdcpub-body">\n'
        f"{body}\n"
        "</body>\n</html>\n"
    )


# ------------------------------------------------------------------ catalog page

def render_catalog_page(
    *,
    config: PublicContentAliasConfig,
    catalog: PublicContentCatalogConfig,
    entries: List[PublicContentIndexEntry],
    counts: Dict[str, int],
    offset: int,
    query: str,
    catalog_url: str,
    catalog_url_for: Callable[[PublicContentCatalogConfig], str],
    item_url_for: Callable[[str], str],
    total_in_catalog: int,
    searched: bool,
    search_tier: str = "",
) -> str:
    """The server-rendered catalog page.

    ``entries`` is the already-windowed page slice when browsing, or the
    ranked result list when ``searched``; ``total_in_catalog`` is the full
    catalog size (browse) / result count (search). ``search_tier`` names what
    answered a search: ``engine`` (the app's declared search hook) or
    ``basic`` (the platform's match over the index cards) — the results hint
    states it so the reader knows the search depth.
    """
    theme = catalog_theme(catalog, config)
    page_size = catalog.page_size
    site = config.og_defaults.site_name

    header = render_chrome_header(config.chrome, active_href=catalog_url)

    cards = [
        _catalog_row(entry, item_url=item_url_for(entry.slug)) for entry in entries
    ]
    if not cards:
        if searched:
            empty_title = f"No articles match “{_esc(query)}”."
            empty_line = "Try a different phrase, or browse everything in this fold."
            empty_link = f'<a href="{_esc(catalog_url)}">Show all</a>'
        else:
            empty_title = "No articles yet."
            empty_line = "New writing lands here as soon as it is published."
            empty_link = ""
        cards = [
            '<div class="kdcpub-empty">'
            f'<div class="kdcpub-empty-t">{empty_title}</div>'
            f"<p>{empty_line}</p>{empty_link}"
            "</div>"
        ]

    folds = _fold_pills(
        config,
        active_prefix=catalog.prefix,
        catalog_url_for=catalog_url_for,
        counts=counts,
        css_class="kdcpub-fold",
    )
    folds_html = f'<div class="kdcpub-folds">{folds}</div>' if folds else ""

    latest = (entries[0].published_at or "")[:10] if entries and not searched else ""
    latest_meta = f" · latest {_esc(latest)}" if latest else ""
    meta_line = (
        f'<div class="kdcpub-meta">{total_in_catalog} '
        f'{"results" if searched else "articles"}{latest_meta}</div>'
    )

    placeholder = catalog.search_placeholder or f"Search {catalog.label.lower()}…"
    q_attr = f' value="{_esc(query)}"' if query else ""
    search_form = (
        f'<form class="kdcpub-search" method="get" action="{_esc(catalog_url)}">'
        f'<input type="search" name="q" placeholder="{_esc(placeholder)}"{q_attr}>'
        "<button type=\"submit\">Search</button></form>"
    )
    if searched:
        depth = (
            "matched over titles, summaries and tags"
            if search_tier == "basic"
            else "searched across titles, summaries, tags and full article text"
        )
        hint = (
            f'{total_in_catalog} results for “{_esc(query)}” · {depth} · '
            f'<a href="{_esc(catalog_url)}">clear search</a>'
        )
    else:
        hint = "Search covers titles, summaries, tags and full article text."
    search_hint = f'<p class="kdcpub-search-hint">{hint}</p>'

    def _page_url(new_offset: int) -> str:
        q_part = f"&q={quote_plus(query)}" if query else ""
        if new_offset <= 0:
            return f"{catalog_url}{('?q=' + quote_plus(query)) if query else ''}"
        return f"{catalog_url}?offset={new_offset}{q_part}"

    has_prev = offset > 0
    has_next = offset + page_size < total_in_catalog
    prev_cls = "" if has_prev else " kdcpub-off"
    next_cls = "" if has_next else " kdcpub-off"
    shown_from = offset + 1 if entries else 0
    shown_to = offset + len(entries)
    # An empty band carries its own message; a 0–0 pager row would only add noise.
    pager = (
        '<div class="kdcpub-pager">'
        f'<a class="kdcpub-pgbtn{prev_cls}" href="{_esc(_page_url(max(0, offset - page_size)))}">← Newer</a>'
        f"<span>{shown_from}–{shown_to} of {total_in_catalog}</span>"
        f'<a class="kdcpub-pgbtn{next_cls}" href="{_esc(_page_url(offset + page_size))}">Older →</a>'
        "</div>"
        if entries
        else ""
    )

    eyebrow = (
        f'<div class="kdcpub-eyebrow">{_esc(catalog.eyebrow)}</div>' if catalog.eyebrow else ""
    )
    # Rendered even when empty: the subtitle reserves two lines so switching
    # between folds with short/long/no descriptions never shifts the layout.
    subtitle = f'<p class="kdcpub-sub">{_esc(catalog.subtitle)}</p>'

    body = (
        f"{header}\n"
        '<main class="kdcpub-wrap">'
        f'<div class="kdcpub-hero">{eyebrow}<h1>{_esc(catalog.title or catalog.prefix)}</h1>'
        f"{subtitle}{meta_line}</div>"
        f'<div class="kdcpub-toolbar">{folds_html}{search_form}</div>'
        f"{search_hint}"
        f'<section class="kdcpub-band"><div class="kdcpub-list">{"".join(cards)}</div>'
        f"{pager}</section>"
        "</main>\n"
        + render_chrome_footer(config.chrome, site_name=site)
    )
    title = f"{catalog.title or catalog.prefix}" + (f" · {site}" if site else "")
    description = catalog.subtitle or f"Browse {catalog.title or catalog.label}."
    head_parts = []
    if catalog_url:
        head_parts.extend(
            [
                f'<link rel="canonical" href="{_esc(catalog_url)}" />',
                f'<meta property="og:url" content="{_esc(catalog_url)}" />',
            ]
        )
    head_parts.extend(
        [
            '<meta property="og:type" content="website" />',
            f'<meta property="og:title" content="{_esc(title)}" />',
            f'<meta name="description" content="{_esc(description)}" />',
            f'<meta property="og:description" content="{_esc(description)}" />',
        ]
    )
    # Search-result windows are session-shaped URLs; keep crawlers on the
    # canonical browse pages.
    return _document(
        title=title,
        theme=theme,
        extra_css=_CATALOG_CSS,
        body=body,
        head_extra="".join(head_parts),
        meta_robots="noindex" if searched else "",
        app_styles=presentation_stylesheets(config, catalog),
    )


# ------------------------------------------------------------------ item shell

def build_item_shell(
    *,
    config: PublicContentAliasConfig,
    catalog: PublicContentCatalogConfig,
    entries: List[PublicContentIndexEntry],
    active_slug: str,
    counts: Dict[str, int],
    catalog_url: str,
    catalog_url_for: Callable[[PublicContentCatalogConfig], str],
    item_url_for: Callable[[str], str],
) -> Tuple[str, str, str]:
    """The chrome + rail shell for an item page under a catalog.

    Returns ``(head_extra, body_prefix, body_suffix)`` for the item renderer:
    the prefix opens chrome header, layout grid, rail card, crumb row and the
    article card; the suffix closes them and appends the rail script.
    """
    theme = catalog_theme(catalog, config)
    header = render_chrome_header(config.chrome, active_href=catalog_url)

    rail_cards = []
    for entry in entries[:_RAIL_ITEM_CAP]:
        now = " kdcpub-now" if entry.slug == active_slug else ""
        summary = f'<span class="s">{_esc(entry.summary)}</span>' if entry.summary else ""
        rail_cards.append(
            f'<a class="kdcpub-rail-item{now}" href="{_esc(item_url_for(entry.slug))}">'
            f"{_card_top(entry)}"
            f'<span class="t">{_esc(entry.title or entry.slug)}</span>'
            f"{summary}"
            f"{_terms_html(entry, limit=4)}"
            "</a>"
        )

    folds = _fold_pills(
        config,
        active_prefix=catalog.prefix,
        catalog_url_for=catalog_url_for,
        counts=counts,
        css_class="kdcpub-rail-fold",
    )
    folds_html = f'<div class="kdcpub-rail-folds">{folds}</div>' if folds else ""

    placeholder = catalog.search_placeholder or "Search articles…"
    rail = (
        '<aside class="kdcpub-rail"><div class="kdcpub-rail-card">'
        '<div class="kdcpub-rail-head">'
        f'<div class="kdcpub-rail-title">{_esc(catalog.title or catalog.prefix)} '
        f"<span>· {counts.get(catalog.prefix, len(entries))}</span></div>"
        '<button class="kdcpub-rail-btn" title="Hide list">«</button>'
        "</div>"
        f'<form class="kdcpub-rail-search" method="get" action="{_esc(catalog_url)}">'
        f'<input type="search" name="q" placeholder="{_esc(placeholder)}">'
        "<button type=\"submit\">Go</button></form>"
        f"{folds_html}"
        f'<nav class="kdcpub-rail-list">{"".join(rail_cards)}</nav>'
        '<div class="kdcpub-rail-pager">'
        '<button class="kdcpub-rail-pg" data-dir="prev">‹</button><span></span>'
        '<button class="kdcpub-rail-pg" data-dir="next">›</button>'
        "</div></div></aside>"
    )

    crumb = (
        '<div class="kdcpub-crumb">'
        f'<a class="kdcpub-chipbtn" href="{_esc(catalog_url)}">← {_esc(catalog.title or catalog.prefix)}</a>'
        '<button class="kdcpub-chipbtn" id="kdcpub-show-rail">☰ Articles</button>'
        "</div>"
    )

    head_extra = (
        f"<style>{_BASE_CSS}{_RAIL_CSS}</style>{_theme_css(theme)}"
        f"{presentation_stylesheets(config, catalog)}"
    )
    body_prefix = (
        f"{header}\n"
        '<div class="kdcpub-layout">\n'
        f"{rail}\n"
        '<main class="kdcpub-main"><div class="kdcpub-main-inner">\n'
        f"{crumb}\n"
        '<div class="kdcpub-article-card">\n'
    )
    body_suffix = (
        "\n</div>\n</div></main>\n</div>\n"
        + _RAIL_JS.replace("__RAIL_PAGE_SIZE__", str(_RAIL_PAGE_SIZE))
    )
    return head_extra, body_prefix, body_suffix
