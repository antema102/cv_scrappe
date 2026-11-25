"""
Optimalité de la répartition du pool (spec §5.5.1) contre des références indépendantes d'account_pool :
- recherche exhaustive de toutes les attributions possibles (petits cas) ;
- flot à coût minimum / flot maximum (grands cas).
Les variantes dégradées (sans chemin libérant, sans équilibrage) doivent être prises en défaut : sinon ces tests ne
prouveraient rien. Graines fixes, ~25 s.

    python -m unittest discover -s facebook_script -p "test_pool_optimality.py" -v
"""
from __future__ import annotations

import random
import unittest
from collections import deque
from itertools import product

from account_pool import allocate, serve

INF = float("inf")
Case = tuple[list[str], list[str], dict[str, list[str]], dict[str, int | None]]  # comptes, groupes, membres, quotas


def _cap(caps: dict[str, int | None], account: str) -> float:
    return INF if caps[account] is None else caps[account]


def _make(n_acc: int, members_idx: list[list[int]], caps_idx: list[int | None]) -> Case:
    accounts = [f"a{i}" for i in range(n_acc)]
    groups = [f"g{j}" for j in range(len(members_idx))]  # g0 = le plus prioritaire
    members = {g: [accounts[i] for i in m] for g, m in zip(groups, members_idx)}
    return accounts, groups, members, dict(zip(accounts, caps_idx))


def random_case(rng: random.Random) -> Case:
    n_acc = rng.randint(1, 4)
    n_groups = rng.randint(1, 7)
    p = rng.choice([0.2, 0.4, 0.6, 0.8, 1.0])
    members_idx = []
    for _ in range(n_groups):
        m = [a for a in range(n_acc) if rng.random() < p]
        members_idx.append(m or [rng.randrange(n_acc)])  # un groupe sans membre n'entre pas dans la répartition
    caps_idx = [rng.choice([None, 0, 1, 1, 2, 2, 3, 4]) for _ in range(n_acc)]
    return _make(n_acc, members_idx, caps_idx)


def country_case(rng: random.Random, max_acc: int = 5, max_groups: int = 8) -> Case:
    n_acc = rng.randint(2, max_acc)
    n_countries = rng.randint(1, 3)
    n_groups = rng.randint(2, max_groups)
    country = sorted(rng.randrange(n_countries) for _ in range(n_groups))
    member_of = [[rng.random() < 0.6 for _ in range(n_countries)] for _ in range(n_acc)]
    if rng.random() < 0.5:
        member_of[0] = [True] * n_countries  # « compte historique » membre de tout
    members_idx = []
    for g in range(n_groups):
        m = [a for a in range(n_acc) if member_of[a][country[g]] and rng.random() < 0.85]
        members_idx.append(m or [rng.randrange(n_acc)])
    caps_idx = [rng.choice([None, None, 0, 1, 2, 3, 5]) for _ in range(n_acc)]
    return _make(n_acc, members_idx, caps_idx)


def exhaustive(case: Case) -> tuple[int, int, tuple[int, ...], int, int]:
    """
    Toutes les attributions (groupe -> un membre ou non servi), quotas respectés. Renvoie : masque du meilleur ensemble
    servi par priorité (g0 = bit de poids fort) ; pour cet ensemble : charge max minimale, vecteur des charges trié
    décroissant minimal, somme des carrés minimale ; nombre maximal de groupes servis.
    """
    accounts, groups, members, caps = case
    n = len(groups)
    best_mask, best_max, best_vec, best_sq, max_count = -1, 0, (), 0, 0
    for combo in product(*([None, *members[g]] for g in groups)):
        load = dict.fromkeys(accounts, 0)
        ok = True
        for account in combo:
            if account is not None:
                load[account] += 1
                if load[account] > _cap(caps, account):
                    ok = False
                    break
        if not ok:
            continue
        mask = sum(1 << (n - 1 - j) for j, a in enumerate(combo) if a is not None)
        max_count = max(max_count, sum(a is not None for a in combo))
        loads = list(load.values())
        vec, sq, mx = tuple(sorted(loads, reverse=True)), sum(x * x for x in loads), max(loads)
        if mask > best_mask:
            best_mask, best_max, best_vec, best_sq = mask, mx, vec, sq
        elif mask == best_mask:
            best_max, best_vec, best_sq = min(best_max, mx), min(best_vec, vec), min(best_sq, sq)
    return best_mask, best_max, best_vec, best_sq, max_count


def min_square_flow(case: Case, served: list[str], limit: int | None = None) -> tuple[int, int]:
    """
    Flot à coût minimum (plus courts chemins successifs, SPFA) : source -> groupe (1) -> membre (1) -> puits, le k-ième
    groupe d'un compte coûtant 2k - 1 (coût total = somme des carrés des charges). Quotas bornés par `limit` si donné.
    Renvoie (flot, coût).
    """
    accounts, _, members, caps = case
    g_count, a_count = len(served), len(accounts)
    source, sink = 0, g_count + a_count + 1
    graph: list[list[list[int]]] = [[] for _ in range(sink + 1)]

    def add(u: int, v: int, capacity: int, cost: int) -> None:
        graph[u].append([v, capacity, cost, len(graph[v])])
        graph[v].append([u, 0, -cost, len(graph[u]) - 1])

    index = {a: i for i, a in enumerate(accounts)}
    for i, group in enumerate(served):
        add(source, 1 + i, 1, 0)
        for account in members[group]:
            add(1 + i, 1 + g_count + index[account], 1, 0)
    for account in accounts:
        bound = g_count if caps[account] is None else min(caps[account], g_count)
        if limit is not None:
            bound = min(bound, limit)
        for k in range(bound):
            add(1 + g_count + index[account], sink, 1, 2 * k + 1)
    flow = cost = 0
    while True:
        dist = [INF] * (sink + 1)
        previous: list[tuple[int, int] | None] = [None] * (sink + 1)
        queued = [False] * (sink + 1)
        dist[source] = 0
        queue = deque([source])
        while queue:
            u = queue.popleft()
            queued[u] = False
            for idx, (v, capacity, w, _) in enumerate(graph[u]):
                if capacity > 0 and dist[u] + w < dist[v]:
                    dist[v], previous[v] = dist[u] + w, (u, idx)
                    if not queued[v]:
                        queued[v] = True
                        queue.append(v)
        if dist[sink] == INF:
            return flow, cost
        v = sink
        while v != source:
            u, idx = previous[v]
            graph[u][idx][1] -= 1
            graph[v][graph[u][idx][3]][1] += 1
            v = u
        flow += 1
        cost += int(dist[sink])


def min_max_flow(case: Case, served: list[str]) -> int:
    """Plus petite charge maximale T telle que tous les groupes de `served` passent (quotas min(cap, T))."""
    for limit in range(len(served) + 1):
        if min_square_flow(case, served, limit)[0] == len(served):
            return limit
    raise AssertionError("ensemble servi impossible")


def served_mask(groups: list[str], owner: dict[str, str | None]) -> int:
    n = len(groups)
    return sum(1 << (n - 1 - j) for j, g in enumerate(groups) if owner.get(g) is not None)


def loads_of(accounts: list[str], owner: dict[str, str | None]) -> list[int]:
    return [sum(1 for a in owner.values() if a == account) for account in accounts]


class OptimalityTest(unittest.TestCase):
    def assert_matches_exhaustive(self, case: Case) -> None:
        accounts, groups, members, caps = case
        result = allocate(groups, members, caps, accounts)
        owner = {g: result.owner.get(g) for g in groups}
        loads = [result.loads[a] for a in accounts]
        mask, best_max, best_vec, best_sq, max_count = exhaustive(case)
        context = f"cas {case}"
        self.assertEqual(served_mask(groups, owner), mask, context)
        self.assertEqual(len(result.owner), max_count, context)
        self.assertEqual(max(loads), best_max, context)
        self.assertEqual(tuple(sorted(loads, reverse=True)), best_vec, context)
        self.assertEqual(sum(x * x for x in loads), best_sq, context)
        for account in accounts:
            self.assertLessEqual(result.loads[account], _cap(caps, account), context)
        for group, account in result.owner.items():
            self.assertIn(account, members[group], context)

    def test_random_cases_match_exhaustive(self) -> None:
        for seed in (1, 2, 3):
            rng = random.Random(seed)
            for _ in range(3000):
                self.assert_matches_exhaustive(random_case(rng))

    def test_country_cases_match_exhaustive(self) -> None:
        rng = random.Random(7)
        for _ in range(1500):
            self.assert_matches_exhaustive(country_case(rng))

    def test_large_cases_match_flow(self) -> None:
        rng = random.Random(7)
        for _ in range(1500):
            country_case(rng)  # même suite de tirages que la vérification de la spec
        for _ in range(400):
            case = country_case(rng, max_acc=8, max_groups=40)
            accounts, groups, members, caps = case
            result = allocate(groups, members, caps, accounts)
            served = [g for g in groups if g in result.owner]
            loads = [result.loads[a] for a in accounts]
            self.assertEqual(len(served), min_square_flow(case, groups)[0], f"cas {case}")
            self.assertEqual(max(loads), min_max_flow(case, served), f"cas {case}")
            self.assertEqual(sum(x * x for x in loads), min_square_flow(case, served)[1], f"cas {case}")

    def test_degraded_variants_are_caught(self) -> None:
        wrong_set = too_high = 0
        for seed in (1, 2, 3):
            rng = random.Random(seed)
            for _ in range(3000):
                case = random_case(rng)
                accounts, groups, members, caps = case
                mask, best_max, *_ = exhaustive(case)
                if served_mask(groups, serve(groups, members, caps, accounts, use_paths=False)) != mask:
                    wrong_set += 1
                unbalanced = serve(groups, members, caps, accounts)
                if served_mask(groups, unbalanced) == mask and max(loads_of(accounts, unbalanced)) != best_max:
                    too_high += 1
        self.assertGreater(wrong_set, 0, "la variante sans chemin libérant n'est jamais prise en défaut")
        self.assertGreater(too_high, 0, "la variante sans équilibrage n'est jamais prise en défaut")


if __name__ == "__main__":
    unittest.main()
