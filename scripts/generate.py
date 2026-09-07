"""Google Calendar(비공개 .ics) -> README 임베드용 월간 달력 SVG 생성기.

사용:
    python scripts/generate.py                 # config.toml 사용
    python scripts/generate.py --demo          # 더미 데이터로 렌더 확인
"""

from __future__ import annotations

import argparse
import calendar as calmod
import os
import sys
import tomllib
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"

# ---------------------------------------------------------------- theme

THEMES = {
    "light": {
        "bg": "#ffffff", "panel": "#f6f8fa", "border": "#d0d7de",
        "text": "#1f2328", "muted": "#59636e", "today_bg": "#ddf4ff",
        "today_ring": "#0969da", "other": "#8c959f",
        "accent": "#0969da", "green": "#1a7f37", "purple": "#8250df",
        "orange": "#bc4c00", "chip_text": "#ffffff",
    },
    "dark": {
        "bg": "#0d1117", "panel": "#151b23", "border": "#3d444d",
        "text": "#f0f6fc", "muted": "#9198a1", "today_bg": "#121d2f",
        "today_ring": "#4493f8", "other": "#6e7681",
        "accent": "#4493f8", "green": "#3fb950", "purple": "#a371f7",
        "orange": "#db6d28", "chip_text": "#0d1117",
    },
}

LABELS = {
    "ko": {
        "dow_mon": ["월", "화", "수", "목", "금", "토", "일"],
        "dow_sun": ["일", "월", "화", "수", "목", "금", "토"],
        "title": "{y}년 {m}월",
        "updated": "갱신 {ts} KST",
    },
    "en": {
        "dow_mon": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
        "dow_sun": ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"],
        "title": "{m:02d} / {y}",
        "updated": "updated {ts} KST",
    },
}

FONT = ("-apple-system, BlinkMacSystemFont, 'Segoe UI', 'Malgun Gothic', "
        "'Apple SD Gothic Neo', 'Noto Sans KR', Helvetica, Arial, sans-serif")

# ---------------------------------------------------------------- model


@dataclass
class Ev:
    day: date
    label: str
    color: str
    start: time | None  # None이면 종일 일정


# ---------------------------------------------------------------- config


def load_config() -> dict:
    path = ROOT / "config.toml"
    if not path.exists():
        path = ROOT / "config.example.toml"
    with path.open("rb") as fh:
        return tomllib.load(fh)


# ---------------------------------------------------------------- fetch


def fetch_events(cfg: dict, first: date, last: date) -> list[Ev]:
    """설정된 모든 .ics 피드를 받아 [first, last] 구간 일정으로 펼친다."""
    import recurring_ical_events
    import requests
    from icalendar import Calendar

    tz = ZoneInfo(cfg["general"]["timezone"])
    fcfg = cfg.get("filter", {})
    public_tag = fcfg.get("public_tag", "")
    exclude = [k.lower() for k in fcfg.get("exclude_keywords", [])]
    include_all_day = fcfg.get("include_all_day", True)
    busy_label = cfg["general"].get("busy_label", "바쁨")

    out: list[Ev] = []
    for feed in cfg.get("calendars", []):
        url = os.environ.get(feed["url_env"], "").strip()
        if not url:
            print(f"[skip] {feed['name']}: ${feed['url_env']} 未설정", file=sys.stderr)
            continue

        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        # iCalendar는 RFC 5545상 UTF-8. 서버가 charset을 안 주면 requests가
        # latin-1로 추측해서 한글이 깨지므로 직접 디코드한다.
        cal = Calendar.from_ical(resp.content.decode("utf-8"))

        # RRULE 을 실제 발생 건으로 펼친다 (icalendar 단독으로는 안 됨)
        occurrences = recurring_ical_events.of(cal).between(first, last + timedelta(days=1))

        vis = feed.get("visibility", "busy")
        color = feed.get("color", "accent")

        for comp in occurrences:
            summary = str(comp.get("SUMMARY", "")).strip()
            if any(k in summary.lower() for k in exclude):
                continue

            dtstart = comp["DTSTART"].dt
            if isinstance(dtstart, datetime):
                local = dtstart.astimezone(tz)
                day, start = local.date(), local.time()
            else:
                if not include_all_day:
                    continue
                day, start = dtstart, None

            if not (first <= day <= last):
                continue

            # 공개 범위 결정
            if vis == "title":
                label = summary or busy_label
            elif vis == "tag" and public_tag and summary.startswith(public_tag):
                label = summary[len(public_tag):].strip() or busy_label
            else:
                label = busy_label

            out.append(Ev(day=day, label=label, color=color, start=start))

    out.sort(key=lambda e: (e.day, e.start is not None, e.start or time.min))
    return out


def demo_events(first: date, last: date) -> list[Ev]:
    today = date.today()
    base = today.replace(day=1)
    picks = [3, 7, 7, 12, 18, 21, 21, 21, 27]
    colors = ["accent", "green", "purple", "orange"]
    out = []
    for i, d in enumerate(picks):
        day = base + timedelta(days=d)
        if not (first <= day <= last):
            continue
        out.append(Ev(day=day,
                      label=["스터디", "바쁨", "PR 리뷰", "발표", "바쁨"][i % 5],
                      color=colors[i % 4],
                      start=None if i % 3 == 0 else time(hour=9 + i % 8)))
    return out


# ---------------------------------------------------------------- render

CELL_W, CELL_H = 134, 92
PAD_X, HEAD_H, DOW_H = 20, 58, 28


def render(cfg: dict, events: list[Ev], year: int, month: int, theme: str) -> str:
    c = THEMES[theme]
    g = cfg["general"]
    loc = LABELS.get(g.get("locale", "ko"), LABELS["ko"])
    week_start_mon = g.get("week_start", "mon") == "mon"
    max_chips = int(g.get("max_chips_per_day", 2))

    cal = calmod.Calendar(firstweekday=0 if week_start_mon else 6)
    weeks = cal.monthdatescalendar(year, month)

    width = PAD_X * 2 + CELL_W * 7
    height = HEAD_H + DOW_H + CELL_H * len(weeks) + 34

    by_day: dict[date, list[Ev]] = {}
    for e in events:
        by_day.setdefault(e.day, []).append(e)

    today = datetime.now(ZoneInfo(g["timezone"])).date()
    dow = loc["dow_mon"] if week_start_mon else loc["dow_sun"]

    p: list[str] = []
    p.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="{escape(FONT)}">'
    )
    p.append(f'<rect width="{width}" height="{height}" rx="12" fill="{c["bg"]}" '
             f'stroke="{c["border"]}"/>')

    # 헤더
    title = loc["title"].format(y=year, m=month)
    p.append(f'<text x="{PAD_X}" y="38" font-size="20" font-weight="700" '
             f'fill="{c["text"]}">{escape(title)}</text>')
    stamp = loc["updated"].format(
        ts=datetime.now(ZoneInfo(g["timezone"])).strftime("%m-%d %H:%M"))
    p.append(f'<text x="{width - PAD_X}" y="38" font-size="11" text-anchor="end" '
             f'fill="{c["muted"]}">{escape(stamp)}</text>')

    # 요일 헤더
    for i, name in enumerate(dow):
        x = PAD_X + CELL_W * i + CELL_W / 2
        p.append(f'<text x="{x:.0f}" y="{HEAD_H + 18}" font-size="12" font-weight="600" '
                 f'text-anchor="middle" fill="{c["muted"]}">{escape(name)}</text>')

    # 날짜 칸
    for r, week in enumerate(weeks):
        for i, day in enumerate(week):
            x = PAD_X + CELL_W * i
            y = HEAD_H + DOW_H + CELL_H * r
            in_month = day.month == month
            is_today = day == today

            fill = c["today_bg"] if is_today else c["panel"]
            stroke = c["today_ring"] if is_today else c["border"]
            op = "1" if in_month else "0.45"
            p.append(f'<g opacity="{op}">')
            p.append(f'<rect x="{x + 2}" y="{y + 2}" width="{CELL_W - 4}" '
                     f'height="{CELL_H - 4}" rx="8" fill="{fill}" stroke="{stroke}"/>')

            num_fill = c["text"] if in_month else c["other"]
            weight = "700" if is_today else "500"
            p.append(f'<text x="{x + 12}" y="{y + 22}" font-size="13" '
                     f'font-weight="{weight}" fill="{num_fill}">{day.day}</text>')

            chips = by_day.get(day, [])
            for k, ev in enumerate(chips[:max_chips]):
                cy = y + 32 + k * 22
                p.append(f'<rect x="{x + 9}" y="{cy}" width="{CELL_W - 22}" height="18" '
                         f'rx="5" fill="{c[ev.color]}" opacity="0.92"/>')
                text = ev.label
                if ev.start is not None:
                    text = f"{ev.start.strftime('%H:%M')} {text}"
                p.append(f'<text x="{x + 15}" y="{cy + 13}" font-size="10.5" '
                         f'fill="{c["chip_text"]}" clip-path="inset(0 0 0 0)">'
                         f'{escape(trunc(text, CELL_W - 32))}</text>')

            if len(chips) > max_chips:
                p.append(f'<text x="{x + 12}" y="{y + CELL_H - 12}" font-size="10" '
                         f'fill="{c["muted"]}">+{len(chips) - max_chips}</text>')
            p.append("</g>")

    p.append("</svg>")
    return "\n".join(p)


def trunc(text: str, px: float) -> str:
    """한글 ~10.2px, 영문 ~5.6px 로 잡은 대략적 폭 기준 말줄임."""
    w, out = 0.0, []
    for ch in text:
        w += 10.2 if ord(ch) > 0x1100 else 5.6
        if w > px:
            return "".join(out).rstrip() + "…"
        out.append(ch)
    return text


# ---------------------------------------------------------------- main


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="더미 데이터로 렌더")
    ap.add_argument("--month", help="YYYY-MM (기본: 이번 달)")
    args = ap.parse_args()

    cfg = load_config()
    tz = ZoneInfo(cfg["general"]["timezone"])
    now = datetime.now(tz)
    if args.month:
        year, month = (int(v) for v in args.month.split("-"))
    else:
        year, month = now.year, now.month

    first = date(year, month, 1)
    last = date(year, month, calmod.monthrange(year, month)[1])
    # 앞뒤 걸친 주까지 포함해서 조회
    span_first = first - timedelta(days=7)
    span_last = last + timedelta(days=7)

    events = demo_events(span_first, span_last) if args.demo \
        else fetch_events(cfg, span_first, span_last)

    ASSETS.mkdir(exist_ok=True)
    for theme in ("light", "dark"):
        svg = render(cfg, events, year, month, theme)
        (ASSETS / f"calendar-{theme}.svg").write_text(svg, encoding="utf-8")

    print(f"generated {year}-{month:02d}: {len(events)} events -> assets/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
