"""Demo role switcher. Judges pick a role in the header and never see a login screen.

DEMO_MODE=true maps the X-Demo-Role header to a seeded user in the NovaTech Cloud
workspace. With DEMO_MODE=false this path is disabled and JWT login is required.
"""
import os
from dataclasses import dataclass
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException

DEMO_MODE = os.getenv("DEMO_MODE", "true").lower() == "true"
DEMO_WORKSPACE_ID = "ws_novatech_cloud"  # Must match seed.py

# role -> (user_id, display name). User IDs must match seed.py.
DEMO_USERS = {
    "creator": ("user_demo_creator", "Creator"),
    "approver": ("user_demo_approver", "Approver"),
    "admin": ("user_demo_admin", "Admin"),
}


@dataclass(frozen=True)
class Actor:
    user_id: str
    name: str
    role: str
    workspace_id: str


def get_actor(x_demo_role: Optional[str] = Header(default=None)) -> Actor:
    if not DEMO_MODE:
        raise HTTPException(status_code=401, detail="Authentication required")
    role = (x_demo_role or "creator").lower()
    if role not in DEMO_USERS:
        raise HTTPException(status_code=400, detail="Unknown demo role")
    user_id, name = DEMO_USERS[role]
    return Actor(user_id=user_id, name=name, role=role, workspace_id=DEMO_WORKSPACE_ID)


router = APIRouter(tags=["demo"])


@router.get("/demo/roles")
def list_demo_roles() -> list[dict]:
    return [{"role": role, "name": name} for role, (_, name) in DEMO_USERS.items()]


@router.get("/me")
def current_actor(actor: Actor = Depends(get_actor)) -> Actor:
    return actor
