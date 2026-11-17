from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.database.base import utc_now
from app.models import AIInteraction


def store_interaction(
    db: Session,
    organization_id: UUID,
    user_id: UUID | None,
    interaction_type: str,
    input_text: str,
    response: dict,
    resource_id: UUID | None = None,
) -> AIInteraction:
    settings = get_settings()
    interaction = AIInteraction(
        organization_id=organization_id,
        user_id=user_id,
        interaction_type=interaction_type,
        resource_id=resource_id,
        input_text=input_text,
        response_json=response,
        evidence_ids=[item["id"] for item in response.get("citations", []) if item.get("id")],
        provider=settings.llm_provider,
        model=settings.llm_model,
        status="succeeded",
        created_at=utc_now(),
    )
    db.add(interaction)
    db.flush()
    return interaction
