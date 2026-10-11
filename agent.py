"""Data Analyst Agent: course completion and student progress.

Flow: fixed read-only SQL -> cleaning in pandas -> one predefined analysis
(chosen by Gemini from a fixed list) -> Gemini writes the answer from the numbers.
Gemini never sees the database and never writes SQL.
"""

import json
import os
import re

import pandas as pd
from dotenv import load_dotenv
from google import genai
from google.genai import types
from sqlalchemy import create_engine, text

load_dotenv()

MIN_ENROLLMENTS = 30
LEVEL_ORDER = ["Beginner", "Intermediate", "Advanced", "unlabeled"]

# C00169 has content metrics exactly doubled (profile P3); rows without
# total_minutes are the broken last batch (P2). Both are excluded from duration analysis.
DURATION_EXCLUDED_IDS = {"C00169"}

COMPLETED_DEFINITION = (
    "An enrollment counts as completed when the student earned a certificate "
    "(is_certified = 1, identical to funnel_state = 'certified'). "
    "Completion rate = completed enrollments / all enrollments."
)

# The only query the app runs. enrollments -> dim_course by course_id
# (joining by course_url would duplicate rows: dim_course is course-in-specialization).
LOAD_SQL = """
SELECT e.progress_pct,
       e.is_certified,
       d.course_id,
       d.course_url,
       d.course,
       d.level,
       d.domain,
       d.n_weeks,
       d.total_minutes
FROM enrollments e
JOIN dim_course d ON d.course_id = e.course_id
"""

# R4: language markers at the end of the course name (from cleaning_rules.md).
LANGUAGE_MARKERS = [
    ("ja", r"日本語版$"),
    ("es", r"(en\s+)?Espa(ñ|n)ol$"),
    ("fr", r"(en\s+)?Fran(ç|c)ais$"),
    ("pt-BR", r"(em\s+)?Portugu(ê|e)s(\s+Brasileiro)?$"),
    ("ko", r"한국어(판)?$"),
    ("id", r"dalam\s+bahasa\s+Indonesia$"),
    ("it", r"(in\s+)?italiano$"),
    ("zh", r"(简体)?中文(版)?$"),
    ("de", r"(auf\s+)?Deutsch$"),
    ("ru", r"на\s+русском$"),
]


# ---------- data ----------

def _engine():
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set in .env")
    # Use the psycopg 3 driver regardless of how the URL scheme is written.
    url = re.sub(r"^postgres(ql)?(\+\w+)?://", "postgresql+psycopg://", url)
    # Every transaction is read-only on the server side: even a mistaken
    # write statement would be rejected by PostgreSQL.
    return create_engine(url, connect_args={"options": "-c default_transaction_read_only=on"})


def load_raw() -> pd.DataFrame:
    engine = _engine()
    try:
        with engine.connect() as conn:
            return pd.read_sql(text(LOAD_SQL), conn)
    finally:
        engine.dispose()


def _language(name: str) -> str:
    for code, pattern in LANGUAGE_MARKERS:
        if re.search(pattern, name, flags=re.IGNORECASE):
            return code
    return "en"


def clean(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.copy()
    # R3.1: strip leading/trailing whitespace (str.strip also removes the tabs in C02779).
    df["course"] = df["course"].str.strip()
    # R1: course key = part of course_url between '/learn/' and '?'.
    df["course_slug"] = df["course_url"].str.split("/learn/").str[1].str.split("?").str[0]
    # R2: 'Unknown' is a masked missing value; show it as 'unlabeled', never impute.
    for col in ["level", "domain"]:
        df[col] = df[col].replace("Unknown", pd.NA).fillna("unlabeled")
    # R4: language from the end of the course name (runs after R3.1, as required).
    df["language"] = df["course"].map(_language)

    df["completed"] = df["is_certified"] == 1
    # Values 101-104 only occur in non-certified rows that never reached the last week: noise.
    df["progress"] = df["progress_pct"].astype(float).clip(upper=100)
    df["total_minutes"] = df["total_minutes"].astype(float)
    df["duration_ok"] = df["total_minutes"].notna() & ~df["course_id"].isin(DURATION_EXCLUDED_IDS)
    return df


def load_data() -> pd.DataFrame:
    return clean(load_raw())


# ---------- analyses ----------

def kpis(df: pd.DataFrame) -> dict:
    return {
        "total_enrollments": int(len(df)),
        "completed_enrollments": int(df["completed"].sum()),
        "completion_rate_pct": round(100 * float(df["completed"].mean()), 2),
        "avg_progress_pct": round(float(df["progress"].mean()), 1),
    }


def completion_by_level(df: pd.DataFrame) -> pd.DataFrame:
    out = (
        df.groupby("level")
        .agg(enrollments=("completed", "size"), completed=("completed", "sum"),
             avg_progress_pct=("progress", "mean"))
        .reindex(LEVEL_ORDER)
        .dropna(subset=["enrollments"])
        .reset_index()
    )
    out["completion_rate_pct"] = (100 * out["completed"] / out["enrollments"]).round(2)
    out["avg_progress_pct"] = out["avg_progress_pct"].round(1)
    return out.astype({"enrollments": int, "completed": int})


def _by_course(df: pd.DataFrame) -> pd.DataFrame:
    """One row per real course (course_slug), only courses with enough enrollments."""
    out = df.groupby("course_slug").agg(
        course=("course", "first"),
        language=("language", "first"),
        enrollments=("completed", "size"),
        completed=("completed", "sum"),
        total_minutes=("total_minutes", "first"),
    ).reset_index()
    out = out[out["enrollments"] >= MIN_ENROLLMENTS].copy()
    out["completion_rate_pct"] = (100 * out["completed"] / out["enrollments"]).round(2)
    return out


def rank_courses(df: pd.DataFrame, order: str = "lowest", n: int = 10) -> pd.DataFrame:
    """Courses with the lowest or highest completion rate (>= MIN_ENROLLMENTS)."""
    courses = _by_course(df)
    # Ties (e.g. many courses at 0%) are broken by enrollments, largest first,
    # in both directions: a rate based on more students is more reliable.
    courses = courses.sort_values(["completion_rate_pct", "enrollments"],
                                  ascending=[order == "lowest", False])
    cols = ["course_slug", "course", "language", "enrollments", "completed", "completion_rate_pct"]
    return courses[cols].head(n).reset_index(drop=True)


def zero_completion_summary(df: pd.DataFrame) -> dict:
    courses = _by_course(df)
    return {
        "courses_with_30plus_enrollments": int(len(courses)),
        "of_them_with_0pct_completion": int((courses["completed"] == 0).sum()),
    }


def completion_by_duration(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    courses = _by_course(df[df["duration_ok"]])
    courses["duration_group"] = pd.qcut(
        courses["total_minutes"], 4,
        labels=["Q1 shortest", "Q2", "Q3", "Q4 longest"],
    )
    groups = courses.groupby("duration_group", observed=True).agg(
        courses=("course_slug", "size"),
        min_minutes=("total_minutes", "min"),
        max_minutes=("total_minutes", "max"),
        enrollments=("enrollments", "sum"),
        completed=("completed", "sum"),
    ).reset_index()
    groups["completion_rate_pct"] = (100 * groups["completed"] / groups["enrollments"]).round(2)
    groups[["min_minutes", "max_minutes"]] = groups[["min_minutes", "max_minutes"]].round(0).astype(int)

    stats = {
        "courses_analyzed": int(len(courses)),
        # Spearman = Pearson on ranks (avoids a scipy dependency).
        "spearman_minutes_vs_completion_rate": round(float(
            courses["total_minutes"].rank().corr(courses["completion_rate_pct"].rank())), 3),
        "excluded": "13 courses without content metrics and course_id C00169 (doubled metrics)",
    }
    return groups, stats


# ---------- agent ----------

ANALYSES = {
    "overview": "Overall KPIs: total enrollments, average progress, completion rate. "
                "Use for general questions or anything the other analyses do not cover.",
    "course_ranking": "Individual courses ranked by completion rate, lowest or highest "
                      "(courses with >= 30 enrollments).",
    "by_level": "Completion rate and average progress by course level "
                "(Beginner / Intermediate / Advanced / unlabeled).",
    "by_duration": "Whether longer courses (more content minutes) have lower completion.",
}


ORDERS = ("lowest", "highest")


def run_analysis(name: str, df: pd.DataFrame, order: str = "lowest") -> dict:
    """Return the computed results as {title: DataFrame}."""
    if name == "course_ranking":
        title = f"{order.capitalize()} completion rate (courses with >= {MIN_ENROLLMENTS} enrollments)"
        tables = {title: rank_courses(df, order)}
        if order == "lowest":
            tables["Context"] = pd.DataFrame([zero_completion_summary(df)])
        return tables
    if name == "by_level":
        return {"Completion by course level": completion_by_level(df)}
    if name == "by_duration":
        groups, stats = completion_by_duration(df)
        return {
            "Completion by course duration (quartiles of total content minutes)": groups,
            "Correlation": pd.DataFrame([stats]),
        }
    return {"Overall KPIs": pd.DataFrame([kpis(df)])}


def _client() -> genai.Client:
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not set in .env")
    # Retry with backoff on transient errors (e.g. 503 "model overloaded").
    retry = types.HttpRetryOptions(attempts=4, initial_delay=2.0)
    return genai.Client(api_key=key, http_options=types.HttpOptions(retry_options=retry))


def _model() -> str:
    model = os.getenv("GEMINI_MODEL")
    if not model:
        raise RuntimeError("GEMINI_MODEL is not set in .env")
    return model


def route(question: str, client: genai.Client) -> tuple[str, str]:
    """Ask Gemini to pick (analysis, order). Invalid values fall back to ('overview', 'lowest')."""
    options = "\n".join(f"- {name}: {desc}" for name, desc in ANALYSES.items())
    prompt = (
        "You route questions about online course data to one predefined analysis.\n"
        f"Available analyses:\n{options}\n\n"
        f"Question (may be in any language): {question}\n\n"
        'Return only JSON like {"analysis": "<name>", "order": "lowest"} with exactly one name '
        'from the list. "order" is "lowest" or "highest" and matters only for course_ranking: '
        'use "highest" for best/highest/most completed courses, otherwise "lowest".'
    )
    try:
        response = client.models.generate_content(
            model=_model(),
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0),
        )
        choice = json.loads(response.text)
        name, order = choice.get("analysis"), choice.get("order", "lowest")
    except Exception:
        return "overview", "lowest"
    if name not in ANALYSES:
        name = "overview"
    if order not in ORDERS:
        order = "lowest"
    return name, order


ANSWER_INSTRUCTIONS = f"""You are a data analyst explaining course completion data.
Rules:
- Answer in the same language as the user's question.
- Use only the numbers in the computed results. Do not invent numbers or add outside facts.
- Definition: {COMPLETED_DEFINITION}
- Units: an enrollment is one student in one course. total_enrollments, completed_enrollments
  and completion rates count ENROLLMENTS, not courses and not unique students. Say
  "completed enrollments" or "certified enrollments", never "completed courses".
  Only the 'courses' column and course-level tables count courses.
- Low completion rates are typical for open online courses (single-digit percentages
  are common), so describe the numbers factually and calmly, not as alarming.
- Mention sample sizes where they matter (e.g. a group with few enrollments).
- 'unlabeled' means the course level is missing in the source data; do not guess it.
- The data shows associations, not causes.
- If the results do not fully answer the question, say so briefly.
- Keep it short: 3-6 sentences or a few bullet points, plain language."""


def answer(question: str, df: pd.DataFrame) -> dict:
    """Return {'analysis', 'tables', 'text', 'error'} for the dashboard."""
    try:
        client = _client()
    except RuntimeError as exc:
        return {"analysis": "overview", "tables": run_analysis("overview", df),
                "text": None, "error": str(exc)}

    name, order = route(question, client)
    tables = run_analysis(name, df, order)
    if name == "course_ranking":
        name = f"course_ranking ({order})"
    results = "\n\n".join(f"{title}:\n{table.to_string(index=False)}" for title, table in tables.items())

    try:
        response = client.models.generate_content(
            model=_model(),
            contents=f"Question: {question}\n\nComputed results:\n{results}",
            config=types.GenerateContentConfig(system_instruction=ANSWER_INSTRUCTIONS, temperature=0.2),
        )
        return {"analysis": name, "tables": tables, "text": response.text, "error": None}
    except Exception as exc:
        # HTTP status (e.g. 429 = rate limit, 503 = overloaded) helps diagnose; no secrets in it.
        code = getattr(exc, "code", None)
        reason = f"{type(exc).__name__} {code}" if code else type(exc).__name__
        return {"analysis": name, "tables": tables, "text": None,
                "error": f"Gemini request failed ({reason})."}
