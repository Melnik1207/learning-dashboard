# Course Completion Analyst

A simple Data Analyst Agent with a Streamlit dashboard. It analyzes course completion
and student progress, and answers plain-language questions about the data
(in English, Ukrainian or any other language).

## What the agent does

1. **Routing.** Gemini receives the question and a fixed list of analyses
   (`overview`, `course_ranking`, `by_level`, `by_duration`) and returns JSON with one name
   plus an optional `order` (`"lowest"` or `"highest"`, default `"lowest"`) used by `course_ranking`.
   Both values are validated in code; an invalid name or any API error falls back to `overview`,
   an invalid order falls back to `"lowest"`.
2. **Analysis.** The chosen analysis runs in pandas on data loaded by one predefined,
   read-only SQL query. Gemini never writes SQL and never sees the database.
3. **Answer.** Gemini writes a short answer **only from the computed numbers**, in the
   language of the question. The prompt contains the definition of "completed", asks to
   mention sample sizes and notes that low completion is typical for online courses
   (so answers stay factual, not alarmist). The tables behind each answer are shown
   in the dashboard so every number can be checked.

Example questions:
- "Which courses have the lowest completion rate?" / "Який курс має найвищий рівень завершення?"
- "Does course level affect completion?" / "Чи впливає рівень курсу на завершення?"
- "Do longer courses have lower completion?"

The dashboard also shows KPIs (total enrollments, average progress, completion rate),
completion rate by course level, and the 10 courses with the lowest completion rate.

## Data

- `enrollments` — 94,705 rows, one per student × course enrollment.
- `dim_course` — 3,032 rows, joined **by `course_id`** (100% of enrollments match).
  Not joined by `course_url`: `dim_course` has one row per course-in-specialization,
  so a URL-based join could duplicate rows.
- `reviews`, `payments`, `weekly_activity` are not used.

## Definition of "completed"

**An enrollment is completed when `is_certified = 1`** (row-for-row identical to
`funnel_state = 'certified'`): 4,555 of 94,705 enrollments, **4.81%**.

Why not the other fields:
- `completed_at` is filled for 356 extra non-certified enrollments, but in all of them the
  date is *before* `enrolled_at`, none reached the last week and progress goes as low as 0%.
- `progress_pct` exceeds 100 (up to 104) in 277 rows; the 263 non-certified ones never
  reached the last week. Exactly 100 occurs only for certified enrollments.

Average progress uses `progress_pct` capped at 100.

## Why the low-completion table groups by `course_slug`

`course_id` identifies a course *inside a specialization*: the same course included in
several specializations has several `course_id`s (115 courses repeat across 202 rows).
Grouping by `course_id` would split one course's enrollments into several small groups.
`course_slug` (rule R1) identifies the real course, so each course appears once with all
its enrollments. Only courses with at least 30 enrollments are shown, to avoid
unreliable rates from tiny samples. 153 of 706 such courses have 0% completion, so ties
are ordered by enrollments (largest first) — the most significant zero-completion
courses come first. The agent can also rank the *highest* completion rates with the same
30-enrollment rule and the same tie-break.

Note that all KPIs count **enrollments** (one student in one course), not courses or unique students.

## Data quality

The database is read-only (the app also forces read-only transactions), so cleaning
rules from the previous homework (`docs/cleaning_rules.md`) are applied in pandas at load time.
Before building, the rule pre-checks were run against the database: it contains the raw,
uncleaned `dim_course` and matches the documented expectations (196 `Unknown` levels,
137 `Unknown` domains, 2,917 slugs, 13 rows without content metrics, etc.).

Applied rules:

| Rule | What | Why |
|---|---|---|
| R3.1 | Strip leading/trailing whitespace in course names | 122 names had extra whitespace; it would create duplicate names and break R4 |
| R1 | Derive `course_slug` from `course_url` | A real course key; `course_id` is a course-in-specialization key |
| R2 | `'Unknown'` in `level`/`domain` → missing, shown as **"unlabeled"** | It is a masked NULL. Never imputed: unlabeled courses are larger than average (missing not at random), so filling with "Beginner" would bias results |
| R4 | `language` from the end-of-name marker | Shown in the low-completion table as context |

Duration analysis excludes the 13 rows without content metrics (no enrollments point to
them) and `course_id` C00169, whose content minutes are exactly doubled (179 enrollments
excluded from this analysis only).

Not applied: R3.2 (type changes — already done in the database: `n_weeks` is integer)
and the `n_reviews` part of R2 (reviews are not used).

Findings that differ from the previous homework:
- The documented 122 names with whitespace are 121 with SQL `BTRIM` and 122 with full
  whitespace trimming: `C02779` ends with tab characters. Python `str.strip()` removes them.
- 29 course names contain double spaces *inside* the name (e.g. `Modern Robotics, Course 1:  Foundations…`).
  Per the rule they are not collapsed automatically; this does not affect results because
  courses are grouped by slug.
- `enrollments.enrolled_at` mixes two formats (`YYYY-MM-DD` and `DD.MM.YYYY`); dates are
  not used in this project.

## How to run (Windows, PowerShell)

```powershell
py -3.14 -m venv .venv            # or: uv venv --python 3.12
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env       # then fill in DATABASE_URL, GEMINI_API_KEY, GEMINI_MODEL
streamlit run app.py
```

`.env` is loaded with python-dotenv and is excluded from git via `.gitignore`.

## Project structure

```
app.py            Streamlit dashboard
agent.py          SQL loading, cleaning, analyses, Gemini routing and answers
requirements.txt
.env.example      variable names only
docs/             data profile and cleaning rules from the previous homework
```
