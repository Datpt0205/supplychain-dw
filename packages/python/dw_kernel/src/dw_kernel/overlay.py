"""Two-layer lookup: a tenant's own version of a thing, else the platform's.

This is the shape that lets one deployment serve many customers whose processes
differ, without a branch per customer. A prompt, a tool spec, a toolset, a model
profile, a policy — each has a platform default, and a tenant may have its own.
Resolution asks for the tenant's first and falls back; nothing else in the code
needs to know whether an override exists.

Why it lives in the kernel, and why it exists at all as a named thing: the
alternative is each registry growing its own ``if tenant in self._overrides``,
which is four copies of one rule and therefore four rules that drift. And the
alternative to having it *now* is worse. Resolution signatures are a contract
with every bounded context built on this platform; adding a tenant argument
later means editing every call site in every product, which is the kind of
change nobody makes and everybody works around.

A ``None`` tenant means the platform layer — the default that ships with the
repo, versioned in ``configs/``. A concrete tenant id means an override, which
in a deployment is loaded from storage rather than from the checkout.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from uuid import UUID


@dataclass
class TenantOverlay[K, V]:
    """A platform layer plus zero or more tenant layers over the same keys."""

    _platform: dict[K, V] = field(default_factory=dict)
    _tenants: dict[UUID, dict[K, V]] = field(default_factory=dict)

    def existing(self, key: K, *, tenant_id: UUID | None = None) -> V | None:
        """What is already registered in *this* layer, ignoring fallback.

        Registration uses this rather than ``get`` so that a tenant may define
        an artifact the platform also defines — that is the whole point of an
        override — while a genuine double-registration inside one layer is still
        refused by the caller with its own message.
        """
        return self._layer(tenant_id).get(key)

    def put(self, key: K, value: V, *, tenant_id: UUID | None = None) -> None:
        self._layer(tenant_id)[key] = value

    def get(self, key: K, *, tenant_id: UUID | None = None) -> V | None:
        """The tenant's version if it has one, otherwise the platform's."""
        if tenant_id is not None:
            own = self._tenants.get(tenant_id, {}).get(key)
            if own is not None:
                return own
        return self._platform.get(key)

    def platform_values(self) -> Iterator[V]:
        """Only the platform layer.

        Used where the answer must be the same for everyone — the release
        manifest, and the operator-facing list of what this deployment can do.
        A per-tenant answer there would mean an operator's inventory changed
        depending on whose request happened to ask.
        """
        return iter(self._platform.values())

    def tenant_values(self, tenant_id: UUID) -> Iterator[V]:
        """Only that tenant's own layer, never another's: what a resolver that
        picks among versions (a range, not one key) reads before falling back
        to `platform_values`."""
        return iter(self._tenants.get(tenant_id, {}).values())

    def _layer(self, tenant_id: UUID | None) -> dict[K, V]:
        if tenant_id is None:
            return self._platform
        return self._tenants.setdefault(tenant_id, {})
