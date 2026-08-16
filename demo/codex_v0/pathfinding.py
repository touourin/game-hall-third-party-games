from __future__ import annotations

import heapq
from collections import deque
from collections.abc import Iterable


Cell = tuple[int, int]


# Furniture is deliberately represented as ordinary blocked tiles. The web
# client receives the same list from /api/bootstrap, so rendering and server
# collision always share one source of truth.
FURNITURE_BLOCKED: frozenset[Cell] = frozenset(
    {
        # Two desk islands.
        (3, 2),
        (4, 2),
        (5, 2),
        (3, 3),
        (4, 3),
        (5, 3),
        (14, 2),
        (15, 2),
        (16, 2),
        # Meeting table.
        (8, 4),
        (9, 4),
        (10, 4),
        (11, 4),
        (8, 5),
        (9, 5),
        (10, 5),
        (11, 5),
        # Kitchen counter and sofa.
        (2, 8),
        (3, 8),
        (4, 8),
        (5, 8),
        (14, 8),
        (15, 8),
        (16, 8),
        # Plants / cabinets.
        (1, 5),
        (18, 5),
        (7, 9),
        (12, 9),
    }
)


def in_bounds(cell: Cell, columns: int, rows: int) -> bool:
    return 0 <= cell[0] < columns and 0 <= cell[1] < rows


def neighbours(
    cell: Cell,
    columns: int,
    rows: int,
    blocked: frozenset[Cell],
) -> Iterable[Cell]:
    x, y = cell
    for candidate in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
        if in_bounds(candidate, columns, rows) and candidate not in blocked:
            yield candidate


def nearest_walkable(
    requested: Cell,
    columns: int,
    rows: int,
    blocked: frozenset[Cell],
) -> Cell | None:
    """Resolve furniture clicks to the closest reachable-looking floor tile."""

    start = (
        min(max(requested[0], 0), columns - 1),
        min(max(requested[1], 0), rows - 1),
    )
    if start not in blocked:
        return start
    queue: deque[Cell] = deque([start])
    seen = {start}
    while queue:
        x, y = queue.popleft()
        for candidate in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if candidate in seen or not in_bounds(candidate, columns, rows):
                continue
            if candidate not in blocked:
                return candidate
            seen.add(candidate)
            queue.append(candidate)
    return None


def astar(
    start: Cell,
    goal: Cell,
    *,
    columns: int,
    rows: int,
    blocked: frozenset[Cell] = FURNITURE_BLOCKED,
) -> list[Cell]:
    """Return a four-direction route including start and goal, or an empty list."""

    if (
        not in_bounds(start, columns, rows)
        or not in_bounds(goal, columns, rows)
        or start in blocked
        or goal in blocked
    ):
        return []
    if start == goal:
        return [start]

    frontier: list[tuple[int, int, Cell]] = [(0, 0, start)]
    came_from: dict[Cell, Cell] = {}
    cost: dict[Cell, int] = {start: 0}
    order = 0
    while frontier:
        _, _, current = heapq.heappop(frontier)
        if current == goal:
            route = [current]
            while current in came_from:
                current = came_from[current]
                route.append(current)
            route.reverse()
            return route
        for candidate in neighbours(current, columns, rows, blocked):
            candidate_cost = cost[current] + 1
            if candidate_cost >= cost.get(candidate, 1 << 30):
                continue
            cost[candidate] = candidate_cost
            came_from[candidate] = current
            order += 1
            heuristic = abs(candidate[0] - goal[0]) + abs(candidate[1] - goal[1])
            heapq.heappush(
                frontier,
                (candidate_cost + heuristic, order, candidate),
            )
    return []
