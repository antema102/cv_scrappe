from __future__ import annotations

from seleniumbase import SB


def maybe_solve_captcha(sb: SB) -> None:
    try:
        sb.solve_captcha()
    except Exception:
        pass
