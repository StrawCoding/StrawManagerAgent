"""Mode policy — authorization / state transitions (Baseline v1.1)."""

from __future__ import annotations

from sma.db import Store, utcnow
from sma.models import (
    ExecutorType,
    Mode,
    Plan,
    PlanStatus,
    Task,
    TaskStatus,
    new_id,
)


class ModePolicyError(Exception):
    pass


class ModePolicy:
    """Computes plan/task dispatchability; does not spawn workers."""

    def __init__(self, store: Store) -> None:
        self.store = store

    def create_plan_for_request(
        self,
        session_id: str,
        title: str,
        body_md: str,
        mode: Mode,
    ) -> Plan:
        now = utcnow()
        if mode == Mode.MANAGER:
            status = PlanStatus.APPROVED  # auto-approved path to DISPATCHABLE
        elif mode == Mode.TEAM_LEADER:
            status = PlanStatus.WAITING_APPROVAL
        else:
            status = PlanStatus.READY
        plan = Plan(
            id=new_id("plan_"),
            session_id=session_id,
            status=status,
            title=title,
            body_md=body_md,
            created_at=now,
            updated_at=now,
        )
        return self.store.create_plan(plan)

    def approve_plan(self, plan_id: str) -> Plan:
        plan = self.store.get_plan(plan_id)
        if plan is None:
            raise ModePolicyError(f"plan not found: {plan_id}")
        session = self.store.get_session(plan.session_id)
        if session is None:
            raise ModePolicyError("session missing")
        if session.mode != Mode.TEAM_LEADER:
            raise ModePolicyError("approve only valid in team_leader mode")
        if plan.status not in (PlanStatus.WAITING_APPROVAL, PlanStatus.READY):
            raise ModePolicyError(f"cannot approve plan in status {plan.status}")
        plan = self.store.update_plan_status(plan_id, PlanStatus.APPROVED)
        # Mark related queued tasks dispatchable
        for task in self.store.list_tasks(plan.session_id):
            if task.plan_id == plan_id and task.status in (TaskStatus.DRAFT, TaskStatus.QUEUED):
                task.dispatchable = True
                task.status = TaskStatus.QUEUED
                task.updated_at = utcnow()
                self.store.save_task(task)
        return plan

    def reject_plan(self, plan_id: str) -> Plan:
        plan = self.store.get_plan(plan_id)
        if plan is None:
            raise ModePolicyError(f"plan not found: {plan_id}")
        return self.store.update_plan_status(plan_id, PlanStatus.REJECTED)

    def create_task(
        self,
        session_id: str,
        title: str,
        prompt: str,
        *,
        plan_id: str | None = None,
        executor: ExecutorType = ExecutorType.OPENCODE,
        confirmed: bool = False,
        dispatch_key: str | None = None,
    ) -> Task:
        session = self.store.get_session(session_id)
        if session is None:
            raise ModePolicyError("session not found")
        now = utcnow()
        dispatchable = False
        status = TaskStatus.DRAFT

        if session.mode == Mode.MANAGER:
            if plan_id:
                plan = self.store.get_plan(plan_id)
                if plan and plan.status == PlanStatus.APPROVED:
                    dispatchable = True
                    status = TaskStatus.QUEUED
            else:
                # Manager may create plan-less operational tasks once planned inline
                dispatchable = True
                status = TaskStatus.QUEUED
        elif session.mode == Mode.TEAM_LEADER:
            if not plan_id:
                raise ModePolicyError("team_leader tasks require plan_id")
            plan = self.store.get_plan(plan_id)
            if plan is None:
                raise ModePolicyError("plan not found")
            if plan.status == PlanStatus.APPROVED:
                dispatchable = True
                status = TaskStatus.QUEUED
            else:
                dispatchable = False
                status = TaskStatus.QUEUED  # waiting — not dispatchable
        elif session.mode == Mode.DEVELOPER:
            if confirmed:
                dispatchable = True
                status = TaskStatus.QUEUED
            else:
                dispatchable = False
                status = TaskStatus.DRAFT

        task = Task(
            id=new_id("task_"),
            session_id=session_id,
            plan_id=plan_id,
            title=title,
            status=status,
            dispatchable=dispatchable,
            dispatch_key=dispatch_key or new_id("dk_"),
            attempt=0,
            lease_owner=None,
            lease_expires_at=None,
            executor_type=executor,
            prompt=prompt,
            confirmed=confirmed,
            created_at=now,
            updated_at=now,
        )
        return self.store.create_task(task)

    def confirm_task(self, task_id: str) -> Task:
        task = self.store.get_task(task_id)
        if task is None:
            raise ModePolicyError("task not found")
        session = self.store.get_session(task.session_id)
        if session is None or session.mode != Mode.DEVELOPER:
            raise ModePolicyError("confirm only in developer mode")
        task.confirmed = True
        task.dispatchable = True
        task.status = TaskStatus.QUEUED
        task.updated_at = utcnow()
        return self.store.save_task(task)

    def assert_spawn_allowed(self, task: Task) -> None:
        """Hard invariant used by DispatchService."""
        session = self.store.get_session(task.session_id)
        if session is None:
            raise ModePolicyError("session missing")
        if not task.dispatchable:
            raise ModePolicyError("task not dispatchable")
        if task.status not in (TaskStatus.QUEUED, TaskStatus.BLOCKED):
            # BLOCKED may be re-queued after recovery with dispatchable True
            if task.status != TaskStatus.QUEUED:
                raise ModePolicyError(f"task status {task.status} cannot spawn")

        if session.mode == Mode.TEAM_LEADER:
            if not task.plan_id:
                raise ModePolicyError("team_leader spawn requires plan")
            plan = self.store.get_plan(task.plan_id)
            if plan is None or plan.status != PlanStatus.APPROVED:
                raise ModePolicyError("plan not approved")

        if session.mode == Mode.DEVELOPER and not task.confirmed:
            raise ModePolicyError("developer task not confirmed")
