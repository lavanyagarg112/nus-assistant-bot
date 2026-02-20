import asyncio
import hashlib
import logging
from datetime import datetime, timezone

import httpx

import config

logger = logging.getLogger(__name__)

CANVAS_API = f"{config.CANVAS_BASE_URL}/api/v1"
PER_PAGE = 50
MAX_PAGES = 20


class CanvasTokenError(Exception):
    """Raised when the Canvas API returns 401 (token expired/invalid)."""
    pass


# In-memory course cache: token_sha256 -> courses (permanent until /refresh)
_course_cache: dict[str, list[dict]] = {}


def _cache_key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def _get_paginated(
    client: httpx.AsyncClient, url: str, headers: dict, params: dict | None = None
) -> list[dict]:
    results: list[dict] = []
    params = params or {}
    params.setdefault("per_page", PER_PAGE)
    next_url: str | None = url
    page = 0

    while next_url:
        page += 1
        if page > MAX_PAGES:
            logger.warning("Pagination limit reached (%d pages), stopping", MAX_PAGES)
            break
        resp = await client.get(next_url, headers=headers, params=params)
        if resp.status_code == 401:
            raise CanvasTokenError("Canvas API token is invalid or expired")
        resp.raise_for_status()

        # Respect rate limits
        remaining = resp.headers.get("X-Rate-Limit-Remaining")
        if remaining and float(remaining) < 20:
            logger.warning("Canvas rate limit low (%s remaining), sleeping 1s", remaining)
            await asyncio.sleep(1)

        data = resp.json()
        if isinstance(data, list):
            results.extend(data)
        else:
            results.append(data)

        # Parse Link header for pagination
        next_url = None
        params = {}  # params are embedded in the Link URL
        link_header = resp.headers.get("Link", "")
        for part in link_header.split(","):
            if 'rel="next"' in part:
                next_url = part.split("<")[1].split(">")[0]
                break

    return results


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def clear_course_cache(token: str) -> None:
    """Remove cached courses for the given token."""
    _course_cache.pop(_cache_key(token), None)


async def get_courses(token: str) -> list[dict]:
    """Return active courses the user is enrolled in (cached until /refresh)."""
    cache_key = _cache_key(token)
    cached = _course_cache.get(cache_key)
    if cached is not None:
        return cached

    async with httpx.AsyncClient(timeout=30) as client:
        courses = await _get_paginated(
            client,
            f"{CANVAS_API}/courses",
            _auth_headers(token),
            {
                "enrollment_state": "active",
                "include[]": "term",
                "state[]": "available",
            },
        )
    # Filter to student enrollments and sort by name
    result = sorted(
        [c for c in courses if c.get("name")],
        key=lambda c: c.get("name", ""),
    )
    _course_cache[cache_key] = result
    return result


async def get_assignments(token: str, course_id: int) -> list[dict]:
    """Return assignments for a course, sorted by due date."""
    async with httpx.AsyncClient(timeout=30) as client:
        assignments = await _get_paginated(
            client,
            f"{CANVAS_API}/courses/{course_id}/assignments",
            _auth_headers(token),
            {"order_by": "due_at", "include[]": "submission"},
        )
    # Sort: assignments with due dates first, then those without
    return sorted(
        assignments,
        key=lambda a: (a.get("due_at") is None, a.get("due_at") or ""),
    )


async def get_upcoming_assignments(token: str, days: int = 7) -> list[dict]:
    """Return assignments and quizzes due within `days` across all courses."""
    courses = await get_courses(token)
    now = datetime.now(timezone.utc)

    async with httpx.AsyncClient(timeout=30) as client:
        # Fetch all courses in parallel
        results = await asyncio.gather(
            *[_fetch_course_upcoming(client, token, course, now, days) for course in courses],
            return_exceptions=True,
        )

    upcoming: list[dict] = []
    for result in results:
        if isinstance(result, list):
            upcoming.extend(result)

    return sorted(upcoming, key=lambda a: a["_due_dt"])


async def _fetch_course_upcoming(
    client: httpx.AsyncClient, token: str, course: dict, now: datetime, days: int
) -> list[dict]:
    """Fetch upcoming assignments and quizzes for a single course."""
    items: list[dict] = []

    # Fetch assignments
    assignments = await _get_paginated(
        client,
        f"{CANVAS_API}/courses/{course['id']}/assignments",
        _auth_headers(token),
        {"order_by": "due_at", "include[]": "submission"},
    )
    for a in assignments:
        due = a.get("due_at")
        if not due:
            continue
        due_dt = datetime.fromisoformat(due.replace("Z", "+00:00"))
        diff = (due_dt - now).total_seconds()
        if 0 < diff <= days * 86400:
            a["_course_name"] = course.get("name", "Unknown")
            a["_course_id"] = course["id"]
            a["_due_dt"] = due_dt
            a["_type"] = "assignment"
            items.append(a)

    # Fetch quizzes
    try:
        quizzes = await _get_paginated(
            client,
            f"{CANVAS_API}/courses/{course['id']}/quizzes",
            _auth_headers(token),
        )
        # Filter to upcoming quizzes first, then check submissions in parallel
        upcoming_quizzes: list[tuple[dict, datetime]] = []
        for q in quizzes[:10]:
            due = q.get("due_at")
            if not due:
                continue
            due_dt = datetime.fromisoformat(due.replace("Z", "+00:00"))
            diff = (due_dt - now).total_seconds()
            if 0 < diff <= days * 86400:
                upcoming_quizzes.append((q, due_dt))

        if upcoming_quizzes:
            sub_results = await asyncio.gather(
                *[_check_quiz_submitted(client, token, course["id"], q["id"]) for q, _ in upcoming_quizzes],
                return_exceptions=True,
            )
            for (q, due_dt), submitted in zip(upcoming_quizzes, sub_results):
                q["name"] = q.get("title", "Untitled Quiz")
                q["_course_name"] = course.get("name", "Unknown")
                q["_course_id"] = course["id"]
                q["_due_dt"] = due_dt
                q["_type"] = "quiz"
                q["_submitted"] = submitted if isinstance(submitted, bool) else False
                items.append(q)
    except Exception:
        logger.debug("Could not fetch quizzes for course %s", course["id"])

    return items


async def get_quizzes(token: str, course_id: int) -> list[dict]:
    """Return quizzes for a course with submission status, sorted by due date."""
    async with httpx.AsyncClient(timeout=30) as client:
        quizzes = await _get_paginated(
            client,
            f"{CANVAS_API}/courses/{course_id}/quizzes",
            _auth_headers(token),
        )
        check_quizzes = quizzes[:10]
        if check_quizzes:
            sub_results = await asyncio.gather(
                *[_check_quiz_submitted(client, token, course_id, q["id"]) for q in check_quizzes],
                return_exceptions=True,
            )
            for q, submitted in zip(check_quizzes, sub_results):
                q["_type"] = "quiz"
                q["_submitted"] = submitted if isinstance(submitted, bool) else False
    return sorted(
        quizzes,
        key=lambda q: (q.get("due_at") is None, q.get("due_at") or ""),
    )


async def get_quiz(token: str, course_id: int, quiz_id: int) -> dict | None:
    """Return a single quiz."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{CANVAS_API}/courses/{course_id}/quizzes/{quiz_id}",
            headers=_auth_headers(token),
        )
        if resp.status_code == 401:
            raise CanvasTokenError("Canvas API token is invalid or expired")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()


async def get_quiz_submission(token: str, course_id: int, quiz_id: int) -> dict | None:
    """Return the current user's quiz submission, or None."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{CANVAS_API}/courses/{course_id}/quizzes/{quiz_id}/submissions",
            headers=_auth_headers(token),
        )
        if resp.status_code == 401:
            raise CanvasTokenError("Canvas API token is invalid or expired")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        subs = data.get("quiz_submissions", [])
        return subs[0] if subs else None


async def _check_quiz_submitted(
    client: httpx.AsyncClient, token: str, course_id: int, quiz_id: int
) -> bool:
    """Check if the user has completed a quiz."""
    try:
        resp = await client.get(
            f"{CANVAS_API}/courses/{course_id}/quizzes/{quiz_id}/submissions",
            headers=_auth_headers(token),
        )
        if resp.status_code == 404:
            return False
        resp.raise_for_status()
        data = resp.json()
        subs = data.get("quiz_submissions", [])
        if subs:
            state = subs[0].get("workflow_state", "")
            return state in ("complete", "pending_review")
    except Exception:
        logger.debug("Could not fetch quiz submission for quiz %s", quiz_id)
    return False


def is_submitted(item: dict) -> bool:
    """Check if an assignment or quiz has been submitted."""
    if item.get("_type") == "quiz":
        return bool(item.get("_submitted"))
    # For assignments, check submission.workflow_state *and* that there
    # was a real submission attempt.  Canvas always returns a submission
    # object (even when the student never submitted), and instructors can
    # set workflow_state to "graded" without an actual submission.
    sub = item.get("submission", {})
    if not sub:
        return False
    state = sub.get("workflow_state", "unsubmitted")
    if state not in ("submitted", "graded", "pending_review"):
        return False
    return bool(sub.get("attempt"))


def submission_status_text(item: dict) -> str:
    """Return a human-readable submission status string."""
    if item.get("_type") == "quiz":
        return "Complete" if item.get("_submitted") else "Not taken"
    sub = item.get("submission", {})
    if not sub:
        return "Not submitted"
    state = sub.get("workflow_state", "unsubmitted")
    has_attempt = bool(sub.get("attempt"))
    if state == "graded":
        if not has_attempt:
            # Instructor graded without a student submission
            score = sub.get("score")
            if score is not None:
                return f"Graded, no submission ({score} pts)"
            return "Not submitted"
        score = sub.get("score")
        if score is not None:
            return f"Graded ({score} pts)"
        return "Graded"
    if state == "submitted":
        return "Submitted" if has_attempt else "Not submitted"
    if state == "pending_review":
        return "Pending review" if has_attempt else "Not submitted"
    return "Not submitted"


def assignment_url(course_id: int, assignment_id: int) -> str:
    return f"{config.CANVAS_BASE_URL}/courses/{course_id}/assignments/{assignment_id}"


def quiz_url(course_id: int, quiz_id: int) -> str:
    return f"{config.CANVAS_BASE_URL}/courses/{course_id}/quizzes/{quiz_id}"


def course_url(course_id: int) -> str:
    return f"{config.CANVAS_BASE_URL}/courses/{course_id}"


async def get_root_folder(token: str, course_id: int) -> dict | None:
    """Return the root folder for a course."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{CANVAS_API}/courses/{course_id}/folders/root",
            headers=_auth_headers(token),
        )
        if resp.status_code == 401:
            raise CanvasTokenError("Canvas API token is invalid or expired")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()


async def get_subfolders(token: str, folder_id: int) -> list[dict]:
    """Return subfolders of a folder."""
    async with httpx.AsyncClient(timeout=30) as client:
        return await _get_paginated(
            client,
            f"{CANVAS_API}/folders/{folder_id}/folders",
            _auth_headers(token),
        )


async def get_folder_files(token: str, folder_id: int) -> list[dict]:
    """Return files in a folder."""
    async with httpx.AsyncClient(timeout=30) as client:
        return await _get_paginated(
            client,
            f"{CANVAS_API}/folders/{folder_id}/files",
            _auth_headers(token),
        )


async def get_assignment(token: str, course_id: int, assignment_id: int) -> dict | None:
    """Return a single assignment with submission data."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{CANVAS_API}/courses/{course_id}/assignments/{assignment_id}",
            headers=_auth_headers(token),
            params={"include[]": "submission"},
        )
        if resp.status_code == 401:
            raise CanvasTokenError("Canvas API token is invalid or expired")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
