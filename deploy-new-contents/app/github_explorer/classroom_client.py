"""
GitHub Classroom Client — wraps the GitHub Classroom REST API
to expose classrooms, assignments, submissions, and grades.

Requires the authenticated user to be an administrator of the classroom.
"""

from __future__ import annotations

import os
from typing import Optional

import requests


def _headers(token: Optional[str] = None) -> dict:
    t = token or os.getenv("GITHUB_TOKEN")
    h = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if t:
        h["Authorization"] = f"Bearer {t}"
    return h


BASE = "https://api.github.com"


def list_classrooms(token: Optional[str] = None) -> list[dict]:
    """List all classrooms the authenticated user administers."""
    resp = requests.get(f"{BASE}/classrooms", headers=_headers(token), timeout=15)
    resp.raise_for_status()
    return [
        {
            "id": c["id"],
            "name": c["name"],
            "archived": c.get("archived", False),
            "url": c.get("url", ""),
        }
        for c in resp.json()
    ]


def get_classroom(classroom_id: int, token: Optional[str] = None) -> dict:
    """Get details for a specific classroom."""
    resp = requests.get(
        f"{BASE}/classrooms/{classroom_id}", headers=_headers(token), timeout=15
    )
    resp.raise_for_status()
    data = resp.json()
    org = data.get("organization", {})
    return {
        "id": data["id"],
        "name": data["name"],
        "archived": data.get("archived", False),
        "url": data.get("url", ""),
        "organization": {
            "login": org.get("login"),
            "name": org.get("name"),
            "html_url": org.get("html_url"),
            "avatar_url": org.get("avatar_url"),
        },
    }


def list_assignments(
    classroom_id: int, token: Optional[str] = None
) -> list[dict]:
    """List assignments for a classroom."""
    resp = requests.get(
        f"{BASE}/classrooms/{classroom_id}/assignments",
        headers=_headers(token),
        timeout=15,
    )
    resp.raise_for_status()
    results = []
    items = resp.json()
    if not isinstance(items, list):
        items = [items]
    for a in items:
        results.append(
            {
                "id": a["id"],
                "title": a.get("title", ""),
                "slug": a.get("slug", ""),
                "type": a.get("type", ""),
                "invite_link": a.get("invite_link", ""),
                "accepted": a.get("accepted", 0),
                "submitted": a.get("submitted", 0),
                "passing": a.get("passing", 0),
                "language": a.get("language", ""),
                "deadline": a.get("deadline"),
                "editor": a.get("editor", ""),
            }
        )
    return results


def get_assignment(assignment_id: int, token: Optional[str] = None) -> dict:
    """Get details for a specific assignment."""
    resp = requests.get(
        f"{BASE}/assignments/{assignment_id}", headers=_headers(token), timeout=15
    )
    resp.raise_for_status()
    a = resp.json()
    classroom = a.get("classroom", {})
    starter = a.get("stater_code_repository") or a.get("starter_code_repository") or {}
    return {
        "id": a["id"],
        "title": a.get("title", ""),
        "slug": a.get("slug", ""),
        "type": a.get("type", ""),
        "invite_link": a.get("invite_link", ""),
        "accepted": a.get("accepted", 0),
        "submitted": a.get("submitted", 0),
        "passing": a.get("passing", 0),
        "language": a.get("language", ""),
        "deadline": a.get("deadline"),
        "editor": a.get("editor", ""),
        "feedback_pull_requests_enabled": a.get("feedback_pull_requests_enabled", False),
        "max_teams": a.get("max_teams", 0),
        "max_members": a.get("max_members", 0),
        "starter_code_repo": starter.get("full_name") if starter else None,
        "classroom": {
            "id": classroom.get("id"),
            "name": classroom.get("name"),
        },
    }


def list_accepted_assignments(
    assignment_id: int,
    page: int = 1,
    per_page: int = 30,
    token: Optional[str] = None,
) -> list[dict]:
    """List student submissions for an assignment."""
    resp = requests.get(
        f"{BASE}/assignments/{assignment_id}/accepted_assignments",
        headers=_headers(token),
        params={"page": page, "per_page": per_page},
        timeout=15,
    )
    resp.raise_for_status()
    results = []
    for s in resp.json():
        students = s.get("students", [])
        repo = s.get("repository", {})
        results.append(
            {
                "id": s.get("id"),
                "submitted": s.get("submitted", False),
                "passing": s.get("passing", False),
                "commit_count": s.get("commit_count", 0),
                "grade": s.get("grade", ""),
                "students": [
                    {
                        "login": st.get("login"),
                        "avatar_url": st.get("avatar_url"),
                        "html_url": st.get("html_url"),
                    }
                    for st in students
                ],
                "repository": {
                    "full_name": repo.get("full_name"),
                    "html_url": repo.get("html_url"),
                    "private": repo.get("private", False),
                },
            }
        )
    return results


def get_assignment_grades(
    assignment_id: int, token: Optional[str] = None
) -> list[dict]:
    """Get grades for an assignment."""
    resp = requests.get(
        f"{BASE}/assignments/{assignment_id}/grades",
        headers=_headers(token),
        timeout=15,
    )
    resp.raise_for_status()
    return [
        {
            "assignment_name": g.get("assignment_name", ""),
            "github_username": g.get("github_username", ""),
            "roster_identifier": g.get("roster_identifier", ""),
            "student_repository_name": g.get("student_repository_name", ""),
            "student_repository_url": g.get("student_repository_url", ""),
            "submission_timestamp": g.get("submission_timestamp"),
            "points_awarded": g.get("points_awarded"),
            "points_available": g.get("points_available"),
            "group_name": g.get("group_name", ""),
        }
        for g in resp.json()
    ]
