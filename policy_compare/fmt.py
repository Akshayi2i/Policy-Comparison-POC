"""Formatting helpers shared by the engine (money, percent, dates, durations)."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

MINUS = "−"


def money(amount: Optional[float], cents: bool = False) -> str:
    if amount is None:
        return "—"
    sign = MINUS if amount < 0 else ""
    a = abs(amount)
    if cents or (a != int(a)):
        return f"{sign}${a:,.2f}"
    return f"{sign}${a:,.0f}"


def money_compact(amount: Optional[float]) -> str:
    """$2M, $1.8M, $400K, $5,000 (thousands below 100K stay in full)."""
    if amount is None:
        return "—"
    a = abs(amount)
    sign = MINUS if amount < 0 else ""
    if a >= 1_000_000:
        v = a / 1_000_000
        return f"{sign}${v:.1f}".rstrip("0").rstrip(".") + "M"
    if a >= 100_000:
        v = a / 1_000
        return f"{sign}${v:.0f}K"
    return money(amount)


def pct(delta: float, base: float, places: int = 1) -> str:
    """Signed percent with a true minus sign: −50%, +11.1%, −11.1%."""
    if not base:
        return ""
    p = delta / base * 100
    s = f"{abs(p):.{places}f}".rstrip("0").rstrip(".")
    return f"{MINUS if p < 0 else '+'}{s}%"


def signed_money(delta: float) -> str:
    return (MINUS if delta < 0 else "+") + money(abs(delta))


def mdy(d: Optional[date]) -> str:
    return d.strftime("%m/%d/%Y") if d else "—"


def long_date(d: date) -> str:
    return d.strftime("%B %d, %Y")


def duration(td: timedelta) -> str:
    """'11 h 59 min', '2 days 3 h', '45 min'."""
    secs = abs(int(td.total_seconds()))
    days, rem = divmod(secs, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    parts = []
    if days:
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours:
        parts.append(f"{hours} h")
    if minutes and not days:
        parts.append(f"{minutes} min")
    return " ".join(parts) or "0 min"


def years_between(a: date, b: date) -> int:
    return b.year - a.year - ((b.month, b.day) < (a.month, a.day))


NUMBER_WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]


def number_word(n: int, capital: bool = False) -> str:
    w = NUMBER_WORDS[n] if 0 <= n < len(NUMBER_WORDS) else str(n)
    return w[:1].upper() + w[1:] if capital else w


def plural(n: int, word: str, plural_word: Optional[str] = None) -> str:
    return f"{n} {word if n == 1 else (plural_word or word + 's')}"
