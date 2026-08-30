"""P1 -- provider registry dispatch and the Adani byte-identical regression."""
from __future__ import annotations

from typing import ClassVar

import pytest

from berth_truth.models import PortId
from berth_truth.providers import (
    Provider,
    RawCapture,
    UnknownAdapterError,
    get_provider,
)
from berth_truth.sources import PORT_SOURCES, AdapterKind
from berth_truth.store import list_snapshot_hashes, load_snapshot
from opt.network import PortEnum

_ALL_KINDS = list(AdapterKind)


class TestProtocolConformance:
    @pytest.mark.parametrize("kind", _ALL_KINDS)
    def test_every_adapter_kind_resolves_to_a_provider(self, kind: AdapterKind) -> None:
        provider = get_provider(kind)
        assert isinstance(provider, Provider)

    @pytest.mark.parametrize("kind", _ALL_KINDS)
    def test_provider_supports_its_own_kind(self, kind: AdapterKind) -> None:
        provider = get_provider(kind)
        # Build a minimal PortSource of this kind to check supports().
        source = next(
            (s for sources in PORT_SOURCES.values() for s in sources if s.adapter is kind), None
        )
        if source is not None:
            assert provider.supports(source) is True

    def test_unknown_adapter_kind_raises_not_silently_skips(self) -> None:
        class _NotARealKind:
            pass

        with pytest.raises(UnknownAdapterError):
            get_provider(_NotARealKind())  # type: ignore[arg-type]


class TestAdaniPathByteIdentical:
    """The regression guarantee P1 requires: the 3 pre-existing captures in
    raw_data/berth_truth/ must produce identical parsed output through the
    new registry-dispatched path. Compared via Pydantic object equality
    (frozen model __eq__), not JSON-string equality -- frozenset-typed
    fields (tables_present, observed_berth_ids) do not guarantee stable
    JSON array order across separate serialisation calls even when the
    underlying value is unchanged; confirmed directly (a JSON-string
    comparison on real data produced a false-positive mismatch purely from
    reordering, verified by then comparing the same two objects with `==`
    and finding them equal).
    """

    _PORT_MAP: ClassVar[dict[PortId, PortEnum]] = {
        PortId.DHAMRA: PortEnum.DHAMRA, PortId.GANGAVARAM: PortEnum.GANGAVARAM,
    }

    def test_all_three_existing_captures_match(self) -> None:
        provider = get_provider(AdapterKind.HTML_TABLE)
        checked = 0
        for port_id, port_enum in self._PORT_MAP.items():
            source = PORT_SOURCES[port_enum][0]
            for h in list_snapshot_hashes(port_id):
                old = load_snapshot(port_id, h)
                capture = RawCapture(
                    source_url=source.source_url, retrieved_at=old.fetched_at,
                    doc_published_date=None, content_sha256=h,
                    parser_version="adani_schedule/1", is_new_content=False,
                )
                result = provider.parse(source, capture)
                assert result.schedule_snapshot == old, f"mismatch for {port_id.value} {h[:12]}"
                checked += 1
        assert checked == 3, f"expected to check all 3 known fixtures, checked {checked}"
