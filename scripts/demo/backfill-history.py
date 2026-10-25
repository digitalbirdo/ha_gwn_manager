"""Write 24 h of made-up history for the graph sensors into the recorder.

Stop Home Assistant first, run this, start it again. Only for a dev instance
that uses the demo server: it writes into the recorder database directly.

    python scripts/demo/backfill-history.py <config dir>/home-assistant_v2.db
"""
import math
import random
import sqlite3
import sys
import time

random.seed(3)
db = sqlite3.connect(sys.argv[1])
now = time.time()
start = now - 24 * 3600
step = 300

def meta(entity_id):
    row = db.execute("select metadata_id from states_meta where entity_id=?", (entity_id,)).fetchone()
    if row:
        return row[0]
    return db.execute("insert into states_meta(entity_id) values (?)", (entity_id,)).lastrowid

def write(entity_id, fn):
    mid = meta(entity_id)
    # drop what the short real run recorded, then the series
    db.execute("delete from states where metadata_id=? and last_updated_ts < ?", (mid, now - 60))
    t = start
    while t < now - 120:
        v = fn(t)
        db.execute(
            "insert into states(state, last_changed_ts, last_reported_ts, last_updated_ts, metadata_id, origin_idx) values (?,?,?,?,?,0)",
            (str(v), t, t, t, mid),
        )
        t += step

def daily(t, peak_hour, width=3.0):
    h = time.localtime(t).tm_hour + time.localtime(t).tm_min / 60
    d = min(abs(h - peak_hour), 24 - abs(h - peak_hour))
    return math.exp(-(d / width) ** 2)

# Internet: low at night, evening peak, a few bursts
def wan_down(t):
    base = 3 + 45 * daily(t, 20.5) + 15 * daily(t, 13, 2)
    burst = 120 if random.random() < 0.02 else 0
    return round(max(0.2, base * random.uniform(0.6, 1.3) + burst), 2)
def wan_up(t):
    return round(max(0.1, (1 + 6 * daily(t, 20.5) + 3 * daily(t, 10, 2)) * random.uniform(0.5, 1.4)), 2)
write("sensor.gateway_wan_download", wan_down)
write("sensor.gateway_wan_upload", wan_up)

for ap, (b24, b5, b6) in {"ap_living_room": (22, 6, 2), "ap_office": (14, 9, 4), "ap_basement": (30, 4, 1)}.items():
    write(f"sensor.{ap}_channel_utilization_2_4_ghz", lambda t, b=b24: round(max(2, b * (0.6 + 0.8 * daily(t, 19)) + random.uniform(-3, 3))))
    write(f"sensor.{ap}_channel_utilization_5_ghz", lambda t, b=b5: round(max(1, b * (0.4 + 1.2 * daily(t, 20.5)) + random.uniform(-1.5, 1.5))))
    write(f"sensor.{ap}_channel_utilization_6_ghz", lambda t, b=b6: round(max(0, b * (0.5 + daily(t, 21)) + random.uniform(-0.8, 0.8))))

SIGNAL = {
    "laptop": -52, "phone": -58, "tablet": -63, "smart_tv": -49, "phone_2": -55, "watch": -66,
    "robot_vacuum": -61, "thermostat": -45, "smart_plug_1": -57, "smart_plug_2": -69, "led_strip": -48,
    "doorbell": -71, "air_purifier": -59, "speaker_kitchen": -53, "speaker_office": -42,
    "washing_machine": -64, "heat_pump": -55, "water_meter": -79, "3d_printer": -47,
    "garage_door": -73, "energy_meter": -38, "guest_phone": -67,
}
MOVING = {"phone": 12, "tablet": 8, "laptop": 7, "robot_vacuum": 14, "watch": 10, "phone_2": 9}
for name, base in SIGNAL.items():
    spread = MOVING.get(name, 2.5 if name != "water_meter" else 6)
    write(f"sensor.{name}_signal_strength",
          lambda t, b=base, s=spread: round(b + s * math.sin(t / 2700 + hash(b) % 7) * random.uniform(0.3, 1) + random.uniform(-1.5, 1.5)))
db.commit()
print("done")
