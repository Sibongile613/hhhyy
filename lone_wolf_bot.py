"""
Lone Wolf Labs bot v14 - NO MERCY+
for Builders - Mech Royale (mechfight/1).

Based on v13, which scored 3.58.

More ruthless changes:
- Hunts cube carriers harder.
- Commits to damaged enemies earlier.
- Closes distance more aggressively when healthy.
- Boosts to finish weakened targets.
- Still avoids starting fair fights while damaged.
- Still disengages only when outnumbered.
"""

from __future__ import annotations

import array
import json
import math
import os
import random
import sys
from typing import Any, Dict, List, Optional, Tuple

import mechfight as mf


def _wrap_angle(angle: float) -> float:
    while angle > math.pi:
        angle -= 2.0 * math.pi
    while angle < -math.pi:
        angle += 2.0 * math.pi
    return angle


def _dist2d(a: List[float], b: List[float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


NO_MERCY = True


class Terrain:
    def __init__(self, map_dir: str) -> None:
        self.ok = False
        try:
            base = os.path.join(map_dir, "v2")
            with open(os.path.join(base, "world.json"), "r", encoding="utf-8") as f:
                meta = json.load(f)["terrain"]
            self.min_x = float(meta["minX"])
            self.min_y = float(meta["minY"])
            self.step = float(meta["step"])
            self.nx = int(meta["nx"])
            self.ny = int(meta["ny"])
            data = array.array("h")
            with open(os.path.join(base, str(meta.get("file", "terrain.i16"))), "rb") as f:
                data.fromfile(f, self.nx * self.ny)
            if sys.byteorder == "big":
                data.byteswap()
            self.data = data
            self.ok = True
        except Exception:
            self.ok = False

    def ground(self, x: float, y: float) -> Optional[float]:
        if not self.ok:
            return None
        i = max(0, min(self.nx - 1, int(round((x - self.min_x) / self.step))))
        j = max(0, min(self.ny - 1, int(round((y - self.min_y) / self.step))))
        return self.data[j * self.nx + i] / 100.0


class Geo:
    """Building footprints and a 3D line-of-sight test."""

    def __init__(self, map_dir: str) -> None:
        self.ok = False
        self.prisms: List[Tuple[float, float, float, float, float, float, List[float]]] = []

        try:
            with open(os.path.join(map_dir, "v2", "world.json"), "r", encoding="utf-8") as f:
                data = json.load(f)

            for prism in data.get("prisms", []):
                base = float(prism.get("base", 0.0))
                top = float(prism.get("top", 10.0))

                if top - base < 1.5:
                    continue

                ring = prism.get("ring", [])

                if len(ring) < 6:
                    continue

                xs = ring[0::2]
                ys = ring[1::2]

                self.prisms.append(
                    (
                        min(xs),
                        min(ys),
                        max(xs),
                        max(ys),
                        base,
                        top,
                        ring,
                    )
                )

            self.ok = len(self.prisms) > 0

        except Exception:
            self.ok = False

    @staticmethod
    def _inside(px: float, py: float, ring: List[float]) -> bool:
        count = len(ring) // 2
        inside = False
        previous = count - 1

        for index in range(count):
            xi = ring[2 * index]
            yi = ring[2 * index + 1]
            xj = ring[2 * previous]
            yj = ring[2 * previous + 1]

            if ((yi > py) != (yj > py)) and (
                px
                < (xj - xi) * (py - yi) / ((yj - yi) or 1e-12)
                + xi
            ):
                inside = not inside

            previous = index

        return inside

    def blocked(
        self,
        ax: float,
        ay: float,
        az: float,
        bx: float,
        by: float,
        bz: float,
    ) -> bool:
        low_x = min(ax, bx)
        high_x = max(ax, bx)
        low_y = min(ay, by)
        high_y = max(ay, by)

        delta_x = bx - ax
        delta_y = by - ay

        for prism in self.prisms:
            x0, y0, x1, y1, base, top, ring = prism

            if x1 < low_x or x0 > high_x or y1 < low_y or y0 > high_y:
                continue

            intersections = [0.0, 1.0]
            count = len(ring) // 2

            for index in range(count):
                previous = index - 1

                current_x = ring[2 * index]
                current_y = ring[2 * index + 1]
                previous_x = ring[2 * previous]
                previous_y = ring[2 * previous + 1]

                edge_x = current_x - previous_x
                edge_y = current_y - previous_y

                denominator = delta_x * edge_y - delta_y * edge_x

                if abs(denominator) < 1e-12:
                    continue

                intersection_t = (
                    (previous_x - ax) * edge_y
                    - (previous_y - ay) * edge_x
                ) / denominator

                intersection_u = (
                    (previous_x - ax) * delta_y
                    - (previous_y - ay) * delta_x
                ) / denominator

                if 0.0 <= intersection_t <= 1.0 and 0.0 <= intersection_u <= 1.0:
                    intersections.append(intersection_t)

            intersections.sort()

            for index in range(len(intersections) - 1):
                start_t = intersections[index]
                end_t = intersections[index + 1]

                if end_t - start_t < 1e-9:
                    continue

                middle_t = 0.5 * (start_t + end_t)

                if not self._inside(
                    ax + delta_x * middle_t,
                    ay + delta_y * middle_t,
                    ring,
                ):
                    continue

                start_z = az + (bz - az) * start_t
                end_z = az + (bz - az) * end_t

                if max(start_z, end_z) >= base and min(start_z, end_z) <= top:
                    return True

        return False


class Bot:
    name = "lone-wolf-v14-no-mercy-plus"

    def __init__(self, hello: mf.Hello) -> None:
        config = hello.get("config", {})
        loot_config = config.get("loot", {})

        self.pickup_radius = float(loot_config.get("pickup_radius", 4.0))
        self.upgrades = config.get("upgrades", [])
        self.upgrade_priority = {}

        for upgrade in self.upgrades:
            upgrade_id = str(upgrade.get("id", ""))
            effects = upgrade.get("effects", [])
            score = 0

            for effect in effects:
                path = str(effect.get("path", ""))

                if "damage" in path or "rate" in path or "cooldown" in path:
                    score += 35
                if "speed" in path or "boost" in path or "energy" in path:
                    score += 25
                if "armor" in upgrade_id or "rear" in path or "max_hp" in path or "regen" in path:
                    score += 20
                if "sensor" in upgrade_id or "vision" in path or "sound" in path:
                    score += 15

            self.upgrade_priority[upgrade_id] = score

        self.rng = random.Random(int(hello.get("seed", 1234567)))
        self.last_damage_t = -999.0
        self.last_damage_bearing = None
        self.laser_charging = [False, False]
        self.terrain = Terrain(str(hello.get("map", {}).get("dir", "/arena")))
        self.skip_pickups = {}
        self.loot_mem = None
        self.last_pos = None
        self.stuck_ticks = 0
        self._strafe_dir = self.rng.choice([-1.0, 1.0])
        self._strafe_until = 0.0
        self._hunt_target_id = None
        self._hunt_until = 0.0
        self.geo = Geo(str(hello.get("map", {}).get("dir", "/arena")))
        self.unstick_until = -999.0
        self.unstick_sign = 1.0
        self.skip_crates: Dict[int, float] = {}
        self.crate_id = None
        self.crate_since = 0.0
        self.last_kill_t = -999.0

    @staticmethod
    def _wrank(kind: Optional[str]) -> int:
        return {
            "mortar": 1,
            "lightning": 2,
            "laser": 2,
        }.get(str(kind), 0)

    def _pick_upgrade(self, offer: Dict[str, Any]) -> int:
        choices = offer.get("choices", [])

        if not choices:
            return 0

        best_index = 0
        best_score = -10**9

        for index, choice in enumerate(choices):
            upgrade_id = str(choice.get("id", ""))
            description = str(choice.get("desc", "")).lower()
            score = self.upgrade_priority.get(upgrade_id, 0)

            if "damage" in description or "rate" in description or "cooldown" in description:
                score += 30
            if "speed" in description or "energy" in description or "boost" in description:
                score += 20
            if "armor" in description or "hp" in description or "regen" in description:
                score += 15
            if "vision" in description or "sensor" in description or "sound" in description:
                score += 10

            if score > best_score:
                best_score = score
                best_index = index

        return best_index

    def _zone_target(self, obs: mf.Observation, x: float, y: float) -> Tuple[float, float]:
        zone = obs["zone"]
        t = float(obs["t"])
        shrink_start = float(zone.get("shrink_start", t))
        shrink_end = float(zone.get("shrink_end", t + 1.0))
        current_center = zone.get("center", [0.0, 0.0])
        current_radius = float(zone.get("radius", 500.0))
        next_center = zone.get("next_center", current_center)
        next_radius = float(zone.get("next_radius", current_radius))

        if t >= shrink_start:
            if shrink_end > shrink_start:
                progress = max(
                    0.0,
                    min(1.0, (t - shrink_start) / (shrink_end - shrink_start)),
                )
            else:
                progress = 1.0

            center_x = current_center[0] + (next_center[0] - current_center[0]) * progress
            center_y = current_center[1] + (next_center[1] - current_center[1]) * progress
            radius = current_radius + (next_radius - current_radius) * progress
        else:
            center_x, center_y, radius = current_center[0], current_center[1], current_radius

        safe_radius = max(5.0, radius * 0.82)
        delta_x = next_center[0] - center_x
        delta_y = next_center[1] - center_y
        delta_length = math.hypot(delta_x, delta_y)

        if delta_length > 1e-6:
            target_x = center_x + (delta_x / delta_length) * min(
                delta_length * 0.75,
                safe_radius * 0.55,
            )
            target_y = center_y + (delta_y / delta_length) * min(
                delta_length * 0.75,
                safe_radius * 0.55,
            )
        else:
            target_x, target_y = center_x, center_y

        current_distance = math.hypot(x - center_x, y - center_y)
        next_distance = math.hypot(x - next_center[0], y - next_center[1])

        if current_distance < safe_radius and next_distance > next_radius * 0.90:
            return float(next_center[0]), float(next_center[1])

        return float(target_x), float(target_y)

    def _fire_decision(
        self,
        slot: int,
        weapon: Optional[mf.WeaponObs],
        distance: float,
        has_target: bool,
        energy: float,
        target_has_mortar: bool,
    ) -> bool:
        if weapon is None or not has_target:
            self.laser_charging[slot] = False
            return False

        kind = weapon.get("kind")
        stats = weapon.get("stats", {})
        max_range = float(stats.get("range", 200.0))

        if kind == "gun":
            ammo = float(weapon.get("ammo", 0.0))
            reload_left = float(weapon.get("reload", 0.0))

            if reload_left > 0.0 or ammo <= 0.0:
                return False

            return distance <= max_range * 0.98

        if kind == "lightning":
            if energy < 10.0 or distance > min(50.0, max_range):
                return False

            return float(weapon.get("cooldown", 0.0)) <= 0.05

        if kind == "mortar":
            if distance < 18.0 or distance > max_range:
                return False

            return float(weapon.get("cooldown", 0.0)) <= 0.05

        if kind == "laser":
            charge_time = float(weapon.get("charge", 0.0))
            full_charge = float(stats.get("charge", 0.75))

            if distance > max_range:
                self.laser_charging[slot] = False
                return False

            release_charge = full_charge * (0.80 if target_has_mortar else 0.95)

            if not self.laser_charging[slot]:
                if float(weapon.get("cooldown", 0.0)) <= 0.05 and energy > 12.0:
                    self.laser_charging[slot] = True
                    return True

                return False

            if charge_time >= release_charge:
                self.laser_charging[slot] = False
                return False

            return True

        return False

    def _zone_circle(self, obs: mf.Observation) -> Tuple[float, float, float]:
        zone = obs["zone"]
        t = float(obs["t"])
        shrink_start = float(zone.get("shrink_start", t))
        shrink_end = float(zone.get("shrink_end", t + 1.0))
        current_center = zone.get("center", [0.0, 0.0])
        current_radius = float(zone.get("radius", 500.0))
        next_center = zone.get("next_center", current_center)
        next_radius = float(zone.get("next_radius", current_radius))

        if t >= shrink_start:
            progress = max(
                0.0,
                min(
                    1.0,
                    (t - shrink_start) / max(1e-6, shrink_end - shrink_start),
                ),
            )

            center_x = current_center[0] + (next_center[0] - current_center[0]) * progress
            center_y = current_center[1] + (next_center[1] - current_center[1]) * progress
            radius = current_radius + (next_radius - current_radius) * progress

            return float(center_x), float(center_y), float(radius)

        return float(current_center[0]), float(current_center[1]), current_radius

    def act(self, obs: mf.Observation) -> mf.Action:
        me = obs["self"]
        x, y, z = float(me["pos"][0]), float(me["pos"][1]), float(me["pos"][2])
        hp = float(me.get("hp", 1400.0))
        max_hp = max(1.0, float(me.get("max_hp", 1400.0)))
        hp_ratio = hp / max_hp
        energy = float(me.get("energy", 0.0))
        in_zone = bool(me.get("in_zone", True))
        t = float(obs["t"])
        my_stats = me.get("stats", {})
        muzzle_z = z + float(my_stats.get("muzzle_height", 7.0))

        for event in obs.get("events", []):
            if event.get("type") == "damaged":
                self.last_damage_t = t

                if event.get("bearing") is not None:
                    self.last_damage_bearing = float(event["bearing"])

            elif event.get("type") == "take_failed":
                self.skip_pickups[int(event.get("id", -1))] = t + 25.0

            elif event.get("type") == "kill":
                self.last_kill_t = t

        if self.last_pos is not None:
            moved = math.hypot(x - self.last_pos[0], y - self.last_pos[1])

            if moved < 0.12:
                self.stuck_ticks += 1
            else:
                self.stuck_ticks = 0
        else:
            self.stuck_ticks = 0

        self.last_pos = (x, y)

        action: mf.Action = {"tick": obs["tick"]}

        if obs.get("offer"):
            action["pick"] = self._pick_upgrade(obs["offer"])  # type: ignore[arg-type]

        weapons = me.get("weapons", [None, None])
        gun_slot = weapons[0] if len(weapons) > 0 and weapons[0] and weapons[0].get("kind") == "gun" else None

        bullet_speed = 252.0

        if gun_slot is not None:
            bullet_speed = max(
                1.0,
                float(gun_slot.get("stats", {}).get("speed", 252.0)),
            )

            ammo = float(gun_slot.get("ammo", 0.0))
            magazine = float(gun_slot.get("stats", {}).get("mag", 12.0))
            reload_left = float(gun_slot.get("reload", 0.0))

            if reload_left <= 0.0 and ammo <= max(1.0, magazine * 0.20):
                action["reload"] = True

        pickups = obs.get("pickups", [])

        if len(weapons) > 1 and weapons[1] is None:
            for pickup in pickups:
                if pickup.get("kind") in ("laser", "lightning", "mortar"):
                    if _dist2d(pickup["pos"], [x, y]) <= self.pickup_radius:
                        action["take"] = pickup["id"]
                        break

        elif len(weapons) > 1 and weapons[1] is not None:
            current_secondary = weapons[1]

            if not (
                current_secondary.get("kind") == "laser"
                and float(current_secondary.get("charge", 0.0)) > 0.0
            ):
                for pickup in pickups:
                    pickup_kind = pickup.get("kind")

                    if pickup_kind not in ("laser", "lightning"):
                        continue

                    if self._wrank(pickup_kind) <= self._wrank(current_secondary.get("kind")):
                        continue

                    if pickup_kind == current_secondary.get("kind"):
                        continue

                    if pickup_kind == (weapons[0] or {}).get("kind"):
                        continue

                    if self.skip_pickups.get(int(pickup["id"]), -1.0) > t:
                        continue

                    if _dist2d(pickup["pos"], [x, y]) <= self.pickup_radius:
                        action["take"] = pickup["id"]
                        action["drop"] = 1
                        break

        mechs = obs.get("mechs", [])
        enemies = [mech for mech in mechs if mech.get("kind") == "mech"]

        try:
            zone_cx, zone_cy, zone_radius = self._zone_circle(obs)
        except Exception:
            zone = obs["zone"]
            zone_cx = float(zone.get("center", [0.0, 0.0])[0])
            zone_cy = float(zone.get("center", [0.0, 0.0])[1])
            zone_radius = float(zone.get("radius", 500.0))

        zone_center = [zone_cx, zone_cy]
        zone_margin = min(
            max(15.0, 0.08 * zone_radius),
            0.6 * zone_radius,
        )

        nearest_distance = min(
            (
                math.hypot(enemy["pos"][0] - x, enemy["pos"][1] - y)
                for enemy in enemies
            ),
            default=999.0,
        )

        recently_hit = (t - self.last_damage_t) < 4.0
        critical_hp = hp_ratio < 0.18
        post_kill = (t - self.last_kill_t) < 12.0
        loot_clear = 35.0 if post_kill else 60.0

        target = None

        if enemies:
            def kill_score(enemy: Dict[str, Any]) -> float:
                distance = math.dist(enemy["pos"], [x, y, z])
                enemy_hp = float(enemy.get("hp", 1400.0))
                enemy_max_hp = max(1.0, float(enemy.get("max_hp", 1400.0)))
                enemy_hp_ratio = enemy_hp / enemy_max_hp
                cubes = int(enemy.get("cubes", 0))

                score = distance + enemy_hp_ratio * 70.0 - cubes * 12.0

                if enemy_hp <= 750.0:
                    score -= 55.0

                try:
                    target_aim = float(enemy.get("aim", [0.0, 0.0])[0])
                    bearing_to_me = math.atan2(
                        y - float(enemy["pos"][1]),
                        x - float(enemy["pos"][0]),
                    )

                    if abs(_wrap_angle(target_aim - bearing_to_me)) > 1.2:
                        score -= 35.0

                except Exception:
                    pass

                return score

            target = min(enemies, key=kill_score)

            # Do not start a fair 1v1 against a healthy enemy.
            try:
                target_hp = float(target.get("hp", 1400.0))
                target_max_hp = max(1.0, float(target.get("max_hp", 1400.0)))
                target_hp_ratio = target_hp / target_max_hp

                target_zone_distance = math.hypot(
                    float(target["pos"][0]) - zone_cx,
                    float(target["pos"][1]) - zone_cy,
                )

                target_distracted = False

                try:
                    target_aim = float(target.get("aim", [0.0, 0.0])[0])
                    bearing_to_me = math.atan2(
                        y - float(target["pos"][1]),
                        x - float(target["pos"][0]),
                    )

                    if abs(_wrap_angle(target_aim - bearing_to_me)) > 1.0:
                        target_distracted = True

                except Exception:
                    target_distracted = False

                target_zone_trapped = target_zone_distance > zone_radius * 0.82

                fair_fight = (
                    target_hp_ratio >= 0.65
                    and not target_distracted
                    and not target_zone_trapped
                    and hp_ratio < 0.85
                )

                if fair_fight:
                    target = None

            except Exception:
                pass

            if target is not None:
                if self._hunt_target_id is None or t > self._hunt_until:
                    self._hunt_target_id = target.get("id")
                    self._hunt_until = t + 2.0

                for enemy in enemies:
                    if enemy.get("id") == self._hunt_target_id:
                        target = enemy
                        break

        finisher = False

        if target is not None:
            try:
                target_distance = math.hypot(
                    target["pos"][0] - x,
                    target["pos"][1] - y,
                )

                finisher = (
                    float(target.get("hp", 1400.0)) <= 750.0
                    and target_distance <= 110.0
                    and hp_ratio >= 0.0
                )

            except Exception:
                finisher = False

            if finisher:
                self._hunt_target_id = target.get("id")
                self._hunt_until = max(self._hunt_until, t + 5.0)

        loot_target = None
        loot_distance = 0.0

        secondary_kind = (
            weapons[1].get("kind")
            if len(weapons) > 1 and weapons[1] is not None
            else None
        )

        secondary_swappable = secondary_kind is not None and not (
            secondary_kind == "laser"
            and float(weapons[1].get("charge", 0.0)) > 0.0
        )

        if len(weapons) > 1 and in_zone and (
            weapons[1] is None or secondary_swappable
        ):
            held_weapons = {weapon.get("kind") for weapon in weapons if weapon}
            best_score = -1e9

            for pickup in pickups:
                pickup_kind = pickup.get("kind")

                if pickup_kind not in ("laser", "lightning", "mortar"):
                    continue

                if pickup_kind in held_weapons:
                    continue

                if secondary_kind is not None and self._wrank(pickup_kind) <= self._wrank(secondary_kind):
                    continue

                if self.skip_pickups.get(int(pickup["id"]), -1.0) > t:
                    continue

                ground = self.terrain.ground(
                    float(pickup["pos"][0]),
                    float(pickup["pos"][1]),
                )

                if ground is not None and float(pickup["pos"][2]) - ground > 2.0:
                    continue

                distance = _dist2d(pickup["pos"], [x, y])

                if distance > 250.0:
                    continue

                value = {
                    "laser": 55.0,
                    "lightning": 35.0,
                    "mortar": 15.0,
                }[pickup_kind]

                score = value - 0.22 * distance

                if nearest_distance < 45.0:
                    score -= 25.0

                if score > best_score:
                    best_score = score
                    loot_target = pickup
                    loot_distance = distance

        if loot_target is not None:
            self.loot_mem = {
                "id": int(loot_target["id"]),
                "kind": loot_target["kind"],
                "pos": [
                    float(loot_target["pos"][0]),
                    float(loot_target["pos"][1]),
                    float(loot_target["pos"][2]),
                ],
                "seen": t,
            }
        elif self.loot_mem is not None:
            memory = self.loot_mem
            memory_distance = _dist2d(memory["pos"], [x, y])

            gone = (
                len(weapons) <= 1
                or (
                    weapons[1] is not None
                    and self._wrank(memory["kind"]) <= self._wrank(weapons[1].get("kind"))
                )
                or not in_zone
                or t - memory["seen"] > 7.0
                or memory_distance < 35.0
                or self.skip_pickups.get(memory["id"], -1.0) > t
            )

            if gone:
                self.loot_mem = None
            else:
                loot_target = memory
                loot_distance = memory_distance

        if loot_target is not None and self.stuck_ticks >= 10:
            self.skip_pickups[int(loot_target["id"])] = t + 12.0
            self.loot_mem = None
            loot_target = None

        zone_x, zone_y = self._zone_target(obs, x, y)

        move_x = zone_x - x
        move_y = zone_y - y
        move_mode = "zone_advance"

        my_zone_distance = math.hypot(
            x - zone_center[0],
            y - zone_center[1],
        )

        if not in_zone or my_zone_distance >= zone_radius - zone_margin:
            move_x = zone_x - x
            move_y = zone_y - y
            move_mode = "zone_escape"

        elif critical_hp and enemies and not finisher and len(enemies) >= 2:
            closest_enemy = min(
                enemies,
                key=lambda enemy: math.hypot(
                    enemy["pos"][0] - x,
                    enemy["pos"][1] - y,
                ),
            )

            away_x = x - closest_enemy["pos"][0]
            away_y = y - closest_enemy["pos"][1]
            away_length = max(1e-6, math.hypot(away_x, away_y))

            move_x = (away_x / away_length) * 80.0 + (zone_x - x) * 0.45
            move_y = (away_y / away_length) * 80.0 + (zone_y - y) * 0.45
            move_mode = "critical_retreat"

        elif loot_target is not None and nearest_distance >= loot_clear:
            move_x = float(loot_target["pos"][0]) - x
            move_y = float(loot_target["pos"][1]) - y
            move_mode = "loot_rush"

        elif target is not None:
            target_x, target_y, target_z = target["pos"]
            target_distance = math.hypot(target_x - x, target_y - y)
            target_yaw = math.atan2(target_y - y, target_x - x)

            if self.rng.random() < 0.04 or t > self._strafe_until:
                self._strafe_dir = self.rng.choice([-1.0, 1.0])
                self._strafe_until = t + self.rng.uniform(1.0, 2.5)

            try:
                target_aim = float(target.get("aim", [target_yaw + math.pi, 0.0])[0])
                bearing_from_target = math.atan2(y - target_y, x - target_x)
                angular_error = _wrap_angle(target_aim + math.pi - bearing_from_target)

                if 0.35 < abs(angular_error) < 2.6:
                    self._strafe_dir = -1.0 if angular_error > 0 else 1.0

            except Exception:
                pass

            strafe_yaw = target_yaw + self._strafe_dir * (math.pi * 0.5)

            if hp_ratio > 0.70:
                desired_range = 38.0
            elif hp_ratio > 0.45:
                desired_range = 58.0
            else:
                desired_range = 85.0

            my_weapons = {weapon.get("kind") for weapon in weapons if weapon}
            enemy_weapons = set(target.get("weapons", []))

            if "lightning" in my_weapons and "lightning" not in enemy_weapons and hp_ratio > 0.40:
                desired_range = min(desired_range, 32.0)
            elif "laser" in my_weapons and "lightning" in enemy_weapons:
                desired_range = max(desired_range, 105.0)
            elif "mortar" in my_weapons and "lightning" in enemy_weapons:
                desired_range = max(desired_range, 90.0)

            if finisher:
                desired_range = min(desired_range, 35.0)

            radial_pull = 0.0

            if target_distance < desired_range * 0.70:
                radial_pull = -0.45
            elif target_distance > desired_range * 1.20:
                radial_pull = 0.85

            move_x = (
                (zone_x - x) * 0.30
                + math.cos(target_yaw) * radial_pull * 60.0
                + math.cos(strafe_yaw) * 40.0
            )
            move_y = (
                (zone_y - y) * 0.30
                + math.sin(target_yaw) * radial_pull * 60.0
                + math.sin(strafe_yaw) * 40.0
            )

            move_mode = "hunt_and_kill"

        elif loot_target is not None:
            move_x = float(loot_target["pos"][0]) - x
            move_y = float(loot_target["pos"][1]) - y
            move_mode = "loot_rush"

        move_length = math.hypot(move_x, move_y)

        if move_length > 1e-6:
            action["move"] = [
                move_x / move_length,
                move_y / move_length,
            ]
        else:
            action["move"] = [0.0, 0.0]

        should_boost = (
            (not in_zone and energy > 15.0)
            or (my_zone_distance >= zone_radius - zone_margin * 0.7 and energy > 20.0)
            or (
                move_mode == "hunt_and_kill"
                and energy > 20.0
                and nearest_distance > 50.0
                and hp_ratio > 0.55
            )
            or (
                finisher
                and energy > 18.0
                and nearest_distance > 40.0
            )
            or (recently_hit and critical_hp and energy > 25.0)
            or (loot_target is not None and loot_distance > 25.0 and energy > 35.0)
        )

        action["boost"] = bool(should_boost)
        action["jump"] = bool(
            me.get("grounded", False)
            and self.rng.random() < 0.03
            and target is not None
        )

        if target is not None:
            target_position = target["pos"]
            target_velocity = target.get("vel", [0.0, 0.0, 0.0])
            horizontal_distance = max(
                1.0,
                math.hypot(target_position[0] - x, target_position[1] - y),
            )

            lead_time = min(1.0, horizontal_distance / bullet_speed)
            predicted_x = target_position[0] + target_velocity[0] * lead_time
            predicted_y = target_position[1] + target_velocity[1] * lead_time

            target_cubes = int(target.get("cubes", 0))
            predicted_z = target_position[2] + 4.5 * (
                1.0 + 0.06 * min(target_cubes, 12)
            )

            yaw = math.atan2(predicted_y - y, predicted_x - x)
            pitch = math.atan2(predicted_z - muzzle_z, horizontal_distance)
            pitch = max(-0.85, min(0.85, pitch))

            action["aim"] = [yaw, pitch]

            target_weapons = target.get("weapons", [])
            target_has_mortar = "mortar" in target_weapons

            fire_primary = self._fire_decision(
                0,
                weapons[0] if len(weapons) > 0 else None,
                horizontal_distance,
                True,
                energy,
                target_has_mortar,
            )

            fire_secondary = self._fire_decision(
                1,
                weapons[1] if len(weapons) > 1 else None,
                horizontal_distance,
                True,
                energy,
                target_has_mortar,
            )

            fire = [fire_primary, fire_secondary]

            my_aim = me.get("aim", [yaw, pitch])
            aim_error = abs(_wrap_angle(float(my_aim[0]) - yaw))

            for index, weapon in enumerate(weapons[:2]):
                if weapon is not None and weapon.get("kind") == "laser" and self.laser_charging[index]:
                    continue

                if aim_error > 0.06:
                    fire[index] = False

            if self.geo.ok:
                try:
                    blocked_low = self.geo.blocked(
                        x,
                        y,
                        muzzle_z,
                        float(target_position[0]),
                        float(target_position[1]),
                        float(target_position[2]) + 4.5,
                    )

                    blocked_high = self.geo.blocked(
                        x,
                        y,
                        muzzle_z,
                        float(target_position[0]),
                        float(target_position[1]),
                        float(target_position[2]) + 8.5,
                    )

                    if blocked_low and blocked_high:
                        for index, weapon in enumerate(weapons[:2]):
                            if weapon is not None and weapon.get("kind") != "mortar":
                                fire[index] = False

                                if weapon.get("kind") == "laser":
                                    self.laser_charging[index] = False

                except Exception:
                    pass

            action["fire"] = fire

            mf.draw.line(
                [x, y, muzzle_z],
                [predicted_x, predicted_y, predicted_z],
                "#ff2020",
            )

        else:
            self.laser_charging = [False, False]

            if recently_hit and self.last_damage_bearing is not None:
                action["aim"] = [_wrap_angle(self.last_damage_bearing), 0.0]
            elif loot_target is not None:
                action["aim"] = [
                    math.atan2(
                        float(loot_target["pos"][1]) - y,
                        float(loot_target["pos"][0]) - x,
                    ),
                    0.0,
                ]
            else:
                action["aim"] = [math.atan2(zone_y - y, zone_x - x), 0.0]

            action["fire"] = [False, False]

        mf.draw.point([zone_x, zone_y, z], "#ffcc00", 6)
        mf.draw.label(
            f"NO MERCY+ v14 | hp {hp:.0f}/{max_hp:.0f} | "
            f"mode {move_mode} | enemies {len(enemies)} | "
            f"nearest {nearest_distance:.0f}"
        )

        return action


mf.run(Bot)