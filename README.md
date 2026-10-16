# Tesla Roadster 2 — "Flying Car" (Blender)

A procedurally generated, fully scripted Blender scene recreating the spirit of the
**15 Oct 2026 Tesla / SpaceX demo** at McGregor, Texas: a sleek electric hypercar rides a
giant curved **suction ramp**, goes **upside down**, launches on **compressed-air thrusters**,
kick-flips in mid-air and lands.

Everything is generated from one Python script — no manual modelling. Re-run it and you get
the identical scene back.

---

## Deliverables

| File | What it is |
|---|---|
| `roadster_flying_car.blend` | The editable Blender scene (car, ramp, site, lights, cameras, animation) |
| `renders/roadster_hero.png` | 2560×1440 hero poster — airborne car with the loop ramp |
| `renders/roadster_anim.mp4` | ~8 s animation of the full sequence |
| `renders/car_studio.png` | Studio beauty render of the car alone |
| `scripts/build_roadster.py` | The generator (Blender 5.2, Cycles) |

---

## The sequence

| Time | Beat |
|---|---|
| 0.0 – 3.2 s | Car accelerates up the curved suction ramp, rotating to **upside down** at the top |
| 3.2 s | Launches off the ramp end on the rear thrusters |
| 3.2 – 5.8 s | Ballistic arc with a full **kick-flip**, attitude-control thrusters firing |
| ~5.8 s | Lands on its wheels and rolls out |

Physics is real: the launch velocity is the tangent to the ramp at the exit angle, and the
flight follows `z = z₀ + v_z t + ½ g t²` with `g = −9.81 m/s²`. The landing time is solved
analytically, so the car always touches down on the ground plane.

---

## The car

Built from a lofted superellipse hull, boolean-cut wheel arches, and separate canopy /
aero / wheel / thruster parts. Local frame is **+X forward, +Z up, z = 0 at the ground
contact plane**, which is what lets the car sit exactly on the ramp surface and land cleanly.

- Loft **body** with real wheel arches (boolean difference, with roof margin so the cut never
  breaches the top surface)
- Dark **canopy** greenhouse
- Front **splitter**, underbody, rear **diffuser + fins**, active **wing**, mirrors
- Four wheels (tire / rim / hub / spokes)
- Two **main rear thruster bells** + five **attitude-control nozzles** + two belly **suction fans**
- Emissive thruster plumes with an **alpha gradient** (opaque at the nozzle, fading along the
  plume) and an animated emission strength that only fires during flight

## Environment

SpaceX-style test site at dusk: Raptor test stand, floodlight towers, water towers, a line of
vehicles and a semi for the fly-over, all under a Nishita/scattering sky with a low warm sun,
cool fill and a warm rim light.

---

## Re-running

Requires Blender 5.2+ (`blender` on `PATH`). From this folder:

```bash
blender -b -P scripts/build_roadster.py -- --stage car      # studio beauty still
blender -b -P scripts/build_roadster.py -- --stage preview  # fast scene check
blender -b -P scripts/build_roadster.py -- --stage hero     # poster + .blend
blender -b -P scripts/build_roadster.py -- --stage anim     # mp4 animation
blender -b -P scripts/build_roadster.py -- --stage save     # .blend only
```

Key knobs are at the top of the script: `RAMP_R`, `RAMP_A_END`, `V_LAUNCH`, `F_DRIVE_END`,
`F_LAND`, `F_TOTAL`.

---

## Notes / limitations

- **Stylised**, not a licensed CAD replica of the real Roadster. Proportions are close
  (≈4.9 m long, ≈1.96 m wide, ≈0.8 m tall) but the details are interpretive.
- Blender 5.x renamed the sky enum (`NISHITA` → `MULTIPLE_SCATTERING`) and moved video output
  under `image_settings.media_type = "VIDEO"`; both are handled in the script.
- This is a fan-made concept render, not an official Tesla/SpaceX product visual.