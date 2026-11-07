# MasterPi motor bring-up

- **Date:** 2026-11-07
- **Robot:** Hiwonder MasterPi (mecanum wheels) on a Raspberry Pi 5, fresh Raspberry Pi OS, no Hiwonder software installed
- **Script:** [`nudge.py`](nudge.py)

## Board

| | |
|---|---|
| Board type | **Pi 5 UART expansion board**. Nothing at `0x7a` on I2C bus 1, so it isn't the older I2C board and `--i2c` isn't needed. |
| Device | `/dev/ttyAMA0` (GPIO14 = TXD0, GPIO15 = RXD0) |
| Baud | 1,000,000 |
| Framing | `0xAA 0x55`, function, length, data, then CRC8 (Dallas/Maxim). Beep is function 2. Motor duty is function 3, sub-command `0x05`. |

## Motor mapping

| Motor | Corner | FORWARD_SIGN |
|---|---|---|
| M1 | front-left | −1 |
| M2 | front-right | +1 |
| M3 | rear-left | −1 |
| M4 | rear-right | +1 |

Final `FORWARD_SIGN = {1: -1, 2: +1, 3: -1, 4: +1}`. These are the values from Hiwonder's `mecanum.py`, unchanged. Each wheel was checked turning forward in the air, so no signs needed flipping.

## What worked

| Test | Command | Duty | Time | Result |
|---|---|---|---|---|
| Link | `python3 nudge.py beep` | – | – | beep heard |
| Each wheel, in the air | `python3 nudge.py wheel N` | 35 | 0.4 s | each turned forward |
| All four, in the air | `python3 nudge.py forward` | 35 | 0.4 s | all turned forward |
| **On the floor** | `python3 nudge.py forward --time 0.3` | **35** | **0.3 s** | **rolled forward a little and stopped** |

Limits built into the script: duty is capped at 50 and time at 1.0 s, and it always sends stop three times afterward.

## Pi setup that was needed

1. In `/boot/firmware/config.txt`, under `[all]`:
   ```
   enable_uart=1
   dtparam=uart0=on
   ```
2. I2C on: `sudo raspi-config nonint do_i2c 0`. This sets `dtparam=i2c_arm=on` and loads `i2c-dev`.
3. **Serial console off:** `sudo raspi-config nonint do_serial_cons 1`.
   - On the Pi 5, `dtparam=uart0=on` makes `serial0` point at `ttyAMA0`. The default `console=serial0,115200` in `cmdline.txt` then puts kernel messages and a login prompt on the motor board's port.
   - The command above removes that entry. A backup is at `/boot/firmware/cmdline.txt.bak`.
   - To check: `cat /proc/consoles` should list only `tty1`, and no `serial-getty@ttyAMA0` should be running.
4. The user must be in the `dialout` group.
5. Packages from apt: `python3-serial`, `python3-smbus2`, `i2c-tools`.

Reboot after steps 1–3.

## Troubleshooting

Once, after the robot was moved onto the floor, the board stopped responding. Its LED stayed lit, but there was no beep and the motors didn't respond, even at duty 50.

- The Pi side was fine: the port, the pins and the console were all unchanged.
- The USB camera wasn't the cause. The board stayed silent with the camera unplugged.
- The board recovered after it was switched off for 5+ seconds and on again, with the Pi rebooted at the same time.
- The cause isn't confirmed. A battery voltage dip under load is the main suspect. The battery voltage wasn't measured.

If it happens again, power-cycle the board first. Then beep before and after a floor run to check the board stays responsive.
