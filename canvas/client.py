import asyncio
import logging
from datetime import datetime, timezone

import httpx

import config

logger = logging.getLogger(__name__)

CANVAS_API = f"{config.CANVAS_BASE_URL}/api/v1"
PER_PAGE = 50
MAX_PAGES = 20


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


async def get_courses(token: str) -> list[dict]:
    """Return active courses the user is enrolled in."""
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
    return sorted(
        [c for c in courses if c.get("name")],
        key=lambda c: c.get("name", ""),
    )


async def get_assignments(token: str, course_id: int) -> list[dict]:
    """Return assignments for a course, sorted by due date."""
    async with httpx.AsyncClient(timeout=30) as client:
        assignments = await _get_paginated(
            client,
            f"{CANVAS_API}/courses/{course_id}/assignments",
            _auth_headers(token),
            {"order_by": "due_at"},
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
    upcoming: list[dict] = []

    async with httpx.AsyncClient(timeout=30) as client:
        for course in courses:
            # Fetch assignments
            assignments = await _get_paginated(
                client,
                f"{CANVAS_API}/courses/{course['id']}/assignments",
                _auth_headers(token),
                {"order_by": "due_at"},
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
                    upcoming.append(a)

            # Fetch quizzes
            try:
                quizzes = await _get_paginated(
                    client,
                    f"{CANVAS_API}/courses/{course['id']}/quizzes",
                    _auth_headers(token),
                )
                for q in quizzes:
                    due = q.get("due_at")
                    if not due:
                        continue
                    due_dt = datetime.fromisoformat(due.replace("Z", "+00:00"))
                    diff = (due_dt - now).total_seconds()
                    if 0 < diff <= days * 86400:
                        q["name"] = q.get("title", "Untitled Quiz")
                        q["_course_name"] = course.get("name", "Unknown")
                        q["_course_id"] = course["id"]
                        q["_due_dt"] = due_dt
                        q["_type"] = "quiz"
                        upcoming.append(q)
            except Exception:
                logger.debug("Could not fetch quizzes for course %s", course["id"])

    return sorted(upcoming, key=lambda a: a["_due_dt"])


async def get_quizzes(token: str, course_id: int) -> list[dict]:
    """Return quizzes for a course, sorted by due date."""
    async with httpx.AsyncClient(timeout=30) as client:
        quizzes = await _get_paginated(
            client,
            f"{CANVAS_API}/courses/{course_id}/quizzes",
            _auth_headers(token),
        )
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
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()


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
    """Return a single assignment."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{CANVAS_API}/courses/{course_id}/assignments/{assignment_id}",
            headers=_auth_headers(token),
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
