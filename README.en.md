# DPiRS — receive-only D-STAR D-PRS i-Gate

[日本語](README.md)

A program that turns an MMDVM hotspot board (MMDVM_HS / ADF7021) into an i-Gate that **anyone** can use: just transmit your position with your D-STAR radio and it appears on APRS (aprs.fi, etc.).

- Listens on the configured frequency (default 438.010 MHz) for D-STAR DV mode (simplex) transmissions.
- Extracts the **D-PRS (GPS position)** carried with the voice, verifies its CRC and forwards it to APRS-IS.
- You do **not** have to match the repeater/gateway settings (RPT1, RPT2, UR). Transmitting in DV mode is enough.
- **It never transmits.** Only setup/status commands are ever written to the modem (`modem.py`, covered by tests); any transmit-data command is refused in code.
- MMDVMHost, ircDDBGateway and Pi-Star are **not needed** (and cannot share the same board at the same time).

```
Radio (DV, 438.010 MHz) ──RF──> [MMDVM board] ──serial──> DPiRS ──> APRS-IS ──> aprs.fi
```

## Tested environment

| Item | Tested | Not tested (may work) |
|---|---|---|
| Computer | Raspberry Pi 3B | Pi 4 / Zero (installer supports them, unverified), Pi 5 (manual setup needed) |
| OS | Raspberry Pi OS Trixie (tested on a Pi 3B) | Bookworm (supported by the installer, untested) |
| Modem | AURSINC MMDVM hotspot board (GPIO, firmware MMDVM_HS_Hat v1.5.2 / ADF7021) | USB modems, other ADF7021 MMDVM_HS boards |
| Radios | ID-52, ID-31, ID-31PLUS, IC-7100 (DV mode, simplex) | IC-705, etc. |

## Limitations

- **D-STAR D-PRS only.** APRS (144 MHz AFSK 1200 bps) cannot be received.
- Only **one frequency** can be monitored (the board listens on one frequency at a time).
- Radios set to send positions in **NMEA format** (`$GPRMC`, etc.) are not supported (they are received and shown in the log, but not sent to APRS-IS). Set them to D-PRS.
- **It never transmits**, so there is no acknowledgement or reply to the sender.
- The same board cannot be used at the same time by MMDVMHost, Pi-Star, etc.

## Quick install (Raspberry Pi OS Bookworm or later)

```bash
sudo apt install -y git
git clone https://github.com/Nickel2Plus/DPiRS.git
cd DPiRS
./install.sh
```

What `install.sh` does (`config.txt` and `cmdline.txt` are backed up automatically before any change):

1. Installs `python3-serial`
2. (GPIO hats) enables the UART, moves Bluetooth off it, disables the serial console
3. Adds you to the `dialout` group
4. Creates `config.ini` (asks for your callsign, and whether to show the station itself on the map)
5. Registers the systemd service `dpirs`

If the UART settings were changed, **reboot** (`sudo reboot`).

### Check that it works

```bash
python3 check.py                 # diagnosis (config, serial port, modem / service state, network)
journalctl -u dpirs -f        # watch the log
```

Set your radio to **DV mode, 438.010 MHz, simplex** and transmit. You should see:

```
RX MY='XXXXXX  '/'ID31' UR='CQCQCQ  ' ...
D-PRS [CRC OK]: $$CRC....,XXXXXX>API31,DSTAR*:!....
  [gate] DRY-RUN: XXXXXX>API31,DSTAR*,qAR,YOURCALL-10:!....
```

### Forwarding to APRS-IS (positions become public)

It starts with `send = no` (dry run). When it works, edit `config.ini`:

```bash
nano config.ini        # set [aprs_is] send = yes
sudo systemctl restart dpirs
```

> ⚠ With `send = yes`, the positions of **every station** received are sent to APRS-IS and published (aprs.fi, etc.). Operate responsibly, including announcing your location and frequency to users.

## Configuration (config.ini)

Edit with `nano config.ini` or `vi config.ini`, then `sudo systemctl restart dpirs`.

| Section | Key | Default | Description |
|---|---|---|---|
| general | language | auto | Message language (auto / ja / en) |
| station | callsign | NOCALL-10 | **Required.** Your callsign + SSID (e.g. JA1ABC-10) |
| modem | serial_port | /dev/serial0 | `/dev/serial0` for GPIO hats, `/dev/ttyACM0` for USB |
| modem | rx_frequency | 438010000 | Receive frequency in Hz |
| modem | frequency_offset | 0 | Frequency correction in Hz |
| modem | rx_invert | no | Try `yes` only if nothing is received |
| aprs_is | send | no | `yes` to forward to APRS-IS |
| aprs_is | server / server_port | asia.aprs2.net / 14580 | Server |
| aprs_is | passcode | (empty) | Empty = computed from the callsign |
| filter | dupe_seconds | 30 | Drop the same content from the same station within this time |
| filter | min_interval | 10 | Minimum seconds between different packets from one station |
| beacon | enabled / latitude / longitude / comment / interval_minutes | no | Show the i-Gate station itself on the APRS map (optional). **The coordinates become public**; for a home station use an approximate nearby point. Sent only when `send = yes`. Can also be set by install.sh |

## For radio users

- **DV mode**, the frequency announced by the operator (e.g. 438.010 MHz), duplex OFF (simplex).
- Set the GPS transmit format to **D-PRS** (NMEA does not work). The position is sent **together with the voice**, so press PTT (or use GPS auto-transmit).
- ⚠ **Your transmitted position is forwarded to APRS-IS and made public (aprs.fi, etc.).** If you do not want your home or travel position to be public, do not transmit.
- Approximate transmit time needed: ID-31 about 0.6 s, IC-7100 about 0.7 s, ID-31PLUS about 0.9 s, ID-52 about 1 s. Transmit for **at least 1 second**.
- For the ID-52, parts missing from short transmissions are rebuilt and verified against the CRC (only data that passes the check is forwarded).

## How it works / safety

- D-PRS lines (`$$CRCxxxx,...`) are assembled from the D-STAR slow data and used only if the CRC-CCITT matches.
- Source callsign format check, `NOGATE` / `RFONLY` path filter, duplicate suppression and per-station rate limiting.
- APRS-IS forwarding runs in a separate thread, so reception continues during network outages (automatic reconnect).
- If the modem stops responding the program exits and systemd restarts it.

## Troubleshooting

Run `python3 check.py` first. Common causes:

| Symptom | Cause / fix |
|---|---|
| `/dev/serial0` missing | UART not configured. Run `./install.sh` and reboot |
| No `GET_VERSION` reply / `multiple access` | A serial console (`agetty`) is using the port. Re-run `install.sh` and reboot |
| Permission denied | Run `sudo usermod -aG dialout $USER` and log in again |
| Nothing received | Check radio settings. Lower the power and increase the distance. Try `rx_invert = yes`. Use `frequency_offset` for an off-frequency board |
| `CRC NG` lines | Missed data. Transmit longer, from a closer distance |
| `login was not verified` | Check callsign / passcode |
| `multiple access` when run by hand | The service is running. Stop it first with `sudo systemctl stop dpirs`, then start it again afterwards with `sudo systemctl start dpirs` |

Manual UART setup (unsupported models such as Pi 5): assign the UART (GPIO14/15) to the modem, disable the serial console, and set `serial_port` to `/dev/serial0` or the right device.

## Uninstall

```bash
./uninstall.sh
```

Removes the service and the `config.txt` additions (`config.ini` and the program files stay).

The serial console entry in `cmdline.txt` is **not restored automatically**; restore it by hand from the backup made at install time (`cmdline.txt.dpirs-*.bak`). You can run `./install.sh` again (it leaves already-configured parts unchanged).

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Tests use synthetic data only; no radio or network needed.

## Notes

- Connecting to APRS-IS with this program requires a **valid amateur radio callsign**. Operate under your own callsign.
- Follow the radio regulations of your country. This program does not transmit, but the operator is responsible for the operation.
- Received positions (including those of other stations) are published.

## Credits

- The MMDVM serial protocol was studied from G4KLX's [MMDVM](https://github.com/g4klx/MMDVMHost) and CA6JAU's MMDVM_HS sources. The code here is written independently.
- D-STAR is a specification by Icom / JARL. This program is not affiliated with those organizations or any radio manufacturer.

## License

GNU General Public License v3.0 ([LICENSE](LICENSE))

Copyright (C) 2026 JQ7EAH (GitHub: Nickel2Plus)
