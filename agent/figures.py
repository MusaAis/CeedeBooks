"""Figures written by code (rule 18). Every number a narrative shows comes from render_figures(); model text may only
name a figure with a placeholder such as {amount}, and fill() refuses text that carries a digit of its own."""
import re
from typing import Optional

_DIGIT = re.compile(r"\d")
_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")
_BRACES = re.compile(r"[{}]")

WITHHELD = "The model's narrative was withheld because it contained figures not written by code."


def _plain(amount: float) -> str:
    return f"{amount:.6f}".rstrip("0").rstrip(".")


def render_figures(amount_usdc: Optional[float] = None, po_amount_usdc: Optional[float] = None) -> dict:
    """The only source of figures for model-written text, as {placeholder: text}. A figure that is not known is absent,
    so a placeholder for it is refused rather than filled with a guess."""
    figures = {}
    if amount_usdc is not None:
        figures["amount"] = _plain(amount_usdc) + " USDC"
    if po_amount_usdc is not None:
        figures["po_amount"] = _plain(po_amount_usdc) + " USDC"
    return figures


def placeholder_help(figures: dict) -> str:
    """One line for the prompt naming the placeholders the model may use."""
    names = ", ".join("{" + k + "}" for k in figures) or "none"
    return f"Name a figure only with one of these placeholders (write no digits yourself): {names}."


def fill(text: Optional[str], figures: dict) -> Optional[str]:
    """Model text with its placeholders replaced by code-written figures, or None if the text has a digit of its own, a
    placeholder that is not in `figures`, or stray braces. None means: use the code-written reason instead."""
    if not text or _DIGIT.search(text):
        return None
    names = _PLACEHOLDER.findall(text)
    if any(n not in figures for n in names):
        return None
    if _BRACES.search(_PLACEHOLDER.sub("", text)):
        return None
    out = _PLACEHOLDER.sub(lambda m: figures[m.group(1)], text)
    return " ".join(out.split())
