"""Canonical action catalog service."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy import exists, select
from sqlalchemy.orm import aliased

from app.agent.memory.embedding import get_embedding
from app.database import get_db_session
from app.models.motion import Motion
from app.models.tag import MotionTag, MotionTagMap

if TYPE_CHECKING:
    from app.agent.character.loader import CharacterConfig

logger = logging.getLogger(__name__)

_DEFAULT_PROMPT_ACTION_LIMIT = 18
_DEFAULT_SEARCH_LIMIT = 5
_MIN_EMBEDDING_SIM = 0.35


def _normalize_text(text: str) -> str:
    return "".join(text.lower().split()) if isinstance(text, str) else ""


def _cosine_similarity(vec1: list[float] | None, vec2: list[float] | None) -> float:
    if not vec1 or not vec2 or len(vec1) != len(vec2):
        return 0.0

    dot_product = sum(a * b for a, b in zip(vec1, vec2))
    norm1 = sum(a * a for a in vec1) ** 0.5
    norm2 = sum(b * b for b in vec2) ** 0.5
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return dot_product / (norm1 * norm2)


class MotionCatalogService:
    """缓存 system=others 下的 canonical action 标签。"""

    def __init__(self):
        self._lock = asyncio.Lock()
        self._initialized = False
        self._actions: list[dict[str, Any]] = []
        self._actions_by_name: dict[str, dict[str, Any]] = {}

    async def initialize(self):
        await self.refresh()

    async def refresh(self):
        async with self._lock:
            actions = await self._load_actions()
            self._actions = actions
            self._actions_by_name = {item["action_tag"]: item for item in actions}
            self._initialized = True
            logger.info("[MotionCatalog] 已加载 %d 个 canonical actions", len(actions))

    async def get_all_actions(self) -> list[dict[str, Any]]:
        await self._ensure_initialized()
        return [self._copy_action(item) for item in self._actions]

    async def get_actions_for_character(
        self,
        character_config: CharacterConfig | None,
    ) -> list[dict[str, Any]]:
        await self._ensure_initialized()
        return [self._copy_action(item) for item in self._filter_actions_for_character(self._actions, character_config)]

    async def get_prompt_actions(
        self,
        character_config: CharacterConfig | None,
        *,
        limit: int = _DEFAULT_PROMPT_ACTION_LIMIT,
    ) -> list[str]:
        actions = await self.get_actions_for_character(character_config)
        if not actions or limit <= 0:
            return []

        available_names = {item["action_tag"] for item in actions}
        preferred = [
            action
            for action in self._preferred_actions(character_config)
            if action in available_names
        ]

        selected: list[str] = []
        seen: set[str] = set()
        for action in preferred:
            if action not in seen:
                selected.append(action)
                seen.add(action)

        for item in actions:
            action = item["action_tag"]
            if action in seen:
                continue
            selected.append(action)
            seen.add(action)
            if len(selected) >= limit:
                break

        return selected[:limit]

    async def search_actions(
        self,
        query: str,
        *,
        character_config: CharacterConfig | None,
        limit: int = _DEFAULT_SEARCH_LIMIT,
    ) -> list[str]:
        await self._ensure_initialized()
        query = query.strip()
        if not query or limit <= 0:
            return []

        actions = self._filter_actions_for_character(self._actions, character_config)
        if not actions:
            return []

        preferred_names = set(self._preferred_actions(character_config))
        normalized_query = _normalize_text(query)
        query_embedding = None
        try:
            query_embedding = await get_embedding(query)
        except Exception as e:
            logger.warning("[MotionCatalog] 查询动作 embedding 失败: %s", e)

        scored: list[tuple[float, int, str]] = []
        for item in actions:
            lexical_bonus = self._lexical_bonus(normalized_query, item["normalized_action"])
            embedding_sim = 0.0
            if query_embedding and item.get("embedding"):
                embedding_sim = max(_cosine_similarity(query_embedding, item["embedding"]), 0.0)

            if lexical_bonus <= 0 and embedding_sim < _MIN_EMBEDDING_SIM:
                continue

            score = lexical_bonus + embedding_sim
            if item["action_tag"] in preferred_names:
                score += 0.25
            score += min(item["motion_count"], 10) * 0.01
            scored.append((score, item["motion_count"], item["action_tag"]))

        scored.sort(key=lambda value: (-value[0], -value[1], value[2]))
        return [action for _, _, action in scored[:limit]]

    async def _ensure_initialized(self):
        if self._initialized:
            return
        await self.initialize()

    async def _load_actions(self) -> list[dict[str, Any]]:
        action_map = aliased(MotionTagMap)
        action_tag = aliased(MotionTag)
        system_map = aliased(MotionTagMap)
        system_tag = aliased(MotionTag)

        system_exists = exists(
            select(1)
            .select_from(system_map)
            .join(system_tag, system_map.tag_id == system_tag.id)
            .where(
                system_map.motion_id == Motion.id,
                system_tag.tag_type == "system",
                system_tag.tag_name == "others",
            )
        )

        stmt = (
            select(
                Motion.id.label("motion_id"),
                Motion.display_name,
                Motion.description,
                action_tag.tag_name.label("action_tag"),
                action_tag.embedding.label("action_embedding"),
            )
            .join(action_map, action_map.motion_id == Motion.id)
            .join(action_tag, action_map.tag_id == action_tag.id)
            .where(
                Motion.status == "active",
                action_tag.tag_type == "action",
                system_exists,
            )
        )

        async with get_db_session() as session:
            rows = (await session.execute(stmt)).mappings().all()

        aggregated: dict[str, dict[str, Any]] = {}
        for row in rows:
            action_name = (row.get("action_tag") or "").strip()
            if not action_name:
                continue

            item = aggregated.setdefault(
                action_name,
                {
                    "action_tag": action_name,
                    "normalized_action": _normalize_text(action_name),
                    "motion_ids": set(),
                    "display_names": [],
                    "descriptions": [],
                    "embedding": list(row["action_embedding"]) if row.get("action_embedding") is not None else None,
                },
            )

            motion_id = str(row["motion_id"])
            if motion_id in item["motion_ids"]:
                continue
            item["motion_ids"].add(motion_id)

            display_name = (row.get("display_name") or "").strip()
            if display_name and display_name not in item["display_names"] and len(item["display_names"]) < 5:
                item["display_names"].append(display_name)

            description = (row.get("description") or "").strip()
            if description and description not in item["descriptions"] and len(item["descriptions"]) < 3:
                item["descriptions"].append(description)

            if item["embedding"] is None and row.get("action_embedding") is not None:
                item["embedding"] = list(row["action_embedding"])

        actions: list[dict[str, Any]] = []
        for item in aggregated.values():
            motion_ids = item.pop("motion_ids")
            item["motion_count"] = len(motion_ids)
            actions.append(item)

        actions.sort(key=lambda value: (-value["motion_count"], value["action_tag"]))
        return actions

    def _copy_action(self, item: dict[str, Any]) -> dict[str, Any]:
        return {
            "action_tag": item["action_tag"],
            "normalized_action": item["normalized_action"],
            "motion_count": item["motion_count"],
            "display_names": list(item["display_names"]),
            "descriptions": list(item["descriptions"]),
            "embedding": list(item["embedding"]) if item.get("embedding") else None,
        }

    def _filter_actions_for_character(
        self,
        actions: list[dict[str, Any]],
        character_config: CharacterConfig | None,
    ) -> list[dict[str, Any]]:
        if character_config is None:
            return actions

        prefs = character_config.motion_preferences
        allowed = {name for name in prefs.allowed_actions if name}
        blocked = {name for name in prefs.blocked_actions if name}

        filtered: list[dict[str, Any]] = []
        for item in actions:
            action_name = item["action_tag"]
            if blocked and action_name in blocked:
                continue
            if allowed and action_name not in allowed:
                continue
            filtered.append(item)
        return filtered

    def _preferred_actions(self, character_config: CharacterConfig | None) -> list[str]:
        if character_config is None:
            return []
        return [name for name in character_config.motion_preferences.preferred_actions if name]

    def _lexical_bonus(self, query: str, action: str) -> float:
        if not query or not action:
            return 0.0
        if query == action:
            return 3.0
        if action in query:
            return 2.4
        if query in action:
            return 1.6
        if query[:2] and action[:2] and query[:2] == action[:2]:
            return 0.8
        return 0.0


_motion_catalog_service: MotionCatalogService | None = None


def get_motion_catalog_service() -> MotionCatalogService:
    global _motion_catalog_service
    if _motion_catalog_service is None:
        _motion_catalog_service = MotionCatalogService()
    return _motion_catalog_service
