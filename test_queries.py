#!/usr/bin/env python3
import sqlite3
import os
import sys
from query_schedule import query_station_schedule, get_available_dates, get_closest_date

DB_PATH = 'gtfs_metro.db'

def run_tests():
    print("=" * 80)
    print("RUNNING METRO SCHEDULE DATABASE INTEGRITY TESTS")
    print("=" * 80)
    
    # Test 1: Check database file existence
    print("Test 1: Verifying database file exists...")
    if not os.path.exists(DB_PATH):
        print(f"FAIL: Database file '{DB_PATH}' not found.", file=sys.stderr)
        sys.exit(1)
    print("PASS: Database file found.")
    
    conn = sqlite3.connect(DB_PATH)
    
    # Test 2: Check database tables exist and have entries
    print("\nTest 2: Verifying table schemas and row counts...")
    cursor = conn.cursor()
    tables = ['routes', 'stops', 'trips', 'stop_times', 'calendar_dates']
    for table in tables:
        try:
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            count = cursor.fetchone()[0]
            if count == 0:
                print(f"FAIL: Table '{table}' is empty.", file=sys.stderr)
                sys.exit(1)
            print(f"PASS: Table '{table}' contains {count} rows.")
        except sqlite3.OperationalError as e:
            print(f"FAIL: Table '{table}' query failed: {e}", file=sys.stderr)
            sys.exit(1)
            
    # Test 3: Get available dates
    print("\nTest 3: Verifying calendar date availability...")
    dates = get_available_dates(conn)
    if not dates:
        print("FAIL: No active dates found in calendar_dates.", file=sys.stderr)
        sys.exit(1)
    test_date = dates[0]
    print(f"PASS: Found {len(dates)} available dates. Using test date: {test_date}")
    
    # Test 4: Query M1/M3 interchange (Duomo)
    print("\nTest 4: Querying Duomo station schedule (M1 & M3 interchange)...")
    res = query_station_schedule(conn, "Duomo", test_date)
    if "error" in res:
        print(f"FAIL: {res['error']}", file=sys.stderr)
        sys.exit(1)
        
    deps = res["departures"]
    if len(deps) == 0:
        print("FAIL: No departures returned for Duomo.", file=sys.stderr)
        sys.exit(1)
        
    print(f"PASS: Successfully returned {len(deps)} departures for Duomo.")
    
    # Check chronological ordering
    times = [d["departure_time"] for d in deps]
    is_sorted = (times == sorted(times))
    if not is_sorted:
        print("FAIL: Departures are not in chronological order.", file=sys.stderr)
        sys.exit(1)
    print("PASS: Departures are correctly sorted chronologically.")
    
    # Verify line color matches
    m1_color = next((d["color"] for d in deps if d["line"] == "M1"), None)
    m3_color = next((d["color"] for d in deps if d["line"] == "M3"), None)
    
    if m1_color != "#ff0000":
        print(f"FAIL: M1 line color is incorrect: {m1_color}", file=sys.stderr)
        sys.exit(1)
    if m3_color != "#fcff01":
        print(f"FAIL: M3 line color is incorrect: {m3_color}", file=sys.stderr)
        sys.exit(1)
    print("PASS: Metro line colors match official specifications (M1 = #ff0000, M3 = #fcff01).")
    
    # Test 5: Query specific lines
    print("\nTest 5: Validating all metro line query coverage...")
    test_stations = {
        "M1/M2 Interchange (Cadorna)": "Cadorna",
        "M4 Line (Linate Aeroporto)": "Linate Aeroporto",
        "M5 Line (Bignami)": "Bignami"
    }
    
    for desc, name in test_stations.items():
        res = query_station_schedule(conn, name, test_date)
        if "error" in res or len(res["departures"]) == 0:
            print(f"FAIL: Could not query {desc}.", file=sys.stderr)
            sys.exit(1)
        print(f"PASS: successfully matched and queried schedule for {desc} ({len(res['departures'])} departures).")
        
    conn.close()
    
    print("\n" + "=" * 80)
    print("ALL TESTS PASSED SUCCESSFULLY! Database is healthy and queries are fully accurate.")
    print("=" * 80)

if __name__ == '__main__':
    run_tests()
