# WaveSense demo speech

Team 28 · Sal, Oleh, Kristjan
Code Metal Forge 2026 · Operation Lost and Found · BASE-X, Boston

This is the first version. Read it out loud once before the judge sits down. The live actions are marked **[SHOW]**. The lines in bold are the talking points. Hit every one of them. They are the score.

Table judging is about 15 minutes. This script is about 7 minutes of talking, with the robot moving inside that. Finals, if we get there, are 10 minutes on the main stage. The short cut is at the bottom.

## How the judge scores

From the Saturday mission brief. Four criteria, equal weight. Each one is 1 to 5. Twenty points max. The same four are used at the table and on the main stage.

| Criterion | Weight | What they are listening for | Where we hit it |
|---|---|---|---|
| Real-world relevance | 25% | A clear user, a real problem, an operating environment, and a capability that helps | Open, and the close |
| A working live demo | 25% | Real functionality built this weekend. Partial progress on a hard problem still scores | The demo, and the numbers |
| Deployability | 25% | A credible path off the laptop: hardware, power, latency, bandwidth, reliability | “Off this table” |
| Ideation | 25% | A fresh approach, a novel combination, or clever use of the hardware on hand | “Why this combination” |

Not scored: code cleanliness, pitch polish, production readiness, a finished product. A simple thing that works can win. So can an ambitious one with real progress. Do not apologize for the cardboard box. Do not pretend the unfinished parts are finished.

The judge is also holding four questions. Answer them even if they never ask.

1. Can you show it working?
2. What existed before, and what did you build here?
3. What worked, what didn’t, and what’s unfinished?
4. What would it take to run this outside your laptop?

The mission has four objectives. We did not try all four. **We took locate, map, and deliver, and one slice of detect.** A focused capability that works beats a broad one that does not.

## Talking points, in order

Say these. If the demo eats the clock, skip a story, not a point.

1. **The user is a responder who should not be the first person in the room.**
2. **The place has no reliable GPS or cell, and it may be closed off or still changing.**
3. **WaveSense is a phone-driven scout. It brings back a picture, a local map, and a radar cue. A person still decides.**
4. **The chassis, the Pi, and the sensors were here. The phone console, the motor safety, the radar detector, and the rule that the phone cannot invent a detection were built this weekend.**
5. **[SHOW] The camera is live. The motors stay disarmed until a stick moves.**
6. **[SHOW] Drive a short path. Let go. It stops.** Watchdog, deadman, and a lost camera also stop the wheels.
7. **[SHOW] The map is the lidar’s plane around the robot, not a floor plan of the building.** It can drift.
8. **[SHOW] Stop the robot. A person sits in front. Read the range.** Then say the sentence below.
9. **A candidate means check it. It does not name a person. No candidate does not clear the room.**
10. **Bio Radar is optional. If there is no measured direction, it draws a band that says direction unknown. It does not guess a spot.** The words on a blob are “Possible person.” Left and right are not lined up with the camera yet.
11. **What worked, with the label still on it.** Still person, 57 of 60 seconds, about 0.9 m. Wooden door, 95 of 95 seconds, one run, and background motion also flagged it. Direction on that recording, mostly 4° to 9°, and none in the empty minute. Camera, lidar, and radar ran together for 45 seconds while the robot was still.
12. **What did not.** Breathing rate. A still person on Wi-Fi CSI. Livox, because Ethernet had no carrier. Thermal, which is not on the vehicle. Driving and sensing at the same time. Concrete and rubble. Live accuracy of the camera boxes.
13. **It already runs on the robot, not on a laptop.** The path out of this room is power, a calibrated mount, a held-out test, and a radio that still works when the network is gone.
14. **The new combination is a scout you drive from outside, plus a 24 GHz radar that can flag someone sitting still, plus a picture that refuses to invent a location.**
15. **Thank you to Code Metal and the organizers of Forge.**

The sentence, when the cue appears:

> Possible person, about N metres. A place to check. Not identified. Left and right are not calibrated. The robot is stopped.

If the radar is dark, say that. Show drive, camera, and the map. Unplug the radar for five seconds and replug. Do not invent a cue.

## The speech

### Open — real-world relevance

We are Team 28. Sal, Oleh, and Kristjan. The project is WaveSense.

**The user is a responder who should not be the first person through the door.** Someone is missing. The space is dangerous, closed off, or still changing. **GPS and cell are not something we get to count on.**

Forge gave us four objectives: locate isolated people, detect life and hazards, map changing conditions, and deliver critical information. **We took locate, map, and deliver, and one slice of detect.** We did not build a gas sensor, and we did not build a structural-hazard sensor. We built the look that happens before anyone walks in.

**WaveSense is a phone-driven scout. It brings back a picture, a local map, and a radar cue. A person standing outside still decides what it means.**

**[SHOW]** Put a hand on the vehicle. Nose is a 24 GHz TinyRad. Top is a Slamtec C1 lidar. The camera is USB, 640 by 480. The brain is a Raspberry Pi 5. The wheels are mecanum, so it can strafe in a tight room.

### What existed, and what we built — the live-demo criterion

**The chassis, the Pi, and the sensors were here to check out. Judges score what we built between Saturday at 10 and Sunday at 12.**

Before this weekend we had a Hiwonder MasterPi, a Pi 5, and the parts on the kit table: the C1, the TinyRad, and a USB camera. We also used open tools that already existed: OpenCV’s NanoDet for person boxes, BreezySLAM for the local map, and Analog Devices’ own TinyRad code for the radar hardware.

**What we built here is the phone console, the motor path with a deadman, the radar detector, and the rule that the phone cannot invent a detection.** The git history is in this repo, on the branches from this weekend. That is the proof of what is new.

### Show it working

**[SHOW]** Open the phone. **The camera is live. The motors stay disarmed until a stick moves.**

**[SHOW]** Drive a short path. Let go of the stick. **It stops.** A 300 millisecond watchdog and a deadman cut the wheels if the link goes quiet. Losing the camera stops them too. Saturday, on the floor, duty 35 for 0.3 seconds rolled it forward a little and stopped. The speed slider caps the command. It is not a measured ground speed.

**[SHOW]** Swap to the map. **This is the lidar’s plane around the robot, not a floor plan of the building.** Distances are from the scanner, not the bumper. It is one horizontal slice, and the map can drift. The C1 turns about 10 times a second.

**[SHOW]** Stop the robot. Ask someone to sit still in front of the nose. Do not drive during this. **The radar cue hides while a drive command is active, and for 3 seconds after, because driving disturbs the whole scene.**

When the amber range appears, say:

> Possible person, about N metres. A place to check. Not identified. Left and right are not calibrated. The robot is stopped.

**A candidate means check it. It does not name a person. Silence does not clear the room.**

**Bio Radar is a button, and it is optional.** It draws an orange cue on the camera, with the range. **If the radar has no measured direction, it draws a faint band that says direction unknown. It does not guess a spot.** If a blob is up, the words on it are “Possible person.” **Left, right, and the camera lineup have not been checked. Treat the blob as a place to look, not a location.**

If the radar does not answer USB, say so. Show the drive, the camera, and the map. A five-second unplug has brought it back before. **Do not invent a cue.**

### What worked, what did not — still the live-demo criterion

Be the one who says the limits. Partial progress on a hard problem still scores. A claim we cannot stand behind does not.

**What worked.**

- **A still person, seated about a metre in front: a candidate for 57 of 60 seconds, first hit at 3 seconds, median range 0.9 metres.** One room, one person. The thresholds were set on this recording.
- **The same kind of cue behind one wooden door: 95 of 95 seconds, about 1.5 metres.** People moving in the background also flagged it. The radar does not know what moved. One run. **Concrete, masonry, and rubble stop 24 GHz. We are not claiming a wall.**
- **Direction, on the replay of that seated person: a bearing on 57 of 57 candidate seconds, mostly 4 to 9 degrees, and none in the empty minute.** Through the door the bearing wandered, and the person’s true spot was not logged. **That number is in the radar’s own frame. It is not yet a mark on the camera.**
- **Camera, lidar, and radar ran together for 45 seconds with the robot still.** Camera at least 22 frames a second, lidar at least 9.8 turns a second, radar advanced about 11,000 frames, no reconnects. That run needed a powered hub and a second supply. The wheels were not commanded.
- An empty replay, after the radar had learned the room, was a candidate for 0 of 31 seconds. A quiet room cleared about 20 seconds after the person left. **A clear result is not an empty building.**

**What did not, and what is unfinished.**

- **We do not show a breathing rate.** The estimator also sat near 9 a minute when the room was empty.
- **The white brackets are a camera model. Live accuracy was not measured.** The test that passed was an OpenCV picture, and the live camera spent time pointed at the floor.
- **Wi-Fi CSI caught a person walking across the link, about 58 of 60 seconds, first hit around 2 seconds, and the first empty minute was clear.** A still person was not separable, and the cue lingered after a walk. **CSI is not in this demo.**
- **Livox 3D software is written. Ethernet had no carrier, so it is not on the robot.**
- **Thermal saw a hand on Saturday and is not on the vehicle now.**
- **We do not judge presence while the robot is moving.**

### Off this table — deployability

**This is already not a laptop demo. The compute is on the robot. The operator is on a phone, over Tailscale.**

Four things have to be true before this leaves the room.

- **Power.** Camera, lidar, and radar together tripped the Pi’s own USB ports. The working run used a powered hub and a second supply. That is a hardware fact, not a software bug.
- **Latency and a failed link.** The motor port stays closed until a stick moves. Release commands a stop. The watchdog is 300 milliseconds. A radar file older than 2 seconds is ignored. A device merely showing up on USB is not a person.
- **A calibrated mount.** Until someone stands at known spots and we set left, right, and the camera field of view, a bearing is not a place in the picture. That is a measurement we have not done.
- **Bandwidth when the network is actually gone.** Today the phone rides a network that exists. **A path for a degraded or overloaded channel is unfinished.** That is the piece of “deliver” we have not closed.

The next test, if we had another day, is a held-out one: drive in, stop, then score an empty scene against a still person. We have not done that yet.

### Why this combination — ideation

**The fresh piece is not any one sensor. It is the combination, and the refusal.**

A scout you can drive from outside. A 2D lidar so the operator can see the shape of the room. A 24 GHz radar that can flag someone who is sitting still, which a camera aimed at a floor and a Wi-Fi link both failed to do. And a picture that will not paint a person where the radar has no direction.

**We tried Wi-Fi CSI first. It is a clever sensor for a walk across the link, and it is the wrong sensor for a still person. The radar is the edge we kept.**

**We use the hardware that was on the table, and we do not ask it to do the job of a rescuer.**

### Close

**Thank you to Code Metal, and to the organizers of Forge,** for the floor, the sensors, the mentors, and a problem that had to work in the room.

**The operator stays outside. The scout looks first. A human makes the call.**

## If the judge asks

**Can you show it working?**
Yes. Drive, stop, map, and a range from a still person, with the robot stopped. If the radar is down, we will say so and show the rest.

**What existed before?**
The MasterPi, the Pi 5, the C1, the TinyRad, the USB camera, and open-source pieces: NanoDet, BreezySLAM, and Analog Devices’ radar tools. The integration, the phone, the motor safety, and the radar detector are this weekend. The branches are the record.

**What worked, what didn’t, what’s unfinished?**
Use the two lists above. Do not skip the wooden door’s background motion, the uncalibrated blob, or the breathing rate.

**What would it take to run this outside the laptop?**
It is already on the robot. Next is power that is not a bench supply, a measured mount, a held-out test, and a radio for when Tailscale is gone.

## Ten-minute stage cut

If we are on the main stage, keep the same points and drop the long number list to four lines.

1. User, place, and the three objectives we actually took.
2. **[SHOW]** Drive, release, stop. Then the map, named for what it is.
3. **[SHOW]** Robot stopped. Still person. Say the sentence.
4. One line of what worked, one line of what did not, one line of what it takes to leave this room. Thank the organizers. Stop.

## Fallback if a sensor dies

| What died | What we still show | What we say |
|---|---|---|
| Radar USB | Drive, camera, map | “The radar is down. We will not invent a cue.” Replug for five seconds. |
| Camera | Map and radar range only, no driving | “No camera, so the wheels stay shut.” |
| Lidar | Drive and camera, radar with the robot stopped | “No map this pass. The picture and the range are the demo.” |
| Phone link | Say so and use the backup | A dead link is a failed demo of “deliver.” Do not narrate a recording as if it were live. |
