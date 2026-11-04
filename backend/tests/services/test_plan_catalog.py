import pytest

from app.errors import AlreadyExistsError, InvalidDataError
from app.services.plan_catalog import PlanCatalog
from tests.support.in_memory import InMemoryStore, InMemoryUnitOfWork


@pytest.fixture
def store():
    return InMemoryStore()


@pytest.fixture
def catalog(store):
    return PlanCatalog(lambda: InMemoryUnitOfWork(store))


def test_added_plans_are_stored_and_listed_by_slug(catalog, store):
    pro = catalog.add_plan("pro", "Pro", memory_mb=4096, cpu_millicores=2000)
    basic = catalog.add_plan("basic", "Basic", memory_mb=2048, cpu_millicores=1000)
    assert catalog.list_plans() == [basic, pro]
    assert set(store.plans) == {basic.id, pro.id}


def test_listing_with_no_plans_is_empty(catalog):
    assert catalog.list_plans() == []


def test_a_duplicate_slug_is_rejected_and_the_first_plan_is_kept(catalog):
    first = catalog.add_plan("basic", "Basic", memory_mb=2048, cpu_millicores=1000)
    with pytest.raises(AlreadyExistsError) as excinfo:
        catalog.add_plan("basic", "Other", memory_mb=1, cpu_millicores=1)
    assert excinfo.value.field == "slug"
    assert catalog.list_plans() == [first]


@pytest.mark.parametrize(
    "values",
    [
        dict(slug="Bad Slug", name="N", memory_mb=1, cpu_millicores=1),
        dict(slug="ok", name="", memory_mb=1, cpu_millicores=1),
        dict(slug="ok", name="N", memory_mb=0, cpu_millicores=1),
        dict(slug="ok", name="N", memory_mb=1, cpu_millicores=-5),
    ],
    ids=["slug", "name", "memory", "cpu"],
)
def test_invalid_values_are_rejected_and_nothing_is_stored(catalog, store, values):
    with pytest.raises(InvalidDataError):
        catalog.add_plan(**values)
    assert not store.plans
