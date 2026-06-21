"""Athena â€” Executive Assistant. Calendar, scheduling, tasks, organization."""
import logging
from typing import Any
from datetime import datetime
from app.agents.base import BaseAgent, AgentContext, AgentActionResult
from app.services.calendar_service import CalendarService
from app.services.task_service import TaskService
from app.schemas.calendar import CalendarEventCreate
from app.schemas.task import TaskCreate

logger = logging.getLogger(__name__)

ATHENA_SYSTEM_PROMPT = """You are Athena, the Executive Assistant of the AI council.

Your role: Calendar management. Scheduling. Task organization. Meeting booking.
You do NOT raw-query the database â€” use your dedicated calendar and task tools.

Personality: Warm, efficient, proactive. You keep the human organized
so they can focus on what matters. You anticipate needs before they arise.

Capabilities:
- Managing calendar events and schedules
- Booking and coordinating meetings
- Setting reminders and follow-ups
- Organizing tasks by priority
- Planning daily agendas
- Resolving scheduling conflicts
- Sending meeting reminders

You make the user feel like they have a world-class executive assistant."""


class Athena(BaseAgent):
    LLM_MODEL = "deepseek-v4-flash"
    LLM_MODEL_FALLBACKS = ["deepseek-v4-pro"]
    def __init__(self):
        super().__init__(
            name="athena",
            role="Executive Assistant",
            system_prompt=ATHENA_SYSTEM_PROMPT,
        )

    @property
    def tools(self) -> list[dict]:
        return [
            {
                "name": "list_calendar_events",
                "description": "Get calendar events for a date range",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "start_date": {"type": "string", "description": "ISO date to start from"},
                        "end_date": {"type": "string", "description": "ISO date to end at"},
                        "limit": {"type": "integer"},
                    },
                },
            },
            {
                "name": "create_event",
                "description": "Create a new calendar event",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "start_time": {"type": "string", "description": "ISO datetime"},
                        "end_time": {"type": "string", "description": "ISO datetime"},
                        "description": {"type": "string"},
                        "attendees": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["title", "start_time", "end_time"],
                },
            },
            {
                "name": "list_tasks",
                "description": "Get tasks with optional filters",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
                        "priority": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
                    },
                },
            },
            {
                "name": "create_task",
                "description": "Create a new task",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                        "priority": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
                        "due_date": {"type": "string", "description": "ISO datetime"},
                        "assignee_id": {"type": "string"},
                    },
                    "required": ["title"],
                },
            },
        ]

    async def _execute_tool(self, tool_name: str, args: dict, context: AgentContext = None):
        if not context or not context.db_session or not context.org_id:
            return {"status": "requires_db_session", "tool": tool_name}

        if tool_name == "list_calendar_events":
            svc = CalendarService(context.db_session, context.org_id)
            result = await svc.list_events(
                date_from=args.get("start_date"),
                date_to=args.get("end_date"),
                page_size=args.get("limit", 50),
            )
            return {"status": "success", "events": result.get("items", [])}

        if tool_name == "create_event":
            svc = CalendarService(context.db_session, context.org_id)
            event = await svc.create_event(CalendarEventCreate(
                title=args.get("title", ""),
                start_time=datetime.fromisoformat(args["start_time"]),
                end_time=datetime.fromisoformat(args["end_time"]),
                description=args.get("description"),
                attendees=[{"email": a} for a in args.get("attendees", [])],
            ))
            return {"status": "created", "event_id": event.get("id")}

        if tool_name == "list_tasks":
            svc = TaskService(context.db_session, context.org_id)
            result = await svc.list_tasks(
                status=args.get("status"),
                priority=args.get("priority"),
            )
            return {"status": "success", "tasks": result.get("items", [])}

        if tool_name == "create_task":
            svc = TaskService(context.db_session, context.org_id)
            task = await svc.create_task(TaskCreate(
                title=args.get("title", ""),
                description=args.get("description"),
                priority=args.get("priority", "medium"),
                due_date=datetime.fromisoformat(args["due_date"]) if args.get("due_date") else None,
            ))
            return {"status": "created", "task_id": task.get("id")}

        return {"status": "unknown_tool", "tool": tool_name}

