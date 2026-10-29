"""Выбор и построение графиков. Спецификация графика — простой dict, её можно хранить в дашборде."""
import re

import pandas as pd

ALLOWED = {"bar", "line", "area", "pie", "histogram", "scatter", "box"}
TIMEISH_RE = re.compile(r"^\d{4}-\d{2}")


def _is_text(s: pd.Series) -> bool:
    """Строки: object в pandas 2, str в pandas 3."""
    return s.dtype == object or pd.api.types.is_string_dtype(s)


def _is_time(s: pd.Series) -> bool:
    if pd.api.types.is_datetime64_any_dtype(s):
        return True
    if _is_text(s) and len(s):
        return s.dropna().astype(str).head(20).map(lambda v: bool(TIMEISH_RE.match(v))).all()
    return False


def _split(df: pd.DataFrame):
    time_cols = [c for c in df.columns if _is_time(df[c])]
    num = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])
           and not pd.api.types.is_bool_dtype(df[c]) and c not in time_cols]
    cat = [c for c in df.columns if c not in time_cols and c not in num]
    return time_cols, num, cat


def is_time(s: pd.Series) -> bool:
    return _is_time(s)


def guess_axes(df: pd.DataFrame) -> dict | None:
    """Оси для случая, когда авто-выбор графика ничего не нашёл, а пользователь назвал тип графика."""
    if df is None or df.empty:
        return None
    t, n, c = _split(df)
    xs = t or c or n
    if not xs:
        return None
    x = xs[0]
    ys = [col for col in n if col != x]
    return {"type": "bar", "x": x, "y": ys[0] if ys else None, "color": None, "title": None}


def auto_spec(df: pd.DataFrame) -> dict | None:
    if df is None or len(df) < 2 or len(df.columns) < 1:
        return None
    t, n, c = _split(df)
    if t and n:
        color = c[0] if c and df[c[0]].nunique() <= 8 else None
        return {"type": "line", "x": t[0], "y": n[0], "color": color, "title": None}
    if c and n:
        return {"type": "bar", "x": c[0], "y": n[0], "color": None, "title": None}
    if len(n) >= 2:
        return {"type": "scatter", "x": n[0], "y": n[1], "color": None, "title": None}
    if len(n) == 1 and len(df) >= 10:
        return {"type": "histogram", "x": n[0], "y": None, "color": None, "title": None}
    return None


def validate_spec(spec: dict | None, df: pd.DataFrame) -> dict | None:
    if not isinstance(spec, dict) or df is None or df.empty:
        return None
    typ = str(spec.get("type", "")).lower()
    if typ not in ALLOWED:
        return None
    cols = set(df.columns)
    pick = lambda k: spec.get(k) if spec.get(k) in cols else None  # noqa: E731
    x, y, color = pick("x"), pick("y"), pick("color")
    if x is None:
        return None
    if typ != "histogram" and y is None:
        return None
    if typ in ("bar", "line", "area", "pie", "box", "scatter") and y is not None \
            and typ != "box" and not pd.api.types.is_numeric_dtype(df[y]):
        return None
    return {"type": typ, "x": x, "y": y, "color": color, "title": spec.get("title")}


def make_figure(df: pd.DataFrame, spec: dict):
    import plotly.express as px

    typ, x, y, color = spec["type"], spec["x"], spec.get("y"), spec.get("color")
    title = spec.get("title")
    d = df
    if typ in ("bar", "pie") and not _is_time(df[x]) and y:
        d = df.sort_values(y, ascending=False).head(30)
    if typ == "bar":
        fig = px.bar(d, x=x, y=y, color=color, title=title)
    elif typ == "line":
        fig = px.line(d.sort_values(x), x=x, y=y, color=color, title=title, markers=True)
    elif typ == "area":
        fig = px.area(d.sort_values(x), x=x, y=y, color=color, title=title)
    elif typ == "pie":
        fig = px.pie(d.head(12), names=x, values=y, title=title)
    elif typ == "histogram":
        fig = px.histogram(d, x=x, color=color, title=title)
    elif typ == "scatter":
        fig = px.scatter(d, x=x, y=y, color=color, title=title)
    else:
        fig = px.box(d, x=color or x, y=y, title=title)
    fig.update_layout(template="plotly_white", height=420, margin=dict(l=10, r=10, t=50, b=10))
    return fig
