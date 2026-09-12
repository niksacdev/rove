"""Preview and explicitly save immutable variants of configured strategies."""

from fastapi import APIRouter, HTTPException

from rove.models.config import active_config_path, load_config
from rove.strategies.revisions import (
    RevisionConflict,
    RevisionDraft,
    RevisionSave,
    list_revisions,
    parent,
    preview,
    save,
)


def create_strategy_revision_router() -> APIRouter:
    router = APIRouter()

    def invoke(action):
        try:
            return action()
        except RevisionConflict as error:
            raise HTTPException(409, str(error)) from error
        except KeyError as error:
            raise HTTPException(
                404, "Strategy is not available in the current configuration"
            ) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @router.get("/api/strategy-revisions")
    async def revisions():
        return invoke(lambda: {"revisions": list_revisions(active_config_path(), load_config())})

    @router.get("/api/strategy-revisions/parents/{identity}")
    async def source(identity: str):
        return invoke(lambda: parent(load_config(), identity))

    @router.post("/api/strategy-revisions/preview")
    async def prepare(request: RevisionDraft):
        return invoke(lambda: preview(load_config(), request))

    @router.post("/api/strategy-revisions", status_code=201)
    async def create(request: RevisionSave):
        return invoke(lambda: save(active_config_path(), request))

    return router
