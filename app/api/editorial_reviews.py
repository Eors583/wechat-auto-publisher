from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.services.batches import BatchService
from app.services.editorial_reviews import EditorialReviewConflict


class EditorialReviewProfileRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class AccountEditorialReviewDefaultRequest(BaseModel):
    profile_id: str = Field(min_length=1, max_length=120)
    config: dict[str, Any] = Field(default_factory=dict)


class RunEditorialReviewRequest(BaseModel):
    profile_id: str | None = Field(default=None, max_length=120)
    config: dict[str, Any] = Field(default_factory=dict)


class GenerateEditorialRewriteRequest(BaseModel):
    issue_ids: list[
        Annotated[str, Field(min_length=1, max_length=120)]
    ] = Field(default_factory=list)
    rewrite_mode: str = Field(default="selected_issues", max_length=80)
    paragraph_numbers: list[
        Annotated[int, Field(ge=1)]
    ] = Field(default_factory=list)
    instruction: str = Field(default="", max_length=4000)


class ResolveEditorialReviewIssueRequest(BaseModel):
    resolution: Literal["open", "resolved", "waived"]
    note: str = Field(default="", max_length=1000)
    resolved_by: str = Field(default="", max_length=100)


def create_editorial_review_router(
    service: BatchService,
    require_token: Callable[..., Any],
) -> APIRouter:
    """Expose the shared editorial-review service without duplicating its rules."""

    router = APIRouter(dependencies=[Depends(require_token)])

    @router.get("/api/v1/editorial-review/options")
    def get_editorial_review_options() -> dict[str, Any]:
        return service.get_editorial_review_options()

    @router.get("/api/v1/editorial-review/profiles")
    def list_editorial_review_profiles(
        include_builtin: bool = Query(default=True),
    ) -> list[dict[str, Any]]:
        return service.list_editorial_review_profiles(
            include_builtin=include_builtin
        )

    @router.get("/api/v1/editorial-review/profiles/{profile_id}")
    def get_editorial_review_profile(profile_id: str) -> dict[str, Any]:
        profiles = service.list_editorial_review_profiles(
            include_builtin=True
        )
        profile = next(
            (
                item
                for item in profiles
                if str(item.get("id") or "") == str(profile_id)
            ),
            None,
        )
        if profile is None:
            raise HTTPException(
                status_code=404,
                detail=f"评审方案不存在：{profile_id}",
            )
        return profile

    @router.post("/api/v1/editorial-review/profiles")
    def create_editorial_review_profile(
        payload: EditorialReviewProfileRequest,
    ) -> dict[str, Any]:
        return _domain_call(
            service.save_editorial_review_profile,
            **payload.model_dump(),
        )

    @router.put("/api/v1/editorial-review/profiles/{profile_id}")
    def update_editorial_review_profile(
        profile_id: str,
        payload: EditorialReviewProfileRequest,
    ) -> dict[str, Any]:
        return _domain_call(
            service.save_editorial_review_profile,
            profile_id=profile_id,
            **payload.model_dump(),
        )

    @router.delete("/api/v1/editorial-review/profiles/{profile_id}")
    def delete_editorial_review_profile(profile_id: str) -> dict[str, Any]:
        _domain_call(service.delete_editorial_review_profile, profile_id)
        return {"id": profile_id, "deleted": True}

    @router.get(
        "/api/v1/accounts/{account_id}/editorial-review-default"
    )
    def get_account_editorial_review_default(
        account_id: str,
    ) -> dict[str, Any]:
        return _domain_call(
            service.get_account_editorial_review_default,
            account_id,
        )

    @router.put(
        "/api/v1/accounts/{account_id}/editorial-review-default"
    )
    def set_account_editorial_review_default(
        account_id: str,
        payload: AccountEditorialReviewDefaultRequest,
    ) -> dict[str, Any]:
        return _domain_call(
            service.set_account_editorial_review_default,
            account_id,
            profile_id=payload.profile_id,
            config=payload.config,
        )

    @router.post(
        "/api/v1/batches/{batch_id}/jobs/{job_id}/editorial-reviews"
    )
    def run_editorial_review(
        batch_id: str,
        job_id: int,
        payload: RunEditorialReviewRequest | None = None,
    ) -> dict[str, Any]:
        request = payload or RunEditorialReviewRequest()
        return _domain_call(
            service.run_editorial_review,
            batch_id,
            job_id,
            profile_id=request.profile_id,
            config=request.config,
        )

    @router.get("/api/v1/editorial-reviews")
    def list_editorial_reviews(
        job_id: int | None = Query(default=None, ge=1),
        batch_id: str | None = Query(default=None),
        limit: int = Query(default=20, ge=1, le=100),
    ) -> list[dict[str, Any]]:
        return service.list_editorial_reviews(
            job_id=job_id,
            batch_id=batch_id,
            limit=limit,
        )

    @router.get("/api/v1/editorial-reviews/{review_id}")
    def get_editorial_review(review_id: str) -> dict[str, Any]:
        return _domain_call(service.get_editorial_review, review_id)

    @router.post(
        "/api/v1/batches/{batch_id}/jobs/{job_id}/"
        "editorial-reviews/{review_id}/rewrite-candidates"
    )
    def generate_editorial_rewrite_candidate(
        batch_id: str,
        job_id: int,
        review_id: str,
        payload: GenerateEditorialRewriteRequest,
    ) -> dict[str, Any]:
        return _domain_call(
            service.generate_editorial_rewrite_candidate,
            batch_id,
            job_id,
            review_id,
            issue_ids=payload.issue_ids,
            rewrite_mode=payload.rewrite_mode,
            paragraph_numbers=payload.paragraph_numbers,
            instruction=payload.instruction,
        )

    @router.get(
        "/api/v1/editorial-reviews/{review_id}/applications"
    )
    def list_editorial_review_applications(
        review_id: str,
        limit: int = Query(default=20, ge=1, le=100),
    ) -> list[dict[str, Any]]:
        return _domain_call(
            service.list_editorial_review_applications,
            review_id,
            limit=limit,
        )

    @router.get(
        "/api/v1/editorial-review-applications/{application_id}"
    )
    def get_editorial_review_application(
        application_id: str,
    ) -> dict[str, Any]:
        return _domain_call(
            service.get_editorial_review_application,
            application_id,
        )

    @router.post(
        "/api/v1/batches/{batch_id}/jobs/{job_id}/"
        "editorial-review-applications/{application_id}/apply"
    )
    def apply_editorial_review_application(
        batch_id: str,
        job_id: int,
        application_id: str,
    ) -> dict[str, Any]:
        return _domain_call(
            service.apply_editorial_review_application,
            batch_id,
            job_id,
            application_id,
        )

    @router.patch(
        "/api/v1/editorial-reviews/{review_id}/issues/{issue_id}"
    )
    def resolve_editorial_review_issue(
        review_id: str,
        issue_id: str,
        payload: ResolveEditorialReviewIssueRequest,
    ) -> dict[str, Any]:
        return _domain_call(
            service.resolve_editorial_review_issue,
            review_id,
            issue_id,
            resolution=payload.resolution,
            note=payload.note,
            resolved_by=payload.resolved_by,
        )

    return router


def _domain_call(callback: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    try:
        return callback(*args, **kwargs)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except EditorialReviewConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


__all__ = [
    "AccountEditorialReviewDefaultRequest",
    "EditorialReviewProfileRequest",
    "GenerateEditorialRewriteRequest",
    "ResolveEditorialReviewIssueRequest",
    "RunEditorialReviewRequest",
    "create_editorial_review_router",
]
