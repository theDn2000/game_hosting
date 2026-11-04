"""Plans: the product tiers and the resources they allow.

For now plans are created by the operator (CLI). Enforcing the limits when
servers are provisioned is Phase 5; managing plans together with billing is
Phase 7.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.domain.models import Plan

if TYPE_CHECKING:
    from app.services.repositories import UnitOfWorkFactory


class PlanCatalog:
    def __init__(self, uow_factory: UnitOfWorkFactory):
        self._uow_factory = uow_factory

    def add_plan(self, slug, name, memory_mb, cpu_millicores):
        """Raises InvalidDataError for invalid values, AlreadyExistsError for a
        slug that is taken."""
        plan = Plan.new(slug, name, memory_mb, cpu_millicores)
        with self._uow_factory() as uow:
            uow.plans.add(plan)
            uow.commit()
        return plan

    def list_plans(self):
        with self._uow_factory() as uow:
            return uow.plans.list_all()
