# Source audit register

The app ships with no third-party map image, marker catalog, or gameplay fact until this register marks the specific field as approved.

| Candidate | Intended use | Current status | Decision rule |
| --- | --- | --- | --- |
| Pocketpair official server documentation | Integration constraints only | Citation allowed | Use only for documented server behavior; it is not a game-data source. |
| Installed Palworld 1.0 manifest | Table/file provenance | Verified 2026-07-13 | Confirms the installed build contains drop, technology, boss-location, and world-map tables. It does not decode their rows. |
| Installed Palworld 1.0 `.pak` | Primary candidate | Regional map extraction proven 2026-07-14 | Archive is unencrypted. The exact `T_WorldMap.uexp` and `T_TreeMap.uexp` payloads can be selectively extracted and decoded into private local caches. General table-row decoding remains blocked by the missing matching 1.0 `.usmap`. |
| PalDB index and individual 1.0 records | Current decoded records | Citation-only, admitted field by field | Core, Expedition Station, Pure Quartz, Crude Oil, Ancient Bark, and Ancient Technology pages checked 2026-07-13. Never copy site assets or reproduce its map UI. |
| Palworld Wiki data tables | Structural cross-check | Citation allowed under CC BY-SA 4.0 | Confirms which game tables contain drops and absolute spawner coordinates. |
| PalworldSaveTools pinned fast-travel file | Fast-travel names and source coordinates | Admitted under MIT with attribution | Revision `e4e1439b274c1140eed5690051ce59ab14b68027`, checked 2026-07-13. Only the normalized factual subset is bundled; repository map imagery is not copied. |
| Palworld Atlas Data current-build spawn catalogs | Alpha Pal names, levels, regions, and source coordinates | Admitted under MIT with attribution | Revision `1063da140f38cc80791007ab7d440fe8e121466b`, build `24088465`, checked 2026-07-14. Only 90 normalized Alpha records are bundled; no raw assets or rendered map UI are copied. |
| ARXII-13 Palworld Interactive Map source | Coordinate-transform cross-check | Apache-2.0 code reference only | Revision `ad6bec0fb2ab06cec93f1cf45cd2198f9f52075d`, checked 2026-07-13. No map image or game-derived visual asset is copied. |
| palworld-map.com data methodology | Provenance discovery and live cross-check | Citation/reference only | Its methodology identifies pinned sources and licenses. Mount Obsidian - Midpoint independently matched the transformed `-498, -444` coordinate. |
| THGL | Interaction benchmark and cross-check | Reference only | Never scrape, bundle, reproduce, or rely on its map UI/data. |
| Local user-supplied map image | Private map base | Allowed locally | Store outside the repository and calibrate explicitly; do not redistribute. |
| Installed Palworld process | Live player position, heading, and replicated fast-travel unlock set | Admitted only for exact verified executable hash | External read-only handle requests only `PROCESS_VM_READ | PROCESS_QUERY_LIMITED_INFORMATION`; unknown builds fail closed. |

## Admission checklist

For each imported record, record: field coverage, source URL, source date, game version, last checked date, confidence, contradiction notes, and whether the record is a citation-only fact or has redistribution permission. Facts without a current source date and verified coordinates must not become planner destinations.

## Admitted fast-travel slice

- Bundle 0.6 contains 171 unique travel waypoints normalized from the pinned PalworldSaveTools file: 149 standard fast-travel destinations and 22 map-reveal watchtowers.
- All records preserve their upstream IDs and raw Unreal world coordinates. Confidence means verified against that pinned source, not independently visited in game.
- The live overlay renders these admitted coordinates with original geometric markers only. It does not copy Palworld or third-party marker icon assets.
- 150 Palpagos records have local `palpagos-map-v1` coordinates derived with the audited transform. Mount Obsidian - Midpoint was independently cross-checked at `-498, -444` against the current palworld-map.com display.
- All 14 World Tree waypoints use the separately audited `world-tree-map-v1` transform and private `T_TreeMap` cache. Two of those records are watchtowers.
- The 7 Sunreach records remain `source-world`. The UI names them but does not fabricate positions until that regional map is calibrated.
- Palworld 1.0 in-game evidence on 2026-07-14 confirmed that Windswept Island Watchtower reveals map coverage and exposes Transfer. This corrected the prior audit assumption that `WatchTower_*` records were not usable travel destinations.
- World Tree middle-boss records remain excluded because they are not travel waypoints.
- No third-party or game-derived map image is bundled. Palpagos and World Tree imagery are extracted into private local caches; user-imported calibrated imagery remains available as a private override.

## Admitted Alpha Pal slice

- Bundle 0.6 contains 90 unique Alpha Pal POIs normalized from the pinned Palworld Atlas Data current-build spawn catalogs: 82 Palpagos records and 8 World Tree records.
- Every record preserves its upstream spawn ID, internal Pal ID, localized name, level range, availability, region, and raw world coordinates.
- The records have no unlock semantics. They remain searchable and routable regardless of whether the default-on Alpha Pal map layer is visible.
- PalPlus draws an original geometric star marker. It does not copy Palworld or third-party marker icons.
- Confidence means verified against the pinned normalized dedicated-server data source, not independently visited in game.

## Live runtime telemetry

- The admitted profiles are `steam-1.0-2026-07-10`, gated by executable SHA-256 `5A0009A2D429CF7B84FF22FD99B318FF7E512A91F40F463C4A7476DB9C066755`, and `steam-1.0-build-24181527`, gated by executable SHA-256 `2FF94A03BC777661BE100249B4940242F70661D890C6B8F8ACA4D6DCE79EE5A5`.
- The external reader requests Windows access mask `0x1010`: `PROCESS_VM_READ | PROCESS_QUERY_LIMITED_INFORMATION`.
- It does not request memory write, memory operation, remote-thread, all-access, injection, driver, save-file, or server-admin capabilities.
- The pointer chain resolves the current local pawn, root component, position, and rotation from exact-version offsets. Every pointer, array shape, coordinate, and rotation value passes sanity checks before a sample is emitted.
- For the exact audited Steam build, the same reader follows `APlayerController.PlayerState` to `APalPlayerState.RecordData`, reads `UPalPlayerRecordData.FastTravelPointUnlockFlag`, and decodes its FName GUID keys through the build-pinned FName pool.
- Runtime GUID keys join directly to the audited `upstream_key` field in `fast_travel.json`. Present true-valued records are unlocked; audited catalog keys absent from the replicated set are locked.
- The July 14 live acceptance probe decoded all 140 replicated records as 32-digit GUIDs. Of those, 137 join to admitted travel waypoints; the remaining three are the intentionally excluded `WorldTree_MiddleBoss_*` warp records. Invalid array shapes, invalid pointers, non-GUID names, unknown fingerprints, or name-pool drift make unlock state unavailable rather than inferred.
- The July 15 build `24181527` audit independently relocated `GWorld` and the inline FName block table, then revalidated every downstream pointer and field offset. Its live acceptance probe decoded 166 replicated waypoint records, with 163 joining the admitted catalog and the same three excluded middle-boss warp records. It also decoded 43 true-valued normal-boss defeat records for Alpha first-clear state.
- Alpha Pals never receive an unlock state. Manual SQLite unlock tracking remains available for export/import and offline fallback, but validated runtime state is authoritative for the live overlay.
- `palplus-helper --telemetry-check` emits the executable fingerprint, access mask, build decision, pointer-chain addresses, and one sample as inspectable JSON.
- Unknown executable hashes, incomplete profiles, invalid pointers, and failed sanity checks stop live telemetry rather than falling back to guessed offsets.

### Local executable-update audit

- An unknown executable hash may enter the deterministic local auto-auditor. This does not add a network, LLM, injection, debugger, write-process, save-file, or server-admin path.
- The auditor scans RIP-relative references in the installed PE, then accepts `GWorld` only when exactly one candidate resolves the existing verified player chain and passes all pointer, array-shape, finite-coordinate, and rotation checks.
- FName discovery is progression-independent: the inline block table is anchored on Unreal's hardcoded `FName[0] = None`. Replicated waypoint GUID names are additional evidence when present, not a requirement for a new character.
- The candidate must decode the complete current waypoint array, decode the Alpha first-clear array, and survive three repeated player samples. Available waypoint landmarks must join the admitted catalog except for at most three intentionally excluded warp records.
- A successful candidate is stored as an exact-SHA-256 local profile under `%LOCALAPPDATA%\PalworldCompanion\build-profiles`. Built-in source profiles are never mutated. The JSON records the seed profile, candidate counts, admitted RVAs, decoded record counts, sample count, timestamps, permission boundary, and network-use state without storing player coordinates.
- A failed audit writes the same structured report with its real exception and remains fail-closed. It never falls back to the seed profile's old global offsets.
- Map textures retain their independent exact-fingerprint gate. Runtime-profile admission cannot silently admit changed map assets.

## Private installed-game map extraction

- The installed archive contains separately fingerprinted Palpagos (`T_WorldMap`) and World Tree (`T_TreeMap`) texture assets under `Pal/Content/Pal/Texture/UI/Map/`.
- For the admitted July 10 Steam build, the `.uexp` is 33,554,588 bytes with SHA-256 `3B603EFB5891D8C02A09C334085FCCA832EB568EC92AEAE62DA9F2F53A0555AD`. A selective read-only extraction on July 15 confirmed that build `24181527` retains the same Palpagos and World Tree map metadata and payload fingerprints.
- The audited payload is an 8192×8192 `PF_DXT1`/BC1 texture: a 128-byte Unreal header, 33,554,432-byte pixel payload, and 28-byte trailer.
- `map_asset.py` rejects any size, hash, pixel-format, or byte-range mismatch. It wraps only the admitted BC1 payload in a DDS header, decodes it locally with Pillow, and writes a 4096×4096 WebP cache outside the repository.
- The extracted map image is private user-local material. It is neither bundled nor uploaded.
- End-to-end decoding from an already selectively extracted `.uexp` was verified in 4.68 seconds on 2026-07-13.
- Automatic selective extraction uses pinned `pyuepak 0.2.8` for read-only archive parsing and GPL `pyooz 0.0.8` for Oodle decompression. PalPlus injects the open-source adapter before importing pyuepak, preventing its optional proprietary downloader from loading.
- The exact installed 40.5 GB archive was indexed in 4.67 seconds. Selective extraction produced a 674-byte `.uasset` with SHA-256 `AFD5869FC3F41F850E29A328935FA181D351020C9E9249192689F124D1346632` and the admitted 33,554,588-byte `.uexp` in 0.15 seconds.
- First launch now creates a private, fingerprinted WebP cache and structured `map-provision.json` status under `%LOCALAPPDATA%\PalworldCompanion`. No archive content is bundled, uploaded, or fetched from the network.

## First admitted slice: Ancient Civilization Cores

- PalDB's current 1.0 Expedition Station records provide the acquisition ladder and tower-boss prerequisites.
- PalDB's current Core record provides World Tree and raid alternatives.
- Semantic destinations are used for the user's base and broad regions. No numeric coordinate is emitted until it can be decoded or independently verified.
- The installed game manifest confirms that `DT_PalDropItem`, `DT_BossSpawnerLoactionData`, `DT_TechnologyRecipeUnlock`, and `DT_WorldMapUIData` are present in the July 10 Steam build.
- General table-row decoding remains outside the public tool's scope. Generating the required matching `.usmap` would require a runtime dumper and is not authorized by the current read-only audit permission.

## Second admitted slice: Pure Quartz

- PalDB's current 1.0 Pure Quartz record supports a deterministic level-52 Quarry recommendation and its exact bootstrap cost.
- The same record supports a guaranteed Snow expedition bootstrap alternative.
- The destination is the user's existing base or Expedition Station. No third-party coordinate or map asset is bundled.

## Search-only scope notes

- Ancient Bark: the current item page verifies several crafting uses but does not provide a sufficiently traceable acquisition path. Farming recommendations remain withheld.
- Ancient Technology Points: the current help page verifies only the broad powerful-enemy acquisition statement. A repeatable farm remains withheld.
- Crude Oil: the current page verifies extraction, expedition, and oil-rig sources. An extractor-retirement calculation remains withheld because PalDB's indexed Crude Oil and Plasteel pages disagree on the Plasteel oil input.
- PalDB's map visibly exposes marker categories, including Ancient Bark and Pure Quartz, but its imagery and marker catalog have not passed the reuse gate. They are not copied into the bundle.
