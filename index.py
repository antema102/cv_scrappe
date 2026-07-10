from seleniumbase import SB

with SB(uc=True, test=True, locale="en") as sb:
    sb.activate_cdp_mode()
    sb.goto("https://www.emploi.ci")
    sb.sleep(10)
    sb.solve_captcha()
    sb.sleep(10)