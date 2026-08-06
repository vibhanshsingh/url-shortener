"""
NOT production-grade. Real systems use a proper library (e.g.
ua-parser) with a maintained database of user agent signatures — user
agent strings are messy and this naive substring-matching approach will
misclassify plenty of real-world traffic. It's included here because
the click_events schema has device_type/browser columns to populate,
and a honest, clearly-labeled naive parser teaches the concept without
pulling in a large external dependency for a learning project. Flagging
this limitation explicitly is exactly the kind of thing worth calling
out in a design review rather than silently shipping.
"""


def parse_device_type(user_agent: str | None) -> str | None:
    if not user_agent:
        return None
    ua = user_agent.lower()
    if "mobile" in ua or "android" in ua or "iphone" in ua:
        return "mobile"
    if "tablet" in ua or "ipad" in ua:
        return "tablet"
    return "desktop"


def parse_browser(user_agent: str | None) -> str | None:
    if not user_agent:
        return None
    ua = user_agent.lower()
    # Order matters: Edge and Chrome both contain "safari" in their UA
    # strings for legacy compatibility reasons, so more specific
    # matches must be checked first.
    if "edg/" in ua:
        return "Edge"
    if "chrome" in ua:
        return "Chrome"
    if "firefox" in ua:
        return "Firefox"
    if "safari" in ua:
        return "Safari"
    return "Other"
