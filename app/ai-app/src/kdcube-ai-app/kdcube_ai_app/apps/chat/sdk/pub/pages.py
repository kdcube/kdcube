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

Design language (approved 2026-07-10): white cards on a per-catalog background
tint; selection states are transparent tints of the catalog accent (never solid
fills); modest radii (6–14px); one shared card anatomy (date, kicker badge,
title, summary, tag chips) on catalog cards and rail cards. All classes are
namespaced ``kdcpub-`` and the styles are self-contained, so authored article
CSS and the chrome cannot bleed into each other.
"""
from __future__ import annotations

import html
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import quote_plus

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

_DEFAULT_THEME = {
    "bg": "#F6FAFA",
    "border": "#D8ECEB",
    "accent": "#01BEB2",
    "accent_dark": "#009C92",
    "accent_rgb": "1,190,178",
}


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


def catalog_theme(catalog: Optional[PublicContentCatalogConfig]) -> Dict[str, str]:
    """Resolved color world for a catalog: configured accent/background/border
    with derived values (rgb triple for tints, darkened accent) and teal-family
    defaults when nothing is configured."""
    theme = dict(_DEFAULT_THEME)
    if catalog is None:
        return theme
    rgb = _hex_to_rgb(catalog.accent)
    if rgb is not None:
        theme["accent"] = catalog.accent.strip()
        theme["accent_rgb"] = f"{rgb[0]},{rgb[1]},{rgb[2]}"
        theme["accent_dark"] = _darken(rgb)
    if catalog.background.strip():
        theme["bg"] = catalog.background.strip()
    if catalog.border.strip():
        theme["border"] = catalog.border.strip()
    return theme


def _theme_css(theme: Dict[str, str]) -> str:
    return (
        "<style>:root{"
        f"--kdcpub-bg:{theme['bg']};--kdcpub-border:{theme['border']};"
        f"--kdcpub-accent:{theme['accent']};--kdcpub-accent-dark:{theme['accent_dark']};"
        f"--kdcpub-accent-rgb:{theme['accent_rgb']};"
        "}</style>"
    )


# ------------------------------------------------------------------ styles

_BASE_CSS = """
  :root{
    --kdcpub-bg:#F6FAFA; --kdcpub-surface:#FFFFFF; --kdcpub-ink:#0D1E2C;
    --kdcpub-dim:#3A5672; --kdcpub-muted:#7A99B0;
    --kdcpub-accent:#01BEB2; --kdcpub-accent-dark:#009C92;
    --kdcpub-accent-rgb:1,190,178; --kdcpub-border:#D8ECEB;
  }
  .kdcpub-header,.kdcpub-header *,.kdcpub-wrap,.kdcpub-wrap *,.kdcpub-rail,.kdcpub-rail *,
  .kdcpub-crumb,.kdcpub-crumb *,.kdcpub-foot,.kdcpub-foot *{box-sizing:border-box}
  body.kdcpub-body{margin:0;background:var(--kdcpub-bg);font-family:Inter,system-ui,-apple-system,'Segoe UI',sans-serif;color:var(--kdcpub-ink)}
  .kdcpub-header{position:sticky;top:0;z-index:50;background:rgba(255,255,255,.92);backdrop-filter:blur(8px);border-bottom:1px solid var(--kdcpub-border);font-family:Inter,system-ui,-apple-system,'Segoe UI',sans-serif}
  .kdcpub-header-in{max-width:1180px;margin:0 auto;display:flex;align-items:center;gap:22px;padding:12px 24px}
  .kdcpub-brand{display:flex;align-items:center;gap:8px;text-decoration:none;margin-right:auto;color:var(--kdcpub-ink);font-weight:700;font-size:15px}
  .kdcpub-brand img{height:26px;display:block}
  .kdcpub-nav{display:flex;gap:2px;flex-wrap:wrap}
  .kdcpub-nav a{color:var(--kdcpub-dim);text-decoration:none;font-size:14px;font-weight:500;padding:6px 10px;border-radius:8px}
  .kdcpub-nav a:hover{color:var(--kdcpub-accent-dark);background:rgba(var(--kdcpub-accent-rgb),.07)}
  .kdcpub-nav a.kdcpub-active{color:var(--kdcpub-accent-dark);background:rgba(var(--kdcpub-accent-rgb),.10)}
  .kdcpub-chipbtn{display:inline-flex;align-items:center;gap:6px;padding:7px 12px;border-radius:8px;border:1px solid var(--kdcpub-border);background:var(--kdcpub-surface);color:var(--kdcpub-dim);font-family:inherit;font-size:13px;font-weight:600;text-decoration:none;cursor:pointer;line-height:1.2}
  .kdcpub-chipbtn:hover{border-color:rgba(var(--kdcpub-accent-rgb),.5);color:var(--kdcpub-accent-dark)}
  .kdcpub-date{color:var(--kdcpub-muted);font-size:13px;font-variant-numeric:tabular-nums}
  .kdcpub-rubric{font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;padding:3px 8px;border-radius:6px;background:rgba(var(--kdcpub-accent-rgb),.10);color:var(--kdcpub-accent-dark)}
  .kdcpub-terms{display:flex;flex-wrap:wrap;gap:6px}
  .kdcpub-term{font-size:12px;color:var(--kdcpub-dim);border:1px solid var(--kdcpub-border);border-radius:6px;padding:3px 9px;background:var(--kdcpub-bg)}
  .kdcpub-card-top{display:flex;align-items:center;gap:10px;margin-bottom:6px}
  .kdcpub-foot{max-width:1180px;margin:0 auto;padding:18px 24px 40px;color:var(--kdcpub-muted);font-size:12.5px;border-top:1px solid var(--kdcpub-border);font-family:Inter,system-ui,sans-serif}
  .kdcpub-foot a{color:var(--kdcpub-accent-dark)}
"""

_CATALOG_CSS = """
  .kdcpub-wrap{max-width:1080px;margin:0 auto;padding:26px 24px 48px;font-family:Inter,system-ui,-apple-system,'Segoe UI',sans-serif}
  .kdcpub-eyebrow{font-size:11px;font-weight:700;letter-spacing:.14em;color:var(--kdcpub-accent-dark);text-transform:uppercase}
  .kdcpub-hero h1{font-size:28px;line-height:1.15;margin:6px 0 6px;letter-spacing:-.01em}
  .kdcpub-sub{color:var(--kdcpub-dim);font-size:14px;max-width:640px;margin:0 0 4px}
  .kdcpub-meta{color:var(--kdcpub-muted);font-size:12.5px}
  .kdcpub-meta a{color:var(--kdcpub-accent-dark)}
  .kdcpub-folds{display:flex;gap:8px;margin:14px 0 10px}
  .kdcpub-fold{padding:5px 12px;border-radius:8px;border:1px solid var(--kdcpub-border);background:var(--kdcpub-surface);color:var(--kdcpub-dim);font-size:13px;font-weight:600;text-decoration:none}
  .kdcpub-fold:hover{border-color:rgba(var(--kdcpub-accent-rgb),.5);color:var(--kdcpub-accent-dark)}
  .kdcpub-fold.kdcpub-on{background:rgba(var(--kdcpub-accent-rgb),.10);border-color:rgba(var(--kdcpub-accent-rgb),.45);color:var(--kdcpub-accent-dark)}
  .kdcpub-search{display:flex;gap:8px;margin:0 0 6px}
  .kdcpub-search input{flex:1;padding:8px 13px;border-radius:8px;border:1px solid var(--kdcpub-border);background:var(--kdcpub-surface);font:inherit;font-size:14px;color:var(--kdcpub-ink);outline:none}
  .kdcpub-search input:focus{border-color:rgba(var(--kdcpub-accent-rgb),.6)}
  .kdcpub-search button{padding:8px 18px;border-radius:8px;border:0;background:var(--kdcpub-accent);color:#fff;font:inherit;font-size:14px;font-weight:600;cursor:pointer}
  .kdcpub-search button:hover{background:var(--kdcpub-accent-dark)}
  .kdcpub-search-hint{color:var(--kdcpub-muted);font-size:12px;margin:0 0 14px}
  .kdcpub-search-hint a{color:var(--kdcpub-accent-dark)}
  .kdcpub-list{display:flex;flex-direction:column;gap:8px}
  .kdcpub-card{background:var(--kdcpub-surface);border:1px solid var(--kdcpub-border);border-radius:11px;padding:11px 15px;transition:border-color .15s, box-shadow .15s}
  .kdcpub-card:hover{border-color:rgba(var(--kdcpub-accent-rgb),.5);box-shadow:0 4px 14px rgba(var(--kdcpub-accent-rgb),.07)}
  .kdcpub-card .kdcpub-card-top{margin-bottom:2px}
  .kdcpub-card .kdcpub-date{font-size:12px}
  .kdcpub-card h2{margin:0 0 3px;font-size:15.5px;line-height:1.3}
  .kdcpub-card h2 a{color:var(--kdcpub-ink);text-decoration:none}
  .kdcpub-card h2 a:hover{color:var(--kdcpub-accent-dark)}
  .kdcpub-card p{margin:0 0 7px;color:var(--kdcpub-dim);font-size:13px;line-height:1.5;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
  .kdcpub-card .kdcpub-term{font-size:11px;padding:2px 8px}
  .kdcpub-empty{background:var(--kdcpub-surface);border:1px solid var(--kdcpub-border);border-radius:11px;padding:18px;color:var(--kdcpub-dim);font-size:13.5px}
  .kdcpub-pager{display:flex;align-items:center;justify-content:space-between;margin-top:16px}
  .kdcpub-pager span{color:var(--kdcpub-muted);font-size:12.5px}
  .kdcpub-pgbtn{padding:6px 14px;border-radius:8px;border:1px solid var(--kdcpub-border);background:var(--kdcpub-surface);color:var(--kdcpub-dim);font-size:13px;font-weight:600;text-decoration:none}
  .kdcpub-pgbtn:hover{border-color:rgba(var(--kdcpub-accent-rgb),.5);color:var(--kdcpub-accent-dark)}
  .kdcpub-pgbtn.kdcpub-off{opacity:.4;pointer-events:none}
"""

_RAIL_CSS = """
  .kdcpub-layout{display:grid;grid-template-columns:324px minmax(0,1fr);align-items:start;max-width:1420px;margin:0 auto;font-family:Inter,system-ui,-apple-system,'Segoe UI',sans-serif}
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
    active_attr = ' class="kdcpub-active"'
    nav_links = "".join(
        f'<a href="{_esc(link.href)}"'
        f"{active_attr if active_href and link.href == active_href else ''}"
        f">{_esc(link.label)}</a>"
        for link in chrome.links
    )
    nav = f'<nav class="kdcpub-nav">{nav_links}</nav>' if nav_links else ""
    return (
        '<header class="kdcpub-header"><div class="kdcpub-header-in">'
        f"{brand}{nav}"
        "</div></header>"
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


def _catalog_card(entry: PublicContentIndexEntry, *, item_url: str) -> str:
    summary = f"<p>{_esc(entry.summary)}</p>" if entry.summary else ""
    return (
        '<article class="kdcpub-card">'
        f"{_card_top(entry)}"
        f'<h2><a href="{_esc(item_url)}">{_esc(entry.title or entry.slug)}</a></h2>'
        f"{summary}"
        f"{_terms_html(entry)}"
        "</article>"
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
) -> str:
    robots = f'<meta name="robots" content="{_esc(meta_robots)}" />' if meta_robots else ""
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n'
        '<meta charset="utf-8" />\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1" />\n'
        f"<title>{_esc(title)}</title>\n"
        f"{robots}"
        f"<style>{_BASE_CSS}{extra_css}</style>{_theme_css(theme)}\n"
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
) -> str:
    """The server-rendered catalog page.

    ``entries`` is the already-windowed page slice when browsing, or the
    ranked result list when ``searched``; ``total_in_catalog`` is the full
    catalog size (browse) / result count (search).
    """
    theme = catalog_theme(catalog)
    page_size = catalog.page_size
    site = config.og_defaults.site_name

    header = render_chrome_header(config.chrome, active_href=catalog_url)

    cards = [
        _catalog_card(entry, item_url=item_url_for(entry.slug)) for entry in entries
    ]
    if not cards:
        empty_text = (
            f"No articles match “{_esc(query)}”." if searched else "No articles yet."
        )
        clear = (
            f' <a href="{_esc(catalog_url)}" style="color:var(--kdcpub-accent-dark)">Show all</a>'
            if searched
            else ""
        )
        cards = [f'<div class="kdcpub-empty">{empty_text}{clear}</div>']

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
        hint = (
            f'{total_in_catalog} results for “{_esc(query)}” · '
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
    pager = (
        '<div class="kdcpub-pager">'
        f'<a class="kdcpub-pgbtn{prev_cls}" href="{_esc(_page_url(max(0, offset - page_size)))}">← Newer</a>'
        f"<span>{shown_from}–{shown_to} of {total_in_catalog}</span>"
        f'<a class="kdcpub-pgbtn{next_cls}" href="{_esc(_page_url(offset + page_size))}">Older →</a>'
        "</div>"
    )

    eyebrow = (
        f'<div class="kdcpub-eyebrow">{_esc(catalog.eyebrow)}</div>' if catalog.eyebrow else ""
    )
    subtitle = f'<p class="kdcpub-sub">{_esc(catalog.subtitle)}</p>' if catalog.subtitle else ""

    body = (
        f"{header}\n"
        '<main class="kdcpub-wrap">'
        f'<div class="kdcpub-hero">{eyebrow}<h1>{_esc(catalog.title or catalog.prefix)}</h1>'
        f"{subtitle}{meta_line}</div>"
        f"{folds_html}{search_form}{search_hint}"
        f'<div class="kdcpub-list">{"".join(cards)}</div>'
        f"{pager}"
        "</main>\n"
        + (
            f'<footer class="kdcpub-foot">© {_esc(site)}</footer>'
            if site
            else ""
        )
    )
    title = f"{catalog.title or catalog.prefix}" + (f" · {site}" if site else "")
    # Search-result windows are session-shaped URLs; keep crawlers on the
    # canonical browse pages.
    return _document(
        title=title,
        theme=theme,
        extra_css=_CATALOG_CSS,
        body=body,
        meta_robots="noindex" if searched else "",
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
    theme = catalog_theme(catalog)
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

    head_extra = f"<style>{_BASE_CSS}{_RAIL_CSS}</style>{_theme_css(theme)}"
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
