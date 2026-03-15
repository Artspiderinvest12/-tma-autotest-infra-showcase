from datetime import datetime

HR = "─" * 78
SUB_HR = "┄" * 78
HR_BOLD = "═" * 78
SECTION = "•" * 3

def fmt_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M:%S")