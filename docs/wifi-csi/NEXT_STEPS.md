# Wi-Fi CSI: next steps

A prioritized research plan for whoever picks this up. Each step depends on the ones before it, so don't skip ahead to localization or dashboard integration.

**Guardrails for every step:**

- Run experiments only with **informed, consenting participants**. Tell everyone within radio range, and let anyone opt out or stop a session.
- Record **conditions**: node position, distance and orientation, room, time, and who or what was present. Use a ground-truth log that doesn't identify anyone.
- Keep raw captures **outside git**. `wifi-sensing/captures/` is ignored. Don't store names or faces alongside the RF data.
- Report results as **measured / inferred / unverified**, the way [RESULTS.md](RESULTS.md) does. Never present a RuView classification as a validated detection.
- Don't reflash the working ESP32-C5. Don't touch `wlan0`, `tailscale0` or running containers you didn't start.

## 1. Verify stable real-time CSI ingestion

- **Why.** We have about 9 minutes of container runtime on record, and the run ended `Exited (255)` for an unknown reason.
- **How.**
  1. Bring up the hotspot: `nmcli connection up ForgeCSI`.
  2. Run `start_ruview.sh --start`.
  3. Run `check_stream.sh` every few minutes for at least 30 minutes, logging its exit status and `seq_rate_hz`.
  4. Note every `esp32:offline` period, every container restart and every stall in `csi_sequence`.
  5. Find out why the last run exited with 255 ([TROUBLESHOOTING §12](TROUBLESHOOTING.md#12-container-exited-255)).
- **Done when.** A 30-minute run has no offline periods, or every gap is explained. For comparison, upstream's C5 notes report that the AP they tested didn't consistently sustain 20 packets/s; we saw 30.9 Hz in one 60 s run.

## 2. Record controlled stationary and empty-area baselines

- **Why.** Without a baseline, no change in the signal can be interpreted.
- **How.**
  1. Fix the node and the Panda adapter in place and record their positions.
  2. With the area empty, record at least 3 sessions of 10 minutes each, at different times of day.
  3. Save per-frame timestamp, sequence number, RSSI and per-subcarrier amplitude/phase.
  4. For tooling, extend the harness capture (`harness/ruview/src/devices/esp32.js`) or try RuView's `/api/v1/recording/*` endpoints. **We haven't tested the recording endpoints.**
- **Done when.** Repeatable empty-area datasets exist, stored outside git, with a written description of the setup.

## 3. Measure empty-area false detections

- **Why.** This is the most important number for a rescue setting: how often the system claims motion when nobody is there.
- **How.** During the step 2 sessions, poll `/api/v1/nodes` at 1 Hz and log `motion_level`, `person_count` and `person_count_valid`.
- **Metric.** The fraction of samples that report presence or motion while the area is verified empty, per session, with the spread across sessions.
- **Done when.** There is a false-positive rate with its spread, measured on at least 3 sessions.

## 4. Repeated movement tests with consenting participants

- **Why.** This tests whether the signal responds to people at all, under controlled conditions.
- **How.**
  - Follow a written protocol: 2 min empty → enter → walk a marked path → stand still for 1 min → leave → 2 min empty.
  - Keep a timestamped ground-truth log of the segments, with no identities.
  - Repeat at least 5 times per condition.
  - Start with **line of sight in the same room**. Try through-wall tests only after that works.
- **Done when.** You have labelled sessions in which each segment can be compared with the baseline.

## 5. Analyze RSSI variation and reported packet loss

- **Why.**
  - RSSI ranged from -65 to -81 dBm across our observations.
  - The 33.4% "loss" probably includes firmware rate-limit gaps ([TROUBLESHOOTING §9](TROUBLESHOOTING.md#9-reported-packet-loss)).
- **How.**
  - Capture the serial callback rate (`cli.js monitor`) and the UDP delivery rate at the **same time**. Alternatively, expose the firmware's `s_send_fail` and `s_rate_skip` counters.
  - Plot RSSI over time and compare empty and occupied sessions.
  - Check whether loss correlates with RSSI or with distance.
- **Done when.** Real radio or network loss is separated from intentional rate limiting, and RSSI stability is known for the chosen placement.

## 6. Implement baseline subtraction and optional robust smoothing

- **How.**
  - Subtract a per-subcarrier baseline (median of the step 2 empty sessions) from each frame.
  - Optionally apply robust smoothing: a Hampel filter for outliers, then a short moving median.
  - Keep the raw signal next to every filtered one.
  - Put the code under `wifi-sensing/`, using the standard library or NumPy, with unit tests on synthetic arrays. **Synthetic arrays are for tests only, never to stand in for results.**
- **Done when.** Filtering is reproducible from a saved capture with one command.

## 7. Compare raw versus filtered signals

- **How.** Evaluate on held-out sessions from steps 2–4. Compare how well empty and occupied segments separate in raw and in filtered data, for example with a variance ratio, or with the ROC/AUC of a simple threshold detector.
- **Done when.** There is a clear statement of whether filtering helps, with numbers and the sessions used. If it doesn't help, say so.

## 8. Evaluate confidence scores and signal-quality metrics

- **How.**
  - Compare RuView's confidence outputs, and our own metrics (RSSI, loss, sequence-gap rate, `csi_last_seen_ms`), against the ground truth from step 4.
  - Define a **quality gate** that suppresses occupancy evidence when the signal is poor, with thresholds taken from the data.
- **Done when.** Every reported value carries a quality flag that has been justified by measurements.

## 9. Test the second ESP32, after identifying it

- **How.**
  1. Run `esptool chip-id` and `flash-id` ([SETUP §4](SETUP.md#4-esp32-c5-identification)).
  2. Pick only the release bundle built for that chip (S3 4 MB or 8 MB, C5, or C6).
  3. Back it up and verify the backup (section 6), run `flash-plan`, and only then flash, manually ([SETUP §8](SETUP.md#8-optional-guarded-flashing-new-boards-only)).
  4. Provision it with a **different `--node-id`**.
- **Done when.** The second board streams on its own, and `check_stream.sh` verifies it as a separate node.

## 10. Explore multi-node localization, only after individual nodes are calibrated

- **Prerequisites.** Steps 1–8 pass for **each** node.
- **How.**
  - Provision time-division slots (`provision.py --tdm-slot/--tdm-total`).
  - Measure node positions and pass them to the server (`--node-positions`) so that `position_configured` becomes `true`.
  - Validate positions against measured ground truth.
- **Done when.** There is a localization error, in metres, with its spread, on labelled sessions. Until then, show no position estimates.

## 11. Integrate reliable occupancy evidence into the robot dashboard

- **How.**
  - Publish only the gated, calibrated output from step 8, labelled **"experimental Wi-Fi motion evidence"**, with its quality flag and age. Never label it "person detected".
  - Keep a human in the loop.
  - Coordinate with the dashboard and sensor-fusion owners, and agree on the message format before writing code.
- **Done when.** Responders see the evidence, its confidence and its limits, and turning it off has no effect on any other sensor.

## Open questions

- Why did `ruview-live` exit with 255 at 12:50 EST on 2026-11-07?
- How much of the 33.4% is real loss?
- Does a fixed node placement make RSSI and the visualization stable?
- Is the 26 TOPS accelerator useful for this at all? It's installed but not verified, and nothing here uses it.
