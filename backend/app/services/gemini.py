import logging
import time
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.core.config import get_settings

logger = logging.getLogger(__name__)
OutputModel = TypeVar("OutputModel", bound=BaseModel)


class LLMUnavailableError(RuntimeError):
    pass


class LLMProviderError(RuntimeError):
    pass


def _client():
    settings = get_settings()
    if not settings.llm_enabled:
        raise LLMUnavailableError("Gemini is disabled or GEMINI_API_KEY is not configured.")
    try:
        from google import genai
        from google.genai import types
    except ImportError as error:
        raise LLMProviderError("The Gemini SDK is not installed.") from error
    client = genai.Client(
        api_key=settings.effective_gemini_api_key,
        http_options=types.HttpOptions(timeout=settings.llm_request_timeout_seconds * 1000),
    )
    return client, types


def generate_structured_response(prompt: str, output_model: type[OutputModel]) -> OutputModel:
    settings = get_settings()
    client, types = _client()
    for attempt in range(settings.llm_max_retries + 1):
        try:
            response = client.models.generate_content(
                model=settings.llm_model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=output_model,
                    temperature=0.2,
                ),
            )
            parsed = getattr(response, "parsed", None)
            if isinstance(parsed, output_model):
                return parsed
            if parsed is not None:
                return output_model.model_validate(parsed)
            text = getattr(response, "text", None)
            if not text:
                raise LLMProviderError("Gemini returned no structured response.")
            return output_model.model_validate_json(text)
        except ValidationError as error:
            raise LLMProviderError("Gemini returned a response that did not match the required schema.") from error
        except LLMProviderError:
            raise
        except Exception as error:
            if attempt < settings.llm_max_retries:
                logger.warning("Gemini request failed; retrying attempt %s of %s.", attempt + 1, settings.llm_max_retries)
                time.sleep(min(2 ** attempt, 4))
                continue
            logger.exception("Gemini request failed after %s attempts.", attempt + 1)
            raise LLMProviderError("Gemini could not complete the request.") from error
    raise LLMProviderError("Gemini could not complete the request.")


def generate_embeddings(texts: list[str], task_type: str) -> list[list[float]]:
    if not texts:
        return []
    settings = get_settings()
    client, types = _client()
    for attempt in range(settings.llm_max_retries + 1):
        try:
            response = client.models.embed_content(
                model=settings.llm_embedding_model,
                contents=texts,
                config=types.EmbedContentConfig(task_type=task_type),
            )
            items = getattr(response, "embeddings", None) or []
            vectors = [list(item.values or []) for item in items]
            if len(vectors) != len(texts) or any(not vector for vector in vectors):
                raise LLMProviderError("Gemini returned an incomplete embedding response.")
            return vectors
        except LLMProviderError:
            raise
        except Exception as error:
            if attempt < settings.llm_max_retries:
                logger.warning("Gemini embedding request failed; retrying attempt %s of %s.", attempt + 1, settings.llm_max_retries)
                time.sleep(min(2 ** attempt, 4))
                continue
            logger.exception("Gemini embedding request failed after %s attempts.", attempt + 1)
            raise LLMProviderError("Gemini could not create embeddings.") from error
    raise LLMProviderError("Gemini could not create embeddings.")
