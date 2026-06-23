"""Tests for testagent.plan.port_allocator."""

from __future__ import annotations

from testagent.plan.port_allocator import PortAllocator


class TestPortAllocator:
    def test_allocate_returns_distinct_pairs(self) -> None:
        pa = PortAllocator()
        p1 = pa.allocate()
        p2 = pa.allocate()
        assert p1 != p2
        # Ports within each pair are distinct
        assert p1[0] != p1[1]
        assert p2[0] != p2[1]

    def test_allocate_rejects_duplicate_ports(self) -> None:
        pa = PortAllocator()
        p1 = pa.allocate()
        p2 = pa.allocate()
        # No shared ports
        ports = {p1[0], p1[1], p2[0], p2[1]}
        assert len(ports) == 4

    def test_release_frees_ports(self) -> None:
        pa = PortAllocator()
        p1 = pa.allocate()
        pa.release(p1)
        # Re-allocating should reuse one of the freed ports
        # (not guaranteed, but at least shouldn't error)
        p2 = pa.allocate()
        assert p2 is not None
