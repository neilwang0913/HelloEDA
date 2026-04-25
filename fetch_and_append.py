"""
Fetch live MXP arrivals via OpenSky and append to arrivals_mxp.csv (dedup by callsign+time).
Designed to run in GitHub Actions or locally.
"""
import sys, csv, datetime, os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mxp_flight_scraper import fetch_opensky_live, OUTPUT_CSV

def main():
    now_str = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    flights = fetch_opensky_live(radius_km=80)

    if not flights:
        print(f"[{now_str}] 0 flights detected (off-peak or rate limited). Nothing written.")
        return

    existing_keys: set = set()
    rows: list = []
    if os.path.exists(OUTPUT_CSV):
        with open(OUTPUT_CSV, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                key = (row["callsign"].strip(), row["arr_hour"], row["arr_min"])
                existing_keys.add(key)
                rows.append(row)

    added = 0
    for fl in flights:
        key = (fl.callsign, str(fl.arr_hour), str(fl.arr_min))
        if key not in existing_keys:
            rows.append({
                "callsign": fl.callsign,
                "origin":   fl.origin,
                "arr_hour": fl.arr_hour,
                "arr_min":  fl.arr_min,
                "pax":      fl.pax,
                "status":   fl.status,
            })
            existing_keys.add(key)
            added += 1

    rows_sorted = sorted(rows, key=lambda r: int(r["arr_hour"]) * 60 + int(r["arr_min"]))
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["callsign", "origin", "arr_hour", "arr_min", "pax", "status"])
        writer.writeheader()
        writer.writerows(rows_sorted)

    print(f"[{now_str}] Added {added} new records. CSV total: {len(rows_sorted)} rows.")

if __name__ == "__main__":
    main()
