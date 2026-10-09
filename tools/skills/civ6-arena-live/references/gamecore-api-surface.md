# GameCore accessor surface (verified live 2026-10-09)

Checked over the tuner in the `GameCore_Tuner` state, which is where `execute_read`,
`execute_mutation`, recipe setup ops and the v2 capture program run. The InGame UI
context is a different API; InGame or wiki knowledge does not transfer. Regenerate with
`scripts/probe-gamecore-api.py --markdown` whenever a recipe needs an accessor not listed.

| Object | Present | Absent |
|---|---|---|
| `city:GetDistricts()` | `GetNumDistricts`, `GetDistrictByIndex(i)` (zero-based) | `Members` |
| `city:GetBuildQueue()` | `CurrentlyBuilding` (returns the string `"NONE"` when empty, never nil) | |
| `city:GetBuildings()` | `HasBuilding`, `IsPillaged`, `SetPillaged`, `RemoveBuilding` | |
| `unit` | `GetBuildCharges`, `SetDamage`, `IsDead`, `IsDelayedDeath`, `SetActionCharges`, `ChangeActionCharges` (neither touches build charges) | `ChangeBuildCharges`; there is no build-charge setter at all, and every DB builder-charge modifier is positive |
| `UnitManager` | `InitUnit` (a builder spawns with 3 charges, 4 MP), `PlaceUnit`, `RestoreMovement`, `Kill` | |
| `ImprovementBuilder` | `SetImprovementPillaged`, `SetImprovementType(plot, improvementIndex, owner)` | |
| `Players[0]:GetTreasury()` | `ChangeGoldBalance`, `GetGoldBalance` | |
| `Players[0]:GetResources()` | `GetResourceAmount` | `GetResourceAccumulationPerTurn` |
| `plot` | `SetOwner(player, cityId)`, `GetYield(i)`, `GetDistrictType`, `IsImprovementPillaged` | |
| `Players[0]` | `AttachModifierByID` | |

Behaviours that broke a capture:

- A unit killed in combat stays in `GetUnits():Members()` as a delayed-death row at
  `(-9999,-9999)` with hp 0 until the engine sweeps it. Filter with
  `not u:IsDead() and not u:IsDelayedDeath()`, or losses are never charged and kills
  never counted.
- `BuildQueue:CurrentlyBuilding()` returns `"NONE"` for an empty queue; treating it as
  a production item raises `unknown production item NONE`.
